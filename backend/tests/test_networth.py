from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from financeapp import demo, household, networth
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import Account, Balance, Connection, HoldingSnapshot, InvestmentTransaction
from financeapp.networth import AccountIn
from financeapp.sync import today
from tests.conftest import db_of, needs_db

END = date(2026, 10, 27)
WEEK = END - timedelta(days=7)


def sample() -> list[AccountIn]:
    old = END - timedelta(days=60)
    vti = lambda shares, price: {"VTI": (Decimal(shares), Decimal(price), "Total Stock Market")}  # noqa: E731
    return [
        AccountIn(1, "Checking", "depository", "checking", 5_000_00, [(old, 3_000_00), (WEEK, 4_000_00)]),
        # 10 shares a week ago at $100, 12 now at $110: $100 from prices, $320 from the two new shares.
        AccountIn(
            2,
            "401(k)",
            "investment",
            "401k",
            1_320_00,
            [(old, 900_00), (WEEK, 1_000_00), (END, 1_320_00)],
            vti(12, 110),
            [(old, vti(9, 100)), (WEEK, vti(10, 100))],
        ),
        # No holdings detail: split with the brokerage's own money in.
        AccountIn(
            3,
            "Brokerage",
            "investment",
            None,
            2_600_00,
            [(old, 2_000_00), (WEEK, 2_000_00)],
            moves=[(END - timedelta(days=2), 500_00)],
            has_activity=True,
            unvested=[{"symbol": "NTAP", "name": "NetApp", "shares": 10.0, "value_cents": 1_100_00}],
        ),
        AccountIn(4, "Card", "credit", None, 700_00, [(old, 100_00), (WEEK, 200_00)]),
        # Linked three days ago: counted from its first balance, not as a $50,000 jump.
        AccountIn(5, "Old 401(k)", "investment", "ira", 50_000_00, [(END - timedelta(days=3), 50_000_00)]),
    ]


def test_groups_add_up_to_net_worth():
    b = networth.breakdown(sample(), END)
    by = {g["key"]: g for g in b["groups"]}
    assert set(by) == {"cash", "investments", "retirement", "cards"}
    assert by["retirement"]["cents"] == 1_320_00 + 50_000_00
    assert by["cards"]["cents"] == -700_00 and by["cards"]["owed"]
    assert b["cents"] == sum(g["cents"] for g in b["groups"]) == 5_000_00 + 1_320_00 + 2_600_00 - 700_00 + 50_000_00
    assert b["assets_cents"] - b["owed_cents"] == b["cents"]
    assert b["within_reach_cents"] == 5_000_00 + 2_600_00 - 700_00
    assert b["unvested"]["cents"] == 1_100_00 and b["unvested"]["holdings"][0]["account"] == "Brokerage"
    assert b["history"][-1]["cents"] == b["cents"] and b["history"][0]["date"] == (END - timedelta(days=60)).isoformat()


def test_groups_follow_the_kind_from_settings():
    accounts = sample()
    cd = AccountIn(6, "Marcus", "depository", None, 10_000_00, [(WEEK, 10_000_00)])
    accounts.append(cd)
    rows = {r["name"]: r for g in networth.breakdown(accounts, END)["groups"] for r in g["accounts"]}
    assert rows["Marcus"]["kind"] == "Checking"
    cd.kind_set = "cd"
    accounts[2].kind_set = "retirement"  # the "Brokerage" is really an inherited IRA
    by = {g["key"]: g for g in networth.breakdown(accounts, END)["groups"]}
    assert next(r for r in by["cash"]["accounts"] if r["name"] == "Marcus")["kind"] == "CD"
    assert "Brokerage" in {r["name"] for r in by["retirement"]["accounts"]}
    assert "investments" not in by


def test_a_week_splits_markets_from_saving():
    week = next(w for w in networth.breakdown(sample(), END)["windows"] if w["key"] == "week")
    rows = {r["name"]: r for r in week["accounts"]}
    assert rows["401(k)"]["change_cents"] == 320_00
    assert rows["401(k)"]["market_cents"] == 100_00 and rows["401(k)"]["split"] == "holdings"
    assert rows["Brokerage"]["market_cents"] == 100_00 and rows["Brokerage"]["split"] == "activity"
    assert rows["Card"]["change_cents"] == -500_00 and rows["Card"]["market_cents"] is None
    assert rows["Checking"]["other_cents"] == 1_000_00
    assert "Old 401(k)" not in rows  # no change since it was linked
    assert week["change_cents"] == 1_000_00 + 320_00 + 600_00 - 500_00
    assert week["market_cents"] + week["saving_cents"] == week["change_cents"]
    assert week["movers"] == [{"symbol": "VTI", "name": "Total Stock Market", "cents": 100_00}]
    # Ordered by how much each account moved net worth.
    assert [r["name"] for r in week["accounts"]][:2] == ["Checking", "Brokerage"]


def test_windows_before_the_first_balance_start_there():
    b = networth.breakdown(sample(), END)
    year = next(w for w in b["windows"] if w["key"] == "year")
    assert year["since"] == (END - timedelta(days=60)).isoformat()
    assert year["new_accounts"] == ["Old 401(k)"]
    assert next(w for w in b["windows"] if w["key"] == "ytd")["start"] == "2025-12-31"
    assert networth.window_start("month", date(2026, 3, 31)) == date(2026, 2, 28)
    assert networth.window_start("3m", date(2026, 1, 15)) == date(2025, 10, 15)


def test_without_holdings_or_activity_it_says_it_cant_split():
    a = AccountIn(1, "Manual IRA", "investment", None, 1_500_00, [(WEEK, 1_000_00)])
    week = next(w for w in networth.breakdown([a], END)["windows"] if w["key"] == "week")
    assert week["accounts"][0]["split"] == "none" and week["accounts"][0]["other_cents"] is None
    assert week["unsplit_cents"] == 500_00 and week["market_cents"] == 0 and week["saving_cents"] == 0


def test_demo_sheet_matches_home():
    h = demo.household(END)
    n = demo.net_worth(END)
    assert n["cents"] == h["net_worth"]["cents"]
    assert n["history"][-90:] == h["net_worth"]["history"]
    today_ = n["windows"][0]
    assert today_["change_cents"] == h["net_worth"]["change_today_cents"]
    assert abs(today_["saving_cents"]) < 100  # the day is the market alone, give or take rounding
    assert {g["key"] for g in n["groups"]} == {"cash", "investments", "retirement", "cards"}
    assert n["unvested"]["cents"] > 0 and all(w["accounts"] for w in n["windows"])


def test_demo_route(tmp_path):
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    r = c.get("/api/net-worth")
    assert r.status_code == 200 and r.json()["linked"] is True


@needs_db
def test_from_the_database_matches_household(client, home):
    home.setup()
    on = today()
    week = on - timedelta(days=7)
    with db_of(client).session() as db:
        conn = Connection(provider="manual", institution="Fidelity", state={})
        db.add(conn)
        db.flush()
        bank = Account(connection_id=conn.id, external_id="c", name="Checking", institution="Ally", type="depository")
        ira = Account(
            connection_id=conn.id, external_id="i", name="Roth IRA", institution="Fidelity", type="investment"
        )
        card = Account(connection_id=conn.id, external_id="k", name="Gold", institution="Amex", type="credit")
        db.add_all([bank, ira, card])
        db.flush()
        db.add_all(
            [
                Balance(account_id=bank.id, date=week, current_cents=2_000_00),
                Balance(account_id=bank.id, date=on, current_cents=2_500_00),
                Balance(account_id=card.id, date=on, current_cents=300_00),
                Balance(account_id=ira.id, date=week, current_cents=1_000_00),
                Balance(account_id=ira.id, date=on, current_cents=1_300_00),
            ]
        )
        snap = dict(account_id=ira.id, symbol="FXAIX", name="Fidelity 500", asset_class="US stocks")
        for d, qty, price in ((week, 10, Decimal(100)), (on, 12, Decimal("108.3333"))):
            value = round(qty * price * 100)
            db.add(HoldingSnapshot(date=d, quantity=Decimal(qty), price=price, value_cents=value, **snap))
        unvested = dict(symbol="NTAP", name="NetApp", quantity=Decimal(0), unvested_quantity=Decimal(5))
        db.add(HoldingSnapshot(date=on, account_id=ira.id, asset_class="US stocks", price=Decimal(110), value_cents=0,
                               **unvested))  # fmt: skip
        contribution = dict(type="cash", subtype="contribution", name="Contribution", source="plaid")
        db.add(InvestmentTransaction(account_id=ira.id, external_id="t1", date=on - timedelta(days=1),
                                     amount_cents=216_67, **contribution))  # fmt: skip
    with db_of(client).session() as db:
        n = networth.build(db, on)
        h = household.build(db)
    assert n["cents"] == h["net_worth"]["cents"] == 2_500_00 + 1_300_00 - 300_00
    w = next(w for w in n["windows"] if w["key"] == "week")
    rows = {r["name"]: r for r in w["accounts"]}
    assert rows["Fidelity Roth IRA"]["split"] == "holdings"
    assert rows["Fidelity Roth IRA"]["market_cents"] == 83_33  # 10 shares up $8.3333
    assert {g["key"] for g in n["groups"]} == {"cash", "retirement", "cards"}
    assert n["unvested"]["holdings"] == [
        {"symbol": "NTAP", "name": "NetApp", "shares": 5.0, "value_cents": 550_00, "account": "Fidelity Roth IRA"}
    ]
    assert client.get("/api/net-worth").json()["cents"] == n["cents"]


def test_the_chart_moves_by_exactly_each_windows_change():
    """Linking an account isn't a jump on the chart: it's drawn at its first balance from the start, the way the
    windows count it, so the line moves by exactly the change the card prints beside it."""
    b = networth.breakdown(sample(), END)
    by_day = {p["date"]: p["cents"] for p in b["history"]}
    for w in b["windows"]:
        assert by_day[END.isoformat()] - by_day[w["since"]] == w["change_cents"], w["key"]
    linked = (END - timedelta(days=3)).isoformat()
    day_before = (END - timedelta(days=4)).isoformat()
    assert by_day[linked] - by_day[day_before] == 0


def _quote(db, symbol, price, previous, at):
    from financeapp.models import Quote

    db.add(Quote(symbol=symbol, price=Decimal(price), previous_close=Decimal(previous), as_of=at, source="yahoo"))


@needs_db
def test_today_is_since_the_last_close_and_skips_new_accounts(client, home):
    """Today on Home: yesterday's point values the holdings at the last close (the brokerage's own price can be a day
    behind), and an account linked today isn't a gain."""
    from datetime import UTC, datetime

    home.setup()
    on = today()
    yesterday = on - timedelta(days=1)
    with db_of(client).session() as db:
        conn = Connection(provider="manual", institution="Fidelity", state={})
        db.add(conn)
        db.flush()
        bank = Account(connection_id=conn.id, external_id="c", name="Checking", institution="Ally", type="depository")
        ira = Account(
            connection_id=conn.id, external_id="i", name="Roth IRA", institution="Fidelity", type="investment"
        )
        new = Account(connection_id=conn.id, external_id="n", name="HSA", institution="Fidelity", type="depository")
        db.add_all([bank, ira, new])
        db.flush()
        snap = dict(account_id=ira.id, symbol="VOO", name="Vanguard S&P 500", asset_class="Funds")
        snap["quantity"] = Decimal(100)
        # The brokerage's price lags a day: last night it still had the day before's $500 close, this morning
        # yesterday's $510. The quote has today's $512, after yesterday's $510 close.
        db.add_all(
            [
                Balance(account_id=bank.id, date=yesterday, current_cents=5_000_00),
                Balance(account_id=bank.id, date=on, current_cents=5_100_00),
                Balance(account_id=ira.id, date=yesterday, current_cents=50_000_00 + 50_00),
                Balance(account_id=ira.id, date=on, current_cents=51_000_00 + 50_00),
                Balance(account_id=new.id, date=on, current_cents=8_000_00),
                HoldingSnapshot(date=yesterday, price=Decimal(500), value_cents=50_000_00, **snap),
                HoldingSnapshot(date=on, price=Decimal(510), value_cents=51_000_00, **snap),
            ]
        )
        _quote(db, "VOO", 512, 510, datetime.now(UTC))
    with db_of(client).session() as db:
        h = household.build(db)
        n = networth.build(db, on)
    assert h["net_worth"]["cents"] == n["cents"] == 5_100_00 + 51_200_00 + 50_00 + 8_000_00
    day = n["windows"][0]
    # $100 more in checking, and 100 shares up $2 today. Not yesterday's $10, not the new $8,000.
    assert h["net_worth"]["change_today_cents"] == day["change_cents"] == 100_00 + 200_00
    assert day["market_cents"] == 200_00 and day["saving_cents"] == 100_00
    assert h["net_worth"]["history"][-1]["cents"] - h["net_worth"]["history"][-2]["cents"] == 300_00
    by_day = {p["date"]: p["cents"] for p in n["history"]}
    assert by_day[on.isoformat()] - by_day[yesterday.isoformat()] == 300_00
