import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from financeapp.ai import providers, service
from financeapp.ai.providers import Endpoint, Picture, ProviderError, Request
from financeapp.ai.redact import describe, redact
from financeapp.models import AIProvider, AIUsage, Session, now
from tests.conftest import csrf, db_of, needs_db

CLAUDE_KEY = "sk-ant-api03-very-secret-key-ABCD"


class FakeProviders:
    """Stands in for every provider's HTTP API. Records what was sent; answers like the real thing."""

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.answer = '{"ready": true}'
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        host, path = request.url.host, request.url.path
        if host == "api.anthropic.com":
            if request.headers.get("x-api-key") != CLAUDE_KEY:
                return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
            if path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}]})
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": self.answer}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1000, "output_tokens": 100},
                },
            )
        if host == "api.openai.com" or host == "localhost":
            if path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "text-embedding-3"}]})
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Sure: " + self.answer}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 5},
                },
            )
        if host == "generativelanguage.googleapis.com":
            if path.endswith("/models"):
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"]},
                            {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]},
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": self.answer}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 70, "candidatesTokenCount": 7, "thoughtsTokenCount": 3},
                },
            )
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch) -> FakeProviders:
    f = FakeProviders()
    monkeypatch.setattr(
        providers, "_client", lambda: httpx.Client(transport=httpx.MockTransport(f.handler), follow_redirects=False)
    )
    return f


# ---- redaction


def test_redaction_takes_out_identifying_details_but_keeps_the_money():
    text = (
        "RASTOGI, SHUBHAM   Employee ID: E12345   SSN 123-45-6789\n"
        "123 Main Street, Apt 4B\n"
        "Acct 1234567890123  routing 021000021  card 4111 1111 1111 1111\n"
        "Gross Pay 5,432.10  Pay date 10/15/2026  20261015  Net 3,210.00  (555) 123-4567  me@example.com\n"
        "Fidelity 401(k) 250.00  YTD 12,000.00\n"
    )
    r = redact(text, names=["Shubham Rastogi"], emails=["me@example.com"])
    for gone in ["RASTOGI", "SHUBHAM", "E12345", "123-45-6789", "Main Street", "1234567890123", "021000021", "555"]:
        assert gone not in r.text
    for kept in ["5,432.10", "10/15/2026", "20261015", "3,210.00", "Fidelity 401(k) 250.00", "12,000.00"]:
        assert kept in r.text
    assert "[number ending 0123]" in r.text and "[number ending 1111]" in r.text
    assert r.counts["account or card numbers"] == 3
    assert "1 Social Security number" in describe(r.counts)


def test_parse_json_tolerates_chatty_local_models():
    assert providers.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert providers.parse_json('Here you go: {"a": {"b": 2}} Hope that helps') == {"a": {"b": 2}}
    with pytest.raises(ProviderError):
        providers.parse_json("no idea")


def test_local_servers_may_use_http_but_nothing_else_may():
    assert providers.check_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"
    assert providers.check_base_url("http://192.168.1.20:1234/v1")
    assert providers.check_base_url("http://100.101.102.103:11434/v1")  # Tailscale
    assert providers.check_base_url("https://openrouter.ai/api/v1")
    for bad in ["http://example.com/v1", "ftp://localhost", "https://user:pw@host/v1", "localhost:11434"]:
        with pytest.raises(ProviderError):
            providers.check_base_url(bad)


# ---- the providers


def test_each_provider_gets_the_same_request_in_its_own_shape(fake):
    req = Request(
        system="sys", text="read this", pictures=[Picture("image/png", b"\x89PNG")], schema={"type": "object"}
    )

    r = providers.complete(Endpoint("anthropic", CLAUDE_KEY), "claude-opus-5-5", req)
    body = json.loads(fake.sent[-1].content)
    assert (r.input_tokens, r.output_tokens) == (1000, 100)
    assert body["system"] == "sys" and body["messages"][0]["content"][0]["type"] == "image"
    assert body["output_config"]["format"]["type"] == "json_schema"

    r = providers.complete(Endpoint("openai", "sk-openai"), "gpt-x", req)
    body = json.loads(fake.sent[-1].content)
    assert fake.sent[-1].headers["authorization"] == "Bearer sk-openai"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert providers.parse_json(r.text) == {"ready": True}

    r = providers.complete(Endpoint("gemini", "g-key"), "gemini-x", req)
    assert fake.sent[-1].url.path == "/v1beta/models/gemini-x:generateContent"
    assert fake.sent[-1].headers["x-goog-api-key"] == "g-key" and "key=" not in str(fake.sent[-1].url)
    assert r.output_tokens == 10  # thinking counts as output

    local = Endpoint("openai_compatible", None, "http://localhost:11434/v1")
    providers.complete(local, "llama3.2-vision", req)
    body = json.loads(fake.sent[-1].content)
    assert "authorization" not in fake.sent[-1].headers and "response_format" not in body


def test_listing_models_checks_the_key_and_keeps_only_chat_models(fake):
    assert providers.list_models(Endpoint("anthropic", CLAUDE_KEY)) == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert providers.list_models(Endpoint("openai", "k")) == ["gpt-x"]
    assert providers.list_models(Endpoint("gemini", "k")) == ["gemini-x"]
    with pytest.raises(ProviderError, match="didn't accept the key") as e:
        providers.list_models(Endpoint("anthropic", "wrong-key"))
    assert "wrong-key" not in str(e.value)


def test_a_redirect_is_never_followed(monkeypatch):
    def handler(request):
        return httpx.Response(307, headers={"location": "https://evil.example/steal"})

    monkeypatch.setattr(providers, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="redirect"):
        providers.list_models(Endpoint("openai", "k"))


def test_a_model_name_cant_change_the_url(fake):
    with pytest.raises(ProviderError):
        providers.complete(Endpoint("gemini", "k"), "../../files?x=", Request(system="s", text="t"))
    assert fake.sent == []


# ---- settings, routing, the cap and the log


def add_claude(client) -> dict:
    r = client.post("/api/ai/providers", json={"kind": "anthropic", "api_key": CLAUDE_KEY}, headers=csrf(client))
    assert r.status_code == 200, r.text
    return r.json()


@needs_db
def test_adding_a_provider_checks_the_key_and_encrypts_it(client, home, fake):
    home.setup()
    assert client.get("/api/ai").json()["providers"] == []
    r = client.post("/api/ai/providers", json={"kind": "anthropic", "api_key": "wrong"}, headers=csrf(client))
    assert r.status_code == 422 and "didn't accept the key" in r.json()["detail"]

    data = add_claude(client)
    p = data["providers"][0]
    assert p["key_hint"] == "ABCD" and p["models"] == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert p["enabled"] is True and p["allow_pictures"] is False  # pictures can't be redacted: off for the cloud
    assert CLAUDE_KEY not in json.dumps(data)
    with db_of(client).session() as db:
        row = db.scalar(select(AIProvider))
        assert CLAUDE_KEY not in row.api_key_enc and row.api_key_enc.startswith("v1:")
    # The first provider takes every task, with the suggested model.
    task = data["tasks"][0]
    assert task["route"] == {"provider_id": p["id"], "model": "claude-opus-5-5"}
    assert task["ready"] is True and task["price"] == {"in": 4.0, "out": 20.0, "known": True}


@needs_db
def test_a_test_request_goes_through_and_is_logged_without_content(client, home, fake):
    home.setup()
    add_claude(client)
    r = client.post("/api/ai/try", json={"task": "read_files"}, headers=csrf(client))
    assert r.status_code == 200, r.text
    tried = r.json()["tried"]
    assert tried["ok"] is True and tried["model"] == "claude-opus-5-5"
    assert tried["cost"] == pytest.approx(0.006)  # 1000 in at $4/M + 100 out at $20/M
    data = client.get("/api/ai").json()
    assert data["spent"] == pytest.approx(0.006)
    assert data["usage"][0] | {"id": 0, "at": ""} == {
        "id": 0,
        "at": "",
        "who": "Shubham",
        "task": "Reading uploads",
        "model": "claude-opus-5-5",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost": 0.006,
        "priced": True,
        "ok": True,
        "error": None,
    }


@needs_db
def test_nothing_is_sent_over_the_cap_or_to_a_provider_thats_off(client, home, fake):
    home.setup()
    data = add_claude(client)
    pid = data["providers"][0]["id"]
    sent = len(fake.sent)
    # A cap of $0 stops everything that isn't free.
    client.put("/api/ai/settings", json={"monthly_cap_cents": 0}, headers=csrf(client))
    r = client.post("/api/ai/try", json={"task": "read_files"}, headers=csrf(client))
    assert r.status_code == 409 and "cap" in r.json()["detail"]
    client.put("/api/ai/settings", json={"monthly_cap_cents": 500}, headers=csrf(client))
    client.patch(f"/api/ai/providers/{pid}", json={"enabled": False}, headers=csrf(client))
    r = client.post("/api/ai/try", json={"task": "read_files"}, headers=csrf(client))
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    assert len(fake.sent) == sent
    client.patch(f"/api/ai/providers/{pid}", json={"enabled": True}, headers=csrf(client))
    with db_of(client).session() as db, pytest.raises(service.AIUnavailable, match="pictures"):
        service.run(db_of(client), db, "read_files", None, Request("s", "t", pictures=[Picture("image/png", b"x")]))
    assert len(fake.sent) == sent


@needs_db
def test_a_failed_request_is_still_logged(client, home, fake):
    home.setup()
    add_claude(client)
    fake.status = 500
    r = client.post("/api/ai/try", json={"task": "read_files"}, headers=csrf(client))
    assert r.status_code == 502
    usage = client.get("/api/ai").json()["usage"]
    assert usage[0]["ok"] is False and "error (500" in usage[0]["error"]


@needs_db
def test_unknown_models_are_priced_high_until_given_a_price(client, home, fake):
    home.setup()
    r = client.post(
        "/api/ai/providers", json={"kind": "openai", "api_key": "sk-openai-1234567890"}, headers=csrf(client)
    )
    pid = r.json()["providers"][0]["id"]
    assert r.json()["tasks"][0]["route"]["model"] == "gpt-x"
    assert r.json()["tasks"][0]["price"] == {"in": 15.0, "out": 75.0, "known": False}
    route = {"provider_id": pid, "model": "gpt-x", "price_in": 1.25, "price_out": 10}
    r = client.put("/api/ai/settings", json={"routes": {"read_files": route}}, headers=csrf(client))
    assert r.json()["tasks"][0]["price"] == {"in": 1.25, "out": 10.0, "known": True}
    # A local server costs nothing and may see pictures (they never leave your network).
    r = client.post(
        "/api/ai/providers",
        json={"kind": "openai_compatible", "base_url": "http://localhost:11434/v1", "label": "Ollama"},
        headers=csrf(client),
    )
    local = r.json()["providers"][1]
    assert local["allow_pictures"] is True and local["key_hint"] is None
    r = client.put(
        "/api/ai/settings",
        json={"routes": {"read_files": {"provider_id": local["id"], "model": "gpt-x"}}},
        headers=csrf(client),
    )
    assert r.json()["tasks"][0]["price"] == {"in": 0.0, "out": 0.0, "known": True}


@needs_db
def test_keys_need_the_owner_and_a_recent_check(client, home, fake):
    home.setup()
    data = add_claude(client)
    pid = data["providers"][0]["id"]
    with db_of(client).session() as db:
        db.execute(update(Session).values(reauth_at=now() - timedelta(minutes=30)))
    r = client.put(f"/api/ai/providers/{pid}/key", json={"api_key": "sk-new-key-123456"}, headers=csrf(client))
    assert r.status_code == 403 and r.json()["detail"] == "reauth_required"
    assert client.delete(f"/api/ai/providers/{pid}", headers=csrf(client)).status_code == 403
    client.post("/api/auth/reauth", json={"code": home.code()}, headers=csrf(client))
    # A key that doesn't work never replaces one that does.
    r = client.put(f"/api/ai/providers/{pid}/key", json={"api_key": "sk-new-key-123456"}, headers=csrf(client))
    assert r.status_code == 422
    assert client.get("/api/ai").json()["providers"][0]["key_hint"] == "ABCD"
    r = client.delete(f"/api/ai/providers/{pid}", headers=csrf(client))
    assert r.json()["providers"] == [] and r.json()["tasks"][0]["route"] is None
    with db_of(client).session() as db:
        assert db.scalar(select(AIUsage)) is None


@needs_db
def test_members_can_look_but_not_change(client, home, fake):
    home.setup()
    add_claude(client)
    code = client.post("/api/auth/people/invite", json={"name": "Priya"}, headers=csrf(client)).json()["code"]
    client.post("/api/auth/logout", headers=csrf(client))
    client.cookies.clear()
    r = client.post(
        "/api/auth/invite/accept",
        json={"code": code, "email": "priya@example.com", "password": "another long passphrase"},
    )
    home.enroll(r.json(), "priya@example.com")
    data = client.get("/api/ai").json()
    assert data["can_edit"] is False and data["providers"][0]["key_hint"] == "ABCD"
    assert client.put("/api/ai/settings", json={"monthly_cap_cents": 9}, headers=csrf(client)).status_code == 403
    pid = data["providers"][0]["id"]
    assert client.patch(f"/api/ai/providers/{pid}", json={"enabled": False}, headers=csrf(client)).status_code == 403
