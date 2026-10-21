"""Taking identifying details out of text before it goes to an AI provider.

What goes: Social Security numbers, account and card numbers (the last 4 digits stay, so accounts can still be told
apart), routing numbers, the values after labels like "Employee ID" or "Member #", email addresses, phone numbers,
street addresses, and the names and emails of the people in the household. Amounts, dates, merchants and
institution names stay: they are what the AI is asked to read.

Pictures can't be redacted this way (there's no text to edit), which is why sending them is a separate switch.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}[- ]\d{2}[- ]\d{4}\b")
SSN_LABELLED = re.compile(r"(?i)\b(ssn|social security(?: number| no\.?)?)\s*[:#]?\s*\d{9}\b")
# 9+ digits, optionally in groups split by spaces or dashes (card numbers, account numbers, routing numbers). Amounts
# have a decimal point or commas, and 8-digit runs (20261021) are dates, so neither matches.
LONG_NUMBER = re.compile(r"(?<![\d.,/-])\d(?:[ -]?\d){8,22}(?![\d.,]|/\d)")
LABELLED_ID = re.compile(
    r"(?i)\b((?:employee|emp|member|policy|customer|subscriber|group|client|file|badge|tax ?payer|loan|account|acct)"
    r"\.?\s*(?:id|no\.?|number|num|#))\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})"
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
STREET = re.compile(
    r"(?i)\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-z0-9']*\.?\s+){1,4}"
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|ct|court|way|pl|place|ter|terrace|cir|circle"
    r"|pkwy|parkway|hwy|highway)\b\.?(?:,?\s*(?:apt|unit|suite|ste|#)\.?\s*[\w-]+)?"
)

# Words that follow or precede a name on statements and stubs without being part of it.
_NOT_NAMES = (
    r"(?!(?i:employee|employer|pay|payroll|net|gross|total|earnings|statement|account|acct|check|deposit|inc|llc|corp"
    r"|company|co|dept|department|id|ssn|date|period|amount|balance|member|customer|card|jr|sr|ii|iii)\b)"
)
NEXT_TO_NAME = re.compile(rf"\[name\] {_NOT_NAMES}(?:[A-Z][a-z]+|[A-Z]{{2,}})\b")
BEFORE_NAME = re.compile(rf"\b{_NOT_NAMES}(?:[A-Z][a-z]+|[A-Z]{{2,}})(,? )\[name\]")


@dataclass
class Redacted:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _keep_last4(digits: str) -> str:
    only = re.sub(r"\D", "", digits)
    return f"[number ending {only[-4:]}]"


def redact(text: str, names: list[str] | None = None, emails: list[str] | None = None) -> Redacted:
    """Returns the text with identifying details replaced by [labels], and how many of each were taken out."""
    counts: Counter[str] = Counter()

    def sub(pattern: re.Pattern, label: str, replace) -> None:
        nonlocal text

        def one(m: re.Match) -> str:
            counts[label] += 1
            return replace(m)

        text = pattern.sub(one, text)

    sub(SSN_LABELLED, "Social Security numbers", lambda m: f"{m.group(1)} [SSN]")
    sub(SSN, "Social Security numbers", lambda m: "[SSN]")

    def labelled(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(2))
        if re.match(r"(?i)(account|acct|loan)", m.group(1)) and len(digits) >= 4:
            return f"{m.group(1)} [number ending {digits[-4:]}]"
        return f"{m.group(1)} [ID]"

    sub(LABELLED_ID, "ID numbers", labelled)
    sub(EMAIL, "email addresses", lambda m: "[email]")
    sub(PHONE, "phone numbers", lambda m: "[phone]")
    sub(LONG_NUMBER, "account or card numbers", lambda m: _keep_last4(m.group(0)))
    sub(STREET, "street addresses", lambda m: "[address]")
    for email in sorted({e for e in emails or [] if e}, key=len, reverse=True):
        sub(re.compile(re.escape(email), re.I), "email addresses", lambda m: "[email]")
    for name in sorted({n.strip() for n in names or [] if n and len(n.strip()) >= 2}, key=len, reverse=True):
        # The full name, then each part of it on its own ("Shubham Rastogi", "RASTOGI, SHUBHAM", "Shubham").
        parts = [p for p in re.split(r"\s+", name) if len(p) >= 3]
        for piece in [name, *parts]:
            sub(re.compile(rf"(?<!\w){re.escape(piece)}(?!\w)", re.I), "names", lambda m: "[name]")
    if names:
        # A capitalized word right next to a name is almost always the rest of it ("SHUBHAM RASTOGI", "Rastogi,
        # Shubham" when only the first name is known).
        sub(NEXT_TO_NAME, "names", lambda m: "[name] [name]")
        sub(BEFORE_NAME, "names", lambda m: f"[name]{m.group(1)}[name]")
    return Redacted(text, dict(counts))


def describe(counts: dict[str, int]) -> list[str]:
    """['2 account or card numbers', '1 name'] for showing what was taken out."""
    out = []
    for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n == 1 and label.endswith("s"):
            label = {"street addresses": "street address", "email addresses": "email address"}.get(label, label[:-1])
        out.append(f"{n} {label}")
    return out
