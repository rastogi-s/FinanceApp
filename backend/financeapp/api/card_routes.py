"""Credit cards: the page, which card product each card account is, what points are worth, the store lookup and the
missed points report."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from financeapp import cards, demo
from financeapp.api.deps import Signed, audit, signed_in
from financeapp.models import Account, CardLink
from financeapp.sync import today

router = APIRouter(prefix="/api")
MISSED_MONTHS = (1, 3, 6, 12)


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


class LinkIn(BaseModel):
    product: str = Field(min_length=1, max_length=60)
    choice: str | None = Field(default=None, max_length=32)
    role: Literal["active", "drawer", "closed"] = "active"
    renewal_month: int | None = Field(default=None, ge=1, le=12)


class PreferIn(BaseModel):
    category: str | None = Field(default=None, max_length=32)  # none: your default card everywhere
    account_id: int | None = None  # none: clear it


class ValuesIn(BaseModel):
    cpp: dict[str, float] = Field(max_length=20)


def wallet_for(request: Request, who: Signed) -> cards.Wallet:
    return demo.cards_wallet() if who.demo else cards.wallet(db_of(request), today())


@router.get("/cards")
def get_cards(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.cards_page()
    return cards.page(db_of(request), today())


@router.put("/cards/{account_id}/link")
def link_card(account_id: int, body: LinkIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    a = db.get(Account, account_id)
    if a is None or a.type != "credit":
        raise HTTPException(404, "That card is gone.")
    p = cards.product(body.product)
    if p is None:
        raise HTTPException(422, "Pick a card from the list.")
    if body.choice is not None and body.choice not in p.get("choices", []):
        raise HTTPException(422, "That card doesn't offer that category.")
    link = db.get(CardLink, account_id) or CardLink(account_id=account_id)
    link.product, link.role, link.renewal_month = p["id"], body.role, body.renewal_month
    link.choice = body.choice or (p["choices"][0] if p.get("choices") else None)
    db.merge(link)
    db.flush()
    audit(db, request, "card_linked", who.user.id, account=account_id, product=p["id"])
    return cards.page(db, today())


@router.delete("/cards/{account_id}/link")
def unlink_card(account_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    link = db.get(CardLink, account_id)
    if link is not None:
        db.delete(link)
        db.flush()
        audit(db, request, "card_unlinked", who.user.id, account=account_id)
    return cards.page(db, today())


@router.put("/cards/values")
def set_values(body: ValuesIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    if any(not 0 < v <= 5 for v in body.cpp.values()):
        raise HTTPException(422, "A point is worth between 0.01 and 5 cents.")
    db = db_of(request)
    cards.set_values(db, body.cpp)
    db.flush()
    return cards.page(db, today())


@router.put("/cards/prefer")
def set_prefer(body: PreferIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Which card wins when cards earn the same: a default, or one for a category."""
    no_demo(who)
    db = db_of(request)
    if body.category is not None and body.category not in cards.CATEGORIES:
        raise HTTPException(422, "Pick a category from the list.")
    if body.account_id is not None:
        link = db.get(CardLink, body.account_id)
        if link is None or link.role == "closed":
            raise HTTPException(422, "Pick one of your cards.")
    cards.set_prefer(db, body.category, body.account_id)
    db.flush()
    return cards.page(db, today())


@router.get("/cards/lookup")
def lookup(
    request: Request,
    q: str = Query("", max_length=80),
    pick: str | None = Query(None, max_length=120),
    kind: str | None = Query(None, max_length=32),
    who: Signed = Depends(signed_in),
) -> dict:
    w = wallet_for(request, who)
    return cards.lookup(w.cards, w.everything, w.on, w.values, w.ledger(), q, pick, kind)


@router.get("/cards/missed")
def missed(request: Request, months: int = 12, who: Signed = Depends(signed_in)) -> dict:
    if months not in MISSED_MONTHS:
        raise HTTPException(422, "Pick 1, 3, 6 or 12 months.")
    w = wallet_for(request, who)
    start = w.on - timedelta(days=round(months * 30.44))
    return {**cards.missed(w.cards, w.purchases, start, w.on, w.values), "months": months}
