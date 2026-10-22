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

import itertools
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
        self.accounts = {a.id: a for a in accounts}
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


def match_paydays(
    stubs: list[Paycheck], deposits: list[Transaction], payers: "Payroll | None"
) -> dict[int, list[Transaction]]:
    """paycheck id -> the deposits its take-home landed as: one deposit of the same amount, or else a payday's
    deposits across accounts that add up to it (within $1, within 5 days)."""
    matched = {pid: [t] for pid, t in match_deposits(stubs, deposits).items()}
    if payers is None:
        return matched
    taken = {t.id for ts in matched.values() for t in ts}
    groups: dict[tuple[str, date], list[Transaction]] = defaultdict(list)
    for t in deposits:
        if t.id in payers.paydays and t.id not in taken:
            name, day, _total = payers.paydays[t.id]
            groups[(name, day)].append(t)
    for p in sorted(stubs, key=lambda p: p.date):
        if p.id in matched:
            continue
        for key, ts in sorted(groups.items(), key=lambda kv: abs((kv[0][1] - p.date).days)):
            close = abs((key[1] - p.date).days) <= MATCH_DAYS
            if close and abs(sum(t.amount_cents for t in ts) - p.net_cents) <= MATCH_CENTS:
                matched[p.id] = ts
                del groups[key]
                break
    return matched


# Words a bank puts around an employer's name on a pay deposit. Wording differs from bank to bank ("Deposit from
# NETAPP INC PAYROLL", "NETAPP INC DIR DEP PPD"), so an employer is known by what's left once these are gone.
PAYROLL_WORDS = re.compile(r"payroll|direct dep|dir dep|\bdirdep\b|salary|\bwages?\b", re.I)
BONUS_FACTOR = 1.4  # a paycheck this much over the usual one is a bonus
BONUS_MIN_DEPOSITS = 3  # paychecks to know what usual is
PAYDAY_DAYS = 3  # deposits from one employer this close together are one paycheck, split across accounts
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
        self.typical: dict[str, int] = {}  # employer -> its usual paycheck (the median payday)
        self.counts: dict[str, int] = {}  # employer -> paydays seen
        # A paycheck can land in several accounts at once (a set amount to savings, the rest to checking), so pay
        # is counted by payday: txn id -> (employer, payday, the whole paycheck).
        self.paydays: dict[int, tuple[str, date, int]] = {}
        self.every_days: dict[str, float] = {}  # employer -> days between paychecks, typically
        # Names that turned out to be the same employer (one bank says "EARLY NETAPP", another "NETAPP"), and
        # deposits that are part of a paycheck without saying so (into an account the stub says pay is split to).
        self.alias: dict[str, str] = {}
        self.split_ids: dict[int, str] = {}

    def source(self, t: Transaction) -> str:
        """Who a pay deposit is from: its employer, however the bank words it."""
        if t.id in self.split_ids:
            return self.split_ids[t.id]
        name = self.employer_of(t) or employer_key(text_of(t)) or payer(t)
        return self.alias.get(name, name)

    def is_bonus(self, t: Transaction) -> bool:
        """A paycheck well over the usual one from the same employer: a bonus, or RSU or commission pay."""
        name, _day, cents = self.payday_of(t)
        return self.counts.get(name, 0) >= BONUS_MIN_DEPOSITS and cents > self.typical[name] * BONUS_FACTOR

    def payday_of(self, t: Transaction) -> tuple[str, date, int]:
        return self.paydays.get(t.id) or (self.source(t), t.date, t.amount_cents)

    def usual_per_month(self, name: str) -> int | None:
        """Paychecks in a usual month: 2 when paid every two weeks (a month with a third one is the exception)."""
        days = self.every_days.get(name)
        if days is None:
            return None
        if 12 <= days <= 16:
            return 2
        if 6 <= days <= 8:
            return 4
        return None

    def employer_of(self, t: Transaction) -> str | None:
        words = set(text_of(t).split())
        for e in sorted(self.employers, key=lambda e: (len(e.split()), e)):  # "netapp" before "early netapp"
            if set(e.split()) <= words:
                return self.alias.get(e, e)
        return None

    def matches(self, t: Transaction) -> bool:
        if t.amount_cents <= 0:
            return False
        if t.id in self.split_ids:
            return True
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
    if t.id in payers.split_ids:
        return True
    # Something filed as a transfer is pay only if its words say so, not just because the amount fits a stub.
    return ledger.kind(t) != "transfer" or bool(PAYROLL_WORDS.search(text_of(t))) or payers.employer_of(t) is not None


def pay_merchants(
    ledger: Ledger, stubs: list[tuple[date, int]], prefs: dict | None = None, splits: list[dict] | None = None
) -> Payroll:
    """Who pays the household, from recent pay stubs (`stubs` is (pay date, take-home cents) each), the deposits
    themselves, and what the household typed in or ruled out (budget preferences "payroll" and "not_pay").

    A paycheck split across accounts lands as several deposits on one payday, often worded differently by each
    bank. They're one paycheck: an employer name whose paydays are the main employer's paydays is the same
    employer, and a deposit into an account the latest stub says pay goes to (`splits`, [{"mask", "cents"}]) on
    payday is part of that paycheck whatever its wording."""
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
    payroll = Payroll(employers - not_pay, not_pay, stubs, paycheck_ids)
    by_source: dict[str, list[Transaction]] = defaultdict(list)
    for t in ledger.txns:
        if is_pay_deposit(ledger, t, payroll):
            by_source[payroll.source(t)].append(t)
    same_employers(payroll, by_source)
    split_deposits(ledger, payroll, by_source, splits or [])
    for name, deps in by_source.items():
        paydays = group_paydays(deps)
        for day, group in paydays:
            total = sum(t.amount_cents for t in group)
            for t in group:
                payroll.paydays[t.id] = (name, day, total)
        payroll.typical[name] = round(statistics.median(sum(t.amount_cents for t in g) for _d, g in paydays))
        payroll.counts[name] = len(paydays)
        days = [d for d, _g in paydays]
        if len(days) >= 3:
            payroll.every_days[name] = statistics.median((b - a).days for a, b in itertools.pairwise(days))
    return payroll


SAME_PAYDAYS = 0.75  # an employer name this often paid on another's paydays is the same employer


def near(day: date, days: list[date]) -> bool:
    return any(abs((day - d).days) <= PAYDAY_DAYS for d in days)


def same_employers(payroll: Payroll, by_source: dict[str, list[Transaction]]) -> None:
    """Folds names paid on the same paydays as a bigger one, into other accounts, into it (in place), so each
    bank's wording of one paycheck isn't a separate employer."""
    order = sorted(by_source, key=lambda n: -sum(t.amount_cents for t in by_source[n]))
    for i, name in enumerate(order):
        days = sorted({t.date for t in by_source[name]})
        for main in order[:i]:
            if main not in by_source:
                continue
            main_days = sorted({t.date for t in by_source[main]})
            # Two people paid on the same days usually land in the same (joint) account; one split paycheck
            # lands in different accounts.
            landed = {(t.account_id, t.date) for t in by_source[main]}
            shared = any(
                (t.account_id, t.date + timedelta(days=k)) in landed
                for t in by_source[name]
                for k in range(-PAYDAY_DAYS, PAYDAY_DAYS + 1)
            )
            if not shared and len(days) >= 2 and sum(near(d, main_days) for d in days) >= SAME_PAYDAYS * len(days):
                payroll.alias[name] = main
                for e, target in list(payroll.alias.items()):
                    if target == name:
                        payroll.alias[e] = main
                by_source[main] += by_source.pop(name)
                break
    # Call each employer by the words all its names share: "early paycheck netapp" and "netapp des" are NETAPP.
    for main in list(by_source):
        names = [main] + [e for e, target in payroll.alias.items() if target == main]
        if len(names) == 1:
            continue
        shared = set.intersection(*(set(n.split()) for n in names))
        best = " ".join(w for w in main.split() if w in shared)
        if best and best != main:
            for e in names:
                payroll.alias[e] = best
            by_source[best] = by_source.pop(main) + by_source.pop(best, [])


SPLIT_SHARE = 0.25  # a split deposit can be this far off the stub's amount (a raise moves percentage splits)


def split_deposits(
    ledger: Ledger, payroll: Payroll, by_source: dict[str, list[Transaction]], splits: list[dict]
) -> None:
    """Deposits on payday into the accounts the stub splits pay to, that didn't say whose pay they are."""
    if not splits or not by_source:
        return
    amounts: dict[str, list[int]] = defaultdict(list)
    for d in splits:
        amounts[str(d.get("mask"))].append(int(d.get("cents") or 0))
    main = max(by_source, key=lambda n: sum(t.amount_cents for t in by_source[n]))
    paydays = sorted({t.date for t in by_source[main]})
    counted = {t.id for deps in by_source.values() for t in deps}
    for t in ledger.txns:
        a = ledger.accounts.get(t.account_id)
        if t.id in counted or t.amount_cents <= 0 or a is None or a.mask not in amounts:
            continue
        if ledger.kind(t) not in ("income", "other", "transfer") or not near(t.date, paydays):
            continue
        if any(abs(t.amount_cents - c) <= c * SPLIT_SHARE for c in amounts[a.mask]):
            payroll.split_ids[t.id] = main
            by_source[main].append(t)


def group_paydays(deposits: list[Transaction]) -> list[tuple[date, list[Transaction]]]:
    """One employer's deposits as paydays: those within a few days of each other are one paycheck."""
    paydays: list[tuple[date, list[Transaction]]] = []
    for t in sorted(deposits, key=lambda t: (t.date, t.id)):
        if paydays and (t.date - paydays[-1][0]).days <= PAYDAY_DAYS:
            paydays[-1][1].append(t)
        else:
            paydays.append((t.date, [t]))
    return paydays


def flows(ledger: Ledger, m: date, payers: Payroll | None, skip: set[int] | frozenset = frozenset()) -> dict:
    """One month of money in and out, with pay told apart from other money coming in.

    Transfers between the household's own accounts and card payments are left out (the ledger has no transfer
    pairs, and the Transfer category is neither income nor spending). The Plan page's month rows and the savings
    rate both come from here, so they agree.
    """
    pay = taxes = deposits = bonus = 0
    pay_by: dict[str, dict] = {}  # employer -> {"cents", "count", "bonus_cents"}
    seen: set[tuple[str, date]] = set()  # paydays counted, so a paycheck split across accounts counts once
    other: dict[str, int] = defaultdict(int)  # category name -> cents in
    other_by: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # category -> payer -> cents
    spent: dict[int | None, int] = defaultdict(int)  # category id -> cents out (refunds come off)
    for t in ledger.month(m):
        if t.id in skip:
            continue
        if ledger.is_taxes(t):
            taxes -= t.amount_cents
        elif is_pay_deposit(ledger, t, payers):
            name, day, _total = payers.payday_of(t)
            src = pay_by.setdefault(name, {"cents": 0, "count": 0, "bonus_cents": 0})
            if payers.is_bonus(t):
                bonus += t.amount_cents
                src["bonus_cents"] += t.amount_cents
            else:
                pay += t.amount_cents
                src["cents"] += t.amount_cents
                if (name, day) not in seen:
                    seen.add((name, day))
                    deposits += 1
                    src["count"] += 1
        elif ledger.is_income(t):
            name = ledger.categories[t.category_id].name
            other[name] += t.amount_cents
            other_by[name][t.merchant or t.description] += t.amount_cents
        elif ledger.is_spending(t):
            spent[t.category_id] -= t.amount_cents
    return {
        "pay": pay,  # usual paychecks; a bonus is kept apart so it doesn't set what a usual month looks like
        "pay_deposits": deposits,
        "bonus": bonus,
        "pay_by": pay_by,
        "other": dict(other),
        "other_by": {k: dict(v) for k, v in other_by.items()},
        "taxes": taxes,
        "spent": dict(spent),
    }


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
        # A bank transaction paired with a brokerage's own deposit record: that record is the other side.
        brokerages = [a.id for a in accounts if a.type == "investment"]
        for m in transfers.brokerage_moves(db, brokerages, since - timedelta(days=transfers.WINDOW_DAYS)):
            if (group := transfers.brokerage_group(m)) in self.groups:
                self.groups[group].append(m)


EVERYDAY_SUBTYPES = (None, "checking", "paypal", "prepaid", "cash management")
CARD_PAYMENT = re.compile(transfers.CARD_PAYMENT, re.IGNORECASE)
BROKERAGE = re.compile(transfers.BROKERAGES, re.IGNORECASE)
BROKERAGE_NAMES = {
    "etrade": "E*TRADE",
    "e*trade": "E*TRADE",
    "e trade": "E*TRADE",
    "e-trade": "E*TRADE",
    "morgan stanley": "E*TRADE",
    "mspbna": "E*TRADE",
    "ms&co": "E*TRADE",
}
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


def brokerage_of(text: str, invest: list[Account]) -> str | None:
    """The brokerage a transfer went to or came from: a linked investment account it names by its last four
    digits, else the brokerage's name ("E*TRADE" for Morgan Stanley, which runs it)."""
    for a in invest:
        if a.mask and re.search(rf"(?<!\d){re.escape(a.mask)}(?!\d)", text):
            return a.name
    if hit := BROKERAGE.search(text):
        name = hit.group(0).lower()
        return BROKERAGE_NAMES.get(name, name.title())
    return None


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
        if kind == "invest":
            from financeapp.household import display_name  # household imports this module

            name = display_name(a)  # whose brokerage: "E*TRADE Brokerage ••2600"
        else:
            name = a.name
        label = (f"Paid down {name}" if kind == "loan" else f"Into {name}") if cents > 0 else f"Taken out of {name}"
        lines.append(item(label, cents, kind=kind))

    # Investment accounts whose own transactions don't come through (a brokerage linked for its holdings): a
    # transfer naming one, by its last four digits or its brokerage, went into it (or came out of it).
    invest = [a for a in moves.accounts.values() if a.type == "investment"]
    brokerages: dict[str, int] = defaultdict(int)
    elsewhere: dict[str, int] = defaultdict(int)
    from_elsewhere: dict[str, int] = defaultdict(int)
    cards = moved_in = 0
    for t in ledger.month(m):
        if ledger.kind(t) != "transfer":
            continue
        text = f"{t.merchant} {t.description}"
        into = brokerage_of(text, invest)
        if into:
            brokerages[into] -= t.amount_cents
        elif t.amount_cents > 0:
            moved_in += t.amount_cents
            from_elsewhere[t.merchant or t.description] += t.amount_cents
        elif ledger.is_card_payment(t) or CARD_PAYMENT.search(text):
            cards -= t.amount_cents
        else:
            elsewhere[t.merchant or t.description] -= t.amount_cents
    for name, cents in sorted(brokerages.items(), key=lambda x: -x[1]):
        if cents:
            lines.append(item(f"Into {name}" if cents > 0 else f"Taken out of {name}", cents, kind="invest"))
    if cards:
        lines.append(item("Paid to cards that aren't linked", cards, kind="card"))
    other_out = sum(elsewhere.values())
    if other_out:
        top = sorted(elsewhere.items(), key=lambda x: -x[1])[:5]
        lines.append(
            item("Sent to accounts that aren't linked", other_out, kind="other", items=[item(k, v) for k, v in top])
        )
    if moved_in:
        top = sorted(from_elsewhere.items(), key=lambda x: -x[1])[:5]
        lines.append(
            item(
                "Moved in from accounts that aren't linked",
                -moved_in,
                kind="moved_in",
                items=[item(k, v) for k, v in top],
            )
        )
    left = saved - sum(x["cents"] for x in lines)
    lines.append(
        item(
            "Stayed in your everyday accounts" if left >= 0 else "Came out of your everyday accounts", left, kind="left"
        )
    )
    return lines, {"unlinked_cards": cards, "other_transfers": other_out}


class UsualStub:
    """The household's usual pay stub, as shares of its take-home: what a paycheck that landed without its stub
    added most likely was before taxes, and what came off it. From recent stubs, leaving out bonus ones."""

    def __init__(self, stubs: list[Paycheck]) -> None:
        self.ok = False
        if not stubs:
            return
        usual_gross = statistics.median(p.gross_cents for p in stubs)
        usual = [p for p in stubs if p.gross_cents <= usual_gross * BONUS_FACTOR and p.net_cents > 0]
        net = sum(p.net_cents for p in usual)
        if not net:
            return
        self.ok = True
        self.gross = sum(p.gross_cents for p in usual) / net
        self.taxes = sum(p.taxes_cents for p in usual) / net
        self.savings = sum(p.savings_cents for p in usual) / net
        self.deductions = sum(p.gross_cents - p.taxes_cents - p.savings_cents - p.net_cents for p in usual) / net
        self.match = sum(p.employer_match_cents for p in usual) / net


def savings_for(
    ledger: Ledger,
    stubs: list[Paycheck],
    m: date,
    include_match: bool,
    payers: Payroll | None = None,
    moves: Moves | None = None,
    usual: UsualStub | None = None,
) -> dict | None:
    """One month's savings rate, and the sums behind it.

    Pay comes from that month's pay stubs where they're in (pay before taxes, taxes withheld, 401(k) and the rest).
    A paycheck that landed without its stub is filled in from the usual stub, scaled to what landed, so a month
    with one stub of two (or none) still counts its withholding. A bonus is counted in the month it came, but kept
    out of the "usual month" rate, as is a rare third paycheck in a two-weekly month.
    """
    nxt = add_months(m, 1)
    month_stubs = [p for p in stubs if m <= p.date < nxt]
    # Deposits a few days either side of the month can belong to a stub in it.
    window = [
        t
        for t in ledger.txns
        if (ledger.is_income(t) or is_pay_deposit(ledger, t, payers))
        and m - timedelta(days=MATCH_DAYS) <= t.date < nxt + timedelta(days=MATCH_DAYS)
    ]
    matched_ids = {t.id for ts in match_paydays(month_stubs, window, payers).values() for t in ts}
    f = flows(ledger, m, payers, matched_ids)
    estimate = usual is not None and usual.ok and f["pay"] > 0
    est = {"gross": 0, "taxes": 0, "savings": 0, "deductions": 0, "match": 0}
    if estimate:
        est = {k: round(f["pay"] * getattr(usual, k)) for k in est}
    take_home = 0 if estimate else f["pay"]
    gross = sum(p.gross_cents for p in month_stubs)
    match = (sum(p.employer_match_cents for p in month_stubs) + est["match"]) if include_match else 0
    income = gross + est["gross"] + take_home + f["bonus"] + sum(f["other"].values()) + match
    if income <= 0:
        return None
    withheld = sum(p.taxes_cents for p in month_stubs)
    taxes = withheld + est["taxes"] + f["taxes"]
    deductions = sum(p.gross_cents - p.taxes_cents - p.savings_cents - p.net_cents for p in month_stubs)
    deductions += est["deductions"]
    spending = sum(f["spent"].values())
    saved = income - taxes - deductions - spending
    basis = "gross" if month_stubs or estimate else "take_home"

    # Where the pay came from: each employer, its paychecks, and any bonus.
    everyone = flows(ledger, m, payers)["pay_by"]  # with the stub-matched deposits, to count every paycheck
    money_in = []
    if gross:
        n = len(month_stubs)
        money_in.append(item(f"Pay before taxes, from {n} pay stub{'s' if n > 1 else ''}", gross, kind="stubs"))
    if est["gross"]:
        n = f["pay_deposits"]
        money_in.append(
            item(
                f"Pay before taxes, {n} paycheck{'s' if n > 1 else ''} without a stub (from your usual stub)",
                est["gross"],
                kind="estimated",
            )
        )
    if take_home:
        money_in.append(item("Pay (take-home)", take_home, kind="take_home"))
    if money_in and everyone:
        money_in[0]["items"] = [
            item(f"{name.upper()}: {v['count']} paycheck{'s' if v['count'] != 1 else ''}", v["cents"])
            for name, v in sorted(everyone.items(), key=lambda x: -x[1]["cents"])
            if v["count"]
        ]
    if match:
        money_in.append(item("Your employer's 401(k) match", match))
    if f["bonus"]:
        money_in.append(
            item(
                "Bonus (take-home), left out of your usual month",
                f["bonus"],
                kind="bonus",
                items=[item(name.upper(), v["bonus_cents"]) for name, v in everyone.items() if v["bonus_cents"]],
            )
        )
    for name, cents in sorted(f["other"].items(), key=lambda x: -x[1]):
        who = sorted(f["other_by"].get(name, {}).items(), key=lambda x: -x[1])[:6]
        money_in.append(item(INCOME_NAMES.get(name, name), cents, items=[item(k, v) for k, v in who]))
    money_out = []
    if taxes:
        parts = [
            item("Withheld, from your pay stubs", withheld),
            item("Withheld, estimated from your usual stub", est["taxes"]),
            item("Paid from your accounts (IRS, state)" if f["taxes"] >= 0 else "Refunds to your accounts", f["taxes"]),
        ]
        money_out.append(item("Taxes", taxes, items=[x for x in parts if x["cents"]]))
    if deductions:
        money_out.append(item("Other paycheck deductions (health insurance and the like)", deductions))
    money_out += spending_groups(ledger, f["spent"])

    # A usual month: no bonus, and no third paycheck in a month that usually has two.
    extra = 0
    extras = 0
    for name, v in everyone.items():
        per = payers.usual_per_month(name) if payers else None
        if per and v["count"] > per:
            extras += v["count"] - per
            extra += (v["count"] - per) * payers.typical[name]
    extra_gross = round(extra * usual.gross) if estimate or (usual and usual.ok and month_stubs) else extra
    extra_saved = round(extra * (1 + usual.savings)) if usual and usual.ok else extra
    usual_month = None
    if f["bonus"] or extras:
        u_income = income - f["bonus"] - extra_gross
        u_saved = saved - f["bonus"] - extra_saved
        if u_income > 0:
            usual_month = {
                "percent": round(u_saved / u_income * 100, 1),
                "saved_cents": u_saved,
                "bonus_cents": f["bonus"],
                "extra_paychecks": extras,
            }
    put_away = sum(p.savings_cents for p in month_stubs) + est["savings"] + match
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
        "estimated_paychecks": f["pay_deposits"] if estimate else 0,
        "usual": usual_month,
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
    payers = pay_merchants(ledger, [(p.date, p.net_cents) for p in recent], prefs, latest_splits(recent))
    moves = Moves(db, accounts, since)
    usual = UsualStub(list(recent))
    history = [s for m in months if (s := savings_for(ledger, stubs, m, prefs["include_match"], payers, moves, usual))]

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
    deposits = [t for t in ledger.txns if ledger.is_income(t) or is_pay_deposit(ledger, t, payers)]
    matched = {pid: ts[0] for pid, ts in match_paydays(recent, deposits, payers).items()}

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
                "deposits": (p.detail or {}).get("deposits", []),
                "deposit": {"date": matched[p.id].date.isoformat(), "merchant": matched[p.id].merchant}
                if p.id in matched
                else None,
            }
            for p in recent
        ],
        "people": [{"id": i, "name": n} for i, n in people.items()],
        "schedules": schedules(recent, people),
        "stub_requests": stub_requests(ledger, payers, stubs, end),
        "categories": [
            {"id": c.id, "name": c.name, "kind": c.kind, "essential": c.essential}
            for c in sorted(ledger.categories.values(), key=lambda c: (c.sort, c.name))
            if c.kind in BUDGETED
        ],
    }


def latest_splits(stubs) -> list[dict]:
    """Where the newest stub that says so split take-home: [{"mask", "cents"}] (stubs as rows or as JSON)."""
    for p in stubs:
        found = p.get("deposits") if isinstance(p, dict) else (p.detail or {}).get("deposits")
        if found:
            return found
    return []


# ---- asking for a pay stub when the usual one stops fitting

STUB_CHANGE = 0.03  # take-home this much off the last paycheck: taxes or 401(k) likely changed
STUB_ASK_DAYS = 45  # paydays this recent are worth a stub


def stub_requests(ledger: Ledger, payers: Payroll | None, stubs: list[Paycheck], end: date) -> list[dict]:
    """Recent paychecks whose pay stub would change the numbers: a paycheck that came in different from the one
    before (a raise, a W-4 or 401(k) change, the Social Security cap), the first one of a new year, or a bonus.
    Paychecks without a stub are filled in from the usual stub, which is only right while pay stays the same.
    A paycheck with a stub, or with a newer stub after it, needs nothing."""
    if payers is None:
        return []
    paydays: dict[tuple[str, date], int] = {}
    for name, day, total in payers.paydays.values():
        paydays[(name, day)] = total
    latest = max((p.date for p in stubs), default=None)
    asks = []
    by_source: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for (name, day), total in sorted(paydays.items(), key=lambda kv: kv[0][1]):
        by_source[name].append((day, total))
    for name, days in by_source.items():
        typical = payers.typical.get(name, 0)
        before: tuple[date, int] | None = None  # the last usual paycheck
        for day, total in days:
            bonus = payers.counts.get(name, 0) >= BONUS_MIN_DEPOSITS and total > typical * BONUS_FACTOR
            reason = None
            if bonus:
                reason = "bonus"
            elif before and before[0].year < day.year:
                reason = "new_year"
            elif before and abs(total - before[1]) > before[1] * STUB_CHANGE:
                reason = "changed"
            covered = (latest is not None and latest >= day - timedelta(days=MATCH_DAYS)) or any(
                abs((p.date - day).days) <= MATCH_DAYS for p in stubs
            )
            if reason and not covered and (end - day).days <= STUB_ASK_DAYS:
                asks.append({
                    "date": day.isoformat(),
                    "employer": name.upper(),
                    "cents": total,
                    "before_cents": before[1] if before else None,
                    "reason": reason,
                })  # fmt: skip
            if not bonus:
                before = (day, total)
    return sorted(asks, key=lambda a: a["date"], reverse=True)


def ask_for_stubs(db: DbSession, settings) -> int:
    """After a sync: a notification for each new paycheck that wants its stub, once each."""
    from financeapp import notify

    state = build(db)
    prefs = preferences(db)
    asked = set(prefs.get("stubs_asked", []))
    sent = 0
    for a in state["stub_requests"]:
        key = f"{a['employer']}:{a['date']}"
        if key in asked:
            continue
        asked.add(key)
        day = date.fromisoformat(a["date"]).strftime("%b %-d")
        why = {
            "bonus": "looks like a bonus, which is taxed differently",
            "new_year": "is the first of the year, when withholding and 401(k) limits reset",
            "changed": "came in different from the one before, so taxes or 401(k) may have changed",
        }[a["reason"]]
        notify.send(
            settings,
            "Add this pay stub",
            f"Your {day} paycheck from {a['employer']} {why}. Add its pay stub on Budget so your savings rate "
            "stays right.",
            tags="page_facing_up",
        )
        sent += 1
    if sent:
        set_preferences(db, stubs_asked=sorted(asked)[-50:])
    return sent


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
