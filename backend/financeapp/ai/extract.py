"""Reading a document with AI: what is asked, what comes back, and how the answer becomes an import preview or a
pay stub. The answer is never trusted as-is: every field is checked and converted here, rows that don't make sense
are dropped (and counted in a warning), and identifying details the model repeats are taken out again.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ValidationError

from financeapp.ai.documents import Document
from financeapp.ai.providers import ProviderError, Request, parse_json
from financeapp.ai.redact import Redacted, redact
from financeapp.connectors.base import HoldingData, TransactionData, asset_class_for
from financeapp.connectors.files import FileAccount, ParsedFile, clean_merchant, stable_id

MAX_ROWS = 2000
MAX_REPLY_TOKENS = 16_000

SYSTEM = """You read financial documents for a private household finance app: bank and card statements, \
brokerage and retirement statements, spreadsheets and pay stubs, as text or as pictures.

Return one JSON object, nothing else. Copy numbers exactly as printed; never estimate, total up or make anything \
up. Leave a field null when the document doesn't show it.

- kind: "transactions" for a list of transactions (bank, card, loan), "positions" for holdings (brokerage, 401(k), \
IRA, HSA investments), "paystub" for a pay stub or earnings statement, "other" for anything else.
- institution: the bank, broker, card issuer or employer's name as printed, e.g. "Chase", "Fidelity".
- account_last4: only the last 4 digits of the account number, if shown. Never more digits.
- account_type: depository (checking, savings), credit (credit card), loan, investment, or other.
- as_of: the statement's closing date or the date the holdings are as of, YYYY-MM-DD.
- statement_balance: the closing / new balance (for a card, the amount owed, as a positive number).
- transactions: every transaction listed. date as YYYY-MM-DD (use the statement's year when the row has none). \
amount is negative for money out of the account (purchases, payments made, withdrawals, fees) and positive for \
money in (deposits, refunds, payments received by a card). description as printed, without card numbers.
- holdings: every position. symbol (ticker or fund code; "CASH" for cash or money market sweep), name, quantity \
(shares), price (per share), value (market value).
- paystub: pay_date, period_start, period_end (YYYY-MM-DD), and the amounts for THIS paycheck (not year to date): \
gross (total gross pay), taxes (all taxes withheld: federal, state, local, Social Security, Medicare), \
pre_tax_savings (401(k), 403(b), 457, HSA and ESPP taken out of pay), net (net pay / take-home), employer_match \
(the employer's 401(k) match, if shown), employer_hsa (the employer's own HSA contribution; not the employee's; \
0 if not shown). frequency: weekly, biweekly, semimonthly or monthly if stated.
- notes: one short sentence if something was unclear (cut-off pages, unreadable rows), else null.

Parts of the text were replaced with labels like [name], [SSN] or [number ending 1234] before you got it; that's \
expected."""

_nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_nullable_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(
    {
        "kind": {"type": "string", "enum": ["transactions", "positions", "paystub", "other"]},
        "institution": _nullable_str,
        "account_name": _nullable_str,
        "account_last4": _nullable_str,
        "account_type": _nullable_str,
        "as_of": _nullable_str,
        "statement_balance": _nullable_num,
        "transactions": {
            "type": "array",
            "items": _obj(
                {"date": {"type": "string"}, "description": {"type": "string"}, "amount": {"type": "number"}}
            ),
        },
        "holdings": {
            "type": "array",
            "items": _obj(
                {
                    "symbol": {"type": "string"},
                    "name": _nullable_str,
                    "quantity": _nullable_num,
                    "price": _nullable_num,
                    "value": _nullable_num,
                }
            ),
        },
        "paystub": {
            "anyOf": [
                _obj(
                    {
                        "pay_date": _nullable_str,
                        "period_start": _nullable_str,
                        "period_end": _nullable_str,
                        "frequency": _nullable_str,
                        "gross": _nullable_num,
                        "taxes": _nullable_num,
                        "pre_tax_savings": _nullable_num,
                        "net": _nullable_num,
                        "employer_match": _nullable_num,
                        # A plain number (0 when not shown): Claude's schema limit leaves no room for one more
                        # optional field (see providers._optional_not_null).
                        "employer_hsa": {"type": "number"},
                    }
                ),
                {"type": "null"},
            ]
        },
        "notes": _nullable_str,
    }
)


# ---- what's sent


@dataclass
class Outgoing:
    request: Request
    redacted: Redacted | None  # what the text looked like after redaction (None when only pictures go)


def build_request(doc: Document, names: list[str], emails: list[str], hint: str = "") -> Outgoing:
    """The request for a document, with its text redacted. `hint` says what's expected ("a pay stub")."""
    ask = "Read this document" + (f" (it should be {hint})" if hint else "") + " and return the JSON object."
    if doc.pictures:
        text = ask + f" It's {len(doc.pictures)} picture{'s' if len(doc.pictures) > 1 else ''}, in page order."
        if doc.truncated:
            text += " Only the first pages are included."
        return Outgoing(Request(SYSTEM, text, list(doc.pictures), SCHEMA, MAX_REPLY_TOKENS), None)
    r = redact(doc.text, names, emails)
    text = f"{ask}\n\n<document>\n{r.text}\n</document>"
    if doc.truncated:
        text += "\n(The document was cut off here because it's long.)"
    return Outgoing(Request(SYSTEM, text, [], SCHEMA, MAX_REPLY_TOKENS), r)


# ---- what comes back


class _Txn(BaseModel):
    date: str
    description: str
    amount: float


class _Holding(BaseModel):
    symbol: str
    name: str | None = None
    quantity: float | None = None
    price: float | None = None
    value: float | None = None


class _Stub(BaseModel):
    pay_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    frequency: str | None = None
    gross: float | None = None
    taxes: float | None = None
    pre_tax_savings: float | None = None
    net: float | None = None
    employer_match: float | None = None
    employer_hsa: float | None = None


class Answer(BaseModel):
    kind: str
    institution: str | None = None
    account_name: str | None = None
    account_last4: str | None = None
    account_type: str | None = None
    as_of: str | None = None
    statement_balance: float | None = None
    transactions: list[_Txn] = []
    holdings: list[_Holding] = []
    paystub: _Stub | None = None
    notes: str | None = None


def parse_answer(text: str) -> Answer:
    try:
        return Answer.model_validate(parse_json(text))
    except ValidationError:
        raise ProviderError("The answer wasn't in the expected format. Try again or pick another model.") from None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        d = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    return d if date(1990, 1, 1) <= d <= date(2100, 1, 1) else None


def _cents(value: float | None) -> int | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    if not d.is_finite() or abs(d) >= 10_000_000_000:
        return None
    return int((d * 100).quantize(Decimal(1)))


def _clean(text: str | None, limit: int, names: list[str], emails: list[str]) -> str:
    """Text from the answer, with identifying details taken out again (a model can copy them from a picture)."""
    return redact((text or "").strip(), names, emails).text[:limit]


def _last4(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits[-4:] if len(digits) >= 4 else None


def to_parsed(a: Answer, today: date, names: list[str], emails: list[str], model: str) -> ParsedFile:
    """Transactions or positions from the answer, as the same preview a CSV makes."""
    mask = _last4(a.account_last4)
    institution = _clean(a.institution, 120, names, emails)
    as_of = _date(a.as_of) or today
    acct_type = a.account_type if a.account_type in ("depository", "credit", "loan", "investment", "other") else None
    warnings: list[str] = []
    dropped = 0
    acct = FileAccount(
        mask=mask,
        name=_clean(a.account_name, 120, names, emails),
        institution=institution,
        type=acct_type or ("investment" if a.kind == "positions" else "depository"),
    )
    if a.kind == "transactions":
        seen: dict[tuple, int] = {}
        for t in a.transactions[:MAX_ROWS]:
            d, cents = _date(t.date), _cents(t.amount)
            desc = _clean(t.description, 300, names, emails)
            if d is None or cents is None or not desc or d > today:
                dropped += 1
                continue
            key = (d.isoformat(), cents, desc.lower())
            seen[key] = seen.get(key, 0) + 1
            acct.transactions.append(
                TransactionData(
                    account_external_id="file",
                    external_id=stable_id(*key, seen[key]),
                    date=d,
                    amount_cents=cents,
                    merchant=clean_merchant(desc) or desc[:200],
                    description=desc,
                )
            )
        if not acct.transactions:
            raise ProviderError("No transactions could be read from that. Try a clearer screenshot or the CSV.")
        if a.statement_balance is not None:
            acct.balance_cents = _cents(
                abs(a.statement_balance) if acct.type in ("credit", "loan") else a.statement_balance
            )
    elif a.kind == "positions":
        mismatched = 0
        for h in a.holdings[:MAX_ROWS]:
            symbol = re.sub(r"[^A-Z0-9.\-/ ]", "", h.symbol.upper()).strip()[:40]
            value = _cents(h.value)
            qty = Decimal(str(h.quantity)) if h.quantity is not None else None
            price = Decimal(str(h.price)) if h.price is not None else None
            if value is None and qty is not None and price is not None:
                value = _cents(float(qty * price))
            if not symbol or value is None:
                dropped += 1
                continue
            if (
                qty is not None
                and price is not None
                and value
                and abs(float(qty * price) * 100 - value) > max(100, value * 0.02)
            ):
                mismatched += 1
            cash = symbol in ("CASH", "MONEY MARKET", "SWEEP") or "money market" in (h.name or "").lower()
            acct.holdings.append(
                HoldingData(
                    account_external_id=mask or "file",
                    symbol="CASH" if cash else symbol,
                    name=_clean(h.name, 200, names, emails) or symbol,
                    asset_class="Cash" if cash else asset_class_for(h.name, symbol),
                    quantity=qty if qty is not None else Decimal(value) / 100,
                    price=price if price is not None else Decimal(1),
                    value_cents=value,
                )
            )
        if not acct.holdings:
            raise ProviderError("No holdings could be read from that. Try a clearer screenshot or the CSV.")
        acct.balance_cents = sum(h.value_cents for h in acct.holdings)
        acct.file_total_cents = _cents(a.statement_balance)
        if mismatched:
            warnings.append(f"For {mismatched} holding(s), shares x price doesn't match the value. Check them.")
        if acct.file_total_cents is not None and abs(acct.file_total_cents - acct.balance_cents) > 100:
            warnings.append(
                f"The holdings add up to ${acct.balance_cents / 100:,.2f} but the statement says "
                f"${acct.file_total_cents / 100:,.2f}."
            )
    else:
        raise ProviderError("That doesn't look like a statement, a list of transactions or holdings, or a pay stub.")
    if dropped:
        warnings.append(f"{dropped} row(s) didn't make sense and were left out.")
    if a.notes:
        warnings.append(f"The AI noted: {_clean(a.notes, 200, names, emails)}")
    kind = "transactions" if a.kind == "transactions" else "positions"
    label = f"{institution} statement" if institution else "Statement"
    return ParsedFile(kind, f"{label}, read by AI ({model})", [acct], as_of, warnings)


def to_paystub(a: Answer, model: str) -> dict:
    """The pay stub form's numbers (the same shape paystub.to_json gives), for someone to check and save."""
    s = a.paystub or _Stub()
    freq = (s.frequency or "").lower().replace("-", "").replace(" ", "")
    frequency = freq if freq in ("weekly", "biweekly", "semimonthly", "monthly") else None

    def amount(v: float | None) -> int | None:
        c = _cents(v)
        return abs(c) if c is not None else None

    out = {
        "date": (d.isoformat() if (d := _date(s.pay_date)) else None),
        "period_start": (d.isoformat() if (d := _date(s.period_start)) else None),
        "period_end": (d.isoformat() if (d := _date(s.period_end)) else None),
        "frequency": frequency,
        "gross_cents": amount(s.gross),
        "taxes_cents": amount(s.taxes),
        "savings_cents": amount(s.pre_tax_savings),
        "net_cents": amount(s.net),
        "employer_match_cents": amount(s.employer_match),
        "warnings": [f"Read by AI ({model}). Check every number against the stub before saving."],
    }
    if hsa := amount(s.employer_hsa):
        out["detail"] = {"this": {"employer_hsa": hsa}, "ytd": {}, "deposits": []}
    if out["gross_cents"] is None and out["net_cents"] is None:
        raise ProviderError("No gross or take-home pay could be read. Try a clearer picture, or type the numbers in.")
    g, t, sv, n = (out[k] or 0 for k in ("gross_cents", "taxes_cents", "savings_cents", "net_cents"))
    if g and t + sv + n > g:
        out["warnings"].append("Taxes, savings and take-home add up to more than gross pay. One of them is off.")
    return out
