"""Card offers: Chase Offers and Capital One Offers, the extra cash back or miles a bank gives on one card at one
store ("Food Lion, 5% cash back").

Banks don't share these through Plaid or any API, so they come from the offers page itself: the "Send to Finance"
bookmark copies the text of the Chase Offers or Capital One Offers page you have open (in your own browser, signed in
as usual) and opens this app with it in the link's #fragment, which never reaches a server. The app then posts the
text here to be read, shows what it found, and saves it when you tap Save. Pasting the page's text does the same.
The app never signs in to a bank and never stores the page text: only the offers read from it.

Reading goes by the page's words, not its layout, so it keeps working when the bank redesigns the page: every
"5% cash back", "$20 back" or "5X miles" is an offer, the store is the name just before it, and the lines after it
until the next offer say when it ends, whether it's added to the card, and whether it's online or in store.

Each send is the whole list for one card, so it replaces that card's offers: the ones that are gone were used or
expired. Offers then count in the store lookup (cards.lookup) on top of the card's own rewards, and a push goes out
a few days before an offer ends at a store you shop at.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp.categorize import merchant_key
from financeapp.models import Account, AlertEvent, CardOffer, now

MAX_TEXT = 200_000  # characters of page text read
MAX_OFFERS = 300  # per card
SOON_DAYS = 7  # "ends soon" on the page
PUSH_DAYS = 3  # the push, this many days before an offer you'd likely use ends
PUSH_MIN_VISITS = 2  # purchases at the store in the last year for it to count as a store you shop at
ISSUERS = {"chase": "Chase", "capital_one": "Capital One", "other": "Card"}
OFFER_NAMES = {"chase": "Chase Offer", "capital_one": "Capital One Offer", "other": "Card offer"}
WHERE = {
    "chase": "Add it in the Chase app or on chase.com first.",
    "capital_one": "Start from Capital One Offers so it counts.",
    "other": "Add it to the card first.",
}
MONTHS = {
    m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])
}

_UP_TO = r"(?P<up>up to )?"
PERCENT = re.compile(_UP_TO + r"(?P<n>\d{1,2}(?:\.\d{1,2})?) ?% ?(?:in )?(?:cash ?back|back|off|rewards?)\b", re.I)
AMOUNT = re.compile(_UP_TO + r"\$(?P<n>\d{1,4}(?:\.\d{2})?) ?(?:in )?(?:cash ?back|back|statement credit|off)\b", re.I)
MULTIPLIER = re.compile(_UP_TO + r"(?P<n>\d{1,2}(?:\.\d)?) ?x ?(?:the )?(?:miles|points)\b", re.I)
DAYS_LEFT = re.compile(r"\b(\d{1,3}) days? (?:left|remaining)\b", re.I)
ENDS = r"\b(?:exp(?:ires|iring|\.)?|ends|valid (?:until|through|thru)) ?(?:on )?"
ENDS_DATE = re.compile(ENDS + r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", re.I)
ENDS_NAMED = re.compile(
    ENDS + r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})(?:,? (\d{4}))?",
    re.I,
)
SOON = re.compile(r"\b(?:expiring|ends|ending) soon\b|\blast day\b|\bends today\b", re.I)
ADDED = re.compile(r"^(?:added|added to card|activated|enrolled|offer added|✓|✔)$", re.I)
NOT_ADDED = re.compile(r"^(?:add|add to card|add offer|activate|\+|⊕)$", re.I)
ONLINE = re.compile(r"\bonline(?: only)?\b", re.I)
IN_STORE = re.compile(r"\bin[- ]store(?: only)?\b", re.I)
MIN_SPEND = re.compile(
    r"\b(?:spend|purchases? of|purchase of|on (?:a )?(?:single )?purchase of) \$(\d{1,5})(?:\.\d{2})?(?: or more|\+)?",
    re.I,
)
MAX_BACK = re.compile(r"\b(?:max(?:imum)?(?: of)?|up to) \$(\d{1,5})(?:\.\d{2})? ?(?:cash ?back|back|total)", re.I)
# The card's last four digits: "(...9791)", "…9791", "ending in 9791". The card's name is the text before it.
MASK = re.compile(r"(?:\.{2,3}|…|ending in|x{2,4}) ?(\d{4})\b", re.I)
# Lines that are page furniture, not a store: section names, buttons, filters.
NOISE = re.compile(
    r"^(?:new|all|all offers|featured|recommended|for you|just for you|shopping|groceries|grocery|home|home & garden|"
    r"dining|restaurants|travel|entertainment|gas|health|health & wellness|beauty|fashion|electronics|services|"
    r"expiring soon|expiring|ending soon|added|added to card|add|add to card|add offer|activate|online|in-store|"
    r"in store|online only|in-store only|online & in-store|details|view details|see details|terms|shop now|"
    r"chase offers|capital one offers|offers|my offers|earned|search|filter|sort|sort by|show more|load more|"
    r"view all|see all|back|menu|close|\+|⊕|✓|✔|[\d$.,% ]+|.*\bearned\b.*|.*\boffers?\b.*\bavailable\b.*|"
    r"fresh for \w+|featured offers|popular|trending|ends soon|last day|new offers?|\d+ days? left)$",
    re.I,
)


@dataclass
class Read:
    """One offer as read from a page, before it's saved."""

    merchant: str
    kind: str  # percent | amount | multiplier
    value: float  # 5 for 5%, 20 for $20, 5 for 5X
    up_to: bool = False
    min_spend_cents: int | None = None
    max_cents: int | None = None
    channel: str | None = None  # online | in store | None (either, or not said)
    expires: date | None = None
    soon: bool = False  # the page said it ends soon, without a date
    added: bool | None = None  # None: the page didn't say

    def json(self, issuer: str = "other") -> dict:
        return {
            "merchant": self.merchant,
            "kind": self.kind,
            "value": self.value,
            "up_to": self.up_to,
            "label": label(self.kind, self.value, self.up_to, issuer),
            "min_spend_cents": self.min_spend_cents,
            "max_cents": self.max_cents,
            "channel": self.channel,
            "expires": self.expires.isoformat() if self.expires else None,
            "soon": self.soon,
            "added": self.added,
        }


def label(kind: str, value: float, up_to: bool = False, issuer: str = "other") -> str:
    n = f"{value:g}"
    unit = "miles" if issuer == "capital_one" else "points"
    text = {"percent": f"{n}% back", "amount": f"${n} back", "multiplier": f"{n}X {unit}"}[kind]
    return f"Up to {text}" if up_to else text


def on_site(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def issuer_of(site: str, text: str) -> str:
    host = (site or "").lower()
    if on_site(host, "chase.com"):
        return "chase"
    if on_site(host, "capitalone.com") or on_site(host, "capitaloneoffers.com"):
        return "capital_one"
    head = text[:5000].lower()
    if "chase offers" in head:
        return "chase"
    if "capital one" in head:
        return "capital_one"
    return "other"


def _value(line: str) -> tuple[re.Match, str] | None:
    """The first offer amount on a line, earliest match first."""
    found = [(m, kind) for kind, rx in (("percent", PERCENT), ("amount", AMOUNT), ("multiplier", MULTIPLIER))
             if (m := rx.search(line))]  # fmt: skip
    return min(found, key=lambda f: f[0].start()) if found else None


CAP_LINE = re.compile(r" ?(?:max(?:imum)?|limit|cap)\b", re.I)
PUNCT = " -\u2013\u2014:|•·,"  # spaces, dashes and bullets around a name


def _store_name(text: str) -> str | None:
    name = " ".join(text.split()).strip(PUNCT)
    name = re.sub(r"^(?:earn|get|enjoy|save)\b|\b(?:earn|get|enjoy|save)$", "", name, flags=re.I).strip(PUNCT)
    if len(name) < 2 or len(name) > 60 or not re.search(r"[A-Za-z]{2}", name) or NOISE.fullmatch(name):
        return None
    if _value(name) or DAYS_LEFT.search(name) or ENDS_DATE.search(name) or ENDS_NAMED.search(name):
        return None
    return name


def _ends(line: str, today: date) -> date | None:
    if m := DAYS_LEFT.search(line):
        return today + timedelta(days=int(m.group(1)))
    if m := ENDS_DATE.search(line):
        month, day, year = int(m.group(1)), int(m.group(2)), m.group(3)
        return _date(today, month, day, year)
    if m := ENDS_NAMED.search(line):
        return _date(today, MONTHS[m.group(1).lower()[:3]], int(m.group(2)), m.group(3))
    return None


def _date(today: date, month: int, day: int, year: str | None) -> date | None:
    try:
        if year:
            y = int(year)
            return date(y + 2000 if y < 100 else y, month, day)
        d = date(today.year, month, day)
        return d if d >= today - timedelta(days=31) else date(today.year + 1, month, day)  # "11/30" in December
    except ValueError:
        return None


def parse(text: str, today: date) -> list[Read]:
    """Every offer on an offers page's text, in page order, each store once."""
    # One space between words, so the patterns above only ever see single spaces.
    lines = [" ".join(ln.split()) for ln in text[:MAX_TEXT].splitlines()]
    lines = [ln for ln in lines if ln]
    # A "Max $5 cash back" line is the cap of the offer above it, not an offer.
    hits = [(i, v) for i, ln in enumerate(lines) if not CAP_LINE.match(ln) and (v := _value(ln))]
    out: list[Read] = []
    seen: set[str] = set()
    for n, (i, (m, kind)) in enumerate(hits):
        line = lines[i]
        name = _store_name(line[: m.start()])
        if name is None:
            for j in range(i - 1, max(i - 4, (hits[n - 1][0] if n else -1)), -1):
                if name := _store_name(lines[j]):
                    break
        if name is None:
            continue
        key = merchant_key(name)
        if not key or key in seen:
            continue
        value = float(m.group("n"))
        if value <= 0 or (kind == "percent" and value > 50):
            continue
        r = Read(name, kind, value, up_to=bool(m.group("up")))
        end = hits[n + 1][0] if n + 1 < len(hits) else len(lines)
        # The lines after the amount, up to the next offer (or a few lines).
        block = [line[m.end() :], *lines[i + 1 : min(end, i + 8)]]
        for b in block:
            if r.expires is None and (d := _ends(b, today)):
                r.expires = d
            if SOON.search(b):
                r.soon = True
            if ADDED.fullmatch(b.strip()):
                r.added = True
            elif NOT_ADDED.fullmatch(b.strip()) and r.added is None:
                r.added = False
            online, store = bool(ONLINE.search(b)), bool(IN_STORE.search(b))
            if online != store and r.channel is None:
                r.channel = "online" if online else "in store"
            if (s := MIN_SPEND.search(b)) and r.min_spend_cents is None:
                r.min_spend_cents = int(s.group(1)) * 100
            if (x := MAX_BACK.search(b)) and r.max_cents is None and kind == "percent":
                r.max_cents = int(x.group(1)) * 100
        if r.expires is not None and r.expires < today:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= MAX_OFFERS:
            break
    return out


def card_on_page(text: str) -> tuple[str | None, str | None]:
    """The card the page is showing offers for: its name and last four digits, if the page says."""
    for line in text[:20_000].splitlines():
        line = " ".join(line.split())
        if len(line) > 120 or not (m := MASK.search(line)):
            continue
        name = re.sub(r"[^A-Za-z®™'& -]", " ", line[: m.start()])
        name = " ".join(name.split()[-5:])
        return name or None, m.group(1)
    return None, None


def match_account(accounts: list[Account], issuer: str, mask: str | None) -> Account | None:
    """The card account the offers are for: the one with those last four digits, or the bank's only card."""
    if mask:
        same = [a for a in accounts if a.mask == mask]
        if len(same) == 1:
            return same[0]
    words = {"chase": "chase", "capital_one": "capital one"}.get(issuer)
    if words:
        theirs = [a for a in accounts if words in (a.institution or "").lower()]
        if len(theirs) == 1:
            return theirs[0]
    return None


# ---- using them


@dataclass
class Offer:
    """A saved offer, for the store lookup and the page."""

    id: int | str
    account_id: int
    issuer: str
    merchant: str
    key: str
    kind: str
    value: float
    up_to: bool = False
    min_spend_cents: int | None = None
    max_cents: int | None = None
    channel: str | None = None
    expires: date | None = None
    soon: bool = False
    added: bool | None = None
    seen: date | None = None

    @property
    def label(self) -> str:
        return label(self.kind, self.value, self.up_to, self.issuer)


def of_row(o: CardOffer) -> Offer:
    return Offer(
        o.id, o.account_id, o.issuer, o.merchant, o.key, o.kind, float(o.value), o.up_to, o.min_spend_cents,
        o.max_cents, o.channel, o.expires, o.soon, o.added, o.seen,
    )  # fmt: skip


def load(db: DbSession, on: date) -> list[Offer]:
    rows = db.scalars(select(CardOffer).where((CardOffer.expires.is_(None)) | (CardOffer.expires >= on)))
    return [of_row(o) for o in rows]


GENERIC = {
    "the",
    "shop",
    "store",
    "stores",
    "market",
    "online",
    "fuel",
    "gas",
    "food",
    "home",
    "new",
    "best",
    "american",
}


def same_store(offer_key: str, key: str) -> bool:
    """Whether an offer's store and a purchase's store are the same: "harris teeter" and "harristeeter com", "marathon
    fuel" and "marathon petro"."""
    if not offer_key or not key:
        return False
    if offer_key == key or key.startswith(offer_key + " ") or offer_key.startswith(key + " "):
        return True
    a, b = offer_key.replace(" ", "").removesuffix("com"), key.replace(" ", "").removesuffix("com")
    if len(a) >= 4 and (a == b or b.startswith(a)):
        return True
    first_a, first_b = offer_key.split()[0], key.split()[0]
    return first_a == first_b and len(first_a) >= 5 and first_a not in GENERIC


def worth(o: Offer, cents: int, cpp: float) -> int:
    """What the offer adds to a purchase of `cents`, in cents (at `cpp` cents a point for miles or points)."""
    if o.min_spend_cents and cents < o.min_spend_cents:
        return 0
    if o.kind == "percent":
        v = cents * o.value / 100
        return round(min(v, o.max_cents) if o.max_cents else v)
    if o.kind == "amount":
        return min(round(o.value * 100), cents)
    return round(cents / 100 * o.value * cpp)


def fits(o: Offer, channel: str | None) -> bool:
    return o.channel is None or channel is None or o.channel == channel


def ends_note(o: Offer, on: date) -> str | None:
    if o.expires:
        days = (o.expires - on).days
        if days <= 0:
            return "Ends today"
        if days == 1:
            return "Ends tomorrow"
        if days <= SOON_DAYS:
            return f"Ends {o.expires:%A}"
        return f"Ends {o.expires:%b} {o.expires.day}"
    return "Ends soon" if o.soon else None


def ending_soon(o: Offer, on: date) -> bool:
    return (o.expires is not None and (o.expires - on).days <= SOON_DAYS) or (o.expires is None and o.soon)


def note_for(o: Offer, on: date) -> dict:
    """An offer as the store lookup shows it on a card."""
    extra = []
    if o.min_spend_cents:
        extra.append(f"on ${o.min_spend_cents // 100:,}+")
    if o.max_cents:
        extra.append(f"up to ${o.max_cents // 100:,} back")
    if o.channel:
        extra.append("online" if o.channel == "online" else "in store")
    return {
        "id": o.id,
        "name": OFFER_NAMES.get(o.issuer, "Card offer"),
        "label": o.label + (f" ({', '.join(extra)})" if extra else ""),
        "ends": ends_note(o, on),
        "added": o.added,
        "how": None if o.added else WHERE.get(o.issuer, WHERE["other"]),
    }


# ---- saving


def replace(db: DbSession, account_id: int, issuer: str, found: list[Read], on: date) -> int:
    """Saves a card's offers, replacing what it had. Keeps when each store's offer was first seen."""
    old = {o.key: o for o in db.scalars(select(CardOffer).where(CardOffer.account_id == account_id))}
    db.execute(delete(CardOffer).where(CardOffer.account_id == account_id))
    n = 0
    seen: set[str] = set()
    for r in found[:MAX_OFFERS]:
        key = merchant_key(r.merchant)
        if not key or key in seen:
            continue
        seen.add(key)
        before = old.get(key)
        db.add(
            CardOffer(
                account_id=account_id,
                issuer=issuer,
                merchant=r.merchant[:80],
                key=key[:80],
                kind=r.kind,
                value=r.value,
                up_to=r.up_to,
                min_spend_cents=r.min_spend_cents,
                max_cents=r.max_cents,
                channel=r.channel,
                expires=r.expires,
                soon=r.soon,
                added=r.added,
                first_seen=before.first_seen if before else on,
                seen=on,
            )
        )
        n += 1
    return n


# ---- the push before an offer you'd use ends


def visits(purchases: list, o: Offer, since: date) -> tuple[int, int]:
    """How many times, and how much, you bought at the offer's store since a date (any card or account)."""
    count = spent = 0
    for p in purchases:
        if p.day >= since and same_store(o.key, p.key):
            count += 1
            spent += p.cents
    return count, spent


def queue(db: DbSession, on: date) -> int:
    """Saves a push for each offer at a store you shop at that ends within PUSH_DAYS (once per offer)."""
    from financeapp import cards  # cards imports this module

    offers = [o for o in load(db, on) if o.expires is not None and (o.expires - on).days <= PUSH_DAYS]
    if not offers:
        return 0
    w = cards.wallet(db, on)
    _, accounts, links = cards.load_cards(db)
    labels = {a.id: cards.label_of(a, links.get(a.id)) for a in accounts}
    added = 0
    for o in offers:
        count, _ = visits(w.everything, o, on - timedelta(days=cards.HISTORY_DAYS))
        if count < PUSH_MIN_VISITS:
            continue
        card = labels.get(o.account_id) or "your card"
        when = ends_note(o, on) or "Ends soon"
        title = f"{OFFER_NAMES.get(o.issuer, 'Card offer')} at {o.merchant} {when[0].lower()}{when[1:]}"
        body = f"{o.label} on {card}."
        if not o.added:
            body += " " + WHERE.get(o.issuer, WHERE["other"])
        stmt = insert(AlertEvent).values(
            rule_id=None, kind="account", title=title[:120], body=body[:300],
            dedup_key=f"offer:{o.account_id}:{o.key}:{o.expires}"[:160], fired_at=now(), status="pending", attempts=0,
        )  # fmt: skip
        if db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None:
            added += 1
    return added
