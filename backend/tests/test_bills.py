from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update

from financeapp import alerts, bills, upcoming
from financeapp.models import Account, AlertEvent, Connection, Transaction
from tests.conftest import csrf, db_of, needs_db
from tests.test_budget import add_transactions
from tests.test_reminders import local_today, sent  # noqa: F401 (a fixture)

TODAY = date(2026, 10, 28)


def monthly(day: int, months: range, dollars: float, merchant: str, category: str | None = "Utilities") -> list:
    return [(f"2026-{m:02}-{day:02}", merchant, dollars, category) for m in months]


def synced(client, on: date) -> None:
    """Makes the checking account a linked one, last synced on `on`."""
    with db_of(client).session() as db:
        a = db.scalar(select(Account).where(Account.external_id == "checking"))
        c = db.get_one(Connection, a.connection_id)
        c.provider, c.status = "plaid", "active"
        c.last_synced_at = datetime(on.year, on.month, on.day, 12, tzinfo=UTC)


def status(client, today: date = TODAY) -> dict:
    with db_of(client).session() as db:
        return bills.status(db, today)


def test_rhythm_from_payments():
    assert bills.cadence_of([31, 30, 31, 30]) == ("monthly", 30)
    assert bills.cadence_of([61, 59, 62]) == ("every two months", 61)
    assert bills.cadence_of([92, 90]) == ("quarterly", 91)
    assert bills.cadence_of([7, 7, 7]) is None  # weekly isn't a utility rhythm
    assert bills.cadence_of([30, 30, 12, 45]) is None  # all over the place
    assert bills.cadence_of([30, 31, 61, 30]) == ("monthly", 30)  # one missed month doesn't hide it
    assert bills.ordinal(1) == "1st" and bills.ordinal(12) == "12th" and bills.ordinal(22) == "22nd"


@needs_db
def test_late_power_bill_pushes_once_and_stops_once_paid(client, home, sent):  # noqa: F811
    home.setup()
    power = {5: -150, 6: -210, 7: -230, 8: -190, 9: -183.07}
    rows = [(f"2026-{m:02}-{d:02}", "DUKE ENERGY 102394 ONLINE PMT", v, None) for m, v in power.items() for d in [14]]
    rows += monthly(5, range(5, 11), -70, "Verizon Wireless", "Phone & internet")
    rows += monthly(3, range(5, 11), -2000, "Landlord", "Rent & mortgage")  # a bill, not a utility
    rows += [("2026-10-27", "Coffee", -5, "Dining out")]
    add_transactions(client, rows)
    synced(client, TODAY)

    s = status(client)
    [late] = s["late"]
    assert late["name"] == "Duke Energy" and late["cadence"] == "monthly" and late["usual_day"] == 14
    assert late["usual_cents"] == 190_00
    assert late["expected"] == "2026-10-14" and late["late_on"] == "2026-10-17" and late["days_late"] == 11
    assert late["href"] == "/spending?q=duke%20energy"
    # Verizon came on the 5th as usual; its next one is coming up, not late.
    assert [r["name"] for r in s["soon"]] == ["Verizon Wireless"] and s["soon"][0]["expected"] == "2026-11-05"

    with db_of(client).session() as db:
        assert bills.queue(db, TODAY) == 1
        assert bills.queue(db, TODAY + timedelta(days=1)) == 0  # the same period: never twice
        e = db.scalar(select(AlertEvent))
        assert e.kind == "bill" and e.link == "/" and e.rule_id is None
        assert e.title == "Check your utilities: Duke Energy"
        assert "around the 14th" in e.body and "$" not in e.body and "190" not in e.body
        alerts.send_pending(db, client.app.state.settings)
    assert sent == [(e.title, e.body, "high")]  # like a card payment due: a missed bill costs money

    # Paid (pending still counts): nothing late, and next month's is expected a month on.
    add_transactions(client, [("2026-10-28", "DUKE ENERGY 553311 ONLINE PMT", -201.13, None)])
    with db_of(client).session() as db:
        db.execute(update(Transaction).where(Transaction.amount_cents == -20113).values(pending=True))
    s = status(client)
    assert s["late"] == [] and [r["name"] for r in s["soon"]] == ["Verizon Wireless"]
    with db_of(client).session() as db:
        assert bills.queue(db, TODAY) == 0


@needs_db
def test_quiet_when_it_cant_tell(client, home):
    home.setup()
    add_transactions(client, monthly(10, range(4, 10), -60, "City of Durham", "Shopping"))  # water, by its name
    synced(client, date(2026, 10, 12))  # the last sync was before it was late
    assert status(client)["late"] == []
    synced(client, TODAY)
    assert [r["name"] for r in status(client)["late"]] == ["City of Durham"]
    with db_of(client).session() as db:
        db.execute(update(Connection).values(status="stale"))
    assert status(client)["late"] == []  # a broken connection is its own "Needs you" item
    with db_of(client).session() as db:
        db.execute(update(Connection).values(status="active"))
    # Still unpaid when the next one would be due: it has likely moved or stopped, so it's dropped.
    assert status(client, date(2026, 11, 8))["late"] != []
    assert status(client, date(2026, 11, 9))["late"] == []


@needs_db
def test_two_payments_one_bill_and_renamed_biller(client, home):
    home.setup()
    rows = []
    for m in range(4, 10):  # water and trash on separate days, one bill
        rows += [(f"2026-{m:02}-08", "City of Durham Water", -55, "Utilities")]
        rows += [(f"2026-{m:02}-10", "City of Durham Water", -25, "Utilities")]
    add_transactions(client, rows)
    synced(client, TODAY)
    [late] = status(client)["late"]
    assert late["usual_cents"] == 80_00 and late["cadence"] == "monthly"
    # Paid through the bank's bill pay this time, under another name in Utilities, about the same size.
    add_transactions(client, [("2026-10-20", "BILLPAY DURHAM UTIL", -82, "Utilities")])
    assert status(client)["late"] == []


@needs_db
def test_mark_paid_from_home(client, home):
    home.setup()
    today = local_today(client)
    first = today - timedelta(days=30 * 5 + 8)
    rows = [((first + timedelta(days=30 * n)).isoformat(), "Piedmont Natural Gas", -45, "Utilities") for n in range(5)]
    add_transactions(client, rows)
    synced(client, today)
    [late] = client.get("/api/household").json()["health"]["bills"]
    assert late["name"] == "Piedmont Natural Gas"
    r = client.post("/api/bills/paid", json={"key": "nope"}, headers=csrf(client))
    assert r.status_code == 404
    r = client.post("/api/bills/paid", json={"key": late["key"]}, headers=csrf(client))
    assert r.status_code == 200, r.text
    assert client.get("/api/household").json()["health"]["bills"] == []
    with db_of(client).session() as db:
        [b], _ = bills.watch(db, today)
    # Counted as paid on its expected day, so the next one is about a month after that.
    assert b.dates[-1].isoformat() == late["expected"]
    assert 28 <= (b.expected - date.fromisoformat(late["expected"])).days <= 31


@needs_db
def test_coming_up_lists_the_next_bill(client, home):
    home.setup()
    add_transactions(client, monthly(1, range(5, 11), -120, "Spectrum", "Phone & internet"))
    synced(client, TODAY)
    with db_of(client).session() as db:
        events = upcoming.build(db, TODAY)["events"]
    [e] = [e for e in events if e["kind"] == "bill"]
    assert e["title"] == "Spectrum bill" and e["date"] == "2026-11-01" and e["cents"] == -120_00
    assert e["detail"] == "Monthly bill, usual amount"


def test_demo_bills(tmp_path):
    from financeapp import demo

    h = demo.upcoming_events(TODAY)
    assert any(e["kind"] == "bill" for e in h["events"])
    [late] = demo._bills(TODAY)["late"]
    assert late["days_late"] == 3 and late["usual_day"] is not None
