"""Searching every transaction, for the Spending page: words, category, account, kind, amount and dates.

The household endpoint only carries the last two months; this reaches the whole history, a page at a time, with
totals for everything that matches (not only the page shown). Transfers between your own accounts count in neither
total, the same as everywhere else.
"""

from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, covers, household
from financeapp.models import Category, Transaction

PAGE = 100
MERCHANTS = 5
Kind = Literal["out", "in", "transfer", "review"]
Sort = Literal["newest", "oldest", "largest", "smallest", "merchant"]


class Filters(BaseModel):
    q: str | None = Field(None, max_length=100)
    category: list[Annotated[str, Field(max_length=12)]] = Field(
        default_factory=list, max_length=40
    )  # category ids, or "none" for uncategorized
    account: list[int] = Field(default_factory=list, max_length=40)
    kind: Kind | None = None
    min_cents: int | None = Field(None, ge=0, le=10**13)  # the size of the amount, either direction
    max_cents: int | None = Field(None, ge=0, le=10**13)
    start: date | None = None
    end: date | None = None
    sort: Sort = "newest"
    # Dates go by the month each bill counts toward (covers.py), so a list opened from Budget adds up to its row.
    budget: bool = False
    offset: int = Field(0, ge=0, le=100_000)

    @model_validator(mode="after")
    def _ordered(self) -> "Filters":
        if self.start and self.end and self.start > self.end:
            self.start, self.end = self.end, self.start
        if self.min_cents is not None and self.max_cents is not None and self.min_cents > self.max_cents:
            self.min_cents, self.max_cents = self.max_cents, self.min_cents
        return self


def _pattern(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# Largest and smallest go by size, either direction, the same as the amount filter.
_ORDER = {
    "newest": [Transaction.date.desc()],
    "oldest": [Transaction.date.asc()],
    "largest": [func.abs(Transaction.amount_cents).desc(), Transaction.date.desc()],
    "smallest": [func.abs(Transaction.amount_cents).asc(), Transaction.date.desc()],
    "merchant": [func.lower(Transaction.merchant).asc(), Transaction.date.desc()],
}


def _sample_key(sort: str):
    """The same orders as _ORDER, for demo rows (sorted() is stable, so apply the tie-break first)."""
    if sort == "oldest":
        return lambda r: r["date"], False
    if sort == "largest":
        return lambda r: abs(r["amount_cents"]), True
    if sort == "smallest":
        return lambda r: abs(r["amount_cents"]), False
    if sort == "merchant":
        return lambda r: r["merchant"].lower(), False
    return lambda r: r["date"], True


def budget_range(db: DbSession, where: list, months: covers.Covers, f: Filters, dated):
    """The date range, with each bill dated by the month it counts toward: rent paid on Oct 28 for November is in
    November (as if paid on its first day), a utility paid on Nov 2 for October is in October (its last day)."""
    wide = []
    if f.start:
        wide.append(Transaction.date >= f.start - timedelta(days=covers.LOOKAROUND_DAYS))
    if f.end:
        wide.append(Transaction.date <= f.end + timedelta(days=covers.LOOKAROUND_DAYS))
    moved_in, moved_out = [], []
    for t in db.scalars(select(Transaction).where(*where, *wide, Transaction.amount_cents < 0)):
        posted = covers.month_start(t.date)
        m = months.month_of(t)
        if m == posted:
            continue
        c = months.categories.get(t.category_id) if t.category_id else None
        if t.transfer_group_id is not None or (c is not None and c.kind in ("transfer", "income")):
            continue
        on = m if m > posted else covers.add_months(m, 1) - timedelta(days=1)
        inside = (f.start is None or on >= f.start) and (f.end is None or on <= f.end)
        was = (f.start is None or t.date >= f.start) and (f.end is None or t.date <= f.end)
        if inside and not was:
            moved_in.append(t.id)
        elif was and not inside:
            moved_out.append(t.id)
    rule = and_(dated, Transaction.id.not_in(moved_out)) if moved_out else dated
    return or_(rule, Transaction.id.in_(moved_in)) if moved_in else rule


def search(db: DbSession, f: Filters) -> dict:
    accounts = household.counted(db)
    names = {a.id: household.display_name(a) for a in accounts}
    categories = {c.id: c for c in db.scalars(select(Category))}
    ids = [i for i in names if not f.account or i in f.account]

    transfer_ids = [i for i, c in categories.items() if c.kind == "transfer"]
    not_spending_ids = [i for i, c in categories.items() if c.kind in ("transfer", "income")]
    # Uncategorized is never a transfer; spelled out, since NULL IN (...) is NULL and would drop it from both totals.
    is_transfer = or_(
        Transaction.transfer_group_id.is_not(None),
        and_(Transaction.category_id.is_not(None), Transaction.category_id.in_(transfer_ids)),
    )
    is_spending = and_(
        Transaction.transfer_group_id.is_(None),
        or_(Transaction.category_id.is_(None), Transaction.category_id.not_in(not_spending_ids)),
    )

    where = [Transaction.account_id.in_(ids)]
    if f.q and f.q.strip():
        for word in f.q.split()[:6]:  # every word has to appear, in the merchant or the bank's description
            p = _pattern(word)
            where.append(or_(Transaction.merchant.ilike(p, escape="\\"), Transaction.description.ilike(p, escape="\\")))
    if f.category:
        wanted = [int(c) for c in f.category if c.isdigit()]
        either = [Transaction.category_id.in_(wanted)]
        if "none" in f.category:
            either.append(Transaction.category_id.is_(None))
        where.append(or_(*either))
    if f.kind == "transfer":
        where.append(is_transfer)
    elif f.kind == "out":
        where += [~is_transfer, Transaction.amount_cents < 0]
    elif f.kind == "in":
        where += [~is_transfer, Transaction.amount_cents > 0]
    elif f.kind == "review":
        where += [Transaction.category_source == "none", is_spending]
    if f.min_cents is not None:
        where.append(func.abs(Transaction.amount_cents) >= f.min_cents)
    if f.max_cents is not None:
        where.append(func.abs(Transaction.amount_cents) <= f.max_cents)
    months = covers.Covers(db, list(names), budget.today(), categories)
    in_range = []
    if f.start:
        in_range.append(Transaction.date >= f.start)
    if f.end:
        in_range.append(Transaction.date <= f.end)
    if in_range and f.budget:
        where.append(budget_range(db, where, months, f, and_(*in_range)))
    else:
        where += in_range

    counted = ~is_transfer
    count, out_cents, in_cents, first, last = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((and_(counted, Transaction.amount_cents < 0), -Transaction.amount_cents))), 0),
            func.coalesce(func.sum(case((and_(counted, Transaction.amount_cents > 0), Transaction.amount_cents))), 0),
            func.min(Transaction.date),
            func.max(Transaction.date),
        ).where(*where)
    ).one()
    # Where the money went: the merchants with the most net spending (refunds taken off), across every match.
    net = func.sum(-Transaction.amount_cents)
    merchants = db.execute(
        select(Transaction.merchant, net, func.count())
        .where(*where, counted)
        .group_by(Transaction.merchant)
        .having(net > 0)
        .order_by(net.desc(), Transaction.merchant)
        .limit(MERCHANTS)
    ).all()
    rows = db.scalars(
        select(Transaction).where(*where).order_by(*_ORDER[f.sort], Transaction.id.desc()).offset(f.offset).limit(PAGE)
    ).all()

    def row(t: Transaction) -> dict:
        c = categories.get(t.category_id) if t.category_id else None
        spending = t.transfer_group_id is None and (c is None or c.kind not in ("transfer", "income"))
        posted = covers.month_start(t.date)
        counts = months.month_of(t) if spending else posted
        return {
            "id": str(t.id),
            "date": t.date.isoformat(),
            "merchant": t.merchant,
            "category": c.name if c else "Uncategorized",
            "category_id": t.category_id,
            "amount_cents": t.amount_cents,
            "account": names[t.account_id],
            "transfer": bool(t.transfer_group_id) or (c is not None and c.kind == "transfer"),
            "pending": t.pending,
            "needs_review": t.category_source == "none" and spending,
            "by_hand": t.category_source == "user",
            # The month it counts toward in the budget, when that isn't the month it was paid in.
            "counts_toward": counts.isoformat() if counts != posted else None,
            "counts_by_hand": t.budget_month is not None,
            "bill": spending and t.amount_cents < 0,  # money out that could count toward another month
        }

    return {
        "transactions": [row(t) for t in rows],
        "count": count,
        "out_cents": int(out_cents),
        "in_cents": int(in_cents),
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
        "next_offset": f.offset + PAGE if f.offset + PAGE < count else None,
        "merchants": [{"merchant": m, "cents": int(c), "count": n} for m, c, n in merchants],
        "accounts": [{"id": i, "name": n} for i, n in sorted(names.items(), key=lambda x: x[1])],
    }


def search_sample(rows: list[dict], categories: list[dict], f: Filters) -> dict:
    """The same search over demo.py's made-up transactions, so sample mode behaves like the real thing."""
    by_name = {c["name"]: c["id"] for c in categories}
    accounts = sorted({r["account"] for r in rows})
    account_ids = {n: i for i, n in enumerate(accounts, 1)}
    words = [w.lower() for w in (f.q or "").split()[:6]]

    def keep(r: dict) -> bool:
        cid = by_name.get(r["category"])
        transfer = bool(r.get("transfer"))
        size = abs(r["amount_cents"])
        return (
            all(w in r["merchant"].lower() for w in words)
            and (
                not f.category or str(cid) in f.category or (r["category"] == "Uncategorized" and "none" in f.category)
            )
            and (not f.account or account_ids[r["account"]] in f.account)
            and (f.kind != "transfer" or transfer)
            and (f.kind != "out" or (not transfer and r["amount_cents"] < 0))
            and (f.kind != "in" or (not transfer and r["amount_cents"] > 0))
            and (f.kind != "review" or bool(r.get("needs_review")))
            and (f.min_cents is None or size >= f.min_cents)
            and (f.max_cents is None or size <= f.max_cents)
            and (f.start is None or r["date"] >= f.start.isoformat())
            and (f.end is None or r["date"] <= f.end.isoformat())
        )

    hits = [
        {
            "counts_toward": None,
            **r,
            "category_id": by_name.get(r["category"]),
            "bill": not r.get("transfer") and r["amount_cents"] < 0,
        }
        for r in rows
        if keep(r)
    ]
    hits.sort(key=lambda r: r["date"], reverse=True)
    key, reverse = _sample_key(f.sort)
    hits.sort(key=key, reverse=reverse)
    page = hits[f.offset : f.offset + PAGE]
    by_merchant: dict[str, list[int]] = {}
    for r in hits:
        if not r.get("transfer"):
            m = by_merchant.setdefault(r["merchant"], [0, 0])
            m[0] -= r["amount_cents"]
            m[1] += 1
    merchants = sorted(((m, c, n) for m, (c, n) in by_merchant.items() if c > 0), key=lambda x: (-x[1], x[0]))
    return {
        "transactions": page,
        "count": len(hits),
        "out_cents": sum(-r["amount_cents"] for r in hits if not r.get("transfer") and r["amount_cents"] < 0),
        "in_cents": sum(r["amount_cents"] for r in hits if not r.get("transfer") and r["amount_cents"] > 0),
        "first": min((r["date"] for r in hits), default=None),
        "last": max((r["date"] for r in hits), default=None),
        "next_offset": f.offset + PAGE if f.offset + PAGE < len(hits) else None,
        "merchants": [{"merchant": m, "cents": c, "count": n} for m, c, n in merchants[:MERCHANTS]],
        "accounts": [{"id": i, "name": n} for n, i in account_ids.items()],
    }
