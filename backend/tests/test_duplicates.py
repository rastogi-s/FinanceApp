"""One login, one connection: reconnecting signs in again on the same Plaid Item, linking a login that's already
linked adds nothing, and two links of the same login (made before this was fixed) are folded into one."""

import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from financeapp import crypto, sync
from financeapp.connectors.plaid import Plaid
from financeapp.models import (
    Account,
    Balance,
    Category,
    Connection,
    Goal,
    GoalFunding,
    HoldingSnapshot,
    InvestmentTransaction,
    Transaction,
)
from tests.conftest import csrf, db_of, needs_db

DAY = date.today() - timedelta(days=5)
ACCOUNTS = [  # (account id suffix, name, type, mask, balance)
    ("sav", "Premium Savings -8454", "depository", "8454", 43091),
    ("brk", "Individual Brokerage -2600", "investment", "2600", 239810),
    ("plan", "Stock Plan (NTAP) -0439", "investment", "0439", 31076),
]


class FakePlaid:
    """E*TRADE behind Plaid. Every fresh link makes a new Item, and Plaid gives the same accounts and transactions
    new ids in each Item (that's what made the duplicates)."""

    def __init__(self) -> None:
        self.items = 0
        self.calls: list[tuple[str, dict]] = []
        self.login_required: set[str] = set()
        self.removed: list[str] = []
        self.not_ready: set[str] = set()  # Items whose history Plaid is still gathering
        self.masks = [a[3] for a in ACCOUNTS]

    def paths(self) -> list[str]:
        return [p for p, _ in self.calls]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content)
        self.calls.append((path, body))
        token = body.get("access_token", "")
        item = token.removeprefix("access-")
        if token and item in self.login_required and path != "/item/remove":
            return httpx.Response(400, json={"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "sign in again"})
        if item in self.not_ready and path in ("/transactions/sync", "/investments/transactions/get"):
            return httpx.Response(400, json={"error_code": "PRODUCT_NOT_READY", "error_message": "not yet"})
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-x", "hosted_link_url": "https://hosted.plaid.com/x"})
        if path == "/link/token/get":
            if self.calls and any(p == "/link/token/create" and "access_token" in b for p, b in self.calls[-3:]):
                return httpx.Response(200, json={"link_sessions": [{"finished_at": "2026-10-25T02:00:00Z"}]})
            accounts = [
                {"id": "?", "name": name, "type": kind, "mask": mask}
                for (_s, name, kind, _m, _b), mask in zip(ACCOUNTS, self.masks, strict=True)
            ]
            result = {
                "public_token": "public-x",
                "institution": {"name": "E*TRADE from Morgan Stanley", "institution_id": "ins_129473"},
                "accounts": accounts,
            }
            return httpx.Response(200, json={"link_sessions": [{"results": {"item_add_results": [result]}}]})
        if path == "/item/public_token/exchange":
            self.items += 1
            return httpx.Response(
                200, json={"access_token": f"access-item{self.items}", "item_id": f"item{self.items}"}
            )
        if path == "/accounts/get":
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "account_id": f"{item}-{s}",
                            "name": name,
                            "type": kind,
                            "mask": mask,
                            "balances": {"current": balance},
                        }
                        for (s, name, kind, _m, balance), mask in zip(ACCOUNTS, self.masks, strict=True)
                    ]
                },
            )
        if path == "/investments/holdings/get":
            return httpx.Response(
                200,
                json={
                    "holdings": [
                        {"account_id": f"{item}-brk", "security_id": "s1", "quantity": 10, "institution_value": 2000},
                        {"account_id": f"{item}-plan", "security_id": "s2", "quantity": 5, "institution_value": 500},
                    ],
                    "securities": [
                        {"security_id": "s1", "ticker_symbol": "VTI", "name": "Vanguard Total", "type": "etf"},
                        {"security_id": "s2", "ticker_symbol": "NTAP", "name": "NetApp", "type": "equity"},
                    ],
                },
            )
        if path == "/investments/transactions/get":
            return httpx.Response(
                200,
                json={
                    "investment_transactions": [
                        {
                            "account_id": f"{item}-brk",
                            "investment_transaction_id": f"{item}-div",
                            "date": DAY.isoformat(),
                            "type": "cash",
                            "subtype": "dividend",
                            "name": "VTI dividend",
                            "amount": -12.5,
                            "security_id": "s1",
                        }
                    ],
                    "securities": [{"security_id": "s1", "ticker_symbol": "VTI"}],
                    "total_investment_transactions": 1,
                },
            )
        if path == "/transactions/sync":
            if body.get("cursor"):
                return httpx.Response(
                    200, json={"added": [], "modified": [], "removed": [], "next_cursor": body["cursor"]}
                )
            added = [
                {
                    "account_id": f"{item}-sav",
                    "transaction_id": f"{item}-t{i}",
                    "date": (DAY - timedelta(days=i)).isoformat(),
                    "amount": amount,
                    "name": name,
                    "merchant_name": name,
                }
                for i, (amount, name) in enumerate([(-35.17, "INTEREST PAYMENT"), (2000, "TRANSFER TO BANK")])
            ]
            return httpx.Response(
                200,
                json={"added": added, "modified": [], "removed": [], "next_cursor": f"{item}-c", "has_more": False},
            )
        if path == "/item/remove":
            self.removed.append(item)
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


def link(client, connection_id: int | None = None) -> dict:
    r = client.post(
        "/api/connections/plaid/start",
        json={"kind": "investments", "connection_id": connection_id},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/connections/finish", json={"token": r.json()["token"]}, headers=csrf(client))
    assert r.status_code == 200, r.text
    return r.json()


def counts(client) -> dict:
    with db_of(client).session() as db:
        return {
            "connections": db.scalar(select(func.count()).select_from(Connection)),
            "accounts": db.scalar(select(func.count()).select_from(Account)),
            "transactions": db.scalar(select(func.count()).select_from(Transaction)),
            "activity": db.scalar(select(func.count()).select_from(InvestmentTransaction)),
        }


def net_worth(client) -> int:
    return client.get("/api/household").json()["net_worth"]["cents"]


ONCE = (43091 + 239810 + 31076) * 100


@needs_db
def test_reconnect_signs_in_again_on_the_same_link(client, home, plaid):
    home.setup()
    first = link(client)["connection"]
    plaid.login_required.add("item1")
    client.post("/api/sync", headers=csrf(client))
    assert client.get("/api/connections").json()["connections"][0]["status"] == "stale"

    plaid.login_required.clear()  # what signing in at E*TRADE again does
    again = link(client, first["id"])
    create = [b for p, b in plaid.calls if p == "/link/token/create"][-1]
    assert create["access_token"] == "access-item1" and "products" not in create  # Plaid update mode
    assert plaid.paths().count("/item/public_token/exchange") == 1
    assert plaid.items == 1  # no second Item used up
    assert again["connection"]["id"] == first["id"] and again["connection"]["status"] == "active"
    assert counts(client) == {"connections": 1, "accounts": 3, "transactions": 2, "activity": 1}
    assert net_worth(client) == ONCE


@needs_db
def test_linking_a_login_thats_already_linked_adds_nothing(client, home, plaid):
    home.setup()
    link(client)
    r = link(client)
    assert r["already_linked"] is True
    assert plaid.items == 1 and plaid.paths().count("/item/public_token/exchange") == 1
    assert counts(client)["connections"] == 1 and net_worth(client) == ONCE


@needs_db
def test_a_second_login_at_the_same_bank_is_its_own_connection(client, home, plaid):
    home.setup()
    link(client)
    plaid.masks = ["1111", "2222", "3333"]  # someone else's E*TRADE
    r = link(client)
    assert not r["already_linked"]
    assert counts(client)["connections"] == 2 and counts(client)["accounts"] == 6


def seed_settings(client) -> dict:
    """What the person set up on the original accounts: a category by hand, a renamed account, goal money."""
    with db_of(client).session() as db:
        sav = db.scalar(select(Account).where(Account.mask == "8454"))
        sav.name = "E*TRADE savings"
        goal = Goal(kind="saving", name="House", icon="home", color="blue")
        db.add(goal)
        db.flush()
        db.add(GoalFunding(goal_id=goal.id, account_id=sav.id, cents=10_000_00))
        t = db.scalar(select(Transaction).where(Transaction.merchant == "INTEREST PAYMENT"))
        t.category_id = db.scalar(select(Category.id).where(Category.name == "Groceries"))
        t.category_source = "user"
        db.add(Balance(account_id=sav.id, date=DAY - timedelta(days=30), current_cents=40_000_00))
        return {"account": sav.id, "goal": goal.id, "txn": t.id}


def check_folded(client, seeded: dict) -> None:
    assert counts(client) == {"connections": 1, "accounts": 3, "transactions": 2, "activity": 1}
    assert net_worth(client) == ONCE
    with db_of(client).session() as db:
        sav = db.get_one(Account, seeded["account"])  # the original account survives, with its settings
        assert sav.name == "E*TRADE savings"
        assert db.get(GoalFunding, (seeded["goal"], sav.id)).cents == 10_000_00
        t = db.get_one(Transaction, seeded["txn"])
        assert t.category_source == "user" and t.account_id == sav.id
        assert db.get(Balance, (sav.id, DAY - timedelta(days=30))) is not None  # history kept
        today = db.scalar(select(func.max(HoldingSnapshot.date)))
        assert db.scalar(select(func.count()).select_from(HoldingSnapshot).where(HoldingSnapshot.date == today)) == 2


@needs_db
def test_duplicates_from_before_the_fix_are_folded_into_the_original(client, home, plaid):
    """The bug as it happened: both links work and both sync, so every E*TRADE account counted twice."""
    home.setup()
    link(client)
    seeded = seed_settings(client)
    # A second, working link of the same login, the way the old Reconnect made one.
    with db_of(client).session() as db:
        dup = Connection(
            provider="plaid",
            institution="E*TRADE from Morgan Stanley",
            external_id="item2",
            token_enc=crypto.encrypt("access-item2", "connection:plaid"),
            state={"institution": "E*TRADE from Morgan Stanley"},
        )
        db.add(dup)
        db.flush()
        result = sync.connector_for("plaid", None).fetch("access-item2", "item2", dup.state)
        sync.save_fetch(db, dup, result, sync.today())
    assert counts(client)["accounts"] == 6

    assert client.post("/api/sync", headers=csrf(client)).json()["ok"] is True
    check_folded(client, seeded)
    with db_of(client).session() as db:
        kept = db.scalar(select(Connection))
        assert kept.external_id == "item1"  # the original link was healthy, so it keeps syncing
    assert plaid.removed == ["item2"]
    client.post("/api/sync", headers=csrf(client))  # and it stays that way
    check_folded(client, seeded)


@needs_db
def test_linking_again_after_the_old_link_broke_folds_it_into_one(client, home, plaid):
    """A stale link fixed by linking from scratch: the new Item takes over, the old accounts keep everything."""
    home.setup()
    link(client)
    seeded = seed_settings(client)
    plaid.login_required.add("item1")
    client.post("/api/sync", headers=csrf(client))

    r = link(client)  # a fresh link, not a reconnect
    assert plaid.items == 2
    assert r["connection"]["status"] == "active"
    check_folded(client, seeded)
    with db_of(client).session() as db:
        kept = db.scalar(select(Connection))
        assert kept.external_id == "item2" and kept.state["institution_id"] == "ins_129473"
        assert crypto.decrypt(kept.token_enc, "connection:plaid") == "access-item2"
        assert {a.external_id for a in db.scalars(select(Account))} == {"item2-sav", "item2-brk", "item2-plan"}
        assert not db.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.external_id.startswith("was:"))
        )
    assert plaid.removed == ["item1"]
    client.post("/api/sync", headers=csrf(client))
    check_folded(client, seeded)


@needs_db
def test_history_from_the_new_link_finds_the_transactions_it_already_has(client, home, plaid):
    """When the new Item's history arrives after the fold, it takes over the old rows instead of adding copies."""
    home.setup()
    link(client)
    seeded = seed_settings(client)
    plaid.login_required.add("item1")
    client.post("/api/sync", headers=csrf(client))
    plaid.not_ready.add("item2")
    link(client)
    with db_of(client).session() as db:
        assert db.scalar(select(func.count()).select_from(Connection)) == 1
        waiting = db.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.external_id.startswith("was:"))
        )
        assert waiting == 2

    plaid.not_ready.clear()
    client.post("/api/sync", headers=csrf(client))
    check_folded(client, seeded)
    with db_of(client).session() as db:
        assert all(t.external_id.startswith("item2-") for t in db.scalars(select(Transaction)))
        assert all(t.external_id.startswith("item2-") for t in db.scalars(select(InvestmentTransaction)))
