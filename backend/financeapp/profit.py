"""Profit: how much the portfolio has grown over what was paid for it, from the tax lots (lots.py).

- Not sold yet (unrealized): every open lot's value today less what it cost, split by term (short: held a year or
  less, long: more, unknown: shares from before the activity on file whose buy date isn't known yet). Lots whose
  cost isn't on file are left out of the gain, and their value is said apart so the percent stays honest.
- Sold (realized): every sale in the activity on file, matched first in, first out to the lots it closed. Shares sold
  from before the history started have no cost on file, so their part of the proceeds is said apart too.

Everything is reported per account and per holding so the page can show any mix of accounts without asking again.
Gains in retirement and health accounts are growth all the same, but nothing there is taxed on a sale.
"""

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import costs, household, income, lots
from financeapp.models import Account, InvestmentTransaction
from financeapp.portfolio import Position

SALES_SHOWN = 60  # the most recent sales listed; the totals count them all


def _cents(d: Decimal) -> int:
    return int(d.to_integral_value())


MONEY_MARKET = re.compile(r"money market|government cash|treasury cash|cash reserves|sweep", re.I)


def cash_like(symbol: str, txns: list[InvestmentTransaction], cash: set[str]) -> bool:
    """A money market or sweep fund (SPAXX, VMFXX): held as cash, named like one, or always traded at $1 a share.
    Its "sales" are cash moving out to pay for something else, never a gain."""
    if symbol in cash or symbol.endswith("**") or any(MONEY_MARKET.search(t.name or "") for t in txns):
        return True
    prices = [t.price for t in txns if t.price and t.type.lower() in ("buy", "sell")]
    return bool(prices) and all(Decimal("0.995") <= p <= Decimal("1.005") for p in prices)


def realized(db: DbSession, accounts: list[Account], cash: set[str] | None = None) -> list[dict]:
    """Every sale on file in the investment accounts, newest first, with its gain by term where the cost is known.
    Money market and sweep funds are left out (`cash`: symbols held as cash)."""
    ids = {a.id for a in accounts if a.type == "investment"}
    if not ids:
        return []
    by_id = {a.id: a for a in accounts}
    activity: dict[tuple[int, str], list[InvestmentTransaction]] = {}
    for t in db.scalars(
        select(InvestmentTransaction).where(
            InvestmentTransaction.account_id.in_(ids), InvestmentTransaction.symbol.is_not(None)
        )
    ):
        activity.setdefault((t.account_id, t.symbol or ""), []).append(t)
    out = []
    for (account_id, symbol), txns in activity.items():
        if cash_like(symbol, txns, cash or set()):
            continue
        found: list[lots.Sale] = []
        lots.replay(txns, found)
        account = by_id[account_id]
        names = {t.name for t in txns if t.name}
        for s in found:
            out.append(sale_row(s, symbol, min(names) if names else symbol, account))
    out.sort(key=lambda r: (r["date"], r["symbol"]), reverse=True)
    return out


def sale_row(s: lots.Sale, symbol: str, name: str, account: Account) -> dict:
    """One sale: its proceeds split over the lots it closed, and the gain on each by its term."""
    gains = {"short": 0, "long": 0}
    cost = 0
    known = Decimal(0)
    for piece in s.pieces:
        if piece.acquired is None or piece.cost_cents is None:
            continue
        share = _cents(Decimal(s.proceeds_cents) * piece.quantity / s.quantity)
        term = "long" if s.sold >= lots.long_term_on(piece.acquired) else "short"
        gains[term] += share - piece.cost_cents
        cost += piece.cost_cents
        known += piece.quantity
    known_proceeds = _cents(Decimal(s.proceeds_cents) * known / s.quantity) if s.quantity else 0
    return {
        "date": s.sold.isoformat(),
        "symbol": symbol,
        "name": name,
        "account_id": account.id,
        "taxable": income.taxable(account),
        "quantity": float(s.quantity),
        "proceeds_cents": s.proceeds_cents,
        "cost_cents": cost if known > 0 else None,
        "gain_cents": gains["short"] + gains["long"] if known > 0 else None,
        "short_gain_cents": gains["short"],
        "long_gain_cents": gains["long"],
        "uncosted_proceeds_cents": s.proceeds_cents - known_proceeds,  # shares whose cost isn't on file
    }


def holdings(rows: list[dict], names: dict[tuple[int, str], str], typed: set[str] | None = None) -> list[dict]:
    """The lots added up per holding per account: value, cost and gain by term. `names`: (account id, symbol).
    `typed`: holdings (costs.key) whose cost was typed in."""
    out: dict[tuple[int, str], dict] = {}
    for r in rows:
        key = (r["account_id"], r["symbol"])
        h = out.setdefault(
            key,
            {
                "symbol": r["symbol"],
                "name": names.get(key, r["symbol"]),
                "account_id": r["account_id"],
                "value_cents": 0,
                "cost_cents": 0,
                "short_gain_cents": 0,
                "long_gain_cents": 0,
                "unknown_gain_cents": 0,
                "uncosted_value_cents": 0,
                "quantity": 0.0,
                "lots": 0,
                "typed": costs.key(*key) in (typed or set()),
            },
        )
        h["value_cents"] += r["value_cents"]
        h["quantity"] += r["quantity"]
        h["lots"] += 1
        if r["cost_cents"] is None:
            h["uncosted_value_cents"] += r["value_cents"]
            continue
        h["cost_cents"] += r["cost_cents"]
        h[f"{r['term'] or 'unknown'}_gain_cents"] += r["gain_cents"]
    return sorted(out.values(), key=lambda h: -h["value_cents"])


def report(holding_rows: list[dict], sales: list[dict], accounts: list[dict], today: date) -> dict:
    """The page: accounts (id, name, taxable, history_from), holdings and sales; the page adds up the mix it shows."""
    held = {h["account_id"] for h in holding_rows} | {s["account_id"] for s in sales}
    years = sorted({int(s["date"][:4]) for s in sales} | {today.year}, reverse=True)
    return {
        "as_of": today.isoformat(),
        "accounts": [a for a in accounts if a["id"] in held],
        "holdings": holding_rows,
        "sales": sales[:SALES_SHOWN],
        "sales_by_year": [
            {
                "year": y,
                "by_account": [
                    _sum_sales([s for s in sales if s["account_id"] == aid and s["date"].startswith(str(y))], aid)
                    for aid in sorted(held)
                ],
            }
            for y in years[:3]
        ],
    }


def _sum_sales(sales: list[dict], account_id: int) -> dict:
    return {
        "account_id": account_id,
        "count": len(sales),
        "proceeds_cents": sum(s["proceeds_cents"] for s in sales),
        "short_gain_cents": sum(s["short_gain_cents"] for s in sales),
        "long_gain_cents": sum(s["long_gain_cents"] for s in sales),
        "uncosted_proceeds_cents": sum(s["uncosted_proceeds_cents"] for s in sales),
    }


def of(db: DbSession, accounts: list[Account], positions: list[Position], today: date | None = None) -> dict:
    today = today or date.today()
    invest = [a for a in accounts if a.type == "investment"]
    meta = []
    for a in invest:
        start = lots.history_start(db, a)
        meta.append(
            {
                "id": a.id,
                "name": household.display_name(a),
                "taxable": income.taxable(a),
                "history_from": start.isoformat() if start else None,
            }
        )
    positions, typed = costs.fill(db, positions)
    rows = lots.of(db, accounts, positions)
    names = {(p.account_id, p.symbol): p.name for p in positions}
    cash = {p.symbol for p in positions if p.asset_class == "Cash"}
    return report(holdings(rows, names, typed), realized(db, accounts, cash), meta, today)
