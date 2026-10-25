"""File import: the backup for every account. Reads what the institution lets you download.

- Positions CSVs: Fidelity (Portfolio Positions, every account in one file) and E*TRADE (Portfolio download), plus
  any CSV with a symbol column and a quantity or value column.
- Transactions CSVs from banks and cards (date, description, and an amount or debit/credit columns).
- OFX/QFX ("Quicken" or "Money" downloads) from banks and cards, with the statement's ending balance.

Nothing is saved here: parse() returns a preview, and the app saves it only after someone confirms it. Re-importing
the same file never duplicates anything, because each row gets a stable id from its own contents.
"""

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from financeapp.connectors.base import HoldingData, TransactionData, asset_class_for, last4

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000


class FileError(ValueError):
    """The file couldn't be read. The message is shown to the person as-is."""


@dataclass
class FileAccount:
    """One account found in a file."""

    mask: str | None
    name: str
    institution: str
    type: str  # investment | depository | credit
    holdings: list[HoldingData] = field(default_factory=list)
    transactions: list[TransactionData] = field(default_factory=list)
    balance_cents: int | None = None
    file_total_cents: int | None = None  # the total the file itself states, to check the rows against


@dataclass
class ParsedFile:
    kind: str  # positions | transactions
    format: str  # e.g. "Fidelity positions"
    accounts: list[FileAccount]
    as_of: date
    warnings: list[str] = field(default_factory=list)
    flipped: bool = False  # amounts were read as "positive = money out" (most card exports)


# ---- small helpers


def money(value: str | None) -> Decimal | None:
    """'$1,234.56', '(12.00)', '-$5', '1.2K'-free plain amounts. None for blanks and dashes."""
    if value is None:
        return None
    v = value.strip().replace("$", "").replace(",", "").replace("\u2212", "-")
    if v in ("", "-", "--", "n/a", "N/A"):
        return None
    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()").rstrip("%").strip()
    if v.startswith("+"):
        v = v[1:]
    try:
        d = Decimal(v)
    except InvalidOperation:
        return None
    return -d if negative else d


def to_cents(d: Decimal | None) -> int | None:
    return None if d is None else int((d * 100).quantize(Decimal(1)))


DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y", "%Y/%m/%d")


def parse_date(value: str) -> date | None:
    v = value.strip().split(" ")[0] if "/" in value or "-" in value else value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()


def find_col(headers: list[str], *options: str, exclude: tuple[str, ...] = ()) -> int | None:
    """The first column whose normalized name equals, or else starts with, one of the options."""
    names = [norm(h) for h in headers]
    for opt in options:
        for i, n in enumerate(names):
            if n == opt and not any(x in n for x in exclude):
                return i
    for opt in options:
        for i, n in enumerate(names):
            if n.startswith(opt) and not any(x in n for x in exclude):
                return i
    return None


def stable_id(*parts: object) -> str:
    return "file-" + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise FileError("That file is over 5 MB. Download a shorter date range and try again.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise FileError("That file isn't text. Download it as CSV or OFX/QFX.")


def rows_of(text: str) -> list[list[str]]:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) > MAX_ROWS:
        raise FileError("That file has too many rows. Download a shorter date range.")
    return rows


# ---- entry point


def parse(data: bytes, filename: str, today: date | None = None) -> ParsedFile:
    today = today or date.today()
    text = decode(data)
    head = text[:2000].upper()
    if "<OFX>" in head or "OFXHEADER" in head:
        return parse_ofx(text, today)
    rows = rows_of(text)
    for i, row in enumerate(rows[:60]):
        names = [norm(c) for c in row]
        if (
            "symbol" in names
            and ("account number" in names or "account name" in names)
            and any(n.startswith("current value") for n in names)
        ):
            return parse_fidelity_positions(rows, i, today)
        if names and names[0] == "symbol" and any(n in ("quantity", "qty", "shares") for n in names):
            return parse_positions(rows, i, today)
    for i, row in enumerate(rows[:30]):
        names = [norm(c) for c in row]
        has_date = any("date" in n for n in names)
        has_amount = any(n.startswith(("amount", "debit", "credit", "withdrawal", "deposit")) for n in names)
        has_text = any(
            n in ("description", "payee", "merchant", "name", "details", "memo", "transaction")
            or n.startswith("description")
            for n in names
        )
        if has_date and has_amount and has_text:
            return parse_transactions(rows, i, today, filename)
    raise FileError(
        "This file's layout isn't one the app knows yet. Positions or transactions as CSV, or an OFX/QFX "
        "download, work best."
    )


# ---- positions


def parse_fidelity_positions(rows: list[list[str]], header_row: int, today: date) -> ParsedFile:
    h = rows[header_row]
    c_acct, c_name = find_col(h, "account number"), find_col(h, "account name")
    c_sym, c_desc = find_col(h, "symbol"), find_col(h, "description")
    c_qty, c_price = find_col(h, "quantity"), find_col(h, "last price", exclude=("change",))
    c_value, c_basis = find_col(h, "current value"), find_col(h, "cost basis total", "cost basis")
    c_type = find_col(h, "type")
    accounts: dict[str, FileAccount] = {}
    warnings: list[str] = []
    for row in rows[header_row + 1 :]:
        if len(row) <= max(c for c in (c_sym, c_value) if c is not None):
            continue  # the disclaimer lines at the bottom
        symbol = row[c_sym].strip()
        if not symbol or "pending activity" in symbol.lower():
            continue
        acct_no = row[c_acct].strip() if c_acct is not None else ""
        name = row[c_name].strip() if c_name is not None else "Fidelity account"
        key = acct_no or name
        acct = accounts.setdefault(
            key,
            FileAccount(
                mask=last4(acct_no), name=name or "Fidelity account", institution="Fidelity", type="investment"
            ),
        )
        value = money(row[c_value]) if c_value is not None else None
        if value is None:
            continue
        qty = money(row[c_qty]) if c_qty is not None else None
        price = money(row[c_price]) if c_price is not None else None
        is_cash = symbol.endswith("**") or (c_type is not None and "cash" in row[c_type].lower() and qty is None)
        clean = symbol.rstrip("*")
        acct.holdings.append(
            HoldingData(
                account_external_id=key,
                symbol=clean[:40],
                name=(row[c_desc].strip() if c_desc is not None else clean)[:200] or clean,
                asset_class="Cash" if is_cash else asset_class_for(row[c_desc] if c_desc is not None else "", clean),
                quantity=qty if qty is not None else value,
                price=price if price is not None else Decimal(1),
                value_cents=to_cents(value) or 0,
                cost_basis_cents=to_cents(money(row[c_basis])) if c_basis is not None else None,
            )
        )
    if not accounts:
        raise FileError("No positions found in this Fidelity file.")
    for a in accounts.values():
        a.balance_cents = sum(x.value_cents for x in a.holdings)
    return ParsedFile("positions", "Fidelity positions", list(accounts.values()), today, warnings)


def parse_positions(rows: list[list[str]], header_row: int, today: date) -> ParsedFile:
    """E*TRADE's portfolio download, and any table with Symbol + Quantity (+ Value/Price) columns."""
    before = " ".join(" ".join(r) for r in rows[:header_row])
    is_etrade = "net account value" in before.lower() or any("price paid" in norm(c) for c in rows[header_row])
    institution = "E*TRADE" if is_etrade else "Brokerage"
    mask = None
    m = re.search(r"(?:-|x{2,}|\*{2,}|#)\s*(\d{4})\b", before, re.IGNORECASE)
    if m:
        mask = m.group(1)
    name_match = re.search(r"([A-Za-z][A-Za-z &]+?)\s*-\s*\d{4}", before)
    h = rows[header_row]
    c_sym = find_col(h, "symbol")
    c_qty = find_col(h, "quantity", "qty", "shares")
    c_price = find_col(h, "last price", "price", exclude=("paid", "change"))
    c_value = find_col(h, "value", "market value", "current value")
    c_paid = find_col(h, "price paid", "cost basis", "cost")
    acct = FileAccount(
        mask=mask,
        name=(name_match.group(1).strip() if name_match else f"{institution} account"),
        institution=institution,
        type="investment",
    )
    file_total = None
    for row in rows[header_row + 1 :]:
        if not row or not any(c.strip() for c in row):
            break  # end of the table
        symbol = row[c_sym].strip() if c_sym is not None and len(row) > c_sym else ""
        value = money(row[c_value]) if c_value is not None and len(row) > c_value else None
        if symbol.upper() == "TOTAL":
            file_total = to_cents(value)
            continue
        if not symbol:
            continue
        qty = money(row[c_qty]) if c_qty is not None and len(row) > c_qty else None
        price = money(row[c_price]) if c_price is not None and len(row) > c_price else None
        if value is None and qty is not None and price is not None:
            value = qty * price
        if value is None:
            continue
        is_cash = symbol.upper() in ("CASH", "MONEY MARKET", "SWEEP")
        paid = money(row[c_paid]) if c_paid is not None and len(row) > c_paid else None
        acct.holdings.append(
            HoldingData(
                account_external_id=mask or "file",
                symbol=symbol.upper()[:40],
                name="Cash" if is_cash else symbol.upper(),
                asset_class="Cash" if is_cash else "Stocks",
                quantity=qty if qty is not None else value,
                price=price if price is not None else Decimal(1),
                value_cents=to_cents(value) or 0,
                cost_basis_cents=to_cents(paid * qty) if (paid is not None and qty is not None and is_etrade) else None,
            )
        )
    if not acct.holdings:
        raise FileError("No positions found in this file.")
    acct.balance_cents = sum(x.value_cents for x in acct.holdings)
    acct.file_total_cents = file_total
    warnings = []
    if file_total is not None and abs(file_total - acct.balance_cents) > 100:
        warnings.append(
            f"The rows add up to ${acct.balance_cents / 100:,.2f} but the file's total says ${file_total / 100:,.2f}."
        )
    fmt = "E*TRADE positions" if is_etrade else "Positions"
    return ParsedFile("positions", fmt, [acct], today, warnings)


# ---- transactions


def parse_transactions(rows: list[list[str]], header_row: int, today: date, filename: str) -> ParsedFile:
    h = rows[header_row]
    c_date = find_col(h, "transaction date", "trans date", "date", "posting date", "posted date", "post date")
    c_desc = find_col(h, "description", "payee", "merchant", "name", "details", "transaction", "memo")
    c_amount = find_col(h, "amount", exclude=("balance",))
    c_debit = find_col(h, "debit", "withdrawal", "withdrawals")
    c_credit = find_col(h, "credit", "deposit", "deposits", exclude=("card",))
    c_category = find_col(h, "category")
    c_type = find_col(h, "type", "transaction type", "details")
    names = [norm(c) for c in h]
    # Apple Card's export (Wallet -> Apple Card -> Export Transactions): purchases positive, payments negative, and a
    # tidy Merchant column next to the raw Description.
    apple = "clearing date" in names and "purchased by" in names
    c_merchant = find_col(h, "merchant") if apple else None
    if c_date is None or c_desc is None or (c_amount is None and c_debit is None and c_credit is None):
        raise FileError("Couldn't find the date, description and amount columns in this file.")
    raw: list[tuple[date, Decimal, str, str | None, str, str | None]] = []
    skipped = 0
    for row in rows[header_row + 1 :]:
        if not row or not any(c.strip() for c in row):
            continue
        try:
            d = parse_date(row[c_date])
            desc = row[c_desc].strip()
            if c_amount is not None and money(row[c_amount]) is not None:
                amount = money(row[c_amount])
                split = False
            else:
                debit = money(row[c_debit]) if c_debit is not None else None
                credit = money(row[c_credit]) if c_credit is not None else None
                amount = (abs(credit) if credit else Decimal(0)) - (abs(debit) if debit else Decimal(0))
                split = True
        except IndexError:
            skipped += 1
            continue
        if d is None or amount is None or not desc:
            skipped += 1
            continue
        cat = row[c_category].strip() if c_category is not None and len(row) > c_category else None
        kind = row[c_type].strip() if c_type is not None and len(row) > c_type else ""
        shop = row[c_merchant].strip() if c_merchant is not None and len(row) > c_merchant else None
        raw.append((d, amount, desc, cat, kind, shop or None))
        _ = split
    if not raw:
        raise FileError("No transactions found in this file.")

    # One amount column: most banks write purchases as negative, most card exports as positive. A Type column
    # ("Purchase", "Payment", "Sale", "ACH_DEBIT"...) says which way round the file is; without one, mostly positive
    # amounts mean a card export.
    flipped = False
    if c_amount is not None and c_debit is None:
        votes = sum(direction_vote(kind, a) for _, a, _, _, kind, _ in raw)
        if votes:
            flipped = votes > 0
        else:
            positive = sum(1 for _, a, _, _, _, _ in raw if a > 0)
            flipped = apple or positive / len(raw) > 0.7
    seen: Counter = Counter()
    txns = []
    for d, amount, desc, cat, _kind, shop in raw:
        cents = to_cents(-amount if flipped else amount) or 0
        key = (d.isoformat(), cents, desc.lower())
        seen[key] += 1
        txns.append(
            TransactionData(
                account_external_id="file",
                external_id=stable_id(*key, seen[key]),
                date=d,
                amount_cents=cents,
                merchant=shop[:200] if shop else clean_merchant(desc),
                description=desc[:300],
                provider_category=cat[:80] if cat else None,
            )
        )
    warnings = [f"{skipped} rows couldn't be read and were left out."] if skipped else []
    acct = FileAccount(
        mask=last4_from_name(filename),
        name="Apple Card" if apple else "",
        institution="Apple" if apple else "",
        type="credit" if flipped or apple else "depository",
        transactions=txns,
    )
    kind = "Apple Card CSV" if apple else "Transactions CSV"
    return ParsedFile("transactions", kind, [acct], today, warnings, flipped)


# Words in a Type column that say which way the money went.
MONEY_OUT = re.compile(r"\b(purchase|sale|debit|fee|interest|installment|charge|withdrawal|atm)\b")
MONEY_IN = re.compile(r"\b(payment|credit|refund|return|deposit|cashback|reward)\b")


def direction_vote(kind: str, amount: Decimal) -> int:
    """+1 when a row's Type and sign say "positive = money out", -1 when they say "positive = money in", else 0."""
    kind = norm(kind)
    out, came_in = bool(MONEY_OUT.search(kind)), bool(MONEY_IN.search(kind))
    if out == came_in or not amount:
        return 0
    return (1 if out else -1) * (1 if amount > 0 else -1)


def last4_from_name(filename: str) -> str | None:
    m = re.search(r"(\d{4})(?!.*\d)", filename.rsplit(".", 1)[0])
    return m.group(1) if m and not re.fullmatch(r"(19|20)\d\d", m.group(1)) else None


_NOISE = re.compile(
    r"\b(pos|debit|purchase|card|recurring|ach|ppd|web|id|pmt|checkcard|visa|sq \*|tst\*|sq\*)\b|#\S+|\d{4,}|\*+",
    re.IGNORECASE,
)


def clean_merchant(description: str) -> str:
    """'SQ *BLUE BOTTLE COFFEE 1234 SAN FRANCISCO CA' -> 'Blue Bottle Coffee San Francisco' (best effort)."""
    text = re.sub(r"^(sq|tst|sp|pp)\s*\*\s*", "", description.strip(), flags=re.IGNORECASE)
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", re.sub(r"\s[:/#-]+(?=\s|$)", " ", text)).strip(" -*:#/")
    if not text:
        text = description.strip()
    if text.isupper() or text.islower():
        text = re.sub(r" (?:[A-Z]{2})$", "", text) if text.isupper() else text  # a trailing state code
        text = " ".join(w[:1].upper() + w[1:].lower() for w in text.split())
    return text[:200]


# ---- OFX / QFX


def _ofx_value(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<\r\n]*)", block, re.IGNORECASE)
    return m.group(1).strip() if m else None


def parse_ofx(text: str, today: date) -> ParsedFile:
    is_card = "<CCSTMTRS>" in text.upper()
    acct_id = _ofx_value(text, "ACCTID")
    org = _ofx_value(text, "ORG") or ""
    seen: Counter = Counter()
    txns = []
    for block in re.findall(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)", text, re.S | re.I):
        amount = money(_ofx_value(block, "TRNAMT"))
        posted = _ofx_value(block, "DTPOSTED") or ""
        try:
            d = datetime.strptime(posted[:8], "%Y%m%d").date()
        except ValueError:
            continue
        if amount is None:
            continue
        name = _ofx_value(block, "NAME") or _ofx_value(block, "MEMO") or "Unknown"
        fitid = _ofx_value(block, "FITID")
        cents = to_cents(amount) or 0
        key = (d.isoformat(), cents, name.lower())
        seen[key] += 1
        txns.append(
            TransactionData(
                account_external_id="file",
                external_id="ofx-" + hashlib.sha256(fitid.encode()).hexdigest()[:32]
                if fitid
                else stable_id(*key, seen[key]),
                date=d,
                amount_cents=cents,
                merchant=clean_merchant(name),
                description=(name + (" " + (_ofx_value(block, "MEMO") or "")).rstrip())[:300],
            )
        )
    ledger = re.search(r"<LEDGERBAL>(.*?)</LEDGERBAL>", text, re.S | re.I)
    balance = to_cents(money(_ofx_value(ledger.group(1), "BALAMT"))) if ledger else None
    if is_card and balance is not None:
        balance = -balance  # OFX card balances are negative when you owe; store what's owed as positive
    if not txns and balance is None:
        raise FileError("No transactions or balance found in this OFX/QFX file.")
    acct = FileAccount(
        mask=last4(acct_id),
        name="",
        institution=org,
        type="credit" if is_card else "depository",
        transactions=txns,
        balance_cents=balance,
    )
    return ParsedFile("transactions", "OFX/QFX statement", [acct], today)
