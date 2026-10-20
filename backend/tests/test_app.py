import base64
import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from financeapp.config import PACKAGE_DIR, Settings
from financeapp.main import create_app

INLINE = b"self.__next_f.push([1,'hello'])"


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html><script>" + INLINE + b"</script><script src='/a.js'></script></html>")
    (tmp_path / "spending.html").write_bytes(b"<html><h1>Spending</h1></html>")
    (tmp_path / "404.html").write_bytes(b"<html><h1>Not here</h1></html>")
    (tmp_path / "_next" / "static" / "chunks").mkdir(parents=True)
    (tmp_path / "_next" / "static" / "chunks" / "app.js").write_text("console.log(1)")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    (tmp_path / "sw.js").write_text("// sw")
    return tmp_path


def client(site, **overrides) -> TestClient:
    settings = Settings(static_dir=site, allowed_hosts="testserver", **overrides)
    return TestClient(create_app(settings))


def test_page_csp_allows_exactly_its_inline_scripts(site):
    r = client(site).get("/")
    assert r.status_code == 200
    expected = base64.b64encode(hashlib.sha256(INLINE).digest()).decode()
    csp = r.headers["content-security-policy"]
    assert f"'sha256-{expected}'" in csp
    assert "unsafe-eval" not in csp
    assert re.search(r"script-src [^;]*'unsafe-inline'", csp) is None
    assert "frame-ancestors 'none'" in csp


def test_every_response_has_security_headers(site):
    c = client(site)
    for path in ["/", "/spending", "/api/meta", "/health", "/nope"]:
        h = c.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "camera=()" in h["permissions-policy"]
        assert "content-security-policy" in h
        assert "server" not in h


def test_hsts_only_when_enabled(site):
    assert "strict-transport-security" not in client(site).get("/").headers
    h = client(site, hsts=True).get("/").headers
    assert h["strict-transport-security"].startswith("max-age=")
    assert "upgrade-insecure-requests" in h["content-security-policy"]


def test_api_responses_are_never_cached(site):
    r = client(site, demo_mode=True).get("/api/household")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")


def test_pages_and_assets(site):
    c = client(site)
    assert c.get("/spending").text.startswith("<html><h1>Spending")
    assert c.get("/spending/").status_code == 200
    asset = c.get("/_next/static/chunks/app.js")
    assert asset.headers["cache-control"].endswith("immutable")
    assert c.get("/manifest.webmanifest").headers["content-type"] == "application/manifest+json"
    assert c.get("/sw.js").headers["cache-control"] == "no-cache"


def test_unknown_paths_get_the_404_page_and_cannot_escape_the_build(site):
    c = client(site)
    for path in ["/missing", "/../pyproject.toml", "/%2e%2e/%2e%2e/etc/passwd", "/_next/../index.html"]:
        r = c.get(path)
        assert r.status_code in (200, 404)
        assert b"root:" not in r.content and b"[project]" not in r.content
    assert c.get("/missing").text == "<html><h1>Not here</h1></html>"


def test_unknown_api_paths_are_json_404s(site):
    r = client(site).post("/api/anything")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}


def test_no_api_docs_are_published(site):
    c = client(site)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert c.get(path).status_code == 404


def test_requests_for_other_hosts_are_refused(site):
    r = client(site).get("/", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_health_reports_the_database(site):
    r = client(site, database_url="postgresql+psycopg://x:y@127.0.0.1:1/none").get("/health")
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"
    assert client(site).get("/health").status_code == 503  # no database and not a demo
    ok = client(site, demo_mode=True, app_version="1.2.3").get("/health")
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok", "version": "1.2.3", "database": "not configured"}


def test_household_is_empty_until_accounts_are_linked(site):
    assert client(site).get("/api/household").json() == {"demo": False, "linked": False}


def test_demo_household_adds_up(site):
    h = client(site, demo_mode=True).get("/api/household").json()
    assert h["demo"] is True
    assert h["net_worth"]["history"][-1]["cents"] == h["net_worth"]["cents"]
    assert len(h["budgets_closest"]) == 3
    allocation = sum(a["cents"] for a in h["allocation"])
    debts = sum(d["cents"] for d in h["accounts"]["debts"])
    assert allocation - debts == h["net_worth"]["cents"]
    assert all(isinstance(t["amount_cents"], int) for t in h["transactions"])


def test_whats_new_is_valid():
    entries = json.loads((PACKAGE_DIR / "data" / "whats-new.json").read_text())
    versions = [e["version"] for e in entries]
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in versions)
    assert len(set(versions)) == len(versions)
    for e in entries:
        assert e["title"] and e["features"]
        for f in e["features"]:
            assert f["id"] and f["title"] and f["body"]


def test_csp_hashes_scripts_whatever_the_end_tag_spelling(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html><SCRIPT>a()</script ><script type='x'>b()</Script></html>")
    csp = client(tmp_path).get("/").headers["content-security-policy"]
    for code in (b"a()", b"b()"):
        assert base64.b64encode(hashlib.sha256(code).digest()).decode() in csp
