"""Holdings across every account, at live prices, and how concentrated they are.

Holdings come from each investment account's latest snapshot (the sync, every few hours). Between syncs a holding is
revalued at the latest quote (quotes.py) once the quote is newer than the snapshot: quantity x quote price. The
account's balance moves by the same amount, so net worth follows the market during the day too.

Combined positions add up the same symbol across accounts ("VOO: $40,000 in the IRA and $12,000 in the 401(k)"),
as a share of everything invested. Look-through exposure (lookthrough.py) goes one step further and opens up funds.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import quotes
from financeapp.models import Account, HoldingSnapshot


@dataclass
class Position:
    symbol: str
    name: str
    asset_class: str
    account_id: int
    quantity: Decimal
    price: Decimal
    value_cents: int
    synced_value_cents: int
    change_today_percent: float
    cost_basis_cents: int | None
    as_of: date  # the snapshot's day
    price_as_of: datetime | None  # set when a live quote is used


def positions(db: DbSession, accounts: list[Account]) -> list[Position]:
    out: list[Position] = []
    invest = [a.id for a in accounts if a.type == "investment"]
    snapshots: dict[int, tuple[date, list[HoldingSnapshot], dict[str, Decimal]]] = {}
    for aid in invest:
        last = db.scalar(select(func.max(HoldingSnapshot.date)).where(HoldingSnapshot.account_id == aid))
        if last is None:
            continue
        prev = db.scalar(
            select(func.max(HoldingSnapshot.date)).where(HoldingSnapshot.account_id == aid, HoldingSnapshot.date < last)
        )
        prev_prices = (
            {
                h.symbol: h.price
                for h in db.scalars(
                    select(HoldingSnapshot).where(HoldingSnapshot.account_id == aid, HoldingSnapshot.date == prev)
                )
            }
            if prev
            else {}
        )
        rows = list(
            db.scalars(select(HoldingSnapshot).where(HoldingSnapshot.account_id == aid, HoldingSnapshot.date == last))
        )
        snapshots[aid] = (last, rows, prev_prices)
    live = quotes.latest(db, {h.symbol for _, rows, _ in snapshots.values() for h in rows})
    for aid, (last, rows, prev_prices) in snapshots.items():
        for h in rows:
            q = live.get(h.symbol)
            if quotes.quotable(h.symbol, h.asset_class) and quotes.fresh_for(q, last) and h.quantity > 0:
                value = int((h.quantity * q.price * 100).to_integral_value())
                change = float((q.price - q.previous_close) / q.previous_close * 100) if q.previous_close else 0.0
                out.append(
                    Position(
                        h.symbol, h.name, h.asset_class, aid, h.quantity, q.price, value, h.value_cents,
                        round(change, 2), h.cost_basis_cents, last, q.as_of,
                    )
                )  # fmt: skip
                continue
            before = prev_prices.get(h.symbol)
            change = float((h.price - before) / before * 100) if before else 0.0
            out.append(
                Position(
                    h.symbol, h.name, h.asset_class, aid, h.quantity, h.price, h.value_cents, h.value_cents,
                    round(change, 2), h.cost_basis_cents, last, None,
                )
            )  # fmt: skip
    return out


def live_change_by_account(ps: list[Position]) -> dict[int, int]:
    """How far live prices have moved each account since its last sync."""
    delta: dict[int, int] = defaultdict(int)
    for p in ps:
        delta[p.account_id] += p.value_cents - p.synced_value_cents
    return delta


def prices_as_of(ps: list[Position]) -> datetime | None:
    times = [p.price_as_of for p in ps if p.price_as_of]
    return max(times) if times else None


def combined(ps: list[Position], names: dict[int, str]) -> dict:
    """The same symbol added up across accounts, largest first, each as a share of everything invested."""
    total = sum(p.value_cents for p in ps)
    by_symbol: dict[str, dict] = {}
    for p in ps:
        row = by_symbol.setdefault(
            p.symbol,
            {"symbol": p.symbol, "name": p.name, "asset_class": p.asset_class, "value_cents": 0, "accounts": []},
        )
        row["value_cents"] += p.value_cents
        row["accounts"].append({"account": names.get(p.account_id, ""), "value_cents": p.value_cents})
    rows = sorted(by_symbol.values(), key=lambda r: -r["value_cents"])
    for r in rows:
        r["percent"] = round(r["value_cents"] / total * 100, 2) if total else 0.0
        r["accounts"].sort(key=lambda a: -a["value_cents"])
    top5 = sum(r["value_cents"] for r in rows[:5])
    return {
        "total_cents": total,
        "positions": rows,
        "top5_percent": round(top5 / total * 100, 1) if total else 0.0,
        "largest": rows[0]["symbol"] if rows else None,
    }
