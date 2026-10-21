"""The morning brief: its settings, today's brief for Home, and "write it now" to see what it will look like."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import alerts, brief, notify
from financeapp.ai import service
from financeapp.api.alert_routes import HHMM
from financeapp.api.deps import Signed, audit, settings_of, signed_in
from financeapp.models import Brief, now

router = APIRouter(prefix="/api/brief")
NOW_EVERY = timedelta(seconds=30)


def db_of(request: Request) -> DbSession:
    return request.state.db


def state(db: DbSession, request: Request) -> dict:
    prefs = brief.preferences(db)
    route, problem = service.find_route(db, brief.TASK)
    latest = db.scalar(select(Brief).order_by(Brief.day.desc()).limit(1))
    today = brief.local_now(db).date()
    return {
        **prefs,
        "timezone": alerts.quiet_hours(db)["timezone"],
        "ai": {"ready": route is not None, "model": route.model if route else None, "problem": problem},
        "channel": notify.configured(settings_of(request)),
        "today": brief.brief_json(latest) if latest and latest.day == today else None,
        "latest": brief.brief_json(latest) if latest else None,
    }


@router.get("")
def get_brief(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return {
            **brief.DEFAULTS,
            "timezone": "America/New_York",
            "ai": {"ready": False, "model": None, "problem": None},
            "channel": False,
            "today": None,
            "latest": None,
        }
    return state(db_of(request), request)


class BriefSettings(BaseModel):
    enabled: bool | None = None
    time: str | None = Field(default=None, pattern=HHMM)
    use_ai: bool | None = None


@router.put("/settings")
def set_settings(body: BriefSettings, request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    brief.set_preferences(db, **body.model_dump())
    audit(db, request, "brief_changed", who.user.id)
    return state(db, request)


@router.post("/now")
def write_now(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Writes today's brief now (replacing today's) and sends it, so the owner sees what mornings will bring."""
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    last = db.scalar(select(Brief.created_at).order_by(Brief.created_at.desc()).limit(1))
    if last and now() - last < NOW_EVERY:
        raise HTTPException(429, "One a moment, please. Try again in a few seconds.")
    b = brief.make(request.app.state.db, db, brief.local_now(db).date(), brief.preferences(db)["use_ai"])
    if b is None:
        raise HTTPException(422, "Add an account first: the brief is about your accounts.")
    brief.send(settings_of(request), db, b)
    return state(db, request)


@router.post("/{brief_id}/read")
def mark_read(brief_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return {"ok": True}
    b = db_of(request).get(Brief, brief_id)
    if b is None:
        raise HTTPException(404, "Not found")
    b.read_at = b.read_at or now()
    return {"ok": True}
