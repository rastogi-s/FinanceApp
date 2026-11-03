"""Holdings that stop moving at Plaid (a buy from days ago never shows up): the sync notices from the brokerage's own
prices and Plaid's pull times, asks Plaid for a fresh pull, and the app says so when they stay behind."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from financeapp import sync
from financeapp.connectors.base import last_market_day
from financeapp.connectors.plaid import Plaid
from financeapp.models import Account, Connection
from tests.conftest import db_of, needs_db

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)  # a Tuesday
TODAY = NOW.date()


class Brokerage:
    """A fake Plaid with one brokerage account. Its holdings are stuck on `as_of` until a refresh brings `fresh`."""

    def __init__(self, as_of: date, refresh_error: str | None = None, fresh: date | None = None) -> None:
        self.as_of = as_of
        self.refresh_error = refresh_error
        self.fresh = fresh or last_market_day(TODAY)
        self.pulled = datetime.combine(as_of, datetime.min.time(), UTC) + timedelta(hours=22)
        self.failed: datetime | None = None
        self.calls: list[str] = []
        self.shares = 10

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        json.loads(request.content)
        self.calls.append(path)
        if path == "/accounts/get":
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {"account_id": "brk", "name": "Brokerage", "type": "investment", "mask": "2600"},
                    ]
                },
            )
        if path == "/item/get":
            return httpx.Response(
                200,
                json={
                    "item": {"products": ["investments"]},
                    "status": {
                        "investments": {
                            "last_successful_update": self.pulled.isoformat().replace("+00:00", "Z"),
                            "last_failed_update": self.failed.isoformat() if self.failed else None,
                        }
                    },
                },
            )
        if path == "/investments/refresh":
            if self.refresh_error:
                return httpx.Response(400, json={"error_code": self.refresh_error, "error_message": "no"})
            self.as_of, self.pulled, self.failed, self.shares = self.fresh, NOW, None, 15
            return httpx.Response(200, json={"request_id": "r"})
        if path == "/investments/holdings/get":
            return httpx.Response(
                200,
                json={
                    "holdings": [
                        {
                            "account_id": "brk",
                            "security_id": "s1",
                            "quantity": self.shares,
                            "institution_price": 100,
                            "institution_price_as_of": self.as_of.isoformat(),
                            "institution_value": self.shares * 100,
                        },
                        {  # cash is priced every day and says nothing about whether the rest moved
                            "account_id": "brk",
                            "security_id": "cash",
                            "quantity": 5,
                            "institution_price": 1,
                            "institution_price_as_of": TODAY.isoformat(),
                            "institution_value": 5,
                        },
                    ],
                    "securities": [
                        {"security_id": "s1", "ticker_symbol": "VTI", "name": "Vanguard Total", "type": "etf"},
                        {"security_id": "cash", "name": "Cash", "is_cash_equivalent": True, "type": "cash"},
                    ],
                },
            )
        if path == "/investments/transactions/get":
            return httpx.Response(200, json={"investment_transactions": [], "total_investment_transactions": 0})
        return httpx.Response(404, json={"error_code": "NOT_FOUND"})


def plaid_for(fake: Brokerage) -> Plaid:
    return Plaid("cid", "sec", "sandbox", httpx.Client(transport=httpx.MockTransport(fake.handler)))


def fetch(fake: Brokerage, state: dict | None = None, at: datetime = NOW):
    plaid = plaid_for(fake)
    original = plaid._refresh_holdings
    plaid._refresh_holdings = lambda token, st: original(token, st, at)  # type: ignore[method-assign]
    return plaid.fetch("access", "item", state or {})


def test_fresh_holdings_are_left_alone():
    fake = Brokerage(as_of=last_market_day(TODAY))
    result = fetch(fake)
    assert "/investments/refresh" not in fake.calls
    assert result.state["holdings_as_of"] == last_market_day(TODAY).isoformat()
    assert fake.calls.count("/item/get") == 1


def test_stuck_holdings_get_a_fresh_pull_and_the_buy_shows_up():
    fake = Brokerage(as_of=TODAY - timedelta(days=8))
    result = fetch(fake)
    assert fake.calls.count("/investments/refresh") == 1
    assert fake.calls.count("/investments/holdings/get") == 2
    vti = next(h for h in result.holdings if h.symbol == "VTI")
    assert vti.quantity == 15  # the buy is in
    assert result.state["holdings_as_of"] == last_market_day(TODAY).isoformat()
    assert result.state["refresh"]["result"] == "ok"
    assert result.state["plaid_pulled_at"].startswith("2026-11-03T15:00")


def test_a_fresh_pull_is_asked_for_at_most_every_12_hours():
    fake = Brokerage(as_of=TODAY - timedelta(days=8), fresh=TODAY - timedelta(days=8))  # the pull brings nothing new
    state = fetch(fake).state
    assert fake.calls.count("/investments/refresh") == 1
    fetch(fake, state, NOW + timedelta(hours=6))
    assert fake.calls.count("/investments/refresh") == 1
    fetch(fake, state, NOW + timedelta(hours=13))
    assert fake.calls.count("/investments/refresh") == 2


def test_a_brokerage_without_refresh_is_asked_again_a_week_later_and_the_sync_goes_on():
    fake = Brokerage(as_of=TODAY - timedelta(days=8), refresh_error="PRODUCTS_NOT_SUPPORTED")
    result = fetch(fake)
    assert result.holdings and result.state["refresh"] == {
        "at": NOW.isoformat(),
        "result": "unavailable",
        "code": "PRODUCTS_NOT_SUPPORTED",
    }
    fetch(fake, result.state, NOW + timedelta(days=3))
    assert fake.calls.count("/investments/refresh") == 1
    fetch(fake, result.state, NOW + timedelta(days=8))
    assert fake.calls.count("/investments/refresh") == 2


def test_other_refresh_errors_never_fail_the_sync():
    fake = Brokerage(as_of=TODAY - timedelta(days=8), refresh_error="INTERNAL_SERVER_ERROR")
    result = fetch(fake)
    assert result.holdings and result.state["refresh"]["result"] == "failed"


def test_plaid_failing_to_reach_the_brokerage_asks_for_a_pull():
    fake = Brokerage(as_of=last_market_day(TODAY))
    fake.failed = NOW - timedelta(hours=2)
    fake.pulled = NOW - timedelta(days=1)
    fetch(fake)
    assert fake.calls.count("/investments/refresh") == 1


def test_a_weekend_is_not_behind():
    friday = date(2026, 10, 30)
    monday = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    fake = Brokerage(as_of=friday)
    fake.pulled = monday - timedelta(hours=3)
    fetch(fake, at=monday)
    assert "/investments/refresh" not in fake.calls
    assert last_market_day(date(2026, 11, 2)) == friday


@needs_db
def test_the_app_says_when_holdings_are_behind(client, home):
    home.setup()
    today = sync.today()
    with db_of(client).session() as db:
        c = Connection(
            provider="plaid",
            institution="E*TRADE",
            token_enc=None,
            state={
                "holdings_as_of": (today - timedelta(days=8)).isoformat(),
                "refresh": {"at": "2026-11-03T15:00:00+00:00", "result": "unavailable"},
            },
        )
        fresh = Connection(
            provider="plaid",
            institution="Fidelity",
            token_enc=None,
            state={"holdings_as_of": last_market_day(today).isoformat()},
        )
        db.add_all([c, fresh])
        db.flush()
        for conn, mask in ((c, "2600"), (fresh, "1111")):
            db.add(
                Account(
                    connection_id=conn.id,
                    external_id=mask,
                    name="Brokerage",
                    institution="x",
                    type="investment",
                    mask=mask,
                )
            )
        db.flush()
        sync.save_balance(db, db.query(Account).filter_by(mask="2600").one().id, today, 100_000)
        sync.save_balance(db, db.query(Account).filter_by(mask="1111").one().id, today, 100_000)

    rows = {x["institution"]: x for x in client.get("/api/connections").json()["connections"]}
    assert rows["E*TRADE"]["holdings"]["behind"] is True
    assert rows["E*TRADE"]["holdings"]["refresh_result"] == "unavailable"
    assert rows["Fidelity"]["holdings"]["behind"] is False

    health = client.get("/api/household").json()["health"]
    assert [x["institution"] for x in health["holdings_behind"]] == ["E*TRADE"]
