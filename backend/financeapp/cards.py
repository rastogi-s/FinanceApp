"""Credit cards and the points maximizer: which card earns the most on a purchase, by category or by store.

Card rewards go by how the card network codes the store (its merchant category code, MCC), not by the app's budget
categories, so a purchase is described by reward tags: "dining", "groceries", "flights", "m:costco" for a store the
app knows. Tags come from, in order of trust: the store directory (data/merchants.json), the MCC the bank sent with
your own past purchases there, and Plaid's category as a fallback.

Each card account is linked once to a product in the catalog (data/cards.json, checked against each issuer's page:
see its source and verified fields). Everything is compared in dollars: points earned times what you say a point is
worth (cents per point, 1 cent unless you change it). Bonus caps are counted from your own purchases this period, so
when a 5% category is used up the next best card takes over.

The ranking is plain arithmetic on data passed in (Card and Purchase), so demo mode and the MCP tool run the same
code as the page.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import cardpay
from financeapp import offers as card_offers
from financeapp.categorize import merchant_key, normalize_merchant
from financeapp.config import PACKAGE_DIR
from financeapp.models import Account, Balance, CardLink, CardStatement, Category, Preference, Transaction

SAMPLE_CENTS = 100_00  # rankings show what a $100 purchase earns
CARD_KEY = re.compile(r"card:\d{1,9}")  # a point value set for one card, by account id
HISTORY_DAYS = 365
CHANNELS = ("in store", "online")

# The categories card rewards are written in, in the order the Best card table shows them.
CATEGORIES = {
    "dining": "Dining",
    "groceries": "Groceries",
    "gas": "Gas",
    "ev_charging": "EV charging",
    "flights": "Flights",
    "hotels": "Hotels",
    "car_rental": "Car rentals",
    "travel": "Other travel",
    "transit": "Transit and rideshare",
    "streaming": "Streaming",
    "drugstores": "Drugstores",
    "online_retail": "Online shopping",
    "phone": "Phone plans",
    "entertainment": "Entertainment",
    "home_improvement": "Home improvement",
    "utilities": "Utilities",
    "wholesale": "Warehouse clubs",
    "superstores": "Superstores",
    "other": "Everything else",
}
# Where a purchase in this category usually happens, for the Best card table.
TABLE_CHANNEL = {"online_retail": "online", "streaming": "online", "phone": "online", "utilities": "online"}
TRAVEL = {"flights", "hotels", "car_rental"}
SHOPPING = {"superstores", "wholesale", "home_improvement", "drugstores"}  # can be bought online as online shopping

# Merchant category codes (ISO 18245) to reward tags. Ranges are inclusive.
MCC_RANGES = [
    (3000, 3350, "flights"),
    (3351, 3500, "car_rental"),
    (3501, 3999, "hotels"),
]
MCC_TAGS = {
    "4511": "flights",
    "7011": "hotels",
    "7512": "car_rental",
    "7513": "car_rental",
    "4411": "travel",
    "4722": "travel",
    "4723": "travel",
    "7012": "travel",
    "7033": "travel",
    "4111": "transit",
    "4112": "transit",
    "4121": "transit",
    "4131": "transit",
    "4784": "transit",
    "4789": "transit",
    "7523": "transit",
    "5811": "dining",
    "5812": "dining",
    "5813": "dining",
    "5814": "dining",
    "5411": "groceries",
    "5300": "wholesale",
    "5310": "superstores",
    "5311": "superstores",
    "5541": "gas",
    "5542": "gas",
    "5983": "gas",
    "5552": "ev_charging",
    "5912": "drugstores",
    "4812": "phone",
    "4814": "phone",
    "4899": "streaming",
    "5815": "streaming",
    "5816": "streaming",
    "5817": "streaming",
    "5818": "streaming",
    "4900": "utilities",
    "7832": "entertainment",
    "7922": "entertainment",
    "7929": "entertainment",
    "7941": "entertainment",
    "7991": "entertainment",
    "7996": "entertainment",
    "5200": "home_improvement",
    "5211": "home_improvement",
    "5251": "home_improvement",
}
MCC_NAMES = {
    "dining": "restaurant",
    "groceries": "supermarket",
    "wholesale": "warehouse club",
    "superstores": "discount or department store",
    "gas": "gas station",
    "ev_charging": "EV charging",
    "flights": "airline",
    "hotels": "hotel",
    "car_rental": "car rental",
    "travel": "travel",
    "transit": "transit",
    "drugstores": "drugstore",
    "phone": "phone company",
    "streaming": "digital media and streaming",
    "utilities": "utility",
    "entertainment": "entertainment",
    "home_improvement": "home improvement store",
}
# Plaid's detailed categories, for purchases that came without an MCC.
PFC_TAGS = {
    "FOOD_AND_DRINK_RESTAURANT": "dining",
    "FOOD_AND_DRINK_FAST_FOOD": "dining",
    "FOOD_AND_DRINK_COFFEE": "dining",
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": None,
    "FOOD_AND_DRINK_GROCERIES": "groceries",
    "GENERAL_MERCHANDISE_SUPERSTORES": "superstores",
    "GENERAL_MERCHANDISE_DISCOUNT_STORES": "superstores",
    "GENERAL_MERCHANDISE_DEPARTMENT_STORES": "superstores",
    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES": "online_retail",
    "TRANSPORTATION_GAS": "gas",
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": "transit",
    "TRANSPORTATION_PUBLIC_TRANSIT": "transit",
    "TRANSPORTATION_PARKING": "transit",
    "TRANSPORTATION_TOLLS": "transit",
    "TRAVEL_FLIGHTS": "flights",
    "TRAVEL_LODGING": "hotels",
    "TRAVEL_RENTAL_CARS": "car_rental",
    "TRAVEL_OTHER_TRAVEL": "travel",
    "ENTERTAINMENT_TV_AND_MOVIES": "streaming",
    "ENTERTAINMENT_MUSIC_AND_AUDIO": "streaming",
    "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS": "entertainment",
    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS": "drugstores",
    "RENT_AND_UTILITIES_TELEPHONE": "phone",
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": "utilities",
    "RENT_AND_UTILITIES_WATER": "utilities",
    "HOME_IMPROVEMENT_HARDWARE": "home_improvement",
    "HOME_IMPROVEMENT_FURNITURE": "home_improvement",
}
ISSUERS = {
    "American Express": ("american express", "amex"),
    "Chase": ("chase",),
    "Citi": ("citi", "citibank"),
    "Capital One": ("capital one",),
    "Discover": ("discover",),
    "Wells Fargo": ("wells fargo",),
    "Bank of America": ("bank of america", "bofa"),
    "Goldman Sachs": ("apple", "goldman"),
    "Fidelity": ("fidelity", "elan"),
}


# ---- the catalog and the store directory


@cache
def catalog() -> dict:
    data = json.loads((PACKAGE_DIR / "data" / "cards.json").read_text())
    data["by_id"] = {c["id"]: c for c in data["cards"]}
    return data


@cache
def directory() -> dict[str, dict]:
    merchants = json.loads((PACKAGE_DIR / "data" / "merchants.json").read_text())["merchants"]
    return {mid: {**m, "id": mid} for mid, m in merchants.items()}


@cache
def _directory_keys() -> list[tuple[str, str]]:
    return sorted(((k, mid) for mid, m in directory().items() for k in m["keys"]), key=lambda x: -len(x[0]))


def known_merchant(name: str) -> dict | None:
    """The directory entry for a merchant name: an exact key, or a key of two words or more it starts with."""
    key = merchant_key(name)
    for k, mid in _directory_keys():
        if key == k or (" " in k and key.startswith(k + " ")):
            return directory()[mid]
    return None


def product(product_id: str | None) -> dict | None:
    return catalog()["by_id"].get(product_id or "")


def suggest(institution: str, name: str) -> str | None:
    """The catalog card a bank account most likely is, from the bank's name and the account's name."""
    text = normalize_merchant(f"{institution} {name}")
    issuers = [i for i, words in ISSUERS.items() if any(w in text for w in words)]
    best, best_len = None, 0
    for c in catalog()["cards"]:
        if c["issuer"] not in issuers:
            continue
        for word in c["match"]:
            if word in text and len(word) > best_len:
                best, best_len = c["id"], len(word)
    return best


# ---- describing a purchase


def mcc_tag(code: str | None) -> str | None:
    if not code or not code.isdigit():
        return None
    if code in MCC_TAGS:
        return MCC_TAGS[code]
    n = int(code)
    return next((tag for lo, hi, tag in MCC_RANGES if lo <= n <= hi), None)


def tags_for(
    merchant: dict | None, mcc: str | None, provider_category: str | None, channel: str | None
) -> frozenset[str]:
    tags: set[str] = set()
    if merchant:
        tags |= set(merchant["tags"])
        tags.add("m:" + merchant["id"])
    by_code = mcc_tag(mcc)
    if by_code:
        tags.add(by_code)
    elif not (merchant and merchant["tags"]) and provider_category:
        tag = PFC_TAGS.get(provider_category)
        if tag:
            tags.add(tag)
    finish(tags, channel)
    return frozenset(tags)


def finish(tags: set[str], channel: str | None) -> None:
    if tags & TRAVEL:
        tags.add("travel")
    # Card grocery bonuses leave out superstores and warehouse clubs, whatever the store's code says.
    if tags & {"superstores", "wholesale"}:
        tags.discard("groceries")
    plain = {t for t in tags if not t.startswith("m:")}
    if channel == "online" and (not plain or plain <= SHOPPING):
        tags.add("online_retail")


# ---- cards and purchases


@dataclass
class Card:
    account_id: int
    label: str  # "Amex Gold ••1008"
    product: dict
    choice: str | None = None
    role: str = "active"
    # Where you'd rather use this card when it ties with another: category ids, or "*" for everywhere.
    favored: frozenset[str] = frozenset()

    @property
    def program(self) -> str:
        return self.product["program"]

    def favor(self, category: str) -> int:
        """How much you'd rather use this card for a category, when it earns the same as another."""
        return 2 if category in self.favored else 1 if "*" in self.favored else 0


@dataclass
class Purchase:
    id: int | str
    day: date
    cents: int  # spent, positive
    merchant: str
    key: str  # merchant_key
    tags: frozenset[str]
    channel: str | None
    account_id: int
    mcc: str | None = None
    website: str | None = None
    provider_category: str | None = None


@dataclass
class Earned:
    cents: float  # what it's worth, at your cents per point
    units: float  # points, miles or cash back cents earned
    rate: float  # the best rate that applied
    rule: dict | None  # None: the card's base rate
    capped: bool = False  # part of it fell past a cap


@dataclass
class Ledger:
    """How much of each capped bonus has been spent, per card and period."""

    used: dict = field(default_factory=lambda: defaultdict(int))

    def key(self, card: Card, index: int, rule: dict, on: date) -> tuple:
        group = rule.get("cap_group") or f"rule{index}"
        return (card.account_id, group, period_key(rule.get("period", "year"), on))


def period_key(period: str, on: date) -> str:
    if period == "month":
        return f"{on.year}-{on.month:02d}"
    if period == "quarter":
        return f"{on.year}-Q{(on.month - 1) // 3 + 1}"
    return str(on.year)


def period_start(period: str, on: date) -> date:
    if period == "month":
        return on.replace(day=1)
    if period == "quarter":
        return date(on.year, 3 * ((on.month - 1) // 3) + 1, 1)
    return date(on.year, 1, 1)


def categories_of(rule: dict, card: Card, on: date) -> list[str] | None:
    if "rotating" in rule:
        return rule["rotating"].get(period_key("quarter", on), [])
    if rule.get("choice"):
        return [card.choice] if card.choice else []
    c = rule.get("category")
    if c is None:
        return None
    return c if isinstance(c, list) else [c]


def matches(rule: dict, card: Card, tags: frozenset[str], channel: str | None, on: date) -> bool:
    if rule.get("portal") or (rule.get("until") and on.isoformat() > rule["until"]):
        return False
    if rule.get("channel") and rule["channel"] != channel:
        return False
    cats = categories_of(rule, card, on)
    merchants = rule.get("merchants")
    if cats is not None and not any(c in tags for c in cats):
        return False
    if merchants and not any("m:" + m in tags for m in merchants):
        return False
    return cats is not None or bool(merchants)


def earn(
    card: Card,
    p_tags: frozenset[str],
    channel: str | None,
    cents: int,
    on: date,
    ledger: Ledger | None,
    values: dict[str, float],
    record: bool = False,
) -> Earned:
    """What a purchase earns on a card: the best matching bonus up to its cap, the rest at the next best rate."""
    rules = sorted(
        ((i, r) for i, r in enumerate(card.product["earn"]) if matches(r, card, p_tags, channel, on)),
        key=lambda ir: -ir[1]["rate"],
    )
    left = cents
    units = 0.0
    top_rate, top_rule, capped = None, None, False
    for i, r in rules:
        if left <= 0:
            break
        part = left
        if r.get("cap") and ledger is not None:
            k = ledger.key(card, i, r, on)
            room = max(0, r["cap"] * 100 - ledger.used[k])
            part = min(left, room)
            if part < left:
                capped = True
            if record:
                ledger.used[k] += part
        if part <= 0:
            continue
        units += part / 100 * r["rate"]
        left -= part
        if top_rate is None:
            top_rate, top_rule = r["rate"], r
    if left > 0:
        units += left / 100 * card.product["base"]
        if top_rate is None:
            top_rate = card.product["base"]
    cpp = worth(card, values)
    return Earned(cents=units * cpp, units=units, rate=top_rate, rule=top_rule, capped=capped)


def worth(card: Card, values: dict[str, float]) -> float:
    """Cents a point on this card: what you set for the card, else for its program."""
    return values.get(f"card:{card.account_id}", values.get(card.program, 1.0))


def units_label(card: Card, rate: float) -> str:
    unit = catalog()["programs"][card.program]["unit"]
    number = f"{rate:g}"
    return f"{number}%" if unit == "%" else f"{number}x"


def cap_note(card: Card, rule: dict | None, ledger: Ledger, on: date) -> str | None:
    if not rule or not rule.get("cap"):
        return None
    index = card.product["earn"].index(rule)
    used = ledger.used[ledger.key(card, index, rule, on)]
    period = {"month": "this month", "quarter": "this quarter"}.get(rule.get("period", "year"), "this year")
    return f"${used / 100:,.0f} of ${rule['cap']:,} used {period}"


def portal_hint(card: Card, tags: frozenset[str], on: date, values: dict[str, float], earned: Earned) -> str | None:
    """A better rate this card pays when booked through the bank's own travel or entertainment site."""
    best = None
    for r in card.product["earn"]:
        if not r.get("portal") or r["rate"] <= earned.rate:
            continue
        plain = {k: v for k, v in r.items() if k != "portal"}
        if matches(plain, card, tags, None, on) and (best is None or r["rate"] > best["rate"]):
            best = r
    if best is None:
        return None
    return f"{units_label(card, best['rate'])} if booked through {best['portal']}"


def rank(
    cards: list[Card],
    tags: frozenset[str],
    channel: str | None,
    on: date,
    ledger: Ledger,
    values: dict[str, float],
    cents: int = SAMPLE_CENTS,
    merchant: dict | None = None,
) -> list[dict]:
    """Every active card, best first, for one purchase."""
    accepts = ((merchant or {}).get("accepts") or {}).get(channel or "") if merchant else None
    category = bucket(tags)
    rows = []
    for card in cards:
        if card.role != "active":
            continue
        e = earn(card, tags, channel, cents, on, ledger, values)
        warnings = []
        if accepts and card.product["network"] not in accepts:
            warnings.append(merchant.get("accepts_note") or "This store may not take this card.")
        if e.rule and e.rule.get("activate"):
            warnings.append("Activate this quarter's bonus first.")
        if e.rule and e.rule.get("top"):
            warnings.append("Only your top category each billing cycle earns this.")
        note = cap_note(card, e.rule, ledger, on)
        rows.append(
            {
                "account_id": card.account_id,
                "card": card.label,
                "product": card.product["short"],
                "color": card.product["color"],
                "rate": units_label(card, e.rate),
                "percent": round(e.cents / cents * 100, 2) if cents else 0,
                "value_cents": round(e.cents),
                "why": e.rule["note"] if e.rule else (card.product.get("base_note") or "Everything else"),
                "bonus": e.rule is not None,
                "cap": note,
                "capped": e.capped,
                "portal": portal_hint(card, tags, on, values, e),
                "warnings": warnings,
                "blocked": bool(accepts and card.product["network"] not in accepts),
                "favor": card.favor(category),
            }
        )
    # Ties go to the card you picked for this category, then your default card.
    rows.sort(key=lambda r: (r["blocked"], -r["value_cents"], -r["favor"], r["card"]))
    return rows


def replay(cards: list[Card], purchases: list[Purchase], since: date, values: dict[str, float]) -> Ledger:
    """The caps used so far: each card's own purchases from the start of the year, in order."""
    ledger = Ledger()
    by_id = {c.account_id: c for c in cards}
    for p in sorted(purchases, key=lambda p: (p.day, str(p.id))):
        card = by_id.get(p.account_id)
        if card is not None and p.day >= since:
            earn(card, p.tags, p.channel, p.cents, p.day, ledger, values, record=True)
    return ledger


# ---- the views


def best_by_category(
    cards: list[Card], purchases: list[Purchase], on: date, values: dict[str, float], ledger: Ledger
) -> list[dict]:
    spent: Counter = Counter()
    since = on - timedelta(days=HISTORY_DAYS)
    for p in purchases:
        if p.day >= since:
            spent[bucket(p.tags)] += p.cents
    rows = []
    for cid, label in CATEGORIES.items():
        if cid == "superstores":
            continue
        tags = frozenset() if cid == "other" else frozenset({cid, *({"travel"} if cid in TRAVEL else set())})
        channel = TABLE_CHANNEL.get(cid, "in store")
        ranked = rank(cards, tags, channel, on, ledger, values)
        rows.append(
            {
                "category": cid,
                "label": label,
                "spent_cents": spent.get(cid, 0),
                "best": ranked[0] if ranked else None,
                "next": ranked[1] if len(ranked) > 1 else None,
                "tied": [
                    {"account_id": r["account_id"], "card": r["card"]}
                    for r in ranked
                    if not r["blocked"] and r["value_cents"] == ranked[0]["value_cents"]
                ]
                if ranked
                else [],
            }
        )
    return rows


def history_matches(purchases: list[Purchase], query: str) -> list[dict]:
    """Stores you've bought from whose name has the words typed, most purchases first."""
    q = merchant_key(query)
    groups: dict[str, list[Purchase]] = defaultdict(list)
    for p in purchases:
        if q and (p.key == q or p.key.startswith(q) or f" {q}" in f" {p.key}"):
            groups[p.key].append(p)
    out = []
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        codes = Counter(p.mcc for p in items if p.mcc)
        channels = Counter(p.channel for p in items if p.channel in CHANNELS)
        latest = max(items, key=lambda p: p.day)
        out.append(
            {
                "key": key,
                "name": latest.merchant,
                "count": len(items),
                "mcc": codes.most_common(1)[0][0] if codes else None,
                "channels": dict(channels),
                "website": next((p.website for p in items if p.website), None),
                "provider_category": latest.provider_category,
                "tags": latest.tags,
            }
        )
    return out[:5]


def bucket(tags: frozenset[str]) -> str:
    """The one row of the Best card table a purchase counts toward. Superstores earn the base rate nearly
    everywhere, so they count as everything else."""
    cat = next((t for t in CATEGORIES if t in tags), "other")
    return "other" if cat == "superstores" else cat


def describe(tags: frozenset[str]) -> list[str]:
    return [CATEGORIES[t] for t in CATEGORIES if t in tags and t != "other"] or ["Everything else"]


def lookup(
    cards: list[Card],
    purchases: list[Purchase],
    on: date,
    values: dict[str, float],
    ledger: Ledger,
    query: str = "",
    pick: str | None = None,
    kind: str | None = None,
    cents: int = SAMPLE_CENTS,
    offers: list[card_offers.Offer] | None = None,
) -> dict:
    """Type a store: the best card for it in store and online, and why. A card's offer at the store (Chase Offers
    and the like) counts on top of what the card earns there."""
    query = " ".join(query.split())[:80]
    seen = history_matches(purchases, query) if query else []
    known = known_merchant(query) if query else None
    matches_out = []
    if known:
        matches_out.append({"id": "d:" + known["id"], "name": known["name"], "source": "known"})
    for h in seen:
        if known is None or known_merchant(h["name"]) is not known:
            matches_out.append({"id": "h:" + h["key"], "name": h["name"], "source": "history", "count": h["count"]})
    chosen = (
        next((m for m in matches_out if m["id"] == pick), None) if pick else (matches_out[0] if matches_out else None)
    )

    merchant, history, how = None, None, None
    if chosen and chosen["id"].startswith("d:"):
        merchant = directory()[chosen["id"][2:]]
        history = next((h for h in seen if known_merchant(h["name"]) is merchant), None)
    elif chosen:
        history = next(h for h in seen if h["key"] == chosen["id"][2:])
        merchant = known_merchant(history["name"])

    if kind in CATEGORIES:
        tags = frozenset() if kind == "other" else frozenset({kind})
        fin = set(tags)
        finish(fin, None)
        tags = frozenset(fin)
        how = "You said what kind of store it is."
        name = query or CATEGORIES[kind]
    elif merchant or history:
        mcc = history["mcc"] if history else None
        pc = history["provider_category"] if history else None
        tags = tags_for(merchant, mcc, pc, None)
        name = merchant["name"] if merchant else history["name"]
        if history and mcc and mcc_tag(mcc):
            how = f"{purchases_there(history['count']).capitalize()} coded {mcc} ({MCC_NAMES[mcc_tag(mcc)]})."
        elif merchant:
            how = "A store the app knows."
        else:
            how = f"From how {purchases_there(history['count'])} categorized."
    else:
        return {"query": query, "found": False, "matches": [], "kinds": _kinds()}

    # Where you buy there: both when the store sells both ways or your purchases were both, else the one you use.
    ways = set((history or {}).get("channels") or {})
    if merchant:
        ways |= set(CHANNELS) if merchant.get("channel", "both") == "both" else {merchant["channel"]}
    channels = [ch for ch in CHANNELS if ch in ways] or list(CHANNELS)
    usual = channels[0] if len(channels) == 1 else "both"
    keys = {merchant_key(query)} if query else set()
    if merchant:
        keys |= {merchant_key(merchant["name"]), *merchant["keys"]}
    if history:
        keys.add(history["key"])
    here = [o for o in offers or [] if any(card_offers.same_store(o.key, k) for k in keys if k)]
    results = {}
    for ch in channels:
        t = set(tags)
        finish(t, ch)
        results[ch] = with_offers(
            rank(cards, frozenset(t), ch, on, ledger, values, cents, merchant=merchant),
            here,
            cards,
            ch,
            on,
            values,
            cents,
        )
    labels = {c.account_id: c.label for c in cards}
    same = len(results) == 2 and _winner(results["in store"]) == _winner(results["online"])
    return {
        "query": query,
        "found": True,
        "name": name,
        "matches": matches_out,
        "chosen": chosen["id"] if chosen else None,
        "kind": describe(tags),
        "how": how,
        "website": (history or {}).get("website"),
        "usual": usual,
        "channels": [{"channel": ch, "cards": rows} for ch, rows in results.items()],
        "same": same,
        "kinds": _kinds(),
        "offers": [{**card_offers.note_for(o, on), "card": labels.get(o.account_id, "Another card")} for o in here],
    }


def with_offers(
    rows: list[dict],
    here: list[card_offers.Offer],
    cards: list[Card],
    channel: str,
    on: date,
    values: dict[str, float],
    cents: int,
) -> list[dict]:
    """Adds each card's best offer at the store to what it earns, and ranks again."""
    if not here:
        return rows
    by_id = {c.account_id: c for c in cards}
    for row in rows:
        card = by_id[row["account_id"]]
        cpp = worth(card, values)
        earned = row["value_cents"]
        mine = [(card_offers.adds(o, cents, cpp, earned), o) for o in here if o.account_id == card.account_id]
        mine = [(v, o) for v, o in mine if card_offers.fits(o, channel)]
        if not mine:
            continue
        v, o = max(mine, key=lambda m: m[0])
        row["offer"] = {**card_offers.note_for(o, on), "value_cents": v}
        row["value_cents"] += v
        row["percent"] = round(row["value_cents"] / cents * 100, 2) if cents else 0
        if not o.added:
            row["warnings"].append(card_offers.WHERE.get(o.issuer, card_offers.WHERE["other"]))
    rows.sort(key=lambda r: (r["blocked"], -r["value_cents"], -r["favor"], r["card"]))
    return rows


def purchases_there(n: int) -> str:
    return f"your {n} purchases there were" if n != 1 else "your 1 purchase there was"


def _winner(rows: list[dict]) -> tuple | None:
    return (rows[0]["account_id"], rows[0]["value_cents"]) if rows else None


def _kinds() -> list[dict]:
    return [{"id": k, "label": v} for k, v in CATEGORIES.items() if k not in ("superstores",)]


def missed(cards: list[Card], purchases: list[Purchase], start: date, end: date, values: dict[str, float]) -> dict:
    """What your card purchases earned against what the best card in your wallet would have earned."""
    by_id = {c.account_id: c for c in cards}
    active = [c for c in cards if c.role == "active"]
    actual_ledger, best_ledger = Ledger(), Ledger()
    total_actual = total_best = spent = 0.0
    by_category: dict[str, dict] = {}
    rows = []
    for p in sorted(purchases, key=lambda p: (p.day, str(p.id))):
        card = by_id.get(p.account_id)
        if card is None or p.day > end:
            continue
        got = earn(card, p.tags, p.channel, p.cents, p.day, actual_ledger, values, record=True)
        cat = bucket(p.tags)
        options = [(earn(c, p.tags, p.channel, p.cents, p.day, best_ledger, values), c) for c in active]
        best, best_card = (
            max(options, key=lambda x: (round(x[0].cents, 6), x[1].favor(cat))) if options else (got, card)
        )
        if best.cents < got.cents:
            best, best_card = got, card
        earn(best_card, p.tags, p.channel, p.cents, p.day, best_ledger, values, record=True)
        if p.day < start:
            continue
        spent += p.cents
        total_actual += got.cents
        total_best += best.cents
        row = by_category.setdefault(
            cat,
            {
                "category": cat,
                "label": CATEGORIES[cat],
                "spent_cents": 0,
                "earned_cents": 0.0,
                "best_cents": 0.0,
                "cards": Counter(),
            },
        )
        row["spent_cents"] += p.cents
        row["earned_cents"] += got.cents
        row["best_cents"] += best.cents
        if best.cents - got.cents >= 0.5:
            row["cards"][best_card.label] += 1
            rows.append(
                {
                    "id": str(p.id),
                    "date": p.day.isoformat(),
                    "merchant": p.merchant,
                    "cents": p.cents,
                    "used": card.label,
                    "earned_cents": round(got.cents),
                    "best": best_card.label,
                    "best_cents": round(best.cents),
                    "missed_cents": round(best.cents - got.cents),
                }
            )
    cats = []
    for row in sorted(by_category.values(), key=lambda r: -(r["best_cents"] - r["earned_cents"])):
        cats.append(
            {
                "category": row["category"],
                "label": row["label"],
                "spent_cents": row["spent_cents"],
                "earned_cents": round(row["earned_cents"]),
                "best_cents": round(row["best_cents"]),
                "missed_cents": round(row["best_cents"] - row["earned_cents"]),
                "use": row["cards"].most_common(1)[0][0] if row["cards"] else None,
            }
        )
    rows.sort(key=lambda r: -r["missed_cents"])
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "spent_cents": round(spent),
        "earned_cents": round(total_actual),
        "best_cents": round(total_best),
        "missed_cents": round(total_best - total_actual),
        "categories": cats,
        "purchases": rows[:12],
    }


# ---- reading it from the database


def values_default() -> dict[str, float]:
    return {k: v["cpp"] for k, v in catalog()["programs"].items()}


def values(db: DbSession) -> dict[str, float]:
    """Cents a point for each program, plus "card:<account id>" for a card you value on its own."""
    p = db.get(Preference, "cards")
    mine = (p.value if p else {}).get("cpp", {})
    out = {k: float(mine.get(k, v["cpp"])) for k, v in catalog()["programs"].items()}
    out.update({k: float(v) for k, v in mine.items() if CARD_KEY.fullmatch(k)})
    return out


def set_values(db: DbSession, cpp: dict[str, float]) -> None:
    p = db.get(Preference, "cards")
    if p is None:
        p = Preference(key="cards", value={})
        db.add(p)
    given = {k: round(float(v), 2) for k, v in cpp.items() if k in catalog()["programs"] or CARD_KEY.fullmatch(k)}
    p.value = {**p.value, "cpp": {**p.value.get("cpp", {}), **given}}


def prefer(db: DbSession) -> dict:
    """Which card to use when cards tie: a default, and one per category."""
    p = db.get(Preference, "cards")
    mine = (p.value if p else {}).get("prefer", {})
    return {"default": mine.get("default"), "by": dict(mine.get("by", {}))}


def set_prefer(db: DbSession, category: str | None, account_id: int | None) -> None:
    p = db.get(Preference, "cards")
    if p is None:
        p = Preference(key="cards", value={})
        db.add(p)
    mine = prefer(db)
    if category is None:
        mine["default"] = account_id
    elif account_id is None:
        mine["by"].pop(category, None)
    else:
        mine["by"][category] = account_id
    p.value = {**p.value, "prefer": mine}


def favored(account_id: int, chosen: dict) -> frozenset[str]:
    tags = {c for c, a in chosen.get("by", {}).items() if a == account_id}
    if chosen.get("default") == account_id:
        tags.add("*")
    return frozenset(tags)


def card_accounts(db: DbSession) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .where(Account.type == "credit", Account.hidden.is_(False), Account.is_authoritative.is_(True))
            .order_by(Account.institution, Account.name)
        )
    )


def label_of(a: Account, link: CardLink | None) -> str:
    p = product(link.product) if link else None
    name = p["short"] if p and not p["id"].startswith("flat") else a.name
    return f"{name} ••{a.mask}" if a.mask else name


def load_cards(db: DbSession) -> tuple[list[Card], list[Account], dict[int, CardLink]]:
    accounts = card_accounts(db)
    links = {link.account_id: link for link in db.scalars(select(CardLink))}
    chosen = prefer(db)
    cards = []
    for a in accounts:
        link = links.get(a.id)
        p = product(link.product) if link else None
        if p is not None and link.role != "closed":
            cards.append(Card(a.id, label_of(a, link), p, link.choice, link.role, favored(a.id, chosen)))
    return cards, accounts, links


def load_purchases(db: DbSession, account_ids: list[int], since: date) -> list[Purchase]:
    if not account_ids:
        return []
    kinds = {c.id: c.kind for c in db.scalars(select(Category))}
    out = []
    for t in db.scalars(
        select(Transaction).where(
            Transaction.account_id.in_(account_ids),
            Transaction.date >= since,
            Transaction.amount_cents < 0,
            Transaction.transfer_group_id.is_(None),
        )
    ):
        if t.category_id and kinds.get(t.category_id) in ("transfer", "income"):
            continue
        m = known_merchant(t.merchant)
        out.append(
            Purchase(
                id=t.id,
                day=t.date,
                cents=-t.amount_cents,
                merchant=t.merchant,
                key=merchant_key(t.merchant),
                tags=tags_for(m, t.mcc, t.provider_category, t.channel),
                channel=t.channel,
                account_id=t.account_id,
                mcc=t.mcc,
                website=t.website,
                provider_category=t.provider_category,
            )
        )
    return out


@dataclass
class Wallet:
    cards: list[Card]
    purchases: list[Purchase]  # card purchases, for caps and missed points
    everything: list[Purchase]  # card and bank purchases, for what you spend and where you shop
    values: dict[str, float]
    on: date
    offers: list[card_offers.Offer] = field(default_factory=list)  # Chase Offers and the like, not expired

    def ledger(self) -> Ledger:
        return replay(self.cards, self.purchases, date(self.on.year, 1, 1), self.values)


def wallet(db: DbSession, on: date, everything: bool = True) -> Wallet:
    cards, accounts, _ = load_cards(db)
    since = on - timedelta(days=HISTORY_DAYS)
    card_ids = [a.id for a in accounts]
    purchases = load_purchases(db, card_ids, since)
    if everything:
        spending_ids = [
            a.id
            for a in db.scalars(
                select(Account).where(
                    Account.type == "depository", Account.hidden.is_(False), Account.is_authoritative.is_(True)
                )
            )
        ]
        bank = load_purchases(db, spending_ids, since)
    else:
        bank = []
    return Wallet(cards, purchases, purchases + bank, values(db), on, card_offers.load(db, on))


def card_values(cards: list[Card], vals: dict[str, float]) -> list[dict]:
    """One row per card that earns points (not cash), since two cards on one program can be worth different
    amounts: Ultimate Rewards from a Freedom card, say, go further once moved to a Sapphire card."""
    programs = catalog()["programs"]
    return [
        {
            "account_id": c.account_id,
            "card": c.label,
            "program": c.program,
            "program_name": programs[c.program]["name"],
            "cpp": worth(c, vals),
            "default": vals[c.program],
            "own": f"card:{c.account_id}" in vals,
            "pairs": c.product.get("pairs"),
            "transfers": bool(programs[c.program].get("transfers")),
        }
        for c in cards
        if programs[c.program]["unit"] != "%"
    ]


def program_rows(vals: dict[str, float]) -> list[dict]:
    return [
        {
            "id": k,
            "name": v["name"],
            "unit": v["unit"],
            "cpp": vals[k],
            "default": v["cpp"],
            "transfers": bool(v.get("transfers")),
        }
        for k, v in catalog()["programs"].items()
    ]


def product_summary(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "short": p["short"],
        "issuer": p["issuer"],
        "network": p["network"],
        "color": p["color"],
        "annual_fee": p["annual_fee"],
        "choices": [{"id": c, "label": CATEGORIES[c]} for c in p.get("choices", [])],
    }


def product_detail(p: dict, choice: str | None = None) -> dict:
    unit = catalog()["programs"][p["program"]]["unit"]

    def rate(r: float) -> str:
        return f"{r:g}%" if unit == "%" else f"{r:g}x"

    earn_rows = [
        {
            "rate": rate(r["rate"]),
            "what": r["note"],
            "portal": r.get("portal"),
            "until": r.get("until"),
            "activate": bool(r.get("activate")),
        }
        for r in sorted(p["earn"], key=lambda r: -r["rate"])
    ]
    earn_rows.append(
        {
            "rate": rate(p["base"]),
            "what": p.get("base_note") or "Everything else",
            "portal": None,
            "until": None,
            "activate": False,
        }
    )
    yearly = {"month": 12, "quarter": 4, "half": 2, "year": 1}
    return {
        **product_summary(p),
        "program": catalog()["programs"][p["program"]]["name"],
        "foreign_fee": p["foreign_fee"],
        "earn": earn_rows,
        "credits": [{**c, "per_year": round(c["amount"] * yearly[c["period"]], 2)} for c in p["credits"]],
        "credits_per_year": round(sum(c["amount"] * yearly[c["period"]] for c in p["credits"]), 2),
        "perks": p["perks"],
        "pairs": p.get("pairs"),
        "choices": [{"id": c, "label": CATEGORIES[c]} for c in p.get("choices", [])],
        "choice": choice,
        "source": p["source"],
        "verified": p["verified"],
        "checked": catalog()["checked"],
    }


@dataclass
class CardAccount:
    """What the page shows about one card account besides its rewards."""

    account_id: int
    name: str
    institution: str
    mask: str | None
    link: CardLink | None
    balance_cents: int | None = None
    balance_as_of: date | None = None
    limit_cents: int | None = None
    statement: dict | None = None


def statement_json(s: CardStatement, payment: cardpay.Payment | None = None) -> dict:
    def iso(d: date | None) -> str | None:
        return d.isoformat() if d else None

    return {
        "purchase_apr": float(s.purchase_apr) if s.purchase_apr is not None else None,
        "aprs": s.aprs,
        "statement_cents": s.statement_cents,
        "statement_date": iso(s.statement_date),
        "minimum_cents": s.minimum_cents,
        "due_date": iso(s.due_date),
        "last_payment_cents": s.last_payment_cents,
        "last_payment_date": iso(s.last_payment_date),
        "overdue": s.overdue,
        "as_of": iso(s.as_of),
        "payment": payment.json() if payment else None,  # paid since the statement, and what's left (cardpay)
    }


def page(db: DbSession, on: date) -> dict:
    """The Credit Cards page: every card with its balance, rates and statement, and the best card by category."""
    w = wallet(db, on)
    _, accounts, links = load_cards(db)
    statements = {s.account_id: s for s in db.scalars(select(CardStatement))}
    payments = cardpay.load(db, on)
    rows = []
    for a in accounts:
        bal = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        s = statements.get(a.id)
        rows.append(
            CardAccount(
                account_id=a.id,
                name=a.name,
                institution=a.institution,
                mask=a.mask,
                link=links.get(a.id),
                balance_cents=bal.current_cents if bal else None,
                balance_as_of=bal.date if bal else None,
                limit_cents=a.credit_limit_cents,
                statement=statement_json(s, payments.get(a.id)) if s else None,
            )
        )
    return assemble(w, rows)


def assemble(w: Wallet, accounts: list[CardAccount], demo: bool = False) -> dict:
    on = w.on
    ledger = w.ledger()
    month_start = on.replace(day=1)
    rows = []
    for a in accounts:
        link = a.link
        p = product(link.product) if link else None
        card = next((c for c in w.cards if c.account_id == a.account_id), None)
        mine = [x for x in w.purchases if x.account_id == a.account_id]
        earned = 0.0
        if card is not None:
            year_ledger = Ledger()
            for x in sorted((x for x in mine if x.day.year == on.year), key=lambda x: (x.day, str(x.id))):
                earned += earn(card, x.tags, x.channel, x.cents, x.day, year_ledger, w.values, record=True).cents
        balance, limit = a.balance_cents, a.limit_cents
        label = f"{p['short'] if p and not p['id'].startswith('flat') else a.name}"
        rows.append(
            {
                "account_id": a.account_id,
                "name": a.name,
                "institution": a.institution,
                "mask": a.mask,
                "label": f"{label} ••{a.mask}" if a.mask else label,
                "linked": p is not None,
                "suggested": None if p else suggest(a.institution, a.name),
                "product": product_detail(p, link.choice) if p else None,
                "role": link.role if link else None,
                "renewal_month": link.renewal_month if link else None,
                "balance_cents": balance,
                "balance_as_of": a.balance_as_of.isoformat() if a.balance_as_of else None,
                "limit_cents": limit,
                "utilization": round(balance / limit * 100, 1) if balance is not None and limit else None,
                "statement": a.statement,
                "spent_month_cents": sum(x.cents for x in mine if x.day >= month_start),
                "earned_year_cents": round(earned),
                "caps": caps(card, ledger, on) if card else [],
            }
        )
    return {
        "demo": demo,
        "as_of": on.isoformat(),
        "cards": rows,
        "best": best_by_category(w.cards, w.everything, on, w.values, ledger),
        "prefer": {
            "default": next((c.account_id for c in w.cards if "*" in c.favored), None),
            "by": {t: c.account_id for c in w.cards for t in sorted(c.favored) if t != "*"},
        },
        "programs": program_rows(w.values),
        "card_values": card_values(w.cards, w.values),
        "catalog": [product_summary(p) for p in catalog()["cards"]],
        "checked": catalog()["checked"],
        "offers": offers_section(w, rows),
    }


def offers_section(w: Wallet, rows: list[dict]) -> dict:
    """Your card offers, the ones at stores you shop at first, then the ones ending soonest."""
    labels = {r["account_id"]: r["label"] for r in rows}
    since = w.on - timedelta(days=HISTORY_DAYS)
    items = []
    for o in w.offers:
        if o.account_id not in labels:
            continue
        count, spent = card_offers.visits(w.everything, o, since)
        items.append(
            {
                "id": o.id,
                "account_id": o.account_id,
                "card": labels[o.account_id],
                "issuer": o.issuer,
                "name": card_offers.OFFER_NAMES.get(o.issuer, "Card offer"),
                "merchant": o.merchant,
                "label": o.label,
                "kind": o.kind,
                "value": o.value,
                "channel": o.channel,
                "min_spend_cents": o.min_spend_cents,
                "max_cents": o.max_cents,
                "expires": o.expires.isoformat() if o.expires else None,
                "ends": card_offers.ends_note(o, w.on),
                "soon": card_offers.ending_soon(o, w.on),
                "added": o.added,
                "how": None if o.added else card_offers.WHERE.get(o.issuer, card_offers.WHERE["other"]),
                "visits": count,
                "spent_cents": spent,
            }
        )
    items.sort(key=lambda x: (x["visits"] == 0, not x["soon"], -x["spent_cents"], x["merchant"].lower()))
    cards_with = []
    for aid in dict.fromkeys(o.account_id for o in w.offers if o.account_id in labels):
        mine = [o for o in w.offers if o.account_id == aid]
        seen = max((o.seen for o in mine if o.seen), default=None)
        cards_with.append(
            {
                "account_id": aid,
                "card": labels[aid],
                "issuer": mine[0].issuer,
                "count": len(mine),
                "seen": seen.isoformat() if seen else None,
            }
        )
    return {"items": items, "cards": cards_with}


def caps(card: Card, ledger: Ledger, on: date) -> list[dict]:
    out = []
    for i, r in enumerate(card.product["earn"]):
        if not r.get("cap") or r.get("portal"):
            continue
        cats = categories_of(r, card, on)
        if cats == []:
            continue
        used = ledger.used[ledger.key(card, i, r, on)]
        out.append(
            {"what": r["note"], "used_cents": used, "cap_cents": r["cap"] * 100, "period": r.get("period", "year")}
        )
    return out
