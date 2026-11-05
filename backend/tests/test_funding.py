import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from financeapp import alerts, funding, reminders, upcoming
from financeapp.funding import Reminder
from financeapp.models import (
    Account,
    AlertEvent,
    Balance,
    Connection,
    HoldingSnapshot,
    InvestmentTransaction,
)
from tests.conftest import csrf, db_of, needs_db
from tests.test_reminders import sent  # noqa: F401 (a fixture)

TODAY = date(2026, 11, 4)  # a Wednesday
THURSDAY = date(2026, 11, 5)


def test_dates_on_a_schedule():
    weekly = Reminder(1, "Buy", 1, 105_00, "week", date(2026, 10, 1))  # a Thursday
    assert funding.on_or_after(weekly, TODAY) == THURSDAY
    assert funding.on_or_after(weekly, THURSDAY) == THURSDAY
    assert funding.on_or_after(weekly, date(2026, 9, 1)) == date(2026, 10, 1)  # not before it starts
    assert funding.dates(weekly, TODAY, TODAY + timedelta(days=15)) == [
        THURSDAY,
        date(2026, 11, 12),
        date(2026, 11, 19),
    ]
    assert funding.schedule(weekly) == "Every Thursday"
    other = Reminder(1, "Buy", 1, 1, "2weeks", date(2026, 10, 29))
    assert funding.on_or_after(other, TODAY) == date(2026, 11, 12) and funding.schedule(other) == "Every other Thursday"
    monthly = Reminder(1, "Save", 1, 1, "month", date(2026, 1, 31))
    assert funding.on_or_after(monthly, date(2026, 2, 2)) == date(2026, 2, 28)  # a short month's last day
    assert funding.on_or_after(monthly, date(2026, 3, 1)) == date(2026, 3, 31)
    assert funding.schedule(monthly) == "Every month on the 31st"
    assert funding.cadence([7, 7, 7, 14, 7]) == "week" and funding.cadence([30, 31, 30]) == "month"
    assert funding.cadence([7, 30, 3]) is None


def brokerage(client, cash: float | None, buys: list[date] = (), dollars: float = 105) -> int:
    """An E*TRADE account with holdings (and a cash position when `cash` isn't None) and these buys."""
    with db_of(client).session() as db:
        conn = Connection(provider="plaid", institution="E*TRADE", state={}, status="active")
        db.add(conn)
        db.flush()
        a = Account(
            connection_id=conn.id, external_id="etrade", name="Brokerage", institution="E*TRADE", type="investment",
            mask="2600",
        )  # fmt: skip
        db.add(a)
        db.flush()
        rows = [("VTI", "US stocks", 5_000_00)] + ([("CUR:USD", "Cash", round(cash * 100))] if cash is not None else [])
        for symbol, cls, cents in rows:
            db.add(
                HoldingSnapshot(
                    date=TODAY, account_id=a.id, symbol=symbol, name=symbol, asset_class=cls,
                    quantity=Decimal(1), price=Decimal(cents) / 100, value_cents=cents,
                )
            )  # fmt: skip
        for d in buys:
            for symbol, part in (("VTI", 0.6), ("VXUS", 0.4)):  # one buy, split across two funds
                db.add(
                    InvestmentTransaction(
                        account_id=a.id, external_id=uuid.uuid4().hex, date=d, type="buy", subtype="buy",
                        symbol=symbol, name=symbol, quantity=Decimal(1), price=Decimal(1),
                        amount_cents=-round(dollars * 100 * part), source="plaid",
                    )
                )  # fmt: skip
        return a.id


@needs_db
def test_short_before_the_weekly_buy_pushes_once(client, home, sent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(reminders, "local_today", lambda db, t=None: TODAY)
    home.setup()
    aid = brokerage(client, cash=60)
    with db_of(client).session() as db:
        funding.save(db, [Reminder(1, "E*TRADE weekly buy", aid, 105_00, "week", date(2026, 10, 1), 10_00)])
        [c] = funding.check(db, TODAY)
        assert c.state == "short" and c.next == THURSDAY
        assert c.cash.cents == 60_00 and c.cash.source == "holdings"
        assert c.short_cents == 55_00 and c.needed_cents == 115_00  # $105 and the $10 cushion, less $60
        [row] = funding.status(db, TODAY)["short"]
        assert row["account"] == "E*TRADE Brokerage ••2600" and row["days_left"] == 1

        # Too early for the push (two days ahead by default), then once, then never twice.
        assert funding.queue(db, TODAY - timedelta(days=5)) == 0
        assert funding.queue(db, TODAY) == 1
        assert funding.queue(db, TODAY) == 0
        e = db.scalar(select(AlertEvent))
        assert e.kind == "funding" and e.link == "/" and e.dedup_key == f"funding:1:{THURSDAY}"
        assert e.title == "Add money to E*TRADE Brokerage ••2600"
        assert "tomorrow" in e.body and "$" not in e.body and "55" not in e.body
        alerts.send_pending(db, client.app.state.settings)
    assert sent == [(e.title, e.body, "high")]

    # Coming up shows it, with what to add.
    with db_of(client).session() as db:
        rows = [r for r in upcoming.build(db, TODAY)["events"] if r["kind"] == "funding"]
    assert [(r["date"], r["title"], r["cents"]) for r in rows] == [
        (THURSDAY.isoformat(), "E*TRADE weekly buy", -105_00)
    ]
    assert rows[0]["funding"]["state"] == "short" and rows[0]["funding"]["short_cents"] == 55_00

    # The money arrives: nothing's short, and Home says so.
    with db_of(client).session() as db:
        db.scalar(select(HoldingSnapshot).where(HoldingSnapshot.symbol == "CUR:USD")).value_cents = 300_00
        assert funding.status(db, TODAY)["short"] == []
    assert client.get("/api/household").json()["health"]["funding"] == []


@needs_db
def test_two_reminders_on_one_account_add_up_and_a_buy_made_today_isnt_counted_twice(client, home):
    home.setup()
    aid = brokerage(client, cash=150, buys=[THURSDAY])
    with db_of(client).session() as db:
        funding.save(
            db,
            [
                Reminder(1, "Weekly buy", aid, 105_00, "week", THURSDAY),
                Reminder(2, "Fund buy", aid, 50_00, "week", date(2026, 11, 6)),  # Fridays
            ],
        )
        a, b = funding.check(db, TODAY)
        assert a.state == "ok" and a.needed_cents == 105_00
        assert (
            b.state == "short"
            and b.needed_cents == 155_00
            and b.short_cents == 5_00
            and b.with_others == ["Weekly buy"]
        )
        # On Thursday the buy has gone through: next week's is the one to cover.
        a, _ = funding.check(db, THURSDAY)
        assert a.next == date(2026, 11, 12)


@needs_db
def test_quiet_when_it_cant_tell_and_the_bank_side(client, home):
    home.setup()
    aid = brokerage(client, cash=None)
    with db_of(client).session() as db:
        # Holdings with no cash position: none to spend, unless the brokerage says what's available.
        assert funding.cash_of(db, db.get_one(Account, aid)).cents == 0
        db.add(Balance(account_id=aid, date=TODAY, current_cents=5_000_00, available_cents=400_00))
        db.flush()
        assert funding.cash_of(db, db.get_one(Account, aid)).cents == 400_00
        funding.save(db, [Reminder(1, "Buy", aid, 500_00, "week", THURSDAY)])
        conn = db.scalar(select(Connection))
        conn.status = "stale"
        [c] = funding.check(db, TODAY)
        assert c.state == "unknown"  # a stopped connection is its own Needs you item
        assert funding.queue(db, TODAY) == 0


@needs_db
def test_found_in_activity_and_the_api(client, home, monkeypatch):
    monkeypatch.setattr(reminders, "local_today", lambda db, t=None: TODAY)
    home.setup()
    thursdays = [THURSDAY - timedelta(days=7 * n) for n in range(1, 7)]
    aid = brokerage(client, cash=20, buys=thursdays)
    h = csrf(client)
    s = client.get("/api/funding").json()
    [found] = s["suggestions"]
    assert found["account_id"] == aid and found["amount_cents"] == 105_00 and found["every"] == "week"
    assert found["start"] == THURSDAY.isoformat()
    assert found["schedule"] == "Every Thursday" and found["times"] == 6
    assert [a["id"] for a in s["accounts"]] == [aid]

    body = {k: found[k] for k in ("account_id", "amount_cents", "every", "start")} | {"name": "  E*TRADE   buy "}
    r = client.post("/api/funding", json=body, headers=h)
    assert r.status_code == 200, r.text
    [saved] = r.json()["reminders"]
    assert saved["name"] == "E*TRADE buy" and saved["state"] == "short" and r.json()["suggestions"] == []

    r = client.put(f"/api/funding/{saved['id']}", json={**body, "amount_cents": 10_00}, headers=h)
    assert r.json()["reminders"][0]["state"] == "ok"
    assert client.post("/api/funding", json={**body, "account_id": 999}, headers=h).status_code == 400
    assert client.post("/api/funding", json={**body, "amount_cents": 0}, headers=h).status_code == 422
    r = client.delete(f"/api/funding/{saved['id']}", headers=h)
    assert r.json()["reminders"] == [] and len(r.json()["suggestions"]) == 1
    r = client.post("/api/funding/dismiss", json={"key": found["key"]}, headers=h)
    assert r.json()["suggestions"] == []
