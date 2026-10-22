import json
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from financeapp import budget, review
from financeapp.categorize import categorize, merchant_key
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import Account, Category, Connection, MerchantRule, Transaction
from financeapp.transfers import match_transfers
from tests.conftest import csrf, db_of, needs_db
from tests.test_ai import FakeProviders, add_claude, fake  # noqa: F401  (fake is a fixture)

TODAY = date(2026, 10, 21)
ON = date(2026, 10, 10)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(budget, "today", lambda: TODAY)


class Books:
    """A household's accounts and transactions, added straight to the database."""

    def __init__(self, db) -> None:
        self.db = db
        conn = Connection(provider="manual", institution="Bank", state={})
        db.add(conn)
        db.flush()
        self.accounts = {}
        for name, kind in [("Checking", "depository"), ("Savings", "depository"), ("Visa", "credit")]:
            self.accounts[name] = Account(
                connection_id=conn.id,
                external_id=name,
                name=name,
                institution="Bank",
                type=kind,
                subtype="savings" if name == "Savings" else None,
            )
            db.add(self.accounts[name])
        self.accounts["Brokerage"] = Account(
            connection_id=conn.id, external_id="b", name="Brokerage", institution="Fidelity", type="investment"
        )
        db.add(self.accounts["Brokerage"])
        db.flush()
        self.cats = {c.name: c.id for c in db.scalars(select(Category))}

    def add(self, account: str, text: str, dollars: float, code: str | None = None, on: date = ON, **kw):
        t = Transaction(
            account_id=self.accounts[account].id,
            external_id=uuid.uuid4().hex,
            date=on,
            amount_cents=round(dollars * 100),
            merchant=kw.pop("merchant", text),
            description=text,
            provider_category=code,
            source="manual",
            **kw,
        )
        self.db.add(t)
        return t

    def sort(self) -> None:
        self.db.flush()
        match_transfers(self.db, ON - timedelta(days=60))
        categorize(self.db)
        self.db.flush()

    def category(self, t: Transaction) -> str | None:
        return next((n for n, i in self.cats.items() if i == t.category_id), None)


def test_merchant_keys_ignore_store_numbers_and_card_readers():
    assert merchant_key("TRADER JOE'S #552 DURHAM NC") == merchant_key("Trader Joe's #17") == "trader joe s"
    assert merchant_key("SQ *BLUE BOTTLE 0042 OAKLAND CA") == "blue bottle"
    assert merchant_key("POS DEBIT HARRIS TEETER 0311") == "harris teeter"
    assert merchant_key("7-ELEVEN 33012") == "eleven"
    assert merchant_key("Netflix") == "netflix"


@needs_db
def test_brands_then_plaid_then_loose_words(client, home):
    home.setup()
    with db_of(client).session() as db:
        b = Books(db)
        brand = b.add("Visa", "WHOLE FOODS MKT 10234", -80, "GENERAL_MERCHANDISE_SUPERSTORES")
        plaid_wins = b.add("Visa", "WATERFRONT YOGA STUDIO", -40, "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS")
        word_only = b.add("Visa", "LUIGI'S GRILL", -35)
        rough = b.add("Visa", "STATE DMV RENEWAL", -60, "GOVERNMENT_AND_NON_PROFIT_GOVERNMENT_DEPARTMENTS")
        nothing = b.add("Visa", "XQZ HOLDINGS LLC", -25)
        b.sort()
        assert (b.category(brand), brand.category_source) == ("Groceries", "builtin")
        # "water" would have made it a utility; Plaid knows it's a gym.
        assert (b.category(plaid_wins), plaid_wins.category_source) == ("Personal care", "provider")
        assert (b.category(word_only), word_only.category_source) == ("Dining out", "guess")
        assert rough.category_source == "guess"  # Plaid's broadest group: worth a look
        assert (b.category(nothing), nothing.category_source) == ("Uncategorized", "none")


@needs_db
def test_a_rule_covers_every_store_and_refunds_follow_purchases(client, home):
    home.setup()
    with db_of(client).session() as db:
        b = Books(db)
        first = b.add("Visa", "MOM AND POP SHOP #552", -50)
        other_store = b.add("Visa", "MOM AND POP SHOP #17", -20)
        refund = b.add("Visa", "MOM AND POP SHOP #17 RETURN", 20)
        stray = b.add("Checking", "MERCHANDISE RETURN XQZ", 15)
        gift = b.add("Checking", "MOBILE DEPOSIT", 200)
        b.sort()
        assert b.category(stray) == "Uncategorized"  # a refund nothing matched comes off spending, isn't income
        assert b.category(gift) == "Other income"
        changed = review.learn(db, first, db.get_one(Category, b.cats["Home"]), None)
        assert changed >= 2
        assert b.category(other_store) == b.category(first) == "Home"
        assert b.category(refund) == "Home"
        assert db.scalar(select(MerchantRule)).pattern == "mom and pop shop 552"


@needs_db
def test_transfers_card_payments_and_investments_never_count_as_spending(client, home):
    home.setup(name="John Tester")
    with db_of(client).session() as db:
        b = Books(db)
        b.add("Checking", "ACME CORP PAYROLL", 4000, "INCOME_WAGES")
        b.add("Visa", "WHOLE FOODS", -150)
        b.add("Checking", "Duke Energy", -100)
        # A card payment (both sides), a move to savings (both sides), money into a brokerage (both sides) and
        # one-sided moves: a card that isn't linked, a brokerage contribution, and a Zelle to the household itself.
        b.add("Checking", "CHASE CREDIT CRD AUTOPAY", -900)
        b.add("Visa", "PAYMENT THANK YOU", 900)
        b.add("Checking", "Online transfer to savings", -500)
        b.add("Savings", "Online transfer from checking", 500)
        b.add("Checking", "TRANSFER TO BROKERAGE", -700)
        b.add("Brokerage", "DEPOSIT", 700)
        b.add("Checking", "AMEX EPAYMENT ACH PMT", -300)
        b.add("Checking", "VANGUARD BUY INVESTMENT ACH", -250)
        b.add("Checking", "Zelle payment to John Tester", -50, "TRANSFER_OUT_ACCOUNT_TRANSFER")
        b.sort()

        accounts = budget.counted_accounts(db)
        ledger = budget.Ledger(db, accounts, date(2026, 10, 1))
        f = budget.flows(ledger, date(2026, 10, 1), None)
        assert sum(f["spent"].values()) == 25000  # groceries and power, nothing else

        s = review.state(db)
        kept = s["kept_out"]
        assert kept["card_cents"] == 120000 and kept["invest_cents"] == 95000
        assert kept["own_cents"] == 55000
        assert s["groups"] == [] and s["score"] == 100


@needs_db
def test_zelle_to_someone_else_is_asked_about_and_remembered_by_person(client, home):
    home.setup()
    with db_of(client).session() as db:
        b = Books(db)
        b.add("Checking", "ACME CORP PAYROLL", 4000, "INCOME_WAGES")
        sitter = b.add("Checking", "Zelle payment to MARIA LOPEZ conf 4410", -180, "TRANSFER_OUT", merchant="Zelle")
        b.add("Checking", "Zelle payment to SAM PARK conf 99", -40, "TRANSFER_OUT", merchant="Zelle")
        b.add("Checking", "LUIGI'S GRILL", -35)
        b.sort()
        assert b.category(sitter) == "Transfer"  # left out of spending until someone says otherwise
        s = review.state(db)
        by_reason = {g["reason"]: g for g in s["groups"]}
        assert {g["person"] for g in s["groups"] if g["reason"] == "sent"} == {"Maria Lopez", "Sam Park"}
        assert by_reason["guess"]["merchant"] == "LUIGI'S GRILL"
        assert 0 < s["score"] < 100 and s["items"] == 3
        maria = next(g for g in s["groups"] if g["person"] == "Maria Lopez")
        sam = next(g for g in s["groups"] if g["person"] == "Sam Park")

    r = client.post(
        "/api/review/apply",
        json={"ids": [int(i) for i in maria["ids"]], "category_id": b.cats["Childcare & education"]},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    assert "Maria Lopez" not in {g["person"] for g in r.json()["groups"]}
    # Sam is the household's own other account: a transfer, and his name joins the household's own.
    r = client.post(
        "/api/review/apply",
        json={"ids": [int(i) for i in sam["ids"]], "category_id": b.cats["Transfer"]},
        headers=csrf(client),
    )
    assert [g["reason"] for g in r.json()["groups"]] == ["guess"]
    with db_of(client).session() as db:
        assert "sam park" in budget.preferences(db)["own_senders"]

    with db_of(client).session() as db:
        b2 = Books.__new__(Books)
        b2.db, b2.cats = db, b.cats
        b2.accounts = {a.name: a for a in db.scalars(select(Account))}
        later = b2.add("Checking", "Zelle payment to MARIA LOPEZ conf 5521", -180, "TRANSFER_OUT", merchant="Zelle")
        stranger = b2.add("Checking", "Zelle payment to PAT DOE conf 1", -10, "TRANSFER_OUT", merchant="Zelle")
        b2.sort()
        assert b2.category(later) == "Childcare & education" and later.category_source == "rule"
        assert b2.category(stranger) == "Transfer"  # a rule for Maria isn't a rule for every Zelle
        assert db.scalar(select(MerchantRule.pattern)) == "person maria lopez"
        ledger = budget.Ledger(db, budget.counted_accounts(db), date(2026, 10, 1))
        assert sum(budget.flows(ledger, date(2026, 10, 1), None)["spent"].values()) == 18000 * 2 + 3500


@needs_db
def test_fixing_a_zelle_payment_makes_a_rule_for_the_person_not_for_zelle(client, home):
    home.setup()
    with db_of(client).session() as db:
        b = Books(db)
        one = b.add("Checking", "Zelle payment to MARIA LOPEZ conf 4410", -180, merchant="Zelle")
        two = b.add("Checking", "Zelle payment to SAM PARK conf 99", -40, merchant="Zelle")
        b.sort()
        ids = one.id, two.id
    r = client.patch(f"/api/transactions/{ids[0]}", json={"category_id": b.cats["Home"]}, headers=csrf(client))
    assert r.status_code == 200
    with db_of(client).session() as db:
        assert db.scalar(select(MerchantRule.pattern)) == "person maria lopez"
        assert db.get_one(Transaction, ids[1]).category_id != b.cats["Home"]


@needs_db
def test_review_api_and_home_count(client, home):
    home.setup()
    with db_of(client).session() as db:
        b = Books(db)
        b.add("Visa", "XQZ HOLDINGS LLC", -25)
        b.add("Visa", "XQZ HOLDINGS LLC", -30, on=ON - timedelta(days=30))
        b.sort()
    s = client.get("/api/review").json()
    assert s["items"] == 2 and len(s["groups"]) == 1 and s["groups"][0]["reason"] == "no_category"
    assert client.get("/api/household").json()["spending"]["needs_review"] == 2
    r = client.post("/api/review/apply", json={"ids": [999999], "category_id": b.cats["Home"]}, headers=csrf(client))
    assert r.status_code == 404


@needs_db
def test_ai_suggestions_send_merchants_not_people_and_save_nothing(client, home, fake):  # noqa: F811
    home.setup()
    with db_of(client).session() as db:
        b = Books(db)
        b.add("Visa", "XQZ HOLDINGS LLC 4111111111111111", -25)
        b.add("Visa", "BRIGHT HORIZONS 0091", -310)
        b.add("Checking", "Zelle payment to MARIA LOPEZ conf 4410", -180, "TRANSFER_OUT", merchant="Zelle")
        b.sort()
    offer = client.get("/api/review/ai").json()
    assert offer["available"] is False
    pid = add_claude(client)["providers"][0]["id"]
    client.put(
        "/api/ai/settings",
        json={"routes": {"categorize": {"provider_id": pid, "model": "claude-opus-5-5"}}},
        headers=csrf(client),
    )
    offer = client.get("/api/review/ai").json()
    assert offer["available"] and offer["merchants"] == 2
    for private in ["MARIA", "Maria", "4111111111111111"]:
        assert private not in offer["preview"]
    sent = json.loads(offer["preview"])["merchants"]
    bright = next(m["id"] for m in sent if "BRIGHT" in m["merchant"])
    fake.answer = json.dumps(
        {
            "suggestions": [
                {"id": bright, "category": "childcare & education", "reason": "A daycare chain."},
                {"id": 99, "category": "Groceries", "reason": "Not sent."},
                {"id": 1 if bright == 2 else 2, "category": "Paycheck", "reason": "Money out is never pay."},
            ]
        }
    )
    r = client.post("/api/review/ai", headers=csrf(client))
    assert r.status_code == 200, r.text
    picks = r.json()["suggestions"]
    assert [p["category"] for p in picks.values()] == ["Childcare & education"]
    assert client.get("/api/review").json()["items"] == 3  # nothing saved


def test_demo_review_is_served_and_read_only(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    s = c.get("/api/review").json()
    assert s["groups"] and 0 < s["score"] < 100
    assert c.post("/api/review/apply", json={"ids": [1], "category_id": 1}).status_code == 403
    assert c.post("/api/review/ai").status_code == 403
