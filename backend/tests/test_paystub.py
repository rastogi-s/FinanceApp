from datetime import date

import pytest

from financeapp import budget, paystub
from tests.conftest import csrf, needs_db
from tests.pdf import pdf

ADP = [
    [(50, "ACME CORP")],
    [(50, "Earnings Statement")],
    [(50, "Period Beginning:"), (160, "09/28/2026")],
    [(50, "Period Ending:"), (160, "10/11/2026")],
    [(50, "Pay Date:"), (160, "10/16/2026")],
    [(50, "Earnings"), (300, "this period"), (450, "year to date")],
    [(50, "Regular"), (300, "4,500.00"), (450, "85,500.00")],
    [(50, "Gross Pay"), (300, "$4,500.00"), (450, "85,500.00")],
    [(50, "Statutory Deductions")],
    [(50, "Federal Income Tax"), (300, "-540.00"), (450, "10,260.00")],
    [(50, "Social Security Tax"), (300, "-279.00"), (450, "5,301.00")],
    [(50, "Medicare Tax"), (300, "-65.25"), (450, "1,239.75")],
    [(50, "CA State Income Tax"), (300, "-190.75"), (450, "3,624.25")],
    [(50, "CA SDI Tax"), (300, "-54.00"), (450, "1,026.00")],
    [(50, "Other Deductions")],
    [(50, "401K"), (300, "-450.00*"), (450, "8,550.00")],
    [(50, "HSA"), (300, "-150.00*"), (450, "2,850.00")],
    [(50, "Medical"), (300, "-120.00*"), (450, "2,280.00")],
    [(50, "Net Pay"), (300, "$2,651.00")],
    [(50, "Employer 401K Match"), (300, "225.00"), (450, "4,275.00")],
]

GUSTO = [
    [(50, "Pay stub for Jordan Example")],
    [(50, "Pay period: 10/01/2026 - 10/15/2026"), (350, "Pay day: 10/15/2026")],
    [(50, "Gross Earnings"), (350, "3,000.00"), (450, "57,000.00")],
    [(50, "Federal Income Tax"), (350, "300.00")],
    [(50, "Social Security"), (350, "186.00")],
    [(50, "Medicare"), (350, "43.50")],
    [(50, "Total Employee Taxes"), (350, "529.50"), (450, "10,060.50")],
    [(50, "Roth 401(k)"), (350, "150.00")],
    [(50, "Net Pay"), (350, "2,320.50")],
]


def test_reads_an_adp_style_stub():
    stub = paystub.to_json(paystub.read(pdf(ADP)))
    assert stub == {
        "date": "2026-10-16",
        "period_start": "2026-09-28",
        "period_end": "2026-10-11",
        "frequency": "biweekly",  # a 14-day period
        "gross_cents": 450_000,
        "taxes_cents": 112_900,  # the five tax lines, this period's column only
        "savings_cents": 60_000,  # 401(k) + HSA; medical is an "other deduction"
        "net_cents": 265_100,
        "employer_match_cents": 22_500,
        "detail": {
            "this": {
                "federal": 54_000,
                "social_security": 27_900,
                "medicare": 6_525,
                "state": 19_075,
                "state_other": 5_400,  # CA SDI isn't income tax
                "retirement": 45_000,
                "hsa": 15_000,
                "benefits": 12_000,  # medical, taken before tax
            },
            "ytd": {
                "gross": 8_550_000,
                "federal": 1_026_000,
                "social_security": 530_100,
                "medicare": 123_975,
                "state": 362_425,
                "state_other": 102_600,
                "retirement": 855_000,
                "hsa": 285_000,
                "benefits": 228_000,
            },
        },
        "warnings": [],
    }


def test_reads_a_gusto_style_stub_with_a_tax_total():
    stub = paystub.read(pdf(GUSTO))
    assert (stub.pay_date, stub.frequency) == (date(2026, 10, 15), "semimonthly")
    assert (stub.gross_cents, stub.taxes_cents, stub.savings_cents, stub.net_cents) == (
        300_000,
        52_950,
        15_000,
        232_050,
    )


def test_roth_rsu_and_taxable_wage_lines_are_sorted_out():
    rows = [
        *GUSTO[:3],
        [(50, "RSU Vest"), (350, "1,000.00"), (450, "12,000.00")],
        [(50, "Federal Taxable Wages"), (350, "3,850.00"), (450, "70,000.00")],  # a wage total, not a tax
        *GUSTO[3:],
    ]
    stub = paystub.read(pdf(rows))
    assert stub.taxes_cents == 52_950
    assert stub.detail == {
        "stock": 100_000,
        "federal": 30_000,
        "social_security": 18_600,
        "medicare": 4_350,
        "roth": 15_000,
    }
    assert stub.ytd == {"gross": 5_700_000, "stock": 1_200_000}


def test_frequency_from_words_wins_over_the_period():
    rows = [[(50, "Pay Frequency: Monthly")], *GUSTO[1:]]
    assert paystub.read(pdf(rows)).frequency == "monthly"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"date,amount\n", "isn't a PDF"),
        (pdf([]), "scan"),
        (pdf([[(50, "Quarterly newsletter")], [(50, "Nothing to see here 12.00")]]), "No gross or take-home"),
        (b"%PDF-1.4 broken", "couldn't be opened"),
    ],
)
def test_unreadable_files_say_why(data, message):
    with pytest.raises(paystub.StubError, match=message):
        paystub.read(data)


def test_missing_parts_and_numbers_that_dont_add_up_are_flagged():
    rows = [r for r in ADP if "Pay Date" not in r[0][1] and "Period" not in r[0][1]]
    rows = [[(50, "Net Pay"), (300, "9,999.00")] if r[0][1] == "Net Pay" else r for r in rows]
    stub = paystub.read(pdf(rows))
    assert stub.pay_date is None and len(stub.warnings) == 2


def test_next_payday():
    assert budget.next_payday(date(2026, 10, 16), "biweekly") == date(2026, 10, 30)
    assert budget.next_payday(date(2026, 10, 15), "semimonthly") == date(2026, 10, 31)
    assert budget.next_payday(date(2026, 10, 31), "semimonthly") == date(2026, 11, 15)
    assert budget.next_payday(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


@needs_db
def test_upload_then_save_with_frequency(client, home):
    home.setup()
    r = client.post("/api/paychecks/read", files={"file": ("stub.pdf", pdf(ADP))}, headers=csrf(client))
    assert r.status_code == 200, r.text
    read = r.json()
    assert (
        client.post("/api/paychecks/read", files={"file": ("x.csv", b"a,b")}, headers=csrf(client)).status_code == 422
    )
    body = {k: read[k] for k in ("date", "frequency", "period_start", "period_end")} | {
        k: read[k] for k in ("gross_cents", "taxes_cents", "savings_cents", "net_cents", "employer_match_cents")
    }
    b = client.post("/api/paychecks", json=body, headers=csrf(client)).json()
    assert b["paychecks"][0]["frequency"] == "biweekly"
    [schedule] = b["schedules"]
    assert schedule["next_date"] == "2026-10-30"
    assert schedule["monthly_gross_cents"] == 975_000  # $4,500 x 26 / 12
    bad = {**body, "frequency": "fortnightly"}
    assert client.post("/api/paychecks", json=bad, headers=csrf(client)).status_code == 422
