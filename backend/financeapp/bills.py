"""Utility bills that haven't been paid yet this time. No rule to set up: the app learns each utility's rhythm from
the last year of payments and says so when the usual one hasn't shown up.

Which payments: spending (no transfers) from a utility, the same test Plan uses to tell utilities from
subscriptions (plan.recurring_kind: the Utilities or Phone & internet category, or a name like "Duke Energy" or
"City of Durham"). Payments to one biller are grouped by merchant_key, so "DUKE ENERGY 102394 ONLINE PMT" and
"Duke Energy" are the same biller. Pending payments count: the payment was made.

Its rhythm: payments a few days apart are one bill (a city that charges water and trash separately). At least
MIN_BILLS bills, about a month, two months or three months apart most of the time (CADENCES), make a rhythm. The
next one is expected the usual gap after the last, and it's late GRACE_DAYS after that, more for a biller whose
payment day wanders (up to MAX_GRACE). Its usual amount is the median of the recent bills.

When it's late: on Home's "Needs you" and one push per biller per expected date, through the alerts queue (quiet
hours, grouping, the ntfy/email fallback), at high priority like a card payment due, naming the biller and the
usual day, never an amount. It stops on its own once a payment from that biller comes in, or one the same size to
another utility (a biller whose name changed, or a payment through the bank's bill pay). "Paid" covers a bill paid
from an account the app doesn't see: it counts as paid on the day it was expected (saved in the "bills" preference,
by biller).

It says nothing when it can't tell: the account the last bill came from hasn't synced (or had a file uploaded)
since the payment was due, or the next one would be due already, so it has likely moved, changed or stopped.
"""

import itertools
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, plan, reminders
from financeapp.categorize import merchant_key
from financeapp.models import Account, AlertEvent, Connection, Preference, Transaction, now

PREF = "bills"
LOOKBACK_DAYS = 400  # a year of bills, and the one before
MIN_BILLS = 3
SAME_BILL_DAYS = 6  # payments this close are one bill
RECENT = 6  # how many of the latest gaps and amounts set the rhythm
CADENCES = (  # name, usual days apart, shortest and longest gap that still fits
    ("monthly", 30, 24, 38),
    ("every two months", 61, 52, 70),
    ("quarterly", 91, 80, 102),
)
FITTING_SHARE = 2 / 3  # most gaps have to fit, so one missed or doubled month doesn't hide a biller
GRACE_DAYS = 3
MAX_GRACE = 7
SAME_SIZE = 0.35  # a payment to another utility within 35% of the usual amount pays this bill
SOON_DAYS = 14  # Home's Coming up


@dataclass
class Bill:
    key: str
    name: str
    category_id: int | None
    cadence: str
    period: int
    dates: list[date]  # each bill's first payment day, oldest first
    usual_cents: int
    last_cents: int
    account_id: int  # where the last one came from
    expected: date
    grace: int

    @property
    def late_on(self) -> date:
        return self.expected + timedelta(days=self.grace)

    def json(self, today: date) -> dict:
        late = (today - self.late_on).days
        return {
            "key": self.key,
            "name": self.name,
            "cadence": self.cadence,
            "usual_cents": self.usual_cents,
            "last_cents": self.last_cents,
            "last_paid": self.dates[-1].isoformat(),
            "usual_day": usual_day(self),
            "expected": self.expected.isoformat(),
            "late_on": self.late_on.isoformat(),
            "days_late": max(late, 0),
            "account_id": self.account_id,
            "href": spending_link(self),
        }


def usual_day(b: Bill) -> int | None:
    return steady_day(b.dates) if b.cadence == "monthly" else None


def steady_day(dates: list[date]) -> int | None:
    """The day of the month a monthly bill usually comes, when it keeps to one (within a few days)."""
    days = [d.day for d in dates[-RECENT:]]
    mid = round(statistics.median(days))
    return mid if all(abs(d - mid) <= GRACE_DAYS for d in days) else None


def next_on_day(last: date, day: int) -> date:
    """That day in the month after the last bill's (a short month's last day for the 29th to 31st)."""
    y, m = reminders.add_months(last.year, last.month, 1)
    return reminders.statement_on(y, m, day)


def spending_link(b: Bill) -> str:
    from urllib.parse import quote

    return f"/spending?q={quote(b.key)}"


def display_name(merchant: str, key: str) -> str:
    # Bank wording carries reference numbers ("DUKE ENERGY 102394 PMT"); the key is the part that stays the same.
    if any(ch.isdigit() for ch in merchant) or merchant.isupper():
        return key.title()
    return merchant


def cadence_of(gaps: list[int]) -> tuple[str, int] | None:
    if not gaps:
        return None
    mid = statistics.median(gaps)
    for name, days, lo, hi in CADENCES:
        if lo <= mid <= hi and sum(lo <= g <= hi for g in gaps) >= len(gaps) * FITTING_SHARE:
            return name, days
    return None


def group(payments: list[Transaction]) -> list[tuple[date, int, int]]:
    """(first day, cents, account) per bill: payments within SAME_BILL_DAYS of a bill's first one join it."""
    bills: list[list] = []
    for t in sorted(payments, key=lambda t: t.date):
        if bills and (t.date - bills[-1][0]).days <= SAME_BILL_DAYS:
            bills[-1][1] -= t.amount_cents
            bills[-1][2] = t.account_id
        else:
            bills.append([t.date, -t.amount_cents, t.account_id])
    return [(d, c, a) for d, c, a in bills]


def learn(payments: list[Transaction], name: str, key: str, category_id: int | None, paid: list[date]) -> Bill | None:
    """A biller's rhythm from its payments, plus the bills marked paid by hand (they count as paid on the day)."""
    bills = group(payments)
    if len(bills) < MIN_BILLS:
        return None
    real = {d for d, _, _ in bills}
    # A bill marked paid by hand that then shows up after all is the same bill.
    dates = sorted(real | {p for p in paid if all(abs((p - d).days) > SAME_BILL_DAYS for d in real)})
    gaps = [(b - a).days for a, b in itertools.pairwise(dates)][-RECENT:]
    found = cadence_of(gaps)
    if found is None:
        return None
    cadence, period = found
    mid = round(statistics.median(gaps))
    spread = round(statistics.median([abs(g - mid) for g in gaps]))
    amounts = [c for _, c, _ in bills][-RECENT:]
    day = steady_day(dates) if cadence == "monthly" else None
    expected = next_on_day(dates[-1], day) if day else dates[-1] + timedelta(days=mid)
    return Bill(
        key=key,
        name=name,
        category_id=category_id,
        cadence=cadence,
        period=period,
        dates=dates,
        usual_cents=round(statistics.median(amounts)),
        last_cents=bills[-1][1],
        account_id=bills[-1][2],
        expected=expected,
        grace=min(MAX_GRACE, GRACE_DAYS + spread),
    )


def prefs(db: DbSession) -> dict:
    p = db.get(Preference, PREF)
    return dict(p.value) if p else {}


def save_pref(db: DbSession, key: str, **changes) -> None:
    p = db.get(Preference, PREF)
    if p is None:
        p = Preference(key=PREF, value={})
        db.add(p)
    value = dict(p.value)
    value[key] = {**value.get(key, {}), **changes}
    p.value = value  # a new dict, so SQLAlchemy sees the change


def watch(db: DbSession, today: date) -> tuple[list[Bill], list[Transaction]]:
    """Every utility with a rhythm, and every utility payment (to check renamed billers against)."""
    accounts = budget.counted_accounts(db)
    ledger = budget.Ledger(db, accounts, today - timedelta(days=LOOKBACK_DAYS))
    saved = prefs(db)
    by_key: dict[str, list[Transaction]] = {}
    for t in ledger.txns:
        if t.amount_cents >= 0 or t.date > today or not ledger.is_spending(t):
            continue
        category = ledger.categories.get(t.category_id) if t.category_id else None
        if plan.recurring_kind(t.merchant, category) != "utility":
            continue
        key = merchant_key(t.merchant)
        if key:
            by_key.setdefault(key, []).append(t)
    out = []
    for key, payments in by_key.items():
        p = saved.get(key, {})
        latest = max(payments, key=lambda t: t.date)
        paid = [date.fromisoformat(d) for d in p.get("paid", [])]
        b = learn(payments, display_name(latest.merchant, key), key, latest.category_id, paid)
        if b:
            out.append(b)
    return out, [t for ts in by_key.values() for t in ts]


def current_through(db: DbSession, account_ids: set[int]) -> dict[int, date]:
    """How far each account's data is known to go: the last sync for a linked one, the newest data for one filled by
    hand or from files. A stale connection isn't current at all."""
    out: dict[int, date] = {}
    by_hand = []
    for a, c in db.execute(
        select(Account, Connection)
        .join(Connection, Account.connection_id == Connection.id)
        .where(Account.id.in_(account_ids))
    ):
        if c.provider in reminders.PROVIDERS:
            by_hand.append(a.id)
        elif c.status != "stale" and c.last_synced_at:
            out[a.id] = c.last_synced_at.date()
    out.update(reminders.newest_data(db, by_hand))
    return out


def paid_elsewhere(b: Bill, others: list[Transaction], today: date) -> bool:
    """A payment the right size to a utility not tracked on its own (a renamed biller, the bank's bill pay)."""
    since = b.expected - timedelta(days=b.period // 2)
    return any(
        since <= t.date <= today and abs(-t.amount_cents - b.usual_cents) <= b.usual_cents * SAME_SIZE for t in others
    )


def status(db: DbSession, today: date) -> dict[str, list[dict]]:
    """late: past due with no payment. soon: expected in the next SOON_DAYS, or due and still in its grace days."""
    bills, payments = watch(db, today)
    tracked = {b.key for b in bills}
    others = [t for t in payments if merchant_key(t.merchant) not in tracked]
    known = current_through(db, {b.account_id for b in bills})
    late, soon = [], []
    for b in bills:
        if today > b.late_on:
            if (today - b.expected).days >= b.period or known.get(b.account_id, date.min) < b.late_on:
                continue  # it moved or stopped, or the account it's paid from hasn't caught up
            if paid_elsewhere(b, others, today):
                continue
            late.append(b.json(today))
        elif b.expected <= today + timedelta(days=SOON_DAYS):
            soon.append(b.json(today))
    return {
        "late": sorted(late, key=lambda r: -r["days_late"]),
        "soon": sorted(soon, key=lambda r: r["expected"]),
    }


def when(r: dict) -> str:
    d = date.fromisoformat(r["expected"])
    if r["usual_day"]:
        return f"the {ordinal(r['usual_day'])}"
    return f"{d:%B} {d.day}"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def wording(r: dict) -> tuple[str, str]:
    """A push's title and body: the biller and when it's usually paid, never an amount."""
    title = f"Check your utilities: {r['name']}"
    body = (
        f"{r['name']} is usually paid around {when(r)} and no payment has shown up yet. "
        "Check the bill and pay it if it's still due."
    )
    return title[:120], body[:300]


def queue(db: DbSession, today: date) -> int:
    """Saves one push per late bill. The dedup key names the biller and the expected date, so each period's bill is
    pushed once however often this runs. Returns how many were added."""
    added = 0
    for r in status(db, today)["late"]:
        title, body = wording(r)
        stmt = insert(AlertEvent).values(
            rule_id=None,
            kind="bill",
            title=title,
            body=body,
            dedup_key=f"bill:{r['key'][:120]}:{r['expected']}",
            fired_at=now(),
            status="pending",
            attempts=0,
            link="/",  # Home's "Needs you", with Paid and the payments so far
        )
        if db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None:
            added += 1
    return added


def mark_paid(db: DbSession, key: str, today: date) -> bool:
    """This period's bill was paid somewhere the app doesn't see: counted as paid on its expected day."""
    bills, _ = watch(db, today)
    b = next((b for b in bills if b.key == key), None)
    if b is None:
        return False
    paid = prefs(db).get(key, {}).get("paid", [])
    save_pref(db, key, paid=sorted({*paid, min(b.expected, today).isoformat()})[-12:])
    return True
