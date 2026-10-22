from datetime import date

import pytest

from financeapp import taxes
from financeapp.api import tax_routes
from financeapp.models import Paycheck
from tests.conftest import csrf, make_client, needs_db

TODAY = date(2026, 10, 21)
T = taxes.table(2026)
NO_INVESTMENTS = {
    "interest_cents": 0,
    "dividends_cents": 0,
    "qualified_cents": 0,
    "capital_gain_cents": 0,
    "basis": "none",
}


def stub(
    day: str, gross: float, detail: dict | None = None, taxes_total: float = 0, savings: float = 0, **kw
) -> Paycheck:
    cents = {k: round(v * 100) for k, v in (detail or {}).items()}
    ytd = {k: round(v * 100) for k, v in kw.pop("ytd", {}).items()}
    return Paycheck(
        id=kw.pop("id", None),
        user_id=kw.pop("user_id", 1),
        date=date.fromisoformat(day),
        frequency=kw.pop("frequency", "biweekly"),
        gross_cents=round(gross * 100),
        taxes_cents=round(taxes_total * 100)
        or sum(cents.get(k, 0) for k in ("federal", "state", "social_security", "medicare")),
        savings_cents=round(savings * 100),
        net_cents=0,
        detail={"this": cents, "ytd": ytd},
    )


def facts(stubs: dict | None = None, **kw) -> dict:
    return {
        "viewer_id": 1,
        "people": {1: "Shubham", 2: "Alex"},
        "stubs": stubs or {},
        "investments": kw.pop("investments", NO_INVESTMENTS),
        "unrealized_gain_cents": None,
        "birth_year": None,
        **kw,
    }


def profile(**kw) -> dict:
    amounts = kw.pop("amounts", {})
    return {
        **taxes.DEFAULTS,
        "filing_status": "single",
        **kw,
        "amounts": {k: round(v * 100) for k, v in amounts.items()},
    }


def test_bracket_arithmetic():
    rows = taxes.bands(100_000_00, T["federal"]["brackets"]["single"])
    # 10% of $12,400 + 12% of $38,000 + 22% of $49,600
    assert sum(r["tax_cents"] for r in rows) == 1_240_00 + 4_560_00 + 10_912_00
    # Long-term gains stack on top of ordinary income: $40,000 ordinary leaves $9,450 at 0%, the rest at 15%.
    gains = taxes.bands(20_000_00, T["federal"]["capital_gains"]["single"], start=40_000_00)
    assert [(r["rate"], r["cents"]) for r in gains] == [(0.0, 9_450_00), (0.15, 10_550_00)]


def test_pay_from_the_year_to_date_and_the_paychecks_left():
    # Social Security and Medicare are on pay less the HSA.
    detail = {"federal": 600, "state": 150, "social_security": 272.8, "medicare": 63.8, "retirement": 450, "hsa": 100}
    ytd = {"gross": 90_000, "federal": 12_000, "state": 3_000, "social_security": 5_456, "medicare": 1_276}
    s = stub("2026-10-16", 4_500, detail, ytd=ytd | {"retirement": 9_000})
    r = taxes.estimate(T, profile(), facts({1: [s]}), TODAY)
    items = {i["key"]: i for i in r["items"]}
    # Oct 30, Nov 13, Nov 27, Dec 11, Dec 25 still to come.
    assert r["people"][0]["paychecks_left"] == 5 and r["people"][0]["ytd_source"] == "stub"
    assert items["wages"]["cents"] == (90_000 + 5 * 4_500) * 100
    assert items["retirement"]["cents"] == (9_000 + 5 * 450) * 100
    assert items["federal_withheld"]["cents"] == (12_000 + 5 * 600) * 100
    # No HSA on the year-to-date column: scaled from this paycheck's share of gross.
    assert items["hsa_benefits"]["cents"] == round(100_00 * 90_000 / 4_500) + 5 * 100_00
    assert "the year-to-date on the Oct 16 stub" in items["wages"]["note"]
    f = r["federal"]
    assert f["agi_cents"] == items["wages"]["cents"] - items["retirement"]["cents"] - items["hsa_benefits"]["cents"]
    assert f["taxable_cents"] == f["agi_cents"] - 16_100_00
    assert f["balance_cents"] == f["tax_cents"] - items["federal_withheld"]["cents"]
    # Social Security and Medicare for the rest of the year follow the rates, so they match what's due.
    assert abs(r["fica"]["difference_cents"]) < 100 and r["fica"]["balance_cents"] == 0
    # North Carolina: 3.99% of AGI less the $12,750 standard deduction.
    assert r["state"]["tax_cents"] == round((f["agi_cents"] - 12_750_00) * 0.0399)


def test_a_stub_with_only_total_taxes_is_split_by_the_rates():
    s = stub("2026-10-16", 4_000, taxes_total=1_000, savings=400)
    r = taxes.estimate(T, profile(), facts({1: [s]}), TODAY)
    assert r["people"][0]["split_estimated"] and r["people"][0]["ytd_source"] == "estimated"
    split, estimated = taxes.split(s, 0.0399, round(12_750_00 / 26))
    assert estimated and split["social_security"] == 248_00 and split["medicare"] == 58_00
    assert split["state"] == round((4_000_00 - 400_00 - 12_750_00 / 26) * 0.0399)
    assert sum(split[k] for k in ("federal", "state", "social_security", "medicare")) == 1_000_00
    assert "split into federal, state" in r["assumptions"][0]


def test_social_security_stops_at_the_wage_base_and_extra_medicare_starts():
    s = stub("2026-10-16", 20_000, {"federal": 5_000, "state": 700, "social_security": 1_240, "medicare": 290},
             ytd={"gross": 180_000, "social_security": 11_160, "medicare": 2_610})  # fmt: skip
    r = taxes.estimate(T, profile(), facts({1: [s]}), TODAY)
    ss = next(i for i in r["items"] if i["key"] == "social_security_withheld")
    assert ss["cents"] == round(184_500_00 * 0.062)
    med = next(i for i in r["items"] if i["key"] == "medicare_withheld")
    wages = 280_000_00
    assert med["cents"] == 2_610_00 + round(100_000_00 * 0.0145) + round(80_000_00 * 0.009)
    assert r["fica"]["additional_medicare_cents"] == round((wages - 200_000_00) * 0.009)
    assert abs(r["fica"]["difference_cents"]) < 100 and abs(r["fica"]["balance_cents"]) < 100
    # Married, the extra Medicare starts at $250,000 together, but the employer withheld from $200,000: back at filing.
    joint = taxes.estimate(T, profile(filing_status="mfj"), facts({1: [s]}), TODAY)
    assert joint["fica"]["balance_cents"] == -round(50_000_00 * 0.009)


def test_gains_dividends_niit_credits_and_the_child_deduction():
    p = profile(
        filing_status="mfj",
        children=2,
        amounts={
            "spouse_wages": 150_000,
            "lt_gains": 30_000,
            "st_gains": -5_000,
            "dividends": 10_000,
            "interest": 2_000,
        },
    )
    s = stub("2026-10-16", 8_000, {"federal": 1_400, "state": 300, "social_security": 496, "medicare": 116},
             ytd={"gross": 160_000})  # fmt: skip
    r = taxes.estimate(T, p, facts({1: [s]}), TODAY)
    f = r["federal"]
    # Short-term losses come off long-term gains first; 90% of dividends are qualified.
    assert (f["gains_ordinary_cents"], f["gains_preferred_cents"]) == (0, 25_000_00)
    assert f["preferred_cents"] == 25_000_00 + 9_000_00
    assert f["agi_cents"] == (200_000 + 150_000 + 2_000 + 10_000 + 25_000) * 100
    assert f["deduction_cents"] == 32_200_00
    # Net investment income tax on investment income over $250,000 of AGI (here all of it is over).
    assert f["niit_cents"] == round(37_000_00 * 0.038)
    # $2,200 a child, less $50 per $1,000 over $400,000 (AGI is under it).
    assert f["child_credit_cents"] == 4_400_00
    # North Carolina's child deduction is $0 over $140,000 on a joint return.
    assert r["state"]["child_deduction_cents"] == 0
    assert any(i["key"] == "spouse_wages" for i in r["items"])


def test_capital_losses_and_itemizing_and_typed_numbers():
    p = profile(deduction="itemized", amounts={"st_gains": -10_000, "itemized": 30_000, "federal_withheld": 1_000})
    s = stub("2026-10-16", 4_000, {"federal": 400, "state": 150, "social_security": 248, "medicare": 58},
             ytd={"gross": 80_000})  # fmt: skip
    r = taxes.estimate(T, p, facts({1: [s]}), TODAY)
    f = r["federal"]
    assert f["capital_loss_cents"] == 3_000_00
    assert f["deduction_cents"] == 30_000_00 and f["itemizing"]
    withheld = next(i for i in r["items"] if i["key"] == "federal_withheld")
    assert withheld["typed"] and withheld["cents"] == 1_000_00 and withheld["derived_cents"] > 1_000_00
    # Owing more than $1,000 with a paycheck left: withhold more per paycheck, or pay by January 15.
    assert r["hints"]["federal"]["kind"] == "more"
    q = r["hints"]["quarterly"]
    assert q["federal_needed"] and q["due_dates"] == ["2027-01-15"]


def test_a_state_without_a_table_and_one_without_wage_tax():
    s = stub("2026-10-16", 4_000, {"federal": 400, "state": 0, "social_security": 248, "medicare": 58},
             ytd={"gross": 80_000})  # fmt: skip
    r = taxes.estimate(T, profile(state="TX"), facts({1: [s]}), TODAY)
    assert r["state"]["tax_cents"] == 0 and r["state"]["no_wage_tax"]
    r = taxes.estimate(
        T, profile(state="VA", state_rate=5.0, amounts={"state_itemized": 8_000}), facts({1: [s]}), TODAY
    )
    assert r["state"]["tax_cents"] == round((r["federal"]["agi_cents"] - 8_000_00) * 0.05)


def test_whose_pay_counts():
    mine = stub("2026-10-16", 4_000, {"federal": 400}, ytd={"gross": 80_000})
    theirs = stub("2026-10-16", 3_000, {"federal": 300}, ytd={"gross": 60_000}, user_id=2)
    both = facts({1: [mine], 2: [theirs]})
    single = taxes.estimate(T, profile(), both, TODAY)
    joint = taxes.estimate(T, profile(filing_status="mfj"), both, TODAY)
    assert [p["name"] for p in single["people"]] == ["Shubham"]
    assert [p["name"] for p in joint["people"]] == ["Shubham", "Alex"]
    nothing = taxes.estimate(T, profile(amounts={"stock_to_come": 10_000}), facts(), TODAY)
    assert nothing["federal"]["agi_cents"] == 10_000_00
    assert next(i for i in nothing["items"] if i["key"] == "federal_withheld")["cents"] == 2_200_00


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(tax_routes, "today", lambda: TODAY)


@needs_db
def test_the_page_and_its_answers(client, home, fixed_today):
    home.setup()
    r = client.get("/api/taxes").json()
    assert not r["answered"] and r["profile"]["state"] == "NC"
    body = {
        "filing_status": "mfj",
        "children": 1,
        "amounts": {"spouse_wages": 50_000_00, "lt_gains": -2_000_00},
    }
    r = client.patch("/api/taxes/settings", json=body, headers=csrf(client)).json()
    assert r["answered"] and r["profile"]["filing_status"] == "mfj"
    assert next(i for i in r["items"] if i["key"] == "spouse_wages")["cents"] == 50_000_00
    # null goes back to the app's number
    r = client.patch("/api/taxes/settings", json={"amounts": {"lt_gains": None}}, headers=csrf(client)).json()
    assert not next(i for i in r["items"] if i["key"] == "lt_gains")["typed"]
    for bad in ({"amounts": {"nope": 1}}, {"state": "XX"}, {"amounts": {"wages": -5}}, {"filing_status": "joint"}):
        assert client.patch("/api/taxes/settings", json=bad, headers=csrf(client)).status_code == 422

    # A pay stub with its detail is saved and counted.
    stub_body = {
        "date": "2026-10-16",
        "frequency": "biweekly",
        "gross_cents": 4_000_00,
        "taxes_cents": 900_00,
        "savings_cents": 400_00,
        "net_cents": 2_700_00,
        "detail": {"this": {"federal": 500_00, "state": 94_00, "social_security": 248_00, "medicare": 58_00,
                            "retirement": 400_00}, "ytd": {"gross": 80_000_00}},
    }  # fmt: skip
    assert client.post("/api/paychecks", json=stub_body, headers=csrf(client)).status_code == 200
    bad = {**stub_body, "detail": {"this": {"bogus": 1}}}
    assert client.post("/api/paychecks", json=bad, headers=csrf(client)).status_code == 422
    r = client.get("/api/taxes").json()
    assert r["people"][0]["ytd_source"] == "stub"
    assert next(i for i in r["items"] if i["key"] == "wages")["cents"] == (80_000 + 5 * 4_000) * 100


def test_sample_data(tmp_path):
    with make_client(tmp_path, "", demo_mode=True) as c:
        r = c.get("/api/taxes").json()
    assert r["demo"] and r["answered"] and r["total"]["tax_cents"] > 0


def payment(day: str, dollars: float, kind: str = "dividend", tax: str = "qualified", taxable: bool = True):
    from financeapp.income import Payment

    source = "bank" if kind == "interest" else "brokerage"
    return Payment(
        date.fromisoformat(day), 1, "Brokerage", "VTI", "VTI", round(dollars * 100), kind, source, tax, taxable
    )


def test_dividends_and_interest_from_the_payments():
    pays = [
        payment("2026-03-25", 300),
        payment("2026-06-25", 100, tax="ordinary"),
        payment("2026-09-30", 50, kind="interest", tax="ordinary"),
        payment("2026-09-30", 999, taxable=False),  # in an IRA: not taxed
        payment("2025-12-20", 400),  # last December: the guess for this December
        payment("2025-12-20", 250, kind="capital_gain", tax="unlabeled"),
        payment("2025-06-25", 777),  # already past this year's date: not counted
    ]
    inv = taxes.from_payments(pays, {}, date(2024, 10, 21), TODAY)
    assert inv["basis"] == "last_year"
    assert (inv["dividends_cents"], inv["qualified_cents"]) == (800_00, 700_00)
    assert (inv["interest_cents"], inv["capital_gain_cents"]) == (50_00, 250_00)
    assert inv["so_far"]["dividends_cents"] == 400_00

    s = stub("2026-10-16", 4_000, {"federal": 400}, ytd={"gross": 80_000})
    r = taxes.estimate(T, profile(), facts({1: [s]}, investments=inv), TODAY)
    items = {i["key"]: i for i in r["items"]}
    assert items["qualified_dividends"]["cents"] == 700_00
    assert items["lt_gains"]["cents"] == 250_00 and "capital gain distributions" in items["lt_gains"]["note"]
    # A typed dividend total keeps the payments' qualified share (7/8).
    r = taxes.estimate(T, profile(amounts={"dividends": 1_600}), facts({1: [s]}, investments=inv), TODAY)
    assert next(i for i in r["items"] if i["key"] == "qualified_dividends")["cents"] == 1_400_00

    # Without last year's history, this year so far is scaled to a whole year.
    inv = taxes.from_payments(pays, {}, date(2026, 1, 1), TODAY)
    assert inv["basis"] == "scaled" and inv["dividends_cents"] == round(400_00 * 365 / 294)
