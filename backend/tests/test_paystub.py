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
            "deposits": [],
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


def test_stub_whose_amounts_lose_their_punctuation():
    """ADP stubs can come out as "Gross Pay $5 000 00 90 000 00", two columns to a line, deductions printed negative
    this period and blank (year to date only) otherwise. Made-up numbers, in that layout."""
    from financeapp import paystub

    lines = [
        "ACME, INC. Period Beginning: 10/04/2026",
        "1 MAIN ST Period Ending: 10/17/2026",
        "SAN JOSE, CA 95128-2155 Pay Date: 10/16/2026",
        "rate salary/hours this period year to date this period year to date",
        "Regular 4900 00 80 00 4 900 00 88 200 00 Emp Sup Life -5 00 90 00",
        "Ltd2 Plan 10 00 180 00 Espp_2 -490 00 8 820 00",
        "Restrict Stock 40 000 00 Health Savings -200 00* 3 600 00",
        "Gross Pay $4 910 00 128 380 00 Disq Espp 1 000 00",
        "Espp_1 4 000 00",
        "Federal Income Tax -700 00 12 600 00 Medicare Tax -71 20 1 281 60",
        "Social Security Tax 10 918 20 NC State Income Tax -190 00 3 420 00",
        "401K Pln -245 00* 4 410 00 401K Roth -300 00 5 400 00",
        "Dental -15 00* 270 00 401K Er Match 4 000 00",
        "Net Pay $2 693 80",
        "Net Check $0 00",
        "JANE DOE xxxxxxxxx1234 xxxx xxxx $2 154 80",
        "xxxx5678 xxxx xxxx $539 00",
    ]
    s = paystub.parse(lines)
    assert (s.gross_cents, s.net_cents) == (4_910_00, 2_693_80)
    assert s.taxes_cents == 700_00 + 71_20 + 190_00  # Social Security is maxed out: year to date only
    assert s.savings_cents == 490_00 + 200_00 + 245_00 + 300_00  # not Espp_1's year-to-date
    assert s.employer_match_cents == 0  # the match line is year to date only
    assert s.detail["espp"] == 490_00 and s.detail["roth"] == 300_00 and s.detail["hsa"] == 200_00
    assert s.ytd["gross"] == 128_380_00 and s.ytd["federal"] == 12_600_00
    assert s.frequency == "biweekly" and s.pay_date.isoformat() == "2026-10-16"
    assert not s.warnings
    assert s.deposits == [{"mask": "1234", "cents": 2_154_80}, {"mask": "5678", "cents": 539_00}]  # where it went
    assert (
        paystub.restore_amounts("MORRISVILLE NC 27560-5299 3060 OLSEN DR") == "MORRISVILLE NC 27560-5299 3060 OLSEN DR"
    )
