"""The Income over time page: each year's pay by source, typed-in or uploaded older years, and raise letters."""

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from financeapp import demo, earnings, paystub, w2
from financeapp.ai import documents, paydocs, reading
from financeapp.ai.providers import ProviderError
from financeapp.ai.service import AIUnavailable
from financeapp.api.deps import Signed, audit, signed_in
from financeapp.sync import today

router = APIRouter(prefix="/api/earnings")
MAX_CENTS = 100_000_000_00  # $100M a year: anything bigger is a typo
MAX_BYTES = 10 * 1024 * 1024


def no_demo(who: Signed) -> None:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")


@router.get("")
def get_earnings(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.earnings_page()
    return earnings.build(request.state.db, today())


class YearIn(BaseModel):
    """Only the fields sent change; null goes back to what the app works out."""

    model_config = ConfigDict(extra="forbid")
    base_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    bonus_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    rsu_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    other_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    match_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    employer_hsa_cents: int | None = Field(default=None, ge=0, le=MAX_CENTS)
    note: str | None = Field(default=None, max_length=earnings.MAX_NOTE)
    source: Literal["typed", "w2", "stub"] | None = None


def year_ok(year: int) -> None:
    if not 1970 <= year <= today().year:
        raise HTTPException(422, "Pick a year from 1970 to this year.")


@router.put("/years/{year}")
def put_year(year: int, body: YearIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    year_ok(year)
    db = request.state.db
    changes = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        key = name.removesuffix("_cents")
        changes[key] = value.strip() if isinstance(value, str) else value
    earnings.set_year(db, year, changes)
    audit(db, request, "earnings_year_changed", who.user.id, year=year, fields=sorted(changes))
    return earnings.build(db, today())


@router.delete("/years/{year}")
def clear_year(year: int, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    year_ok(year)
    db = request.state.db
    prefs = earnings.preferences(db)
    prefs["years"].pop(str(year), None)
    earnings.save(db, prefs)
    audit(db, request, "earnings_year_cleared", who.user.id, year=year)
    return earnings.build(db, today())


class LetterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["raise", "promotion", "bonus", "offer"] = "raise"
    effective: datetime.date
    title: str = Field(default="", max_length=80)
    base_cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)
    previous_base_cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)
    bonus_target_percent: float | None = Field(default=None, gt=0, le=200)
    bonus_target_cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)
    bonus_cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)
    stock_cents: int | None = Field(default=None, gt=0, le=MAX_CENTS)
    note: bool = True  # add what changed to that year's note

    @model_validator(mode="after")
    def says_something(self) -> "LetterIn":
        says = self.base_cents or self.bonus_target_percent or self.bonus_target_cents or self.bonus_cents
        if not says and not self.title.strip():
            raise ValueError("Add the new base salary, a bonus or the new title.")
        if not datetime.date(1970, 1, 1) <= self.effective <= today() + datetime.timedelta(days=730):
            raise ValueError("That start date doesn't look right.")
        return self


@router.post("/letters")
def add_letter(body: LetterIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = request.state.db
    letter = body.model_dump(exclude={"note"}, exclude_none=True)
    letter["effective"] = body.effective.isoformat()
    letter["title"] = body.title.strip()
    letter["added"] = today().isoformat()
    saved = earnings.add_letter(db, letter)
    if body.note and (text := earnings.letter_note(saved)):
        year = body.effective.year
        if year <= today().year:
            current = earnings.preferences(db)["years"].get(str(year), {}).get("note", "")
            if text not in current:
                joined = f"{current}. {text}" if current else text
                earnings.set_year(db, year, {"note": joined[: earnings.MAX_NOTE]})
    audit(db, request, "earnings_letter_added", who.user.id, kind=body.kind)
    return earnings.build(db, today())


@router.delete("/letters/{letter_id}")
def remove_letter(letter_id: str, request: Request, who: Signed = Depends(signed_in)) -> dict:
    no_demo(who)
    db = request.state.db
    if not earnings.remove_letter(db, letter_id):
        raise HTTPException(404, "No such letter.")
    audit(db, request, "earnings_letter_removed", who.user.id)
    return earnings.build(db, today())


def from_stub(stub: paystub.Stub) -> dict | None:
    """A year's numbers from the year-to-date column of its last pay stub, read without AI."""
    ytd = stub.ytd
    if not ytd.get("gross") or stub.pay_date is None:
        return None
    rsu, bonus = ytd.get("stock", 0), ytd.get("bonus", 0)
    warnings = list(stub.warnings)
    if stub.pay_date.month < 12:
        warnings.append(
            f"This stub is from {stub.pay_date:%B %-d}, so its year to date leaves out pay after it. "
            "The year's last stub has the whole year."
        )
    return {
        "year": stub.pay_date.year,
        "source": "stub",
        "base_cents": ytd["gross"] - rsu - bonus,
        "bonus_cents": bonus or None,
        "rsu_cents": rsu or None,
        "match_cents": ytd.get("match"),
        "employer_hsa_cents": ytd.get("employer_hsa"),
        "warnings": warnings,
    }


@router.post("/read")
async def read_document(
    file: UploadFile,
    request: Request,
    kind: Literal["letter", "year"],
    read_with_ai: bool = False,
    who: Signed = Depends(signed_in),
) -> dict:
    """Reads a raise or bonus letter, or a year's W-2 or last pay stub, into the form. Nothing is saved and the file
    isn't kept. A W-2 PDF, or a pay stub PDF with a year-to-date column, is read here; everything else comes back
    first as an offer to read it with AI (exactly what would be sent), and again with read_with_ai=true if the person
    agrees."""
    no_demo(who)
    db = request.state.db
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(422, "That file is over 10 MB. Save a smaller copy or take a screenshot.")
    sniffed = documents.sniff(data)
    if sniffed not in ("pdf", "png", "jpeg", "webp", "heic", "text"):
        raise HTTPException(422, "Upload a PDF, a screenshot or a photo.")
    why = "Raise and bonus letters are read with AI." if kind == "letter" else "W-2s are read with AI."
    if kind == "year" and sniffed == "pdf" and not read_with_ai:
        try:
            if form := w2.read(data):
                if found := w2.to_year(form):
                    return found
            elif found := from_stub(paystub.read(data)):
                return found
        except paystub.StubError as e:
            if "password" in str(e):
                raise HTTPException(422, str(e)) from None
        why = "This isn't a W-2 or pay stub the app can read by itself, so it needs AI."
    try:
        doc = documents.open_document(data)
    except documents.DocumentError as e:
        raise HTTPException(422, str(e)) from None
    if not read_with_ai:
        return {"ai_offer": reading.offer(db, doc, paydocs.outgoing_for(db, doc, kind), why)}
    try:
        answer, model, cost = paydocs.read(request.app.state.db, db, who.user.id, doc, kind, today())
        audit(db, request, "ai_read_file", who.user.id, model=model, kind=f"earnings_{kind}")
        return answer | {"ai_cost": cost}
    except AIUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except ProviderError as e:
        raise HTTPException(502, str(e)) from None
