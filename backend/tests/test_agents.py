import json
import re
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from financeapp import brief, budget, household, notify
from financeapp.models import Account, AgentKey, Balance, Brief, Connection
from tests.conftest import csrf, db_of, needs_db
from tests.test_ai import FakeProviders, add_claude, fake  # noqa: F401  (fake is a fixture)
from tests.test_budget import TODAY, add_transactions, history

MORNING = datetime(2026, 10, 21, 11, 30, tzinfo=UTC)  # 7:30 AM in New York


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(budget, "today", lambda: TODAY)
    monkeypatch.setattr(household, "today", lambda: TODAY)


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    out: list[dict] = []

    def deliver(settings, title, body, tags="lock", priority="high", click=None):
        out.append({"title": title, "body": body, "click": click})
        return "ntfy"

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(notify, "configured", lambda settings: True)
    return out


def money_in_the_bank(client) -> None:
    history(client)
    with db_of(client).session() as db:
        a = db.scalar(select(Account).where(Account.external_id == "checking"))
        a.name, a.institution, a.mask = "Total Checking", "Chase", "4417"
        db.add(Balance(account_id=a.id, date=date(2026, 10, 20), current_cents=812_345))
        db.add(Balance(account_id=a.id, date=TODAY, current_cents=814_580))


def new_key(client, label="Claude Code") -> str:
    r = client.post("/api/agents", json={"label": label}, headers=csrf(client))
    assert r.status_code == 200, r.text
    return r.json()["token"]


def rpc(client, method, params=None, key=None, id_=1, **headers):
    body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
    if id_ is not None:
        body["id"] = id_
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return client.post("/mcp", json=body, headers=headers)


def call(client, key, tool, arguments=None) -> dict:
    r = rpc(client, "tools/call", {"name": tool, "arguments": arguments or {}}, key=key)
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ---- the MCP server


@needs_db
def test_agent_keys_are_shown_once_and_stored_as_hashes(client, home):
    home.setup()
    token = new_key(client)
    assert token.startswith("fa_agent_")
    listing = client.get("/api/agents").json()
    [k] = listing["keys"]
    assert k["label"] == "Claude Code" and k["hint"] == token[-4:] and "token" not in k
    assert "Find transactions" in listing["tools"]
    with db_of(client).session() as db:
        stored = db.scalar(select(AgentKey))
        assert token.encode() not in stored.token_hash and len(stored.token_hash) == 32
    audit = client.get("/api/auth/audit").json()
    assert any(e["text"] == "Agent key made" for e in audit)


@needs_db
def test_the_server_needs_a_live_key_and_only_takes_posts(client, home):
    home.setup()
    token = new_key(client)
    # The browser's session cookie is not a way in.
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert rpc(client, "tools/list", key="fa_agent_wrong").status_code == 401
    assert rpc(client, "tools/list", key=token).status_code == 200
    assert client.get("/mcp").status_code == 405
    assert rpc(client, "tools/list", key=token, Origin="https://evil.example").status_code == 403
    kid = client.get("/api/agents").json()["keys"][0]["id"]
    assert client.delete(f"/api/agents/{kid}", headers=csrf(client)).status_code == 200
    assert rpc(client, "tools/list", key=token).status_code == 401
    assert client.get("/api/agents").json()["keys"] == []


@needs_db
def test_wrong_keys_are_limited(client, home):
    home.setup()
    for _ in range(20):
        assert rpc(client, "ping", key="fa_agent_guess").status_code == 401
    assert rpc(client, "ping", key="fa_agent_guess").status_code == 429


@needs_db
def test_the_protocol(client, home):
    home.setup()
    key = new_key(client)
    r = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
        key=key,
    )
    init = r.json()["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"] == {"tools": {"listChanged": False}}
    assert rpc(client, "initialize", {"protocolVersion": "1999-01-01"}, key=key).json()["result"]["protocolVersion"]
    assert rpc(client, "notifications/initialized", key=key, id_=None).status_code == 202
    tools = rpc(client, "tools/list", key=key).json()["result"]["tools"]
    assert {t["name"] for t in tools} >= {"overview", "transactions", "spending_by_category", "budget", "holdings"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert rpc(client, "resources/list", key=key).json()["error"]["code"] == -32601
    batch = client.post(
        "/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers={"Authorization": f"Bearer {key}"}
    )
    assert batch.status_code == 400
    bad = client.post("/mcp", content=b"{nope", headers={"Authorization": f"Bearer {key}"})
    assert bad.json()["error"]["code"] == -32700
    assert client.get("/api/agents").json()["keys"][0]["uses"] == 4  # the two bad requests never got as far as counting


@needs_db
def test_tools_answer_with_the_apps_numbers(client, home):
    home.setup()
    money_in_the_bank(client)
    key = new_key(client)
    o = call(client, key, "overview")["structuredContent"]
    assert o["net_worth"] == 8145.80 and o["net_worth_change_today"] == 22.35 and o["cash"] == 8145.80
    [acct] = call(client, key, "accounts")["structuredContent"]["accounts"]
    assert acct["name"] == "Chase Total Checking ••4417" and acct["balance"] == 8145.80

    spend = call(client, key, "spending_by_category", {"start": "2026-05-01", "end": "2026-09-30"})["structuredContent"]
    groceries = next(c for c in spend["categories"] if c["category"] == "Groceries")
    assert groceries["spent"] == 2500.0  # 400 + 500 + 600 + 450 + 550; the transfer isn't spending
    monthly = call(client, key, "spending_by_category", {"start": "2026-05-01", "end": "2026-06-30", "by_month": True})
    assert {r["month"] for r in monthly["structuredContent"]["categories"]} == {"2026-05", "2026-06"}

    found = call(client, key, "transactions", {"search": "whole", "start": "2026-01-01", "limit": 2})[
        "structuredContent"
    ]
    assert [t["merchant"] for t in found["transactions"]] == ["Whole Foods", "Whole Foods"] and found["more"]
    assert found["transactions"][0]["amount"] == -550.0
    by_cat = call(client, key, "transactions", {"category": "dining out"})["structuredContent"]["transactions"]
    assert [t["merchant"] for t in by_cat] == ["Sweetgreen"]

    b = call(client, key, "budget")["structuredContent"]
    assert any(line["category"] == "Groceries" for line in b["lines"])

    # Mistakes come back as tool errors the agent can read and fix.
    oops = call(client, key, "transactions", {"category": "Yachts"})
    assert oops["isError"] and "Groceries" in oops["content"][0]["text"]
    oops = call(client, key, "transactions", {"limit": 10_000, "surprise": 1})
    assert oops["isError"] and "limit" in oops["content"][0]["text"]
    assert call(client, key, "drop_tables")["isError"]


# ---- the morning brief


def test_the_lock_screen_line_has_no_numbers():
    f = {"budget_over": [], "accounts_needing_attention": 0, "safe_to_spend_cents": 12_300}
    assert not re.search(r"\d", brief.lock_screen(f))
    assert "over budget" in brief.lock_screen({**f, "budget_over": [{"category": "Dining out", "over_cents": 5}]})


def test_ai_amounts_must_be_the_briefs_own():
    f = {
        "net_worth_cents": 814_580,
        "per_day_cents": 1_250,
        "spent_yesterday_top": [{"category": "Groceries", "cents": 5_420}],
    }
    assert brief.amounts_check_out("Net worth is $8,146; about $12.50 a day; groceries $54.20.", f)
    assert not brief.amounts_check_out("You could save $300 this month.", f)


@needs_db
def test_brief_is_due_once_in_the_morning_window(client, home):
    home.setup()
    with db_of(client).session() as db:
        assert brief.due(db, MORNING) is None  # off until turned on
        brief.set_preferences(db, enabled=True, time="07:00")
        assert brief.due(db, MORNING) == date(2026, 10, 21)
        assert brief.due(db, MORNING.replace(hour=10, minute=59)) is None  # 6:59 AM
        assert brief.due(db, MORNING.replace(hour=15)) is None  # 11 AM: too late, after an outage
        db.add(Brief(day=date(2026, 10, 21), facts={}, headline="x", points={}, written_by="app"))
        db.flush()
        assert brief.due(db, MORNING) is None  # already written


@needs_db
def test_the_app_writes_and_sends_the_brief(client, home, sent):
    home.setup()
    money_in_the_bank(client)
    add_transactions(client, [("2026-10-20", "Blue Bottle", -6.5, "Dining out"), ("2026-10-20", "Shell", -40, None)])
    with db_of(client).session() as db:
        brief.set_preferences(db, enabled=True)
    b = brief.run(db_of(client), client.app.state.settings, MORNING)
    assert b is not None and b.written_by == "app" and b.channel == "ntfy"
    text = " ".join([b.headline, *b.points["items"]])
    assert "Net worth is $8,146 (+$22.35 since yesterday)." in text
    assert "Yesterday you spent $46.50: Uncategorized $40.00, Dining out $6.50." in text
    assert "Blue Bottle" not in json.dumps(b.facts) and "Chase" not in json.dumps(b.facts)
    [msg] = sent
    assert msg["title"] == "Morning brief" and not re.search(r"\d", msg["body"])
    assert msg["click"] == "https://testserver/"  # tapping it opens the app
    assert brief.run(db_of(client), client.app.state.settings, MORNING) is None  # once a day
    today = client.get("/api/brief").json()["today"]
    assert today["headline"] == b.headline and not today["read"]
    client.post(f"/api/brief/{today['id']}/read", headers=csrf(client))
    assert client.get("/api/brief").json()["today"]["read"]


@needs_db
def test_ai_words_the_brief_from_totals_only(client, home, sent, fake):  # noqa: F811
    home.setup()
    money_in_the_bank(client)
    add_claude(client)
    fake.answer = json.dumps(
        {"headline": "A calm Tuesday", "points": ["Net worth is $8,146, up $22.35 since yesterday."]}
    )
    r = client.post("/api/brief/now", headers=csrf(client))
    assert r.status_code == 200, r.text
    today = r.json()["today"]
    assert today["written_by"] == "claude-opus-5-5" and today["headline"] == "A calm Tuesday"
    body = json.loads(fake.sent[-1].content)
    sent_text = json.dumps(body)
    for private in ["Whole Foods", "Landlord", "Chase", "4417", "Shubham", "me@example.com"]:
        assert private not in sent_text
    usage = client.get("/api/ai").json()["usage"]
    assert usage[0]["task"] == "Writing the morning brief"

    # An amount that isn't one of the brief's numbers: the app's own wording is used instead.
    fake.answer = json.dumps({"headline": "Good news", "points": ["You saved $9,999 this month."]})
    with db_of(client).session() as db:
        db.query(Brief).delete()
    today = client.post("/api/brief/now", headers=csrf(client)).json()["today"]
    assert today["written_by"] == "app" and "$9,999" not in json.dumps(today)


@needs_db
def test_brief_settings(client, home):
    home.setup()
    r = client.put(
        "/api/brief/settings", json={"enabled": True, "time": "06:45", "use_ai": False}, headers=csrf(client)
    )
    assert r.status_code == 200 and r.json()["time"] == "06:45" and r.json()["use_ai"] is False
    assert client.put("/api/brief/settings", json={"time": "25:00"}, headers=csrf(client)).status_code == 422
    assert client.post("/api/brief/now", headers=csrf(client)).status_code == 422  # no accounts yet
    with db_of(client).session() as db:
        assert db.scalar(select(Connection.id)) is None
