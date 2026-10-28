"""What's coming up in the next two weeks, for Home: paydays, card payments due, CDs maturing, expected dividends and
card offers about to end. Each comes from the page that already works it out:
- paydays: each person's latest pay stub that says how often they're paid (budget.next_payday), for what it paid.
- card payments: each card's latest statement with a due date (CardStatement, from Plaid's liabilities), and whether
  it's been paid since (cardpay): paid, part paid with what's left, or not yet.
- CDs: deposits.maturing, the same list Home's "Needs you" shows (out to 30 days, since a CD needs deciding early).
- dividends: Dividends & interest's expected payments (income.build), at each holding's usual amount.
- offers: card offers ending within a week (offers.load).
Amounts are what's expected, not promised; the rows say so where it matters.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, cardpay, cards, deposits, household, income, offers
from financeapp.models import Account, CardLink, CardStatement, Paycheck, User

DAYS = 14
EVERY = {
    "weekly": "Every week",
    "biweekly": "Every two weeks",
    "semimonthly": "Twice a month",
    "monthly": "Every month",
}
OFFER_DAYS = 7


def event(on: date, kind: str, title: str, detail: str, cents: int | None, href: str | None) -> dict:
    """One row. `cents` is signed: money coming in is positive, a payment going out negative."""
    return {"date": on.isoformat(), "kind": kind, "title": title, "detail": detail, "cents": cents, "href": href}


def paydays(stubs: list[Paycheck], people: dict[int, str], today: date) -> list[dict]:
    """The next payday for each person, from their newest stub with a frequency."""
    out = []
    seen: set[int | None] = set()
    named = len({p.user_id for p in stubs if p.frequency}) > 1
    for p in sorted(stubs, key=lambda p: p.date, reverse=True):
        if p.user_id in seen or not p.frequency:
            continue
        seen.add(p.user_id)
        nxt = budget.next_payday(p.date, p.frequency)
        while nxt < today:
            nxt = budget.next_payday(nxt, p.frequency)
        who = people.get(p.user_id) if p.user_id else None
        title = f"{who}'s paycheck" if named and who else "Paycheck"
        out.append(
            event(
                nxt,
                "pay",
                title,
                EVERY.get(p.frequency, "Regular pay"),
                p.net_cents,
                "/budget#pay",
            )
        )
    return out


def card_dues(
    statements: list[tuple[int, str, CardStatement]], payments: dict[int, cardpay.Payment], today: date, end: date
) -> list[dict]:
    """Each card statement due in the window, or gone by unpaid in the last few days, with whether it's been paid
    (cardpay). The amount is what's left to pay; a paid one has none. `payment` carries the numbers so the page can
    show them (and hide them with the rest)."""
    out = []
    for account_id, name, s in statements:
        p = payments.get(account_id)
        if p is None or not s.due_date:
            continue
        late = p.state == "late" and (today - s.due_date).days <= cardpay.LATE_DAYS
        if not (today <= s.due_date <= end or late):
            continue
        if p.state == "paid":
            title, detail, cents = f"{name} paid", "Statement paid in full", None
        elif p.state == "partial":
            title, detail, cents = f"{name} due", "Part paid, the rest is left", -p.left_cents
        elif p.state == "late":
            title, detail, cents = f"{name} past due", "Not paid in full yet", -p.left_cents
        else:
            title, detail, cents = f"{name} due", "Statement balance, not paid yet", -p.left_cents
        row = event(s.due_date, "card", title, detail, cents, f"/cards?card={account_id}")
        row["payment"] = {**p.json(), "minimum_cents": s.minimum_cents}
        out.append(row)
    return out


def cds(maturing: list[dict]) -> list[dict]:
    return [
        event(
            date.fromisoformat(m["maturity"]),
            "cd",
            f"{m['name']} matures",
            "Renews on its own unless you act" if m["auto_renew"] else "Decide where it goes",
            m["value_cents"],
            f"/settings/accounts?account={m['account_id']}",
        )
        for m in maturing
        if m["status"] == "open"
    ]


def dividends(expected: list[dict], today: date, end: date) -> list[dict]:
    out = []
    for u in expected:
        on = date.fromisoformat(u["pay_date"])
        if today <= on <= end:
            detail = "Expected, reinvested" if u["reinvesting"] else "Expected"
            out.append(event(on, "dividend", f"{u['symbol']} dividend", detail, u["cents"], "/portfolio/income"))
    return out


def ending_offers(rows: list[offers.Offer], names: dict[int, str], today: date) -> list[dict]:
    out = []
    for o in rows:
        if o.expires is None or not today <= o.expires <= today + timedelta(days=OFFER_DAYS):
            continue
        card = names.get(o.account_id)
        detail = f"{o.label} with {card}" if card else o.label
        if o.added is False:
            detail += ", not added to the card yet"
        out.append(event(o.expires, "offer", f"{o.merchant} offer ends", detail, None, "/cards"))
    return out


def merge(*groups: list[dict]) -> list[dict]:
    rows = [r for g in groups for r in g]
    order = {"pay": 0, "card": 1, "cd": 2, "dividend": 3, "offer": 4}
    return sorted(rows, key=lambda r: (r["date"], order[r["kind"]], r["title"]))


def build(db: DbSession, today: date) -> dict:
    end = today + timedelta(days=DAYS)
    accounts = household.counted(db)
    if not accounts:
        return {"as_of": today.isoformat(), "days": DAYS, "events": []}
    names = {a.id: household.display_name(a) for a in accounts}

    people = {u.id: u.name for u in db.scalars(select(User))}
    stubs = list(db.scalars(select(Paycheck).where(Paycheck.date >= today - timedelta(days=120))))

    card_ids = [a.id for a in accounts if a.type == "credit"]
    links = {link.account_id: link for link in db.scalars(select(CardLink).where(CardLink.account_id.in_(card_ids)))}
    card_names = {a.id: cards.label_of(a, links.get(a.id)) for a in accounts if a.id in card_ids}
    statements = [
        (s.account_id, card_names[s.account_id], s)
        for s in db.scalars(select(CardStatement).where(CardStatement.account_id.in_(card_ids)))
    ]
    payments = cardpay.load(db, today, card_ids)

    cd_accounts: list[Account] = [a for a in accounts if deposits.kind_of(a) == "cd"]
    maturing = deposits.maturing(deposits.build(db, cd_accounts, today), names) if cd_accounts else []

    expected = income.build(db, today)["dividends"]["upcoming"]

    return {
        "as_of": today.isoformat(),
        "days": DAYS,
        "events": merge(
            paydays(stubs, people, today),
            card_dues(statements, payments, today, end),
            cds(maturing),
            dividends(expected, today, end),
            ending_offers(offers.load(db, today), card_names, today),
        ),
    }
