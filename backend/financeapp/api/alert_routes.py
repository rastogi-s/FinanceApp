"""Alert rules, quiet hours, the alerts that fired, and a test message to check the phone gets them."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from financeapp import alerts, budget, demo, lookthrough, notify, quotes
from financeapp.api.deps import Signed, audit, settings_of, signed_in
from financeapp.models import AlertEvent, AlertRule, Category, Preference, now

router = APIRouter(prefix="/api")
MAX_RULES = 100
TEST_EVERY = timedelta(seconds=30)
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


class Rule(BaseModel):
    # How hard the phone should try to get attention (see alerts.PRIORITIES). Not part of what the rule watches,
    # so changing it doesn't let the rule fire again.
    priority: Literal["high", "default", "low"] = alerts.DEFAULT_PRIORITY

    def stored(self) -> dict:
        return {**self.params(), "priority": self.priority}  # type: ignore[attr-defined]


class PriceRule(Rule):
    kind: Literal["price"]
    symbol: str = Field(min_length=1, max_length=12)
    above: float | None = Field(default=None, gt=0, lt=10_000_000)
    below: float | None = Field(default=None, gt=0, lt=10_000_000)
    move_percent: float | None = Field(default=None, ge=0.5, le=50)

    @model_validator(mode="after")
    def one_condition(self):
        self.symbol = self.symbol.strip().upper()
        if not quotes.TICKER.match(self.symbol):
            raise ValueError("That doesn't look like a ticker symbol.")
        if sum(v is not None for v in (self.above, self.below, self.move_percent)) != 1:
            raise ValueError("Pick one: above a price, below a price, or a big daily move.")
        return self

    def params(self) -> dict:
        p: dict = {"symbol": self.symbol}
        for k in ("above", "below", "move_percent"):
            v = getattr(self, k)
            if v is not None:
                p[k] = round(v, 4)
        return p


class DipRule(Rule):
    kind: Literal["dip"]
    symbol: str | None = Field(default=None, max_length=12)  # None: every stock and fund held
    from_high_percent: float | None = Field(default=None, ge=2, le=50)
    below_average: Literal[50, 200] | None = None

    @model_validator(mode="after")
    def one_condition(self):
        if self.symbol is not None:
            self.symbol = self.symbol.strip().upper() or None
        if self.symbol is not None and not quotes.TICKER.match(self.symbol):
            raise ValueError("That doesn't look like a ticker symbol.")
        if (self.from_high_percent is None) == (self.below_average is None):
            raise ValueError("Pick one: under the 52-week high, or under an average.")
        return self

    def params(self) -> dict:
        if self.from_high_percent is not None:
            return {"symbol": self.symbol, "from_high_percent": round(self.from_high_percent, 1)}
        return {"symbol": self.symbol, "below_average": self.below_average}


class BudgetRule(Rule):
    kind: Literal["budget"]
    category_id: int | None = None  # None: the whole everyday budget (for a heads-up: every category on its own)
    percent: int = Field(default=90, ge=10, le=200)
    forecast: bool = False  # a heads-up when it's on pace to go over by month-end, instead of a percent

    def params(self) -> dict:
        if self.forecast:
            return {"category_id": self.category_id, "forecast": True}
        return {"category_id": self.category_id, "percent": self.percent}


class ConcentrationRule(Rule):
    kind: Literal["concentration"]
    percent: float = Field(ge=1, le=100)

    def params(self) -> dict:
        return {"percent": round(self.percent, 1)}


RuleIn = Annotated[PriceRule | DipRule | BudgetRule | ConcentrationRule, Field(discriminator="kind")]


class RuleBody(BaseModel):
    rule: RuleIn
    enabled: bool = True


class EnabledIn(BaseModel):
    enabled: bool


class QuietIn(BaseModel):
    enabled: bool
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)
    timezone: str = Field(max_length=64)

    @model_validator(mode="after")
    def known_zone(self):
        if alerts.zone(self.timezone).key != self.timezone:
            raise ValueError("Unknown time zone.")
        return self


def describe(r: AlertRule, categories: dict[int, str]) -> str:
    p = r.params
    if r.kind == "price":
        if "above" in p:
            return f"{p['symbol']} goes above {alerts.money(p['above'])}"
        if "below" in p:
            return f"{p['symbol']} goes below {alerts.money(p['below'])}"
        return f"{p['symbol']} moves {alerts.percent(p['move_percent'])} or more in a day"
    if r.kind == "dip":
        who = p.get("symbol") or "Anything you hold"
        if "from_high_percent" in p:
            step = alerts.percent(p["from_high_percent"])
            return f"{who} falls {step} under its 52-week high, and each {step} more"
        return f"{who} drops under its {p['below_average']}-day average"
    if r.kind == "budget" and p.get("forecast"):
        if p.get("category_id") is None:
            return "Any category is on pace to go over its budget"
        return f"{categories.get(p['category_id'], 'A category')} is on pace to go over its budget"
    if r.kind == "budget":
        name = "Everyday spending" if p.get("category_id") is None else categories.get(p["category_id"], "A category")
        return f"{name} reaches {p['percent']}% of its budget"
    return f"Any one company reaches {alerts.percent(p['percent'])} of your portfolio"


def rule_json(r: AlertRule, categories: dict[int, str]) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "params": {k: v for k, v in r.params.items() if k != "priority"},
        "enabled": r.enabled,
        "priority": alerts.priority_of(r),
        "summary": describe(r, categories),
        # Fired and not re-armed yet: what the app shows as "Triggered".
        "triggered": any(k.get("on") for k in (r.state or {}).get("keys", {}).values()) and r.kind == "price",
    }


def state(db: DbSession, request: Request) -> dict:
    settings = settings_of(request)
    categories = dict(db.execute(select(Category.id, Category.name)).all())
    rules = db.scalars(select(AlertRule).order_by(AlertRule.kind, AlertRule.id)).all()
    events = db.scalars(select(AlertEvent).order_by(AlertEvent.fired_at.desc()).limit(50)).all()
    held = sorted({s for s, c in lookthrough.held(db) if quotes.quotable(s, c)})
    return {
        "rules": [rule_json(r, categories) for r in rules],
        "events": [alerts.event_json(e) for e in events],
        "quiet": alerts.quiet_hours(db),
        "channels": {"ntfy": bool(settings.ntfy_url), "email": bool(settings.smtp_host and settings.alert_email)},
        "delivery": notify.last_attempt(),
        "symbols": held,
        "categories": [
            {"id": c.id, "name": c.name}
            for c in db.scalars(
                select(Category).where(Category.kind == "flexible").order_by(Category.sort, Category.name)
            )
        ],
    }


@router.get("/alerts")
def get_alerts(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.alerts()
    return state(db_of(request), request)


def check_category(db: DbSession, rule: RuleIn) -> None:
    if isinstance(rule, BudgetRule) and rule.category_id is not None:
        c = db.get(Category, rule.category_id)
        if c is None or c.kind not in budget.BUDGETED:
            raise HTTPException(422, "Pick a bill or everyday category.")


def recheck(request: Request, db: DbSession, rule: AlertRule) -> None:
    """Looks at a new or changed price or dip rule right away, so it doesn't wait for the next round of prices."""
    if rule.kind in ("price", "dip") and rule.enabled:
        db.flush()
        alerts.evaluate(db, {rule.kind})
        alerts.send_pending(db, settings_of(request))


@router.post("/alerts/rules")
def add_rule(body: RuleBody, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    if len(db.scalars(select(AlertRule.id)).all()) >= MAX_RULES:
        raise HTTPException(422, f"That's {MAX_RULES} rules already. Remove one first.")
    check_category(db, body.rule)
    r = AlertRule(
        kind=body.rule.kind, params=body.rule.stored(), enabled=body.enabled, state={}, created_by=who.user.id
    )
    db.add(r)
    db.flush()
    audit(db, request, "alert_rule_added", who.user.id, rule=r.id, kind=r.kind)
    recheck(request, db, r)
    return state(db, request)


@router.put("/alerts/rules/{rule_id}")
def change_rule(rule_id: int, body: RuleBody, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(404, "That rule is gone.")
    check_category(db, body.rule)
    watched = {k: v for k, v in r.params.items() if k != "priority"}
    if (r.kind, watched) != (body.rule.kind, body.rule.params()):
        r.state = {}  # a different rule now: it can fire again
    r.kind, r.params, r.enabled = body.rule.kind, body.rule.stored(), body.enabled
    audit(db, request, "alert_rule_changed", who.user.id, rule=r.id, kind=r.kind)
    recheck(request, db, r)
    return state(db, request)


@router.patch("/alerts/rules/{rule_id}")
def toggle_rule(rule_id: int, body: EnabledIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(404, "That rule is gone.")
    r.enabled = body.enabled
    audit(db, request, "alert_rule_changed", who.user.id, rule=r.id, enabled=body.enabled)
    return state(db, request)


@router.delete("/alerts/rules/{rule_id}")
def remove_rule(rule_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(404, "That rule is gone.")
    db.delete(r)
    audit(db, request, "alert_rule_removed", who.user.id, rule=rule_id, kind=r.kind)
    return state(db, request)


@router.put("/alerts/quiet")
def set_quiet(body: QuietIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    p = db.get(Preference, "alerts") or Preference(key="alerts", value={})
    p.value = {**p.value, **body.model_dump()}
    db.merge(p)
    audit(db, request, "quiet_hours_changed", who.user.id, enabled=body.enabled)
    return state(db, request)


@router.post("/alerts/read")
def mark_read(request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    db.execute(update(AlertEvent).where(AlertEvent.read_at.is_(None)).values(read_at=now()))
    return {"ok": True}


@router.post("/alerts/test")
def send_test(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Sends a test message right away (quiet hours don't apply), at most every 30 seconds."""
    no_demo(who)
    db = db_of(request)
    settings = settings_of(request)
    if not notify.configured(settings):
        raise HTTPException(422, "No alert channel is set up on the server yet.")
    p = db.get(Preference, "alert_test") or Preference(key="alert_test", value={})
    last = p.value.get("at")
    if last and now() - datetime.fromisoformat(last) < TEST_EVERY:
        raise HTTPException(429, "A test just went out. Give it a few seconds.")
    p.value = {"at": now().isoformat()}
    db.merge(p)
    channel = notify.deliver(settings, "Test alert", "Alerts from your Finance app reach this phone.", tags="bell")
    audit(db, request, "alert_test", who.user.id, channel=channel)
    if channel is None:
        reason = (notify.last_attempt() or {}).get("reason")
        raise HTTPException(502, reason or "Couldn't send it. Check NTFY_URL (or the email settings) in .env.")
    return {"channel": channel}
