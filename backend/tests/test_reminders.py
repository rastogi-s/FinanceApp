from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select, update

from financeapp import alerts, notify, reminders
from financeapp.models import Account, AlertEvent, Balance, Connection
from tests.conftest import csrf, db_of, needs_db


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def deliver(settings, title, body, tags="lock", priority="high"):
        out.append((title, body, priority))
        return "ntfy"

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(notify, "configured", lambda settings: True)
    monkeypatch.setattr(alerts, "in_quiet_hours", lambda q, t: False)
    return out


def test_next_statement_after_the_newest_data():
    # Month end by default; the reminder comes a few days after the statement closes.
    assert reminders.next_statement("monthly", None, date(2026, 9, 30)) == date(2026, 10, 31)
    assert reminders.due_on("monthly", None, date(2026, 9, 30)) == date(2026, 11, 3)
    # Transactions part way through a period: that period's statement is the next one.
    assert reminders.next_statement("monthly", 15, date(2026, 10, 10)) == date(2026, 10, 15)
    assert reminders.next_statement("monthly", 15, date(2026, 10, 15)) == date(2026, 11, 15)
    # A closing day past the end of a short month is that month's last day.
    assert reminders.next_statement("monthly", 31, date(2026, 1, 31)) == date(2026, 2, 28)
    assert reminders.next_statement("monthly", None, date(2026, 12, 31)) == date(2027, 1, 31)
    assert reminders.next_statement("quarterly", None, date(2026, 9, 30)) == date(2026, 12, 31)
    assert reminders.due_on("weekly", None, date(2026, 10, 20)) == date(2026, 10, 27)


def manual(client, name="HSA", kind="investment") -> dict:
    r = client.post(
        "/api/accounts/manual",
        json={"name": name, "institution": "HealthEquity", "type": kind, "balance_cents": 620000},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    return r.json()["accounts"][0]


def local_today(client) -> date:
    with db_of(client).session() as db:
        return reminders.local_today(db)


def age(client, account_id: int, newest: date) -> None:
    """Moves the account's only balance (and when it was added) back to `newest`."""
    with db_of(client).session() as db:
        db.execute(update(Balance).where(Balance.account_id == account_id).values(date=newest))
        a = db.get_one(Account, account_id)
        a.created_at = a.created_at.replace(year=newest.year, month=newest.month, day=newest.day)


@needs_db
def test_reminder_falls_due_pushes_once_then_once_more(client, home, sent):
    home.setup()
    hsa = manual(client)
    today = local_today(client)
    assert hsa["reminder"]["every"] == "monthly" and not hsa["reminder"]["is_due"]
    newest = reminders.statement_on(*reminders.add_months(today.year, today.month, -2), None)
    age(client, hsa["id"], newest)
    due = reminders.due_on("monthly", None, newest)

    h = client.get("/api/household").json()
    [u] = h["health"]["uploads"]
    assert u["account_id"] == hsa["id"] and u["newest"] == newest.isoformat() and u["days_late"] == (today - due).days

    with db_of(client).session() as db:
        assert reminders.queue(db, due) == 1
        assert reminders.queue(db, due + timedelta(days=1)) == 0  # the same due date: never twice
        alerts.send_pending(db, client.app.state.settings)
        assert reminders.queue(db, due + timedelta(days=reminders.AGAIN_DAYS)) == 1  # one follow-up
        assert reminders.queue(db, due + timedelta(days=30)) == 0  # then it stays in the app
        alerts.send_pending(db, client.app.state.settings)
    assert len(sent) == 2
    title, body, priority = sent[0]
    assert title == "Time to upload HealthEquity HSA" and "Type in the new one" in body and priority == "default"
    assert "$" not in title + body  # nothing about the balance on a lock screen

    # Typing in a new balance is new data: nothing's due any more.
    client.put(f"/api/accounts/{hsa['id']}/balance", json={"balance_cents": 630000}, headers=csrf(client))
    assert client.get("/api/household").json()["health"]["uploads"] == []


@needs_db
def test_snooze_settings_and_which_accounts(client, home):
    home.setup()
    hsa = manual(client)
    house = manual(client, "House", "other")
    assert house["reminder"]["every"] == "off"  # a house's value isn't a statement
    today = local_today(client)
    newest = reminders.statement_on(*reminders.add_months(today.year, today.month, -2), None)
    age(client, hsa["id"], newest)
    age(client, house["id"], newest)
    assert [u["account_id"] for u in client.get("/api/household").json()["health"]["uploads"]] == [hsa["id"]]

    r = client.post(f"/api/accounts/{hsa['id']}/reminder/snooze", json={"days": 3}, headers=csrf(client))
    assert r.status_code == 200
    assert client.get("/api/household").json()["health"]["uploads"] == []
    with db_of(client).session() as db:
        assert reminders.queue(db, today) == 0  # snoozed: no push either

    r = client.put(f"/api/accounts/{hsa['id']}/reminder", json={"every": "off"}, headers=csrf(client))
    assert r.json()["accounts"][0]["reminder"]["every"] == "off"
    assert r.json()["accounts"][0]["reminder"]["due"] is None
    r = client.put(f"/api/accounts/{hsa['id']}/reminder", json={"every": "monthly", "day": 15}, headers=csrf(client))
    rem = r.json()["accounts"][0]["reminder"]
    assert rem["day"] == 15 and rem["is_due"] and rem["snoozed_until"] is None  # a new cadence ends the snooze
    r = client.put(f"/api/accounts/{hsa['id']}/reminder", json={"every": "daily"}, headers=csrf(client))
    assert r.status_code == 422

    # Hidden, not counted, and synced accounts never need an upload.
    with db_of(client).session() as db:
        db.execute(update(Account).where(Account.id == hsa["id"]).values(hidden=True))
        assert reminders.status(db, today) == [r for r in reminders.status(db, today) if r["account_id"] == house["id"]]
        conn = db.scalar(select(Connection).join(Account).where(Account.id == house["id"]))
        conn.provider = "plaid"
        assert reminders.status(db, today) == []
        db.execute(delete(AlertEvent))
    r = client.put(f"/api/accounts/{house['id']}/reminder", json={"every": "monthly"}, headers=csrf(client))
    assert r.status_code == 400


def test_wording_names_the_statement_month():
    r = {"name": "HealthEquity HSA ••0456", "every": "monthly", "provider": "file", "statement": "2026-09-30"}
    title, body = reminders.wording(r)
    assert title == "Time to upload HealthEquity HSA ••0456"
    assert body.startswith("Its September statement should be out by now.") and "Upload it" in body
    assert reminders.wording({**r, "every": "weekly"})[1].startswith("It's been a week")
