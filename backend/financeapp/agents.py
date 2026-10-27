"""What an AI agent can ask over MCP (api/mcp_routes.py): read-only views of the household's numbers.

Every tool only reads. Amounts are dollars (what an agent expects to quote back), accounts are named the way the
app shows them (institution, name and last 4 digits, never the full number), and each answer says how fresh it is.
The tools reuse the same builders as the app's pages, so an agent sees the numbers you see.
"""

from collections import defaultdict
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from financeapp import alerts, budget, cards, household, kinds, portfolio
from financeapp.models import Account, AlertEvent, Balance, Brief, Category, Transaction, now
from financeapp.sync import today

MAX_TRANSACTIONS = 500
MAX_RANGE_DAYS = 3 * 366


def dollars(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


class ToolError(Exception):
    """A problem with what the agent asked, explained so it can try again."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


class TransactionArgs(Args):
    search: str | None = Field(None, max_length=80, description="Text to look for in the merchant name")
    category: str | None = Field(None, max_length=60, description="A category name, e.g. Groceries")
    account: str | None = Field(None, max_length=80, description="Part of an account's name, e.g. Chase or 4417")
    start: date | None = Field(None, description="First day, YYYY-MM-DD (default: 30 days ago)")
    end: date | None = Field(None, description="Last day, YYYY-MM-DD (default: today)")
    kind: Literal["all", "spending", "income"] = Field("all", description="Only money out, only money in, or both")
    min_amount: float | None = Field(None, ge=0, description="Smallest amount in dollars, either direction")
    limit: int = Field(100, ge=1, le=MAX_TRANSACTIONS)


class RangeArgs(Args):
    start: date | None = Field(None, description="First day, YYYY-MM-DD (default: the 1st of this month)")
    end: date | None = Field(None, description="Last day, YYYY-MM-DD (default: today)")
    by_month: bool = Field(False, description="Split the totals by calendar month")


class HistoryArgs(Args):
    days: int = Field(90, ge=2, le=household.HISTORY_DAYS, description="How many days back")


class BestCardArgs(Args):
    store: str | None = Field(None, max_length=80, description="A store or merchant name, e.g. Costco or Delta")
    category: Literal[tuple(k for k in cards.CATEGORIES if k != "superstores")] | None = Field(  # type: ignore[valid-type]
        None, description="A reward category instead of a store, e.g. dining or groceries"
    )
    amount: float = Field(100, gt=0, le=100_000, description="Purchase amount in dollars")


def _range(start: date | None, end: date | None, default_start: date) -> tuple[date, date]:
    end = end or today()
    start = start or default_start
    if start > end:
        raise ToolError("start is after end.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ToolError(f"Ask for at most {MAX_RANGE_DAYS} days at a time.")
    return start, end


def _accounts(db: DbSession) -> list[Account]:
    return household.counted(db)


# ---- the tools


def overview(db: DbSession, _: NoArgs) -> dict:
    h = household.build(db)
    if not h.get("linked"):
        return {"linked": False, "note": "No accounts have been added yet."}
    acc = h["accounts"]
    safe = h.get("safe_to_spend")
    rate = h.get("savings_rate")
    return {
        "as_of": h["as_of"],
        "net_worth": dollars(h["net_worth"]["cents"]),
        "net_worth_change_today": dollars(h["net_worth"]["change_today_cents"]),
        "cash": dollars(sum(a["cents"] for a in acc["cash"])),
        "investments": dollars(sum(a["cents"] for a in acc["investments"])),
        "debts": dollars(sum(a["cents"] for a in acc["debts"])),
        "spent_this_month": dollars(h["spending"]["month_cents"]),
        "spent_last_month_by_same_day": dollars(h["spending"]["last_month_same_day_cents"]),
        "transactions_to_review": h["spending"]["needs_review"],
        "safe_to_spend": {"amount": dollars(safe["cents"]), "days_left_in_month": safe["days_left"]} if safe else None,
        "savings_rate": {"percent": rate["percent"], "month": rate["month"], "basis": rate["basis"]} if rate else None,
        "accounts_needing_attention": [s["institution"] for s in h["health"]["stale"]],
    }


def accounts(db: DbSession, _: NoArgs) -> dict:
    rows = []
    for a in _accounts(db):
        b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        rows.append(
            {
                "name": household.display_name(a),
                "institution": a.institution,
                "type": a.type,
                "subtype": a.subtype,
                "kind": kinds.LABEL[kinds.kind_of(a.type, a.subtype, a.name, a.kind)],
                "balance": dollars(b.current_cents) if b else None,
                "balance_as_of": b.date.isoformat() if b else None,
            }
        )
    return {"accounts": rows, "note": "Card and loan balances are what's owed."}


def transactions(db: DbSession, args: TransactionArgs) -> dict:
    start, end = _range(args.start, args.end, today() - timedelta(days=30))
    accts = {a.id: a for a in _accounts(db)}
    if args.account:
        needle = args.account.lower()
        accts = {i: a for i, a in accts.items() if needle in household.display_name(a).lower()}
        if not accts:
            raise ToolError("No account matches that name. The accounts tool lists them.")
    categories = {c.id: c for c in db.scalars(select(Category))}
    q = (
        select(Transaction)
        .where(Transaction.account_id.in_(list(accts)), Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    if args.search:
        pattern = "%" + args.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        q = q.where(
            or_(Transaction.merchant.ilike(pattern, escape="\\"), Transaction.description.ilike(pattern, escape="\\"))
        )
    if args.category:
        ids = [c.id for c in categories.values() if c.name.lower() == args.category.strip().lower()]
        if not ids:
            names = ", ".join(sorted(c.name for c in categories.values()))
            raise ToolError(f"There's no category called that. The categories are: {names}.")
        q = q.where(Transaction.category_id.in_(ids))
    if args.kind == "spending":
        q = q.where(Transaction.amount_cents < 0)
    elif args.kind == "income":
        q = q.where(Transaction.amount_cents > 0)
    if args.min_amount is not None:
        q = q.where(func.abs(Transaction.amount_cents) >= round(args.min_amount * 100))
    rows = db.scalars(q.limit(args.limit + 1)).all()
    out = []
    for t in rows[: args.limit]:
        c = categories.get(t.category_id) if t.category_id else None
        out.append(
            {
                "date": t.date.isoformat(),
                "merchant": t.merchant,
                "amount": dollars(t.amount_cents),
                "category": c.name if c else "Uncategorized",
                "account": household.display_name(accts[t.account_id]),
                "transfer": t.transfer_group_id is not None or (c is not None and c.kind == "transfer"),
                "pending": t.pending,
            }
        )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "transactions": out,
        "more": len(rows) > args.limit,
        "note": "Negative amounts are money out.",
    }


def spending_by_category(db: DbSession, args: RangeArgs) -> dict:
    start, end = _range(args.start, args.end, today().replace(day=1))
    ledger = budget.Ledger(db, _accounts(db), start)
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for t in ledger.txns:
        if t.date > end or not ledger.is_spending(t):
            continue
        c = ledger.categories.get(t.category_id) if t.category_id else None
        key = (t.date.strftime("%Y-%m") if args.by_month else "", c.name if c else "Uncategorized")
        totals[key] -= t.amount_cents
    rows = [
        {**({"month": m} if args.by_month else {}), "category": name, "spent": dollars(cents)}
        for (m, name), cents in sorted(totals.items(), key=lambda kv: (kv[0][0], -kv[1]))
        if cents
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": dollars(sum(totals.values())),
        "categories": rows,
        "note": "Transfers between your own accounts, income and taxes are left out; refunds reduce spending.",
    }


def budget_status(db: DbSession, _: NoArgs) -> dict:
    s = budget.build(db)
    return {
        "month": s["month"],
        "saved_by_you": s["saved"],
        "lines": [
            {
                "category": line["category"],
                "kind": "bill" if line["kind"] == "fixed" else "everyday",
                "limit": dollars(line["limit_cents"]),
                "spent": dollars(line["spent_cents"]),
                "left": dollars(line["limit_cents"] - line["spent_cents"]),
            }
            for line in s["lines"]
        ],
        "not_in_budget": [{"category": u["category"], "spent": dollars(u["spent_cents"])} for u in s["unbudgeted"]],
        "safe_to_spend": {
            "amount": dollars(s["safe_to_spend"]["cents"]),
            "days_left_in_month": s["safe_to_spend"]["days_left"],
        }
        if s["safe_to_spend"]
        else None,
        "savings_rate_by_month": [
            {"month": r["month"], "percent": r["percent"], "basis": r["basis"]} for r in s["savings_history"]
        ],
        "emergency_fund": {
            "months_target": s["emergency"]["months"],
            "target": dollars(s["emergency"]["target_cents"]),
            "cash_now": dollars(s["emergency"]["have_cents"]),
        },
        "pay_schedules": [
            {
                "person": sch["person"],
                "frequency": sch["frequency"],
                "next_payday": sch["next_date"],
                "monthly_take_home": dollars(sch["monthly_net_cents"]),
            }
            for sch in s["schedules"]
        ],
    }


def holdings(db: DbSession, _: NoArgs) -> dict:
    accts = _accounts(db)
    names = {a.id: household.display_name(a) for a in accts}
    ps = portfolio.positions(db, accts)
    c = portfolio.combined(ps, names)
    change = {p.symbol: p.change_today_percent for p in ps}
    as_of = portfolio.prices_as_of(ps)
    return {
        "total": dollars(c["total_cents"]),
        "prices_as_of": as_of.isoformat() if as_of else None,
        "top5_percent": c["top5_percent"],
        "positions": [
            {
                "symbol": r["symbol"],
                "name": r["name"],
                "asset_class": r["asset_class"],
                "value": dollars(r["value_cents"]),
                "percent_of_investments": r["percent"],
                "change_today_percent": change.get(r["symbol"]),
                "accounts": [{"account": a["account"], "value": dollars(a["value_cents"])} for a in r["accounts"]],
            }
            for r in c["positions"]
        ],
    }


def net_worth_history(db: DbSession, args: HistoryArgs) -> dict:
    history = household.net_worth_history(db, _accounts(db), today())[-args.days :]
    return {
        "points": [{"date": p["date"], "net_worth": dollars(p["cents"])} for p in history],
        "note": "From each account's daily balance; today's live price moves aren't included.",
    }


def recent_alerts(db: DbSession, _: NoArgs) -> dict:
    rows = db.scalars(
        select(AlertEvent)
        .where(AlertEvent.fired_at > now() - timedelta(days=30))
        .order_by(AlertEvent.fired_at.desc())
        .limit(50)
    )
    return {
        "alerts": [
            {k: v for k, v in alerts.event_json(e).items() if k in ("kind", "title", "body", "at")} for e in rows
        ]
    }


def morning_brief(db: DbSession, _: NoArgs) -> dict:
    b = db.scalar(select(Brief).order_by(Brief.day.desc()).limit(1))
    if b is None:
        return {"brief": None, "note": "No morning brief yet. It can be turned on in Settings, Alerts."}
    return {
        "day": b.day.isoformat(),
        "headline": b.headline,
        "points": b.points.get("items", []),
        "written_by": b.written_by,
    }


def best_card(db: DbSession, args: BestCardArgs) -> dict:
    if not args.store and not args.category:
        raise ToolError("Give a store or a category.")
    w = cards.wallet(db, today())
    if not w.cards:
        return {"cards": [], "note": "No cards are linked to a card product yet (Credit cards page)."}
    cents = round(args.amount * 100)
    kind = None if args.store else args.category
    found = cards.lookup(w.cards, w.everything, w.on, w.values, w.ledger(), args.store or "", None, kind, cents)
    if not found["found"]:
        return {"store": args.store, "found": False, "note": "Unknown store: ask again with a category."}
    out = []
    for ch in found["channels"]:
        ranked = []
        for row in ch["cards"]:
            ranked.append(
                {
                    "card": row["card"],
                    "rate": row["rate"],
                    "earns": dollars(row["value_cents"]),
                    "why": row["why"],
                    "cap": row["cap"],
                    "warnings": row["warnings"],
                    "better_through_portal": row["portal"],
                }
            )
        out.append({"channel": ch["channel"], "cards": ranked})
    return {
        "store": found["name"],
        "kind": found["kind"],
        "how_known": found["how"],
        "amount": args.amount,
        "by_channel": out,
        "note": "Earnings are in dollars at the household's cents-per-point values; caps already used are counted.",
    }


# ---- the catalog the MCP server lists


class Tool:
    def __init__(self, name: str, title: str, description: str, args: type[Args], run) -> None:
        self.name, self.title, self.description, self.args, self.run = name, title, description, args, run

    def listing(self) -> dict:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


TOOLS = {
    t.name: t
    for t in [
        Tool(
            "overview",
            "Household overview",
            "Net worth, cash, investments and debts, spending this month against last month, safe to spend, the "
            "savings rate, and any accounts that need attention. Start here.",
            NoArgs,
            overview,
        ),
        Tool("accounts", "Accounts", "Every account the app counts, with its latest balance.", NoArgs, accounts),
        Tool(
            "transactions",
            "Find transactions",
            "Transactions filtered by merchant text, category, account, dates, direction and size. Newest first.",
            TransactionArgs,
            transactions,
        ),
        Tool(
            "spending_by_category",
            "Spending by category",
            "Total spending per category for a date range, optionally split by month. Use this for questions like "
            "'how much did we spend on dining this year?'.",
            RangeArgs,
            spending_by_category,
        ),
        Tool(
            "budget",
            "This month's budget",
            "Each budget line's limit, spending and what's left, spending outside the budget, safe to spend, the "
            "savings rate by month, the emergency fund, and pay schedules.",
            NoArgs,
            budget_status,
        ),
        Tool(
            "holdings",
            "Investments",
            "Every holding added up across accounts, largest first, with its share of investments and today's move.",
            NoArgs,
            holdings,
        ),
        Tool(
            "net_worth_history",
            "Net worth over time",
            "Net worth by day, up to 90 days back.",
            HistoryArgs,
            net_worth_history,
        ),
        Tool(
            "alerts",
            "Recent alerts",
            "Price, budget and concentration alerts from the last 30 days.",
            NoArgs,
            recent_alerts,
        ),
        Tool("morning_brief", "Morning brief", "The latest morning brief the app wrote.", NoArgs, morning_brief),
        Tool(
            "best_card",
            "Best card to use",
            "Which of the household's credit cards earns the most at a store or in a reward category, in store and "
            "online, with the rate, the dollars earned, caps and any warnings (e.g. a store that takes only Visa).",
            BestCardArgs,
            best_card,
        ),
    ]
}


def call(db: DbSession, name: str, arguments: dict | None) -> dict:
    """Runs one tool. Raises ToolError with a message the agent can act on."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"There's no tool called {name[:40]!r}.")
    try:
        args = tool.args.model_validate(arguments or {})
    except ValidationError as e:
        # Only where and what kind (pydantic's codes, e.g. less_than_equal), not the exception's own text.
        problems = ", ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'} ({err['type']})" for err in e.errors()[:5]
        )
        raise ToolError(f"Check these arguments against the tool's input schema: {problems}.") from None
    return tool.run(db, args)
