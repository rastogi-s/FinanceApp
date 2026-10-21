import io
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from defusedxml import DTDForbidden, EntitiesForbidden
from sqlalchemy import select

from financeapp import household, lookthrough, quotes, security
from financeapp.config import Settings
from financeapp.models import Account, Balance, Connection, Fund, FundConstituent, HoldingSnapshot, Quote
from tests.conftest import db_of, make_client, needs_db

TODAY = date.today()

NPORT = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo><seriesName>VANGUARD 500 INDEX FUND</seriesName><repPdDate>2026-06-30</repPdDate></genInfo>
    <invstOrSecs>
      <invstOrSec><name>Apple Inc</name><title>APPLE INC</title><cusip>037833100</cusip>
        <identifiers><isin value="US0378331005"/></identifiers><pctVal>7.0</pctVal><payoffProfile>Long</payoffProfile>
        <assetCat>EC</assetCat></invstOrSec>
      <invstOrSec><name>Alphabet Inc</name><cusip>02079K305</cusip><identifiers><ticker value="GOOGL"/></identifiers>
        <pctVal>2.0</pctVal><payoffProfile>Long</payoffProfile><assetCat>EC</assetCat></invstOrSec>
      <invstOrSec><name>Alphabet Inc</name><cusip>02079K107</cusip><identifiers><ticker value="GOOG"/></identifiers>
        <pctVal>1.5</pctVal><payoffProfile>Long</payoffProfile><assetCat>EC</assetCat></invstOrSec>
      <invstOrSec><name>Procter &amp; Gamble Co/The</name><cusip>742718109</cusip><pctVal>1.0</pctVal>
        <payoffProfile>Long</payoffProfile><assetCat>EC</assetCat></invstOrSec>
      <invstOrSec><name>Some Stock Sold Short</name><cusip>999999999</cusip><pctVal>0.5</pctVal>
        <payoffProfile>Short</payoffProfile><assetCat>EC</assetCat></invstOrSec>
      <invstOrSec><name>N/A</name><title>S&amp;P500 EMINI FUT</title><cusip>N/A</cusip>
        <identifiers><ticker value="ESU6"/></identifiers><pctVal>0.3</pctVal><assetCat>DE</assetCat></invstOrSec>
      <invstOrSec><name>US Treasury Bill</name><cusip>912797AA1</cusip><pctVal>0.2</pctVal><assetCat>DBT</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>"""


def reference() -> lookthrough.Reference:
    companies = [
        {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
        {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
        {"cik_str": 80424, "ticker": "PG", "title": "PROCTER & GAMBLE Co"},
    ]
    return lookthrough.Reference({"VOO": (36405, "S000002839")}, companies)


def test_parse_nport():
    parsed = lookthrough.parse_nport(io.BytesIO(NPORT.encode()))
    assert parsed["name"] == "VANGUARD 500 INDEX FUND" and parsed["as_of"] == date(2026, 6, 30)
    by_cusip = {h["cusip"]: h for h in parsed["holdings"]}
    assert by_cusip["037833100"]["weight"] == Decimal("0.07")
    assert by_cusip["999999999"]["weight"] == Decimal("-0.005")  # short
    assert by_cusip["02079K305"]["ticker"] == "GOOGL"
    assert by_cusip["742718109"]["name"] == "Procter & Gamble Co/The"
    assert None in by_cusip  # the future has no CUSIP


@pytest.mark.parametrize(
    "doc",
    [
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>',
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
    ],
)
def test_filings_cannot_carry_entities(doc):
    with pytest.raises((DTDForbidden, EntitiesForbidden)):
        lookthrough.parse_nport(io.BytesIO(doc.encode()))


def test_stream_caps_size():
    class Fake:
        def iter_bytes(self, n):
            yield from (b"x" * 1000 for _ in range(10))

    with pytest.raises(ValueError):
        lookthrough.Stream(Fake(), 5000).read()


def test_normalize_names():
    assert lookthrough.normalize("Procter &amp; Gamble Co/The".replace("&amp;", "&")) == "procter and gamble"
    assert lookthrough.normalize("PROCTER & GAMBLE Co") == "procter and gamble"
    assert lookthrough.normalize("Alphabet Inc - Class A") == "alphabet"


def seed(db, rows: list[tuple[str, str, int, float]], quote_rows: dict | None = None) -> Account:
    """rows: (symbol, asset class, quantity, price). One investment account, synced today."""
    c = Connection(provider="manual", institution="Fidelity", state={})
    db.add(c)
    db.flush()
    a = Account(connection_id=c.id, external_id="ira", name="IRA", institution="Fidelity", type="investment")
    db.add(a)
    db.flush()
    total = 0
    for symbol, cls, qty, price in rows:
        value = round(qty * price * 100)
        total += value
        db.add(HoldingSnapshot(date=TODAY, account_id=a.id, symbol=symbol, name=symbol, asset_class=cls,
                               quantity=Decimal(qty), price=Decimal(str(price)), value_cents=value))  # fmt: skip
    db.add(Balance(account_id=a.id, date=TODAY, current_cents=total))
    db.flush()
    return a


@needs_db
def test_look_through_math(client):
    """The plan's example: 3% in AAPL directly plus 20% in VOO, where AAPL is 7% of VOO, is 4.4%."""
    with db_of(client).session() as db:
        seed(db, [("AAPL", "Stocks", 3, 100), ("VOO", "Funds", 20, 100), ("CASH", "Cash", 77, 100)])
        fund = Fund(symbol="VOO", source="none", status="new")
        db.add(fund)
        parsed = lookthrough.parse_nport(io.BytesIO(NPORT.encode()))
        lookthrough.identify(db, None, reference(), parsed["holdings"])
        lookthrough.save_fund(db, fund, parsed, "0000036405-26-000473")
        db.flush()
        keys = {c.key: c for c in db.scalars(select(FundConstituent))}
        assert keys["cik:1652044"].weight == Decimal("0.035")  # GOOGL and GOOG are one company
        assert keys["cik:80424"].symbol == "PG"  # by name
        assert "cusip:999999999" in keys and keys["cusip:999999999"].weight < 0
        assert not any(k.startswith("ticker:ESU6") for k in keys)  # futures aren't companies
        assert fund.equity_weight == Decimal("0.1100")

        e = lookthrough.exposure(db, household.counted(db))
        aapl = next(c for c in e["companies"] if c["symbol"] == "AAPL")
        assert aapl["percent"] == pytest.approx(4.4)
        assert aapl["direct_percent"] == pytest.approx(3.0)
        assert [s["symbol"] for s in aapl["sources"]] == ["direct", "VOO"]
        assert all(c["cents"] > 0 for c in e["companies"])  # short positions don't show as holdings
        assert e["looked_through_percent"] == 20.0
        voo = e["funds"][0]
        assert voo["status"] == "ok" and voo["as_of"] == "2026-06-30" and voo["top"][0]["symbol"] == "AAPL"


@needs_db
def test_proxy_and_nested_funds(client):
    with db_of(client).session() as db:
        seed(db, [("SPY", "Funds", 10, 100), ("FFFHX", "Funds", 10, 100)])
        db.add_all([
            Fund(symbol="IVV", source="nport", status="ok", name="iShares Core S&P 500"),
            Fund(symbol="SPY", source="proxy", proxy_of="IVV", status="ok"),
            Fund(symbol="FFFHX", source="nport", status="ok"),
            Fund(symbol="FSKAX", source="nport", status="ok"),
        ])  # fmt: skip
        db.flush()
        db.add_all([
            FundConstituent(fund_symbol="IVV", key="cik:320193", symbol="AAPL", name="Apple", weight=Decimal("0.07")),
            FundConstituent(fund_symbol="FFFHX", key="ticker:FSKAX", symbol="FSKAX", name="Total Market",
                            weight=Decimal("0.5"), is_fund=True),
            FundConstituent(fund_symbol="FSKAX", key="cik:320193", symbol="AAPL", name="Apple", weight=Decimal("0.06")),
        ])  # fmt: skip
        db.flush()
        e = lookthrough.exposure(db, household.counted(db))
        aapl = e["companies"][0]
        # SPY borrows IVV's holdings (3.5% of the total); FFFHX holds half FSKAX, 6% Apple (1.5%).
        assert aapl["percent"] == pytest.approx(5.0)
        assert {s["symbol"] for s in aapl["sources"]} == {"SPY", "FFFHX"}
        spy = next(f for f in e["funds"] if f["symbol"] == "SPY")
        assert spy["proxy_of"] == "IVV" and spy["status"] == "ok"


def test_quotes_due():
    et = quotes.ET
    wed_noon = datetime(2026, 10, 21, 12, 0, tzinfo=et)
    assert quotes.due(None, wed_noon, 15)
    assert not quotes.due(wed_noon - timedelta(minutes=10), wed_noon, 15)
    assert quotes.due(wed_noon - timedelta(minutes=16), wed_noon, 15)
    evening = datetime(2026, 10, 21, 19, 0, tzinfo=et)
    assert quotes.due(datetime(2026, 10, 21, 16, 0, tzinfo=et), datetime(2026, 10, 21, 16, 10, tzinfo=et), 15)
    assert quotes.due(datetime(2026, 10, 21, 16, 10, tzinfo=et), evening, 15)  # the mutual fund prices
    assert not quotes.due(datetime(2026, 10, 21, 18, 40, tzinfo=et), evening, 15)
    saturday = datetime(2026, 10, 24, 12, 0, tzinfo=et)
    assert not quotes.due(datetime(2026, 10, 23, 18, 40, tzinfo=et), saturday, 15)


def test_quotable():
    assert quotes.quotable("VOO", "Funds") and quotes.quotable("BRK.B") and quotes.quotable("FXAIX", "Funds")
    assert not quotes.quotable("CASH", "Cash")
    assert not quotes.quotable("FID 500 INDEX", "Funds")
    assert not quotes.quotable("AAPL261218C00300000", "Options")
    assert not quotes.quotable("../etc", "Stocks")


def mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_finnhub_then_yahoo():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "finnhub.io":
            assert request.headers["X-Finnhub-Token"] == "k"
            s = request.url.params["symbol"]
            if s == "FXAIX":  # Finnhub has no mutual funds: it answers zeros
                return httpx.Response(200, json={"c": 0, "pc": 0, "t": 0})
            return httpx.Response(200, json={"c": 601.5, "pc": 598.0, "t": 1792540800})
        assert request.url.path == "/v8/finance/chart/FXAIX"
        meta = {"regularMarketPrice": 268.06, "chartPreviousClose": 267.3, "regularMarketTime": 1792541318,
                "currency": "USD"}  # fmt: skip
        return httpx.Response(200, json={"chart": {"result": [{"meta": meta}]}})

    settings = Settings(finnhub_api_key="k")
    got = quotes.fetch(settings, ["VOO", "FXAIX"], client=mock(handler))
    assert got["VOO"].source == "finnhub" and got["VOO"].price == Decimal("601.5")
    assert got["FXAIX"].source == "yahoo" and got["FXAIX"].previous_close == Decimal("267.3")
    assert quotes.fetch(Settings(quotes_provider="off"), ["VOO"], client=mock(handler)) == {}


@needs_db
def test_live_prices_move_holdings_and_net_worth(client):
    with db_of(client).session() as db:
        seed(db, [("VOO", "Funds", 10, 600), ("CASH", "Cash", 1, 500)])
        before = household.build(db)
        assert before["net_worth"]["cents"] == 650_000
        db.add(Quote(symbol="VOO", price=Decimal("610"), previous_close=Decimal("600"), source="yahoo",
                     as_of=datetime.now(UTC)))  # fmt: skip
        db.flush()
        after = household.build(db)
        voo = next(h for h in after["holdings"] if h["symbol"] == "VOO")
        assert voo["value_cents"] == 610_000 and voo["live"] and voo["change_today_percent"] == pytest.approx(1.67)
        assert after["net_worth"]["cents"] == 660_000
        assert after["accounts"]["investments"][0]["cents"] == 660_000
        assert after["prices_as_of"]
        # A quote older than the holdings never overrides them.
        q = db.get(Quote, "VOO")
        q.as_of = datetime.now(UTC) - timedelta(days=3)
        db.flush()
        assert next(h for h in household.build(db)["holdings"] if h["symbol"] == "VOO")["value_cents"] == 600_000


@needs_db
def test_security_detail(client, home, monkeypatch):
    home.setup()
    with db_of(client).session() as db:
        seed(db, [("AAPL", "Stocks", 10, 300)])
        db.get_one(HoldingSnapshot, (TODAY, db.scalar(select(Account.id)), "AAPL")).cost_basis_cents = 200_000

    def chart(symbol, range_):
        assert symbol == "AAPL"
        return {"name": "Apple Inc.", "type": "EQUITY", "price": 330.0, "previous_close": 320.0, "points": [[1, 320.0],
                [2, 330.0]], "dividends": [], "exchange": "NasdaqGS", "currency": "USD", "price_at": 2,
                "day_low": 318.0, "day_high": 331.0, "year_low": 240.0, "year_high": 348.0, "volume": 1e6}  # fmt: skip

    monkeypatch.setattr(security, "chart", chart)
    r = client.get("/api/securities/aapl?range=1y")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symbol"] == "AAPL" and d["change_percent"] == pytest.approx(3.12)
    assert d["position"]["value_cents"] == 300_000 and d["position"]["gain_cents"] == 100_000
    assert d["position"]["percent_of_portfolio"] == 100.0
    assert client.get("/api/securities/..%2Fetc").status_code == 404
    assert client.get("/api/securities/AAPL?range=10y").status_code == 422

    def down(symbol, range_):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(security, "chart", down)
    d = client.get("/api/securities/AAPL").json()  # still shows the position from the last sync
    assert d["market_data"] is False and d["price"] == 300.0 and d["points"] == []
    assert client.get("/api/securities/ZZZZ").status_code == 404


def test_chart_parsing(monkeypatch):
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"regularMarketPrice": 100.0, "chartPreviousClose": 98.0, "longName": "Thing",
                             "regularMarketTime": 5, "currency": "USD"},
                    "timestamp": [1, 2, 3],
                    "indicators": {"quote": [{"close": [97.0, None, 100.0]}]},
                    "events": {"dividends": {"1": {"date": int(datetime.now(UTC).timestamp()) - 100, "amount": 1.5}}},
                }
            ]
        }
    }  # fmt: skip
    monkeypatch.setattr(security, "_client", lambda: mock(lambda r: httpx.Response(200, json=payload)))
    security._cache.clear()
    c = security.chart("TEST", "1y")
    assert c["points"] == [[1, 97.0], [3, 100.0]]  # gaps dropped
    assert security.dividend_yield("TEST", 100.0) == 1.5


def test_demo_portfolio_and_detail(tmp_path):
    with make_client(tmp_path, "", demo_mode=True) as c:
        p = c.get("/api/portfolio").json()
        assert p["positions"][0]["symbol"] == "VTI"
        assert p["exposure"]["companies"][0]["symbol"]
        for symbol in ("VTI", "AAPL", "BND"):
            for r in ("1d", "5y"):
                d = c.get(f"/api/securities/{symbol}?range={r}").json()
                assert len(d["points"]) > 10, (symbol, r)
        assert json.dumps(p)  # serializable as it is
