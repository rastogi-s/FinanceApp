"""Tax lots: each holding's shares by the day they were bought, so a sale's gain and its term can be seen up front.

No connector sends lots (Plaid's holdings carry one cost basis per holding), so they're rebuilt from the brokerage's
activity (investment_transactions, up to 24 months back): every buy opens a lot, every sale closes the oldest
first (first in, first out, the brokerages' default), and a split scales the open lots. The result is then squared
with the holding as the brokerage reports it today:

- Shares the activity doesn't explain were there before the history starts: one "earlier" lot, costing whatever is
  left of the brokerage's cost basis. Outside a stock plan they're held since before the history window, so more
  than a year: long-term, marked estimated. Stock plan vests often aren't in the activity, so there the term is
  left unknown.
- More shares in the lots than are held (a sale the activity missed): the oldest lots are trimmed.

A gain held more than one year is long-term: a lot bought on March 3 turns long-term on March 4 a year later.
Lots in retirement and health accounts (401(k), IRA, HSA) are shown too, but nothing there is taxed on a sale.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import household, income
from financeapp.models import Account, Connection, InvestmentTransaction
from financeapp.portfolio import Position

HISTORY_DAYS = 730  # how far back Plaid's investment activity goes on the first sync
TINY = Decimal("0.0001")  # shares: rounding left by fractional buys
SPLIT = ("split", "stock split", "reverse split")


@dataclass
class Lot:
    acquired: date | None  # None: before the history starts
    quantity: Decimal
    cost_cents: int | None


def long_term_on(acquired: date) -> date:
    """The first day a sale counts as long-term: the day after the one-year anniversary."""
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:  # February 29
        anniversary = date(acquired.year + 1, 3, 1) - timedelta(days=1)
    return anniversary + timedelta(days=1)


def _take(open_lots: list[Lot], quantity: Decimal) -> Decimal:
    """Closes `quantity` shares from the oldest lots. Returns how many shares no lot covered."""
    left = quantity
    while left > TINY and open_lots:
        lot = open_lots[0]
        if lot.quantity <= left + TINY:
            left -= lot.quantity
            open_lots.pop(0)
            continue
        if lot.cost_cents is not None:
            lot.cost_cents -= int((Decimal(lot.cost_cents) * left / lot.quantity).to_integral_value())
        lot.quantity -= left
        left = Decimal(0)
    return max(left, Decimal(0))


def replay(activity: list[InvestmentTransaction]) -> tuple[list[Lot], bool]:
    """Open lots after the activity, oldest first, and whether a sale reached shares from before the history."""
    open_lots: list[Lot] = []
    older = False
    for t in sorted(activity, key=lambda t: (t.date, t.id or 0)):
        q = abs(t.quantity or Decimal(0))
        if q <= TINY:
            continue
        kind, sub = t.type.lower(), t.subtype.lower()
        if sub in SPLIT:
            held = sum((lot.quantity for lot in open_lots), Decimal(0))
            if held > TINY:
                after = held + (t.quantity or Decimal(0))  # Plaid: shares added (or removed by a reverse split)
                for lot in open_lots:
                    lot.quantity = lot.quantity * after / held
            continue
        if kind == "buy" or (kind == "transfer" and (t.quantity or 0) > 0):
            cost = -t.amount_cents if t.amount_cents < 0 else None
            if cost is None and t.price:
                cost = int((q * t.price * 100).to_integral_value())
            open_lots.append(Lot(t.date, q, cost))
        elif kind == "sell" or (kind == "transfer" and (t.quantity or 0) < 0):
            older = _take(open_lots, q) > TINY or older
    return open_lots, older


def history_start(db: DbSession, account: Account) -> date | None:
    """The first day the brokerage's activity covers: two years before the account was first linked."""
    connection = db.get(Connection, account.connection_id)
    if connection is None or connection.provider != "plaid":
        return None
    return connection.created_at.date() - timedelta(days=HISTORY_DAYS)


def reconcile(open_lots: list[Lot], p: Position) -> tuple[list[Lot], bool]:
    """Squares the lots with the holding today. Returns them and whether the oldest were trimmed to fit."""
    lots = [Lot(lot.acquired, lot.quantity, lot.cost_cents) for lot in open_lots]
    held = sum((lot.quantity for lot in lots), Decimal(0))
    trimmed = False
    if held > p.quantity + TINY:
        _take(lots, held - p.quantity)
        trimmed = True
    elif p.quantity - held > TINY:
        known = [lot.cost_cents for lot in lots]
        rest = None
        if p.cost_basis_cents is not None and all(c is not None for c in known):
            rest = p.cost_basis_cents - sum(known)  # type: ignore[arg-type]
            rest = rest if rest > 0 else None
        lots.insert(0, Lot(None, p.quantity - held, rest))
    return lots, trimmed


def of(db: DbSession, accounts: list[Account], positions: list[Position], symbol: str | None = None) -> list[dict]:
    """Every open lot of `symbol` (or of every holding), oldest first within each account."""
    today = date.today()
    by_id = {a.id: a for a in accounts}
    mine = [
        p for p in positions if (symbol is None or p.symbol == symbol) and p.quantity > 0 and p.asset_class != "Cash"
    ]
    if not mine:
        return []
    activity: dict[tuple[int, str], list[InvestmentTransaction]] = {}
    for t in db.scalars(
        select(InvestmentTransaction).where(
            InvestmentTransaction.account_id.in_({p.account_id for p in mine}),
            InvestmentTransaction.symbol.in_({p.symbol for p in mine}),
        )
    ):
        activity.setdefault((t.account_id, t.symbol or ""), []).append(t)

    out = []
    for p in mine:
        account = by_id.get(p.account_id)
        if account is None:
            continue
        opened, _ = replay(activity.get((p.account_id, p.symbol), []))
        lots, trimmed = reconcile(opened, p)
        start = history_start(db, account)
        stock_plan = "stock plan" in (account.subtype or "").lower()
        for lot in lots:
            value = int((lot.quantity * p.price * 100).to_integral_value())
            if lot.acquired is not None:
                turns = long_term_on(lot.acquired)
                term = "long" if today >= turns else "short"
                estimated = trimmed
            else:
                turns = None
                # Shares from before a full two-year history are more than a year old, unless they came from a
                # stock plan, whose vests the activity often leaves out.
                term = "long" if start is not None and not stock_plan and (today - start).days > 366 else None
                estimated = True
            out.append(
                {
                    "symbol": p.symbol,
                    "account_id": p.account_id,
                    "account": household.display_name(account),
                    "taxable": income.taxable(account),
                    "acquired": lot.acquired.isoformat() if lot.acquired else None,
                    "acquired_before": start.isoformat() if lot.acquired is None and start else None,
                    "quantity": float(lot.quantity),
                    "cost_cents": lot.cost_cents,
                    "value_cents": value,
                    "gain_cents": value - lot.cost_cents if lot.cost_cents is not None else None,
                    "term": term,
                    "long_term_on": turns.isoformat() if turns and term == "short" else None,
                    "estimated": estimated,
                }
            )
    return out


def summary(rows: list[dict], today: date | None = None) -> dict:
    """Gains not yet sold in taxable accounts, by term, and the next lot to turn long-term (with a gain on it)."""
    today = today or date.today()
    taxable = [r for r in rows if r["taxable"] and r["gain_cents"] is not None]
    upcoming = sorted(
        (r for r in taxable if r["term"] == "short" and r["long_term_on"] and r["gain_cents"] > 0),
        key=lambda r: r["long_term_on"],
    )
    nxt = upcoming[0] if upcoming else None
    return {
        "short_gain_cents": sum(r["gain_cents"] for r in taxable if r["term"] == "short"),
        "long_gain_cents": sum(r["gain_cents"] for r in taxable if r["term"] == "long"),
        "unknown_gain_cents": sum(r["gain_cents"] for r in taxable if r["term"] is None),
        "has_taxable": any(r["taxable"] for r in rows),
        "next_long_term": {
            "on": nxt["long_term_on"],
            "days": (date.fromisoformat(nxt["long_term_on"]) - today).days,
            "quantity": nxt["quantity"],
            "gain_cents": nxt["gain_cents"],
        }
        if nxt
        else None,
        "lot_count": len(rows),
    }
