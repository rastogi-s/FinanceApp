"""The budget, the true savings rate, "safe to spend" and the emergency fund.

Draft. Each bill and everyday category gets the median of what it cost in the last 3 to 6 full months (months
with nothing count as $0, so a one-off doesn't become a monthly limit). Bills get that median rounded up to $5;
everyday categories get 10% on top, rounded up to $10. Until someone saves a budget, the draft is shown as it is;
every save after that is a new version, so an older budget can be brought back.

Savings rate. savings = gross pay - taxes - other paycheck deductions - spending, as a share of gross pay. Money
put into a 401(k), HSA or ESPP before pay arrives is saving, and so is whatever is left of take-home pay. Pay stubs
are typed in; a paycheck deposit is matched to its stub (same amount within $1, within 5 days) so it isn't counted
twice. Deposits with no stub count as they are (take-home), so the rate still works for someone who never enters a
stub, just on take-home rather than gross pay. A deposit from whoever pays the household counts as pay even when it
landed uncategorized. Money moved between the household's own accounts and card payments are neither money in nor
spending. Employer match is left out unless the household turns it on. Each month also says where the savings
went (a savings account, a brokerage, a card that isn't linked...) and what's worth a second look.

Safe to spend. What's left in the everyday budget this month, after anything spent outside the budget and any
bill that came in over its limit.
"""

import math
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import forecast, goals, transfers
from financeapp.categorize import normalize_merchant
from financeapp.models import (
    Account,
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
# payroll: employers confirmed as pay ("netapp"); not_pay: payers someone said aren't pay; own_senders: Zelle and
# Venmo names that are the household's own (transfers.own_names).
DEFAULTS = {"emergency_months": 6, "include_match": False, "payroll": [], "not_pay": [], "own_senders": []}
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


ACTIVE_TXNS = 20  # a bank account or card with fewer transactions than this is not in regular use


def counted_accounts(db: DbSession) -> list[Account]:
    return list(db.scalars(select(Account).where(Account.hidden.is_(False), Account.is_authoritative.is_(True))))


def full_months(db: DbSession, account_ids: list[int], end: date) -> list[date]:
    """The last (up to 6) whole calendar months the transactions cover, oldest first.

    Banks hand over different amounts of history (one gives two years, another 90 days), so a month only counts
    once every account in regular use has its history going back that far. Otherwise the early months would
    have the card's spending but not the paychecks landing in checking, and look like months of no pay.
    """
    if not account_ids:
        return []
    starts = db.execute(
        select(func.min(Transaction.date), func.count(), Account.type)
        .join(Account, Account.id == Transaction.account_id)
        .where(Transaction.account_id.in_(account_ids))
        .group_by(Transaction.account_id, Account.type)
    ).all()
    if not starts:
        return []
    last_full = add_months(month_start(end), -1)

    def first_full(first: date) -> date:
        return month_start(first) if first.day == 1 else add_months(first, 1)

    # Pay lands in bank accounts and spending is on them and on cards; a brokerage's short history doesn't matter.
    active = [first for first, n, kind in starts if n >= ACTIVE_TXNS and kind in ("depository", "credit")]
    first = max(active) if active else min(first for first, *_ in starts)
    if active and first_full(first) > last_full:  # a card opened this month: better uneven months than none
        first = min(first for first, *_ in starts)
    first_full_month = first_full(first)
    start = max(first_full_month, add_months(last_full, -(MAX_MONTHS - 1)))
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

    def is_card_payment(self, t: Transaction) -> bool:
        c = self.categories.get(t.category_id) if t.category_id else None
        return c is not None and c.name == transfers.CARD_CATEGORY

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


# Words a bank puts around an employer's name on a pay deposit. Wording differs from bank to bank ("Deposit from
# NETAPP INC PAYROLL", "NETAPP INC DIR DEP PPD"), so an employer is known by what's left once these are gone.
PAYROLL_WORDS = re.compile(r"payroll|direct dep|dir dep|\bdirdep\b|salary|\bwages?\b", re.I)
NOT_PAY_WORDS = re.compile(r"reimb|expense|refund|bonus adj|reversal|return", re.I)
GENERIC = frozenset({
    "deposit", "deposits", "from", "to", "payroll", "direct", "dep", "dir", "dirdep", "ach", "ppd", "ccd", "credit",
    "crd", "inc", "corp", "corporation", "llc", "ltd", "co", "company", "the", "id", "des", "indn", "web", "pay",
    "salary", "wage", "wages", "transfer", "online", "mobile", "electronic", "edi", "payment", "pmt", "dd", "of",
    "and", "for", "us", "usa", "net", "n", "a",
})  # fmt: skip


def payer(t: Transaction) -> str:
    return normalize_merchant(t.merchant or t.description)


def text_of(t: Transaction) -> str:
    return normalize_merchant(f"{t.merchant} {t.description}")


def employer_key(text: str) -> str:
    """The employer in a deposit's words: "Deposit from NETAPP INC PAYROLL" -> "netapp"."""
    words = [w for w in normalize_merchant(text).split() if w not in GENERIC and not any(c.isdigit() for c in w)]
    return " ".join(list(dict.fromkeys(words))[:3])  # merchant and description often repeat the name


class Payroll:
    """Which deposits are pay: a deposit from a known employer (learned from the deposits a pay stub's take-home
    matches, from deposits that say payroll or direct deposit, and from names someone typed), worded however the
    bank words it, unless someone said that payer isn't pay. Nothing else counts as pay, whatever its category."""

    def __init__(
        self, employers: set[str] = frozenset(), not_pay: set[str] = frozenset(), stubs=(), paycheck_ids=frozenset()
    ) -> None:
        self.employers = {e for e in employers if e}
        self.paycheck_ids = set(paycheck_ids)  # the Paycheck category: a deposit filed there by hand is pay
        self.not_pay = set(not_pay)
        self.stubs = list(stubs)  # (pay date, take-home cents)

    def employer_of(self, t: Transaction) -> str | None:
        words = set(text_of(t).split())
        for e in sorted(self.employers, key=len, reverse=True):
            if set(e.split()) <= words:
                return e
        return None

    def matches(self, t: Transaction) -> bool:
        if t.amount_cents <= 0:
            return False
        text = text_of(t)
        if employer_key(text) in self.not_pay or payer(t) in self.not_pay:
            return False
        if self.matches_stub(t) or (t.category_source == "user" and t.category_id in self.paycheck_ids):
            return True
        if NOT_PAY_WORDS.search(text):
            return False
        return bool(PAYROLL_WORDS.search(text)) or self.employer_of(t) is not None

    def matches_stub(self, t: Transaction) -> bool:
        return any(
            abs(t.amount_cents - net) <= MATCH_CENTS and abs((t.date - on).days) <= MATCH_DAYS for on, net in self.stubs
        )


def picked_as_pay(ledger: Ledger, t: Transaction) -> bool:
    """Someone filed it under Paycheck by hand (a bank's or Plaid's own guess isn't enough)."""
    c = ledger.categories.get(t.category_id) if t.category_id else None
    return c is not None and c.name == "Paycheck" and t.category_source == "user"


def is_pay_deposit(ledger: Ledger, t: Transaction, payers: Payroll | None) -> bool:
    """A deposit of pay, even one left uncategorized (which would otherwise count as a refund against spending)."""
    if payers is None or ledger.kind(t) not in ("income", "other", "transfer") or not payers.matches(t):
        return False
    # Something filed as a transfer is pay only if its words say so, not just because the amount fits a stub.
    return ledger.kind(t) != "transfer" or bool(PAYROLL_WORDS.search(text_of(t))) or payers.employer_of(t) is not None


def pay_merchants(ledger: Ledger, stubs: list[tuple[date, int]], prefs: dict | None = None) -> Payroll:
    """Who pays the household, from recent pay stubs (`stubs` is (pay date, take-home cents) each), the deposits
    themselves, and what the household typed in or ruled out (budget preferences "payroll" and "not_pay")."""
    prefs = prefs or {}
    not_pay = set(prefs.get("not_pay", []))
    probe = Payroll(not_pay=not_pay, stubs=stubs)
    employers = set(prefs.get("payroll", []))
    for t in ledger.txns:
        if t.amount_cents <= 0 or ledger.kind(t) not in ("income", "other", "transfer"):
            continue
        text = text_of(t)
        by_stub = probe.matches_stub(t) and ledger.kind(t) != "transfer"
        if by_stub or picked_as_pay(ledger, t) or (PAYROLL_WORDS.search(text) and not NOT_PAY_WORDS.search(text)):
            employers.add(employer_key(text))
    paycheck_ids = {c.id for c in ledger.categories.values() if c.name == "Paycheck"}
    return Payroll(employers - not_pay, not_pay, stubs, paycheck_ids)


def flows(ledger: Ledger, m: date, payers: Payroll | None, skip: set[int] | frozenset = frozenset()) -> dict:
    """One month of money in and out, with pay told apart from other money coming in.

    Transfers between the household's own accounts and card payments are left out (the ledger has no transfer
    pairs, and the Transfer category is neither income nor spending). The Plan page's month rows and the savings
    rate both come from here, so they agree.
    """
    pay = taxes = deposits = 0
    other: dict[str, int] = defaultdict(int)  # category name -> cents in
    spent: dict[int | None, int] = defaultdict(int)  # category id -> cents out (refunds come off)
    for t in ledger.month(m):
        if t.id in skip:
            continue
        if ledger.is_taxes(t):
            taxes -= t.amount_cents
        elif is_pay_deposit(ledger, t, payers):
            pay += t.amount_cents
            deposits += 1
        elif ledger.is_income(t):
            other[ledger.categories[t.category_id].name] += t.amount_cents
        elif ledger.is_spending(t):
            spent[t.category_id] -= t.amount_cents
    return {"pay": pay, "pay_deposits": deposits, "other": dict(other), "taxes": taxes, "spent": dict(spent)}


class Moves:
    """Money moved between the household's own accounts (both sides seen), to say where savings went."""

    def __init__(self, db: DbSession, accounts: list[Account], since: date) -> None:
        self.accounts = {a.id: a for a in accounts}
        rows = (
            db.scalars(
                select(Transaction).where(
                    Transaction.account_id.in_(list(self.accounts)),
                    Transaction.date >= since,
                    Transaction.transfer_group_id.is_not(None),
                )
            ).all()
            if accounts
            else []
        )
        self.groups: dict = defaultdict(list)
        for t in rows:
            self.groups[t.transfer_group_id].append(t)


EVERYDAY_SUBTYPES = (None, "checking", "paypal", "prepaid", "cash management")
CARD_PAYMENT = re.compile(transfers.CARD_PAYMENT, re.IGNORECASE)
BROKERAGE = re.compile(transfers.BROKERAGES, re.IGNORECASE)
BROKERAGE_NAMES = {"etrade": "E*TRADE", "e*trade": "E*TRADE"}
SPENDING_GROUPS = (("fixed", "Bills"), ("flexible", "Everyday"), ("other", "Other spending"))
INCOME_NAMES = {"Other income": "Other money in"}
WORTH_A_LOOK_CENTS = 100_00


def item(label: str, cents: int, **extra) -> dict:
    return {"label": label, "cents": cents, **extra}


def spending_groups(ledger: Ledger, spent: dict[int | None, int]) -> list[dict]:
    """Spending in bills, everyday and other, each with its categories, biggest first."""
    out = []
    for kind, label in SPENDING_GROUPS:
        cats = []
        for cid, cents in spent.items():
            c = ledger.categories.get(cid) if cid else None
            if (c.kind if c else "other") == kind and cents:
                cats.append(item(c.name if c else "Uncategorized", cents))
        if cats:
            cats.sort(key=lambda x: -x["cents"])
            out.append(item(label, sum(x["cents"] for x in cats), items=cats))
    return out


def where_it_went(ledger: Ledger, moves: Moves, m: date, saved: int, put_away: int) -> tuple[list[dict], dict]:
    """Where the month's savings went, lines that add up to `saved`, and the sums worth a second look.

    Money put away before pay; net moves into savings, brokerage and loan accounts that are linked; one-sided
    transfers out to a brokerage, to a card that isn't linked, or elsewhere (Zelle, Venmo and the like); money
    moved in from accounts that aren't linked; and what's left, which stayed in (or came out of) the everyday
    accounts.
    """
    lines = []
    if put_away:
        lines.append(item("Into your 401(k), HSA or ESPP from your paychecks", put_away, kind="retirement"))
    nxt = add_months(m, 1)
    net: dict[int, int] = defaultdict(int)
    for sides in moves.groups.values():
        if any(moves.accounts[t.account_id].type == "credit" for t in sides):
            continue  # a card payment: what it paid for is already spending
        for t in sides:
            if m <= t.date < nxt:
                net[t.account_id] += t.amount_cents
    for account_id, cents in sorted(net.items(), key=lambda x: -x[1]):
        a = moves.accounts[account_id]
        if not cents or (a.type == "depository" and (a.subtype or None) in EVERYDAY_SUBTYPES):
            continue
        kind = {"investment": "invest", "loan": "loan"}.get(a.type, "savings")
        if cents > 0:
            label = f"Paid down {a.name}" if kind == "loan" else f"Into {a.name}"
        else:
            label = f"Taken out of {a.name}"
        lines.append(item(label, cents, kind=kind))

    brokerages: dict[str, int] = defaultdict(int)
    elsewhere: dict[str, int] = defaultdict(int)
    cards = moved_in = 0
    for t in ledger.month(m):
        if ledger.kind(t) != "transfer":
            continue
        text = f"{t.merchant} {t.description}"
        if t.amount_cents > 0:
            moved_in += t.amount_cents
        elif ledger.is_card_payment(t) or CARD_PAYMENT.search(text):
            cards -= t.amount_cents
        elif hit := BROKERAGE.search(text):
            name = hit.group(0).lower()
            brokerages[BROKERAGE_NAMES.get(name, name.title())] -= t.amount_cents
        else:
            elsewhere[t.merchant or t.description] -= t.amount_cents
    for name, cents in sorted(brokerages.items(), key=lambda x: -x[1]):
        lines.append(item(f"Into {name}", cents, kind="invest"))
    if cards:
        lines.append(item("Paid to cards that aren't linked", cards, kind="card"))
    other_out = sum(elsewhere.values())
    if other_out:
        top = sorted(elsewhere.items(), key=lambda x: -x[1])[:5]
        lines.append(
            item("Sent to accounts that aren't linked", other_out, kind="other", items=[item(k, v) for k, v in top])
        )
    if moved_in:
        lines.append(item("Moved in from accounts that aren't linked", -moved_in, kind="moved_in"))
    left = saved - sum(x["cents"] for x in lines)
    lines.append(
        item(
            "Stayed in your everyday accounts" if left >= 0 else "Came out of your everyday accounts", left, kind="left"
        )
    )
    return lines, {"unlinked_cards": cards, "other_transfers": other_out}


def savings_for(
    ledger: Ledger,
    stubs: list[Paycheck],
    m: date,
    include_match: bool,
    payers: Payroll | None = None,
    moves: Moves | None = None,
) -> dict | None:
    nxt = add_months(m, 1)
    month_stubs = [p for p in stubs if m <= p.date < nxt]
    # Deposits a few days either side of the month can belong to a stub in it.
    window = [
        t
        for t in ledger.txns
        if (ledger.is_income(t) or is_pay_deposit(ledger, t, payers))
        and m - timedelta(days=MATCH_DAYS) <= t.date < nxt + timedelta(days=MATCH_DAYS)
    ]
    matched_ids = {t.id for t in match_deposits(month_stubs, window).values()}
    f = flows(ledger, m, payers, matched_ids)
    other_income = f["pay"] + sum(f["other"].values())
    gross = sum(p.gross_cents for p in month_stubs)
    match = sum(p.employer_match_cents for p in month_stubs) if include_match else 0
    income = gross + other_income + match
    if income <= 0:
        return None
    taxes = sum(p.taxes_cents for p in month_stubs) + f["taxes"]
    deductions = sum(p.gross_cents - p.taxes_cents - p.savings_cents - p.net_cents for p in month_stubs)
    spending = sum(f["spent"].values())
    saved = income - taxes - deductions - spending
    basis = "gross" if month_stubs else "take_home"

    money_in = []
    if gross:
        money_in.append(
            item(f"Pay before taxes, {len(month_stubs)} pay stub{'s' if len(month_stubs) > 1 else ''}", gross)
        )
    if match:
        money_in.append(item("Your employer's 401(k) match", match))
    if f["pay"]:
        money_in.append(item("Pay with no pay stub" if gross else "Pay (take-home)", f["pay"]))
    for name, cents in sorted(f["other"].items(), key=lambda x: -x[1]):
        money_in.append(item(INCOME_NAMES.get(name, name), cents))
    money_out = []
    if taxes:
        money_out.append(item("Taxes", taxes))
    if deductions:
        money_out.append(item("Other paycheck deductions (health insurance and the like)", deductions))
    money_out += spending_groups(ledger, f["spent"])
    put_away = sum(p.savings_cents for p in month_stubs) + match
    went, checks = where_it_went(ledger, moves, m, saved, put_away) if moves else ([], {})
    other_in = f["other"].get("Other income", 0)
    notes = []
    if basis == "take_home":
        notes.append({"kind": "take_home"})
    if checks.get("unlinked_cards"):
        notes.append({"kind": "unlinked_cards", "cents": checks["unlinked_cards"]})
    if checks.get("other_transfers", 0) >= WORTH_A_LOOK_CENTS:
        notes.append({"kind": "other_transfers", "cents": checks["other_transfers"]})
    if other_in >= WORTH_A_LOOK_CENTS and other_in >= income * 0.1:
        notes.append({"kind": "other_income", "cents": other_in})
    return {
        "month": m.isoformat(),
        "percent": round(saved / income * 100, 1),
        "basis": basis,
        "income_cents": income,
        "taxes_cents": taxes,
        "deductions_cents": deductions,
        "spending_cents": spending,
        "saved_cents": saved,
        "match_cents": match,
        "stubs": len(month_stubs),
        "breakdown": {"money_in": money_in, "money_out": money_out, "went": went, "notes": notes},
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
    forecast.for_lines(ledger, months, this_month, end, lines)
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
    recent = db.scalars(select(Paycheck).order_by(Paycheck.date.desc(), Paycheck.id.desc()).limit(12)).all()
    payers = pay_merchants(ledger, [(p.date, p.net_cents) for p in recent], prefs)
    moves = Moves(db, accounts, since)
    history = [s for m in months if (s := savings_for(ledger, stubs, m, prefs["include_match"], payers, moves))]

    held = goals.cash_in_goals(db, accounts)
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
        "forecast": forecast.summary(lines),
        "savings_rate": history[-1] if history else None,
        "savings_history": history,
        "emergency": {
            "months": prefs["emergency_months"],
            "monthly_cents": essentials,
            "target_cents": held["target_cents"]
            if held["target_cents"] is not None
            else essentials * prefs["emergency_months"],
            "target_typed": held["target_cents"] is not None,
            # Only cash meant for emergencies: the accounts picked for it, or the cash no other goal has (goals.py).
            "have_cents": held["have_cents"],
            "set_aside_cents": held["set_aside_cents"],
            "picked": held["picked"],
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
                "frequency": p.frequency,
                "deposit": {"date": matched[p.id].date.isoformat(), "merchant": matched[p.id].merchant}
                if p.id in matched
                else None,
            }
            for p in recent
        ],
        "people": [{"id": i, "name": n} for i, n in people.items()],
        "schedules": schedules(recent, people),
        "categories": [
            {"id": c.id, "name": c.name, "kind": c.kind, "essential": c.essential}
            for c in sorted(ledger.categories.values(), key=lambda c: (c.sort, c.name))
            if c.kind in BUDGETED
        ],
    }


# ---- pay schedules

PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


def next_payday(last: date, frequency: str) -> date:
    if frequency == "weekly":
        return last + timedelta(days=7)
    if frequency == "biweekly":
        return last + timedelta(days=14)
    if frequency == "semimonthly":  # the 15th and the last day of the month
        if last.day < 15:
            return last.replace(day=15)
        end = add_months(month_start(last), 1) - timedelta(days=1)
        return end if last < end else add_months(month_start(last), 1).replace(day=15)
    nxt = add_months(month_start(last), 1)
    end = add_months(nxt, 1) - timedelta(days=1)
    return nxt.replace(day=min(last.day, end.day))


def schedules(recent: list[Paycheck], people: dict[int, str]) -> list[dict]:
    """Each person's pay schedule, from their latest stub that says how often they're paid."""
    out = []
    seen: set[int | None] = set()
    for p in recent:  # newest first
        if p.user_id in seen or not p.frequency:
            continue
        seen.add(p.user_id)
        out.append(
            {
                "user_id": p.user_id,
                "person": people.get(p.user_id) if p.user_id else None,
                "frequency": p.frequency,
                "next_date": next_payday(p.date, p.frequency).isoformat(),
                # A biweekly month can have 3 paychecks: this is the average month.
                "monthly_gross_cents": round(p.gross_cents * PER_YEAR[p.frequency] / 12),
                "monthly_net_cents": round(p.net_cents * PER_YEAR[p.frequency] / 12),
            }
        )
    return out


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
