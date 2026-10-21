"""Savings goals: add, change and remove them, and pick the accounts (or amounts of them) that fund each."""

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from financeapp import alerts, budget, demo, goals, household
from financeapp.api.deps import Signed, audit, signed_in
from financeapp.models import Goal, GoalFunding
from financeapp.sync import today

router = APIRouter(prefix="/api")
MAX_GOALS = 30
MAX_CENTS = 100_000_000_00  # $100M: anything bigger is a typo
MAX_YEARS = 60


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


class FundingIn(BaseModel):
    account_id: int
    cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)  # None: the rest of the account


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    icon: str = "star"
    color: str = "teal"
    priority: Literal[1, 2, 3] = 2
    # Required for a new goal; for the emergency fund, None means months of essentials.
    target_cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)
    target_date: datetime.date | None = None
    rate_percent: float | None = Field(default=None, ge=0, le=15)
    monthly_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    funding: list[FundingIn] = Field(default_factory=list, max_length=20)
    emergency_months: int | None = Field(default=None, ge=1, le=36)  # the emergency fund only

    @model_validator(mode="after")
    def tidy(self):
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Give the goal a name.")
        if self.icon not in goals.ICONS:
            self.icon = "star"
        if self.color not in goals.COLORS:
            self.color = "teal"
        if len({f.account_id for f in self.funding}) != len(self.funding):
            raise ValueError("An account is in the list twice.")
        return self


def respond(db: DbSession) -> dict:
    db.flush()
    state = goals.build(db)
    alerts.settle_goals(db, state["goals"])
    return state


def check(db: DbSession, body: GoalIn, goal: Goal | None) -> None:
    if body.target_date is not None:
        on = today()
        if body.target_date < on.replace(day=1):
            raise HTTPException(422, "Pick a date from this month on.")
        if body.target_date.year > on.year + MAX_YEARS:
            raise HTTPException(422, f"Pick a date within {MAX_YEARS} years.")
    emergency = goal is not None and goal.kind == "emergency"
    if not emergency and body.target_cents is None:
        raise HTTPException(422, "How much is the goal? Enter an amount.")
    accounts = {a.id: a for a in household.counted(db) if a.type in goals.FUNDABLE}
    rest = {
        f.account_id: f.goal_id
        for f in db.scalars(select(GoalFunding).where(GoalFunding.cents.is_(None)))
        if goal is None or f.goal_id != goal.id
    }
    for f in body.funding:
        a = accounts.get(f.account_id)
        if a is None:
            raise HTTPException(422, "Pick a checking, savings or investment account.")
        if f.cents is None and f.account_id in rest:
            other = db.get(Goal, rest[f.account_id])
            name = household.display_name(a)
            raise HTTPException(422, f"The rest of {name} already goes to {other.name if other else 'another goal'}.")


def apply(db: DbSession, g: Goal, body: GoalIn) -> None:
    if g.kind != "emergency":
        g.name, g.icon = body.name, body.icon
    g.color, g.priority = body.color, body.priority
    g.target_cents, g.target_date = body.target_cents, body.target_date
    g.rate_percent = None if body.rate_percent is None else round(body.rate_percent, 2)
    g.monthly_cents = body.monthly_cents
    db.execute(delete(GoalFunding).where(GoalFunding.goal_id == g.id))
    for f in body.funding:
        db.add(GoalFunding(goal_id=g.id, account_id=f.account_id, cents=f.cents))
    if g.kind == "emergency" and body.emergency_months is not None:
        budget.set_preferences(db, emergency_months=body.emergency_months)


@router.get("/goals")
def get_goals(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.goals_page()
    return goals.build(db_of(request))


@router.post("/goals")
def add_goal(body: GoalIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    if len(db.scalars(select(Goal.id)).all()) >= MAX_GOALS:
        raise HTTPException(422, f"That's {MAX_GOALS} goals already. Remove one first.")
    check(db, body, None)
    g = Goal(kind="saving", name=body.name, icon=body.icon, color=body.color, created_by=who.user.id)
    db.add(g)
    db.flush()
    apply(db, g, body)
    audit(db, request, "goal_added", who.user.id, goal=g.id)
    return respond(db)


@router.put("/goals/{goal_id}")
def change_goal(goal_id: int, body: GoalIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    g = db.get(Goal, goal_id)
    if g is None:
        raise HTTPException(404, "That goal is gone.")
    check(db, body, g)
    apply(db, g, body)
    audit(db, request, "goal_changed", who.user.id, goal=g.id)
    return respond(db)


@router.delete("/goals/{goal_id}")
def remove_goal(goal_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    g = db.get(Goal, goal_id)
    if g is None:
        raise HTTPException(404, "That goal is gone.")
    if g.kind == "emergency":
        raise HTTPException(422, "The emergency fund is always a goal. Set it to fewer months instead.")
    db.delete(g)
    audit(db, request, "goal_removed", who.user.id, goal=goal_id)
    return respond(db)
