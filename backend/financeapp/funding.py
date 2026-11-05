"""Low cash before a payment you make on a schedule: a weekly buy in a brokerage, a monthly transfer out of savings, a
loan paid from one checking account. Each reminder names the account, the amount, how often (every week, every two
weeks, every month) and the next date, and the app checks that account has enough cash for it with a cushion to
spare. When it doesn't, Home's "Needs you" says how much to add and by when, Coming up shows each one with whether it's
covered, and a push goes out a day or two before (the reminder's own "ahead" days) so there's time for a transfer to
land.

What counts as cash:
- a brokerage (investment account): the cash it holds, from its latest holdings (the cash or sweep position Plaid
  marks as cash). Without a cash position, the cash the brokerage reports as available (Plaid's available balance),
  and with neither, none: holdings without cash mean there's none to spend.
- a bank account: its available balance, or the current one when the bank doesn't say.
A credit card or loan can't hold cash, so it isn't offered.

Several reminders on one account add up: two weekly buys of $105 and $50 need $155 each Thursday, and one that comes
twice before the next can be checked needs both. Each one's shortfall is what the account is short by its date,
counting every payment due before it, plus the cushion.

A payment already made today (a buy or an outflow about the same size on its date) isn't counted again. It says
nothing when it can't tell: no balance or holdings on file for the account, or its connection has stopped syncing
(that's its own "Needs you" item).

Found in your activity: buys of the same total in a brokerage that repeat every week, every two weeks or every month
are offered as reminders to add with one tap (the E*TRADE $105 every Thursday case).

Pushes go through the alerts queue at high priority, once per reminder per date, and are dropped while still waiting
(quiet hours) if the money arrives. Like every push, they name the account and the day, never an amount: the amount
to add is on Home, one tap away. Reminders are saved in the "funding" preference: no table of their own.
"""

import itertools
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import covers, household
from financeapp.models import (
    Account,
    AlertEvent,
    Balance,
    Connection,
    HoldingSnapshot,
    InvestmentTransaction,
    Preference,
    Transaction,
    now,
)

PREF = "funding"
MAX_REMINDERS = 30
EVERY = {"week": 7, "2weeks": 14, "month": None}
SUGGESTED_NAME = {"week": "weekly buy", "2weeks": "buy every two weeks", "month": "monthly buy"}
EVERY_LABEL = {"week": "every week", "2weeks": "every two weeks", "month": "every month"}
AHEAD_DAYS = 2  # the push, this many days before
NEEDS_DAYS = 7  # Home's "Needs you" shows a shortfall this far out
SOON_DAYS = 14  # Home's "Coming up"
SAME_SIZE = 0.05  # a payment within 5% of the amount on its date is this one, made already
CASH_TYPES = ("depository", "investment", "other")

# Found in your activity
LOOK_BACK_DAYS = 120
MIN_REPEATS = 3
FITTING_SHARE = 2 / 3


@dataclass
class Reminder:
    id: int
    name: str
    account_id: int
    amount_cents: int
    every: str  # week | 2weeks | month
    start: date  # one date it happens on; the rest follow from it
    cushion_cents: int = 0
    ahead_days: int = AHEAD_DAYS
    on: bool = True

    def stored(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "account_id": self.account_id,
            "amount_cents": self.amount_cents,
            "every": self.every,
            "start": self.start.isoformat(),
            "cushion_cents": self.cushion_cents,
            "ahead_days": self.ahead_days,
            "on": self.on,
        }

    @staticmethod
    def of(d: dict) -> "Reminder":
        return Reminder(
            id=int(d["id"]),
            name=str(d["name"]),
            account_id=int(d["account_id"]),
            amount_cents=int(d["amount_cents"]),
            every=d["every"] if d.get("every") in EVERY else "week",
            start=date.fromisoformat(d["start"]),
            cushion_cents=int(d.get("cushion_cents") or 0),
            ahead_days=int(d.get("ahead_days", AHEAD_DAYS)),
            on=bool(d.get("on", True)),
        )


# ---- when


def on_or_after(r: Reminder, day: date) -> date:
    """Its first date on or after `day`."""
    step = EVERY[r.every]
    if step is not None:
        if day <= r.start:
            return r.start
        return r.start + timedelta(days=-(-(day - r.start).days // step) * step)
    # Monthly: the start's day of the month (a short month's last day for the 29th to 31st).
    n = 0 if day <= r.start else (day.year - r.start.year) * 12 + day.month - r.start.month
    while True:
        d = covers.on_day(covers.add_months(r.start, n), r.start.day)
        if d >= day:
            return d
        n += 1


def dates(r: Reminder, start: date, end: date) -> list[date]:
    out = []
    d = on_or_after(r, start)
    while d <= end:
        out.append(d)
        d = on_or_after(r, d + timedelta(days=1))
    return out


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def schedule(r: Reminder) -> str:
    """ "Every Thursday", "Every other Monday", "Every month on the 15th"."""
    if r.every == "week":
        return f"Every {r.start:%A}"
    if r.every == "2weeks":
        return f"Every other {r.start:%A}"
    return f"Every month on the {ordinal(r.start.day)}"


# ---- saved reminders


def load(db: DbSession) -> list[Reminder]:
    p = db.get(Preference, PREF)
    out = []
    for d in (p.value if p else {}).get("reminders", []):
        try:
            out.append(Reminder.of(d))
        except (KeyError, TypeError, ValueError):
            continue  # a broken entry shouldn't take the rest down
    return out


def save(db: DbSession, reminders: list[Reminder], dismissed: list[str] | None = None) -> None:
    p = db.get(Preference, PREF)
    if p is None:
        p = Preference(key=PREF, value={})
        db.add(p)
    value = {**p.value, "reminders": [r.stored() for r in reminders]}
    if dismissed is not None:
        value["dismissed"] = dismissed[-50:]
    p.value = value  # a new dict, so SQLAlchemy sees the change


def dismissed(db: DbSession) -> list[str]:
    p = db.get(Preference, PREF)
    return list((p.value if p else {}).get("dismissed", []))


def next_id(reminders: list[Reminder]) -> int:
    return max((r.id for r in reminders), default=0) + 1


# ---- cash on hand


@dataclass
class Cash:
    cents: int | None  # None: not known
    as_of: date | None
    source: str  # holdings | available | balance | none


def cash_of(db: DbSession, a: Account) -> Cash:
    bal = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
    if a.type == "investment":
        last = db.scalar(select(func.max(HoldingSnapshot.date)).where(HoldingSnapshot.account_id == a.id))
        if last is not None:
            held = list(
                db.scalars(
                    select(HoldingSnapshot.value_cents).where(
                        HoldingSnapshot.account_id == a.id,
                        HoldingSnapshot.date == last,
                        HoldingSnapshot.asset_class == "Cash",
                    )
                )
            )
            if held:
                return Cash(sum(held), last, "holdings")
        if bal is not None and bal.available_cents is not None:
            return Cash(bal.available_cents, bal.date, "available")
        if last is not None:
            return Cash(0, last, "holdings")
        return Cash(None, None, "none")
    if bal is None:
        return Cash(None, None, "none")
    if bal.available_cents is not None:
        return Cash(bal.available_cents, bal.date, "available")
    return Cash(bal.current_cents, bal.date, "balance")


def made_on(db: DbSession, a: Account, r: Reminder, day: date) -> bool:
    """A payment about this size already made on `day` (a buy in a brokerage, money out of a bank account)."""
    lo, hi = r.amount_cents * (1 - SAME_SIZE), r.amount_cents * (1 + SAME_SIZE)
    if a.type == "investment":
        spent = -sum(
            db.scalars(
                select(InvestmentTransaction.amount_cents).where(
                    InvestmentTransaction.account_id == a.id,
                    InvestmentTransaction.date == day,
                    InvestmentTransaction.type == "buy",
                    InvestmentTransaction.amount_cents < 0,
                )
            )
        )
        if spent >= lo:
            return True
    outs = db.scalars(
        select(Transaction.amount_cents).where(
            Transaction.account_id == a.id, Transaction.date == day, Transaction.amount_cents < 0
        )
    )
    return any(lo <= -c <= hi for c in outs)


# ---- the check


@dataclass
class Check:
    reminder: Reminder
    account: str
    cash: Cash
    next: date
    state: str  # ok | short | unknown | off
    short_cents: int = 0  # what to add before `next`
    needed_cents: int = 0  # every payment from this account through `next`, and the cushion
    with_others: list[str] = field(default_factory=list)  # other reminders on the account counted with it

    def json(self, today: date) -> dict:
        r = self.reminder
        return {
            **r.stored(),
            "account": self.account,
            "schedule": schedule(r),
            "every_label": EVERY_LABEL[r.every],
            "next": self.next.isoformat(),
            "days_left": (self.next - today).days,
            "state": self.state,
            "cash_cents": self.cash.cents,
            "cash_as_of": self.cash.as_of.isoformat() if self.cash.as_of else None,
            "cash_from": self.cash.source,
            "short_cents": self.short_cents,
            "needed_cents": self.needed_cents,
            "with_others": self.with_others,
            "href": f"/settings/alerts?funding={r.id}",
        }


def accounts_for(db: DbSession) -> dict[int, Account]:
    return {a.id: a for a in household.counted(db) if a.type in CASH_TYPES}


def check(db: DbSession, today: date, reminders: list[Reminder] | None = None) -> list[Check]:
    """Every reminder with its next date and whether the account has the cash for it."""
    reminders = load(db) if reminders is None else reminders
    accounts = accounts_for(db)
    stale = {
        a.id
        for a, c in db.execute(select(Account, Connection).join(Connection, Account.connection_id == Connection.id))
        if c.status == "stale"
    }
    out: list[Check] = []
    by_account: dict[int, list[Reminder]] = {}
    for r in reminders:
        if r.on and r.account_id in accounts:
            by_account.setdefault(r.account_id, []).append(r)
    cash: dict[int, Cash] = {aid: cash_of(db, accounts[aid]) for aid in by_account}

    def upcoming(r: Reminder) -> date:
        d = on_or_after(r, today)
        if d == today and made_on(db, accounts[r.account_id], r, today):
            d = on_or_after(r, today + timedelta(days=1))
        return d

    for r in reminders:
        a = accounts.get(r.account_id)
        name = household.display_name(a) if a else "An account no longer here"
        if a is None or not r.on:
            out.append(Check(r, name, Cash(None, None, "none"), on_or_after(r, today), "off"))
            continue
        nxt = upcoming(r)
        c = cash[a.id]
        if c.cents is None or a.id in stale:
            out.append(Check(r, name, c, nxt, "unknown"))
            continue
        # Everything this account pays from today through this one's date, this one included.
        due, others = 0, []
        for o in by_account[a.id]:
            first = upcoming(o)
            n = len(dates(o, first, nxt)) if first <= nxt else 0
            due += n * o.amount_cents
            if o.id != r.id and n:
                others.append(o.name)
        needed = due + r.cushion_cents
        short = max(needed - c.cents, 0)
        out.append(Check(r, name, c, nxt, "short" if short else "ok", short, needed, others))
    return out


def status(db: DbSession, today: date) -> dict[str, list[dict]]:
    """short: a reminder in the next NEEDS_DAYS the account can't cover (Needs you). all: every reminder."""
    checks = check(db, today)
    rows = [c.json(today) for c in checks]
    short = [r for r in rows if r["state"] == "short" and r["days_left"] <= NEEDS_DAYS]
    # Two reminders on one account short on the same day: the later one's shortfall already counts the first.
    seen: dict[tuple[int, str], dict] = {}
    for r in sorted(short, key=lambda r: (r["next"], -r["short_cents"])):
        seen.setdefault((r["account_id"], r["next"]), r)
    return {"short": sorted(seen.values(), key=lambda r: r["next"]), "all": rows}


# ---- the push


def wording(r: dict, today: date) -> tuple[str, str]:
    """A push's title and body: the account, what it's for and the day, never an amount."""
    d = date.fromisoformat(r["next"])
    days = (d - today).days
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"{d:%A}"
    title = f"Add money to {r['account']}"
    body = f"There isn't enough cash for {r['name']} {when}. Tap to see how much to move in."
    return title[:120], body[:300]


def key(reminder_id: int, on: date | str) -> str:
    on = on if isinstance(on, str) else on.isoformat()
    return f"funding:{reminder_id}:{on}"


def queue(db: DbSession, today: date) -> int:
    """Saves one push per short reminder, `ahead_days` before its date (once per date, however often this runs), and
    drops any still waiting for one that's covered now. Returns how many were added."""
    added = 0
    for c in check(db, today):
        r = c.reminder
        if c.state == "ok":
            db.execute(
                delete(AlertEvent).where(
                    AlertEvent.dedup_key == key(r.id, c.next), AlertEvent.status.in_(["pending", "held"])
                )
            )
            continue
        if c.state != "short" or (c.next - today).days > r.ahead_days:
            continue
        title, body = wording(c.json(today), today)
        stmt = insert(AlertEvent).values(
            rule_id=None, kind="funding", title=title, body=body, dedup_key=key(r.id, c.next),
            fired_at=now(), status="pending", attempts=0, link="/",  # Home's "Needs you": how much, and by when
        )  # fmt: skip
        if db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None:
            added += 1
    return added


# ---- found in your activity


def cadence(gaps: list[int]) -> str | None:
    if not gaps:
        return None
    for every, lo, hi in (("week", 6, 8), ("2weeks", 13, 15), ("month", 27, 33)):
        if lo <= statistics.median(gaps) <= hi and sum(lo <= g <= hi for g in gaps) >= len(gaps) * FITTING_SHARE:
            return every
    return None


def suggestions(db: DbSession, today: date, reminders: list[Reminder] | None = None) -> list[dict]:
    """Brokerage buys of the same total that repeat on a rhythm, not yet a reminder (or dismissed)."""
    reminders = load(db) if reminders is None else reminders
    accounts = {aid: a for aid, a in accounts_for(db).items() if a.type == "investment"}
    if not accounts:
        return []
    totals: dict[tuple[int, date], int] = {}
    for aid, d, cents in db.execute(
        select(InvestmentTransaction.account_id, InvestmentTransaction.date, InvestmentTransaction.amount_cents).where(
            InvestmentTransaction.account_id.in_(accounts),
            InvestmentTransaction.type == "buy",
            InvestmentTransaction.amount_cents < 0,
            InvestmentTransaction.date >= today - timedelta(days=LOOK_BACK_DAYS),
        )
    ):
        totals[(aid, d)] = totals.get((aid, d), 0) - cents
    groups: dict[tuple[int, int], list[date]] = {}
    for (aid, d), cents in totals.items():
        groups.setdefault((aid, round(cents / 100)), []).append(d)
    have = {(r.account_id, round(r.amount_cents / 100)) for r in reminders}
    skip = set(dismissed(db))
    out = []
    for (aid, dollars), days in groups.items():
        days.sort()
        if len(days) < MIN_REPEATS or (aid, dollars) in have or f"{aid}:{dollars}" in skip:
            continue
        every = cadence([(b - a).days for a, b in itertools.pairwise(days)])
        if every is None:
            continue
        step = EVERY[every] or 31
        if (today - days[-1]).days > 2 * step:
            continue  # it stopped
        if every == "month":
            start = days[-1]
        else:
            weekday = statistics.mode(d.weekday() for d in days)
            start = days[-1] + timedelta(days=(weekday - days[-1].weekday()) % 7)
        r = Reminder(0, "", aid, dollars * 100, every, start)
        nxt = on_or_after(r, today)
        a = accounts[aid]
        out.append(
            {
                "key": f"{aid}:{dollars}",
                "account_id": aid,
                "account": household.display_name(a),
                "name": f"{a.institution} {SUGGESTED_NAME[every]}",
                "amount_cents": dollars * 100,
                "every": every,
                "start": nxt.isoformat(),
                "schedule": schedule(Reminder(0, "", aid, 0, every, nxt)),
                "times": len(days),
                "last": days[-1].isoformat(),
            }
        )
    return sorted(out, key=lambda s: -s["times"])
