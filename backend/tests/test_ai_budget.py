import json
from datetime import date

import pytest

from financeapp import budget
from financeapp.ai import budgeting
from financeapp.models import Paycheck
from tests.conftest import csrf, db_of, needs_db
from tests.test_ai import FakeProviders, add_claude, fake  # noqa: F401  (fake is a fixture)
from tests.test_budget import TODAY, history

ANSWER = {
    "summary": "Groceries come down a little and rent stays; that frees about $50 a month for savings.",
    "lines": [
        {"category": "Rent & mortgage", "limit": 2000, "reason": "Rent was $2,000 every month."},
        {"category": "groceries", "limit": 475.4, "reason": "Median month was $500; trimmed toward your best months."},
        {"category": "Yachts", "limit": 9000, "reason": "Not a category."},
        {"category": "Groceries", "limit": 1, "reason": "A second go at the same line is ignored."},
    ],
}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(budget, "today", lambda: TODAY)
    monkeypatch.setattr(budgeting, "today", lambda: TODAY)


def ai_route(client, task="plan_budget"):
    pid = add_claude(client)["providers"][0]["id"]
    client.put(
        "/api/ai/settings",
        json={"routes": {task: {"provider_id": pid, "model": "claude-opus-5-5"}}},
        headers=csrf(client),
    )


@needs_db
def test_the_offer_shows_what_goes_and_sends_nothing(client, home, fake):  # noqa: F811
    home.setup()
    history(client)
    with db_of(client).session() as db:
        db.add(
            Paycheck(
                date=date(2026, 9, 15),
                gross_cents=450000,
                taxes_cents=112900,
                savings_cents=60000,
                net_cents=277100,
                employer_match_cents=0,
            )
        )
    offer = client.get("/api/budget/ai").json()
    assert offer["available"] is False and "isn't set up" in offer["problem"]
    ai_route(client)
    calls = len(fake.sent)
    offer = client.get("/api/budget/ai").json()
    assert offer["available"] and offer["model"] == "claude-opus-5-5" and offer["months"] == 5 and offer["stubs"] == 1
    preview = offer["preview"]
    assert '"Groceries"' in preview and "monthly_take_home" in preview
    for private in ["Whole Foods", "Landlord", "Trader Joe", "Shubham", "me@example.com"]:
        assert private not in preview
    assert len(fake.sent) == calls  # looking costs nothing


@needs_db
def test_suggestions_come_back_line_by_line_and_save_only_what_is_picked(client, home, fake):  # noqa: F811
    home.setup()
    history(client)
    ai_route(client)
    fake.answer = json.dumps(ANSWER)
    r = client.post("/api/budget/ai", headers=csrf(client))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["model"] == "claude-opus-5-5" and s["cost"] > 0 and "frees" in s["summary"]
    assert [line["category"] for line in s["lines"]] == ["Rent & mortgage", "Groceries"]  # unknown and repeats dropped
    groceries = s["lines"][1]
    assert (
        groceries["suggested_cents"] == 47500
        and groceries["current_cents"] == 55000
        and groceries["typical_cents"] == 50000
    )
    sent = json.loads(fake.sent[-1].content)
    assert "Whole Foods" not in json.dumps(sent) and sent["output_config"]["format"]["type"] == "json_schema"

    # Nothing changed until the person picks; then it's a version marked as AI's.
    assert client.get("/api/budget").json()["saved"] is False
    lines = [{"category_id": groceries["category_id"], "limit_cents": groceries["suggested_cents"]}]
    r = client.put("/api/budget", json={"lines": lines, "source": "ai"}, headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["versions"][0]["source"] == "ai"
    assert any(e["text"] == "Budget suggested by AI" for e in client.get("/api/auth/audit").json())


@needs_db
def test_hand_edits_are_flagged_for_the_ai(client, home, fake):  # noqa: F811
    home.setup()
    history(client)
    groceries = next(line for line in client.get("/api/budget").json()["lines"] if line["category"] == "Groceries")
    client.put(
        "/api/budget",
        json={"lines": [{"category_id": groceries["category_id"], "limit_cents": 30000}]},
        headers=csrf(client),
    )
    ai_route(client)
    with db_of(client).session() as db:
        f = budgeting.facts(db)
    row = next(b for b in f["budget"] if b["category"] == "Groceries")
    assert row == {"category": "Groceries", "limit": 300, "app_draft": 550, "set_by_household": True}
    rent = next(b for b in f["budget"] if b["category"] == "Rent & mortgage")
    assert rent["limit"] is None and rent["set_by_household"]  # taken out of the budget by hand


@needs_db
def test_no_history_no_ai(client, home, fake):  # noqa: F811
    home.setup()
    ai_route(client)
    assert client.get("/api/budget/ai").json()["problem"].startswith("There isn't a full month")
    assert client.post("/api/budget/ai", headers=csrf(client)).status_code == 422
