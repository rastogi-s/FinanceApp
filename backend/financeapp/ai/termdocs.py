"""Reading a CD's terms, or a bank account's rate and fees, from what the bank sends: a CD's receipt or confirmation
(the "CD advice"), a renewal notice, or a statement. The same two steps as any AI upload (reading.py): an offer showing
exactly what would be sent, then the reading once the person says yes. Names, emails and long account numbers are
taken out of the text first (the last 4 digits stay, so the right CD can be picked off a statement listing several).
Nothing is saved and the file isn't kept: the answer only fills in the terms form, and the person checks it and saves.

The schema has no optional or nullable fields (a number is 0 and a string "" when the document doesn't show it), which
keeps it well inside Claude's structured-output limits (see providers._optional_not_null).
"""

from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from financeapp import deposits
from financeapp.ai import service
from financeapp.ai.documents import Document
from financeapp.ai.extract import Outgoing, _cents, _clean, _date, _last4
from financeapp.ai.providers import ProviderError, Request, parse_json
from financeapp.ai.reading import TASK, household_identity
from financeapp.ai.redact import redact
from financeapp.db import Database

MAX_REPLY_TOKENS = 1_500

SYSTEM = """You read bank documents for a private household finance app: a certificate of deposit's (CD's) receipt, \
confirmation or advice, a CD renewal or maturity notice, or a bank statement, as text or as pictures. You're told \
which account to read; a statement can list several, so read only that one (match its last 4 digits or its name).

Return one JSON object, nothing else. Copy numbers exactly as printed; never estimate or make anything up. Use 0 \
for a number and "" for text the document doesn't show.

- document: "cd_receipt" (a new CD's receipt, confirmation or advice), "renewal" (a renewal or maturity notice), \
"statement" or "other".
- institution: the bank's or credit union's name.
- account_last4: the last 4 digits of the account read.
- apy: the annual percentage yield, as a percent (4.25 for 4.25%). On a statement, the APY for the period, or the \
"annual percentage yield earned".
- interest_rate: the interest rate, as a percent, if printed apart from the APY.
- opened_date: when the CD was opened or its current term began (for a renewed CD, the renewal date), YYYY-MM-DD.
- term_months: the term in months (12 for 1 year; 13 for 13 months; days divided by 30.4, rounded, if given in days).
- maturity_date: when the CD matures, YYYY-MM-DD.
- principal: the amount deposited at the start of the term, in dollars (not today's balance, unless that's all \
there is: then 0).
- penalty_days: the early withdrawal penalty as days of interest (3 months = 90, 6 months = 180, 1 year = 365).
- auto_renew: "yes" if it renews by itself at maturity, "no" if it doesn't, "" if not stated.
- grace_days: the grace period after maturity, in days (10 calendar days = 10).
- interest_handling: "added" if interest stays in the CD (compounds, is credited to it), "paid_out" if it's paid \
to another account or by check, "" if not stated.
- monthly_fee: a checking or savings account's monthly service fee, in dollars.
- fee_waiver_balance: the balance that waives that fee, in dollars.
- notes: one short sentence if something was unclear, else "".

Parts of the text were replaced with labels like [name] or [number ending 1234] before you got it; that's expected."""

SCHEMA = {
    "type": "object",
    "properties": {
        "document": {"type": "string", "enum": ["cd_receipt", "renewal", "statement", "other"]},
        "institution": {"type": "string"},
        "account_last4": {"type": "string"},
        "apy": {"type": "number"},
        "interest_rate": {"type": "number"},
        "opened_date": {"type": "string"},
        "term_months": {"type": "number"},
        "maturity_date": {"type": "string"},
        "principal": {"type": "number"},
        "penalty_days": {"type": "number"},
        "auto_renew": {"type": "string", "enum": ["yes", "no", ""]},
        "grace_days": {"type": "number"},
        "interest_handling": {"type": "string", "enum": ["added", "paid_out", ""]},
        "monthly_fee": {"type": "number"},
        "fee_waiver_balance": {"type": "number"},
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}
SCHEMA["required"] = list(SCHEMA["properties"])


@dataclass
class Target:
    """The account being filled in, so the right one is read off a statement listing several."""

    name: str
    mask: str | None
    kind: str  # "cd", "savings" or "checking"


def build_request(doc: Document, names: list[str], emails: list[str], t: Target) -> Outgoing:
    what = (
        "a CD's receipt, renewal notice or statement" if t.kind == "cd" else "a bank statement or the bank's rate page"
    )
    which = f'The account is "{_clean(t.name, 80, names, emails)}"' + (f", ending in {t.mask}" if t.mask else "")
    ask = f"Read this document (it should be {what}). {which}. Return the JSON object."
    if doc.pictures:
        text = ask + f" It's {len(doc.pictures)} picture{'s' if len(doc.pictures) > 1 else ''}, in page order."
        return Outgoing(Request(SYSTEM, text, list(doc.pictures), SCHEMA, MAX_REPLY_TOKENS), None)
    r = redact(doc.text, names, emails)
    text = f"{ask}\n\n<document>\n{r.text}\n</document>"
    if doc.truncated:
        text += "\n(The document was cut off here because it's long.)"
    return Outgoing(Request(SYSTEM, text, [], SCHEMA, MAX_REPLY_TOKENS), r)


def outgoing_for(db, doc: Document, t: Target) -> Outgoing:
    names, emails = household_identity(db)
    return build_request(doc, names, emails, t)


class Answer(BaseModel):
    document: str = "other"
    institution: str = ""
    account_last4: str = ""
    apy: float = 0
    interest_rate: float = 0
    opened_date: str = ""
    term_months: float = 0
    maturity_date: str = ""
    principal: float = 0
    penalty_days: float = 0
    auto_renew: str = ""
    grace_days: float = 0
    interest_handling: str = ""
    monthly_fee: float = 0
    fee_waiver_balance: float = 0
    notes: str = ""


def _whole(value: float, low: int, high: int) -> int | None:
    n = round(value)
    return n if low <= n <= high else None


def _rate(value: float) -> float | None:
    return round(value, 3) if 0 < value <= 25 else None


def _amount(value: float) -> int | None:
    c = _cents(value)
    return abs(c) if c else None


def to_terms(a: Answer, t: Target, names: list[str], emails: list[str], model: str) -> dict:
    """The terms form's fields (the same names PUT /accounts/{id}/terms takes; null = not read), for someone to check
    and save. Only what fits the account's kind is filled: a statement's fee means nothing for a CD."""
    cd = t.kind == "cd"
    warnings = [f"Read by AI ({model}). Check it against the document before saving."]
    apy = _rate(a.apy)
    if apy is None and (rate := _rate(a.interest_rate)):
        apy = rate
        warnings.append("Only the interest rate was printed, not the APY; the APY is a little higher if it compounds.")
    out: dict = {
        "apy": apy,
        "opened": None,
        "term_months": None,
        "maturity": None,
        "principal_cents": None,
        "penalty_days": None,
        "auto_renew": None,
        "grace_days": None,
        "pays_out": None,
        "monthly_fee_cents": None,
        "min_balance_cents": None,
    }
    if cd:
        opened, maturity = _date(a.opened_date), _date(a.maturity_date)
        if opened and maturity and maturity <= opened:
            warnings.append("The dates read don't fit (it matures before it opened), so they were left out.")
            opened = maturity = None
        term = _whole(a.term_months, 1, 120)
        if term is None and opened and maturity:
            months = deposits.months_between(opened, maturity)
            term = months if 1 <= months <= 120 else None
        out |= {
            "opened": opened.isoformat() if opened else None,
            "term_months": term,
            "maturity": maturity.isoformat() if maturity else None,
            "principal_cents": _amount(a.principal),
            "penalty_days": _whole(a.penalty_days, 1, 1825),
            "auto_renew": {"yes": True, "no": False}.get(a.auto_renew),
            "grace_days": _whole(a.grace_days, 1, 60),
            "pays_out": {"paid_out": True, "added": False}.get(a.interest_handling),
        }
        if not out["maturity"] and not (out["opened"] and out["term_months"]):
            warnings.append("No maturity date found. Add it, or the day it opened and its term.")
    elif t.kind == "checking":
        out |= {"monthly_fee_cents": _amount(a.monthly_fee), "min_balance_cents": _amount(a.fee_waiver_balance)}
    if not any(v is not None for v in out.values()):
        what = "terms" if cd else "rate"
        raise ProviderError(f"No {what} could be read from that. Type them in instead.")
    last4 = _last4(a.account_last4)
    if last4 and t.mask and last4 != t.mask[-4:]:
        warnings.append(f"This looks like the account ending {last4}, not this one (ending {t.mask[-4:]}).")
    if a.notes:
        warnings.append(f"The AI noted: {_clean(a.notes, 200, names, emails)}")
    return out | {
        "institution": _clean(a.institution, 80, names, emails) or None,
        "document": a.document if a.document in ("cd_receipt", "renewal", "statement") else "other",
        "filled": [k for k, v in out.items() if v is not None],
        "warnings": warnings,
    }


def read(database: Database, db, user_id: int, doc: Document, t: Target) -> tuple[dict, str, float]:
    """Sends the document. Raises AIUnavailable (nothing sent) or ProviderError (sent, failed, logged)."""
    names, emails = household_identity(db)
    out = build_request(doc, names, emails, t)
    reply, route = service.run(database, db, TASK, user_id, out.request)
    cost = round(service.microdollars(reply.input_tokens, reply.output_tokens, route.price) / 1_000_000, 4)
    try:
        answer = Answer.model_validate(parse_json(reply.text))
    except ValidationError:
        raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None
    return to_terms(answer, t, names, emails, route.model), route.model, cost
