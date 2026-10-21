from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from financeapp import budget, household, plan
from financeapp.api import plan_routes
from financeapp.models import Account, Balance, HoldingSnapshot, Paycheck
from tests.conftest import csrf, db_of, make_client, needs_db
from tests.test_budget import TODAY, add_transactions, history

pytestmark = needs_db


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for module in (budget, household, plan_routes):
        monkeypatch.setattr(module, "today", lambda: TODAY)


def test_the_arithmetic():
    assert plan.grow(100_00, 10_00, 0, 12) == 220_00
    # $1,000 at 12% a year (1% a month) for a year, nothing added.
    assert plan.grow(1_000_00, 0, 12, 12) == round(1_000_00 * 1.01**12)
    assert plan.target_for(60_000_00, 4) == 1_500_000_00
    assert plan.months_to(100, 100, 0, 6) == 0
    assert plan.months_to(1_200_00, 0, 100_00, 0) == 12
    assert plan.months_to(10**12, 0, 1, 0) is None  # never, within 60 years


def link_balance(client, dollars: float) -> None:
    with db_of(client).session() as db:
        a = db.scalar(select(Account).where(Account.external_id == "checking"))
        db.add(Balance(account_id=a.id, date=TODAY, current_cents=round(dollars * 100)))


def pay(client, day: str, gross: float, taxes: float, savings: float, net: float) -> None:
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date.fromisoformat(day),
                gross_cents=round(gross * 100),
                taxes_cents=round(taxes * 100),
                savings_cents=round(savings * 100),
                net_cents=round(net * 100),
            )
        )


def test_projection_and_financial_independence(client, home):
    home.setup()
    assert client.get("/api/plan").json() == {"demo": False, "linked": False}
    history(client)
    link_balance(client, 100_000)
    p = client.get("/api/plan").json()
    i = p["inputs"]
    assert p["linked"] and p["net_worth_cents"] == 100_000_00
    # No income yet, so nothing to project until a savings number is typed in.
    assert i["monthly_savings_cents"] is None and p["projection"] is None
    assert i["savings_source"]["kind"] == "none"
    assert client.get("/api/household").json()["projection"] is None
    # Regular spending: the median month (June: rent $2,000, groceries $500, Netflix $15) for a year; fat is half
    # again. Every one of those is a bill or an essential, so lean is the same here.
    regular = i["levels"]["regular"]
    assert (regular["yearly_cents"], regular["typed"]) == (12 * 251_500, False)
    assert regular["note"] == "From your median month of spending, May 2026 to Sep 2026."
    assert i["levels"]["fat"]["yearly_cents"] == round(12 * 251_500 * 1.5)
    assert i["levels"]["lean"]["yearly_cents"] == 12 * 251_500
    # All cash, so the middle return is cash's.
    assert i["returns"] == {"low": 0.0, "mid": 1.0, "high": 3.0} and i["returns_source"]["mix"]["cash"] == 100

    # Paychecks for the five full months: $9,000 gross, $2,000 taxes, $1,000 to the 401(k), $6,000 net.
    for m in range(5, 10):
        pay(client, f"2026-{m:02}-15", 9_000, 2_000, 1_000, 6_000)
        add_transactions(client, [(f"2026-{m:02}-15", "Acme payroll", 6_000, "Paycheck")])
    p = client.get("/api/plan").json()
    saved = [m["saved_cents"] for m in client.get("/api/budget").json()["savings_history"]]
    assert p["inputs"]["monthly_savings_cents"] == round(sum(saved) / len(saved))
    assert p["inputs"]["savings_source"]["kind"] == "savings_rate"
    proj = p["projection"]
    assert [b["year"] for b in proj["bands"]][:2] == [2026, 2027] and len(proj["bands"]) == plan.YEARS + 1
    assert proj["bands"][0]["mid"] == 100_000_00
    assert proj["bands"][10]["low"] < proj["bands"][10]["mid"] < proj["bands"][10]["high"]
    assert proj["target_cents"] == 12 * 251_500 * 25  # at 4%
    assert proj["fire_year"]["high"] <= proj["fire_year"]["mid"] <= proj["fire_year"]["low"]
    # Home and the brief get the same projection.
    assert client.get("/api/household").json()["projection"] == proj

    # Choices: lean spending, a 3.5% withdrawal rate, a typed savings number and return, a birth year.
    h = csrf(client)
    body = {"level": "lean", "withdrawal_rate": 3.5, "savings_cents": 3_000_00, "birth_year": 1990, "return_mid": 6}
    p = client.patch("/api/plan/settings", json=body, headers=h).json()
    assert p["inputs"]["savings_typed"] and p["inputs"]["monthly_savings_cents"] == 3_000_00
    assert p["projection"]["target_cents"] == round(12 * 251_500 / 0.035)
    assert p["inputs"]["birth_year"] == 1990
    assert p["inputs"]["returns"] == {"low": 4.0, "mid": 6.0, "high": 8.0} and p["inputs"]["returns_source"]["typed"]
    # A typed spending level, then back to the ones the app works out; null goes back to measured.
    p = client.patch("/api/plan/settings", json={"spending": {"lean": 40_000_00}}, headers=h).json()
    lean = p["inputs"]["levels"]["lean"]
    assert (lean["yearly_cents"], lean["typed"], lean["derived_cents"]) == (40_000_00, True, 12 * 251_500)
    p = client.patch(
        "/api/plan/settings", json={"spending": {"lean": None}, "savings_cents": None, "return_mid": None}, headers=h
    ).json()
    assert p["inputs"]["levels"]["lean"]["typed"] is False
    assert p["inputs"]["savings_typed"] is False and p["inputs"]["birth_year"] == 1990
    assert p["inputs"]["returns_source"]["typed"] is False and p["inputs"]["returns"]["mid"] == 1.0
    assert client.patch("/api/plan/settings", json={"withdrawal_rate": 9}, headers=h).status_code == 422
    assert client.patch("/api/plan/settings", json={"level": "huge"}, headers=h).status_code == 422
    assert client.patch("/api/plan/settings", json={"return_mid": 20}, headers=h).status_code == 422


def test_savings_from_whatever_there_is(client, home):
    """Without stubs for whole months, savings still comes from the stubs there are, then deposits."""
    home.setup()
    history(client)
    link_balance(client, 10_000)
    # Take-home deposits only: what landed minus spending.
    for m in range(5, 10):
        add_transactions(client, [(f"2026-{m:02}-15", "Acme payroll", 6_000, "Paycheck")])
    s = client.get("/api/plan").json()["inputs"]
    assert s["savings_source"]["kind"] == "take_home"
    assert "Add a pay stub" in s["savings_source"]["note"]
    # One biweekly stub from this month: $2,800 take-home and $460 to the 401(k), 26 a year.
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date(2026, 10, 16),
                frequency="biweekly",
                gross_cents=4_200_00,
                taxes_cents=900_00,
                savings_cents=460_00,
                net_cents=2_800_00,
            )
        )
    s = client.get("/api/plan").json()["inputs"]
    assert s["savings_source"]["kind"] == "pay_stubs"
    net, put_away = round(2_800_00 * 26 / 12), round(460_00 * 26 / 12)
    assert s["monthly_savings_cents"] == net + put_away - 251_500
    assert "$6,067 take-home a month and $997 into your 401(k)" in s["savings_source"]["note"]


def test_savings_from_the_last_90_days(client, home):
    home.setup()
    # Only this month so far: no full month yet.
    add_transactions(
        client,
        [("2026-10-02", "Acme payroll", 6_000, "Paycheck"), ("2026-10-05", "Landlord", -2_000, "Rent & mortgage")],
    )
    link_balance(client, 1_000)
    s = client.get("/api/plan").json()["inputs"]
    assert s["months_used"] == 0 and s["savings_source"]["kind"] == "recent"
    assert s["monthly_savings_cents"] == round((6_000_00 - 2_000_00) * 30 / 90)
    assert s["levels"]["regular"]["yearly_cents"] == round(2_000_00 * 30 / 90) * 12


def test_return_from_the_mix(client, home):
    home.setup()
    history(client)
    link_balance(client, 20_000)  # cash
    with db_of(client).session() as db:
        checking = db.scalar(select(Account).where(Account.external_id == "checking"))
        broker = Account(
            connection_id=checking.connection_id,
            external_id="broker",
            name="Brokerage",
            institution="Bank",
            type="investment",
        )
        db.add(broker)
        db.flush()
        db.add(Balance(account_id=broker.id, date=TODAY, current_cents=80_000_00))
        for symbol, cls, dollars in (("AAPL", "Stocks", 60_000), ("BND", "Bonds", 20_000)):
            db.add(
                HoldingSnapshot(
                    date=TODAY,
                    account_id=broker.id,
                    symbol=symbol,
                    name=symbol,
                    asset_class=cls,
                    quantity=Decimal(100),
                    price=Decimal(dollars) / 100,
                    value_cents=dollars * 100,
                )
            )
    i = client.get("/api/plan").json()["inputs"]
    # 60% stocks at 6.5%, 20% bonds at 2%, 20% cash at 0.5%: 4.4%, to the nearest half point.
    assert i["returns_source"]["mix"] == {"stocks": 60, "bonds": 20, "cash": 20, "other": 0}
    assert i["returns"] == {"low": 2.5, "mid": 4.5, "high": 6.5}
    assert i["returns_source"]["note"].startswith("From your mix today: 60% stocks, 20% bonds, 20% cash.")


def test_subscriptions_and_rising_categories(client, home):
    home.setup()
    rows = [("2026-06-01", "Corner shop", -5, "Shopping")]  # June 1st on: June to September are full months
    for m in range(6, 11):
        rows.append((f"2026-{m:02}-05", "NETFLIX.COM", -15.49 if m < 10 else -17.99, "Subscriptions"))
        rows.append((f"2026-{m:02}-09", "Gym", -40, "Subscriptions"))
        # Coffee every week: regular, but not a subscription.
        rows += [(f"2026-{m:02}-{d:02}", "Blue Bottle", -6, "Dining out") for d in (2, 9, 16, 23)]
    rows += [("2026-07-01", "Hardware store", -30, "Shopping"), ("2026-09-01", "Hardware store", -31, "Shopping")]
    # Dining out: about $100 a month in June to August (with the coffee), then $260 in September.
    rows += [(f"2026-{m:02}-20", "Bistro", -76, "Dining out") for m in (6, 7, 8)]
    rows += [("2026-09-20", "Bistro", -236, "Dining out")]
    add_transactions(client, rows)
    ideas = client.get("/api/plan").json()["ideas"]
    subs = {s["merchant"]: s for s in ideas["subscriptions"]}
    assert set(subs) == {"NETFLIX.COM", "Gym"}
    assert subs["Gym"]["monthly_cents"] == 40_00 and subs["Gym"]["yearly_cents"] == 480_00
    assert subs["NETFLIX.COM"]["went_up"] is True and subs["Gym"]["went_up"] is False
    assert ideas["subscriptions_yearly_cents"] == sum(s["yearly_cents"] for s in subs.values())
    rising = {r["category"]: r for r in ideas["rising"]}
    assert set(rising) == {"Dining out"}
    assert rising["Dining out"]["month"] == "2026-09-01"
    assert rising["Dining out"]["change_cents"] == 260_00 - 100_00


def test_setup_guide(client, home, monkeypatch):
    home.setup()
    g = client.get("/api/guide").json()
    steps = {s["id"]: s for s in g["steps"]}
    assert [s["id"] for s in g["steps"]][:4] == ["passkey", "accounts", "budget", "alerts"]
    assert g["needed"] == 4 and g["needed_done"] == 0 and not g["complete"] and not g["hidden"]
    assert steps["budget"]["waiting"] == "Link an account first."
    assert steps["alerts"]["waiting"] and steps["alerts"]["test"] is False  # no channel on this server
    assert steps["people"]["optional"] and steps["ai"]["optional"]

    history(client)
    h = csrf(client)
    assert client.post("/api/budget/draft", headers=h).status_code == 200
    client.patch("/api/plan/settings", json={"birth_year": 1988}, headers=h)
    steps = {s["id"]: s for s in client.get("/api/guide").json()["steps"]}
    assert steps["accounts"]["done"] and steps["budget"]["done"] and steps["budget"]["waiting"] is None
    assert steps["plan"]["done"] and not steps["paycheck"]["done"]

    g = client.put("/api/guide", json={"hidden": True}, headers=h).json()
    assert g["hidden"] is True
    assert client.get("/api/guide").json()["hidden"] is True


def test_demo_plan_and_guide(tmp_path):
    with make_client(tmp_path, "", demo_mode=True) as c:
        p = c.get("/api/plan").json()
        assert p["demo"] and p["projection"]["fire_year"]["mid"] is not None
        assert p["projection"] == c.get("/api/household").json()["projection"]
        assert p["ideas"]["subscriptions"] and p["ideas"]["rising"]
        assert c.get("/api/guide").json()["complete"] is True
        assert c.patch("/api/plan/settings", json={"level": "lean"}).status_code == 403
        assert c.put("/api/guide", json={"hidden": True}).status_code == 403
