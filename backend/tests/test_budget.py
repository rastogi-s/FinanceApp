import uuid
from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from financeapp import budget
from financeapp.config import Settings
from financeapp.main import create_app
from financeapp.models import Account, Base, Category, Connection, Transaction
from tests.conftest import csrf, db_of, needs_db

TODAY = date(2026, 10, 21)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(budget, "today", lambda: TODAY)


def add_transactions(client, rows: list[tuple[str, str, float, str | None]], transfer: bool = False) -> None:
    """rows: (date, merchant, dollars, category name). Negative dollars = money out."""
    with db_of(client).session() as db:
        account = db.scalar(select(Account).where(Account.external_id == "checking"))
        if account is None:
            conn = Connection(provider="manual", institution="Bank", state={})
            db.add(conn)
            db.flush()
            account = Account(
                connection_id=conn.id, external_id="checking", name="Checking", institution="Bank", type="depository"
            )
            db.add(account)
            db.flush()
        categories = {c.name: c.id for c in db.scalars(select(Category))}
        for day, merchant, dollars, category in rows:
            db.add(
                Transaction(
                    account_id=account.id,
                    external_id=uuid.uuid4().hex,
                    date=date.fromisoformat(day),
                    amount_cents=round(dollars * 100),
                    merchant=merchant,
                    description=merchant,
                    category_id=categories[category] if category else None,
                    category_source="user" if category else "none",
                    transfer_group_id=uuid.uuid4() if transfer else None,
                    source="manual",
                )
            )


def history(client) -> None:
    """Five full months (May to September) plus a partial April and October so far."""
    rows = [("2026-04-10", "Whole Foods", -999, "Groceries")]  # April is partial: not in the draft
    for month, groceries in zip(range(5, 10), [400, 500, 600, 450, 550], strict=True):
        rows += [
            (f"2026-{month:02}-03", "Landlord", -2000, "Rent & mortgage"),
            (f"2026-{month:02}-12", "Whole Foods", -groceries, "Groceries"),
        ]
    rows += [
        ("2026-06-20", "Netflix", -15, "Subscriptions"),  # once in five months: not a monthly bill
        ("2026-10-03", "Landlord", -2000, "Rent & mortgage"),
        ("2026-10-08", "Trader Joe's", -300, "Groceries"),
        ("2026-10-09", "Sweetgreen", -50, "Dining out"),
        ("2026-10-10", "Mystery shop", -20, None),
    ]
    add_transactions(client, rows)
    add_transactions(client, [("2026-10-11", "To savings", -5000, "Groceries")], transfer=True)  # a transfer


def line(state: dict, name: str) -> dict:
    return next(x for x in state["lines"] if x["category"] == name)


@needs_db
def test_draft_from_recent_months(client, home):
    home.setup()
    assert client.get("/api/budget").json()["lines"] == []
    history(client)
    b = client.get("/api/budget").json()
    assert b["saved"] is False and b["months_used"] == 5 and b["rough"] is False
    assert [x["category"] for x in b["lines"]] == ["Rent & mortgage", "Groceries"]
    # Groceries: median $500 + 10% = $550; rent: $2,000 every month.
    assert (line(b, "Groceries")["limit_cents"], line(b, "Groceries")["typical_cents"]) == (55_000, 50_000)
    assert line(b, "Groceries")["spent_cents"] == 30_000  # the transfer isn't spending
    assert line(b, "Rent & mortgage")["limit_cents"] == 200_000
    assert {u["category"]: u["spent_cents"] for u in b["unbudgeted"]} == {"Dining out": 5_000, "Uncategorized": 2_000}
    # $550 - $300 groceries - $50 dining and $20 uncategorized outside the budget = $180, 11 days left.
    assert b["safe_to_spend"] == {"cents": 18_000, "days_left": 11}
    # Rent is essential (every bill is), groceries too: 6 months of $2,550.
    assert b["emergency"]["target_cents"] == 6 * 255_000

    h = client.get("/api/household").json()
    assert [x["category"] for x in h["budgets_closest"]] == ["Groceries"]


@needs_db
def test_save_edit_and_restore(client, home):
    home.setup()
    history(client)
    b = client.post("/api/budget/draft", headers=csrf(client)).json()
    assert b["saved"] is True and b["version"]["source"] == "draft"
    lines = [{"category_id": x["category_id"], "limit_cents": x["limit_cents"]} for x in b["lines"]]
    groceries = line(b, "Groceries")["category_id"]
    lines = [{**x, "limit_cents": 60_000} if x["category_id"] == groceries else x for x in lines]
    dining = next(c["id"] for c in b["categories"] if c["name"] == "Dining out")
    edited = client.put(
        "/api/budget", json={"lines": [*lines, {"category_id": dining, "limit_cents": 10_000}]}, headers=csrf(client)
    )
    assert edited.status_code == 200, edited.text
    b = edited.json()
    assert line(b, "Groceries")["limit_cents"] == 60_000 and line(b, "Dining out")["limit_cents"] == 10_000
    assert b["unbudgeted"] == [
        {"category_id": None, "category": "Uncategorized", "kind": "other", "spent_cents": 2_000}
    ]
    first = b["versions"][-1]["id"]
    b = client.post(f"/api/budget/versions/{first}/restore", headers=csrf(client)).json()
    assert line(b, "Groceries")["limit_cents"] == 55_000
    assert [v["source"] for v in b["versions"]] == ["restore", "edit", "draft"]

    income = next(c["id"] for c in client.get("/api/categories").json() if c["name"] == "Paycheck")
    bad = client.put("/api/budget", json={"lines": [{"category_id": income, "limit_cents": 100}]}, headers=csrf(client))
    assert bad.status_code == 422
    twice = [{"category_id": groceries, "limit_cents": 1}] * 2
    assert client.put("/api/budget", json={"lines": twice}, headers=csrf(client)).status_code == 422


@needs_db
def test_savings_rate_from_pay_stubs(client, home):
    home.setup()
    history(client)
    add_transactions(
        client,
        [("2026-09-15", "Acme Payroll", 3300, "Paycheck"), ("2026-09-30", "Interest", 10, "Interest & dividends")],
    )
    # No stub yet: take-home basis. $3,310 in, $2,550 spent.
    rate = client.get("/api/budget").json()["savings_rate"]
    assert rate["basis"] == "take_home" and rate["saved_cents"] == 76_000 and rate["month"] == "2026-09-01"

    stub = {
        "date": "2026-09-14",
        "gross_cents": 500_000,
        "taxes_cents": 100_000,
        "savings_cents": 50_000,
        "net_cents": 330_000,
        "employer_match_cents": 25_000,
    }
    over = {**stub, "net_cents": 400_000}
    assert client.post("/api/paychecks", json=over, headers=csrf(client)).status_code == 422
    b = client.post("/api/paychecks", json=stub, headers=csrf(client)).json()
    rate = b["savings_rate"]
    # $5,000 gross + $10 interest (the deposit is the stub's, not counted again) - $1,000 taxes - $200 other
    # deductions - $2,550 spending = $1,260 saved, 25.1% of $5,010.
    assert rate["basis"] == "gross" and rate["income_cents"] == 501_000 and rate["saved_cents"] == 126_000
    assert rate["percent"] == 25.1
    assert b["paychecks"][0]["deposit"] == {"date": "2026-09-15", "merchant": "Acme Payroll"}
    assert client.get("/api/household").json()["savings_rate"] == {
        "percent": 25.1,
        "month": "2026-09-01",
        "basis": "gross",
    }

    b = client.patch("/api/budget/settings", json={"include_match": True}, headers=csrf(client)).json()
    assert b["savings_rate"]["saved_cents"] == 151_000 and b["savings_rate"]["percent"] == 28.7

    b = client.delete(f"/api/paychecks/{b['paychecks'][0]['id']}", headers=csrf(client)).json()
    assert b["paychecks"] == [] and b["savings_rate"]["basis"] == "take_home"


@needs_db
def test_emergency_fund_settings(client, home):
    home.setup()
    history(client)
    b = client.patch("/api/budget/settings", json={"emergency_months": 3}, headers=csrf(client)).json()
    assert b["emergency"]["months"] == 3 and b["emergency"]["target_cents"] == 3 * 255_000
    groceries = line(b, "Groceries")["category_id"]
    b = client.patch(f"/api/categories/{groceries}", json={"essential": False}, headers=csrf(client)).json()
    assert b["emergency"]["monthly_cents"] == 200_000
    assert client.patch("/api/budget/settings", json={"emergency_months": 0}, headers=csrf(client)).status_code == 422


def test_demo_budget_is_read_only(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    c = TestClient(create_app(Settings(static_dir=tmp_path, allowed_hosts="testserver", demo_mode=True)))
    b = c.get("/api/budget").json()
    assert b["saved"] is True and b["savings_rate"]["percent"] == 31.4
    assert c.get("/api/household").json()["savings_rate"]["percent"] == 31.4
    assert c.put("/api/budget", json={"lines": []}).status_code == 403
    assert c.post("/api/paychecks", json={}).status_code == 403


@needs_db
def test_migrations_match_the_models(client):
    """The tables the migrations make are the ones the code expects (nothing missing from a migration)."""
    engine = create_engine(str(db_of(client).engine.url.render_as_string(hide_password=False)))
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
