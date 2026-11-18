"""What you paid, typed in: a holding's cost basis when the brokerage doesn't send one.

Plaid often has no cost basis for 401(k) plan funds (Fidelity NetBenefits sends none), so the Profit page can't say
what they've made. Typed-in costs are kept per holding (account and symbol) in Preference "paid" and stand in only
where the brokerage sends nothing: a cost it does send always wins.
"""

from dataclasses import replace

from sqlalchemy.orm import Session as DbSession

from financeapp.models import Preference
from financeapp.portfolio import Position

PREF = "paid"


def key(account_id: int, symbol: str) -> str:
    return f"{account_id}:{symbol}"


def load(db: DbSession) -> dict[str, int]:
    p = db.get(Preference, PREF)
    out = {}
    for k, v in ((p.value if p else {}).get("holdings") or {}).items():
        if isinstance(v, int) and v > 0:
            out[k] = v
    return out


def save(db: DbSession, account_id: int, symbol: str, cents: int | None) -> None:
    p = db.get(Preference, PREF)
    if p is None:
        p = Preference(key=PREF, value={})
        db.add(p)
    held = dict((p.value or {}).get("holdings") or {})
    if cents:
        held[key(account_id, symbol)] = cents
    else:
        held.pop(key(account_id, symbol), None)
    p.value = {**(p.value or {}), "holdings": held}  # a new dict, so SQLAlchemy sees the change
    db.flush()


def fill(db: DbSession, positions: list[Position]) -> tuple[list[Position], set[str]]:
    """The positions with typed-in costs where the brokerage sent none, and the keys that used one."""
    typed = load(db)
    out, used = [], set()
    for p in positions:
        k = key(p.account_id, p.symbol)
        if p.cost_basis_cents is None and k in typed:
            out.append(replace(p, cost_basis_cents=typed[k]))
            used.add(k)
        else:
            out.append(p)
    return out, used
