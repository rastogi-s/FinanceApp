"""Draft with AI: a budget suggested from everything the app knows, line by line, for the household to accept or not.

What's sent is totals only: each category's spending by month, the budget as it stands (and which lines the household
set by hand, against what the app would draft), pay from the stubs (gross, taxes, pre-tax savings, take-home), the
savings rate by month, the emergency fund and the plan's savings goal. No merchants, no names, no accounts. The
person sees exactly that before it goes, and nothing is saved until they pick the lines they want.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, plan
from financeapp.ai import service
from financeapp.ai.providers import ProviderError, Request, parse_json
from financeapp.db import Database
from financeapp.models import BudgetLine, Paycheck
from financeapp.sync import today

TASK = "plan_budget"
MAX_REPLY_TOKENS = 6000
MAX_CENTS = 10_000_000_00
PREVIEW_CHARS = 3000

SYSTEM = """You help a household set a realistic monthly budget. You get JSON (amounts in dollars) with:
- spending_by_month: each category's spending in recent full months (bills = fixed costs, everyday = flexible),
- budget: each line's current limit, what the app's own draft would be (the median month, plus 10% on everyday
  lines), and set_by_household = true where the household changed it by hand (a null limit: they took the line
  out). Respect those unless the numbers clearly say otherwise, and then say why.
- pay: monthly gross, taxes, pre-tax savings and take-home from pay stubs, plus income with no stub,
- savings_rate_by_month, the emergency fund, and the household's monthly savings goal if they set one.

Suggest a limit for every category that should have one: bills at what they actually cost, everyday spending at a
level that is realistic from their history but leaves room to meet the savings goal (or, with no goal, keeps the
savings rate where it is or a little better). Don't cut essentials below what they cost. Leave out categories with
no real spending. For each line give a reason of at most 15 words that cites the history it rests on. In summary,
2 or 3 sentences: what changes overall and what it means for monthly savings. Amounts in whole dollars."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "limit": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["category", "limit", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "lines"],
    "additionalProperties": False,
}


def dollars(cents: int | float | None) -> float | None:
    return None if cents is None else round(cents / 100)


def facts(db: DbSession) -> dict | None:
    """What's sent. None when there isn't a full month of spending yet."""
    accounts = budget.counted_accounts(db)
    end = today()
    months = budget.full_months(db, [a.id for a in accounts], end)
    if not months:
        return None
    ledger = budget.Ledger(db, accounts, months[0] - timedelta(days=budget.MATCH_DAYS))
    drafted = budget.draft_lines(ledger, months)
    version = budget.latest_version(db)
    current = (
        {
            line.category_id: line.limit_cents
            for line in db.scalars(select(BudgetLine).where(BudgetLine.version_id == version.id))
        }
        if version
        else {cid: line["limit_cents"] for cid, line in drafted.items()}
    )

    by_month = []
    budget_rows = []
    for c in sorted(ledger.categories.values(), key=lambda c: (c.sort, c.name)):
        if c.kind not in budget.BUDGETED:
            continue
        spent = [max(ledger.spent.get((c.id, m), 0), 0) for m in months]
        if not any(spent) and c.id not in current:
            continue
        by_month.append(
            {
                "category": c.name,
                "kind": "bill" if c.kind == "fixed" else "everyday",
                "essential": c.essential,
                "months": {m.strftime("%Y-%m"): dollars(s) for m, s in zip(months, spent, strict=True)},
            }
        )
        if c.id in current or c.id in drafted:
            draft = drafted.get(c.id, {}).get("limit_cents")
            limit = current.get(c.id)
            budget_rows.append(
                {
                    "category": c.name,
                    "limit": dollars(limit),
                    "app_draft": dollars(draft),
                    "set_by_household": version is not None and version.source != "draft" and limit != draft,
                }
            )
    # Spending outside bills and everyday (e.g. a category marked "other"), as a monthly average.
    outside: dict[str, int] = defaultdict(int)
    for (cid, m), cents in ledger.spent.items():
        c = ledger.categories.get(cid) if cid else None
        if m in months and (c is None or c.kind not in budget.BUDGETED):
            outside[c.name if c else "Uncategorized"] += cents
    other = sorted(((k, v / len(months)) for k, v in outside.items()), key=lambda kv: -kv[1])

    state = budget.build(db)
    stubs = db.scalars(select(Paycheck).where(Paycheck.date >= months[0])).all()
    n = len(months)
    pay = {
        "stubs": len(stubs),
        "monthly_gross": dollars(sum(p.gross_cents for p in stubs) / n),
        "monthly_taxes": dollars(sum(p.taxes_cents for p in stubs) / n),
        "monthly_pre_tax_savings": dollars(sum(p.savings_cents for p in stubs) / n),
        "monthly_take_home": dollars(sum(p.net_cents for p in stubs) / n),
        "schedules": [
            {"frequency": s["frequency"], "monthly_take_home": dollars(s["monthly_net_cents"])}
            for s in state["schedules"]
        ],
    }
    goal = plan.preferences(db).get("savings_cents")
    return {
        "today": end.isoformat(),
        "months": [m.strftime("%Y-%m") for m in months],
        "spending_by_month": by_month,
        "other_spending_monthly": [{"category": k, "average": dollars(v)} for k, v in other if v >= 100],
        "budget": budget_rows,
        "pay": pay,
        "savings_rate_by_month": [
            {
                "month": r["month"][:7],
                "percent": r["percent"],
                "income": dollars(r["income_cents"]),
                "spending": dollars(r["spending_cents"]),
                "saved": dollars(r["saved_cents"]),
            }
            for r in state["savings_history"]
        ],
        "emergency_fund": {
            "months_target": state["emergency"]["months"],
            "target": dollars(state["emergency"]["target_cents"]),
            "cash_now": dollars(state["emergency"]["have_cents"]),
        },
        "monthly_savings_goal": dollars(goal),
    }


def request_for(f: dict) -> Request:
    return Request(SYSTEM, json.dumps(f, indent=1), [], SCHEMA, MAX_REPLY_TOKENS)


def offer(db: DbSession) -> dict:
    """What would be sent, to whom, at most what cost; `available` False with the reason when it can't."""
    f = facts(db)
    body: dict = {
        "available": False,
        "problem": None,
        "provider": None,
        "model": None,
        "max_cost": None,
        "months": len(f["months"]) if f else 0,
        "categories": len(f["spending_by_month"]) if f else 0,
        "stubs": f["pay"]["stubs"] if f else 0,
        "preview": json.dumps(f, indent=1)[:PREVIEW_CHARS] if f else None,
    }
    if f is None:
        body["problem"] = "There isn't a full month of spending to work from yet."
        return body
    route, problem = service.find_route(db, TASK)
    if route is not None:
        cost = service.estimate(request_for(f), route.price)
        problem = service.cap_problem(db, cost)
        body |= {"provider": route.provider.label, "model": route.model, "max_cost": round(cost / 1_000_000, 4)}
    body["available"] = problem is None
    body["problem"] = problem
    return body


class _Line(BaseModel):
    category: str = Field(max_length=120)
    limit: float
    reason: str = Field(max_length=400)


class Answer(BaseModel):
    summary: str = Field(max_length=1500)
    lines: list[_Line] = Field(max_length=200)


@dataclass
class Suggestion:
    summary: str
    lines: list[dict]
    model: str
    cost: float


class NotABudget(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def suggest(database: Database, db: DbSession, user_id: int) -> Suggestion:
    """Sends the facts. Raises AIUnavailable (nothing sent), ProviderError (sent, failed) or NotABudget."""
    f = facts(db)
    if f is None:
        raise NotABudget("There isn't a full month of spending to work from yet.")
    reply, route = service.run(database, db, TASK, user_id, request_for(f))
    try:
        answer = Answer.model_validate(parse_json(reply.text))
    except ValidationError:
        raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None

    accounts = budget.counted_accounts(db)
    months = budget.full_months(db, [a.id for a in accounts], today())
    ledger = budget.Ledger(db, accounts, months[0])
    by_name = {c.name.lower(): c for c in ledger.categories.values() if c.kind in budget.BUDGETED}
    drafted = budget.draft_lines(ledger, months)
    version = budget.latest_version(db)
    current = (
        {
            line.category_id: line.limit_cents
            for line in db.scalars(select(BudgetLine).where(BudgetLine.version_id == version.id))
        }
        if version
        else {cid: line["limit_cents"] for cid, line in drafted.items()}
    )
    lines, seen = [], set()
    for line in answer.lines:
        c = by_name.get(line.category.strip().lower())
        if c is None or c.id in seen:
            continue  # a category that doesn't exist (or a second go at one) is dropped
        seen.add(c.id)
        cents = min(max(round(line.limit) * 100, 0), MAX_CENTS)
        lines.append(
            {
                "category_id": c.id,
                "category": c.name,
                "kind": c.kind,
                "current_cents": current.get(c.id, 0),
                "typical_cents": drafted.get(c.id, {}).get("typical_cents", 0),
                "suggested_cents": cents,
                "reason": line.reason.strip(),
            }
        )
    if not lines:
        raise NotABudget("The AI's answer didn't match any of your categories. Try again or pick another model.")
    cost = service.microdollars(reply.input_tokens, reply.output_tokens, route.price) / 1_000_000
    return Suggestion(answer.summary.strip(), lines, route.model, round(cost, 4))
