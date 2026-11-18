from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from financeapp import lots, security
from financeapp.models import Account, Connection, HoldingSnapshot, InvestmentTransaction
from financeapp.portfolio import Position
from tests.conftest import csrf, db_of, needs_db

TODAY = date.today()


def txn(days_ago: int, kind: str, quantity: float, amount: float, subtype: str = "", price: float | None = None):
    return InvestmentTransaction(
        id=None, date=TODAY - timedelta(days=days_ago), type=kind, subtype=subtype or kind, symbol="AAPL", name="",
        quantity=Decimal(str(quantity)), price=Decimal(str(price)) if price is not None else None,
        amount_cents=round(amount * 100), source="plaid",
    )  # fmt: skip


def position(quantity: float, basis: int | None) -> Position:
    return Position("AAPL", "Apple", "Stocks", 1, Decimal(str(quantity)), Decimal("300"), 0, 0, 0.0, basis, TODAY, None)


def test_long_term_starts_the_day_after_the_anniversary():
    assert lots.long_term_on(date(2025, 3, 3)) == date(2026, 3, 4)
    assert lots.long_term_on(date(2024, 2, 29)) == date(2025, 3, 1)


def test_sales_close_the_oldest_lots_first():
    activity = [
        txn(500, "buy", 10, -1_000),  # Plaid: cash out is negative here
        txn(300, "buy", 5, -1_000),
        txn(100, "sell", -12, 2_400),  # all of the first lot and 2 of the second
        txn(50, "buy", 1, 0, subtype="dividend reinvestment", price=250),
    ]
    open_lots, older = lots.replay(activity)
    assert not older
    assert [(lot.quantity, lot.cost_cents) for lot in open_lots] == [(Decimal(3), 60_000), (Decimal(1), 25_000)]


def test_a_split_scales_the_lots_and_keeps_their_cost():
    open_lots, _ = lots.replay([txn(500, "buy", 10, -1_000), txn(200, "transfer", 30, 0, subtype="split")])
    assert open_lots[0].quantity == Decimal(40) and open_lots[0].cost_cents == 100_000


def test_shares_from_before_the_history_get_the_rest_of_the_cost():
    open_lots, older = lots.replay([txn(100, "buy", 2, -600), txn(50, "sell", -5, 1_500)])
    assert older and open_lots == []  # sold 5 with only 2 bought: the rest came from before
    reconciled, trimmed = lots.reconcile(lots.replay([txn(100, "buy", 2, -600)])[0], position(10, 200_000))
    assert not trimmed
    assert [(lot.acquired, lot.quantity, lot.cost_cents) for lot in reconciled] == [
        (None, Decimal(8), 140_000),
        (TODAY - timedelta(days=100), Decimal(2), 60_000),
    ]
    # More shares in the lots than held (a sale the activity missed): the oldest go first.
    reconciled, trimmed = lots.reconcile(lots.replay([txn(400, "buy", 5, -500), txn(20, "buy", 5, -1_500)])[0],
                                         position(6, None))  # fmt: skip
    assert trimmed and [lot.quantity for lot in reconciled] == [Decimal(1), Decimal(5)]


def test_summary_splits_taxable_gains_by_term():
    rows = [
        {"taxable": True, "term": "long", "gain_cents": 500, "long_term_on": None, "quantity": 1.0},
        {"taxable": True, "term": "short", "gain_cents": -200, "long_term_on": "2026-12-01", "quantity": 2.0},
        {"taxable": True, "term": "short", "gain_cents": 30, "long_term_on": "2026-12-20", "quantity": 0.01},
        {"taxable": True, "term": "short", "gain_cents": 6_000, "long_term_on": "2027-01-10", "quantity": 3.0},
        {"taxable": False, "term": "short", "gain_cents": 9_000, "long_term_on": "2026-11-01", "quantity": 4.0},
    ]
    s = lots.summary(rows, date(2026, 11, 1))
    assert s["long_gain_cents"] == 500 and s["short_gain_cents"] == 5_830
    # The next one worth waiting for: in a taxable account and with a gain of $50 or more.
    assert s["next_long_term"] == {"on": "2027-01-10", "days": 70, "quantity": 3.0, "gain_cents": 6_000}


@needs_db
def seed_lots(client, activity: list[tuple[int, int, int]]) -> None:
    """One E*TRADE brokerage holding 10 AAPL at $300 ($2,000 cost basis), and its buys: (days ago, shares, dollars)."""
    with db_of(client).session() as db:
        c = Connection(provider="plaid", institution="E*TRADE", state={}, created_at=datetime.now(UTC))
        db.add(c)
        db.flush()
        a = Account(connection_id=c.id, external_id="brokerage", name="Brokerage", institution="E*TRADE",
                    type="investment", subtype="brokerage")  # fmt: skip
        db.add(a)
        db.flush()
        db.add(HoldingSnapshot(date=TODAY, account_id=a.id, symbol="AAPL", name="Apple", asset_class="Stocks",
                               quantity=Decimal(10), price=Decimal(300), value_cents=300_000,
                               cost_basis_cents=200_000))  # fmt: skip
        for i, (days, q, amount) in enumerate(activity):
            db.add(InvestmentTransaction(account_id=a.id, external_id=f"t{i}", date=TODAY - timedelta(days=days),
                                         type="buy", subtype="buy", symbol="AAPL", name="Apple",
                                         quantity=Decimal(q), price=None, amount_cents=amount * 100,
                                         source="plaid"))  # fmt: skip
        db.flush()


@needs_db
def test_holding_detail_shows_lots(client, home, monkeypatch):
    home.setup()
    seed_lots(client, [(400, 4, -800), (30, 2, -460)])
    monkeypatch.setattr(security, "chart", lambda symbol, range_: None)
    d = client.get("/api/securities/AAPL").json()
    got = d["position"]["lots"]
    rows = got["rows"]
    assert [(r["acquired"] is None, r["quantity"], r["cost_cents"], r["term"]) for r in rows] == [
        (True, 4.0, 74_000, "long"),  # held before the first activity, over a year ago
        (False, 4.0, 80_000, "long"),
        (False, 2.0, 46_000, "short"),
    ]
    assert rows[0]["estimated"] and rows[0]["acquired_before"] == (TODAY - timedelta(days=400)).isoformat()
    assert rows[2]["long_term_on"] == lots.long_term_on(TODAY - timedelta(days=30)).isoformat()
    assert got["short_gain_cents"] == 60_000 - 46_000
    assert got["long_gain_cents"] == (120_000 - 74_000) + (120_000 - 80_000)
    assert got["next_long_term"]["days"] == (lots.long_term_on(TODAY - timedelta(days=30)) - TODAY).days


@needs_db
def test_a_short_history_leaves_earlier_shares_term_unknown(client, home, monkeypatch):
    """Fidelity sent a month of activity: shares older than that may be only weeks older, so not surely long-term."""
    home.setup()
    seed_lots(client, [(30, 2, -560)])
    monkeypatch.setattr(security, "chart", lambda symbol, range_: None)
    earlier = client.get("/api/securities/AAPL").json()["position"]["lots"]["rows"][0]
    assert earlier["acquired"] is None and earlier["acquired_before"] == (TODAY - timedelta(days=30)).isoformat()
    assert earlier["term"] is None
    assert earlier["long_term_on"] == lots.long_term_on(TODAY - timedelta(days=31)).isoformat()


def test_a_sale_is_matched_to_the_lots_it_closed():
    sales: list[lots.Sale] = []
    lots.replay(
        [
            txn(600, "buy", 10, -1_000),  # $100 a share, long-term by the sale
            txn(200, "buy", 10, -2_000),  # $200 a share, short-term at the sale
            txn(100, "sell", -15, 3_750),  # $250 a share: all of the first lot, 5 of the second
            txn(90, "transfer", -1, 0),  # shares moved out aren't a sale
        ],
        sales,
    )
    assert len(sales) == 1
    from financeapp import profit
    from financeapp.models import Account

    row = profit.sale_row(sales[0], "AAPL", "Apple", Account(id=1, name="Brokerage", subtype="brokerage"))
    assert row["proceeds_cents"] == 375_000 and row["cost_cents"] == 200_000
    assert row["long_gain_cents"] == 250_000 - 100_000 and row["short_gain_cents"] == 125_000 - 100_000
    assert row["uncosted_proceeds_cents"] == 0 and row["taxable"]


def test_shares_sold_from_before_the_history_have_no_cost():
    sales: list[lots.Sale] = []
    lots.replay([txn(100, "buy", 2, -400), txn(50, "sell", -4, 1_000)], sales)
    from financeapp import profit
    from financeapp.models import Account

    row = profit.sale_row(sales[0], "AAPL", "Apple", Account(id=1, name="Roth IRA", subtype="roth"))
    assert row["cost_cents"] == 40_000 and row["short_gain_cents"] == 50_000 - 40_000
    assert row["uncosted_proceeds_cents"] == 50_000 and not row["taxable"]


@needs_db
def test_profit_adds_up_the_lots_and_the_sales(client, home):
    home.setup()
    seed_lots(client, [(400, 4, -800), (30, 2, -460)])
    with db_of(client).session() as db:
        a = db.query(Account).filter_by(external_id="brokerage").one()
        db.add(InvestmentTransaction(account_id=a.id, external_id="s1", date=TODAY - timedelta(days=10), type="sell",
                                     subtype="sell", symbol="AAPL", name="Apple", quantity=Decimal(-1),
                                     price=Decimal(300), amount_cents=30_000, source="plaid"))  # fmt: skip
        db.flush()
    p = client.get("/api/portfolio/profit").json()
    assert [x["name"] for x in p["accounts"]] == ["E*TRADE Brokerage"]
    h = next(x for x in p["holdings"] if x["symbol"] == "AAPL")
    assert h["value_cents"] == 300_000 and h["cost_cents"] == 200_000 and h["uncosted_value_cents"] == 0
    # The sale took the first of the four shares bought 400 days ago at $200 (+$100, long-term).
    assert p["sales"][0]["long_gain_cents"] == 10_000 and p["sales"][0]["short_gain_cents"] == 0
    sold_in = next(y for y in p["sales_by_year"] if y["year"] == (TODAY - timedelta(days=10)).year)
    assert sold_in["by_account"][0]["count"] == 1 and sold_in["by_account"][0]["long_gain_cents"] == 10_000


def test_demo_profit_has_every_account_with_lots():
    from financeapp import demo

    p = demo.profit(date(2026, 11, 17))
    assert {a["name"] for a in p["accounts"]} == {"Fidelity 401(k)", "E*TRADE", "Fidelity IRA"}
    assert [h["symbol"] for h in p["holdings"] if h["uncosted_value_cents"]] == ["VXUS"]  # the plan fund with no cost
    assert p["sales_by_year"][0]["year"] == 2026


def test_money_market_sales_are_not_sales():
    from financeapp import profit

    mmf = [txn(30, "buy", 500, -500, price=1), txn(10, "sell", -120, 120, price=1)]
    for t in mmf:
        t.symbol = "SPAXX"
    assert profit.cash_like("SPAXX", mmf, set())
    assert profit.cash_like("VMFXX", [], {"VMFXX"})
    assert not profit.cash_like("AAPL", [txn(30, "buy", 2, -600, price=300)], set())


def test_a_fund_without_a_ticker_keeps_its_name_in_the_activity():
    from financeapp.connectors.plaid import activity_symbol

    assert activity_symbol({"ticker_symbol": "VTI", "name": "Vanguard"}) == "VTI"
    assert activity_symbol({"ticker_symbol": None, "name": "NETAPP TARGET 2050"}) == "NETAPP TARGET 2050"
    assert activity_symbol({"ticker_symbol": None, "name": "Cash", "is_cash_equivalent": True}) is None
    assert activity_symbol({}) is None


@needs_db
def test_what_you_paid_fills_a_holding_with_no_cost(client, home):
    home.setup()
    with db_of(client).session() as db:
        c = Connection(provider="plaid", institution="Fidelity", state={}, created_at=datetime.now(UTC))
        db.add(c)
        db.flush()
        a = Account(connection_id=c.id, external_id="k", name="401(K) Savings", institution="Fidelity",
                    type="investment", subtype="401k")  # fmt: skip
        db.add(a)
        db.flush()
        db.add(HoldingSnapshot(date=TODAY, account_id=a.id, symbol="NETAPP TARGET 2050", name="NETAPP TARGET 2050",
                               asset_class="Funds", quantity=Decimal(100), price=Decimal(50), value_cents=500_000,
                               cost_basis_cents=None))  # fmt: skip
        db.add(HoldingSnapshot(date=TODAY, account_id=a.id, symbol="SPAXX", name="Fidelity Government Money Market",
                               asset_class="Cash", quantity=Decimal(10), price=Decimal(1), value_cents=1_000,
                               cost_basis_cents=None))  # fmt: skip
        db.add(InvestmentTransaction(account_id=a.id, external_id="m", date=TODAY - timedelta(days=5), type="sell",
                                     subtype="sell", symbol="SPAXX", name="Fidelity Government Money Market",
                                     quantity=Decimal(-50), price=Decimal(1), amount_cents=5_000,
                                     source="plaid"))  # fmt: skip
        db.flush()
        aid = a.id
    p = client.get("/api/portfolio/profit").json()
    h = next(x for x in p["holdings"] if x["account_id"] == aid)
    assert h["uncosted_value_cents"] == 500_000 and h["cost_cents"] == 0 and not h["typed"]
    assert not [s for s in p["sales"] if s["symbol"] == "SPAXX"]  # the sweep isn't a sale
    h = csrf(client)
    r = client.put("/api/portfolio/paid", json={"account_id": aid, "symbol": "NETAPP TARGET 2050", "cents": 400_000},
                   headers=h)  # fmt: skip
    assert r.status_code == 200, r.text
    got = next(x for x in r.json()["holdings"] if x["account_id"] == aid)
    assert got["cost_cents"] == 400_000 and got["uncosted_value_cents"] == 0 and got["typed"]
    assert got["short_gain_cents"] + got["long_gain_cents"] + got["unknown_gain_cents"] == 100_000
    nope = {"account_id": aid, "symbol": "NOPE", "cents": 1}
    assert client.put("/api/portfolio/paid", json=nope, headers=h).status_code == 404
    forget = {"account_id": aid, "symbol": "NETAPP TARGET 2050", "cents": None}
    client.put("/api/portfolio/paid", json=forget, headers=h)
    got = next(x for x in client.get("/api/portfolio/profit").json()["holdings"] if x["account_id"] == aid)
    assert got["uncosted_value_cents"] == 500_000
