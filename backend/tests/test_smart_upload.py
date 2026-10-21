import io
import json
import zipfile
from datetime import date

import pytest
from openpyxl import Workbook
from PIL import Image

from financeapp.ai import documents, extract
from financeapp.ai.documents import DocumentError
from tests.conftest import csrf, needs_db
from tests.pdf import pdf
from tests.test_ai import CLAUDE_KEY, FakeProviders, add_claude, fake  # noqa: F401  (fake is a fixture)

STATEMENT = [
    [(50, "CHASE TOTAL CHECKING")],
    [(50, "SHUBHAM RASTOGI  123 Main Street, Apt 4B")],
    [(50, "Account Number: 000123456789  SSN 123-45-6789")],
    [(50, "10/02 TRADER JOE'S #123   -54.20")],
    [(50, "10/03 ACME CORP PAYROLL   3,200.00")],
    [(50, "Ending Balance   8,145.80")],
]

ANSWER = {
    "kind": "transactions",
    "institution": "Chase",
    "account_name": "Total Checking",
    "account_last4": "000123456789",
    "account_type": "depository",
    "as_of": "2026-10-10",
    "statement_balance": 8145.80,
    "transactions": [
        {"date": "2026-10-02", "description": "TRADER JOE'S #123", "amount": -54.20},
        {"date": "2026-10-03", "description": "ACME CORP PAYROLL", "amount": 3200.00},
        {"date": "not a date", "description": "junk", "amount": 1},
        {"date": "2099-01-01", "description": "From the future", "amount": 5},
    ],
    "holdings": [],
    "paystub": None,
    "notes": "Card ending 4111 1111 1111 1111 for Shubham Rastogi was mentioned.",
}


def png(width=400, height=300, exif: bool = False, fmt="PNG") -> bytes:
    img = Image.new("RGB", (width, height), "white")
    out = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "Phone maker"  # Make
        e[0x8825] = {2: (37.0, 46.0, 30.0)}  # GPS latitude
        img.save(out, format=fmt, exif=e)
    else:
        img.save(out, format=fmt)
    return out.getvalue()


def scan_pdf() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (850, 1100), "white").save(out, format="PDF")
    return out.getvalue()


def xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---- opening files


def test_pictures_are_reencoded_without_location_or_camera_details():
    original = png(3000, 2000, exif=True, fmt="JPEG")
    assert b"Phone maker" in original
    doc = documents.open_document(original)
    [p] = doc.pictures
    assert p.mime == "image/jpeg" and b"Phone maker" not in p.data
    with Image.open(io.BytesIO(p.data)) as img:
        assert max(img.size) == documents.LONG_SIDE and not img.getexif()


def test_a_scanned_pdf_becomes_pictures_and_a_text_pdf_becomes_text():
    doc = documents.open_document(scan_pdf())
    assert doc.kind == "scan" and len(doc.pictures) == 1
    doc = documents.open_document(pdf(STATEMENT))
    assert doc.kind == "pdf" and "TRADER JOE" in doc.text and not doc.pictures


def test_spreadsheets_open_as_csv_text_and_zip_bombs_dont():
    doc = documents.open_document(xlsx([["Date", "Description", "Amount"], ["10/02/2026", "Coffee", -4.5]]))
    assert doc.kind == "spreadsheet" and doc.sheets[0].startswith("Date,Description,Amount")
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", "<x/>")
        z.writestr("xl/big.xml", b"0" * (documents.MAX_UNZIPPED + 1))
    with pytest.raises(DocumentError, match="too big"):
        documents.open_document(bomb.getvalue())


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\x00\x01binary\x00stuff", "can't be read"),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20, "HEIC"),
        (b"\x89PNG\r\n\x1a\nnot really", "couldn't be opened"),
    ],
)
def test_files_that_cant_be_opened_say_why(data, message):
    with pytest.raises(DocumentError, match=message):
        documents.open_document(data)


def test_what_is_sent_is_redacted_and_what_comes_back_is_checked():
    doc = documents.open_document(pdf(STATEMENT))
    out = extract.build_request(doc, ["Shubham Rastogi"], ["me@example.com"])
    sent = out.request.text
    for secret in ["SHUBHAM", "RASTOGI", "Main Street", "000123456789", "123-45-6789"]:
        assert secret not in sent
    assert "[number ending 6789]" in sent and "-54.20" in sent
    parsed = extract.to_parsed(
        extract.Answer.model_validate(ANSWER), date(2026, 10, 21), ["Shubham Rastogi"], [], "claude-opus-5-5"
    )
    [acct] = parsed.accounts
    assert acct.mask == "6789" and acct.institution == "Chase" and acct.balance_cents == 814580
    assert [t.amount_cents for t in acct.transactions] == [-5420, 320000]
    assert any("2 row(s)" in w for w in parsed.warnings)
    notes = next(w for w in parsed.warnings if w.startswith("The AI noted"))
    assert "4111 1111" not in notes and "Shubham" not in notes


# ---- uploading


def upload(client, name, data, ai=False, path="/api/imports"):
    return client.post(path + ("?read_with_ai=true" if ai else ""), files={"file": (name, data)}, headers=csrf(client))


@needs_db
def test_a_spreadsheet_in_a_known_layout_needs_no_ai(client, home, fake):  # noqa: F811
    home.setup()
    rows = [["Posting Date", "Description", "Amount"], ["10/02/2026", "Coffee", -4.5], ["10/03/2026", "Pay", 100]]
    r = upload(client, "chase.xlsx", xlsx(rows))
    assert r.status_code == 200, r.text
    assert r.json()["accounts"][0]["transactions"] == 2 and r.json()["ai"] is None
    assert fake.sent == []


@needs_db
def test_without_ai_set_up_the_offer_says_so_and_nothing_is_sent(client, home, fake):  # noqa: F811
    home.setup()
    r = upload(client, "statement.pdf", pdf(STATEMENT))
    offer = r.json()["ai_offer"]
    assert offer["available"] is False and "isn't set up" in offer["problem"]
    assert offer["sends"] == "text" and "000123456789" not in offer["preview"]
    assert "1 Social Security number" in offer["redactions"]
    r = upload(client, "statement.pdf", pdf(STATEMENT), ai=True)
    assert r.status_code == 409
    assert fake.sent == []


@needs_db
def test_a_pdf_statement_is_read_with_ai_then_saved_like_any_file(client, home, fake):  # noqa: F811
    home.setup()
    add_claude(client)
    # The first name is known from the account; the surname is added by hand.
    r = client.put("/api/ai/settings", json={"hide_words": [" Rastogi ", "x"]}, headers=csrf(client))
    assert r.json()["hide_words"] == ["Rastogi"]
    offer = upload(client, "statement.pdf", pdf(STATEMENT)).json()["ai_offer"]
    assert offer["available"] is True and offer["provider"] == "Claude" and offer["model"] == "claude-opus-5-5"
    assert 0 < offer["max_cost"] < 0.5
    calls = len(fake.sent)
    fake.answer = json.dumps(ANSWER)
    r = upload(client, "statement.pdf", pdf(STATEMENT), ai=True)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["ai"]["model"] == "claude-opus-5-5" and preview["ai"]["cost"] > 0
    assert "read by AI" in preview["format"]
    [acct] = preview["accounts"]
    assert acct["mask"] == "6789" and acct["transactions"] == 2
    # Exactly one request went out, and it had the redacted text, not the account number or the name.
    assert len(fake.sent) == calls + 1
    sent = fake.sent[-1].content.decode()
    assert "000123456789" not in sent and "RASTOGI" not in sent and "123-45-6789" not in sent
    r = client.post(
        f"/api/imports/{preview['id']}/commit",
        json={"targets": [{"index": 0, "institution": "Chase", "name": "Checking", "type": "depository"}]},
        headers=csrf(client),
    )
    assert r.json() == {"holdings": 0, "transactions": 2, "accounts": 1}
    activity = client.get("/api/auth/audit").json()
    assert any(e["action"] == "ai_read_file" for e in activity)


@needs_db
def test_screenshots_only_go_to_a_provider_allowed_to_see_pictures(client, home, fake):  # noqa: F811
    home.setup()
    pid = add_claude(client)["providers"][0]["id"]
    offer = upload(client, "IMG_0001.PNG", png()).json()["ai_offer"]
    assert offer["sends"] == "pictures" and offer["available"] is False and "pictures" in offer["problem"]
    assert upload(client, "IMG_0001.PNG", png(), ai=True).status_code == 409
    client.patch(f"/api/ai/providers/{pid}", json={"allow_pictures": True}, headers=csrf(client))
    fake.answer = json.dumps(
        ANSWER
        | {
            "kind": "positions",
            "transactions": [],
            "statement_balance": 1500.0,
            "holdings": [
                {
                    "symbol": "VTI",
                    "name": "Vanguard Total Stock Market ETF",
                    "quantity": 5,
                    "price": 200,
                    "value": 1000,
                },
                {
                    "symbol": "SPAXX",
                    "name": "Fidelity Government Money Market",
                    "quantity": 500,
                    "price": 1,
                    "value": 500,
                },
            ],
        }
    )
    r = upload(client, "IMG_0001.PNG", png(), ai=True)
    assert r.status_code == 200, r.text
    [acct] = r.json()["accounts"]
    assert acct["holdings"] == 2 and acct["holdings_cents"] == 150000 and acct["file_total_cents"] == 150000
    body = json.loads(fake.sent[-1].content)
    assert body["messages"][0]["content"][0]["type"] == "image"


@needs_db
def test_a_scanned_pay_stub_fills_the_form_with_ai(client, home, fake):  # noqa: F811
    home.setup()
    pid = add_claude(client)["providers"][0]["id"]
    client.patch(f"/api/ai/providers/{pid}", json={"allow_pictures": True}, headers=csrf(client))
    r = upload(client, "stub.pdf", scan_pdf(), path="/api/paychecks/read")
    offer = r.json()["ai_offer"]
    assert offer["available"] is True and "scan" in offer["reason"] and offer["sends"] == "pictures"
    stub = {
        "pay_date": "2026-10-16",
        "period_start": "2026-09-28",
        "period_end": "2026-10-11",
        "frequency": "Bi-Weekly",
        "gross": 4500,
        "taxes": 1129,
        "pre_tax_savings": 600,
        "net": 2651,
        "employer_match": 225,
    }
    fake.answer = json.dumps(ANSWER | {"kind": "paystub", "transactions": [], "paystub": stub})
    r = upload(client, "stub.pdf", scan_pdf(), ai=True, path="/api/paychecks/read")
    assert r.status_code == 200, r.text
    read = r.json()
    assert read["frequency"] == "biweekly" and read["date"] == "2026-10-16"
    assert (read["gross_cents"], read["taxes_cents"], read["savings_cents"], read["net_cents"]) == (
        450000,
        112900,
        60000,
        265100,
    )
    assert "Check every number" in read["warnings"][0]
    # Something that isn't a pay stub isn't passed off as one.
    fake.answer = json.dumps(ANSWER)
    assert upload(client, "stub.pdf", scan_pdf(), ai=True, path="/api/paychecks/read").status_code == 422


@needs_db
def test_a_nonsense_answer_is_refused_and_still_counted(client, home, fake):  # noqa: F811
    home.setup()
    add_claude(client)
    fake.answer = "I can't help with that."
    r = upload(client, "statement.pdf", pdf(STATEMENT), ai=True)
    assert r.status_code == 502 and "expected format" in r.json()["detail"]
    usage = client.get("/api/ai").json()["usage"]
    assert usage[0]["task"] == "Reading uploads" and usage[0]["cost"] > 0


def count(schema, test) -> int:
    if isinstance(schema, list):
        return sum(count(s, test) for s in schema)
    if not isinstance(schema, dict):
        return 0
    return int(test(schema)) + sum(count(v, test) for v in schema.values())


def optional_fields(schema) -> int:
    if isinstance(schema, list):
        return sum(optional_fields(s) for s in schema)
    if not isinstance(schema, dict):
        return 0
    own = 0
    if schema.get("type") == "object" and "properties" in schema:
        own = len(set(schema["properties"]) - set(schema.get("required", [])))
    return own + sum(optional_fields(v) for v in schema.values())


def test_claude_gets_a_schema_without_unions(fake):  # noqa: F811
    """Claude refuses schemas with many union-typed fields (a 400 seen reading a pay stub photo), so nullable fields
    go to it as optional ones, and an answer that leaves them out still reads."""
    from financeapp.ai import providers

    doc = documents.open_document(png())
    out = extract.build_request(doc, [], [], "a pay stub")
    fake.answer = json.dumps({"kind": "paystub", "transactions": [], "holdings": [], "paystub": {"gross": 4500}})
    reply = providers.complete(providers.Endpoint("anthropic", CLAUDE_KEY, None), "claude-opus-5-5", out.request)
    sent = json.loads(fake.sent[-1].content)["output_config"]["format"]["schema"]
    assert count(sent, lambda s: "anyOf" in s or isinstance(s.get("type"), list)) == 0
    assert optional_fields(sent) <= 24  # Claude's limit on optional fields
    assert sent["required"] == ["kind", "transactions", "holdings"] and sent["additionalProperties"] is False
    answer = extract.parse_answer(reply.text)
    assert answer.paystub.gross == 4500 and answer.paystub.net is None and answer.institution is None
    # Other providers still get the strict, all-required schema.
    assert count(extract.SCHEMA, lambda s: "anyOf" in s) == 21
