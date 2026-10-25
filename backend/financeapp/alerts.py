"""Alert rules: price, dip, budget, concentration and goal milestones. Checked after every price update and every sync,
sent by ntfy push with email as the fallback (notify.py).

Rules and their params
  price          {"symbol": "VOO", "above": 600} or {"symbol": "VOO", "below": 500}: the price crosses a line;
                 {"symbol": "VOO", "move_percent": 3}: a day's move of at least 3% either way
  dip            {"symbol": "VOO", "from_high_percent": 10}: the price is 10% or more under its 52-week high, and
                 again at 20%, 30%...; {"symbol": "VOO", "below_average": 200}: the price drops under its 200-day
                 (or 50-day) average. symbol null means every stock and fund held. Facts about the price, worded
                 as information: the app never says whether to buy.
  budget         {"category_id": 12, "percent": 90}: a category reaches 90% of its monthly limit; category_id null
                 means the whole everyday budget
                 {"category_id": null, "forecast": true}: a heads-up when a category is on pace to go over its limit
                 by the end of the month (forecast.py); category_id null means each category on its own
  concentration  {"percent": 10}: any one company reaches 10% of everything invested, counting what's inside funds
                 (look-through, when the fund's holdings are known)
  goal           {"goal_id": 3}: a savings goal reaches 25%, 50%, 75% and 100% of its target (goals.py); goal_id
                 null means every goal. Only the furthest milestone reached sends anything, and a goal changed in
                 the app (a new target, an account added) never sends one for the change itself.

No repeats. A rule fires when its condition becomes true and re-arms only after it has clearly stopped being true:
a price back across its line by 1%, a company back under its limit by a point, a dip back within 3 points of
the 52-week high's line, a goal back 10 points under a milestone. A budget alert can fire once a
month (a heads-up once a month per category) and a daily move once a day. Every alert carries a dedup key that
the database keeps unique, so even two copies of the app checking at once can't save or send the same alert twice.

Quiet hours (10 PM to 7 AM by default, in the household's time zone) hold alerts back, and when they end
everything held goes out as one message. Security alerts (a new sign-in, a broken connection) never wait: they
don't come through here.

How loud. Each rule picks a priority, sent to ntfy as is: high (the default) shows a banner and, on an iPhone,
comes through as Time Sensitive so a Focus mode can let it in; default shows a normal banner that a Focus can hold
back; low arrives without a sound or banner. Several alerts at once go at the loudest of their priorities, except
that what was held over quiet hours goes out no louder than default, so the morning roundup doesn't break through
a Sleep Focus.

Upload reminders (reminders.py) come through the same queue without a rule, at default priority.

Lock screens. Messages name a market price, a share of the budget or of the portfolio, never balances or dollar
amounts of the household's own money.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from financeapp import notify, quotes
from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import AlertEvent, AlertRule, AuditEvent, Preference, now

log = logging.getLogger("financeapp")
REARM_PRICE = 0.01  # back across the line by 1%
REARM_POINTS = 1.0  # back under a concentration limit by 1 point
REARM_GOAL = 10.0  # back 10 points under a goal milestone
REARM_DIP = 3.0  # back within 7% of the 52-week high, after a "10% under" alert
DIP_MAX = 90  # the deepest "under its 52-week high" step
TREND_DAYS = 7  # a dip is only judged on a price and trend from the last week
NOT_ADVICE = "Information only, not advice."
MAX_ATTEMPTS = 3
MILESTONES = (25, 50, 75, 100)
KEEP_DAYS = 180
PRIORITIES = ("low", "default", "high")  # quietest first
DEFAULT_PRIORITY = "high"
QUIET_DEFAULTS = {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "America/New_York"}


@dataclass
class Hit:
    """One thing a rule watches: `key` names it (a month, a day, a symbol), `on` says whether the condition holds,
    `rearm` whether it has clearly stopped holding."""

    key: str
    on: bool
    rearm: bool
    title: str = ""
    body: str = ""
    silent: bool = False  # becomes true without an alert (a smaller step of a dip passed on the way down)


def money(dollars: float) -> str:
    return f"${dollars:,.2f}"


def percent(p: float) -> str:
    return f"{p:.1f}%".replace(".0%", "%")


# ---- quiet hours


def quiet_hours(db: DbSession) -> dict:
    p = db.get(Preference, "alerts")
    return {**QUIET_DEFAULTS, **(p.value if p else {})}


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def in_quiet_hours(q: dict, t: datetime) -> bool:
    if not q.get("enabled"):
        return False
    local = t.astimezone(zone(q["timezone"])).time()
    start, end = time.fromisoformat(q["start"]), time.fromisoformat(q["end"])
    if start == end:
        return False
    return start <= local < end if start < end else (local >= start or local < end)


# ---- conditions


def price_hits(rule: AlertRule, quote, t: datetime) -> list[Hit]:
    p = rule.params
    s = p["symbol"]
    if quote is None:
        return []
    price = float(quote.price)
    if "above" in p:
        line = float(p["above"])
        return [Hit("", price >= line, price < line * (1 - REARM_PRICE), f"{s} is above {money(line)}",
                    f"{s} is at {money(price)}.")]  # fmt: skip
    if "below" in p:
        line = float(p["below"])
        return [Hit("", price <= line, price > line * (1 + REARM_PRICE), f"{s} is below {money(line)}",
                    f"{s} is at {money(price)}.")]  # fmt: skip
    if "move_percent" in p and quote.previous_close:
        day = quote.as_of.astimezone(quotes.ET).date()
        if day != t.astimezone(quotes.ET).date():
            return []  # yesterday's move isn't news
        change = (price - float(quote.previous_close)) / float(quote.previous_close) * 100
        word = "up" if change > 0 else "down"
        return [Hit(day.isoformat(), abs(change) >= float(p["move_percent"]), False,
                    f"{s} is {word} {percent(abs(change))} today", f"{s} is at {money(price)}.")]  # fmt: skip
    return []


def dip_hits(rule: AlertRule, live: dict, held: set[str], t: datetime) -> list[Hit]:
    p = rule.params
    symbols = [p["symbol"]] if p.get("symbol") else sorted(held)
    keys = (rule.state or {}).get("keys", {})
    hits = []
    for s in symbols:
        q = live.get(s)
        if q is None or q.trend_as_of is None or t - q.as_of > timedelta(days=TREND_DAYS):
            continue
        price = float(q.price)
        if "from_high_percent" in p and q.year_high:
            high = float(q.year_high)
            under = (high - price) / high * 100
            step = float(p["from_high_percent"])
            steps = [step * i for i in range(1, int(DIP_MAX // step) + 1)]
            deepest = max((x for x in steps if under >= x), default=None)
            for x in steps:
                key = f"{s}:{x:g}"
                # Only the deepest step reached sends anything: a fall straight through 10% and 20% is one alert.
                silent = deepest is not None and x < deepest and not keys.get(key, {}).get("on")
                hits.append(
                    Hit(key, under >= x, under < x - REARM_DIP,
                        f"{s} is {percent(round(under))} under its 52-week high",
                        f"{s} is at {money(price)}; its 52-week high is {money(high)}. {NOT_ADVICE}", silent)
                )  # fmt: skip
        elif "below_average" in p:
            days = int(p["below_average"])
            avg = q.average_200 if days == 200 else q.average_50
            if not avg:
                continue
            line = float(avg)
            under = (line - price) / line * 100
            hits.append(
                Hit(s, price < line, price > line * (1 + REARM_PRICE), f"{s} dropped under its {days}-day average",
                    f"{s} is at {money(price)}, {percent(max(under, 0.1))} under its {days}-day average of "
                    f"{money(line)}. {NOT_ADVICE}")
            )  # fmt: skip
    return hits


def budget_hits(rule: AlertRule, state: dict, today: date) -> list[Hit]:
    p = rule.params
    lines = [line for line in state["lines"] if line["limit_cents"] > 0]
    if p.get("category_id") is None:
        lines = [line for line in lines if line["kind"] == "flexible"]
        name, verb = "Everyday spending", "is"
    else:
        lines = [line for line in lines if line["category_id"] == p["category_id"]]
        name, verb = (lines[0]["category"], "is") if lines else ("", "")
    if not lines:
        return []
    limit = sum(line["limit_cents"] for line in lines)
    spent = sum(line["spent_cents"] for line in lines)
    used = spent / limit * 100
    month = today.replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    left = (next_month - today).days
    title = f"{name} {verb} over budget" if used > 100 else f"{name} {verb} at {percent(min(used, 100))} of its budget"
    days = f"{left} day{'s' if left != 1 else ''} left in {month.strftime('%B')}."
    return [Hit(month.isoformat()[:7], used >= float(p["percent"]), False, title, days)]


def forecast_hits(rule: AlertRule, state: dict, today: date) -> list[Hit]:
    """One hit per category: on when it's heading over (not already over: the plain budget rule says that)."""
    cid = rule.params.get("category_id")
    month = today.replace(day=1)
    hits = []
    for line in state["lines"]:
        f = line.get("forecast")
        if not f or line["limit_cents"] <= 0 or (cid is not None and line["category_id"] != cid):
            continue
        share = f["projected_cents"] / line["limit_cents"] * 100 - 100
        left = f["days_left"]
        hits.append(
            Hit(f"{month.isoformat()[:7]}:{line['category_id']}", f["status"] == "heading_over", False,
                f"{line['category']} is on pace to go over budget",
                f"At this pace it ends {month.strftime('%B')} about {percent(max(round(share), 1))} over. "
                f"{left} day{'s' if left != 1 else ''} left to slow down.")
        )  # fmt: skip
    return hits


def concentration_hits(rule: AlertRule, companies: list[dict]) -> list[Hit]:
    limit = float(rule.params["percent"])
    hits = []
    for c in companies:
        label = c["symbol"] or c["name"]
        through = " counting your funds" if any(s["symbol"] != "direct" for s in c["sources"]) else ""
        hits.append(
            Hit(c["key"], c["percent"] >= limit, c["percent"] < limit - REARM_POINTS,
                f"{label} is {percent(c['percent'])} of your portfolio",
                f"That's over your {percent(limit)} limit for one company{through}.")
        )  # fmt: skip
    return hits


MILESTONE_WORDS = {25: "A quarter of the way.", 50: "Halfway there.", 75: "Three quarters of the way."}


def goal_hits(rule: AlertRule, goals: list[dict], silent: bool = False) -> list[Hit]:
    """One hit per goal and milestone. `silent` (after a change made in the app) marks what's reached without
    sending anything, so typing a smaller target doesn't announce a milestone."""
    gid = rule.params.get("goal_id")
    keys = (rule.state or {}).get("keys", {})
    hits = []
    for g in goals:
        if (gid is not None and g["id"] != gid) or g["percent"] is None:
            continue
        pct = g["percent"]
        furthest = max((m for m in MILESTONES if pct >= m), default=None)
        for m in MILESTONES:
            key = f"{g['id']}:{m}"
            quiet = silent or (furthest is not None and m < furthest and not keys.get(key, {}).get("on"))
            if m == 100:
                title, body = f"{g['name']}: goal reached", "Everything you set out to save is there."
            else:
                title = f"{g['name']} is {m}% of the way there"
                body = MILESTONE_WORDS[m]
                if g.get("projected_month"):
                    body += f" On pace for {date.fromisoformat(g['projected_month']).strftime('%B %Y')}."
            hits.append(Hit(key, pct >= m, pct < m - REARM_GOAL, title, body, quiet))
    return hits


def settle_goals(db: DbSession, goals: list[dict]) -> None:
    """After a goal is added or changed in the app: what it has reached now counts as reached, without an alert."""
    for r in db.scalars(select(AlertRule).where(AlertRule.kind == "goal")):
        fire(db, r, goal_hits(r, goals, silent=True))


# ---- firing


def fire(db: DbSession, rule: AlertRule, hits: list[Hit]) -> int:
    """Applies hits to the rule's state; saves an alert for every condition that has just become true."""
    keys = dict((rule.state or {}).get("keys", {}))
    fired = 0
    for h in hits:
        k = keys.get(h.key, {"on": False, "n": 0})
        if k["on"] and h.rearm:
            k = {"on": False, "n": k["n"]}
        elif not k["on"] and h.on and h.silent:
            k = {"on": True, "n": k["n"]}
        elif not k["on"] and h.on:
            k = {"on": True, "n": k["n"] + 1}
            stmt = insert(AlertEvent).values(
                rule_id=rule.id,
                kind=rule.kind,
                title=h.title[:120],
                body=h.body[:300],
                dedup_key=f"rule:{rule.id}:{h.key}:{k['n']}"[:160],
                fired_at=now(),
                status="pending",
                attempts=0,
            )
            if db.execute(stmt.on_conflict_do_nothing(index_elements=["dedup_key"])).rowcount:
                fired += 1
        keys[h.key] = k
    if rule.kind == "goal":  # a goal that's gone
        keys = {k: v for k, v in keys.items() if k in {h.key for h in hits}}
    # Only the most recent keys matter for budgets and daily moves; keep the state small.
    if rule.kind in ("price", "budget") and len(keys) > 40:
        keys = dict(sorted(keys.items())[-40:])
    rule.state = {"keys": keys}
    return fired


def evaluate(db: DbSession, kinds: set[str], companies: list[dict] | None = None, budget_state: dict | None = None,
             t: datetime | None = None, goals: list[dict] | None = None) -> int:  # fmt: skip
    """Checks every enabled rule of these kinds. Budget, concentration and goal rules need what they look at passed in
    (built once by the caller), so a check with nothing to look at is skipped rather than seen as 'all clear'."""
    t = t or now()
    rules = list(db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.kind.in_(kinds))))
    held = quotes.holding_symbols(db) if any(r.kind == "dip" and not r.params.get("symbol") for r in rules) else set()
    watched = {r.params.get("symbol") for r in rules if r.kind in ("price", "dip")} | held
    live = quotes.latest(db, {s for s in watched if s})
    fired = 0
    for r in rules:
        if r.kind == "price":
            hits = price_hits(r, live.get(r.params.get("symbol")), t)
        elif r.kind == "dip":
            hits = dip_hits(r, live, held, t)
        elif r.kind == "budget" and budget_state is not None:
            day = t.astimezone(zone(quiet_hours(db)["timezone"])).date()
            hits = (forecast_hits if r.params.get("forecast") else budget_hits)(r, budget_state, day)
        elif r.kind == "concentration" and companies is not None:
            hits = concentration_hits(r, companies)
        elif r.kind == "goal" and goals is not None:
            hits = goal_hits(r, goals)
        else:
            continue
        fired += fire(db, r, hits)
    return fired


# ---- sending


def priority_of(rule: AlertRule | None, kind: str | None = None) -> str:
    if rule is None and kind == "upload":
        return "default"  # an upload reminder (reminders.py) is a to-do, not news: a Focus mode can hold it
    p = (rule.params or {}).get("priority") if rule is not None else None
    return p if p in PRIORITIES else DEFAULT_PRIORITY


def loudest(db: DbSession, events: list[AlertEvent]) -> str:
    ids = {e.rule_id for e in events if e.rule_id is not None}
    rules = {r.id: r for r in db.scalars(select(AlertRule).where(AlertRule.id.in_(ids)))} if ids else {}
    p = max((priority_of(rules.get(e.rule_id), e.kind) for e in events), key=PRIORITIES.index)
    if any(e.status == "held" for e in events):
        p = min(p, "default", key=PRIORITIES.index)
    return p


def send_pending(db: DbSession, settings: Settings, t: datetime | None = None) -> int:
    """Sends what's waiting, unless it's quiet hours (then it waits, marked held). Several at once go as one
    message. Returns how many alerts went out."""
    t = t or now()
    waiting = list(
        db.scalars(select(AlertEvent).where(AlertEvent.status.in_(["pending", "held"])).order_by(AlertEvent.fired_at))
    )
    if not waiting:
        return 0
    if not notify.configured(settings):
        for e in waiting:
            e.status = "unsent"  # still shown in the app
        return 0
    if in_quiet_hours(quiet_hours(db), t):
        for e in waiting:
            e.status = "held"
        return 0
    if len(waiting) == 1:
        title, body = waiting[0].title, waiting[0].body
    else:
        title = f"{len(waiting)} alerts"
        body = "\n".join(f"• {e.title}" for e in waiting[:8])
        if len(waiting) > 8:
            body += f"\n…and {len(waiting) - 8} more in the app."
    channel = notify.deliver(settings, title, body, tags="bell", priority=loudest(db, waiting))
    for e in waiting:
        e.attempts += 1
        if channel:
            e.status, e.channel, e.sent_at = "sent", channel, t
        elif e.attempts >= MAX_ATTEMPTS:
            e.status = "failed"
        else:
            e.status = "pending"
    db.add(AuditEvent(action="alert_sent" if channel else "alert_failed", detail={"count": len(waiting),
                                                                                  "channel": channel}))  # fmt: skip
    return len(waiting) if channel else 0


def check(database: Database, settings: Settings, kinds: set[str]) -> int:
    """Checks the rules of these kinds against current numbers, then sends. Used by the scheduler and after a sync.
    Returns how many new alerts there were."""
    from financeapp import budget, goals, household, lookthrough

    with database.session() as db:
        companies = None
        state = None
        progress = None
        if "concentration" in kinds:
            companies = lookthrough.exposure(db, household.counted(db))["companies"]
        if "budget" in kinds:
            state = budget.build(db)
        if "goal" in kinds:
            progress = goals.build(db)["goals"]
        fired = evaluate(db, kinds, companies=companies, budget_state=state, goals=progress)
    with database.session() as db:
        send_pending(db, settings)
        db.execute(delete(AlertEvent).where(AlertEvent.fired_at < now() - timedelta(days=KEEP_DAYS)))
    if fired:
        log.info("%d new alert%s", fired, "" if fired == 1 else "s")
    return fired


def event_json(e: AlertEvent) -> dict:
    return {
        "id": str(e.id),
        "kind": e.kind,
        "title": e.title,
        "body": e.body,
        "at": e.fired_at.astimezone(UTC).isoformat(),
        "status": e.status,
        "channel": e.channel,
        "read": e.read_at is not None,
    }
