from financeapp import w2
from tests.conftest import csrf, needs_db
from tests.pdf import pdf
from tests.test_ai import fake  # noqa: F401  (a fixture)

# ADP's W-2 and Earnings Summary as PDF text comes out of it, columns run together (made-up numbers).
ADP_W2 = [
    "Wage and Tax 2025",
    "W-2 Statement OMB No. 1545-0029",
    "ACME CORP",
    "Box 1 of W-2 Box 3 of W-2 Box 5 of W-2 Box 16 of W-2",
    "PAT DOE",
    "1 MAIN ST Gross Pay 15000000 150 000 00 150 000 00 9 000 00",
    "Plus GTL (C-Box 12) 3003000303000 300 00 20 00",
    "Less 401(k) (D-Box 12) N/A900000N/A 500 00",
    "Less Other Cafe 125 2200000020002000 00 150 00",
    "Less Cafe 125 HSA (W-Box 12) 330000300000 3 000 00 200 00",
    "128300.00 25000.00 Wages Over Limit N/A N/A",
    "Reported W-2 Wages 145,300.00 145,300.00",
    "145300.00 9008.60",
    "145300.00 2106.85 Note - Fringe benefits include : Other $100.00",
    "C 300.00 PAT DOE",
    "D 9000.00 1 MAIN ST",
    "W 4000.00",
    "AA 9000.00",
    "W 250.00",
]


def test_an_adp_w2_reads_gross_pay_and_the_employers_hsa_money():
    w = w2.parse(ADP_W2)
    assert w.year == 2025 and w.gross_cents == 150_000_00 and w.medicare_cents == 145_300_00
    assert w.codes["W"] == 4_000_00 and w.employee_hsa_cents == 3_000_00
    y = w2.to_year(w)
    assert y["base_cents"] == 150_000_00 and y["employer_hsa_cents"] == 1_000_00 and y["source"] == "w2"
    assert any("doesn't split base pay" in x for x in y["warnings"])


def test_a_w2_without_a_summary_uses_box_5_and_leaves_the_hsa_split_to_you():
    plain = [
        "2025 W-2 Wage and Tax Statement",
        "1 Wages, tips, other comp. 2 Federal income tax withheld",
        "88000.00 9000.00",
        "92000.00 5704.00",
        "95000.00 1377.50",
        "W 2500.00",
    ]
    y = w2.to_year(w2.parse(plain))
    assert y["year"] == 2025 and y["base_cents"] == 95_000_00 and y["employer_hsa_cents"] is None
    assert any("box 5" in x for x in y["warnings"]) and any("Box 12 W" in x for x in y["warnings"])
    assert w2.parse(["Pay stub", "Gross Pay 1,000.00"]) is None


@needs_db
def test_a_w2_pdf_is_read_without_ai(client, home, fake):  # noqa: F811
    home.setup()
    rows = [[(40, "2025 W-2 and EARNINGS SUMMARY")], [(40, "Wage and Tax Statement")]]
    rows += [[(40, "Gross Pay"), (200, "150,000.00"), (300, "150,000.00")]]
    rows += [[(40, "Less Cafe 125 HSA (W-Box 12)"), (200, "3,000.00"), (300, "3,000.00")]]
    rows += [[(40, "W 4000.00")]]
    r = client.post("/api/earnings/read?kind=year", files={"file": ("w2.pdf", pdf(rows))}, headers=csrf(client))
    assert r.status_code == 200, r.text
    y = r.json()
    assert y["year"] == 2025 and y["base_cents"] == 150_000_00 and y["employer_hsa_cents"] == 1_000_00
    assert fake.sent == []
