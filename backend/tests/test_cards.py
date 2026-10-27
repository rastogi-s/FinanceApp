import json
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from financeapp import cards, sync
from financeapp.api import card_routes
from financeapp.config import Settings
from financeapp.connectors.plaid import Plaid
from financeapp.main import create_app
from financeapp.models import Account, Balance, CardStatement, Connection, Transaction
from tests.conftest import csrf, db_of, needs_db
from tests.test_agents import call, new_key

TODAY = date(2026, 10, 27)  # Q4: Freedom Flex pays 5% on groceries and dining
VALUES = cards.values_default()


def card(account_id: int, product: str, choice: str | None = None, role: str = "active") -> cards.Card:
    p = cards.product(product)
    return cards.Card(account_id, f"{p['short']} ••{account_id:04d}", p, choice, role)


def tags(*names: str, merchant: str | None = None, channel: str | None = None) -> frozenset[str]:
    m = cards.directory()[merchant] if merchant else None
    t = set(names) | (set(m["tags"]) | {"m:" + merchant} if m else set())
    cards.finish(t, channel)
    return frozenset(t)


def best(wallet: list[cards.Card], t: frozenset[str], channel: str | None = "in store", ledger=None, **kw) -> dict:
    return cards.rank(wallet, t, channel, TODAY, ledger or cards.Ledger(), VALUES, merchant=kw.get("merchant"))[0]


# ---- the catalog


def test_the_catalog_is_complete():
    c = cards.catalog()
    ids = [x["id"] for x in c["cards"]]
    assert len(ids) == len(set(ids)) >= 20
    known = set(cards.CATEGORIES)
    merchants = set(cards.directory())
    for x in c["cards"]:
        assert x["program"] in c["programs"], x["id"]
        assert x["source"].startswith("https://") or x["id"].startswith("flat"), x["id"]
        for r in x["earn"]:
            assert r["note"] and r["rate"] > 0, x["id"]
            cats = r.get("category", [])
            for cat in cats if isinstance(cats, list) else [cats]:
                assert cat in known, (x["id"], cat)
            for m in r.get("merchants", []):
                assert m in merchants, (x["id"], m)
            for quarter in r.get("rotating", {}).values():
                assert set(quarter) <= known
            assert r.get("period", "year") in ("month", "quarter", "year")
        for credit in x["credits"]:
            assert credit["period"] in ("month", "quarter", "half", "year") and credit["amount"] > 0
    for m in cards.directory().values():
        assert set(m["tags"]) <= known and m["channel"] in ("online", "in store", "both")


def test_suggesting_the_card_from_the_banks_names():
    assert cards.suggest("American Express", "Gold Card") == "amex-gold"
    assert cards.suggest("American Express", "Blue Cash Preferred") == "amex-blue-cash-preferred"
    assert cards.suggest("Chase", "Sapphire Preferred") == "chase-sapphire-preferred"
    assert cards.suggest("Chase", "Freedom Flex") == "chase-freedom-flex"
    assert cards.suggest("Citibank", "Costco Anywhere Visa") == "citi-costco"
    assert cards.suggest("Capital One", "Venture X") == "capital-one-venture-x"
    assert (
        cards.suggest("Capital One", "VentureOne") is None
        or cards.suggest("Capital One", "Venture") == "capital-one-venture"
    )
    assert cards.suggest("Chase", "CREDIT CARD") is None  # not enough to go on: ask


def test_store_names_and_codes_become_reward_tags():
    assert cards.known_merchant("COSTCO WHSE #1234")["id"] == "costco"
    assert cards.known_merchant("Delta Air Lines 0062")["id"] == "delta"
    assert cards.known_merchant("United Healthcare") is None  # one-word keys only match the whole name
    assert (
        cards.mcc_tag("3058") == "flights" and cards.mcc_tag("3700") == "hotels" and cards.mcc_tag("5812") == "dining"
    )
    # A superstore coded as a supermarket still isn't groceries for card bonuses.
    walmart = cards.tags_for(cards.directory()["walmart"], "5411", None, "in store")
    assert "groceries" not in walmart and "superstores" in walmart
    # Without a code, Plaid's category stands in; online shopping is online retail.
    assert "dining" in cards.tags_for(None, None, "FOOD_AND_DRINK_RESTAURANT", "in store")
    assert "online_retail" in cards.tags_for(cards.directory()["target"], None, None, "online")
    assert "travel" in cards.tags_for(None, "4511", None, None)


# ---- the ranking


def test_the_best_card_by_category():
    wallet = [card(1, "amex-gold"), card(2, "chase-sapphire-preferred"), card(3, "citi-double-cash")]
    assert best(wallet, tags("dining"))["account_id"] == 1  # 4x
    assert best(wallet, tags("gas"))["account_id"] == 2  # 3x beats 2%
    assert best(wallet, frozenset())["account_id"] == 3  # 2% on everything else
    r = best(wallet, tags("flights"))
    assert r["account_id"] == 1 and r["rate"] == "3x" and r["value_cents"] == 300
    # At 2 cents a point, Membership Rewards beat 2% cash back on everything else too.
    rows = cards.rank(wallet, frozenset(), "in store", TODAY, cards.Ledger(), {**VALUES, "amex-mr": 2.0})
    assert rows[0]["account_id"] == 1 and rows[0]["value_cents"] == 200


def test_caps_hand_over_to_the_next_card():
    wallet = [card(1, "amex-blue-cash-preferred"), card(2, "citi-double-cash")]
    ledger = cards.Ledger()
    groceries = tags("groceries")
    assert best(wallet, groceries, ledger=ledger)["account_id"] == 1
    # $5,950 of the $6,000 a year at 6% spent: a $100 shop gets 6% on $50 and 1% on the rest.
    cards.earn(wallet[0], groceries, "in store", 5950_00, TODAY, ledger, VALUES, record=True)
    e = cards.earn(wallet[0], groceries, "in store", 100_00, TODAY, ledger, VALUES)
    assert e.cents == pytest.approx(50 * 6 + 50 * 1) and e.capped
    top = best(wallet, groceries, ledger=ledger)
    assert top["account_id"] == 1 and top["cap"] == "$5,950 of $6,000 used this year"
    cards.earn(wallet[0], groceries, "in store", 50_00, TODAY, ledger, VALUES, record=True)
    assert best(wallet, groceries, ledger=ledger)["account_id"] == 2  # used up: 2% wins now
    # A new year starts over.
    assert cards.earn(wallet[0], groceries, "in store", 100_00, date(2027, 1, 2), ledger, VALUES).cents == 600


def test_shared_caps_and_merchant_bonuses():
    costco = card(1, "citi-costco")
    ledger = cards.Ledger()
    gas_at_costco = tags("gas", merchant="costco")
    assert cards.earn(costco, gas_at_costco, "in store", 100_00, TODAY, ledger, VALUES).rate == 5
    assert cards.earn(costco, tags("gas"), "in store", 100_00, TODAY, ledger, VALUES).rate == 4
    assert cards.earn(costco, tags(merchant="costco"), "in store", 100_00, TODAY, ledger, VALUES).rate == 2
    # Gas at Costco and other gas share one $7,000 cap.
    cards.earn(costco, tags("gas"), "in store", 7000_00, TODAY, ledger, VALUES, record=True)
    assert cards.earn(costco, gas_at_costco, "in store", 100_00, TODAY, ledger, VALUES).cents == 200  # Costco's 2%


def test_costco_takes_only_visa_in_its_warehouses():
    wallet = [card(1, "amex-gold"), card(2, "chase-sapphire-preferred"), card(3, "citi-costco")]
    costco = cards.directory()["costco"]
    rows = cards.rank(wallet, tags(merchant="costco"), "in store", TODAY, cards.Ledger(), VALUES, merchant=costco)
    assert rows[0]["account_id"] == 3 and rows[0]["rate"] == "2%"
    amex = next(r for r in rows if r["account_id"] == 1)
    assert amex["blocked"] and "Visa" in amex["warnings"][0] and rows[-1]["account_id"] == 1
    online = cards.rank(wallet, tags(merchant="costco"), "online", TODAY, cards.Ledger(), VALUES, merchant=costco)
    assert not any(r["blocked"] for r in online)


def test_rotating_choice_channel_and_end_dates():
    flex = card(1, "chase-freedom-flex")
    # Oct-Dec 2026: groceries and dining at 5%, activation reminded.
    r = best([flex], tags("groceries"))
    assert r["rate"] == "5x" and "Activate" in r["warnings"][0]
    assert cards.earn(flex, tags("groceries"), "in store", 100_00, date(2027, 1, 5), None, VALUES).rate == 1
    # Discover's quarters aren't known: no bonus is assumed.
    assert best([card(2, "discover-it")], tags("groceries"))["rate"] == "1%"
    # The category you chose on Customized Cash.
    boa = card(3, "bofa-customized-cash", choice="online_retail")
    assert best([boa], tags(merchant="target", channel="online"), "online")["rate"] == "3%"
    assert best([boa], tags("gas"))["rate"] == "1%"
    # Online grocery only on the Sapphire Preferred; Lyft's 5x only until it ends.
    csp = card(4, "chase-sapphire-preferred")
    assert best([csp], tags("groceries", channel="online"), "online")["rate"] == "3x"
    assert best([csp], tags("groceries"), "in store")["rate"] == "1x"
    lyft = tags("transit", merchant="lyft")
    assert cards.earn(csp, lyft, "online", 100_00, TODAY, None, VALUES).rate == 5
    assert cards.earn(csp, lyft, "online", 100_00, date(2027, 10, 1), None, VALUES).rate == 2  # travel


def test_portal_rates_are_a_hint_not_a_ranking():
    csr = card(1, "chase-sapphire-reserve")
    r = best([csr], tags("hotels"))
    assert r["rate"] == "4x" and r["portal"] == "8x if booked through Chase Travel"
    gold = card(2, "amex-gold")
    assert best([gold], tags("hotels"))["portal"] == "5x if booked through Amex Travel"


def test_drawer_and_closed_cards_are_left_out():
    wallet = [card(1, "amex-gold", role="drawer"), card(2, "flat-1")]
    assert [r["account_id"] for r in cards.rank(wallet, tags("dining"), None, TODAY, cards.Ledger(), VALUES)] == [2]


def test_missed_points():
    gold, csp = card(1, "amex-gold"), card(2, "chase-sapphire-preferred")

    def bought(i, account, *t, cents=100_00, merchant="Shop"):
        return cards.Purchase(
            i, TODAY - timedelta(days=i), cents, merchant, merchant.lower(), tags(*t), "in store", account
        )

    purchases = [
        bought(1, 1, "gas", merchant="Shell"),  # 1x on the Gold, 3x on the Sapphire: 200 cents missed
        bought(2, 2, "dining", merchant="Cafe"),  # 3x, the Gold pays 4x: 100 missed
        bought(3, 1, "groceries", merchant="Kroger"),  # already the best
    ]
    m = cards.missed([gold, csp], purchases, TODAY - timedelta(days=30), TODAY, VALUES)
    assert m["spent_cents"] == 300_00 and m["earned_cents"] == 100 + 300 + 400
    assert m["best_cents"] == 300 + 400 + 400 and m["missed_cents"] == 300
    assert [c["category"] for c in m["categories"]][:2] == ["gas", "dining"]
    assert m["categories"][0]["use"] == csp.label
    assert [p["merchant"] for p in m["purchases"]] == ["Shell", "Cafe"]


def test_store_lookup_from_history():
    gold, costco = card(1, "amex-gold"), card(2, "citi-costco")
    history = [
        cards.Purchase(
            i,
            TODAY - timedelta(days=i),
            40_00,
            "SQ *BLUE BOTTLE 0042",
            "blue bottle",
            cards.tags_for(None, "5814", None, "in store"),
            "in store",
            1,
            mcc="5814",
        )
        for i in range(3)
    ]
    found = cards.lookup([gold, costco], history, TODAY, VALUES, cards.Ledger(), "blue bottle")
    assert found["found"] and found["name"] == "SQ *BLUE BOTTLE 0042"
    assert found["how"] == "Your 3 purchases there were coded 5814 (restaurant)."
    assert [c["channel"] for c in found["channels"]] == ["in store"]
    assert found["channels"][0]["cards"][0]["account_id"] == 1  # 4x dining beats 3%

    known = cards.lookup([gold, costco], [], TODAY, VALUES, cards.Ledger(), "Costco")
    assert known["how"] == "A store the app knows." and [c["channel"] for c in known["channels"]] == CHANNELS
    in_store, online = (c["cards"] for c in known["channels"])
    assert in_store[-1]["blocked"] and not online[-1]["blocked"]  # the Amex: not in the warehouse, fine online
    unknown = cards.lookup([gold], [], TODAY, VALUES, cards.Ledger(), "Zzyzx Supply")
    assert not unknown["found"] and unknown["kinds"]
    told = cards.lookup([gold], [], TODAY, VALUES, cards.Ledger(), "Zzyzx Supply", kind="dining")
    assert told["found"] and told["channels"][0]["cards"][0]["rate"] == "4x"


CHANNELS = ["in store", "online"]


# ---- the API, on a real database


def credit_card(client, name: str, institution: str, mask: str, owed: float, limit: float | None = None) -> int:
    with db_of(client).session() as db:
        conn = Connection(provider="plaid", institution=institution, state={})
        db.add(conn)
        db.flush()
        a = Account(
            connection_id=conn.id,
            external_id=name,
            name=name,
            institution=institution,
            type="credit",
            mask=mask,
            credit_limit_cents=round(limit * 100) if limit else None,
        )
        db.add(a)
        db.flush()
        db.add(Balance(account_id=a.id, date=TODAY, current_cents=round(owed * 100)))
        return a.id


def spend(
    client,
    account_id: int,
    merchant: str,
    dollars: float,
    days_ago: int,
    mcc: str | None = None,
    channel: str = "in store",
) -> None:
    with db_of(client).session() as db:
        n = db.scalar(select(Transaction.id).order_by(Transaction.id.desc()).limit(1)) or 0
        db.add(
            Transaction(
                account_id=account_id,
                external_id=f"t{n + 1}",
                date=TODAY - timedelta(days=days_ago),
                amount_cents=-round(dollars * 100),
                merchant=merchant,
                description=merchant,
                source="plaid",
                mcc=mcc,
                channel=channel,
            )
        )


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(card_routes, "today", lambda: TODAY)


@needs_db
def test_the_cards_page(client, home, fixed_today):
    home.setup()
    gold = credit_card(client, "Gold Card", "American Express", "1008", 2140)
    csp = credit_card(client, "Sapphire Preferred", "Chase", "9012", 860, limit=18000)
    spend(client, gold, "Shell", 50, 3, "5541")
    spend(client, gold, "Whole Foods Market", 120, 5, "5411")
    spend(client, csp, "Sweetgreen", 20, 1, "5812")
    with db_of(client).session() as db:
        db.add(
            CardStatement(
                account_id=csp,
                as_of=TODAY,
                purchase_apr=21.49,
                aprs=[],
                minimum_cents=35_00,
                due_date=TODAY + timedelta(days=10),
            )
        )

    page = client.get("/api/cards").json()
    rows = {r["account_id"]: r for r in page["cards"]}
    assert not rows[gold]["linked"] and rows[gold]["suggested"] == "amex-gold"
    assert rows[csp]["utilization"] == pytest.approx(4.8) and rows[csp]["statement"]["minimum_cents"] == 35_00
    assert all(b["best"] is None for b in page["best"])  # nothing linked yet

    h = csrf(client)
    assert client.put(f"/api/cards/{gold}/link", json={"product": "nope"}, headers=h).status_code == 422
    assert client.put(f"/api/cards/{gold}/link", json={"product": "amex-gold"}, headers=h).status_code == 200
    page = client.put(f"/api/cards/{csp}/link", json={"product": "chase-sapphire-preferred"}, headers=h).json()
    rows = {r["account_id"]: r for r in page["cards"]}
    assert rows[gold]["label"] == "Amex Gold ••1008" and rows[gold]["product"]["annual_fee"] == 325
    assert rows[gold]["earned_year_cents"] == 50 + 480  # gas 1x, groceries 4x
    best = {b["category"]: b for b in page["best"]}
    assert best["dining"]["best"]["account_id"] == gold and best["gas"]["best"]["account_id"] == csp
    assert best["groceries"]["spent_cents"] == 120_00

    found = client.get("/api/cards/lookup", params={"q": "shell"}).json()
    assert found["found"] and found["channels"][0]["cards"][0]["account_id"] == csp
    m = client.get("/api/cards/missed", params={"months": 1}).json()
    assert m["missed_cents"] == 100 + 20  # Shell: 3x not 1x on $50; Sweetgreen: 4x not 3x on $20
    assert client.get("/api/cards/missed", params={"months": 2}).status_code == 422

    # Points worth more: Membership Rewards at 2 cents.
    page = client.put("/api/cards/values", json={"cpp": {"amex-mr": 2}}, headers=h).json()
    assert next(p for p in page["programs"] if p["id"] == "amex-mr")["cpp"] == 2
    assert client.put("/api/cards/values", json={"cpp": {"amex-mr": 50}}, headers=h).status_code == 422

    page = client.delete(f"/api/cards/{gold}/link", headers=h).json()
    assert not next(r for r in page["cards"] if r["account_id"] == gold)["linked"]

    key = new_key(client)
    answer = call(client, key, "best_card", {"store": "Shell", "amount": 40})["structuredContent"]
    assert answer["by_channel"][0]["cards"][0]["earns"] == 1.20  # 3x on $40 at a cent a point


def test_demo_mode_cards(tmp_path):
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    page = c.get("/api/cards").json()
    assert page["demo"] and {r["label"] for r in page["cards"]} == {"Amex Gold ••1008", "Sapphire Preferred ••9012"}
    assert c.get("/api/cards/lookup", params={"q": "trader joe"}).json()["found"]
    assert c.get("/api/cards/missed").json()["missed_cents"] > 0
    assert c.put("/api/cards/4/link", json={"product": "amex-gold"}).status_code in (403, 401)


# ---- Plaid: codes, channels, limits and card statements


def test_plaid_sends_codes_limits_and_statements():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content)
        seen.append((path, body.get("cursor")))
        if path == "/accounts/get":
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "account_id": "card",
                            "name": "Gold",
                            "type": "credit",
                            "mask": "1008",
                            "balances": {"current": 100, "limit": 5000},
                        },
                    ]
                },
            )
        if path == "/transactions/sync":
            return httpx.Response(
                200,
                json={
                    "added": [
                        {
                            "transaction_id": "t1",
                            "account_id": "card",
                            "date": "2026-10-20",
                            "amount": 12.5,
                            "merchant_name": "Blue Bottle",
                            "merchant_category_code": "5814",
                            "payment_channel": "in store",
                            "website": "bluebottlecoffee.com",
                        },
                        {
                            "transaction_id": "t2",
                            "account_id": "card",
                            "date": "2026-10-21",
                            "amount": 3,
                            "name": "X",
                            "merchant_category_code": "abc",
                            "payment_channel": "nonsense",
                        },
                    ],
                    "modified": [],
                    "removed": [],
                    "next_cursor": "c9",
                    "has_more": False,
                },
            )
        if path == "/item/get":
            return httpx.Response(200, json={"item": {"products": ["transactions", "liabilities"]}})
        if path == "/liabilities/get":
            return httpx.Response(
                200,
                json={
                    "liabilities": {
                        "credit": [
                            {
                                "account_id": "card",
                                "aprs": [{"apr_type": "purchase_apr", "apr_percentage": 24.49}],
                                "last_statement_balance": 98.1,
                                "last_statement_issue_date": "2026-10-05",
                                "minimum_payment_amount": 35,
                                "next_payment_due_date": "2026-11-01",
                                "is_overdue": False,
                            },
                        ]
                    }
                },
            )
        return httpx.Response(404, json={"error_code": "NOT_FOUND"})

    plaid = Plaid("cid", "sec", "sandbox", httpx.Client(transport=httpx.MockTransport(handler)))
    # A login synced before codes existed pulls its history again, once.
    result = plaid.fetch("tok", "item", {"cursor": "old"})
    assert ("/transactions/sync", None) in seen and result.state == {"cursor": "c9", "mcc": True}
    assert result.accounts[0].limit_cents == 500000
    t1, t2 = result.transactions
    assert (t1.mcc, t1.channel, t1.website) == ("5814", "in store", "bluebottlecoffee.com")
    assert (t2.mcc, t2.channel, t2.website) == (None, None, None)
    [s] = result.card_statements
    assert float(s.purchase_apr) == 24.49 and s.minimum_cents == 3500 and s.due_date == date(2026, 11, 1)
    seen.clear()
    plaid.fetch("tok", "item", result.state)
    assert ("/transactions/sync", "c9") in seen


def test_plaid_never_adds_liabilities_to_a_login_without_it():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/accounts/get":
            return httpx.Response(
                200,
                json={"accounts": [{"account_id": "c", "name": "Card", "type": "credit", "balances": {"current": 1}}]},
            )
        if request.url.path == "/transactions/sync":
            return httpx.Response(
                200, json={"added": [], "modified": [], "removed": [], "next_cursor": "c", "has_more": False}
            )
        if request.url.path == "/item/get":
            return httpx.Response(
                200, json={"item": {"products": ["transactions"], "billed_products": ["transactions"]}}
            )
        return httpx.Response(500)

    plaid = Plaid("cid", "sec", "sandbox", httpx.Client(transport=httpx.MockTransport(handler)))
    assert plaid.fetch("tok", "item", {}).card_statements == []
    assert "/liabilities/get" not in paths


@needs_db
def test_sync_saves_codes_and_statements(client, home, monkeypatch):
    home.setup()

    class Fake:
        provider = "plaid"

        def fetch(self, token, external_id, state):
            from financeapp.connectors.base import AccountData, CardStatementData, FetchResult, TransactionData

            return FetchResult(
                accounts=[
                    AccountData("card", "Gold", "American Express", "credit", None, "1008", 100_00, limit_cents=5000_00)
                ],
                transactions=[
                    TransactionData(
                        "card",
                        "t1",
                        TODAY,
                        -12_50,
                        "Blue Bottle",
                        "",
                        mcc="5814",
                        channel="in store",
                        website="bluebottlecoffee.com",
                    )
                ],
                card_statements=[CardStatementData("card", minimum_cents=35_00, due_date=TODAY)],
            )

    monkeypatch.setattr(sync, "connector_for", lambda provider, settings: Fake())
    from financeapp import crypto

    with db_of(client).session() as db:
        conn = Connection(
            provider="plaid",
            institution="American Express",
            state={},
            token_enc=crypto.encrypt("tok", "connection:plaid"),
        )
        db.add(conn)
        db.flush()
        sync.sync_connection(db, conn, client.app.state.settings, TODAY)
    with db_of(client).session() as db:
        t = db.scalar(select(Transaction))
        assert (t.mcc, t.channel, t.website) == ("5814", "in store", "bluebottlecoffee.com")
        a = db.scalar(select(Account))
        assert a.credit_limit_cents == 5000_00
        assert db.get(CardStatement, a.id).minimum_cents == 35_00
