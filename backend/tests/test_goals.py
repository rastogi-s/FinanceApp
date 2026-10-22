from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from financeapp import alerts, budget, goals, household
from financeapp.api import goal_routes
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import Account, AlertEvent, Balance, Connection, Goal, GoalFunding
from tests.conftest import csrf, db_of, needs_db
from tests.test_alerts import NOON, sent  # noqa: F401 - a fixture
from tests.test_budget import TODAY, history

pytestmark = needs_db


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for module in (budget, household, goals, goal_routes):
        monkeypatch.setattr(module, "today", lambda: TODAY)


def account(client, external_id: str, kind: str, dollars: float) -> int:
    with db_of(client).session() as db:
        a = db.scalar(select(Account).where(Account.external_id == external_id))
        if a is None:
            conn = Connection(provider="manual", institution="Bank", state={})
            db.add(conn)
            db.flush()
            a = Account(connection_id=conn.id, external_id=external_id, name=external_id.title(), institution="Bank",
                        type=kind)  # fmt: skip
            db.add(a)
            db.flush()
        b = db.get(Balance, (a.id, TODAY)) or Balance(account_id=a.id, date=TODAY, current_cents=0)
        b.current_cents = round(dollars * 100)
        db.merge(b)
        return a.id


def by_name(state: dict, name: str) -> dict:
    return next(g for g in state["goals"] if g["name"] == name)


def test_the_arithmetic():
    assert goals.needed_monthly(12_000_00, 0, 12, 0) == 1_000_00
    assert goals.needed_monthly(100, 200, 12, 5) == 0
    assert goals.needed_monthly(10_000_00, 4_000_00, 0, 5) == 6_000_00  # due now: all of it
    # At 6% a year, saving toward $10,000 in 24 months takes less than $10,000 / 24 a month.
    assert goals.needed_monthly(10_000_00, 0, 24, 6) < 10_000_00 / 24
    assert goals.months_between(date(2026, 10, 21), date(2028, 3, 1)) == 17


def test_share_out_fills_by_priority_then_the_rest():
    house = Goal(id=2, priority=1)
    car = Goal(id=3, priority=3)
    trip = Goal(id=4, priority=2)
    funding = [
        GoalFunding(goal_id=3, account_id=1, cents=30_000_00),
        GoalFunding(goal_id=2, account_id=1, cents=30_000_00),
        GoalFunding(goal_id=4, account_id=1, cents=None),
    ]
    counted = goals.share_out([house, car, trip], funding, {1: 50_000_00})
    # The house comes first, the car gets what's left of its $30,000, the rest of the account is empty.
    assert counted == {(2, 1): 30_000_00, (3, 1): 20_000_00, (4, 1): 0}


def test_emergency_fund_counts_only_cash_not_set_aside(client, home):
    home.setup()
    history(client)
    account(client, "checking", "depository", 10_000)
    hysa = account(client, "hysa", "depository", 60_000)
    assert client.get("/api/budget").json()["emergency"]["have_cents"] == 70_000_00

    body = {"name": "House down payment", "icon": "house", "color": "blue", "priority": 1,
            "target_cents": 100_000_00, "target_date": "2029-10-01", "funding": [{"account_id": hysa,
                                                                                  "cents": 40_000_00}]}  # fmt: skip
    g = client.post("/api/goals", json=body, headers=csrf(client)).json()
    house = by_name(g, "House down payment")
    assert house["saved_cents"] == 40_000_00 and house["percent"] == 40.0
    assert house["needed_cents"] and house["needed_cents"] < 60_000_00 / 36  # interest helps
    em = by_name(g, "Emergency fund")
    assert em["auto"] and em["saved_cents"] == 30_000_00
    b = client.get("/api/budget").json()["emergency"]
    assert (b["have_cents"], b["set_aside_cents"], b["picked"]) == (30_000_00, 40_000_00, False)

    # Picking accounts for the emergency fund: only what's picked counts.
    checking = account(client, "checking", "depository", 10_000)
    eid = em["id"]
    em_body = {"name": "Emergency fund", "priority": 1, "funding": [{"account_id": checking}], "emergency_months": 3}
    g = client.put(f"/api/goals/{eid}", json=em_body, headers=csrf(client)).json()
    em = by_name(g, "Emergency fund")
    assert not em["auto"] and em["saved_cents"] == 10_000_00
    assert g["emergency"]["months"] == 3
    assert em["target_cents"] == 3 * 255_000  # three months of essentials (bills and groceries from history())
    assert client.get("/api/budget").json()["emergency"]["have_cents"] == 10_000_00

    # The rest of an account goes to one goal only.
    rest = {**body, "name": "Car", "target_date": None, "funding": [{"account_id": checking}]}
    r = client.post("/api/goals", json=rest, headers=csrf(client))
    assert r.status_code == 422 and "already goes to Emergency fund" in r.json()["detail"]

    # The emergency fund can't be removed; another goal can, and its money goes back to the emergency fund.
    assert client.delete(f"/api/goals/{eid}", headers=csrf(client)).status_code == 422
    client.put(f"/api/goals/{eid}", json={**em_body, "funding": []}, headers=csrf(client))
    g = client.delete(f"/api/goals/{house['id']}", headers=csrf(client)).json()
    assert [x["name"] for x in g["goals"]] == ["Emergency fund"]
    assert by_name(g, "Emergency fund")["saved_cents"] == 70_000_00


def test_validation(client, home):
    home.setup()
    hysa = account(client, "hysa", "depository", 1_000)
    card = account(client, "card", "credit", 500)
    ok = {"name": "  Trip   to Japan ", "target_cents": 5_000_00}
    for bad, detail in [
        ({"target_cents": None}, "Enter an amount"),
        ({"target_date": "2020-01-01"}, "from this month on"),
        ({"funding": [{"account_id": card}]}, "checking, savings or investment"),
    ]:
        r = client.post("/api/goals", json={**ok, **bad}, headers=csrf(client))
        assert r.status_code == 422 and detail in r.json()["detail"], r.json()
    dup = {**ok, "funding": [{"account_id": hysa}, {"account_id": hysa, "cents": 100}]}
    assert client.post("/api/goals", json=dup, headers=csrf(client)).status_code == 422
    g = client.post("/api/goals", json={**ok, "icon": "<script>"}, headers=csrf(client)).json()
    trip = by_name(g, "Trip to Japan")
    assert trip["icon"] == "star" and trip["status"] == "open"


def test_monthly_from_savings_and_typed():
    em = Goal(id=1, kind="emergency", priority=1)
    house = Goal(id=2, kind="saving", priority=1, target_cents=50_000_00, target_date=date(2028, 10, 1),
                       rate_percent=0)  # fmt: skip
    car = Goal(id=3, kind="saving", priority=3, target_cents=20_000_00)
    trip = Goal(id=4, kind="saving", priority=2, target_cents=3_000_00, monthly_cents=500_00)
    state = {"months": 6, "monthly_cents": 1_000_00, "target_cents": 6_000_00, "have_cents": 3_000_00}
    out = goals.assemble([em, house, car, trip], [], {}, [], state, 4_000_00, TODAY)
    by_id = {r["id"]: r for r in out["goals"]}
    house_row, car_row, trip_row, em_row = by_id[2], by_id[3], by_id[4], by_id[1]
    # $50,000 in 24 months at 0%: $2,083.34 a month, from the $4,000 saved; the trip's typed $500 comes off first.
    assert house_row["needed_cents"] == 2_083_34 and house_row["monthly_source"] == "savings"
    assert trip_row["monthly_cents"] == 500_00 and trip_row["monthly_source"] == "typed"
    left = 4_000_00 - 500_00 - 2_083_34
    # What's left: three shares to the high-priority emergency fund, one to the low-priority car.
    assert em_row["monthly_cents"] == left * 3 // 4 and car_row["monthly_cents"] == left // 4
    assert house_row["status"] == "on_track" and car_row["status"] == "open"
    assert out["summary"]["short_cents"] == 0


def test_milestone_alerts_once_and_never_for_an_edit(client, home, sent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(alerts, "now", lambda: NOON)  # outside quiet hours, whenever the tests run
    home.setup()
    hysa = account(client, "hysa", "depository", 30_000)
    body = {"name": "House", "target_cents": 100_000_00, "funding": [{"account_id": hysa}]}
    client.post("/api/goals", json=body, headers=csrf(client))  # 30%: reached 25% by being set up, no alert
    settings = client.app.state.settings
    assert alerts.check(db_of(client), settings, {"goal"}) == 0

    account(client, "hysa", "depository", 80_000)  # 80%: past 50% and 75% at once, one alert
    assert alerts.check(db_of(client), settings, {"goal"}) == 1
    assert sent[-1][0] == "House is 75% of the way there" and "$" not in sent[-1][1]
    assert alerts.check(db_of(client), settings, {"goal"}) == 0  # no repeats

    # A smaller target typed in reaches 100%: no alert for the change itself.
    gid = next(g["id"] for g in client.get("/api/goals").json()["goals"] if g["name"] == "House")
    client.put(f"/api/goals/{gid}", json={**body, "target_cents": 80_000_00}, headers=csrf(client))
    assert alerts.check(db_of(client), settings, {"goal"}) == 0

    with db_of(client).session() as db:
        titles = [e.title for e in db.scalars(select(AlertEvent).where(AlertEvent.kind == "goal"))]
    assert titles == ["House is 75% of the way there"]
    rules = client.get("/api/alerts").json()
    assert "Any goal reaches 25%, 50%, 75% and 100%" in [r["summary"] for r in rules["rules"]]
    assert {"id": gid, "name": "House"} in rules["goals"]
    r = client.post("/api/alerts/rules", json={"rule": {"kind": "goal", "goal_id": gid}}, headers=csrf(client))
    assert "House reaches 25%, 50%, 75% and 100%" in [x["summary"] for x in r.json()["rules"]]
    r = client.post("/api/alerts/rules", json={"rule": {"kind": "goal", "goal_id": 999}}, headers=csrf(client))
    assert r.status_code == 422


def test_demo_goals_are_read_only(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    g = c.get("/api/goals").json()
    assert g["demo"] and [x["name"] for x in g["goals"]][:2] == ["Emergency fund", "House down payment"]
    house = by_name(g, "House down payment")
    assert house["saved_cents"] == 50_000_00 and house["status"] == "on_track"
    b = c.get("/api/budget").json()["emergency"]
    assert b["have_cents"] == by_name(g, "Emergency fund")["saved_cents"] and b["set_aside_cents"] == 52_500_00
    assert c.post("/api/goals", json={"name": "x", "target_cents": 100}).status_code == 403
