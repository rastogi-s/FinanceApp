import httpx
import pytest

from financeapp import notify
from financeapp.config import Settings


def settings(**kw) -> Settings:
    return Settings(**{"ntfy_url": "https://ntfy.sh/finance-abc123", **kw})


@pytest.fixture
def posted(monkeypatch) -> list[dict]:
    out: list[dict] = []

    def post(url, json=None, headers=None, timeout=None, **kw):
        out.append({"url": url, "json": json, "headers": headers or {}})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", post)
    return out


def test_ntfy_target():
    assert notify.ntfy_target("https://ntfy.sh/finance-abc123") == ("https://ntfy.sh/", "finance-abc123")
    assert notify.ntfy_target(" https://my.host/ntfy/topic/ ") == ("https://my.host/ntfy/", "topic")
    for bad in ("ntfy.sh/topic", "https://ntfy.sh", "https://ntfy.sh/", ""):
        with pytest.raises(notify.SendError):
            notify.ntfy_target(bad)


def test_titles_with_accents_and_dashes_go_out(posted):
    # Sent as headers, a title like this failed with UnicodeEncodeError before it reached ntfy.
    assert notify.deliver(settings(ntfy_token="tk"), "Café — over budget", "• one\n• two", tags="bell",
                          priority="default", click="https://finance.example/") == "ntfy"  # fmt: skip
    p = posted[0]
    assert p["url"] == "https://ntfy.sh/"
    assert p["json"] == {"topic": "finance-abc123", "title": "Café — over budget", "message": "• one\n• two",
                         "tags": ["bell"], "priority": 3, "click": "https://finance.example/"}  # fmt: skip
    assert p["headers"] == {"Authorization": "Bearer tk"}
    last = notify.last_attempt()
    assert last and last["ok"] and last["channel"] == "ntfy" and last["reason"] is None


@pytest.mark.parametrize(("status", "words"), [(403, "NTFY_TOKEN"), (429, "limiting"), (500, "error (500)")])
def test_failures_are_recorded_with_a_reason(monkeypatch, status, words):
    monkeypatch.setattr(
        notify.httpx, "post", lambda url, **kw: httpx.Response(status, request=httpx.Request("POST", url))
    )
    assert notify.deliver(settings(), "Test alert", "hi") is None
    last = notify.last_attempt()
    assert last and not last["ok"] and words in last["reason"]


def test_unreachable_and_bad_address(monkeypatch):
    def down(url, **kw):
        raise httpx.ConnectError("no route to host 10.0.0.1")

    monkeypatch.setattr(notify.httpx, "post", down)
    assert notify.deliver(settings(), "Test alert", "hi") is None
    reason = notify.last_attempt()["reason"]
    assert "Couldn't reach" in reason and "10.0.0.1" not in reason  # never an exception's own text
    assert notify.deliver(settings(ntfy_url="ntfy.sh/topic"), "Test alert", "hi") is None
    assert "full address" in notify.last_attempt()["reason"]
