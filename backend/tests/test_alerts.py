from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from financeapp import alerts, budget, notify
from financeapp.models import AlertEvent, AlertRule, Preference, Quote
from tests.conftest import csrf, db_of, make_client, needs_db

# 2:30 PM Eastern on a Wednesday: markets open, not quiet hours.
NOON = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)
NIGHT = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)  # 11 PM Eastern


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str]]:
    """Captures what would go to the phone (as if ntfy is set up)."""
    out: list[tuple[str, str]] = []

    def deliver(settings, title, body, tags="lock", priority="high"):
        out.append((title, body))
        return "ntfy"

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(notify, "configured", lambda settings: True)
    return out


def set_quote(db, symbol: str, price: float, previous: float | None = None, at: datetime = NOON) -> None:
    q = db.get(Quote, symbol) or Quote(symbol=symbol, source="yahoo", price=Decimal(0), as_of=at)
    q.price, q.previous_close, q.as_of = Decimal(str(price)), Decimal(str(previous)) if previous else None, at
    db.merge(q)
    db.flush()


def test_quiet_hours_wrap_past_midnight():
    q = {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "America/New_York"}
    assert alerts.in_quiet_hours(q, NIGHT)
    assert not alerts.in_quiet_hours(q, NOON)
    assert alerts.in_quiet_hours(q, datetime(2026, 10, 21, 10, 59, tzinfo=UTC))  # 6:59 AM Eastern
    assert not alerts.in_quiet_hours(q, datetime(2026, 10, 21, 11, 0, tzinfo=UTC))  # 7:00 AM
    assert not alerts.in_quiet_hours({**q, "enabled": False}, NIGHT)
    assert alerts.in_quiet_hours({**q, "start": "01:00", "end": "05:00"}, datetime(2026, 10, 21, 7, 0, tzinfo=UTC))


@needs_db
def test_price_rule_fires_once_and_rearms(client, sent):
    with db_of(client).session() as db:
        rule = AlertRule(kind="price", params={"symbol": "VOO", "above": 600}, enabled=True, state={})
        db.add(rule)
        set_quote(db, "VOO", 590)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 0
        set_quote(db, "VOO", 601)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 1
        set_quote(db, "VOO", 605)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 0  # still above: no repeat
        set_quote(db, "VOO", 597)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 0  # dipped, but not 1% under: not re-armed
        set_quote(db, "VOO", 602)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 0
        set_quote(db, "VOO", 590)  # clearly back under
        alerts.evaluate(db, {"price"}, t=NOON)
        set_quote(db, "VOO", 600)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 1  # crossed again: a new alert
        events = db.scalars(select(AlertEvent).order_by(AlertEvent.id)).all()
        assert [e.title for e in events] == ["VOO is above $600.00", "VOO is above $600.00"]
        assert len({e.dedup_key for e in events}) == 2
        assert alerts.send_pending(db, client.app.state.settings, t=NOON) == 2
    assert sent == [("2 alerts", "• VOO is above $600.00\n• VOO is above $600.00")]


@needs_db
def test_daily_move_only_counts_today(client, sent):
    with db_of(client).session() as db:
        db.add(AlertRule(kind="price", params={"symbol": "AAPL", "move_percent": 3}, enabled=True, state={}))
        set_quote(db, "AAPL", 103.5, 100, at=NOON - timedelta(days=1))  # yesterday's move
        assert alerts.evaluate(db, {"price"}, t=NOON) == 0
        set_quote(db, "AAPL", 96, 100, at=NOON)
        assert alerts.evaluate(db, {"price"}, t=NOON) == 1
        assert alerts.evaluate(db, {"price"}, t=NOON) == 0
        e = db.scalar(select(AlertEvent))
        assert e.title == "AAPL is down 4% today"


def set_trend(db, symbol: str, high: float, avg200: float | None = None, day: date = date(2026, 10, 20)) -> None:
    q = db.get(Quote, symbol)
    q.year_high, q.trend_as_of = Decimal(str(high)), day
    q.average_200 = Decimal(str(avg200)) if avg200 else None
    db.flush()


@needs_db
def test_dip_from_high_steps_down_once_each(client, sent):
    with db_of(client).session() as db:
        db.execute(delete(AlertRule))  # not the starter rules
        db.add(AlertRule(kind="dip", params={"symbol": "VOO", "from_high_percent": 10}, enabled=True, state={}))
        set_quote(db, "VOO", 95)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 0  # no trend yet: nothing to judge
        set_trend(db, "VOO", 100)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 0  # 5% under
        set_quote(db, "VOO", 75)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 1  # straight through 10% and 20%: one alert, for 25%
        set_quote(db, "VOO", 88)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 0  # recovering isn't news
        set_quote(db, "VOO", 70)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 1  # 30%
        set_quote(db, "VOO", 95)
        alerts.evaluate(db, {"dip"}, t=NOON)  # back within 7%: every step re-armed
        set_quote(db, "VOO", 89)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 1
        titles = [e.title for e in db.scalars(select(AlertEvent).order_by(AlertEvent.id))]
        assert titles == [
            "VOO is 25% under its 52-week high",
            "VOO is 30% under its 52-week high",
            "VOO is 11% under its 52-week high",
        ]
        body = db.scalar(select(AlertEvent)).body
        assert body == "VOO is at $75.00; its 52-week high is $100.00. Information only, not advice."
        # A trend from weeks ago isn't used.
        set_quote(db, "VOO", 50, at=NOON - timedelta(days=10))
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 0


@needs_db
def test_dip_below_average_for_everything_held(client, sent, monkeypatch):
    monkeypatch.setattr(alerts.quotes, "holding_symbols", lambda db: {"VTI", "AAPL"})
    with db_of(client).session() as db:
        db.execute(delete(AlertRule))
        db.add(AlertRule(kind="dip", params={"symbol": None, "below_average": 200}, enabled=True, state={}))
        set_quote(db, "VTI", 290)
        set_trend(db, "VTI", 320, avg200=300)
        set_quote(db, "AAPL", 250)
        set_trend(db, "AAPL", 260, avg200=230)
        set_quote(db, "MSFT", 100)  # not held
        set_trend(db, "MSFT", 200, avg200=150)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 1
        set_quote(db, "VTI", 301)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 0  # not 1% over yet: not re-armed
        set_quote(db, "VTI", 299)
        assert alerts.evaluate(db, {"dip"}, t=NOON) == 0
        e = db.scalar(select(AlertEvent))
        assert e.title == "VTI dropped under its 200-day average"
        assert e.body == "VTI is at $290.00, 3.3% under its 200-day average of $300.00. Information only, not advice."
        assert "buy" not in (e.title + e.body).lower()


@needs_db
def test_budget_rule_once_a_month(client, sent):
    state = {
        "lines": [
            {
                "category_id": 7,
                "category": "Dining out",
                "kind": "flexible",
                "limit_cents": 40000,
                "spent_cents": 37000,
            },
            {"category_id": 8, "category": "Groceries", "kind": "flexible", "limit_cents": 60000, "spent_cents": 10000},
            {"category_id": 2, "category": "Rent", "kind": "fixed", "limit_cents": 200000, "spent_cents": 200000},
        ]
    }
    with db_of(client).session() as db:
        db.execute(delete(AlertRule))  # not the starter rules
        db.add(AlertRule(kind="budget", params={"category_id": 7, "percent": 90}, enabled=True, state={}))
        db.add(AlertRule(kind="budget", params={"category_id": None, "percent": 90}, enabled=True, state={}))
        assert alerts.evaluate(db, {"budget"}, budget_state=state, t=NOON) == 1  # Dining (92.5%), not everyday (47%)
        assert alerts.evaluate(db, {"budget"}, budget_state=state, t=NOON) == 0
        nov = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
        assert alerts.evaluate(db, {"budget"}, budget_state=state, t=nov) == 1  # a new month
        titles = [e.title for e in db.scalars(select(AlertEvent).order_by(AlertEvent.id))]
        assert titles == ["Dining out is at 92.5% of its budget", "Dining out is at 92.5% of its budget"]
        assert "$" not in "".join(e.body for e in db.scalars(select(AlertEvent)))  # no amounts on a lock screen


@needs_db
def test_concentration_rearms_a_point_under(client, sent):
    def companies(p: float) -> list[dict]:
        return [{"key": "cik:1", "symbol": "NVDA", "name": "NVIDIA", "percent": p,
                 "sources": [{"symbol": "direct"}, {"symbol": "VOO"}]}]  # fmt: skip

    with db_of(client).session() as db:
        db.execute(delete(AlertRule))  # not the starter rules
        db.add(AlertRule(kind="concentration", params={"percent": 10}, enabled=True, state={}))
        assert alerts.evaluate(db, {"concentration"}, companies=companies(10.4), t=NOON) == 1
        assert alerts.evaluate(db, {"concentration"}, companies=companies(9.5), t=NOON) == 0  # not re-armed yet
        assert alerts.evaluate(db, {"concentration"}, companies=companies(10.1), t=NOON) == 0
        alerts.evaluate(db, {"concentration"}, companies=companies(8.9), t=NOON)
        assert alerts.evaluate(db, {"concentration"}, companies=companies(11), t=NOON) == 1
        # Nothing to look at (look-through not loaded) is skipped, not taken as "all clear".
        assert alerts.evaluate(db, {"concentration"}, companies=None, t=NOON) == 0
        e = db.scalar(select(AlertEvent).order_by(AlertEvent.id))
        assert e.title == "NVDA is 10.4% of your portfolio" and "counting your funds" in e.body


@needs_db
def test_quiet_hours_hold_then_one_message(client, sent):
    settings = client.app.state.settings
    with db_of(client).session() as db:
        db.add(AlertRule(kind="price", params={"symbol": "VOO", "below": 500}, enabled=True, state={}))
        db.add(AlertRule(kind="price", params={"symbol": "VTI", "below": 250}, enabled=True, state={}))
        set_quote(db, "VOO", 480)
        set_quote(db, "VTI", 240)
        alerts.evaluate(db, {"price"}, t=NIGHT)
        assert alerts.send_pending(db, settings, t=NIGHT) == 0
        assert {e.status for e in db.scalars(select(AlertEvent))} == {"held"}
        morning = datetime(2026, 10, 22, 11, 5, tzinfo=UTC)
        assert alerts.send_pending(db, settings, t=morning) == 2
        assert alerts.send_pending(db, settings, t=morning) == 0  # nothing sent twice
        assert {e.status for e in db.scalars(select(AlertEvent))} == {"sent"}
    assert len(sent) == 1 and sent[0][0] == "2 alerts"


@needs_db
def test_failed_sends_retry_then_stop(client, monkeypatch):
    monkeypatch.setattr(notify, "configured", lambda settings: True)
    monkeypatch.setattr(notify, "deliver", lambda *a, **k: None)
    settings = client.app.state.settings
    with db_of(client).session() as db:
        db.add(AlertEvent(kind="price", title="t", body="b", dedup_key="x", status="pending", attempts=0))
        db.flush()
        for _ in range(alerts.MAX_ATTEMPTS):
            alerts.send_pending(db, settings, t=NOON)
        assert db.scalar(select(AlertEvent)).status == "failed"


@needs_db
def test_no_channel_keeps_alerts_in_the_app(client):
    with db_of(client).session() as db:
        db.add(AlertEvent(kind="price", title="t", body="b", dedup_key="x", status="pending", attempts=0))
        db.flush()
        alerts.send_pending(db, client.app.state.settings, t=NOON)
        assert db.scalar(select(AlertEvent)).status == "unsent"


@needs_db
def test_starter_rules_and_check_after_budget(client, home, sent, monkeypatch):
    home.setup()
    r = client.get("/api/alerts")
    assert r.status_code == 200
    kinds = sorted(x["kind"] for x in r.json()["rules"])
    assert kinds == ["budget", "budget", "concentration", "dip", "dip"]  # from the migrations
    assert "Any category is on pace to go over its budget" in [x["summary"] for x in r.json()["rules"]]
    monkeypatch.setattr(budget, "today", lambda: date(2026, 10, 21))
    assert alerts.check(db_of(client), client.app.state.settings, {"budget", "concentration"}) == 0


@needs_db
def test_rules_api(client, home, sent):
    home.setup()
    h = csrf(client)
    bad = [
        {"kind": "price", "symbol": "not a ticker", "above": 5},
        {"kind": "price", "symbol": "VOO", "above": 5, "below": 4},
        {"kind": "price", "symbol": "VOO"},
        {"kind": "budget", "category_id": None, "percent": 5},
        {"kind": "concentration", "percent": 0},
        {"kind": "dip", "symbol": None},
        {"kind": "dip", "from_high_percent": 10, "below_average": 200},
        {"kind": "dip", "below_average": 100},
        {"kind": "dip", "symbol": "not a ticker", "from_high_percent": 10},
        {"kind": "email-everyone"},
    ]
    for rule in bad:
        assert client.post("/api/alerts/rules", json={"rule": rule}, headers=h).status_code == 422, rule
    off = {"enabled": False, "start": "22:00", "end": "07:00", "timezone": "America/New_York"}
    assert client.put("/api/alerts/quiet", json=off, headers=h).status_code == 200  # whenever the test runs
    with db_of(client).session() as db:
        set_quote(db, "VOO", 650)
    r = client.post("/api/alerts/rules", json={"rule": {"kind": "price", "symbol": "voo", "above": 600}}, headers=h)
    assert r.status_code == 200, r.text
    rule = next(x for x in r.json()["rules"] if x["kind"] == "price")
    assert rule["summary"] == "VOO goes above $600.00" and rule["triggered"]
    assert r.json()["events"][0]["title"] == "VOO is above $600.00"  # checked right away
    assert sent and sent[0][0] == "VOO is above $600.00"
    r = client.patch(f"/api/alerts/rules/{rule['id']}", json={"enabled": False}, headers=h)
    assert not next(x for x in r.json()["rules"] if x["id"] == rule["id"])["enabled"]
    r = client.delete(f"/api/alerts/rules/{rule['id']}", headers=h)
    assert all(x["id"] != rule["id"] for x in r.json()["rules"])
    assert client.delete(f"/api/alerts/rules/{rule['id']}", headers=h).status_code == 404
    # A rule from another household member's session still needs the CSRF token.
    assert client.post("/api/alerts/rules", json={"rule": {"kind": "concentration", "percent": 5}}).status_code == 403

    q = {"enabled": True, "start": "21:30", "end": "06:45", "timezone": "America/Los_Angeles"}
    assert client.put("/api/alerts/quiet", json=q, headers=h).json()["quiet"] == q
    assert client.put("/api/alerts/quiet", json={**q, "timezone": "Mars/Base"}, headers=h).status_code == 422
    assert client.put("/api/alerts/quiet", json={**q, "start": "25:00"}, headers=h).status_code == 422

    assert client.post("/api/alerts/read", headers=h).status_code == 200
    assert client.get("/api/household").json().get("alerts", []) == []


@needs_db
def test_test_message_rate_limited(client, home, sent):
    home.setup()
    h = csrf(client)
    assert client.post("/api/alerts/test", headers=h).json() == {"channel": "ntfy"}
    assert client.post("/api/alerts/test", headers=h).status_code == 429
    with db_of(client).session() as db:
        db.merge(Preference(key="alert_test", value={"at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}))
    assert client.post("/api/alerts/test", headers=h).status_code == 200


def test_demo_alerts(tmp_path):
    with make_client(tmp_path, "", demo_mode=True) as c:
        r = c.get("/api/alerts")
        assert r.status_code == 200 and r.json()["rules"]
        assert c.post("/api/alerts/rules", json={"rule": {"kind": "concentration", "percent": 5}}).status_code == 403
