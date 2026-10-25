import json
from datetime import date, timedelta

import pytest

from financeapp import budget, demo, earnings, household
from financeapp.api import earnings_routes, plan_routes
from financeapp.models import Paycheck
from tests.conftest import csrf, db_of, needs_db
from tests.pdf import pdf
from tests.test_ai import add_claude, fake  # noqa: F401  (fake is a fixture)
from tests.test_budget import history
from tests.test_paystub import ADP
from tests.test_plan import link_balance

TODAY = date(2026, 10, 25)
NO_PREFS = {"years": {}, "letters": []}


def stub(day: date, gross: int, **kw) -> Paycheck:
    this = kw.pop("this", {})
    return Paycheck(
        id=kw.pop("id", None),
        user_id=1,
        frequency="biweekly",
        date=day,
        gross_cents=gross,
        taxes_cents=round(gross * 0.25),
        savings_cents=0,
        net_cents=round(gross * 0.7),
        employer_match_cents=kw.pop("match", 0),
        detail={"this": this, "ytd": kw.pop("ytd", {})},
    )


def every_two_weeks(first: date, until: date):
    d = first
    while d <= until:
        yield d
        d += timedelta(days=14)


def by_year(page: dict) -> dict[int, dict]:
    return {r["year"]: r for r in page["years"]}


def amounts(row: dict) -> dict[str, int]:
    return {s["key"]: s["cents"] for s in row["sources"]}


def test_a_year_from_its_last_stubs_year_to_date_and_paychecks_without_stubs():
    # 2025: only the last stub is in, with the whole year to date. 2026: stubs, then two paychecks without one.
    last_2025 = stub(
        date(2025, 12, 26),
        400_000,
        this={"employer_hsa": 4_000},
        match=16_000,
        ytd={"gross": 117_000_00, "stock": 8_000_00, "bonus": 5_000_00, "match": 4_160_00, "employer_hsa": 1_040_00},
    )
    stubs_2026 = [
        stub(d, 420_000, match=16_800, this={"employer_hsa": 4_000})
        for d in every_two_weeks(date(2026, 1, 9), date(2026, 9, 25))
    ]
    stubs_2026[4] = stub(
        stubs_2026[4].date, 420_000 + 1_000_000, match=16_800, this={"bonus": 1_000_000, "employer_hsa": 4_000}
    )
    days = [earnings.Payday(d, 294_000, False) for d in (date(2026, 10, 9), date(2026, 10, 23))]
    page = earnings.assemble([last_2025, *stubs_2026], days, date(2025, 1, 2), NO_PREFS, TODAY)
    rows = by_year(page)

    y25 = rows[2025]
    assert y25["complete"] and amounts(y25) == {
        "base": 104_000_00,
        "bonus": 5_000_00,
        "rsu": 8_000_00,
        "other": 0,
        "match": 4_160_00,
        "employer_hsa": 1_040_00,
    }
    assert y25["pay_cents"] == 117_000_00 and y25["comp_cents"] == 117_000_00 + 5_200_00

    y26 = rows[2026]
    a = amounts(y26)
    assert a["bonus"] == 10_000_00  # the stub's own bonus line
    # 19 stubs, plus two paychecks that landed without one, before taxes from the usual stub (take-home / 0.7).
    assert a["base"] == 19 * 4_200_00 + 2 * 4_200_00
    assert y26["estimated_paychecks"] == 2 and next(s for s in y26["sources"] if s["key"] == "base")["how"] == "stubs"
    # On track: 4 more paychecks (Nov 6 to Dec 18) at the usual $4,200.
    assert y26["projected"]["paychecks_left"] == 4
    assert y26["full_pay_cents"] == y26["pay_cents"] + 4 * 4_200_00
    assert y26["growth"]["on_track"] and y26["growth"]["pay"] == round(
        (y26["full_pay_cents"] / 117_000_00 - 1) * 100, 1
    )
    # Raise: $4,200 x 26 against $4,000 x 26 is 5%, against 3.7% inflation (the latest 12 months): ahead by 1.3.
    r = y26["raise"]
    assert r["percent"] == 5.0 and r["inflation"]["percent"] == 3.7 and r["inflation"]["latest"]
    assert r["real"] == 1.3 and r["verdict"] == "ahead"


def test_a_big_stub_without_a_bonus_line_counts_the_extra_as_bonus():
    stubs = [stub(d, 400_000) for d in every_two_weeks(date(2026, 1, 9), date(2026, 3, 20))]
    stubs[2] = stub(stubs[2].date, 1_000_000)
    row = by_year(earnings.assemble(stubs, [], date(2026, 1, 2), NO_PREFS, TODAY))[2026]
    assert amounts(row)["bonus"] == 6_000_00 and row["bonus_guessed"]


def test_take_home_only_years_and_partial_years_are_not_compared():
    days = [earnings.Payday(d, 300_000, False) for d in every_two_weeks(date(2024, 6, 7), date(2026, 10, 9))]
    page = earnings.assemble([], days, date(2024, 6, 1), NO_PREFS, TODAY)
    rows = by_year(page)
    assert not rows[2024]["complete"] and rows[2025]["complete"]  # 2024 starts in June
    assert rows[2025]["take_home"] and rows[2025]["growth"] is None and rows[2026]["growth"] is None


def test_typed_years_letters_and_raise_vs_inflation():
    prefs = {
        "years": {
            "2023": {"base": 100_000_00, "bonus": 8_000_00, "note": "Joined", "source": "w2"},
            "2024": {"base": 101_000_00, "match": 4_000_00},
        },
        "letters": [{"id": "a", "kind": "promotion", "effective": "2026-11-01", "title": "Staff engineer",
                     "base_cents": 130_000_00, "bonus_target_percent": 15}],
    }  # fmt: skip
    stubs = [stub(d, 450_000) for d in every_two_weeks(date(2026, 1, 9), date(2026, 10, 23))]
    page = earnings.assemble(stubs, [], date(2026, 1, 2), prefs, TODAY)
    rows = by_year(page)
    assert rows[2023]["note"] == "Joined" and rows[2023]["typed"] == ["base", "bonus"] and rows[2023]["complete"]
    # 1% against 4.1% (2024's yearly average): behind.
    assert rows[2024]["raise"]["percent"] == 1.0 and rows[2024]["raise"]["verdict"] == "behind"
    assert 2025 not in rows  # nothing for it
    assert rows[2026]["growth"] is None  # 2025 is missing
    # The letter's new salary from Nov 1 for the rest of the year, and in the events of 2026.
    p = rows[2026]["projected"]
    assert p["raise_from"] == "2026-11-01" and p["paychecks_left"] == 5  # Oct 30 at the old pay, then 4 at the new
    assert rows[2026]["full_pay_cents"] == rows[2026]["pay_cents"] + 4_500_00 + 4 * round(130_000_00 / 26)
    assert rows[2026]["events"][0]["text"] == "Promoted to Staff engineer. Base $130,000. Bonus target 15%"
    assert page["now"]["coming"] == {"effective": "2026-11-01", "base_cents": 130_000_00}
    assert page["now"]["base_cents"] == 4_500_00 * 26


def test_sample_data():
    page = demo.earnings_page(TODAY)
    assert page["demo"] and len(page["years"]) == 5
    assert page["years"][0]["projected"] and page["years"][1]["raise"]["verdict"] == "ahead"
    json.dumps(page)


@pytest.fixture
def fixed_today(monkeypatch):
    for module in (earnings_routes, plan_routes, budget, household):
        monkeypatch.setattr(module, "today", lambda: TODAY)


@needs_db
def test_the_page_typed_years_and_a_letter_that_updates_plan(client, home, fixed_today):
    home.setup()
    history(client)
    link_balance(client, 10_000)
    with db_of(client).session() as db:
        for d in every_two_weeks(date(2026, 1, 9), date(2026, 10, 9)):
            db.add(stub(d, 450_000, ytd={}))
        db.commit()
    page = client.get("/api/earnings").json()
    assert page["years"][0]["year"] == 2026 and page["now"]["base_cents"] == 4_500_00 * 26

    r = client.put(
        "/api/earnings/years/2024", json={"base_cents": 100_000_00, "note": " Joined "}, headers=csrf(client)
    )
    assert r.status_code == 200, r.text
    row = by_year(r.json())[2024]
    assert row["note"] == "Joined" and amounts(row)["base"] == 100_000_00
    assert client.put("/api/earnings/years/2099", json={"note": "x"}, headers=csrf(client)).status_code == 422
    r = client.put("/api/earnings/years/2024", json={"note": None, "base_cents": None}, headers=csrf(client))
    assert 2024 not in by_year(r.json())

    before = client.get("/api/plan").json()["inputs"]
    letter = {"kind": "raise", "effective": "2026-12-01", "base_cents": 130_000_00, "bonus_target_percent": 10}
    r = client.post("/api/earnings/letters", json=letter, headers=csrf(client))
    assert r.status_code == 200, r.text
    page = r.json()
    assert by_year(page)[2026]["note"] == "Base $130,000. Bonus target 10%"
    assert page["now"]["coming"]["base_cents"] == 130_000_00
    after = client.get("/api/plan").json()["inputs"]
    # $13,000 more a year, $1,083 a month, less the usual stub's 25% taxes.
    assert after["measured_savings_cents"] - before["measured_savings_cents"] == round(13_000_00 / 12 * 0.75)
    assert after["savings_source"]["breakdown"][-1]["label"] == "Your raise to $130,000 from Dec 1, after taxes"

    bad = {"kind": "raise", "effective": "2026-12-01"}
    assert client.post("/api/earnings/letters", json=bad, headers=csrf(client)).status_code == 422
    lid = page["letters"][0]["id"]
    assert client.delete(f"/api/earnings/letters/{lid}", headers=csrf(client)).json()["letters"] == []
    assert client.delete("/api/earnings/letters/nope", headers=csrf(client)).status_code == 404


@needs_db
def test_raise_ahead_for_plan(client, home):
    home.setup()
    with db_of(client).session() as db:
        for d in every_two_weeks(date(2026, 1, 9), date(2026, 10, 9)):
            db.add(stub(d, 450_000))
        db.flush()
        assert earnings.raise_ahead(db, TODAY) is None
        earnings.add_letter(db, {"kind": "raise", "effective": "2026-11-01", "base_cents": 130_000_00})
        ahead = earnings.raise_ahead(db, TODAY)
        assert ahead["gross_cents"] == round((130_000_00 - 117_000_00) / 12)
        assert ahead["cents"] == round((130_000_00 - 117_000_00) / 12 * 0.75)
        # Once a stub from after the raise is in, the stubs show it.
        db.add(stub(date(2026, 11, 6), 500_000))
        db.flush()
        assert earnings.raise_ahead(db, TODAY) is None


@needs_db
def test_an_older_year_from_its_last_pay_stub_needs_no_ai(client, home, fixed_today, fake):  # noqa: F811
    home.setup()
    r = client.post("/api/earnings/read?kind=year", files={"file": ("stub.pdf", pdf(ADP))}, headers=csrf(client))
    assert r.status_code == 200, r.text
    y = r.json()
    assert y["year"] == 2026 and y["base_cents"] == 85_500_00 and y["match_cents"] == 4_275_00 and y["source"] == "stub"
    assert any("year to date leaves out" in w for w in y["warnings"])  # an October stub
    assert fake.sent == []


LETTER = [
    [(50, "Dear Shubham Rastogi,")],
    [(50, "Effective March 1, 2027 your base salary will increase to $130,000.00.")],
    [(50, "Your annual bonus target is 15% of base salary.")],
]


@needs_db
def test_a_raise_letter_is_read_with_ai_after_a_yes(client, home, fixed_today, fake):  # noqa: F811
    home.setup()
    # Without AI set up: the offer says so, and nothing is sent.
    r = client.post("/api/earnings/read?kind=letter", files={"file": ("l.pdf", pdf(LETTER))}, headers=csrf(client))
    offer = r.json()["ai_offer"]
    assert not offer["available"] and "Raise and bonus letters" in offer["reason"]
    add_claude(client)
    offer = client.post(
        "/api/earnings/read?kind=letter", files={"file": ("l.pdf", pdf(LETTER))}, headers=csrf(client)
    ).json()["ai_offer"]
    assert offer["available"] and "130,000.00" in offer["preview"] and "Shubham" not in offer["preview"]
    sent_before = len(fake.sent)
    fake.answer = json.dumps(
        {
            "kind": "raise",
            "effective_date": "2027-03-01",
            "title": "",
            "base_salary": 130000,
            "previous_base_salary": 0,
            "bonus_target_percent": 15,
            "bonus_target_amount": 0,
            "bonus_amount": 0,
            "stock_award_value": 0,
            "notes": "",
        }
    )
    r = client.post(
        "/api/earnings/read?kind=letter&read_with_ai=true", files={"file": ("l.pdf", pdf(LETTER))}, headers=csrf(client)
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["base_cents"] == 130_000_00 and got["bonus_target_percent"] == 15 and got["effective"] == "2027-03-01"
    assert got["previous_base_cents"] is None and got["bonus_cents"] is None
    body = json.loads(fake.sent[-1].content)
    assert len(fake.sent) == sent_before + 1 and "Shubham" not in json.dumps(body)
    schema = body["output_config"]["format"]["schema"]
    assert set(schema["required"]) == set(schema["properties"])  # nothing optional for Claude's limits


def test_year_to_date_pay_the_stubs_dont_name_is_other_pay_and_late_linked_years_are_partial():
    # Regular $7,780 paychecks; the last stub's year to date also has a $70,000 vest whose stub wasn't uploaded, and
    # that last stub prints no stock line. The paycheck account was linked in November 2025, so 2025 and 2024 are
    # only part of the year, even though another account reaches back further.
    stubs = [stub(d, 778_000) for d in every_two_weeks(date(2026, 9, 4), date(2026, 10, 16))]
    stubs[-1].detail["ytd"] = {"gross": 778_000 * 21 + 7_000_000}
    days = [earnings.Payday(d, 545_000, False) for d in every_two_weeks(date(2025, 11, 7), date(2025, 12, 19))]
    page = earnings.assemble(
        stubs, [earnings.Payday(date(2024, 12, 20), 200_000, False), *days], date(2024, 1, 2), NO_PREFS, TODAY
    )
    rows = by_year(page)

    now = amounts(rows[2026])
    assert now["base"] == 778_000 * 21 and now["other"] == 7_000_000
    assert next(s["how"] for s in rows[2026]["sources"] if s["key"] == "other") == "unsplit"
    assert rows[2026]["projected"]["paychecks_left"] == 5
    assert rows[2026]["full_pay_cents"] == 778_000 * 26 + 7_000_000

    assert not rows[2025]["complete"] and not rows[2024]["complete"]
    assert rows[2026]["growth"] is None and rows[2026]["raise"] is None and rows[2025]["raise"] is None
    assert page["linked_from"] == "2024-12-20"


def test_the_employers_hsa_seed_comes_once_a_year_and_the_match_every_paycheck():
    stubs = [
        stub(d, 778_000, match=23_000, this={"employer_hsa": 180_000, "employer_hsa_seed": 180_000} if i == 0 else {})
        for i, d in enumerate(every_two_weeks(date(2026, 1, 2), date(2026, 10, 16)))
    ]
    stubs[-1].detail["ytd"] = {"gross": 778_000 * 21, "match": 23_000 * 21, "employer_hsa": 180_000}
    row = by_year(earnings.assemble(stubs, [], date(2025, 1, 2), NO_PREFS, TODAY))[2026]
    full = {s["key"]: s["full_cents"] for s in row["sources"]}
    assert full["employer_hsa"] == 180_000 and full["match"] == 23_000 * 26
    # A paycheck without a stub carries the match, not the seed.
    assert earnings.Usual(stubs).hsa == 0 and earnings.Usual(stubs).seed == 180_000
