"""The Taxes page: this year's estimate, and the household's answers behind it."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from financeapp import demo, taxes
from financeapp.api.deps import Signed, audit, signed_in
from financeapp.sync import today

router = APIRouter(prefix="/api")
MAX_CENTS = 100_000_000_00  # $100M: anything bigger is a typo


@router.get("/taxes")
def get_taxes(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.taxes_page()
    return taxes.build(request.state.db, who.user.id, today())


class TaxIn(BaseModel):
    """Only the fields sent change. In `amounts`, null goes back to the number the app works out."""

    model_config = ConfigDict(extra="forbid")
    filing_status: Literal["single", "mfj", "mfs", "hoh"] | None = None
    state: str | None = None
    state_rate: float | None = Field(default=None, ge=0, le=15)
    deduction: Literal["standard", "itemized"] | None = None
    state_deduction: Literal["standard", "itemized"] | None = None
    children: int | None = Field(default=None, ge=0, le=20)
    other_dependents: int | None = Field(default=None, ge=0, le=20)
    qualified_share: int | None = Field(default=None, ge=0, le=100)
    amounts: dict[str, int | None] | None = None

    @field_validator("state")
    @classmethod
    def known_state(cls, v: str | None) -> str | None:
        if v is not None and v not in taxes.STATES:
            raise ValueError("Pick a state from the list.")
        return v

    @field_validator("amounts")
    @classmethod
    def known_amounts(cls, v: dict[str, int | None] | None) -> dict[str, int | None] | None:
        for key, cents in (v or {}).items():
            if key not in taxes.ITEMS:
                raise ValueError("Unknown amount.")
            if cents is not None and not (-MAX_CENTS if taxes.ITEMS[key][1] else 0) <= cents <= MAX_CENTS:
                raise ValueError("That amount is out of range.")
        return v


@router.patch("/taxes/settings")
def tax_settings(body: TaxIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = request.state.db
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    for key in (
        "filing_status",
        "state",
        "deduction",
        "state_deduction",
        "children",
        "other_dependents",
        "qualified_share",
    ):
        if key in changes and changes[key] is None:
            del changes[key]  # these always have a value once answered
    taxes.set_profile(db, changes)
    fields = sorted(k for k in changes if k != "amounts") + sorted(f"amounts.{k}" for k in changes.get("amounts") or {})
    audit(db, request, "tax_changed", who.user.id, fields=fields)
    return taxes.build(db, who.user.id, today())
