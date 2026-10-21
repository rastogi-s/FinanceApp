from datetime import date, timedelta
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from financeapp import income
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import Account, Connection, DividendHistory, Transaction
from tests.conftest import csrf, db_of, needs_db
from tests.test_money import plaid  # noqa: F401  (the fixture)


def link(client) -> None:
    r = client.post("/api/connections/plaid/start", json={"kind": "investments"}, headers=csrf(client))
    r = client.post("/api/connections/finish", json={"token": r.json()["token"]}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["synced"] is True, r.text


@needs_db
def test_brokerage_dividends_and_bank_interest(client, home, plaid):  # noqa: F811
    home.setup()
    link(client)
    # Two years back the first time, then from a month before the last sync.
    assert plaid.investment_starts == [(date.today() - timedelta(days=730)).isoformat()]
    with db_of(client).session() as db:
        checking = db.scalar(select(Account).where(Account.name == "Checking"))
        db.add(
            Transaction(
                account_id=checking.id, external_id="int1", date=date.today() - timedelta(days=3), amount_cents=1_234,
                merchant="Interest paid", description="INTEREST PAYMENT", provider_category="INCOME_INTEREST_EARNED",
                source="plaid",
            )
        )  # fmt: skip
        checking_id = checking.id
        connection_id = db.scalar(select(Connection.id))
    assert client.post("/api/sync", headers=csrf(client)).json()["summary"]["connections"][str(connection_id)]["ok"]
    assert len(plaid.investment_starts) == 2
    assert plaid.investment_starts[1] == (date.today() - timedelta(days=31)).isoformat()

    page = client.get("/api/income").json()
    d = page["dividends"]
    # The reinvested AAPL dividend counts once; NetApp's reinvestment-only dividend counts as its dividend.
    assert d["received_12m_cents"] == 2_450 + 600
    assert {r["symbol"] for r in d["holdings"]} == {"AAPL", "NTAP"}
    assert d["reinvesting"] == ["AAPL", "NTAP"]
    aapl = next(r for r in d["holdings"] if r["symbol"] == "AAPL")
    assert aapl["from"] == "payments" and aapl["annual_cents"] == 2_450 and aapl["frequency"] == 1

    i = page["interest"]
    assert i["received_12m_cents"] == 125 + 1_234 and i["brokerage_12m_cents"] == 125
    row = next(a for a in i["accounts"] if a["id"] == checking_id)
    assert row["interest_12m_cents"] == 1_234 and row["apy"] is None and row["implied_apy"] is not None

    # The APY and a maturity date typed in on the page.
    body = {"apy": 4.25, "maturity": "2027-03-01", "term_months": 12}
    r = client.put(f"/api/income/accounts/{checking_id}", json=body, headers=csrf(client))
    assert r.status_code == 200, r.text
    row = next(a for a in r.json()["interest"]["accounts"] if a["id"] == checking_id)
    assert (row["apy"], row["maturity"], row["term_months"]) == (4.25, "2027-03-01", 12)
    assert client.put("/api/income/accounts/99999", json=body, headers=csrf(client)).status_code == 404
    assert client.put(f"/api/income/accounts/{checking_id}", json={"apy": 60}, headers=csrf(client)).status_code == 422

    # The tax page's numbers: the brokerage is a taxable account; the unlabeled dividend is estimated qualified.
    tax = client.get("/api/income/tax", params={"year": date.today().year}).json()
    if all((date.today() - timedelta(days=n)).year == date.today().year for n in (3, 10, 40, 100)):
        assert tax["dividends"]["qualified_cents"] == 2_450
        assert tax["dividends"]["unlabeled_cents"] == 600
        assert tax["dividends"]["estimated_qualified_cents"] == 3_050
        assert tax["interest"] == {"total_cents": 1_359, "bank_cents": 1_234, "brokerage_cents": 125}
        assert tax["estimated"] is True and tax["tax_advantaged_cents"] == 0
    assert set(tax) >= {"year", "through", "complete", "covers_whole_year", "dividends", "interest"}


def test_retirement_accounts_are_not_taxable():
    def a(subtype, name="Account"):
        return Account(subtype=subtype, name=name)

    assert not income.taxable(a("401k"))
    assert not income.taxable(a("roth"))
    assert not income.taxable(a(None, "Fidelity Rollover IRA"))
    assert not income.taxable(a("hsa"))
    assert income.taxable(a("brokerage"))
    assert income.taxable(a("savings"))


def test_frequency_growth_and_next_payment():
    today = date(2026, 10, 21)
    quarterly = [
        (date(2016 + y, m, 10), Decimal("0.50") * Decimal("1.05") ** y) for y in range(11) for m in (2, 5, 8, 11)
    ]
    quarterly = [r for r in quarterly if r[0] <= today]
    assert income.frequency([d for d, _ in quarterly], today) == 4
    assert income.frequency([date(2026, m, 1) for m in range(1, 11)], today) == 12
    assert income.frequency([date(2024, 5, 1)], today) == 0
    assert abs(income.growth_rate(quarterly, today) - 0.05) < 0.001
    assert income.growth_rate(quarterly[-6:], today) is None  # not enough full years

    h = income.Holding("ABC", "ABC Corp", "Stocks", Decimal(100), Decimal(50), 500_000, 400_000, ["Brokerage"])
    paid = [income.Payment(date(2026, 8, 24), 1, "Brokerage", "ABC", "Dividend", 5_000, "dividend", "brokerage",
                           "qualified", True)]  # fmt: skip
    rows = income.dividend_holdings([h], {"ABC": quarterly}, paid, set(), today)
    r = rows[0]
    assert r["frequency"] == 4 and r["next_ex_date"] > today.isoformat()
    assert r["next_pay_date"] == (date.fromisoformat(r["next_ex_date"]) + timedelta(days=14)).isoformat()
    last_year = sum(a for d, a in quarterly if d > today - timedelta(days=365))
    assert r["annual_cents"] == int(last_year * 100 * 100)
    assert r["yield_on_cost_percent"] == round(r["annual_cents"] / 400_000 * 100, 2)
    months, upcoming = income.expected_months(rows, today)
    assert len(months) == 12 and sum(m["cents"] for m in months) == 4 * r["next_cents"]
    assert upcoming[0]["symbol"] == "ABC"


@needs_db
def test_dividend_history_from_yahoo(client, home, plaid):  # noqa: F811
    home.setup()
    link(client)
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        asked.append(symbol)
        assert request.url.params["events"] == "div"
        if symbol == "NTAP":
            return httpx.Response(404)
        ts = int(date(2026, 8, 11).strftime("%s"))
        return httpx.Response(
            200,
            json={"chart": {"result": [{"meta": {"currency": "USD"},
                                        "events": {"dividends": {str(ts): {"date": ts, "amount": 0.26}}}}]}},
        )  # fmt: skip

    settings = Settings(quotes_provider="yahoo", database_url="x")
    found = income.refresh_history(db_of(client), settings, httpx.Client(transport=httpx.MockTransport(handler)))
    assert found == 2 and sorted(asked) == ["AAPL", "NTAP"]
    with db_of(client).session() as db:
        rows = list(db.scalars(select(DividendHistory)))
        assert [(r.symbol, float(r.amount)) for r in rows] == [("AAPL", 0.26)]
    # Checked today: not asked again until tomorrow.
    assert income.refresh_history(db_of(client), settings, httpx.Client(transport=httpx.MockTransport(handler))) is None


def test_demo_income(tmp_path):
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    page = c.get("/api/income").json()
    assert page["demo"] is True and page["dividends"]["received_12m_cents"] > 0
    assert any(a["kind"] == "cd" and a["maturity"] for a in page["interest"]["accounts"])
    assert c.get("/api/income/tax", params={"year": 2026}).json()["year"] == 2026
    assert c.put("/api/income/accounts/2", json={"apy": 4}).status_code == 403
