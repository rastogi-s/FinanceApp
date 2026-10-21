"""The budget, the true savings rate, "safe to spend" and the emergency fund.

Draft. Each bill and everyday category gets the median of what it cost in the last 3 to 6 full months (months
with nothing count as $0, so a one-off doesn't become a monthly limit). Bills get that median rounded up to $5;
everyday categories get 10% on top, rounded up to $10. Until someone saves a budget, the draft is shown as it is;
every save after that is a new version, so an older budget can be brought back.

Savings rate. savings = gross pay - taxes - other paycheck deductions - spending, as a share of gross pay. Money
put into a 401(k), HSA or ESPP before pay arrives is saving, and so is whatever is left of take-home pay. Pay stubs
are typed in; a paycheck deposit is matched to its stub (same amount within $1, within 5 days) so it isn't counted
twice. Deposits with no stub count as they are (take-home), so the rate still works for someone who never enters a
stub, just on take-home rather than gross pay. Employer match is left out unless the household turns it on.

Safe to spend. What's left in the everyday budget this month, after anything spent outside the budget and any
bill that came in over its limit.
"""

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp.models import (
    Account,
    Balance,
    BudgetLine,
    BudgetVersion,
    Category,
    Paycheck,
    Preference,
    Transaction,
    User,
)
from financeapp.sync import today

MAX_MONTHS = 6
FULL_DRAFT_MONTHS = 3  # fewer full months than this and the draft says it's rough
FLEXIBLE_BUFFER = 1.10
MATCH_CENTS = 100
MATCH_DAYS = 5
DEFAULTS = {"emergency_months": 6, "include_match": False}
BUDGETED = ("fixed", "flexible")


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    m = d.year * 12 + d.month - 1 + n
    return date(m // 12, m % 12 + 1, 1)


def preferences(db: DbSession) -> dict:
    p = db.get(Preference, "household")
    return {**DEFAULTS, **(p.value if p else {})}


def set_preferences(db: DbSession, **changes) -> dict:
    p = db.get(Preference, "household")
    if p is None:
        p = Preference(key="household", value={})
        db.add(p)
    p.value = {**p.value, **changes}
    return preferences(db)


def counted_accounts(db: DbSession) -> list[Account]:
    return list(db.scalars(select(Account).where(Account.hidden.is_(False), Account.is_authoritative.is_(True))))


def full_months(db: DbSession, account_ids: list[int], end: date) -> list[date]:
    """The last (up to 6) whole calendar months the transactions cover, oldest first."""
    if not account_ids:
        return []
    first = db.scalar(select(func.min(Transaction.date)).where(Transaction.account_id.in_(account_ids)))
    if first is None:
        return []
    first_full = month_start(first) if first.day == 1 else add_months(first, 1)
    last_full = add_months(month_start(end), -1)
    start = max(first_full, add_months(last_full, -(MAX_MONTHS - 1)))
    months = []
    while start <= last_full:
        months.append(start)
        start = add_months(start, 1)
    return months


def round_up(cents: float, step_dollars: int) -> int:
    step = step_dollars * 100
    return math.ceil(round(cents) / step) * step  # round first: 500 * 1.1 is 550.0000000000001


class Ledger:
    """Every counted transaction from the first month we look at, sorted into per-category, per-month totals."""

    def __init__(self, db: DbSession, accounts: list[Account], since: date) -> None:
        self.categories = {c.id: c for c in db.scalars(select(Category))}
        self.txns = (
            db.scalars(
                select(Transaction).where(
                    Transaction.account_id.in_([a.id for a in accounts]),
                    Transaction.date >= since,
                    Transaction.transfer_group_id.is_(None),
                )
            ).all()
            if accounts
            else []
        )
        self.spent: dict[tuple[int | None, date], int] = defaultdict(int)  # (category, month) -> cents out
        for t in self.txns:
            if self.is_spending(t):
                self.spent[(t.category_id, month_start(t.date))] -= t.amount_cents

    def kind(self, t: Transaction) -> str:
        c = self.categories.get(t.category_id) if t.category_id else None
        return c.kind if c else "other"

    def is_taxes(self, t: Transaction) -> bool:
        c = self.categories.get(t.category_id) if t.category_id else None
        return c is not None and c.name == "Taxes"

    def is_spending(self, t: Transaction) -> bool:
        return self.kind(t) not in ("transfer", "income") and not self.is_taxes(t)

    def is_income(self, t: Transaction) -> bool:
        return self.kind(t) == "income" and t.amount_cents > 0

    def month(self, m: date) -> list[Transaction]:
        nxt = add_months(m, 1)
        return [t for t in self.txns if m <= t.date < nxt]


def draft_lines(ledger: Ledger, months: list[date]) -> dict[int, dict]:
    """category id -> {limit_cents, typical_cents}, for every bill and everyday category with spending."""
    lines = {}
    for c in ledger.categories.values():
        if c.kind not in BUDGETED:
            continue
        typical = statistics.median([max(ledger.spent.get((c.id, m), 0), 0) for m in months]) if months else 0
        if typical < 100:
            continue
        limit = round_up(typical, 5) if c.kind == "fixed" else round_up(typical * FLEXIBLE_BUFFER, 10)
        lines[c.id] = {"limit_cents": limit, "typical_cents": int(typical)}
    return lines


def latest_version(db: DbSession) -> BudgetVersion | None:
    return db.scalar(select(BudgetVersion).order_by(BudgetVersion.id.desc()).limit(1))


def save_version(db: DbSession, lines: dict[int, int], source: str, user_id: int | None) -> BudgetVersion:
    v = BudgetVersion(source=source, created_by=user_id)
    db.add(v)
    db.flush()
    for category_id, limit in lines.items():
        if limit > 0:
            db.add(BudgetLine(version_id=v.id, category_id=category_id, limit_cents=limit))
    db.flush()
    return v


def save_draft(db: DbSession, user_id: int | None) -> BudgetVersion | None:
    accounts = counted_accounts(db)
    months = full_months(db, [a.id for a in accounts], today())
    if not months:
        return None
    ledger = Ledger(db, accounts, months[0])
    lines = {cid: line["limit_cents"] for cid, line in draft_lines(ledger, months).items()}
    return save_version(db, lines, "draft", user_id)


# ---- pay and the savings rate


def match_deposits(stubs: list[Paycheck], deposits: list[Transaction]) -> dict[int, Transaction]:
    """paycheck id -> its deposit: same amount within $1, within 5 days, the closest date first."""
    taken: set[int] = set()
    matched = {}
    for p in sorted(stubs, key=lambda p: p.date):
        best = None
        for t in deposits:
            if t.id in taken or abs(t.amount_cents - p.net_cents) > MATCH_CENTS:
                continue
            gap = abs((t.date - p.date).days)
            if gap <= MATCH_DAYS and (best is None or gap < abs((best.date - p.date).days)):
                best = t
        if best is not None:
            taken.add(best.id)
            matched[p.id] = best
    return matched


def savings_for(ledger: Ledger, stubs: list[Paycheck], m: date, include_match: bool) -> dict | None:
    txns = ledger.month(m)
    nxt = add_months(m, 1)
    month_stubs = [p for p in stubs if m <= p.date < nxt]
    # Deposits a few days either side of the month can belong to a stub in it.
    window = [
        t
        for t in ledger.txns
        if ledger.is_income(t) and m - timedelta(days=MATCH_DAYS) <= t.date < nxt + timedelta(days=MATCH_DAYS)
    ]
    matched_ids = {t.id for t in match_deposits(month_stubs, window).values()}
    other_income = sum(t.amount_cents for t in txns if ledger.is_income(t) and t.id not in matched_ids)
    gross = sum(p.gross_cents for p in month_stubs)
    match = sum(p.employer_match_cents for p in month_stubs) if include_match else 0
    income = gross + other_income + match
    if income <= 0:
        return None
    taxes = sum(p.taxes_cents for p in month_stubs) - sum(t.amount_cents for t in txns if ledger.is_taxes(t))
    deductions = sum(p.gross_cents - p.taxes_cents - p.savings_cents - p.net_cents for p in month_stubs)
    spending = -sum(t.amount_cents for t in txns if ledger.is_spending(t))
    saved = income - taxes - deductions - spending
    return {
        "month": m.isoformat(),
        "percent": round(saved / income * 100, 1),
        "basis": "gross" if month_stubs else "take_home",
        "income_cents": income,
        "taxes_cents": taxes,
        "deductions_cents": deductions,
        "spending_cents": spending,
        "saved_cents": saved,
        "match_cents": match,
        "stubs": len(month_stubs),
    }


# ---- everything the Budget page shows


def build(db: DbSession) -> dict:
    accounts = counted_accounts(db)
    end = today()
    this_month = month_start(end)
    months = full_months(db, [a.id for a in accounts], end)
    since = min([*months, this_month]) - timedelta(days=MATCH_DAYS)
    ledger = Ledger(db, accounts, since)
    prefs = preferences(db)
    drafted = draft_lines(ledger, months)

    version = latest_version(db)
    if version is not None:
        limits = {
            line.category_id: line.limit_cents
            for line in db.scalars(select(BudgetLine).where(BudgetLine.version_id == version.id))
        }
    else:
        limits = {cid: line["limit_cents"] for cid, line in drafted.items()}

    lines = []
    unbudgeted = []
    for c in sorted(ledger.categories.values(), key=lambda c: (c.sort, c.name)):
        spent = ledger.spent.get((c.id, this_month), 0)
        if c.id in limits and c.kind in BUDGETED:
            lines.append(
                {
                    "category_id": c.id,
                    "category": c.name,
                    "kind": c.kind,
                    "essential": c.essential,
                    "limit_cents": limits[c.id],
                    "spent_cents": spent,
                    "typical_cents": drafted.get(c.id, {}).get("typical_cents", 0),
                }
            )
        elif spent > 0 and c.kind not in ("transfer", "income") and c.name != "Taxes":
            unbudgeted.append({"category_id": c.id, "category": c.name, "kind": c.kind, "spent_cents": spent})
    no_category = ledger.spent.get((None, this_month), 0)
    if no_category > 0:
        unbudgeted.append(
            {"category_id": None, "category": "Uncategorized", "kind": "other", "spent_cents": no_category}
        )

    next_month = add_months(this_month, 1)
    flexible_left = sum(line["limit_cents"] - line["spent_cents"] for line in lines if line["kind"] == "flexible")
    bills_over = sum(max(line["spent_cents"] - line["limit_cents"], 0) for line in lines if line["kind"] == "fixed")
    outside = sum(u["spent_cents"] for u in unbudgeted)
    safe = None
    if any(line["kind"] == "flexible" for line in lines):
        safe = {"cents": max(flexible_left - bills_over - outside, 0), "days_left": (next_month - end).days}

    stubs = list(db.scalars(select(Paycheck).where(Paycheck.date >= since - timedelta(days=MATCH_DAYS))))
    history = [s for m in months if (s := savings_for(ledger, stubs, m, prefs["include_match"]))]

    cash = 0
    for a in accounts:
        if a.type == "depository":
            b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
            cash += b.current_cents if b else 0
    essentials = sum(line["limit_cents"] for line in lines if line["essential"])

    people = {u.id: u.name for u in db.scalars(select(User))}
    versions = db.scalars(select(BudgetVersion).order_by(BudgetVersion.id.desc()).limit(10)).all()
    totals = dict(
        db.execute(
            select(BudgetLine.version_id, func.sum(BudgetLine.limit_cents))
            .where(BudgetLine.version_id.in_([v.id for v in versions]))
            .group_by(BudgetLine.version_id)
        ).all()
    )
    recent = db.scalars(select(Paycheck).order_by(Paycheck.date.desc(), Paycheck.id.desc()).limit(12)).all()
    deposits = [t for t in ledger.txns if ledger.is_income(t)]
    matched = match_deposits(recent, deposits)

    return {
        "saved": version is not None,
        "version": version_json(version, people, totals) if version else None,
        "months_used": len(months),
        "rough": len(months) < FULL_DRAFT_MONTHS,
        "month": this_month.isoformat(),
        "lines": lines,
        "unbudgeted": unbudgeted,
        "safe_to_spend": safe,
        "savings_rate": history[-1] if history else None,
        "savings_history": history,
        "emergency": {
            "months": prefs["emergency_months"],
            "monthly_cents": essentials,
            "target_cents": essentials * prefs["emergency_months"],
            "have_cents": cash,
        },
        "include_match": prefs["include_match"],
        "versions": [version_json(v, people, totals) for v in versions],
        "paychecks": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "person": people.get(p.user_id) if p.user_id else None,
                "date": p.date.isoformat(),
                "gross_cents": p.gross_cents,
                "taxes_cents": p.taxes_cents,
                "savings_cents": p.savings_cents,
                "net_cents": p.net_cents,
                "employer_match_cents": p.employer_match_cents,
                "deposit": {"date": matched[p.id].date.isoformat(), "merchant": matched[p.id].merchant}
                if p.id in matched
                else None,
            }
            for p in recent
        ],
        "people": [{"id": i, "name": n} for i, n in people.items()],
        "categories": [
            {"id": c.id, "name": c.name, "kind": c.kind, "essential": c.essential}
            for c in sorted(ledger.categories.values(), key=lambda c: (c.sort, c.name))
            if c.kind in BUDGETED
        ],
    }


def version_json(v: BudgetVersion, people: dict[int, str], totals: dict[int, int]) -> dict:
    return {
        "id": v.id,
        "source": v.source,
        "by": people.get(v.created_by) if v.created_by else None,
        "at": v.created_at.isoformat(),
        "total_cents": int(totals.get(v.id) or 0),
    }


def for_household(state: dict) -> dict:
    """The parts of the budget that Home and the tab pages show."""
    budgets = [
        {
            "category": line["category"],
            "category_id": line["category_id"],
            "kind": line["kind"],
            "limit_cents": line["limit_cents"],
            "spent_cents": line["spent_cents"],
        }
        for line in state["lines"]
    ]
    closest = sorted(
        (b for b in budgets if b["kind"] == "flexible" and b["limit_cents"] > 0),
        key=lambda b: b["spent_cents"] / b["limit_cents"],
        reverse=True,
    )[:3]
    rate = state["savings_rate"]
    return {
        "safe_to_spend": state["safe_to_spend"],
        "savings_rate": {"percent": rate["percent"], "month": rate["month"], "basis": rate["basis"]} if rate else None,
        "budgets": budgets,
        "budgets_closest": closest,
    }
