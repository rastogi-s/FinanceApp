"""Low cash reminders: payments made on a schedule from one account, and whether it has the cash for the next one
(funding.py)."""

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session as DbSession

from financeapp import demo, funding, household, reminders
from financeapp.api.deps import Signed, audit, signed_in

router = APIRouter(prefix="/api")
MAX_CENTS = 10_000_000_00  # $10M: anything bigger is a typo


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


class ReminderIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    account_id: int
    amount_cents: int = Field(gt=0, le=MAX_CENTS)
    every: Literal["week", "2weeks", "month"] = "week"
    start: datetime.date
    cushion_cents: int = Field(default=0, ge=0, le=MAX_CENTS)
    ahead_days: int = Field(default=funding.AHEAD_DAYS, ge=0, le=7)
    on: bool = True

    @model_validator(mode="after")
    def tidy(self):
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Give it a name, like E*TRADE weekly buy.")
        return self


class DismissIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)


def state(db: DbSession) -> dict:
    today = reminders.local_today(db)
    saved = funding.load(db)
    accounts = funding.accounts_for(db)
    return {
        "today": today.isoformat(),
        "reminders": [c.json(today) for c in funding.check(db, today, saved)],
        "suggestions": funding.suggestions(db, today, saved),
        "accounts": [
            {
                "id": a.id,
                "name": household.display_name(a),
                "type": a.type,
                "cash_cents": (c := funding.cash_of(db, a)).cents,
                "cash_as_of": c.as_of.isoformat() if c.as_of else None,
            }
            for a in sorted(accounts.values(), key=lambda a: (a.type != "investment", household.display_name(a)))
        ],
    }


def checked(db: DbSession, body: ReminderIn) -> None:
    if body.account_id not in funding.accounts_for(db):
        raise HTTPException(400, "Pick a bank or brokerage account.")


@router.get("/funding")
def get_funding(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.funding_state()
    return state(db_of(request))


@router.post("/funding")
def add_reminder(body: ReminderIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    checked(db, body)
    saved = funding.load(db)
    if len(saved) >= funding.MAX_REMINDERS:
        raise HTTPException(400, f"That's {funding.MAX_REMINDERS} already. Remove one first.")
    r = funding.Reminder(id=funding.next_id(saved), **body.model_dump())
    funding.save(db, [*saved, r])
    audit(db, request, "funding.add", who.user.id, reminder=r.id)
    return state(db)


@router.put("/funding/{reminder_id}")
def change_reminder(reminder_id: int, body: ReminderIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    checked(db, body)
    saved = funding.load(db)
    if all(r.id != reminder_id for r in saved):
        raise HTTPException(404, "No such reminder.")
    funding.save(db, [funding.Reminder(id=r.id, **body.model_dump()) if r.id == reminder_id else r for r in saved])
    audit(db, request, "funding.change", who.user.id, reminder=reminder_id)
    return state(db)


@router.delete("/funding/{reminder_id}")
def remove_reminder(reminder_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    saved = funding.load(db)
    if all(r.id != reminder_id for r in saved):
        raise HTTPException(404, "No such reminder.")
    funding.save(db, [r for r in saved if r.id != reminder_id])
    audit(db, request, "funding.remove", who.user.id, reminder=reminder_id)
    return state(db)


@router.post("/funding/dismiss")
def dismiss_suggestion(body: DismissIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """ "Not this one" on a suggestion found in the activity: it isn't offered again."""
    no_demo(who)
    db = db_of(request)
    funding.save(db, funding.load(db), [*funding.dismissed(db), body.key])
    return state(db)
