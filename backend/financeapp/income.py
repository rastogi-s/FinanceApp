"""Dividends and interest: what came in, what's expected next, and the numbers the projections start from.

Where the numbers come from:
- Dividends, fund capital gain distributions and brokerage cash interest: each brokerage account's activity from
  Plaid Investments (investment_transactions), up to 24 months back. A dividend that was reinvested shows up as a
  cash dividend plus a "dividend reinvestment" buy; some brokerages send only the buy, which then counts as the
  dividend. Holdings with reinvestment buys in the last year are marked as reinvesting (DRIP).
- Bank interest: deposits into bank accounts categorized Interest & dividends (Plaid's INCOME_INTEREST_EARNED, or
  the description).
- What a holding pays per share, how often, and how fast that has grown: its dividend history from the quote service
  (dividend_history, refreshed daily; only the ticker is sent). The next ex-dividend date is estimated from the usual
  gap between past ones; the payment usually follows by the lag seen in your own payments (two weeks if unknown).
- APY of a savings account or CD: typed in on the page, or estimated from the last three months of interest.

Taxes. Only taxable accounts count toward the tax numbers (not IRAs, 401(k)s, HSAs or 529s). Brokerages label most
dividends qualified or non-qualified; an unlabeled one is estimated (bond and money market funds ordinary, the rest
qualified) until the 1099-DIV arrives. All interest is ordinary income. `tax_summary` is the stable shape the tax
page reads.
"""

import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import household, kinds, portfolio, quotes
from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import (
    Account,
    AccountTerms,
    Balance,
    Category,
    DividendHistory,
    InvestmentTransaction,
    Preference,
    Transaction,
    now,
)

log = logging.getLogger("financeapp")

DIVIDEND = {"dividend", "qualified dividend", "non-qualified dividend"}
REINVESTED_DIVIDEND = {"dividend reinvestment"}
CAPITAL_GAIN = {"long-term capital gain", "short-term capital gain"}
INTEREST = {"interest"}
INTEREST_CATEGORIES = {"INCOME_INTEREST_EARNED", "INCOME_DIVIDENDS"}
INTEREST_WORDS = re.compile(r"interest|dividend", re.I)  # credit unions call savings interest a dividend
TAX_ADVANTAGED = re.compile(
    r"ira|401|403|457|roth|hsa|529|retirement|pension|keogh|sep|simple|tsp|education|health|rdsp|resp|rrsp|tfsa",
    re.I,
)
ORDINARY_HINT = re.compile(r"bond|treasury|money market|income|cash|reit|realty|preferred|mortgage", re.I)
HISTORY_YEARS = 10
HISTORY_EVERY = timedelta(hours=20)
MAX_HISTORY_SYMBOLS = 150
DEFAULT_PAY_LAG = 14
GROWTH_CAP = (-0.10, 0.15)  # a growth rate outside this is a special dividend or a cut, not a trend


@dataclass
class Payment:
    date: date
    account_id: int
    account: str
    symbol: str | None
    name: str
    cents: int
    kind: str  # dividend | capital_gain | interest
    source: str  # brokerage | bank
    tax: str  # qualified | ordinary | unlabeled
    taxable: bool


@dataclass
class Holding:
    symbol: str
    name: str
    asset_class: str
    shares: Decimal
    price: Decimal
    value_cents: int
    cost_basis_cents: int | None
    accounts: list[str] = field(default_factory=list)
    account_shares: dict[str, Decimal] = field(default_factory=dict)  # account name -> shares held there
    account_values: dict[str, int] = field(default_factory=dict)

    def add(self, account: str, shares: Decimal, value_cents: int) -> None:
        self.account_shares[account] = self.account_shares.get(account, Decimal(0)) + shares
        self.account_values[account] = self.account_values.get(account, 0) + value_cents


@dataclass
class CashAccount:
    id: int
    name: str
    subtype: str | None
    balance_cents: int
    apy: float | None = None  # typed in
    maturity: date | None = None
    term_months: int | None = None


def taxable(a: Account) -> bool:
    return not TAX_ADVANTAGED.search(f"{a.subtype or ''} {a.name}")


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


# ---- loading


def payments(db: DbSession, accounts: list[Account], since: date) -> list[Payment]:
    names = {a.id: household.display_name(a) for a in accounts}
    by_id = {a.id: a for a in accounts}
    out: list[Payment] = []

    # Brokerage activity. Reinvestment buys count only where no cash dividend for the same holding came that month.
    rows = list(
        db.scalars(
            select(InvestmentTransaction).where(
                InvestmentTransaction.account_id.in_(list(by_id)), InvestmentTransaction.date >= since
            )
        )
    )
    cash_months = {
        (t.account_id, t.symbol, _month(t.date)) for t in rows if t.subtype in DIVIDEND and t.amount_cents > 0
    }
    for t in rows:
        sub = t.subtype.lower()
        a = by_id[t.account_id]
        if sub in DIVIDEND and t.amount_cents > 0:
            kind = "dividend"
            tax = {"qualified dividend": "qualified", "non-qualified dividend": "ordinary"}.get(sub, "unlabeled")
        elif sub in REINVESTED_DIVIDEND and (t.account_id, t.symbol, _month(t.date)) not in cash_months:
            kind, tax = "dividend", "unlabeled"
        elif sub in CAPITAL_GAIN and t.amount_cents > 0:
            kind, tax = "capital_gain", "unlabeled"
        elif sub in INTEREST and t.amount_cents > 0:
            kind, tax = "interest", "ordinary"
        else:
            continue
        out.append(
            Payment(
                t.date, t.account_id, names[t.account_id], t.symbol, t.name, abs(t.amount_cents), kind,
                "brokerage", tax, taxable(a),
            )
        )  # fmt: skip

    # Bank interest.
    banks = [a.id for a in accounts if a.type == "depository"]
    if banks:
        category = db.scalar(select(Category.id).where(Category.name == "Interest & dividends"))
        txns = db.scalars(
            select(Transaction).where(
                Transaction.account_id.in_(banks),
                Transaction.date >= since,
                Transaction.amount_cents > 0,
                Transaction.pending.is_(False),
                Transaction.transfer_group_id.is_(None),
            )
        )
        for t in txns:
            if not (
                (category is not None and t.category_id == category)
                or (t.provider_category or "") in INTEREST_CATEGORIES
                or (t.category_source != "user" and INTEREST_WORDS.search(t.merchant + " " + t.description))
            ):
                continue
            a = by_id[t.account_id]
            out.append(
                Payment(t.date, a.id, names[a.id], None, t.merchant, t.amount_cents, "interest", "bank", "ordinary",
                        taxable(a))
            )  # fmt: skip
    out.sort(key=lambda p: p.date)
    return out


def reinvesting(db: DbSession, accounts: list[Account], since: date) -> set[str]:
    """Symbols with dividend reinvestment buys since `since`: their dividends are being reinvested (DRIP)."""
    return set(
        db.scalars(
            select(InvestmentTransaction.symbol).where(
                InvestmentTransaction.account_id.in_([a.id for a in accounts]),
                InvestmentTransaction.subtype.in_(REINVESTED_DIVIDEND),
                InvestmentTransaction.date >= since,
                InvestmentTransaction.symbol.is_not(None),
            )
        )
    )


def holdings(db: DbSession, accounts: list[Account], cash: set[str] | None = None) -> list[Holding]:
    """Holdings combined across accounts (cash and money market positions left out; their symbols go in `cash`)."""
    names = {a.id: household.display_name(a) for a in accounts}
    combined: dict[str, Holding] = {}
    for p in portfolio.positions(db, accounts):
        if p.asset_class == "Cash" and cash is not None:
            cash.add(p.symbol)
        if p.asset_class == "Cash" or p.quantity <= 0:
            continue
        h = combined.get(p.symbol)
        if h is None:
            combined[p.symbol] = h = Holding(
                p.symbol, p.name, p.asset_class, p.quantity, p.price, p.value_cents, p.cost_basis_cents,
                [names.get(p.account_id, "")],
            )  # fmt: skip
            h.add(names.get(p.account_id, ""), p.quantity, p.value_cents)
            continue
        h.add(names.get(p.account_id, ""), p.quantity, p.value_cents)
        h.shares += p.quantity
        h.value_cents += p.value_cents
        h.cost_basis_cents = (
            h.cost_basis_cents + p.cost_basis_cents
            if h.cost_basis_cents is not None and p.cost_basis_cents is not None
            else None
        )
        h.accounts.append(names.get(p.account_id, ""))
    return sorted(combined.values(), key=lambda h: -h.value_cents)


def history(db: DbSession, symbols: list[str]) -> dict[str, list[tuple[date, Decimal]]]:
    out: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    if symbols:
        for r in db.scalars(
            select(DividendHistory).where(DividendHistory.symbol.in_(symbols)).order_by(DividendHistory.ex_date)
        ):
            out[r.symbol].append((r.ex_date, r.amount))
    return out


def cash_accounts(db: DbSession, accounts: list[Account]) -> list[CashAccount]:
    """Bank accounts with their rate and CD terms (account_terms, typed on the account or here). The APY the bank
    sends counts like a typed one when nothing is typed."""
    ids = [a.id for a in accounts if a.type == "depository"]
    terms = {t.account_id: t for t in db.scalars(select(AccountTerms).where(AccountTerms.account_id.in_(ids)))}
    out = []
    for a in accounts:
        if a.type != "depository":
            continue
        b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        t = terms.get(a.id)
        rate = t.apy if t and t.apy is not None else (t.bank_apy if t else None)
        is_cd = kinds.kind_of(a.type, a.subtype, a.name, a.kind) == "cd"
        out.append(
            CashAccount(
                a.id,
                household.display_name(a),
                "cd" if is_cd else (a.subtype or "").lower() or None,
                b.current_cents if b else 0,
                float(rate) if rate is not None else None,
                t.maturity if t else None,
                t.term_months if t else None,
            )
        )
    return out


# ---- dividend history from the quote service


def yahoo_dividends(client: httpx.Client, symbol: str) -> list[tuple[date, Decimal]] | None:
    r = client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.replace('.', '-')}",
        params={"range": f"{HISTORY_YEARS}y", "interval": "1mo", "events": "div"},
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result") or []
    if not result:
        return None
    if ((result[0].get("meta") or {}).get("currency") or "USD") != "USD":
        return None
    out = []
    for d in ((result[0].get("events") or {}).get("dividends") or {}).values():
        amount = quotes._decimal(d.get("amount"))
        if isinstance(d.get("date"), int) and amount is not None:
            out.append((datetime.fromtimestamp(d["date"], UTC).astimezone(quotes.ET).date(), amount))
    return sorted(out)


def refresh_history(database: Database, settings: Settings, client: httpx.Client | None = None) -> int | None:
    """Looks up the dividend history of held symbols not checked in the last day. None if nothing was due."""
    if settings.quotes_provider.lower() == "off":
        return None
    with database.session() as db:
        p = db.get(Preference, "dividend_history") or Preference(key="dividend_history", value={})
        checked = dict(p.value.get("checked") or {})
        cutoff = now() - HISTORY_EVERY
        due = [
            s
            for s in sorted(quotes.holding_symbols(db))
            if s not in checked or datetime.fromisoformat(checked[s]) < cutoff
        ][:MAX_HISTORY_SYMBOLS]
    if not due:
        return None
    own = client is None
    client = client or httpx.Client(timeout=10, headers={"User-Agent": "FinanceApp (self-hosted)"})
    found: dict[str, list[tuple[date, Decimal]]] = {}
    try:
        for s in due:
            try:
                found[s] = yahoo_dividends(client, s) or []
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    log.info("yahoo rate limit reached; the rest of the dividend histories wait for the next round")
                    break
            except (httpx.HTTPError, ValueError):
                continue
    finally:
        if own:
            client.close()
    at = now().isoformat()
    with database.session() as db:
        for s, rows in found.items():
            for ex, amount in rows:
                stmt = insert(DividendHistory).values(symbol=s, ex_date=ex, amount=amount)
                db.execute(stmt.on_conflict_do_update(index_elements=["symbol", "ex_date"], set_={"amount": amount}))
            checked[s] = at
        p = db.get(Preference, "dividend_history") or Preference(key="dividend_history", value={})
        p.value = {"checked": checked}
        db.merge(p)
    return len(found)


# ---- the numbers


def frequency(ex_dates: list[date], today: date) -> int:
    """Payments a year: 12, 4, 2 or 1 from the last year's ex-dates (0 when it stopped paying)."""
    recent = [d for d in ex_dates if d > today - timedelta(days=380)]
    n = len(recent)
    if n == 0:
        return 0
    return min((12, 4, 2, 1), key=lambda f: abs(f - n))


def growth_rate(rows: list[tuple[date, Decimal]], today: date) -> float | None:
    """Yearly growth of dividends per share over up to five full calendar years, or None without enough history."""
    by_year: dict[int, Decimal] = defaultdict(Decimal)
    for d, amount in rows:
        by_year[d.year] += amount
    last = today.year - 1
    if by_year.get(last, 0) <= 0:
        return None
    for span in (5, 4, 3):
        first = by_year.get(last - span, Decimal(0))
        # A year before the history starts doesn't count: the first year is often partial.
        if first > 0 and min(by_year) < last - span:
            rate = float((by_year[last] / first) ** (Decimal(1) / span)) - 1
            return round(max(GROWTH_CAP[0], min(GROWTH_CAP[1], rate)), 4)
    return None


def pay_lag(rows: list[tuple[date, Decimal]], paid: list[date]) -> int:
    """Days from ex-dividend date to the payment landing in your account, from your own history."""
    lags = []
    for p in paid:
        before = [d for d, _ in rows if d <= p and (p - d).days <= 60]
        if before:
            lags.append((p - before[-1]).days)
    return int(statistics.median(lags)) if lags else DEFAULT_PAY_LAG


def dividend_holdings(
    hs: list[Holding], hist: dict[str, list[tuple[date, Decimal]]], pays: list[Payment], drip: set[str], today: date
) -> list[dict]:
    year_ago = today - timedelta(days=365)
    received: dict[str, int] = defaultdict(int)
    paid_on: dict[str, list[date]] = defaultdict(list)
    for p in pays:
        if p.kind == "dividend" and p.symbol:
            paid_on[p.symbol].append(p.date)
            if p.date > year_ago:
                received[p.symbol] += p.cents
    out = []
    for h in hs:
        rows = hist.get(h.symbol, [])
        ex_dates = [d for d, _ in rows]
        freq = frequency(ex_dates, today)
        ttm = sum((a for d, a in rows if d > year_ago), Decimal(0))
        source = "history"
        if not rows and received.get(h.symbol) and h.shares > 0:
            # No history from the quote service: what you were paid over the shares held now.
            ttm = Decimal(received[h.symbol]) / 100 / h.shares
            freq = frequency(paid_on[h.symbol], today) or 4
            source = "payments"
        if ttm <= 0 and not received.get(h.symbol):
            continue
        annual_cents = int(ttm * h.shares * 100)
        last_amount = rows[-1][1] if rows else (ttm / freq if freq else Decimal(0))
        next_ex = None
        if freq and rows:
            gap = (
                statistics.median([(b - a).days for a, b in zip(ex_dates[-5:], ex_dates[-4:], strict=False)])
                if len(ex_dates) > 1
                else 365 / freq
            )
            nxt = ex_dates[-1]
            while nxt <= today:
                nxt += timedelta(days=round(gap))
            next_ex = nxt
        lag = pay_lag(rows, paid_on[h.symbol])
        price = float(h.price)
        held = h.account_shares or {(h.accounts[0] if h.accounts else ""): h.shares}
        values = h.account_values or {next(iter(held)): h.value_cents}
        out.append(
            {
                "symbol": h.symbol,
                "name": h.name,
                "asset_class": h.asset_class,
                "accounts": h.accounts,
                "shares": float(h.shares),
                "price": price,
                "value_cents": h.value_cents,
                "cost_basis_cents": h.cost_basis_cents,
                "received_12m_cents": received.get(h.symbol, 0),
                "annual_per_share": round(float(ttm), 4),
                "annual_cents": annual_cents,
                "yield_percent": round(float(ttm) / price * 100, 2) if price else None,
                "yield_on_cost_percent": (
                    round(annual_cents / h.cost_basis_cents * 100, 2) if h.cost_basis_cents else None
                ),
                "frequency": freq,
                "last_ex_date": ex_dates[-1].isoformat() if ex_dates else None,
                "last_amount": round(float(last_amount), 4),
                "next_ex_date": next_ex.isoformat() if next_ex else None,
                "next_pay_date": (next_ex + timedelta(days=lag)).isoformat() if next_ex else None,
                "next_cents": int(last_amount * h.shares * 100) if next_ex else None,
                "growth_5y": growth_rate(rows, today),
                "reinvesting": h.symbol in drip,
                "from": source,
                "by_account": [
                    {
                        "account": name,
                        "shares": float(n),
                        "value_cents": values.get(name, 0),
                        "annual_cents": int(ttm * n * 100),
                    }
                    for name, n in sorted(held.items(), key=lambda kv: -kv[1])
                ],
            }
        )
    return sorted(out, key=lambda r: -r["annual_cents"])


def expected_months(rows: list[dict], today: date, months: int = 12) -> tuple[list[dict], list[dict]]:
    """The next `months` of expected dividend payments, each holding repeating at its usual frequency."""
    start = date(today.year, today.month, 1)
    end = _add_months(start, months)
    by_month: dict[str, int] = defaultdict(int)
    upcoming = []
    for r in rows:
        if not r["next_pay_date"] or not r["frequency"]:
            continue
        step = timedelta(days=round(365 / r["frequency"]))
        pay = date.fromisoformat(r["next_pay_date"])
        ex = date.fromisoformat(r["next_ex_date"])
        while pay < end:
            by_month[_month(pay)] += r["next_cents"]
            upcoming.append(
                {"symbol": r["symbol"], "name": r["name"], "ex_date": ex.isoformat(), "pay_date": pay.isoformat(),
                 "cents": r["next_cents"], "reinvesting": r["reinvesting"]}
            )  # fmt: skip
            pay += step
            ex += step
    upcoming.sort(key=lambda u: u["pay_date"])
    return [
        {"month": _month(_add_months(start, i)), "cents": by_month.get(_month(_add_months(start, i)), 0)}
        for i in range(months)
    ], upcoming[:24]


def by_month(pays: list[Payment], kind: set[str], today: date, months: int = 24) -> list[dict]:
    start = _add_months(date(today.year, today.month, 1), -(months - 1))
    totals: dict[str, int] = defaultdict(int)
    for p in pays:
        if p.kind in kind and p.date >= start:
            totals[_month(p.date)] += p.cents
    return [
        {"month": _month(_add_months(start, i)), "cents": totals.get(_month(_add_months(start, i)), 0)}
        for i in range(months)
    ]


def by_year(pays: list[Payment], kind: set[str]) -> list[dict]:
    totals: dict[int, int] = defaultdict(int)
    for p in pays:
        if p.kind in kind:
            totals[p.date.year] += p.cents
    return [{"year": y, "cents": totals[y]} for y in sorted(totals)]


def estimated_tax(p: Payment, classes: dict[str, str]) -> str:
    if p.tax != "unlabeled":
        return p.tax
    if p.kind == "capital_gain":
        return "capital_gain"
    hint = f"{p.name} {classes.get(p.symbol or '', '')}"
    return "ordinary" if ORDINARY_HINT.search(hint) or classes.get(p.symbol or "") in ("Bonds", "Cash") else "qualified"


def tax_summary(pays: list[Payment], year: int, today: date, classes: dict[str, str], since: date) -> dict:
    """The year's taxable dividends and interest (the shape the tax page reads; keep it stable)."""
    mine = [p for p in pays if p.date.year == year]
    taxed = [p for p in mine if p.taxable]
    div = [p for p in taxed if p.kind == "dividend"]

    def total(ps: list[Payment]) -> int:
        return sum(p.cents for p in ps)

    return {
        "year": year,
        "through": min(today, date(year, 12, 31)).isoformat(),
        "complete": today > date(year, 12, 31),
        "history_from": since.isoformat(),
        "covers_whole_year": since <= date(year, 1, 1),
        "dividends": {
            "total_cents": total(div),
            "qualified_cents": total([p for p in div if p.tax == "qualified"]),
            "ordinary_cents": total([p for p in div if p.tax == "ordinary"]),
            "unlabeled_cents": total([p for p in div if p.tax == "unlabeled"]),
            # Unlabeled ones sorted by what the holding is (bond and money market funds ordinary, the rest qualified).
            "estimated_qualified_cents": total([p for p in div if estimated_tax(p, classes) == "qualified"]),
            "estimated_ordinary_cents": total([p for p in div if estimated_tax(p, classes) == "ordinary"]),
            "capital_gain_distributions_cents": total([p for p in taxed if p.kind == "capital_gain"]),
        },
        "interest": {
            "total_cents": total([p for p in taxed if p.kind == "interest"]),
            "bank_cents": total([p for p in taxed if p.kind == "interest" and p.source == "bank"]),
            "brokerage_cents": total([p for p in taxed if p.kind == "interest" and p.source == "brokerage"]),
        },
        "tax_advantaged_cents": total([p for p in mine if not p.taxable]),
        "estimated": any(p.tax == "unlabeled" for p in div),
    }


def interest_accounts(cash: list[CashAccount], pays: list[Payment], today: date) -> list[dict]:
    out = []
    window = today - timedelta(days=95)
    for a in cash:
        mine = [p for p in pays if p.account_id == a.id and p.kind == "interest"]
        last12 = sum(p.cents for p in mine if p.date > today - timedelta(days=365))
        recent = [p for p in mine if p.date > window]
        implied = None
        if recent and a.balance_cents > 0:
            months = len({_month(p.date) for p in recent})
            monthly = sum(p.cents for p in recent) / months
            implied = round(((1 + monthly / a.balance_cents) ** 12 - 1) * 100, 2)
            if implied > 25:
                implied = None  # a one-off bonus, not a rate
        is_cd = (a.subtype or "") == "cd"
        # Checking accounts with no interest aren't worth a row; savings, money market and CDs always are.
        if not mine and a.apy is None and a.subtype not in ("savings", "money market", "cd", "hsa"):
            continue
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "kind": "cd" if is_cd else (a.subtype or "savings"),
                "balance_cents": a.balance_cents,
                "interest_12m_cents": last12,
                "apy": a.apy,
                "implied_apy": implied,
                "maturity": a.maturity.isoformat() if a.maturity else None,
                "term_months": a.term_months,
                "last_paid": mine[-1].date.isoformat() if mine else None,
                "months": by_month(mine, {"interest"}, today, 12),
            }
        )
    return sorted(out, key=lambda r: -r["balance_cents"])


def cash_yearly(pays: list[Payment], today: date) -> int:
    """What a brokerage's cash pays in a year at the current pace: the monthly average of the last three months when
    it pays monthly, else the last year's total; nothing when it hasn't paid in four months (the cash moved)."""
    if not pays or pays[-1].date <= today - timedelta(days=120):
        return 0
    recent = [p for p in pays if p.date > today - timedelta(days=95)]
    months = len({_month(p.date) for p in recent})
    if months >= 2:
        return round(sum(p.cents for p in recent) / months * 12)
    return sum(p.cents for p in pays if p.date > today - timedelta(days=365))


def estimate(rows: list[dict], accounts: list[dict], pays: list[Payment], cash_symbols: set[str], today: date) -> dict:
    """A year of dividends and interest at today's holdings, balances and rates, in all and by account, plus each
    expected dividend payment of the next 12 months by account (so the page can total the next 1, 2, 3 or 6 months).

    Dividends: each holding's last 12 months of dividends per share times the shares held now in each account.
    Brokerage cash (sweep interest, money market fund dividends): its current pace. Bank accounts: balance times APY
    (typed in, or what the last three months of interest work out to)."""
    by: dict[str, dict] = {}

    def row(name: str, kind: str) -> dict:
        return by.setdefault(
            name,
            {"account": name, "kind": kind, "value_cents": 0, "dividends_annual_cents": 0, "interest_annual_cents": 0,
             "apy": None, "rate_from": None, "payers": []},
        )  # fmt: skip

    for r in rows:
        for a in r["by_account"]:
            if a["annual_cents"] <= 0:
                continue
            acct = row(a["account"], "investment")
            acct["dividends_annual_cents"] += a["annual_cents"]
            acct["value_cents"] += a["value_cents"]
            acct["payers"].append({"symbol": r["symbol"], "annual_cents": a["annual_cents"]})

    cash_pays: dict[tuple[str, str | None], list[Payment]] = defaultdict(list)  # each account's cash, each fund
    for p in pays:
        if p.source == "brokerage" and (p.kind == "interest" or (p.kind == "dividend" and p.symbol in cash_symbols)):
            cash_pays[(p.account, p.symbol)].append(p)
    for (name, _), ps in cash_pays.items():
        yearly = cash_yearly(ps, today)
        if yearly > 0:
            row(name, "investment")["interest_annual_cents"] += yearly

    for a in accounts:
        apy = a["apy"] if a["apy"] is not None else a["implied_apy"]
        if apy is None or a["balance_cents"] <= 0:
            continue
        acct = row(a["name"], "bank")
        acct["kind"] = "cd" if a["kind"] == "cd" else "bank"
        acct["value_cents"] = a["balance_cents"]
        acct["interest_annual_cents"] += round(a["balance_cents"] * apy / 100)
        acct["apy"] = apy
        acct["rate_from"] = "typed" if a["apy"] is not None else "recent"

    out = []
    for acct in by.values():
        acct["annual_cents"] = acct["dividends_annual_cents"] + acct["interest_annual_cents"]
        if acct["annual_cents"] <= 0:
            continue
        acct["payers"] = sorted(acct["payers"], key=lambda x: -x["annual_cents"])[:5]
        acct["dividend_yield_percent"] = (
            round(acct["dividends_annual_cents"] / acct["value_cents"] * 100, 2)
            if acct["kind"] == "investment" and acct["value_cents"] and acct["dividends_annual_cents"]
            else None
        )
        out.append(acct)
    out.sort(key=lambda x: -x["annual_cents"])

    end = _add_months(date(today.year, today.month, 1), 13)
    schedule = []
    for r in rows:
        if not r["next_pay_date"] or not r["frequency"] or not r["next_cents"]:
            continue
        total = sum(a["shares"] for a in r["by_account"]) or 1
        step = timedelta(days=round(365 / r["frequency"]))
        pay = date.fromisoformat(r["next_pay_date"])
        while pay < end:
            for a in r["by_account"]:
                cents = round(r["next_cents"] * a["shares"] / total)
                if cents > 0:
                    schedule.append({"account": a["account"], "symbol": r["symbol"], "pay_date": pay.isoformat(),
                                     "cents": cents})  # fmt: skip
            pay += step
    schedule.sort(key=lambda x: x["pay_date"])
    dividends = sum(a["dividends_annual_cents"] for a in out)
    interest = sum(a["interest_annual_cents"] for a in out)
    return {
        "annual_cents": dividends + interest,
        "dividends_annual_cents": dividends,
        "interest_annual_cents": interest,
        "accounts": out,
        "payments": schedule,
    }


def build_from(
    pays: list[Payment],
    hs: list[Holding],
    hist: dict[str, list[tuple[date, Decimal]]],
    drip: set[str],
    cash: list[CashAccount],
    since: date,
    today: date,
    cash_symbols: set[str] | None = None,
) -> dict:
    year_ago = today - timedelta(days=365)
    rows = dividend_holdings(hs, hist, pays, drip, today)
    months_ahead, upcoming = expected_months(rows, today)
    received = [p for p in pays if p.kind == "dividend"]
    ttm = sum(p.cents for p in received if p.date > year_ago)
    prior = sum(p.cents for p in received if year_ago - timedelta(days=365) < p.date <= year_ago)
    forward = sum(r["annual_cents"] for r in rows)
    invested = sum(h.value_cents for h in hs)
    basis = [h.cost_basis_cents for h in hs if any(r["symbol"] == h.symbol for r in rows)]
    payers = sorted(rows, key=lambda r: -r["annual_cents"])
    classes = {h.symbol: h.asset_class for h in hs}
    brokerage_interest = [p for p in pays if p.kind == "interest" and p.source == "brokerage"]
    interest_pays = [p for p in pays if p.kind == "interest"]
    accounts = interest_accounts(cash, pays, today)
    ytd_start = date(today.year, 1, 1)
    same_day_last_year = today - timedelta(days=366 if today.month > 2 and today.year % 4 == 0 else 365)
    last_year_same = (date(today.year - 1, 1, 1), same_day_last_year)
    return {
        "as_of": today.isoformat(),
        "history_from": since.isoformat(),
        "dividends": {
            "received_12m_cents": ttm,
            "received_prior_12m_cents": prior if since <= year_ago - timedelta(days=330) else None,
            "this_year_cents": sum(p.cents for p in received if p.date >= ytd_start),
            "last_year_same_period_cents": (
                sum(p.cents for p in received if last_year_same[0] <= p.date <= last_year_same[1])
                if since <= last_year_same[0]
                else None
            ),
            "forward_annual_cents": forward,
            "yield_percent": round(forward / invested * 100, 2) if invested else None,
            "yield_on_cost_percent": (
                round(forward / sum(basis) * 100, 2) if basis and all(b for b in basis) else None  # type: ignore[arg-type]
            ),
            "capital_gains_12m_cents": sum(p.cents for p in pays if p.kind == "capital_gain" and p.date > year_ago),
            "by_month": by_month(pays, {"dividend"}, today, 24),
            "by_year": by_year(pays, {"dividend"}),
            "expected_months": months_ahead,
            "upcoming": upcoming,
            "holdings": rows,
            "top_share_percent": round(payers[0]["annual_cents"] / forward * 100, 1) if forward else None,
            "top3_share_percent": round(sum(r["annual_cents"] for r in payers[:3]) / forward * 100, 1)
            if forward
            else None,
            "invested_cents": invested,
            "non_payers_cents": invested - sum(r["value_cents"] for r in rows),
            "reinvesting": sorted({r["symbol"] for r in rows if r["reinvesting"]}),
            "recent": [
                {
                    "date": p.date.isoformat(),
                    "symbol": p.symbol,
                    "name": p.name,
                    "account": p.account,
                    "cents": p.cents,
                    "tax": p.tax,
                }
                for p in reversed(received[-12:])
            ],
        },
        "interest": {
            "received_12m_cents": sum(p.cents for p in interest_pays if p.date > year_ago),
            "this_year_cents": sum(p.cents for p in interest_pays if p.date >= ytd_start),
            "brokerage_12m_cents": sum(p.cents for p in brokerage_interest if p.date > year_ago),
            "by_month": by_month(pays, {"interest"}, today, 24),
            "by_year": by_year(pays, {"interest"}),
            "accounts": accounts,
        },
        "tax": [tax_summary(pays, y, today, classes, since) for y in (today.year, today.year - 1)],
        "estimate": estimate(rows, accounts, pays, cash_symbols or set(), today),
    }


def since_for(today: date) -> date:
    return today - timedelta(days=731)


def history_start(db: DbSession, accounts: list[Account], today: date) -> date:
    """The earliest day the brokerage activity and bank history reach (Plaid keeps two years), so totals can say
    what they cover. The later of the two: a total is only complete where both have history."""
    firsts = []
    for model, types in ((InvestmentTransaction, ("investment",)), (Transaction, ("depository",))):
        ids = [a.id for a in accounts if a.type in types]
        if ids:
            first = db.scalar(select(model.date).where(model.account_id.in_(ids)).order_by(model.date).limit(1))
            if first is not None:
                firsts.append(first)
    return max(max(firsts) if firsts else today, since_for(today))


def build(db: DbSession, today: date) -> dict:
    accounts = household.counted(db)
    since = since_for(today)
    pays = payments(db, accounts, since)
    cash_symbols: set[str] = set()
    hs = holdings(db, accounts, cash_symbols)
    hist = history(db, [h.symbol for h in hs])
    drip = reinvesting(db, accounts, today - timedelta(days=400))
    return build_from(
        pays, hs, hist, drip, cash_accounts(db, accounts), history_start(db, accounts, today), today, cash_symbols
    )


def tax_year(db: DbSession, year: int, today: date) -> dict:
    accounts = household.counted(db)
    since = since_for(today)
    pays = payments(db, accounts, min(since, date(year, 1, 1)))
    classes = {h.symbol: h.asset_class for h in holdings(db, accounts)}
    return tax_summary(pays, year, today, classes, history_start(db, accounts, today))
