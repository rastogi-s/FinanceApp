"""The budget (draft, edits, versions), pay stubs, and the household settings behind the savings numbers."""

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp import budget, demo, paystub
from financeapp.ai import budgeting, documents, extract, reading
from financeapp.ai.providers import ProviderError
from financeapp.ai.service import AIUnavailable
from financeapp.api.deps import Signed, audit, signed_in
from financeapp.models import BudgetLine, BudgetVersion, Category, Paycheck, User

router = APIRouter(prefix="/api")
MAX_CENTS = 10_000_000_00  # $10M: anything bigger is a typo


def db_of(request: Request) -> DbSession:
    return request.state.db


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


@router.get("/budget")
def get_budget(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.budget()
    return budget.build(db_of(request))


class LineIn(BaseModel):
    category_id: int
    limit_cents: int = Field(ge=0, le=MAX_CENTS)


class BudgetIn(BaseModel):
    lines: list[LineIn] = Field(max_length=200)
    source: Literal["edit", "ai"] = "edit"  # "ai": lines picked from a Draft with AI suggestion


@router.put("/budget")
def save_budget(body: BudgetIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Saves the whole budget as a new version (the page sends every line, changed or not)."""
    no_demo(who)
    db = db_of(request)
    ids = [line.category_id for line in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "A category is in the budget twice.")
    kinds = dict(db.execute(select(Category.id, Category.kind).where(Category.id.in_(ids))).all())
    if any(kinds.get(i) not in budget.BUDGETED for i in ids):
        raise HTTPException(422, "Only bills and everyday spending categories can have a budget.")
    v = budget.save_version(db, {line.category_id: line.limit_cents for line in body.lines}, body.source, who.user.id)
    audit(db, request, "budget_saved", who.user.id, version=v.id, lines=len(body.lines))
    return budget.build(db)


@router.post("/budget/draft")
def redraft(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """Saves the draft made from recent spending: "looks right" on a first draft, or starting over later."""
    no_demo(who)
    db = db_of(request)
    v = budget.save_draft(db, who.user.id)
    if v is None:
        raise HTTPException(409, "There isn't a full month of spending to draft from yet.")
    audit(db, request, "budget_saved", who.user.id, version=v.id, source="draft")
    return budget.build(db)


@router.get("/budget/ai")
def ai_offer(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """What Draft with AI would send, to whom and at most what cost, before anything is sent."""
    if who.demo:
        return {
            "available": False,
            "problem": "Not available with sample data.",
            "provider": None,
            "model": None,
            "max_cost": None,
            "months": 0,
            "categories": 0,
            "stubs": 0,
            "preview": None,
        }
    return budgeting.offer(db_of(request))


@router.post("/budget/ai")
def ai_suggest(request: Request, who: Signed = Depends(signed_in)) -> dict:
    """A suggested budget, line by line. Nothing is saved: the page saves the lines the person picks."""
    no_demo(who)
    db = db_of(request)
    try:
        s = budgeting.suggest(request.app.state.db, db, who.user.id)
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, e.message) from None
    except budgeting.NotABudget as e:
        raise HTTPException(422, e.message) from None
    audit(db, request, "budget_ai_suggested", who.user.id, model=s.model, lines=len(s.lines))
    return {"summary": s.summary, "lines": s.lines, "model": s.model, "cost": s.cost}


@router.post("/budget/versions/{version_id}/restore")
def restore(version_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    if db.get(BudgetVersion, version_id) is None:
        raise HTTPException(404, "No such budget version.")
    lines = {
        line.category_id: line.limit_cents
        for line in db.scalars(select(BudgetLine).where(BudgetLine.version_id == version_id))
    }
    v = budget.save_version(db, lines, "restore", who.user.id)
    audit(db, request, "budget_saved", who.user.id, version=v.id, restored=version_id)
    return budget.build(db)


class SettingsIn(BaseModel):
    emergency_months: int | None = Field(default=None, ge=1, le=36)
    include_match: bool | None = None


@router.patch("/budget/settings")
def budget_settings(body: SettingsIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    budget.set_preferences(db, **body.model_dump(exclude_none=True))
    return budget.build(db)


class CategoryPatch(BaseModel):
    essential: bool


@router.patch("/categories/{category_id}")
def patch_category(category_id: int, body: CategoryPatch, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    c = db.get(Category, category_id)
    if c is None:
        raise HTTPException(404, "No such category.")
    c.essential = body.essential
    return budget.build(db)


# ---- pay stubs


class StubAmounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gross: int | None = Field(default=None, ge=0, le=MAX_CENTS)  # year to date only
    federal: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    state: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    local: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    state_other: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    social_security: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    medicare: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    retirement: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    roth: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    hsa: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    espp: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    benefits: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    stock: int | None = Field(default=None, ge=0, le=MAX_CENTS)


class StubDetail(BaseModel):
    """What the Taxes page uses: this paycheck's taxes and deductions by kind, and the stub's year-to-date column."""

    model_config = ConfigDict(extra="forbid")
    this: StubAmounts = StubAmounts()
    ytd: StubAmounts = StubAmounts()


class PaycheckIn(BaseModel):
    user_id: int | None = None  # whose pay; the person entering it when left out
    date: datetime.date
    gross_cents: int = Field(gt=0, le=MAX_CENTS)
    taxes_cents: int = Field(ge=0, le=MAX_CENTS)
    savings_cents: int = Field(ge=0, le=MAX_CENTS)
    net_cents: int = Field(ge=0, le=MAX_CENTS)
    employer_match_cents: int = Field(default=0, ge=0, le=MAX_CENTS)
    frequency: Literal["weekly", "biweekly", "semimonthly", "monthly"] | None = None
    period_start: datetime.date | None = None
    period_end: datetime.date | None = None
    detail: StubDetail | None = None

    @model_validator(mode="after")
    def adds_up(self) -> "PaycheckIn":
        if self.taxes_cents + self.savings_cents + self.net_cents > self.gross_cents:
            raise ValueError("Taxes, savings and take-home pay add up to more than gross pay.")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("The pay period ends before it starts.")
        return self


@router.post("/paychecks/read")
async def read_paycheck(
    file: UploadFile, request: Request, read_with_ai: bool = False, who: Signed = Depends(signed_in)
) -> dict:
    """Reads an uploaded pay stub into the form's numbers. Nothing is saved, and the file isn't kept. A PDF with text
    is read here; a scan, a photo or a layout the reader doesn't know comes back as an offer to read it with AI, and
    the browser sends it again with read_with_ai=true if the person agrees."""
    no_demo(who)
    db = db_of(request)
    data = await file.read(paystub.MAX_BYTES + 1)
    if len(data) > paystub.MAX_BYTES:
        raise HTTPException(422, "That file is over 5 MB. A pay stub is usually much smaller.")
    kind = documents.sniff(data)
    why = "Photos and screenshots of pay stubs need AI to read."
    if kind == "pdf" and not read_with_ai:
        try:
            return paystub.to_json(paystub.read(data))
        except paystub.StubError as e:
            if "password" in str(e) or "couldn't be opened" in str(e):
                raise HTTPException(422, str(e)) from None
            why = (
                "This PDF is a scan, with no text in it for the app to read by itself."
                if "scan" in str(e)
                else "The app can't read this pay stub's layout by itself."
            )
    elif kind not in ("pdf", "png", "jpeg", "webp", "heic"):
        raise HTTPException(422, "Upload the pay stub as a PDF, a screenshot or a photo.")
    try:
        doc = documents.open_document(data)
    except documents.DocumentError as e:
        raise HTTPException(422, str(e)) from None
    if not read_with_ai:
        return {"ai_offer": reading.offer(db, doc, reading.outgoing_for(db, doc, "a pay stub"), why)}
    try:
        r = reading.read(request.app.state.db, db, who.user.id, doc, "a pay stub")
        audit(db, request, "ai_read_file", who.user.id, model=r.model, kind=r.answer.kind)
        if r.answer.kind != "paystub" or r.answer.paystub is None:
            raise HTTPException(422, "That doesn't look like a pay stub.")
        return extract.to_paystub(r.answer, r.model) | {"ai_cost": r.cost}
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, str(e)) from None


@router.post("/paychecks")
def add_paycheck(body: PaycheckIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    user_id = body.user_id if body.user_id is not None else who.user.id
    if db.get(User, user_id) is None:
        raise HTTPException(422, "No such person.")
    detail = body.detail.model_dump(exclude_none=True) if body.detail else {}
    db.add(
        Paycheck(
            **body.model_dump(exclude={"user_id", "detail"}), detail=detail, user_id=user_id, created_by=who.user.id
        )
    )
    db.flush()
    audit(db, request, "paycheck_added", who.user.id)
    return budget.build(db)


@router.delete("/paychecks/{paycheck_id}")
def delete_paycheck(paycheck_id: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = db_of(request)
    p = db.get(Paycheck, paycheck_id)
    if p is None:
        raise HTTPException(404, "No such pay stub.")
    db.delete(p)
    db.flush()
    audit(db, request, "paycheck_removed", who.user.id)
    return budget.build(db)
