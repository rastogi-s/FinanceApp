"""The Dividends & interest page, the APY and maturity typed in for a savings account or CD, and the year's taxable
dividends and interest for the tax page (income.tax_summary; keep that shape stable)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from financeapp import demo, deposits, household, income
from financeapp.api.deps import Signed, audit, signed_in
from financeapp.models import Account
from financeapp.sync import today

router = APIRouter(prefix="/api")


def db_of(request: Request) -> DbSession:
    return request.state.db


@router.get("/income")
def get_income(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return demo.income()
    return income.build(db_of(request), today())


@router.get("/income/tax")
def get_tax(
    request: Request, year: int | None = Query(default=None, ge=2000, le=2100), who: Signed = Depends(signed_in)
) -> dict:
    """Taxable dividends (qualified, ordinary, capital gain distributions) and interest for one calendar year."""
    on = today()
    if who.demo:
        return demo.income_tax(year or on.year)
    return income.tax_year(db_of(request), year or on.year, on)


class InterestAccountIn(BaseModel):
    """What the bank's site says: the APY, and for a CD when it matures and for how long it was opened."""

    apy: float | None = Field(default=None, ge=0, le=25)
    maturity: date | None = None
    term_months: int | None = Field(default=None, ge=1, le=120)


@router.put("/income/accounts/{account_id}")
def set_account(account_id: int, body: InterestAccountIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        raise HTTPException(403, "Not available with sample data.")
    db = db_of(request)
    a = db.get(Account, account_id)
    if a is None or a.type != "depository" or a not in household.counted(db):
        raise HTTPException(404, "No such bank account.")
    # Only these three: anything else typed on the account (opened, penalty, renewal) stays as it was.
    deposits.save_terms(db, a.id, {"apy": body.apy, "maturity": body.maturity, "term_months": body.term_months})
    audit(db, request, "interest_account_changed", who.user.id, account=a.id)
    db.flush()
    return income.build(db, today())
