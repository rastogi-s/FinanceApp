"""The Plan page (projection, financial independence, ideas) and the setup guide."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from financeapp import demo, guide, household, plan
from financeapp.api.deps import Signed, audit, settings_of, signed_in
from financeapp.models import Preference
from financeapp.sync import today

router = APIRouter(prefix="/api")
MAX_CENTS = 100_000_000_00  # $100M a year: anything bigger is a typo
DAILY_UP_TO = 120  # a longer history is sent one point a week, which is plenty for a phone-wide chart


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


def thin(points: list[dict]) -> list[dict]:
    if len(points) <= DAILY_UP_TO:
        return points
    # Every 7th day counted back from today, so the newest point is always there.
    return points[::-1][::7][::-1]


def build(db: DbSession) -> dict:
    h, inputs = household.build_with_plan(db)
    if inputs is None:
        return {"demo": False, "linked": False}
    on = today()
    history = household.net_worth_history(db, household.counted(db), on, days=plan.MAX_YEARS * 366)
    if history:
        history[-1] = {**history[-1], "cents": h["net_worth"]["cents"]}  # at today's live prices, like Home
    return {
        "demo": False,
        "linked": True,
        "net_worth_cents": h["net_worth"]["cents"],
        "history": thin(history),
        "inputs": inputs,
        "projection": h["projection"],
        "ideas": plan.ideas(db, on),
    }


@router.get("/plan")
def get_plan(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.plan_page()
    return build(db_of(request))


class SpendingIn(BaseModel):
    lean: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    regular: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    fat: int | None = Field(default=None, ge=0, le=MAX_CENTS)


class PlanIn(BaseModel):
    """Only the fields sent change; null clears a typed-in number (back to the one the app works out)."""

    withdrawal_rate: float | None = Field(default=None, ge=2.5, le=6)
    level: Literal["lean", "regular", "fat"] | None = None
    birth_year: int | None = Field(default=None, ge=1920, le=2020)
    savings_cents: int | None = Field(default=None, ge=-MAX_CENTS // 12, le=MAX_CENTS // 12)
    return_mid: float | None = Field(default=None, ge=1, le=10)
    spending: SpendingIn | None = None


@router.patch("/plan/settings")
def plan_settings(body: PlanIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "spending"}
    for key in ("withdrawal_rate", "level"):
        if key in changes and changes[key] is None:
            del changes[key]  # these always have a value
    if body.spending is not None:
        changes["spending"] = {k: getattr(body.spending, k) for k in body.spending.model_fields_set}
    plan.set_preferences(db, changes)
    audit(db, request, "plan_changed", who.user.id, fields=sorted(changes))
    return build(db)


# ---- setup guide


@router.get("/guide")
def get_guide(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return guide.demo_state()
    return guide.state(db_of(request), settings_of(request), who.user)


class GuideIn(BaseModel):
    hidden: bool


@router.put("/guide")
def set_guide(body: GuideIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Hides the guide from Home (it stays in Settings), or brings it back."""
    no_demo(who)
    db = db_of(request)
    db.merge(Preference(key="guide", value={"hidden": body.hidden}))
    db.flush()
    return guide.state(db, settings_of(request), who.user)
