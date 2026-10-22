"""Suggest with AI: a category for each merchant on the Check categories screen, for the household to accept or not.

What's sent: for each merchant the app isn't sure about, its name, one description with identifying details taken
out (ai/redact.py), whether the money went out or came in, how many times and a typical amount, and the category
Plaid suggested; plus the list of the household's categories. Zelle and Venmo payments to people aren't sent (a
name says nothing about what the money was for), and nothing is saved until the person taps a suggestion.
"""

import json
import statistics
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import review
from financeapp.ai import service
from financeapp.ai.providers import ProviderError, Request, parse_json
from financeapp.ai.reading import household_identity
from financeapp.ai.redact import redact
from financeapp.db import Database
from financeapp.models import Category, Transaction

TASK = "categorize"
MAX_GROUPS = 60
MAX_REPLY_TOKENS = 4000
PREVIEW_CHARS = 3000
ASKED = ("no_category", "guess", "money_in", "refund")
KIND_LABEL = {"fixed": "bill", "flexible": "everyday", "income": "money in", "other": "other"}

SYSTEM = """You sort a household's bank and card transactions into their budget categories. You get JSON with:
- categories: the categories to choose from, each with its kind (bill, everyday, money in, other),
- merchants: the merchants the app isn't sure about. Each has an id, the merchant name, one example description,
  direction ("out" = money spent, "in" = money received), how many transactions and a typical amount in dollars,
  plaid (the bank aggregator's own guess, often right but broad) and current (what the app picked for now).

For each merchant pick the single best category from the list, by its exact name. Money in from a shop is a refund:
pick the category its purchases belong in, not a money-in category. Only pick a money-in category for money in that
isn't a refund. If you can't tell what a merchant is, leave it out rather than guessing. Give a reason of at most 12
words (what the merchant is)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "number"},
                    "category": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "category", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def choices(db: DbSession) -> list[Category]:
    """Categories the AI may pick: not transfers (it can't know whose account money went to)."""
    return [c for c in db.scalars(select(Category).order_by(Category.sort, Category.name)) if c.kind != "transfer"]


def facts(db: DbSession, state: dict | None = None) -> tuple[dict, list[dict]] | None:
    """What's sent, and the groups it covers (in id order). None when there's nothing to ask about."""
    state = state or review.state(db)
    groups = [g for g in state["groups"] if g["reason"] in ASKED and not g["person"]][:MAX_GROUPS]
    if not groups:
        return None
    names, emails = household_identity(db)
    plaid = {
        t.id: t.provider_category
        for t in db.scalars(select(Transaction).where(Transaction.id.in_([int(g["ids"][0]) for g in groups])))
    }
    merchants = []
    for i, g in enumerate(groups, 1):
        amounts = [abs(e["amount_cents"]) for e in g["examples"]]
        merchants.append(
            {
                "id": i,
                "merchant": redact(g["merchant"], names, emails).text[:120],
                "example": redact(g["examples"][0]["description"], names, emails).text[:120],
                "direction": "in" if g["total_cents"] > 0 else "out",
                "count": g["count"],
                "typical": round(statistics.median(amounts) / 100),
                "plaid": plaid.get(int(g["ids"][0])) or "",
                "current": g["category"],
            }
        )
    cats = [{"name": c.name, "kind": KIND_LABEL.get(c.kind, c.kind)} for c in choices(db)]
    return {"categories": cats, "merchants": merchants}, groups


def request_for(f: dict) -> Request:
    return Request(SYSTEM, json.dumps(f, indent=1), [], SCHEMA, MAX_REPLY_TOKENS)


def offer(db: DbSession) -> dict:
    """What would be sent, to whom, at most what cost; `available` False with the reason when it can't."""
    found = facts(db)
    body: dict = {
        "available": False,
        "problem": None,
        "provider": None,
        "model": None,
        "max_cost": None,
        "merchants": len(found[1]) if found else 0,
        "preview": json.dumps(found[0], indent=1)[:PREVIEW_CHARS] if found else None,
    }
    if found is None:
        body["problem"] = "Nothing left that AI could help with."
        return body
    route, problem = service.find_route(db, TASK)
    if route is not None:
        cost = service.estimate(request_for(found[0]), route.price)
        problem = service.cap_problem(db, cost)
        body |= {"provider": route.provider.label, "model": route.model, "max_cost": round(cost / 1_000_000, 4)}
    body["available"] = problem is None
    body["problem"] = problem
    return body


class _Pick(BaseModel):
    id: float
    category: str = Field(max_length=120)
    reason: str = Field(max_length=300)


class Answer(BaseModel):
    suggestions: list[_Pick] = Field(max_length=MAX_GROUPS * 2)


@dataclass
class Suggestions:
    by_group: dict[str, dict]  # group key -> {category_id, category, reason}
    model: str
    cost: float


class NothingToAsk(Exception):
    pass


def suggest(database: Database, db: DbSession, user_id: int) -> Suggestions:
    """Sends the facts. Raises AIUnavailable (nothing sent), ProviderError (sent, failed) or NothingToAsk."""
    found = facts(db)
    if found is None:
        raise NothingToAsk
    f, groups = found
    reply, route = service.run(database, db, TASK, user_id, request_for(f))
    try:
        answer = Answer.model_validate(parse_json(reply.text))
    except ValidationError:
        raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None
    by_name = {c.name.lower(): c for c in choices(db)}
    out: dict[str, dict] = {}
    for pick in answer.suggestions:
        i = int(pick.id)
        c = by_name.get(pick.category.strip().lower())
        if c is None or not 1 <= i <= len(groups) or groups[i - 1]["key"] in out:
            continue  # a category that doesn't exist, an id that wasn't sent, or a second go at one
        g = groups[i - 1]
        if c.kind == "income" and g["total_cents"] < 0:
            continue  # money out is never income
        reason = redact(pick.reason.strip(), *household_identity(db)).text[:200]
        out[g["key"]] = {"category_id": c.id, "category": c.name, "reason": reason}
    cost = service.microdollars(reply.input_tokens, reply.output_tokens, route.price) / 1_000_000
    return Suggestions(out, route.model, round(cost, 4))
