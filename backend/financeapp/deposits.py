"""Bank accounts in detail, for Settings → Accounts: what each checking, savings and CD account pays, what it has
earned, how that compares with the market, and for a CD its terms and what it's worth at maturity.

Where the numbers come from:
- APY: typed in on the account (or on Dividends & interest) first, then the APY the bank sends with each sync
  (Plaid's `apy`, when the bank fills it in), then an estimate from the last three months of interest.
- Interest: deposits categorized as interest (income.payments), so it matches Dividends & interest.
- A CD's terms (opened, term, maturity, amount put in, penalty, renewal): typed in. Plaid doesn't send any of them,
  so what's missing is guessed: the opening date from maturity less the term, or from the account's first deposit;
  the amount put in from that deposit, or the balance less the interest added since.
- Earned so far: amount x ((1 + APY)^(days / 365) - 1). The APY already includes compounding, so how often the bank
  compounds doesn't matter. A CD that pays its interest out to another account earns simple interest instead.
- The market: national averages and Treasury yields (rates.py).
- Checking: bank fees from Plaid's BANK_FEES_* categories, and cash above about a month and a half of what leaves
  the account, which could be earning a savings rate.

It also queues the CD pushes (maturing in 30 days, in 7, today, grace period ending) and a push when the rate a bank
reports drops, from the scheduler's hourly check. Pushes name the account and the date, never an amount.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import income, kinds, rates
from financeapp.models import Account, AccountTerms, AlertEvent, Balance, Transaction, now

KINDS = ("checking", "savings", "cd")
FDIC_LIMIT = 250_000_00  # per depositor, per bank, per ownership category
DEFAULT_GRACE_DAYS = 7  # typical, not a rule: the bank's notice says
BUFFER_MONTHS = 1.5  # what checking keeps before the rest counts as idle
IDLE_MIN = 1_000_00
RATE_DROP = 0.25  # percentage points, month to month, before a push
CD_NOTICES = (30, 7, 0)  # days before maturity
NOTICE_WINDOW = 3  # a notice that should have gone out more than this many days ago is skipped
FEE_LABELS = {
    "BANK_FEES_OVERDRAFT_FEES": "Overdraft",
    "BANK_FEES_INSUFFICIENT_FUNDS": "Insufficient funds",
    "BANK_FEES_ATM_FEES": "ATM",
    "BANK_FEES_FOREIGN_TRANSACTION_FEES": "Foreign transaction",
    "BANK_FEES_LATE_FEES": "Late",
    "BANK_FEES_INTEREST_CHARGE": "Interest",
    "BANK_FEES_CASH_ADVANCE": "Cash advance",
    "BANK_FEES_OTHER_BANK_FEES": "Other bank fees",
}
EDITABLE = (
    "apy", "opened", "term_months", "maturity", "principal_cents", "penalty_days", "auto_renew", "grace_days",
    "pays_out", "monthly_fee_cents", "min_balance_cents",
)  # fmt: skip


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def months_between(a: date, b: date) -> int:
    return round((b - a).days / 30.4375)


def implied_apy(pays: list[income.Payment], balance_cents: int, today: date) -> float | None:
    """The APY the last three months of interest work out to; None for none, or a one-off bonus."""
    recent = [p for p in pays if p.date > today - timedelta(days=95)]
    if not recent or balance_cents <= 0:
        return None
    months = len({(p.date.year, p.date.month) for p in recent})
    monthly = sum(p.cents for p in recent) / months
    apy = round(((1 + monthly / balance_cents) ** 12 - 1) * 100, 2)
    return apy if apy <= 25 else None


def grown(principal: int, apy: float, days: int, simple: bool) -> int:
    """Interest on principal over days at apy (percent)."""
    if days <= 0 or principal <= 0:
        return 0
    if simple:
        return round(principal * apy / 100 * days / 365)
    return round(principal * ((1 + apy / 100) ** (days / 365) - 1))


@dataclass
class CdInputs:
    balance_cents: int
    apy: float | None
    opened: date | None = None
    term_months: int | None = None
    maturity: date | None = None
    principal_cents: int | None = None
    penalty_days: int | None = None
    auto_renew: bool | None = None
    grace_days: int | None = None
    pays_out: bool | None = None
    first_deposit: tuple[date, int] | None = None  # the account's first deposit, if history reaches back that far
    credited_cents: int = 0  # interest that came into the CD itself since it opened


def cd(i: CdInputs, today: date, rates_now: dict | None = None) -> dict:
    """A CD's numbers from what's typed, filling gaps from its history. `guessed` names what wasn't typed."""
    guessed: list[str] = []
    opened, term, maturity, principal = i.opened, i.term_months, i.maturity, i.principal_cents
    if opened is None and maturity and term:
        opened = add_months(maturity, -term)
        guessed.append("opened")
    if opened is None and i.first_deposit and i.first_deposit[1] >= i.balance_cents // 2:
        opened = i.first_deposit[0]
        guessed.append("opened")
    if term is None and opened and maturity:
        term = months_between(opened, maturity)
        guessed.append("term_months")
    if maturity is None and opened and term:
        maturity = add_months(opened, term)
        guessed.append("maturity")
    simple = bool(i.pays_out)
    if principal is None:
        if i.first_deposit and opened and abs((i.first_deposit[0] - opened).days) <= 7:
            principal = i.first_deposit[1]
        else:
            principal = i.balance_cents - (0 if simple else i.credited_cents)
        guessed.append("principal_cents")
    # Interest the bank added shows in the balance even when no transaction says so.
    credited = i.credited_cents if simple else max(i.credited_cents, i.balance_cents - principal, 0)

    out: dict = {
        "opened": opened.isoformat() if opened else None,
        "term_months": term,
        "maturity": maturity.isoformat() if maturity else None,
        "principal_cents": principal,
        "penalty_days": i.penalty_days,
        "auto_renew": i.auto_renew,
        "grace_days": i.grace_days,
        "grace_typical": i.grace_days is None,
        "pays_out": i.pays_out,
        "guessed": guessed,
        "missing": [k for k, v in (("apy", i.apy), ("opened", opened), ("maturity", maturity)) if v is None],
        "earned_cents": None,
        "credited_cents": credited,
        "built_up_cents": None,
        "interest_total_cents": None,
        "value_at_maturity_cents": None,
        "days_left": None,
        "days_total": None,
        "progress": None,
        "penalty_cents": None,
        "grace_ends": None,
        "status": None,
        "break_even": None,
        "balance_gap_cents": None,
    }
    if maturity:
        grace_ends = maturity + timedelta(days=i.grace_days if i.grace_days is not None else DEFAULT_GRACE_DAYS)
        out["grace_ends"] = grace_ends.isoformat()
        out["days_left"] = max((maturity - today).days, 0)
        if today < maturity:
            out["status"] = "open"
        elif today <= grace_ends:
            out["status"] = "grace"
        else:
            out["status"] = "renewed" if i.auto_renew else "matured"
    if i.apy is None or opened is None:
        return out
    end = maturity or today
    days_total = (end - opened).days if maturity else None
    days = (min(today, end) - opened).days
    earned = grown(principal, i.apy, days, simple)
    out["earned_cents"] = earned
    out["built_up_cents"] = None if simple else max(earned - credited, 0)
    if days_total:
        total = grown(principal, i.apy, days_total, simple)
        out["days_total"] = days_total
        out["progress"] = round(min(max(days / days_total, 0), 1), 3)
        out["interest_total_cents"] = total
        out["value_at_maturity_cents"] = principal + (0 if simple else total)
    if i.penalty_days:
        out["penalty_cents"] = grown(principal, i.apy, i.penalty_days, True)
    if (
        not simple
        and principal
        and i.credited_cents
        and abs(i.balance_cents - principal - i.credited_cents) > max(5_00, i.balance_cents // 200)
    ):
        out["balance_gap_cents"] = i.balance_cents - principal - i.credited_cents
    # Breaking it early: what a Treasury over the time left would earn more, less the penalty.
    left = out["days_left"] or 0
    if rates_now and out["status"] == "open" and left > 30 and i.penalty_days:
        market = rates.benchmark(rates_now, "cd", max(round(left / 30.4375), 1))["treasury"]
        gain = round(principal * (market - i.apy) / 100 * left / 365) - out["penalty_cents"]
        out["break_even"] = {"rate": market, "gain_cents": gain}
    return out


def _first_deposits(db: DbSession, ids: list[int]) -> dict[int, tuple[date, int]]:
    if not ids:
        return {}
    first = (
        select(Transaction.account_id, func.min(Transaction.date).label("d"))
        .where(Transaction.account_id.in_(ids), Transaction.amount_cents > 0, Transaction.pending.is_(False))
        .group_by(Transaction.account_id)
        .subquery()
    )
    rows = db.execute(
        select(Transaction.account_id, Transaction.date, func.max(Transaction.amount_cents))
        .join(first, (first.c.account_id == Transaction.account_id) & (first.c.d == Transaction.date))
        .where(Transaction.amount_cents > 0)
        .group_by(Transaction.account_id, Transaction.date)
    )
    return {aid: (d, cents) for aid, d, cents in rows}


def _fees(db: DbSession, ids: list[int], today: date) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not ids:
        return out
    year_ago = today - timedelta(days=365)
    rows = db.execute(
        select(Transaction.account_id, Transaction.date, Transaction.amount_cents, Transaction.provider_category).where(
            Transaction.account_id.in_(ids),
            Transaction.date > year_ago,
            Transaction.amount_cents < 0,
            Transaction.pending.is_(False),
            Transaction.provider_category.like("BANK_FEES%"),
        )
    )
    for aid, d, cents, cat in rows:
        f = out.setdefault(aid, {"ytd_cents": 0, "12m_cents": 0, "by_kind": defaultdict(int)})
        f["12m_cents"] += -cents
        if d.year == today.year:
            f["ytd_cents"] += -cents
            f["by_kind"][FEE_LABELS.get(cat, "Other bank fees")] += -cents
    for f in out.values():
        f["by_kind"] = [{"label": k, "cents": v} for k, v in sorted(f["by_kind"].items(), key=lambda kv: -kv[1])]
    return out


def _monthly_out(db: DbSession, ids: list[int], today: date) -> dict[int, int]:
    """The average money out of each account over the last three whole months."""
    if not ids:
        return {}
    end = date(today.year, today.month, 1)
    start = add_months(end, -3)
    rows = db.execute(
        select(Transaction.account_id, func.sum(Transaction.amount_cents))
        .where(
            Transaction.account_id.in_(ids),
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.amount_cents < 0,
            Transaction.pending.is_(False),
        )
        .group_by(Transaction.account_id)
    )
    return {aid: round(-total / 3) for aid, total in rows}


def kind_of(a: Account) -> str:
    return kinds.kind_of(a.type, a.subtype, a.name, a.kind)


def build(db: DbSession, accounts: list[Account], today: date) -> dict[int, dict]:
    """The details of every checking, savings and CD account among these, by account id."""
    mine = [a for a in accounts if a.type not in ("credit", "loan") and kind_of(a) in KINDS]
    if not mine:
        return {}
    ids = [a.id for a in mine]
    terms = {t.account_id: t for t in db.scalars(select(AccountTerms).where(AccountTerms.account_id.in_(ids)))}
    balances = {}
    for a in mine:
        b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        balances[a.id] = b.current_cents if b else 0
    pays = defaultdict(list)
    for p in income.payments(db, mine, today - timedelta(days=731)):
        if p.kind == "interest":
            pays[p.account_id].append(p)
    cds = [a.id for a in mine if kind_of(a) == "cd"]
    checking = [a.id for a in mine if kind_of(a) == "checking"]
    firsts = _first_deposits(db, cds)
    fees = _fees(db, ids, today)
    outflow = _monthly_out(db, checking, today)
    market = rates.current(db)

    # Cash at one bank, across its accounts, for the FDIC limit.
    by_bank: dict[str, int] = defaultdict(int)
    for a in mine:
        by_bank[a.institution] += max(balances[a.id], 0)

    out: dict[int, dict] = {}
    for a in mine:
        kind = kind_of(a)
        t = terms.get(a.id)
        bal = balances[a.id]
        mine_pays = pays[a.id]
        implied = implied_apy(mine_pays, bal, today)
        typed = float(t.apy) if t and t.apy is not None else None
        bank = float(t.bank_apy) if t and t.bank_apy is not None else None
        apy, apy_from = next(
            ((v, src) for v, src in ((typed, "typed"), (bank, "bank"), (implied, "recent")) if v is not None),
            (None, None),
        )
        month_start = date(today.year, today.month, 1)
        d: dict = {
            "kind": kind,
            "apy": apy,
            "apy_from": apy_from,
            "apy_typed": typed,
            "bank_apy": bank,
            "implied_apy": implied,
            "interest": {
                "month_cents": sum(p.cents for p in mine_pays if p.date >= month_start),
                "ytd_cents": sum(p.cents for p in mine_pays if p.date.year == today.year),
                "12m_cents": sum(p.cents for p in mine_pays if p.date > today - timedelta(days=365)),
                "last_paid": mine_pays[-1].date.isoformat() if mine_pays else None,
            },
            "expected_12m_cents": round(bal * apy / 100) if apy is not None and bal > 0 else None,
            "market": rates.benchmark(
                market,
                "money market" if (a.subtype or "").lower() == "money market" else kind,
                t.term_months if t else None,
            ),
            "rates": [{"month": m, "apy": v} for m, v in sorted(((t.rates if t else None) or {}).items())][-12:],
            "fdic": {"bank_cents": by_bank[a.institution], "over_cents": max(by_bank[a.institution] - FDIC_LIMIT, 0)},
            "fees": fees.get(a.id, {"ytd_cents": 0, "12m_cents": 0, "by_kind": []}),
            "monthly_fee_cents": t.monthly_fee_cents if t else None,
            "min_balance_cents": t.min_balance_cents if t else None,
            "below_minimum": bool(t and t.min_balance_cents and bal < t.min_balance_cents),
            "idle": None,
            "cd": None,
        }
        if kind == "cd":
            d["cd"] = cd(
                CdInputs(
                    balance_cents=bal,
                    apy=apy,
                    opened=t.opened if t else None,
                    term_months=t.term_months if t else None,
                    maturity=t.maturity if t else None,
                    principal_cents=t.principal_cents if t else None,
                    penalty_days=t.penalty_days if t else None,
                    auto_renew=t.auto_renew if t else None,
                    grace_days=t.grace_days if t else None,
                    pays_out=t.pays_out if t else None,
                    first_deposit=firsts.get(a.id),
                    credited_cents=sum(
                        p.cents
                        for p in mine_pays
                        if not (t and t.opened) or p.date >= t.opened  # type: ignore[operator]
                    ),
                ),
                today,
                market,
            )
            if d["cd"]["term_months"]:
                d["market"] = rates.benchmark(market, "cd", d["cd"]["term_months"])
        if kind == "checking" and a.id in outflow:
            buffer = round(outflow[a.id] * BUFFER_MONTHS)
            idle = bal - buffer
            if idle >= IDLE_MIN:
                d["idle"] = {"cents": idle, "buffer_cents": buffer, "monthly_out_cents": outflow[a.id]}
        out[a.id] = d

    # Idle checking money: what it would earn at the best savings rate here, or a 3-month Treasury.
    savings_rates = [d["apy"] for d in out.values() if d["kind"] == "savings" and d["apy"] is not None]
    best = max(savings_rates) if savings_rates else None
    for d in out.values():
        if d["idle"]:
            rate = best if best is not None else market["treasury"]["3"]
            d["idle"] |= {
                "rate": rate,
                "rate_from": "savings" if best is not None else "treasury",
                "earn_cents": round(d["idle"]["cents"] * (rate - (d["apy"] or 0)) / 100),
            }
    return out


def get_terms(db: DbSession, account_id: int) -> AccountTerms:
    return db.get(AccountTerms, account_id) or AccountTerms(account_id=account_id, rates={})


def save_terms(db: DbSession, account_id: int, values: dict) -> None:
    t = get_terms(db, account_id)
    for k, v in values.items():
        if k in EDITABLE:
            setattr(t, k, Decimal(str(v)) if k == "apy" and v is not None else v)
    t.updated_at = now()
    db.merge(t)


# ---- pushes


def record_rates(db: DbSession, details: dict[int, dict], today: date) -> list[tuple[int, float, float]]:
    """Notes this month's rate for each account; returns (account, last month's, now) where a bank-reported rate
    dropped by RATE_DROP or more."""
    month = f"{today.year:04d}-{today.month:02d}"
    prev = add_months(date(today.year, today.month, 1), -1)
    prev_key = f"{prev.year:04d}-{prev.month:02d}"
    drops = []
    for aid, d in details.items():
        if d["apy"] is None:
            continue
        t = get_terms(db, aid)
        saved = dict(t.rates or {})
        saved[month] = d["apy"]
        t.rates = dict(sorted(saved.items())[-24:])
        db.merge(t)
        before = saved.get(prev_key)
        if d["apy_from"] == "bank" and before is not None and before - d["apy"] >= RATE_DROP:
            drops.append((aid, before, d["apy"]))
    return drops


def _event(db: DbSession, title: str, body: str, key: str, account_id: int) -> bool:
    stmt = insert(AlertEvent).values(
        rule_id=None, kind="account", title=title[:120], body=body[:300], dedup_key=key[:160], fired_at=now(),
        status="pending", attempts=0, link=f"/settings/accounts?account={account_id}",
    )  # fmt: skip
    return db.scalar(stmt.on_conflict_do_nothing(index_elements=["dedup_key"]).returning(AlertEvent.id)) is not None


def notices(name: str, c: dict, today: date) -> list[tuple[str, str, str]]:
    """The CD pushes due today: (stage, title, body). Only the latest one that's due, and not one that's days old
    (the first check after the terms are typed in shouldn't send a month of notices at once)."""
    if not c["maturity"]:
        return []
    maturity = date.fromisoformat(c["maturity"])
    grace_ends = date.fromisoformat(c["grace_ends"])
    nice = f"{maturity:%b} {maturity.day}"
    grace = f"{grace_ends:%b} {grace_ends.day}"
    renew = (
        "It renews by itself at the bank's rate then" if c["auto_renew"] else "Decide whether to renew it or move it"
    )
    stages = [
        (f"{n}d", maturity - timedelta(days=n), f"{name} matures in {n} days" if n else f"{name} matures today")
        for n in CD_NOTICES
    ]
    stages.append(("grace", grace_ends - timedelta(days=1), f"{name}: grace period ends tomorrow"))
    due = [s for s in stages if s[1] <= today]
    if not due or (c["status"] in ("renewed", "matured") and today > grace_ends):
        return []
    stage, on, title = due[-1]
    if (today - on).days > NOTICE_WINDOW:
        return []
    if stage == "grace":
        body = f"After {grace} it's locked in again for another term. Move it or change it at the bank before then."
        if c["auto_renew"] is False:
            body = f"After {grace} the money just sits there. Move it somewhere that pays."
    elif stage == "0d":
        body = f"You have until about {grace} to move it without a penalty. {renew}."
    else:
        body = f"It matures {nice}. {renew}; compare rates in the app before then."
    return [(stage, title, body)]


def queue(db: DbSession, today: date) -> int:
    """Saves the CD pushes and rate-drop pushes due today (deduplicated by account, date and stage)."""
    from financeapp import household  # household imports a lot; only when needed

    accounts = [a for a in household.counted(db) if not a.hidden]
    details = build(db, accounts, today)
    names = {a.id: household.display_name(a) for a in accounts}
    added = 0
    for aid, d in details.items():
        if d["cd"]:
            for stage, title, body in notices(names[aid], d["cd"], today):
                added += _event(db, title, body, f"cd:{aid}:{d['cd']['maturity']}:{stage}", aid)
    for aid, before, after in record_rates(db, details, today):
        title = f"{names[aid]} cut its rate"
        body = f"The bank now reports {after:.2f}% APY, down from {before:.2f}% last month. Worth comparing rates."
        added += _event(db, title, body, f"rate:{aid}:{today:%Y-%m}", aid)
    return added
