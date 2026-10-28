"""The morning brief: a few lines each morning on where the household stands, at a time the owner picks.

It is built from the same numbers as Home (household.py and budget.py), so it always matches the dashboard. The app
writes it itself; when AI is set up for "Writing the morning brief", the AI words it instead, from totals,
category names and tickers only (never names, merchants or accounts). Any dollar amount in the AI's wording that isn't
one of the brief's own numbers sends it back to the app's wording, so an AI can't make a number up.

The phone notification says the brief is ready, with no amounts (lock screens are public); the brief itself is on
Home, behind sign-in.
"""

import json
import logging
import re
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from financeapp import alerts, budget, household, notify, portfolio
from financeapp.ai import service
from financeapp.ai.providers import ProviderError, Request, parse_json
from financeapp.ai.service import AIUnavailable
from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import AlertEvent, Brief, Category, Preference, Transaction, now

log = logging.getLogger("financeapp")
PREF_KEY = "brief"
DEFAULTS = {"enabled": False, "time": "07:00", "use_ai": True}
TASK = "daily_brief"
LATE = timedelta(hours=3)  # after a long outage, a 7 AM brief isn't sent at 6 PM
MOVE_PERCENT = 2.0  # a holding's move worth a mention
NEAR_PERCENT = 90  # a budget line this close to its limit is worth a mention
MAX_POINTS = 6


def preferences(db: DbSession) -> dict:
    p = db.get(Preference, PREF_KEY)
    return {**DEFAULTS, **(p.value if p else {})}


def set_preferences(db: DbSession, **changes) -> dict:
    p = db.get(Preference, PREF_KEY)
    value = {**preferences(db), **{k: v for k, v in changes.items() if v is not None}}
    if p is None:
        db.add(Preference(key=PREF_KEY, value=value))
    else:
        p.value = value
    return value


def timezone_of(db: DbSession):
    return alerts.zone(alerts.quiet_hours(db)["timezone"])


def local_now(db: DbSession, t: datetime | None = None) -> datetime:
    return (t or now()).astimezone(timezone_of(db))


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars = abs(cents) / 100
    return f"{sign}${dollars:,.0f}" if dollars >= 100 else f"{sign}${dollars:,.2f}"


def signed_money(cents: int) -> str:
    return ("+" if cents > 0 else "") + money(cents)


# ---- the numbers


def facts(db: DbSession, day: date) -> dict | None:
    """Everything the brief can say, as plain numbers. None when there are no accounts yet."""
    h = household.build(db)
    if not h.get("linked"):
        return None
    history = h["net_worth"]["history"]
    net = h["net_worth"]["cents"]
    month_ago = next((p["cents"] for p in history if p["date"] >= (day - timedelta(days=30)).isoformat()), None)

    accounts = household.counted(db)
    yesterday = day - timedelta(days=1)
    categories = {c.id: c for c in db.scalars(select(Category))}
    by_category: dict[str, int] = {}
    for t in db.scalars(
        select(Transaction).where(
            Transaction.account_id.in_([a.id for a in accounts]),
            Transaction.date == yesterday,
            Transaction.transfer_group_id.is_(None),
        )
    ):
        c = categories.get(t.category_id) if t.category_id else None
        if c is not None and (c.kind in ("transfer", "income") or c.name == "Taxes"):
            continue
        name = c.name if c else "Uncategorized"
        by_category[name] = by_category.get(name, 0) - t.amount_cents
    spent_yesterday = sum(by_category.values())
    top = sorted(((k, v) for k, v in by_category.items() if v > 0), key=lambda kv: -kv[1])[:3]

    state = budget.build(db)
    over = [
        {"category": line["category"], "over_cents": line["spent_cents"] - line["limit_cents"]}
        for line in state["lines"]
        if line["limit_cents"] > 0 and line["spent_cents"] > line["limit_cents"]
    ]
    near = [
        {"category": line["category"], "percent": round(line["spent_cents"] / line["limit_cents"] * 100)}
        for line in state["lines"]
        if line["kind"] == "flexible"
        and line["limit_cents"] > 0
        and NEAR_PERCENT <= line["spent_cents"] / line["limit_cents"] * 100 <= 100
    ]
    safe = state["safe_to_spend"]

    positions = portfolio.positions(db, accounts)
    moves: dict[str, float] = {}
    for p in positions:
        if p.change_today_percent is not None and abs(p.change_today_percent) >= MOVE_PERCENT:
            moves[p.symbol] = round(p.change_today_percent, 1)
    movers = sorted(({"symbol": s, "percent": v} for s, v in moves.items()), key=lambda m: -abs(m["percent"]))[:3]

    paydays = sorted(s["next_date"] for s in state["schedules"])
    soon = [d for d in paydays if day.isoformat() <= d <= (day + timedelta(days=3)).isoformat()]
    alerts_count = db.scalar(select(func.count(AlertEvent.id)).where(AlertEvent.fired_at > now() - timedelta(days=1)))

    return {
        "day": day.isoformat(),
        "weekday": day.strftime("%A"),
        "net_worth_cents": net,
        "net_worth_change_1d_cents": h["net_worth"]["change_today_cents"],
        "net_worth_change_30d_cents": net - month_ago if month_ago is not None else None,
        "spent_yesterday_cents": spent_yesterday,
        "spent_yesterday_top": [{"category": k, "cents": v} for k, v in top],
        "spent_month_cents": h["spending"]["month_cents"],
        "spent_last_month_same_day_cents": h["spending"]["last_month_same_day_cents"],
        "safe_to_spend_cents": safe["cents"] if safe else None,
        "days_left": safe["days_left"] if safe else None,
        "per_day_cents": safe["cents"] // max(safe["days_left"], 1) if safe else None,
        "budget_over": over,
        "budget_near": near,
        "movers": movers,
        "payday_soon": soon[0] if soon else None,
        "alerts_last_day": alerts_count,
        "to_review": h["spending"]["needs_review"],
        "accounts_needing_attention": len(h["health"]["stale"]),
    }


# ---- the app's own wording


def written(f: dict) -> tuple[str, list[str]]:
    change = f["net_worth_change_1d_cents"]
    if f["budget_over"]:
        headline = f"{f['budget_over'][0]['category']} is over budget"
    elif f["safe_to_spend_cents"] is not None:
        headline = f"{money(f['safe_to_spend_cents'])} safe to spend for the rest of the month"
    elif f["spent_yesterday_cents"] > 0:
        headline = f"You spent {money(f['spent_yesterday_cents'])} yesterday"
    else:
        headline = f"A quiet {f['weekday']} for your money"
    points = []
    move = f" ({signed_money(change)} since yesterday)" if change else ""
    points.append(f"Net worth is {money(f['net_worth_cents'])}{move}.")
    if f["spent_yesterday_cents"] > 0:
        top = ", ".join(f"{t['category']} {money(t['cents'])}" for t in f["spent_yesterday_top"])
        points.append(f"Yesterday you spent {money(f['spent_yesterday_cents'])}" + (f": {top}." if top else "."))
    else:
        points.append("Nothing was spent yesterday.")
    last = f["spent_last_month_same_day_cents"]
    month = f["spent_month_cents"]
    if last:
        compare = "less than" if month < last else "more than" if month > last else "the same as"
        points.append(f"This month so far: {money(month)}, {compare} {money(last)} by this point last month.")
    if f["per_day_cents"] is not None and f["days_left"]:
        points.append(f"That leaves about {money(f['per_day_cents'])} a day for {f['days_left']} days.")
    for o in f["budget_over"][:2]:
        points.append(f"{o['category']} is {money(o['over_cents'])} over its budget.")
    if f["budget_near"]:
        names = ", ".join(n["category"] for n in f["budget_near"][:3])
        points.append(f"Close to the limit: {names}.")
    if f["movers"]:
        points.append("Big moves: " + ", ".join(f"{m['symbol']} {m['percent']:+.1f}%" for m in f["movers"]) + ".")
    if f["payday_soon"]:
        points.append(
            "Payday is "
            + ("today." if f["payday_soon"] == f["day"] else f"on {date.fromisoformat(f['payday_soon']):%A}.")
        )
    if f["accounts_needing_attention"]:
        points.append("An account needs signing in again to keep syncing.")
    return headline, points[:MAX_POINTS]


def lock_screen(f: dict) -> str:
    """The notification's line: no amounts, since anyone can read a lock screen."""
    if f["budget_over"]:
        return "Something is over budget. Open the app to see your morning brief."
    if f["accounts_needing_attention"]:
        return "An account needs attention. Your morning brief is ready."
    if f["safe_to_spend_cents"] is not None and f["safe_to_spend_cents"] > 0:
        return "You're on track this month. Your morning brief is ready."
    return "Your morning brief is ready."


# ---- the AI's wording

SYSTEM = (
    "You write a household's morning money brief. You get the morning's numbers as JSON (amounts in cents). Write a "
    "short headline (under 80 characters) and 3 to 6 points, each one plain sentence a person reads in a few "
    "seconds. Lead with what matters today: anything over budget, then safe to spend, then yesterday's spending, "
    "then net worth and big moves. Write dollar amounts like $1,234 (whole dollars from $100) or $12.50. Only use "
    "numbers that are in the JSON or that you get by dividing cents by 100; never add, estimate or invent any. Don't "
    "give advice, don't mention cents or JSON, and leave out anything that is null or empty."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "points"],
    "additionalProperties": False,
}


class Answer(BaseModel):
    headline: str = Field(min_length=3, max_length=120)
    points: list[str] = Field(min_length=1, max_length=8)


AMOUNT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def known_amounts(f: dict) -> set[int]:
    """Every amount in the facts, in cents, as the AI could legitimately write it (rounded or not)."""
    out: set[int] = set()

    def walk(v, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, int) and not isinstance(v, bool) and key.endswith("cents"):
            out.add(abs(v))
            out.add(round(abs(v), -2))  # whole dollars

    walk(f)
    return out


def amounts_check_out(text: str, f: dict) -> bool:
    known = known_amounts(f)
    for m in AMOUNT.finditer(text):
        cents = round(float(m.group(1).replace(",", "")) * 100)
        if cents not in known and round(cents, -2) not in {round(k, -2) for k in known}:
            return False
    return True


def ai_written(database: Database, db: DbSession, f: dict) -> tuple[str, list[str], str] | None:
    """The AI's wording, or None (not set up, failed, or it used a number that isn't in the facts)."""
    route, _ = service.find_route(db, TASK)
    if route is None:
        return None
    req = Request(system=SYSTEM, text=json.dumps(f), pictures=[], schema=SCHEMA, max_tokens=800)
    try:
        reply, route = service.run(database, db, TASK, None, req)
        answer = Answer.model_validate(parse_json(reply.text))
    except (AIUnavailable, ProviderError, ValidationError) as e:
        log.info("morning brief: AI wording skipped (%s)", type(e).__name__)
        return None
    points = [p.strip() for p in answer.points if p.strip()][:MAX_POINTS]
    if not all(amounts_check_out(t, f) for t in [answer.headline, *points]):
        log.warning("morning brief: the AI used an amount that isn't in the numbers; using the app's wording")
        return None
    return answer.headline.strip()[:200], [p[:300] for p in points], route.model


# ---- making and sending


def make(database: Database, db: DbSession, day: date, use_ai: bool) -> Brief | None:
    """Writes the brief for `day`, replacing one already written that day. None when there are no accounts yet."""
    f = facts(db, day)
    if f is None:
        return None
    headline, points = written(f)
    by = "app"
    if use_ai and (ai := ai_written(database, db, f)) is not None:
        headline, points, by = ai
    b = db.scalar(select(Brief).where(Brief.day == day))
    if b is None:
        b = Brief(day=day)
        db.add(b)
    b.facts, b.headline, b.points, b.written_by, b.read_at, b.created_at = (
        f,
        headline,
        {"items": points},
        by,
        None,
        now(),
    )
    db.flush()
    return b


def due(db: DbSession, t: datetime | None = None) -> date | None:
    """Today's date (in the household's time zone) when it's time for a brief that hasn't been written yet."""
    prefs = preferences(db)
    if not prefs["enabled"]:
        return None
    local = local_now(db, t)
    at = datetime.combine(local.date(), time.fromisoformat(prefs["time"]), local.tzinfo)
    if not (at <= local < at + LATE):
        return None
    if db.scalar(select(Brief.id).where(Brief.day == local.date())) is not None:
        return None
    return local.date()


def send(settings: Settings, db: DbSession, b: Brief) -> str | None:
    click = notify.app_link(settings, "/")
    channel = notify.deliver(settings, "Morning brief", lock_screen(b.facts), tags="sunrise", priority="default",
                             click=click)  # fmt: skip
    b.channel = channel
    return channel


def run(database: Database, settings: Settings, t: datetime | None = None) -> Brief | None:
    """The scheduler's check: writes and sends today's brief when it's time."""
    with database.session() as db:
        day = due(db, t)
        if day is None:
            return None
        b = make(database, db, day, preferences(db)["use_ai"])
        if b is None:
            return None
        send(settings, db, b)
        log.info("morning brief written (%s) and %s", b.written_by, f"sent by {b.channel}" if b.channel else "not sent")
        return b


def brief_json(b: Brief) -> dict:
    return {
        "id": b.id,
        "day": b.day.isoformat(),
        "headline": b.headline,
        "points": b.points.get("items", []),
        "written_by": b.written_by,
        "channel": b.channel,
        "at": b.created_at.isoformat(),
        "read": b.read_at is not None,
    }
