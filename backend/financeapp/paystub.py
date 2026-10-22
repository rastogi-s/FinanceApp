"""Reads a pay stub PDF (ADP, Gusto, Paychex, Workday and most payroll systems) into the numbers the pay stub form
needs, so a stub is uploaded and checked instead of typed.

Stubs put the amount for this paycheck first on a line and the year-to-date total after it ("Gross Pay 4,500.00
81,000.00"), so each line's first amount is the one taken. Taxes are the stub's own total when it prints one, or
else the sum of the federal, state, Social Security and Medicare lines. Savings are 401(k)/403(b)/457, HSA and ESPP
lines; employer contributions go to the match instead. How often you're paid comes from the stub's own words
("Biweekly") or the length of the pay period.

For the Taxes page each line is also sorted into a detail (federal, state, Social Security, Medicare, pre-tax 401(k),
Roth, HSA, ESPP, pre-tax health benefits, stock pay), with the year-to-date column when the stub prints one: the last
amount on a line, when there are two or more.

Only PDFs with text in them can be read here; a scanned or photographed stub has none, and is read with AI
(ai/extract.py) if the household has set it up. Nothing is stored: the file is read in memory and the numbers go
back to the form for someone to check and save.
"""

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from pypdf import PdfReader

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 4
FREQUENCIES = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}

log = logging.getLogger("financeapp")

AMOUNT = re.compile(r"\(?-?\$?\s?\d{1,3}(?:,\d{3})*\.\d{2}\)?|\(?-?\$?\s?\d+\.\d{2}\)?")
DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")

GROSS = re.compile(r"\b(total\s+)?gross(\s+(pay|earnings|wages|amount))?\b", re.I)
NET = re.compile(r"\bnet\s+(pay|check|amount|wages)\b|\btake[- ]home\b|\bnet\s*$", re.I)
TAX_TOTAL = re.compile(r"\b(total\s+(employee\s+)?(taxes|tax\s+withholdings?|withholdings?)|taxes\s+total)\b", re.I)
TAX = re.compile(
    r"\bfed(eral)?\b.*\b(income|tax|withholding|w/h|fit)\b|\bfed\s*(w/?h|med|oasdi|mwt)\b|\bfit\b|\bsocial\s+security\b|\boasdi\b|"
    r"\bfica\b|\bmedicare\b|\bmed\s+tax\b|\bstate\b.*\b(income|tax|withholding)\b|\b(sit|sdi|fli|pfl)\b|"
    r"\b[A-Z]{2}\s+(state\s+)?(income\s+)?tax\b|\b(city|local|county)\s+tax\b",
    re.I,
)
SAVINGS = re.compile(
    r"\b(40[13]\s*\(?[kb]\)?|457\s*\(?b?\)?|hsa|health\s+savings|stock\s+purchase)\b|(?<![a-z])espp(?![a-z])", re.I
)  # "Espp_2" is ESPP too
EMPLOYER = re.compile(r"\b(employer|company|er)\b.*\b(match|contrib|contribution|401|hsa)\b|\bmatch\b", re.I)
TAXABLE = re.compile(r"\btaxable\b", re.I)  # "Federal Taxable Wages" is a wage total, not a tax
BENEFITS = re.compile(
    r"\b(medical|dental|vision|health\s+ins(urance)?|fsa|flexible\s+spending|dependent\s+care|section\s+125)\b", re.I
)
STOCK = re.compile(
    r"\brsus?\b|\brestricted\s+stock|\bstock\s+(award|vest(ing)?|units?|comp(ensation)?)|\bespp\s+(gain|income|disq)|"
    r"\bdisqualifying\b|\bnso\b|\bstock\s+option",
    re.I,
)
YTD_ONLY = re.compile(r"\b(year\s+to\s+date|ytd)\b", re.I)

PAY_DATE = re.compile(r"\b(pay|check|payment|advice|deposit)\s*date\b", re.I)
PERIOD_START = re.compile(r"\bperiod\s+(begin(ning)?|start(ing)?|from)\b", re.I)
PERIOD_END = re.compile(r"\bperiod\s+(end(ing)?|to|through)\b", re.I)
PERIOD = re.compile(r"\bpay\s+period\b|\bperiod\b", re.I)
WORDS = [
    (re.compile(r"\bbi-?weekly\b|\bevery\s+(two|2)\s+weeks\b", re.I), "biweekly"),
    (re.compile(r"\bsemi-?monthly\b|\btwice\s+a\s+month\b", re.I), "semimonthly"),
    (re.compile(r"\bweekly\b", re.I), "weekly"),
    (re.compile(r"\bmonthly\b", re.I), "monthly"),
]


class StubError(ValueError):
    """The stub couldn't be read. The message is shown to the person as-is."""


@dataclass
class Stub:
    pay_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    frequency: str | None = None
    gross_cents: int | None = None
    taxes_cents: int | None = None
    savings_cents: int | None = None
    net_cents: int | None = None
    employer_match_cents: int | None = None
    # For the Taxes page: this paycheck's amounts and the year to date, by kind (see DETAIL).
    detail: dict[str, int] = field(default_factory=dict)
    ytd: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def cents(text: str) -> int:
    negative = text.startswith("(") or "-" in text
    value = round(float(re.sub(r"[^\d.]", "", text)) * 100)
    return -value if negative else value


def amounts(line: str) -> list[int]:
    return [abs(cents(m.group())) for m in AMOUNT.finditer(line)]


def dates(line: str) -> list[date]:
    out = []
    for m in DATE.finditer(line):
        try:
            if m.group(4):
                out.append(date(int(m.group(4)), int(m.group(5)), int(m.group(6))))
            else:
                year = int(m.group(3))
                out.append(date(year + 2000 if year < 100 else year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            continue
    return out


def text_of(data: bytes) -> list[str]:
    if len(data) > MAX_BYTES:
        raise StubError("That file is over 5 MB. A pay stub PDF is usually much smaller.")
    if not data.startswith(b"%PDF"):
        raise StubError("That isn't a PDF. Download the stub as a PDF from your payroll site and upload that.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise StubError("That PDF is password-protected. Save a copy without the password and upload that.")
        lines = []
        for page in reader.pages[:MAX_PAGES]:
            lines += (page.extract_text(extraction_mode="layout") or "").splitlines()
    except StubError:
        raise
    except Exception as e:
        log.info("pay stub PDF couldn't be read (%s)", type(e).__name__)
        raise StubError("That PDF couldn't be opened. Try downloading it again.") from None
    lines = [re.sub(r"\s+", " ", line).strip() for line in lines]
    lines = [line for line in lines if line]
    if not lines:
        raise StubError("This PDF is a scan, with no text in it for the app to read by itself.")
    return lines


DETAIL = (
    "federal", "state", "local", "state_other", "social_security", "medicare",  # taxes
    "retirement", "roth", "hsa", "espp", "benefits",  # taken out before pay
    "stock",  # RSU and ESPP income, already inside gross pay
)  # fmt: skip


def tax_kind(line: str) -> str:
    if re.search(r"\bsocial\s+security\b|\boasdi\b|\bss\s+tax\b|\bfica\b(?!.*\bmed)", line, re.I):
        return "social_security"
    if re.search(r"\bmedicare\b|\bmed\s+tax\b|\bfed\s*med\b|\bfica\s+med", line, re.I):
        return "medicare"
    if re.search(r"\b(sdi|fli|pfl|vpdi|disability|family\s+leave)\b", line, re.I):
        return "state_other"
    if re.search(r"\b(city|local|county|school\s+district)\b", line, re.I):
        return "local"
    if re.search(r"\bfed(eral)?\b|\bfit\b|\bfwt\b", line, re.I):
        return "federal"
    return "state"


def savings_kind(line: str) -> str:
    if re.search(r"\broth\b", line, re.I):
        return "roth"
    if re.search(r"\bhsa\b|\bhealth\s+savings\b", line, re.I):
        return "hsa"
    if re.search(r"(?<![a-z])espp(?![a-z])|\bstock\s+purchase\b", line, re.I):
        return "espp"
    return "retirement"


def ytd_amount(line: str, label: re.Match) -> int | None:
    """The year-to-date column: the last amount on the line, when there's more than one."""
    found = amounts(line[label.end() :])
    return found[-1] if len(found) >= 2 and found[-1] >= found[0] else None


def first_amount(line: str, label: re.Match) -> int | None:
    """The first amount after the label (this paycheck's, not the year to date)."""
    found = amounts(line[label.end() :])
    return found[0] if found else None


def frequency_from(stub: Stub, lines: list[str]) -> str | None:
    for line in lines:
        if re.search(r"\b(frequency|pay\s+(cycle|schedule|frequency))\b", line, re.I) or PERIOD.search(line):
            for pattern, name in WORDS:
                if pattern.search(line):
                    return name
    if stub.period_start and stub.period_end:
        days = (stub.period_end - stub.period_start).days + 1
        if days <= 8:
            return "weekly"
        if days == 14:
            return "biweekly"
        if 13 <= days <= 16:
            return "semimonthly" if stub.period_start.day in (1, 16) else "biweekly"
        if 27 <= days <= 31:
            return "monthly"
    for pattern, name in WORDS:  # anywhere on the stub, as a last resort
        if any(pattern.search(line) for line in lines):
            return name
    return None


# Some stubs (ADP's among them) draw the comma and the decimal point apart from the digits, so the text comes out
# as "Gross Pay $7 773 08 239 276 46": dollars in groups of three, then two digits of cents.
BARE = re.compile(r"^(\(?-?\$?)(\d+)(\*?)$")
CENTS = re.compile(r"^(\d{2})(\*?\)?)$")


def restore_amounts(line: str) -> str:
    """ "Gross Pay $7 773 08 239 276 46" -> "Gross Pay $7,773.08 239,276.46"."""
    tokens = line.split(" ")
    out = []
    i = 0
    while i < len(tokens):
        head = BARE.match(tokens[i])
        if head and not head.group(3):
            j = i + 1
            groups = [head.group(2)]
            while j < len(tokens) and re.fullmatch(r"\d{3}", tokens[j]) and j + 1 < len(tokens):
                groups.append(tokens[j])
                j += 1
            if j < len(tokens) and (tail := CENTS.match(tokens[j])) and (len(groups) == 1 or len(groups[0]) <= 3):
                dollars = int("".join(groups))
                out.append(f"{head.group(1)}{dollars:,}.{tail.group(1)}{tail.group(2)}")
                i = j + 1
                continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)


AMOUNT_TOKEN = re.compile(r"^\(?-?\$?\d[\d,]*\.\d{2}\)?\*?$")


def split_columns(line: str) -> list[str]:
    """A line with two columns side by side ("Regular 7,743.62 147,936.83 Dental -21.39 449.19") as one line per
    item: a label, then its amounts; a word after an amount starts the next item."""
    parts: list[list[str]] = [[]]
    seen_amount = False
    for token in line.split(" "):
        is_amount = bool(AMOUNT_TOKEN.match(token))
        if not is_amount and seen_amount:
            parts.append([])
            seen_amount = False
        parts[-1].append(token)
        seen_amount = seen_amount or is_amount
    return [" ".join(p) for p in parts if p]


def signed(line: str, label: re.Match) -> list[int]:
    return [cents(m.group()) for m in AMOUNT.finditer(line[label.end() :])]


def read(data: bytes) -> Stub:
    return parse(text_of(data))


def parse(lines: list[str]) -> Stub:
    """The numbers on a stub, from its lines of text."""
    if not any(AMOUNT.search(line) for line in lines if NET.search(line) or GROSS.search(line)):
        lines = [restore_amounts(line) for line in lines]
    lines = [part for line in lines for part in split_columns(line)]
    # Stubs that print this paycheck's deductions as negative amounts ("Dental -21.39 449.19") leave the column
    # blank when nothing came out this time, so a deduction with no negative amount is a year-to-date figure only.
    negatives = sum(1 for line in lines if re.search(r"(^|\s)\(?-\$?\d[\d,]*\.\d{2}", line)) >= 3

    def this_period(line: str, m: re.Match, earnings: bool = False) -> int | None:
        found = signed(line, m)
        if not negatives:
            return abs(found[0]) if found else None
        if earnings:  # an earning with one amount printed is the year to date
            return abs(found[-2]) if len(found) >= 2 else None
        neg = [v for v in found if v < 0]
        return abs(neg[0]) if neg else None

    stub = Stub()
    tax_lines = 0
    tax_sum = 0
    savings = 0
    match = 0

    def add(kind: str, line: str, m: re.Match, value: int) -> None:
        stub.detail[kind] = stub.detail.get(kind, 0) + value
        if (ytd := ytd_amount(line, m)) is not None:
            stub.ytd[kind] = stub.ytd.get(kind, 0) + ytd

    for line in lines:
        if YTD_ONLY.match(line):
            continue
        if stub.gross_cents is None and not TAXABLE.search(line) and (m := GROSS.search(line)):
            stub.gross_cents = first_amount(line, m)
            if stub.gross_cents is not None and (ytd := ytd_amount(line, m)) is not None:
                stub.ytd["gross"] = ytd
        elif stub.net_cents is None and (m := NET.search(line)):
            stub.net_cents = first_amount(line, m)
        elif stub.taxes_cents is None and (m := TAX_TOTAL.search(line)):
            stub.taxes_cents = first_amount(line, m)
        elif TAXABLE.search(line):
            continue
        elif (
            (m := STOCK.search(line))
            and not re.search(r"\b(offset|refund|tax)\b", line, re.I)
            and (value := this_period(line, m, earnings=True))
        ):
            add("stock", line, m, value)
        elif EMPLOYER.search(line) and (m := EMPLOYER.search(line)):
            if (value := this_period(line, m, earnings=True)) is not None:
                match += value
        elif (m := SAVINGS.search(line)) and (value := this_period(line, m)) is not None:
            savings += value
            add(savings_kind(line), line, m, value)
        elif (m := TAX.search(line)) and (value := this_period(line, m)) is not None:
            tax_lines += 1
            tax_sum += value
            add(tax_kind(line), line, m, value)
        elif (m := BENEFITS.search(line)) and (value := this_period(line, m)):
            add("benefits", line, m, value)

        if stub.pay_date is None and (m := PAY_DATE.search(line)) and (found := dates(line[m.end() :])):
            stub.pay_date = found[0]
        if stub.period_start is None and (m := PERIOD_START.search(line)) and (found := dates(line[m.end() :])):
            stub.period_start = found[0]
        if stub.period_end is None and (m := PERIOD_END.search(line)) and (found := dates(line[m.end() :])):
            stub.period_end = found[0]
        if stub.period_start is None and PERIOD.search(line) and len(found := dates(line)) >= 2:
            stub.period_start, stub.period_end = found[0], found[1]

    if stub.taxes_cents is None and tax_lines:
        stub.taxes_cents = tax_sum
    stub.savings_cents = savings
    stub.employer_match_cents = match
    if stub.pay_date is None and stub.period_end is not None:
        stub.pay_date = stub.period_end
    stub.frequency = frequency_from(stub, lines)

    if stub.gross_cents is None and stub.net_cents is None:
        raise StubError("No gross or take-home pay found in that PDF. Is it a pay stub? You can type the numbers in.")
    if stub.gross_cents is None:
        stub.warnings.append("Gross pay wasn't found. Type it in from the stub.")
    if stub.net_cents is None:
        stub.warnings.append("Take-home pay wasn't found. Type it in from the stub.")
    if stub.pay_date is None:
        stub.warnings.append("The pay date wasn't found. Check it.")
    if (
        stub.gross_cents is not None
        and stub.net_cents is not None
        and (stub.taxes_cents or 0) + stub.savings_cents + stub.net_cents > stub.gross_cents
    ):
        stub.warnings.append("These numbers add up to more than gross pay. Check them against the stub.")
    return stub


def to_json(stub: Stub) -> dict:
    def iso(d: date | None) -> str | None:
        return d.isoformat() if d else None

    return {
        "date": iso(stub.pay_date),
        "period_start": iso(stub.period_start),
        "period_end": iso(stub.period_end),
        "frequency": stub.frequency,
        "gross_cents": stub.gross_cents,
        "taxes_cents": stub.taxes_cents,
        "savings_cents": stub.savings_cents,
        "net_cents": stub.net_cents,
        "employer_match_cents": stub.employer_match_cents,
        "detail": {"this": stub.detail, "ytd": stub.ytd},
        "warnings": stub.warnings,
    }


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()
