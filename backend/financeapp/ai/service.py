"""Running an AI task: which provider and model it goes to, whether the monthly cap allows it, and the usage log.

Each task (reading uploads, for now) is routed to one provider and model the owner picked. Before a call the cost is
estimated from the request's size and the reply's token limit; a call that could push the month over the cap is
refused, not sent. After it, the real token counts are logged with their cost (in its own transaction, so a failed
request is still counted).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import crypto
from financeapp.ai import providers
from financeapp.ai.providers import Endpoint, ProviderError, Reply, Request
from financeapp.db import Database
from financeapp.models import AIProvider, AIUsage, Preference, now

PREF_KEY = "ai"
DEFAULT_CAP_CENTS = 500  # $5 a month until the owner changes it
MAX_CAP_CENTS = 100_000


@dataclass(frozen=True)
class Task:
    title: str
    detail: str
    pictures: bool  # needs a model that can read pictures


TASKS = {
    "read_files": Task(
        "Reading uploads",
        "Statements, spreadsheets, screenshots and scanned pay stubs that the built-in readers don't know.",
        pictures=True,
    ),
}

# Dollars per million tokens (input, output), from each provider's price list. A model that isn't here is priced
# at GUESS (on the high side, so the cap errs towards stopping early) until the owner types in its price.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
GUESS = (15.0, 75.0)
# What to suggest when a provider is first set up (the owner can pick any model the provider lists).
SUGGESTED = {"anthropic": "claude-opus-5-5"}
PICTURE_TOKENS = 1600  # roughly what one page-sized picture costs as input


class AIUnavailable(RuntimeError):
    """The task can't run: no provider picked, provider off, over the cap. The message is shown as-is."""


# ---- settings


def settings(db: DbSession) -> dict:
    p = db.get(Preference, PREF_KEY)
    value = p.value if p else {}
    return {"monthly_cap_cents": value.get("monthly_cap_cents", DEFAULT_CAP_CENTS), "routes": value.get("routes", {})}


def save_settings(db: DbSession, **changes) -> dict:
    p = db.get(Preference, PREF_KEY)
    if p is None:
        p = Preference(key=PREF_KEY, value={})
        db.add(p)
    p.value = {**p.value, **changes}
    return settings(db)


def price_of(provider: AIProvider, model: str, route: dict | None = None) -> tuple[float, float, bool]:
    """(input, output) dollars per million tokens, and whether that's a known price rather than a guess."""
    if route and route.get("price_in") is not None and route.get("price_out") is not None:
        return float(route["price_in"]), float(route["price_out"]), True
    if model in PRICES:
        return *PRICES[model], True
    if provider.kind == "openai_compatible" and provider.base_url:
        host = urlsplit(provider.base_url).hostname or ""
        if providers.is_local_host(host):
            return 0.0, 0.0, True  # runs on your own machine
    return *GUESS, False


def microdollars(tokens_in: int, tokens_out: int, price: tuple[float, float, bool]) -> int:
    return round(tokens_in * price[0] + tokens_out * price[1])  # $/million tokens x tokens = millionths of a dollar


def month_start() -> datetime:
    t = now()
    return datetime(t.year, t.month, 1, tzinfo=UTC)


def spent_this_month(db: DbSession) -> int:
    """Microdollars spent since the 1st (UTC)."""
    return int(
        db.scalar(select(func.coalesce(func.sum(AIUsage.cost_microdollars), 0)).where(AIUsage.at >= month_start())) or 0
    )


def endpoint(p: AIProvider) -> Endpoint:
    key = crypto.decrypt(p.api_key_enc, "ai_providers.api_key") if p.api_key_enc else None
    return Endpoint(kind=p.kind, api_key=key, base_url=p.base_url)


# ---- routing


@dataclass
class Route:
    task: str
    provider: AIProvider
    model: str
    price: tuple[float, float, bool]


def find_route(db: DbSession, task: str, pictures: bool = False) -> tuple[Route | None, str | None]:
    """Where a task would go right now, or what to set up first (a message for the person)."""
    if task not in TASKS:
        return None, "Unknown AI task."
    title = TASKS[task].title.lower()
    r = settings(db)["routes"].get(task)
    provider = db.get(AIProvider, r["provider_id"]) if r and r.get("provider_id") else None
    if provider is None or not r.get("model"):
        return None, f"AI isn't set up for {title}. The owner can turn it on in Settings, AI."
    if not provider.enabled:
        return None, f"{provider.label} is turned off. The owner can turn it on in Settings, AI."
    if pictures and not provider.allow_pictures:
        return None, (
            f"{provider.label} isn't allowed to see pictures and scans, which can't be redacted first. The owner can "
            "allow it in Settings, AI."
        )
    return Route(task, provider, r["model"], price_of(provider, r["model"], r)), None


def route_for(db: DbSession, task: str, pictures: bool = False) -> Route:
    route, problem = find_route(db, task, pictures)
    if route is None:
        raise AIUnavailable(problem)
    return route


def estimate(req: Request, price: tuple[float, float, bool]) -> int:
    """The most a request could cost, in microdollars: its size in tokens (about 3 characters each, generously)
    plus every reply token it's allowed."""
    tokens_in = (len(req.system) + len(req.text)) // 3 + PICTURE_TOKENS * len(req.pictures)
    return microdollars(tokens_in, req.max_tokens, price)


def cap_problem(db: DbSession, cost: int) -> str | None:
    """Why a request costing up to `cost` microdollars can't go this month, or None if it can."""
    cap = settings(db)["monthly_cap_cents"] * 10_000
    spent = spent_this_month(db)
    if spent + cost > cap:
        return (
            f"That could take this month's AI spending past the ${cap / 1_000_000:.2f} cap "
            f"(${spent / 1_000_000:.2f} used). The owner can raise it in Settings, AI."
        )
    return None


def check_cap(db: DbSession, cost: int) -> None:
    problem = cap_problem(db, cost)
    if problem:
        raise AIUnavailable(problem)


def run(database: Database, db: DbSession, task: str, user_id: int | None, req: Request) -> tuple[Reply, Route]:
    """Sends one request for a task through its route. Raises AIUnavailable (not sent) or ProviderError (sent, failed;
    logged)."""
    route = route_for(db, task, pictures=bool(req.pictures))
    check_cap(db, estimate(req, route.price))
    p = route.provider
    reply: Reply | None = None
    error: str | None = None
    try:
        reply = providers.complete(endpoint(p), route.model, req)
        return reply, route
    except ProviderError as e:
        reply, error = e.reply, e.message
        raise
    except Exception as e:
        error = f"failed ({type(e).__name__})"
        raise
    finally:
        tokens_in = reply.input_tokens if reply else 0
        tokens_out = reply.output_tokens if reply else 0
        with database.session() as log_db:
            log_db.add(
                AIUsage(
                    user_id=user_id,
                    task=task,
                    provider_id=p.id,
                    provider_kind=p.kind,
                    model=route.model,
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    cost_microdollars=microdollars(tokens_in, tokens_out, route.price),
                    priced=route.price[2],
                    ok=error is None,
                    error=error[:200] if error else None,
                )
            )
