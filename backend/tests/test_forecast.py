from collections import defaultdict
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select

from financeapp import alerts, budget, forecast
from financeapp.models import AlertEvent, AlertRule
from tests.conftest import csrf, db_of, needs_db
from tests.test_alerts import sent  # noqa: F401  (a fixture)
from tests.test_budget import add_transactions, line

TODAY = date(2026, 10, 21)  # 20 days gone, 11 to come
OCT = date(2026, 10, 1)
MONTHS = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]
NOON = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)


def charges(rows: list[tuple[date, str, float, int]]) -> dict:
    """rows: (month, merchant, dollars, day)"""
    out: dict = defaultdict(list)
    for m, merchant, dollars, day in rows:
        out[m].append((merchant, round(dollars * 100), day))
    return out


def project(kind: str, limit: float, rows, today: date = TODAY, months=MONTHS) -> dict | None:
    c = charges(rows)
    spent = sum(x[1] for x in c.get(today.replace(day=1), []))
    return forecast.project(kind=kind, limit_cents=round(limit * 100), spent_cents=spent, month=today.replace(day=1),
                            today=today, months=months, charges=c, names={})  # fmt: skip


def test_bills_still_to_come_are_expected_once():
    rows = [(m, "landlord", 2000, 3) for m in MONTHS] + [(m, "verizon", 80, 25) for m in MONTHS]
    rows += [(OCT, "landlord", 2000, 3)]
    f = project("fixed", 2080, rows)
    assert f["due_cents"] == 8_000 and [d["day"] for d in f["due"]] == [25]
    assert f["projected_cents"] == 208_000 and f["status"] == "on_track"  # a bill landing on its limit is fine
    # Paid already: nothing more to come.
    f = project("fixed", 2080, [*rows, (OCT, "verizon", 80, 20)])
    assert f["due_cents"] == 0 and f["projected_cents"] == 208_000


def test_a_bill_a_week_late_is_taken_as_skipped():
    rows = [(m, "gym", 60, 5) for m in MONTHS]
    assert project("fixed", 60, rows, today=date(2026, 10, 12))["due_cents"] == 6_000  # 7 days late: still coming
    assert project("fixed", 60, rows, today=date(2026, 10, 13))["due_cents"] == 0


def test_a_shop_visited_weekly_isnt_a_monthly_charge():
    rows = [(m, "whole foods", 100, d) for m in MONTHS for d in (2, 9, 16, 23)]
    f = project("flexible", 450, rows)
    assert f["due_cents"] == 0 and f["pace_cents"] > 0


def test_everyday_pace_leans_on_this_month_as_it_goes():
    rows = [(m, "shop", 100, d) for m in MONTHS for d in (2, 9, 16, 23)]  # usually $400
    rows += [(OCT, f"cafe {d}", 19, d) for d in range(1, 21)]  # $380 in 20 days: a faster pace
    f = project("flexible", 450, rows)
    w = 20 / 31
    expect = 38_000 + round((w * 38_000 / 20 + (1 - w) * 40_000 / 31) * 11)
    assert f["projected_cents"] == expect and f["status"] == "heading_over"
    assert f["over_cents"] == expect - 45_000 and f["days_left"] == 11


def test_a_big_one_off_isnt_repeated():
    rows = [(m, "shop", 100, d) for m in MONTHS for d in (2, 16)]  # usually $200
    rows += [(OCT, "sofa", 500, 4), (OCT, "lamp", 40, 6)]
    f = project("flexible", 1000, rows)
    w = 20 / 31
    assert f["pace_cents"] == round((w * 4_000 / 20 + (1 - w) * 20_000 / 31) * 11)
    assert f["status"] == "on_track"


def test_subscriptions_inside_an_everyday_category():
    rows = [(m, "netflix", 23, 28) for m in MONTHS] + [(m, f"movie {m.month}", 30, 10) for m in MONTHS]
    f = project("flexible", 60, [*rows, (OCT, "cinema", 30, 10)])
    assert f["due_cents"] == 2_300
    assert f["projected_cents"] == 3_000 + 2_300 + f["pace_cents"]


def test_close_and_heading_over_need_a_real_margin():
    assert forecast.status(100_400, 50_000, 100_000) == "close"  # $4 over: not a flag
    assert forecast.status(103_000, 50_000, 100_000) == "heading_over"
    assert forecast.status(90_000, 50_000, 100_000) == "close"
    assert forecast.status(90_000, 50_000, 100_000, "fixed") == "on_track"
    assert forecast.status(120_000, 101_000, 100_000) == "over"


def test_without_history_it_waits_for_the_10th():
    rows = [(OCT, "cafe", 50, 2)]
    assert project("flexible", 400, rows, today=date(2026, 10, 9), months=[]) is None
    f = project("flexible", 400, rows, today=date(2026, 10, 10), months=[])
    assert f["pace_cents"] == round(5_000 / 9 * 22)


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(budget, "today", lambda: TODAY)


@needs_db
def test_budget_page_shows_the_forecast(client, home, fixed_today):
    home.setup()
    rows = []
    for m in range(5, 10):
        rows += [
            (f"2026-{m:02}-03", "Landlord", -2000, "Rent & mortgage"),
            (f"2026-{m:02}-25", "Verizon", -80, "Rent & mortgage"),
            (f"2026-{m:02}-12", "Whole Foods", -200, "Groceries"),
            (f"2026-{m:02}-19", "Whole Foods", -200, "Groceries"),
        ]
    rows += [
        ("2026-10-03", "Landlord", -2000, "Rent & mortgage"),
        *[(f"2026-10-{d:02}", "Whole Foods", -38, "Groceries") for d in range(2, 21, 2)],
    ]
    add_transactions(client, rows)
    b = client.get("/api/budget").json()
    rent, groceries = line(b, "Rent & mortgage"), line(b, "Groceries")
    assert rent["forecast"]["due"] == [{"merchant": "Verizon", "cents": 8_000, "day": 25}]
    assert rent["forecast"]["projected_cents"] == 208_000 and rent["forecast"]["status"] == "on_track"
    assert groceries["limit_cents"] == 44_000 and groceries["forecast"]["status"] == "heading_over"
    assert b["forecast"]["heading_over"] == [groceries["category_id"]] and b["forecast"]["days_left"] == 11


@needs_db
def test_heads_up_once_per_category_per_month(client, sent):  # noqa: F811
    def state(status: str) -> dict:
        f = {"projected_cents": 52_000, "status": status, "days_left": 11}
        return {
            "lines": [
                {"category_id": 7, "category": "Dining out", "kind": "flexible", "limit_cents": 40_000,
                 "spent_cents": 30_000, "forecast": f},
                {"category_id": 8, "category": "Groceries", "kind": "flexible", "limit_cents": 60_000,
                 "spent_cents": 50_000, "forecast": f},
                {"category_id": 9, "category": "Shopping", "kind": "flexible", "limit_cents": 60_000,
                 "spent_cents": 10_000, "forecast": None},
            ]
        }  # fmt: skip

    with db_of(client).session() as db:
        db.execute(delete(AlertRule))  # not the starter rules
        db.add(AlertRule(kind="budget", params={"category_id": None, "forecast": True}, enabled=True, state={}))
        db.add(AlertRule(kind="budget", params={"category_id": 8, "forecast": True}, enabled=False, state={}))
        assert alerts.evaluate(db, {"budget"}, budget_state=state("heading_over"), t=NOON) == 2
        assert alerts.evaluate(db, {"budget"}, budget_state=state("heading_over"), t=NOON) == 0
        alerts.evaluate(db, {"budget"}, budget_state=state("on_track"), t=NOON)
        assert alerts.evaluate(db, {"budget"}, budget_state=state("heading_over"), t=NOON) == 0  # still October
        assert alerts.evaluate(db, {"budget"}, budget_state=state("over"), t=NOON) == 0
        nov = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)
        assert alerts.evaluate(db, {"budget"}, budget_state=state("heading_over"), t=nov) == 2
        events = db.scalars(select(AlertEvent).order_by(AlertEvent.id)).all()
        assert events[0].title == "Dining out is on pace to go over budget"
        assert events[0].body == "At this pace it ends October about 30% over. 11 days left to slow down."
        assert "$" not in "".join(e.title + e.body for e in events)  # no amounts on a lock screen


@needs_db
def test_heads_up_rule_api(client, home):
    home.setup()
    h = csrf(client)
    with db_of(client).session() as db:
        db.execute(delete(AlertRule))
    r = client.post("/api/alerts/rules", json={"rule": {"kind": "budget", "forecast": True}}, headers=h)
    assert r.status_code == 200
    [rule] = r.json()["rules"]
    assert rule["params"] == {"category_id": None, "forecast": True}
    assert rule["summary"] == "Any category is on pace to go over its budget"
    bad = {"kind": "budget", "category_id": 999_999, "forecast": True}
    assert client.post("/api/alerts/rules", json={"rule": bad}, headers=h).status_code == 422
