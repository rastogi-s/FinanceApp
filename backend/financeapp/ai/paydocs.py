"""Reading a raise or bonus letter, or a year's pay summary (a W-2, the year's last pay stub), with AI for the Income
over time page. The same two steps as any AI upload (reading.py): an offer showing exactly what would be sent, then
the reading once the person says yes. Names and emails are taken out of the text first; nothing is saved until the
person checks the numbers.

The schemas have no optional or nullable fields (a number is 0 and a string "" when the document doesn't show it),
which keeps them well inside Claude's structured-output limits (see providers._optional_not_null).
"""

from datetime import date

from pydantic import BaseModel, ValidationError

from financeapp.ai import service
from financeapp.ai.documents import Document
from financeapp.ai.extract import Outgoing, _cents, _clean, _date
from financeapp.ai.providers import ProviderError, Request, parse_json
from financeapp.ai.reading import TASK, household_identity
from financeapp.ai.redact import redact
from financeapp.db import Database

MAX_REPLY_TOKENS = 2_000

LETTER_SYSTEM = """You read pay letters for a private household finance app: raise letters, promotion letters, \
bonus letters, offer letters and compensation statements, as text or as pictures.

Return one JSON object, nothing else. Copy numbers exactly as printed; never estimate or make anything up. Use 0 \
for a number and "" for text the letter doesn't show.

- kind: "raise", "promotion", "bonus" (a one-time bonus award), "offer" (a new job) or "other".
- effective_date: when the new pay starts or the bonus is paid, YYYY-MM-DD.
- title: the new job title, if the letter gives one.
- base_salary: the new yearly base salary in dollars (an hourly rate times 2,080 if only hourly is given).
- previous_base_salary: the old yearly base salary, if shown.
- bonus_target_percent: the yearly bonus target as a percent of base (15 for 15%).
- bonus_target_amount: the yearly bonus target in dollars, if given as an amount instead.
- bonus_amount: a one-time bonus being awarded or paid, in dollars.
- stock_award_value: the value of a new stock (RSU) grant, in dollars.
- notes: one short sentence if something was unclear, else "".

Parts of the text were replaced with labels like [name] before you got it; that's expected."""

LETTER_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["raise", "promotion", "bonus", "offer", "other"]},
        "effective_date": {"type": "string"},
        "title": {"type": "string"},
        "base_salary": {"type": "number"},
        "previous_base_salary": {"type": "number"},
        "bonus_target_percent": {"type": "number"},
        "bonus_target_amount": {"type": "number"},
        "bonus_amount": {"type": "number"},
        "stock_award_value": {"type": "number"},
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}
LETTER_SCHEMA["required"] = list(LETTER_SCHEMA["properties"])

YEAR_SYSTEM = """You read a year's pay summary for a private household finance app: a W-2, the year's last pay \
stub (its year-to-date column), or a year-end earnings statement, as text or as pictures.

Return one JSON object, nothing else. Copy numbers exactly as printed; never estimate or make anything up. Use 0 \
for a number and "" for text the document doesn't show.

- kind: "w2", "paystub" or "other".
- tax_year: the year the pay is for, like 2024.
- total_pay: all pay for the year before taxes and deductions. On a W-2 with an earnings summary beside it, its \
Gross Pay; on a W-2 alone, box 5 (Medicare wages and tips); on a pay stub, the year-to-date gross pay.
- bonus: bonus, incentive or commission pay for the year, if shown on its own.
- stock_pay: stock pay for the year (RSU vests, ESPP income), if shown on its own (a W-2 may list it in box 14).
- employer_match: the employer's 401(k) match for the year, if shown.
- employer_hsa: the employer's own HSA contribution for the year, if shown on its own (W-2 box 12 code W adds the \
employee's payroll HSA money in: when the earnings summary shows the employee's own HSA deduction, like "Cafe 125 \
HSA", use box 12 W less that; otherwise 0).
- notes: one short sentence if something was unclear, else "".

Parts of the text were replaced with labels like [name] or [SSN] before you got it; that's expected."""

YEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["w2", "paystub", "other"]},
        "tax_year": {"type": "number"},
        "total_pay": {"type": "number"},
        "bonus": {"type": "number"},
        "stock_pay": {"type": "number"},
        "employer_match": {"type": "number"},
        "employer_hsa": {"type": "number"},
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}
YEAR_SCHEMA["required"] = list(YEAR_SCHEMA["properties"])

KINDS = {
    "letter": (LETTER_SYSTEM, LETTER_SCHEMA, "a raise, promotion, bonus or offer letter"),
    "year": (YEAR_SYSTEM, YEAR_SCHEMA, "a W-2 or a year's last pay stub"),
}


def build_request(doc: Document, names: list[str], emails: list[str], kind: str) -> Outgoing:
    system, schema, hint = KINDS[kind]
    ask = f"Read this document (it should be {hint}) and return the JSON object."
    if doc.pictures:
        text = ask + f" It's {len(doc.pictures)} picture{'s' if len(doc.pictures) > 1 else ''}, in page order."
        return Outgoing(Request(system, text, list(doc.pictures), schema, MAX_REPLY_TOKENS), None)
    r = redact(doc.text, names, emails)
    text = f"{ask}\n\n<document>\n{r.text}\n</document>"
    if doc.truncated:
        text += "\n(The document was cut off here because it's long.)"
    return Outgoing(Request(system, text, [], schema, MAX_REPLY_TOKENS), r)


def outgoing_for(db, doc: Document, kind: str) -> Outgoing:
    names, emails = household_identity(db)
    return build_request(doc, names, emails, kind)


class LetterAnswer(BaseModel):
    kind: str = "other"
    effective_date: str = ""
    title: str = ""
    base_salary: float = 0
    previous_base_salary: float = 0
    bonus_target_percent: float = 0
    bonus_target_amount: float = 0
    bonus_amount: float = 0
    stock_award_value: float = 0
    notes: str = ""


class YearAnswer(BaseModel):
    kind: str = "other"
    tax_year: float = 0
    total_pay: float = 0
    bonus: float = 0
    stock_pay: float = 0
    employer_match: float = 0
    employer_hsa: float = 0
    notes: str = ""


def _amount(value: float) -> int | None:
    c = _cents(value)
    return abs(c) if c else None


def to_letter(a: LetterAnswer, names: list[str], emails: list[str], model: str) -> dict:
    """The letter form's fields, for someone to check and save."""
    effective = _date(a.effective_date)
    pct = a.bonus_target_percent if 0 < a.bonus_target_percent <= 200 else None
    out = {
        "kind": a.kind if a.kind in ("raise", "promotion", "bonus", "offer") else "raise",
        "effective": effective.isoformat() if effective else None,
        "title": _clean(a.title, 80, names, emails),
        "base_cents": _amount(a.base_salary),
        "previous_base_cents": _amount(a.previous_base_salary),
        "bonus_target_percent": round(pct, 2) if pct else None,
        "bonus_target_cents": _amount(a.bonus_target_amount),
        "bonus_cents": _amount(a.bonus_amount),
        "stock_cents": _amount(a.stock_award_value),
        "warnings": [f"Read by AI ({model}). Check it against the letter before saving."],
    }
    if not any(out[k] for k in ("base_cents", "bonus_target_percent", "bonus_target_cents", "bonus_cents", "title")):
        raise ProviderError("No new salary, bonus or title could be read from that. Type them in instead.")
    if out["effective"] is None:
        out["warnings"].append("No start date found. Add the date the new pay starts.")
    if a.notes:
        out["warnings"].append(f"The AI noted: {_clean(a.notes, 200, names, emails)}")
    return out


def to_year(a: YearAnswer, names: list[str], emails: list[str], model: str, today: date) -> dict:
    """A year's numbers, in the shape the year form takes: base is what's left of total pay after bonus and stock."""
    year = int(a.tax_year) if 1980 <= a.tax_year <= today.year else None
    total = _amount(a.total_pay)
    if not total:
        raise ProviderError("No pay for the year could be read from that. Type it in instead.")
    bonus, stock = _amount(a.bonus) or 0, _amount(a.stock_pay) or 0
    out = {
        "year": year,
        "source": "w2" if a.kind == "w2" else "stub",
        "base_cents": max(total - bonus - stock, 0),
        "bonus_cents": bonus or None,
        "rsu_cents": stock or None,
        "match_cents": _amount(a.employer_match),
        "employer_hsa_cents": _amount(a.employer_hsa),
        "warnings": [f"Read by AI ({model}). Check it against the document before saving."],
    }
    if a.kind == "w2":
        out["warnings"].append(
            "A W-2 doesn't split base pay from stock vests or bonuses unless it lists them. Check base pay."
        )
    if year is None:
        out["warnings"].append("The year wasn't found. Pick it.")
    if a.notes:
        out["warnings"].append(f"The AI noted: {_clean(a.notes, 200, names, emails)}")
    return out


def read(database: Database, db, user_id: int, doc: Document, kind: str, today: date) -> tuple[dict, str, float]:
    """Sends the document. Raises AIUnavailable (nothing sent) or ProviderError (sent, failed, logged)."""
    names, emails = household_identity(db)
    out = build_request(doc, names, emails, kind)
    reply, route = service.run(database, db, TASK, user_id, out.request)
    cost = round(service.microdollars(reply.input_tokens, reply.output_tokens, route.price) / 1_000_000, 4)
    try:
        if kind == "letter":
            return (
                to_letter(LetterAnswer.model_validate(parse_json(reply.text)), names, emails, route.model),
                (route.model),
                cost,
            )
        answer = YearAnswer.model_validate(parse_json(reply.text))
        return to_year(answer, names, emails, route.model, today), route.model, cost
    except ValidationError:
        raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None
