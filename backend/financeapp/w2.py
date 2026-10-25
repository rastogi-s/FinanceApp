"""Reading a W-2 PDF without AI, for a year on the Income over time page.

The year's pay comes from the earnings summary payroll providers print beside the form (ADP's "W-2 and Earnings
Summary"): its Gross Pay row is all pay before anything came out, where box 1 has the 401(k) taken out and boxes 3
and 5 the pre-tax health and HSA money. Without a summary, box 5 (Medicare wages) is the closest the form has.

Employer HSA money is box 12 code W less the employee's own payroll HSA money, which the summary's "Cafe 125 HSA"
row shows. A W-2 doesn't split base pay from stock vests or bonuses, and doesn't show the 401(k) match.

PDF text comes out of ADP's summary table with the columns run together ("21822748 218 227 48"), so each row's
amount is the largest digit-grouped amount on it ("218 227 48" is $218,227.48), the columns being that amount, or it
less a capped wage.
"""

import re
from dataclasses import dataclass, field

from financeapp import paystub

FORM = re.compile(r"wage\s+and\s+tax", re.I)
YEAR = re.compile(r"\b(20\d\d)\s+W-2\b|wage\s+and\s+tax\s+(20\d\d)\b", re.I)
GROUPED = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ ,]\d{3})*)[ .](\d{2})(?![\d.,])")
RUN = re.compile(r"(?<![\d.,])(\d{3,})(?![\d.,])")
BOXES = re.compile(r"^(\d+\.\d{2}) (\d+\.\d{2})\b")  # box 1 and 2, then 3 and 4, then 5 and 6
CODE = re.compile(r"(?<![\w.])(C|D|W|AA|DD) (\d+\.\d{2})\b")
GROSS = re.compile(r"\bgross\s+pay\b", re.I)
EMPLOYEE_HSA = re.compile(r"\bHSA\b.*\(W-Box 12\)|cafe\s*125\s+HSA", re.I)


@dataclass
class W2:
    year: int | None = None
    gross_cents: int | None = None  # the summary's Gross Pay
    medicare_cents: int | None = None  # box 5
    codes: dict[str, int] = field(default_factory=dict)  # box 12, the first (federal) copy
    employee_hsa_cents: int | None = None


def row_amount(line: str) -> int | None:
    grouped = [int(re.sub(r"[ ,]", "", m.group(1))) * 100 + int(m.group(2)) for m in GROUPED.finditer(line)]
    if grouped:
        return max(grouped)
    run = RUN.search(line)
    return int(run.group(1)) if run else None


def parse(lines: list[str]) -> W2 | None:
    """None when this isn't a W-2."""
    text = " ".join(lines)
    if "W-2" not in text or not FORM.search(text):
        return None
    w = W2()
    if m := YEAR.search(text):
        w.year = int(m.group(1) or m.group(2))
    boxes = []
    for line in lines:
        if w.gross_cents is None and GROSS.search(line):
            w.gross_cents = row_amount(GROSS.split(line, 1)[1])
        elif w.employee_hsa_cents is None and EMPLOYEE_HSA.search(line):
            w.employee_hsa_cents = row_amount(re.split(r"\(W-Box 12\)|HSA", line, maxsplit=1, flags=re.I)[-1])
        if m := BOXES.match(line):
            boxes.append(paystub.cents(m.group(1)))
        for code, amount in CODE.findall(line):
            w.codes.setdefault(code, paystub.cents(amount))
    if len(boxes) >= 3:
        w.medicare_cents = boxes[2]
    return w


def read(data: bytes) -> W2 | None:
    return parse(paystub.text_of(data))


def to_year(w: W2) -> dict | None:
    """The year form's numbers, or None when no pay could be read."""
    total = w.gross_cents or w.medicare_cents
    if not total:
        return None
    warnings = []
    if not w.gross_cents:
        warnings.append(
            "From box 5 (Medicare wages), which leaves out pre-tax health insurance and HSA money. Adjust it if you "
            "know your gross pay."
        )
    warnings.append(
        "A W-2 doesn't split base pay from stock vests or bonuses, so it's all in base pay. Move any you know of to "
        "their own lines."
    )
    hsa = None
    if (total_hsa := w.codes.get("W")) is not None:
        if w.employee_hsa_cents is not None and 0 <= total_hsa - w.employee_hsa_cents <= total_hsa:
            hsa = total_hsa - w.employee_hsa_cents
        else:
            warnings.append(
                f"Box 12 W has {total_hsa / 100:,.2f} of HSA money, yours and your employer's together. Type in your "
                "employer's part if you know it."
            )
    warnings.append("Your employer's 401(k) match isn't on a W-2. Add it if you know it.")
    if w.year is None:
        warnings.append("The year wasn't found. Pick it.")
    return {
        "year": w.year,
        "source": "w2",
        "base_cents": total,
        "bonus_cents": None,
        "rsu_cents": None,
        "match_cents": None,
        "employer_hsa_cents": hsa,
        "warnings": warnings,
    }
