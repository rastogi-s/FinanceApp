"""Check categories: what the app isn't sure about, answering it a merchant at a time, and AI suggestions."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from financeapp import demo, review
from financeapp.ai import categorizing
from financeapp.ai.providers import ProviderError
from financeapp.ai.service import AIUnavailable
from financeapp.api.deps import Signed, audit, signed_in

router = APIRouter(prefix="/api")


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


@router.get("/review")
def get_review(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.review()
    return review.state(db_of(request))


class ApplyIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    category_id: int
    remember: bool = True  # also later ones from this merchant or person, and their history


@router.post("/review/apply")
def apply(body: ApplyIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    try:
        changed = review.apply(db, body.ids, body.category_id, body.remember, who.user.id)
    except review.NotFound:
        raise HTTPException(404, "No such transactions or category.") from None
    audit(db, request, "recategorized", who.user.id, count=len(body.ids), remember=body.remember)
    return {"also_changed": changed, **review.state(db)}


@router.get("/review/ai")
def ai_offer(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """What Suggest with AI would send, to whom and at most what cost, before anything is sent."""
    if who.demo:
        return {
            "available": False,
            "problem": "Not available with sample data.",
            "provider": None,
            "model": None,
            "max_cost": None,
            "merchants": 0,
            "preview": None,
        }
    return categorizing.offer(db_of(request))


@router.post("/review/ai")
def ai_suggest(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """A suggested category per merchant. Nothing is saved: the page applies the ones the person taps."""
    no_demo(who)
    db = db_of(request)
    try:
        s = categorizing.suggest(request.app.state.db, db, who.user.id)
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, e.message) from None
    except categorizing.NothingToAsk:
        raise HTTPException(422, "Nothing left that AI could help with.") from None
    audit(db, request, "categories_ai_suggested", who.user.id, model=s.model, merchants=len(s.by_group))
    return {"suggestions": s.by_group, "model": s.model, "cost": s.cost}
