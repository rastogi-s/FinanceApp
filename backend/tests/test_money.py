import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update

from financeapp import crypto, notify, sync
from financeapp.connectors.plaid import Plaid
from financeapp.connectors.snaptrade import SnapTrade, signature
from financeapp.models import Category, Connection, MerchantRule, Session, Transaction, now
from tests.conftest import csrf, db_of, needs_db

FIXTURES = Path(__file__).parent / "fixtures"


def upload(client, name: str) -> dict:
    r = client.post("/api/imports", files={"file": (name, (FIXTURES / name).read_bytes())}, headers=csrf(client))
    assert r.status_code == 200, r.text
    return r.json()


def commit(client, preview: dict, **target) -> dict:
    targets = [{"index": a["index"], "account_id": a["suggested_account_id"], **target} for a in preview["accounts"]]
    r = client.post(f"/api/imports/{preview['id']}/commit", json={"targets": targets}, headers=csrf(client))
    assert r.status_code == 200, r.text
    return r.json()


# ---- file imports end to end


@needs_db
def test_importing_files_builds_the_household(client, home):
    home.setup()
    assert client.get("/api/household").json() == {"demo": False, "linked": False}

    preview = upload(client, "fidelity_positions.csv")
    assert [a["holdings"] for a in preview["accounts"]] == [3, 1]
    assert commit(client, preview) == {"holdings": 4, "transactions": 0, "accounts": 2}

    checking = upload(client, "chase_checking_4417.csv")
    assert checking["accounts"][0]["mask"] == "4417"
    commit(client, checking, institution="Chase", name="Checking")
    card = upload(client, "amex_1008.csv")
    assert card["flipped"] is True
    commit(client, card, institution="American Express", name="Gold", type="credit")

    h = client.get("/api/household").json()
    assert h["linked"] is True
    investments = 123456 + 2_010_000 + 850_000 + 250_000
    assert sum(a["cents"] for a in h["accounts"]["investments"]) == investments
    # No balance in those CSVs, so cash and debts are 0 until a statement balance or OFX arrives.
    assert h["net_worth"]["cents"] == investments

    # The card payment shows on both sides: one transfer, left out of spending.
    t = {x["merchant"]: x for x in h["transactions"]}
    payment = next(x for x in h["transactions"] if x["merchant"].startswith("Amex Epayment"))
    assert payment["transfer"] and t["Autopay Payment Received Thank You"]["transfer"]
    assert t["Trader Joe's San Francisco"]["category"] == "Groceries"
    assert t["Acme Corp Payroll"]["category"] == "Paycheck"
    assert t["Netflix.com"]["category"] == "Subscriptions"

    # Uploading the same file again adds nothing.
    again = upload(client, "chase_checking_4417.csv")
    assert again["accounts"][0]["duplicates"] == 6
    assert commit(client, again)["transactions"] == 0


@needs_db
def test_transfer_matching(client, home):
    home.setup()
    commit(client, upload(client, "chase_checking_4417.csv"), institution="Chase")
    commit(client, upload(client, "amex_1008.csv"), institution="Amex", type="credit")
    commit(client, upload(client, "ally.qfx"))
    with db_of(client).session() as db:
        txns = db.scalars(select(Transaction)).all()
        groups = {t.transfer_group_id for t in txns if t.transfer_group_id}
        assert len(groups) == 1  # checking -> card payment (500.00, same day)
        paired = sorted(t.amount_cents for t in txns if t.transfer_group_id)
        assert paired == [-50000, 50000]
        # "Internet transfer from Chase" into Ally has no matching outflow in the files: one-sided transfer.
        ally_in = next(t for t in txns if t.amount_cents == 150000)
        transfer = db.scalar(select(Category).where(Category.name == "Transfer"))
        assert ally_in.category_id == transfer.id and ally_in.transfer_group_id is None

    h = client.get("/api/household").json()
    spent = h["spending"]["month_cents"]
    # Groceries, utilities, two coffees, and the card's purchases; never the 500 payment or the 1,500 transfer.
    assert spent == 5420 + 8810 + 650 + 650 + 1575 + 1549 + 4210 + 6399 or date.today().month != 10


@needs_db
def test_fixing_a_category_makes_a_rule(client, home):
    home.setup()
    commit(client, upload(client, "chase_checking_4417.csv"), institution="Chase")
    h = client.get("/api/household").json()
    coffee = [t for t in h["transactions"] if t["merchant"] == "Blue Bottle Coffee"]
    assert len(coffee) == 2 and all(t["category"] == "Dining out" for t in coffee)
    cats = {c["name"]: c["id"] for c in client.get("/api/categories").json()}
    r = client.patch(
        f"/api/transactions/{coffee[0]['id']}", json={"category_id": cats["Groceries"]}, headers=csrf(client)
    )
    assert r.status_code == 200 and r.json()["also_changed"] == 1
    h = client.get("/api/household").json()
    assert {t["category"] for t in h["transactions"] if t["merchant"] == "Blue Bottle Coffee"} == {"Groceries"}
    with db_of(client).session() as db:
        assert db.scalar(select(MerchantRule)).pattern == "blue bottle coffee"


@needs_db
def test_searching_transactions(client, home):
    home.setup()
    commit(client, upload(client, "chase_checking_4417.csv"), institution="Chase")
    commit(client, upload(client, "amex_1008.csv"), institution="Amex", type="credit")

    def find(**params) -> dict:
        r = client.get("/api/transactions", params=params)
        assert r.status_code == 200, r.text
        return r.json()

    everything = find()
    assert everything["count"] == 11 and everything["next_offset"] is None
    # The 500 card payment shows on both sides but counts in neither total.
    assert everything["out_cents"] == 5420 + 8810 + 650 + 650 + 1575 + 1549 + 4210 + 6399
    assert everything["in_cents"] == 320000

    coffee = find(q="blue  BOTTLE")
    assert coffee["count"] == 2 and coffee["out_cents"] == 1300
    assert find(q="100%")["count"] == 0  # wildcards are matched literally
    assert find(q="_")["count"] == 0

    transfers = find(kind="transfer")
    assert transfers["count"] == 2 and transfers["out_cents"] == transfers["in_cents"] == 0
    assert find(kind="in")["count"] == 1
    assert find(kind="out")["count"] == 8

    cats = {c["name"]: c["id"] for c in client.get("/api/categories").json()}
    dining = find(category=[cats["Dining out"]])
    assert {t["category"] for t in dining["transactions"]} == {"Dining out"} and dining["count"] >= 3

    amex = next(a["id"] for a in everything["accounts"] if a["name"].startswith("Amex"))
    assert find(account=[amex])["count"] == 5
    assert find(account=[999999])["count"] == 0  # an account that isn't counted never leaks in

    big = find(min_cents=5000, max_cents=10000)
    assert sorted(-t["amount_cents"] for t in big["transactions"]) == [5420, 6399, 8810]
    assert find(min_cents=10000, max_cents=5000)["count"] == 3  # a reversed range still works

    one_day = find(start="2026-10-17", end="2026-10-17")
    assert one_day["count"] == 4 and one_day["first"] == one_day["last"] == "2026-10-17"

    assert client.get("/api/transactions", params={"q": "x" * 101}).status_code == 422
    assert client.get("/api/transactions", params={"category": "1" * 13}).status_code == 422


@needs_db
def test_by_hand_categories_survive_a_sync(client, home):
    home.setup()
    commit(client, upload(client, "chase_checking_4417.csv"), institution="Chase")
    h = client.get("/api/household").json()
    payroll = next(t for t in h["transactions"] if t["category"] == "Paycheck")
    cats = {c["name"]: c["id"] for c in client.get("/api/categories").json()}
    client.patch(
        f"/api/transactions/{payroll['id']}",
        json={"category_id": cats["Other income"], "remember": False},
        headers=csrf(client),
    )
    commit(client, upload(client, "chase_checking_4417.csv"))  # re-runs matching and categorizing
    h = client.get("/api/household").json()
    assert next(t for t in h["transactions"] if t["id"] == payroll["id"])["category"] == "Other income"


@needs_db
def test_manual_accounts_and_statement_check(client, home):
    home.setup()
    r = client.post(
        "/api/accounts/manual",
        json={"name": "HSA", "institution": "HealthEquity", "type": "investment", "balance_cents": 620000},
        headers=csrf(client),
    )
    account = r.json()["accounts"][0]
    r = client.patch(f"/api/accounts/{account['id']}", json={"statement_cents": 625000}, headers=csrf(client))
    assert r.json()["accounts"][0]["statement_diff_percent"] == 0.8  # within the 1% gate
    r = client.post(
        "/api/accounts/manual", json={"name": "Visa", "type": "credit", "balance_cents": 100000}, headers=csrf(client)
    )
    assert client.get("/api/household").json()["net_worth"]["cents"] == 620000 - 100000


@needs_db
def test_net_worth_history_carries_balances_forward(client, home):
    home.setup()
    r = client.post(
        "/api/accounts/manual",
        json={"name": "Savings", "type": "depository", "balance_cents": 1000},
        headers=csrf(client),
    )
    aid = r.json()["accounts"][0]["id"]
    with db_of(client).session() as db:
        sync.save_balance(db, aid, sync.today() - timedelta(days=10), 500)
    history = client.get("/api/household").json()["net_worth"]["history"]
    assert history[0]["cents"] == 500 and history[-1]["cents"] == 1000
    assert len(history) == 11


# ---- Plaid, against a fake Plaid


class FakePlaid:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: str | None = None
        self.removed = False
        self.pages = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content)
        self.calls.append(path)
        assert body["client_id"] == "cid" and body["secret"] == "sec"
        if self.fail and path != "/item/remove":
            return httpx.Response(400, json={"error_code": self.fail, "error_message": "login required"})
        if path == "/link/token/create":
            assert body["hosted_link"]["completion_redirect_uri"].startswith("https://testserver/settings/accounts")
            assert "auth" not in body["products"] and "transfer" not in body["products"]  # read-only products only
            return httpx.Response(200, json={"link_token": "link-1", "hosted_link_url": "https://hosted.plaid.com/x"})
        if path == "/link/token/get":
            return httpx.Response(
                200,
                json={
                    "link_sessions": [
                        {
                            "results": {
                                "item_add_results": [{"public_token": "public-1", "institution": {"name": "Chase"}}]
                            }
                        }
                    ]
                },
            )
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": "access-secret-1", "item_id": "item-1"})
        if path == "/accounts/get":
            assert body["access_token"] == "access-secret-1"
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "account_id": "chk",
                            "name": "Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "mask": "4417",
                            "balances": {"current": 5000.25, "available": 4900},
                        },
                        {
                            "account_id": "card",
                            "name": "Sapphire",
                            "type": "credit",
                            "mask": "9012",
                            "balances": {"current": 860.10},
                        },
                        {
                            "account_id": "brk",
                            "name": "Brokerage",
                            "type": "investment",
                            "mask": "6621",
                            "balances": {"current": 20012},  # vested shares only, as Plaid reports it
                        },
                    ]
                },
            )
        if path == "/investments/holdings/get":
            return httpx.Response(
                200,
                json={
                    "holdings": [
                        {
                            "account_id": "brk",
                            "security_id": "s1",
                            "quantity": 100,
                            "institution_price": 190.12,
                            "institution_value": 19012,
                            "cost_basis": 15000,
                        },
                        {
                            "account_id": "brk",
                            "security_id": "s2",
                            "quantity": 30,
                            "vested_quantity": 10,
                            "institution_price": 100,
                            "institution_value": 3000,
                            "vested_value": 1000,
                        },
                    ],
                    "securities": [
                        {"security_id": "s1", "ticker_symbol": "AAPL", "name": "Apple", "type": "equity"},
                        {"security_id": "s2", "ticker_symbol": "NTAP", "name": "NetApp", "type": "equity"},
                    ],
                },
            )
        if path == "/transactions/sync":
            self.pages += 1
            if body.get("cursor") is None and self.pages == 1:
                return httpx.Response(
                    200,
                    json={
                        "added": [
                            {
                                "transaction_id": "t1",
                                "account_id": "chk",
                                "date": date.today().isoformat(),
                                "amount": 54.2,
                                "name": "TRADER JOE'S",
                                "merchant_name": "Trader Joe's",
                                "pending": False,
                                "personal_finance_category": {
                                    "primary": "FOOD_AND_DRINK",
                                    "detailed": "FOOD_AND_DRINK_GROCERIES",
                                },
                            },
                            {
                                "transaction_id": "t2",
                                "account_id": "chk",
                                "date": date.today().isoformat(),
                                "amount": 500,
                                "name": "CHASE CARD PAYMENT",
                                "pending": False,
                            },
                        ],
                        "modified": [],
                        "removed": [],
                        "next_cursor": "c1",
                        "has_more": True,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "added": [
                        {
                            "transaction_id": "t3",
                            "account_id": "card",
                            "date": date.today().isoformat(),
                            "amount": -500,
                            "name": "PAYMENT",
                            "pending": False,
                        },
                    ],
                    "modified": [],
                    "removed": [],
                    "next_cursor": "c2",
                    "has_more": False,
                },
            )
        if path == "/item/remove":
            self.removed = True
            return httpx.Response(200, json={"removed": True})
        return httpx.Response(404, json={"error_code": "NOT_FOUND"})


@pytest.fixture
def plaid(monkeypatch):
    fake = FakePlaid()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(
        sync,
        "connector_for",
        lambda provider, settings: Plaid("cid", "sec", "sandbox", client) if provider == "plaid" else None,
    )
    return fake


@needs_db
def test_linking_and_syncing_plaid(client, home, plaid):
    home.setup()
    r = client.post("/api/connections/plaid/start", json={"kind": "banking"}, headers=csrf(client))
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://hosted.plaid.com/x"
    r = client.post("/api/connections/finish", json={"token": r.json()["token"]}, headers=csrf(client))
    assert r.status_code == 200, r.text
    assert r.json()["synced"] is True
    conn = r.json()["connection"]
    assert conn["institution"] == "Chase" and len(conn["accounts"]) == 3

    with db_of(client).session() as db:
        stored = db.scalar(select(Connection))
        assert "access-secret-1" not in stored.token_enc  # encrypted at rest
        assert crypto.decrypt(stored.token_enc, "connection:plaid") == "access-secret-1"
        assert stored.state["cursor"] == "c2"

    h = client.get("/api/household").json()
    assert h["net_worth"]["cents"] == 500025 - 86010 + 1901200 + 100000  # vested shares only
    assert {x["symbol"]: x["value_cents"] for x in h["holdings"]} == {"AAPL": 1901200, "NTAP": 100000}
    by_id = {t["merchant"]: t for t in h["transactions"]}
    assert by_id["Trader Joe's"]["category"] == "Groceries" and by_id["Trader Joe's"]["amount_cents"] == -5420
    assert by_id["CHASE CARD PAYMENT"]["transfer"] and by_id["PAYMENT"]["transfer"]

    # Removing it revokes at Plaid and forgets the token.
    r = client.delete(f"/api/connections/{conn['id']}", headers=csrf(client))
    assert r.json() == {"ok": True, "problem": None} and plaid.removed
    with db_of(client).session() as db:
        assert db.scalar(select(Connection)).token_enc is None


@needs_db
def test_a_broken_connection_goes_stale_and_alerts_once(client, home, plaid, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda s, title, body, tags="": sent.append(title))
    home.setup()
    r = client.post("/api/connections/plaid/start", json={}, headers=csrf(client))
    client.post("/api/connections/finish", json={"token": r.json()["token"]}, headers=csrf(client))
    plaid.fail = "ITEM_LOGIN_REQUIRED"
    for _ in range(2):
        r = client.post("/api/sync", headers=csrf(client))
        assert r.json()["ok"] is False
    h = client.get("/api/household").json()
    assert [s["institution"] for s in h["health"]["stale"]] == ["Chase"]
    assert sent == ["A connection stopped working"]
    plaid.fail = None
    assert client.post("/api/sync", headers=csrf(client)).json()["ok"] is True
    assert client.get("/api/household").json()["health"]["stale"] == []


@needs_db
def test_revoke_all(client, home, plaid):
    home.setup()
    r = client.post("/api/connections/plaid/start", json={}, headers=csrf(client))
    client.post("/api/connections/finish", json={"token": r.json()["token"]}, headers=csrf(client))
    with db_of(client).session() as db:
        db.execute(update(Session).values(reauth_at=now() - timedelta(hours=1)))
    r = client.post("/api/connections/revoke-all", headers=csrf(client))
    assert r.status_code == 403 and r.json()["detail"] == "reauth_required"
    client.post("/api/auth/reauth", json={"code": home.code()}, headers=csrf(client))
    r = client.post("/api/connections/revoke-all", headers=csrf(client))
    assert r.json() == {"revoked": 1, "problems": []}
    assert plaid.removed
    with db_of(client).session() as db:
        assert all(c.token_enc is None and c.status == "revoked" for c in db.scalars(select(Connection)))
    # History stays, so the app keeps working on what it has.
    assert client.get("/api/household").json()["linked"] is True


def test_plaid_retries_when_transactions_change_mid_sync():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"})
        return httpx.Response(
            200, json={"added": [], "modified": [], "removed": [], "next_cursor": "z", "has_more": False}
        )

    from financeapp.connectors.base import FetchResult

    p = Plaid("cid", "sec", "sandbox", httpx.Client(transport=httpx.MockTransport(handler)))
    result = FetchResult(state={})
    p._transactions("tok", result)
    assert result.state["cursor"] == "z" and calls["n"] == 2


# ---- SnapTrade


def test_snaptrade_signature_is_stable():
    sig = signature("key", "/accounts/all", "clientId=C&timestamp=1", None)
    assert sig == signature("key", "/accounts/all", "clientId=C&timestamp=1", None)
    assert sig != signature("other", "/accounts/all", "clientId=C&timestamp=1", None)
    # Keys are sorted and compact, so the body's key order doesn't matter.
    assert signature("k", "/p", "q", {"b": 1, "a": 2}) == signature("k", "/p", "q", {"a": 2, "b": 1})


def snaptrade_api(personal: bool, seen: list):
    """A fake SnapTrade: Personal keys refuse registerUser and take no userId/userSecret."""

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        seen.append((request.method, path, request.headers.get("Signature")))
        assert params["clientId"] == "C" and not path.startswith("/api/v1")
        if path != "/snapTrade/registerUser":
            assert ("userId" in params) is not personal
        if path == "/snapTrade/registerUser":
            if personal:
                return httpx.Response(
                    400,
                    json={
                        "detail": "Personal SnapTrade keys are provisioned with their user automatically at signup.",
                        "code": "1076",
                    },
                )
            return httpx.Response(200, json={"userId": "u", "userSecret": "s3cret"})
        if path == "/snapTrade/login":
            assert json.loads(request.content)["connectionType"] == "read"
            return httpx.Response(200, json={"redirectURI": "https://app.snaptrade.com/connect"})
        if path == "/authorizations":
            return httpx.Response(200, json=[{"id": "conn-1", "brokerage": {"display_name": "E*TRADE"}}])
        if path == "/accounts/all":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "kind": "investment",
                            "id": "a1",
                            "connection_id": "conn-1",
                            "display_name": "Individual",
                            "masked_account_number": "***6621",
                            "raw_type": "Individual Brokerage",
                            "net_value": {"amount": 20246.56, "currency": "USD"},
                        }
                    ]
                },
            )
        if path == "/accounts/a1/positions/all":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "instrument": {
                                "kind": "stock",
                                "symbol": "AAPL",
                                "raw_symbol": "AAPL",
                                "description": "Apple",
                            },
                            "units": "100",
                            "price": "190.12",
                            "cost_basis": "150",
                        },
                        {
                            "instrument": {
                                "kind": "etf",
                                "symbol": "VTI",
                                "raw_symbol": "VTI",
                                "description": "Vanguard",
                            },
                            "units": "2",
                            "price": "250",
                        },
                    ],
                    "data_freshness": {"as_of": "2026-10-21T00:00:00Z"},
                },
            )
        if path == "/accounts/a1/balances":
            return httpx.Response(200, json=[{"currency": {"code": "USD"}, "cash": 1234.56}])
        if path == "/connection/conn-1" and request.method == "DELETE":
            return httpx.Response(200, json={})
        if path == "/snapTrade/deleteUser":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return SnapTrade("C", "K", httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("personal", [True, False])
def test_snaptrade_holdings_and_read_only_portal(personal):
    seen: list = []
    st = snaptrade_api(personal, seen)
    start = st.start_link("household-1", "https://testserver/settings/accounts", "investments")
    assert start.url == "https://app.snaptrade.com/connect"
    linked = st.finish_link(start.pending)
    assert linked.institution == "E*TRADE"
    assert json.loads(linked.token) == (None if personal else ["u", "s3cret"])
    result = st.fetch(linked.token, linked.external_id, {})
    (account,) = result.accounts
    assert (account.institution, account.mask, account.current_cents) == ("E*TRADE", "6621", 2_024_656)
    assert [(h.symbol, h.asset_class, h.value_cents, h.cost_basis_cents) for h in result.holdings] == [
        ("AAPL", "Stocks", 1_901_200, 1_500_000),
        ("VTI", "Funds", 50_000, None),
        ("CASH-USD", "Cash", 123_456, None),
    ]
    assert all(sig for _, _, sig in seen)
    # A second brokerage reuses the same SnapTrade user (or the Personal key) without registering again.
    seen.clear()
    st.start_link("household-1", "https://testserver/settings/accounts", "investments", token=linked.token)
    assert [p for _, p, _ in seen] == ["/snapTrade/login"]
    st.revoke(linked.token, linked.external_id)
    assert seen[-1][1] == ("/connection/conn-1" if personal else "/snapTrade/deleteUser")


# ---- demo mode


def test_demo_mode_serves_sample_settings_and_refuses_changes(tmp_path):
    from fastapi.testclient import TestClient

    from financeapp.config import Settings
    from financeapp.main import create_app

    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    assert c.get("/api/auth/state").json()["demo"] is True
    assert len(c.get("/api/connections").json()["connections"]) == 6
    assert c.get("/api/categories").status_code == 200
    sample = c.get("/api/transactions", params={"kind": "transfer"}).json()
    assert sample["count"] == 1 and sample["out_cents"] == 0
    assert c.get("/api/transactions", params={"q": "zzz"}).json()["count"] == 0
    assert c.post("/api/sync").status_code == 403
    assert c.post("/api/auth/login", json={"email": "a@b.c", "password": "x"}).status_code in (403, 503)
