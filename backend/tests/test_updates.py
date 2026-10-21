import json
import os

import httpx
import pytest

from financeapp import updates
from tests.conftest import Household, csrf, db_of, make_client, needs_db

NOTES = [
    {"version": "0.9.0", "title": "Newest", "features": [{"id": "a", "title": "A", "body": "Alpha", "tryIt": "/x"}]},
    {"version": "0.8.0", "title": "Middle", "features": [{"id": "b", "title": "B", "body": "Beta"}]},
    {"version": "0.7.0", "title": "Running now", "features": []},
    {"version": "1.0.0", "title": "Not released yet", "features": []},
    {"version": "0.8.5", "title": "<script>", "features": [{"title": "x" * 5000, "body": "too long"}]},
]


def github(latest="v0.9.0", notes=NOTES, status=200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(
                200,
                json={"tag_name": latest, "name": "Finance App 0.9.0: Newest", "published_at": "2026-10-21T10:00:00Z"},
            )
        if request.url.path.endswith(f"/download/{latest}/whats-new.json"):
            return httpx.Response(200, json=notes) if notes is not None else httpx.Response(404)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_versions_compare_numerically():
    assert updates.newer("0.10.0", "0.9.9")
    assert not updates.newer("0.9.0", "0.9.0")
    assert updates.newer("1.0.0", "1.0.0-beta.1") is False  # a pre-release counts as its base version
    assert not updates.newer("1.0.0", "dev")
    assert not updates.newer("1.0.0; rm -rf /", "0.1.0")


def test_notes_cover_every_skipped_version_and_nothing_else():
    found = updates.fetch_latest("0.7.0", github())
    assert found["latest"] == "0.9.0"
    assert [n["version"] for n in found["releases"]] == ["0.9.0", "0.8.0"]  # not 0.7.0, 1.0.0, or the bad 0.8.5
    assert found["releases"][0]["features"] == [{"title": "A", "body": "Alpha"}]  # links and ids dropped


def test_a_release_without_notes_still_shows_its_name():
    found = updates.fetch_latest("0.7.0", github(notes=None))
    assert found["releases"] == [{"version": "0.9.0", "title": "Newest", "features": []}]


@pytest.mark.parametrize("tag", ["0.9.0", "v0.9", "v0.9.0-beta.1", "v1.0.0/../../x", 7])
def test_odd_tags_are_refused(tag):
    with pytest.raises(ValueError):
        updates.fetch_latest("0.7.0", github(latest=tag))


def test_up_to_date_fetches_no_notes():
    found = updates.fetch_latest("0.9.0", github())
    assert found == {"latest": "0.9.0", "published_at": "2026-10-21T10:00:00+00:00", "releases": []}


@pytest.fixture
def shared(tmp_path):
    request_dir, status_dir = tmp_path / "update-request", tmp_path / "update-status"
    request_dir.mkdir()
    status_dir.mkdir()
    return request_dir, status_dir


@pytest.fixture
def app(tmp_path, fresh_db, shared):
    request_dir, status_dir = shared
    web = tmp_path / "web"
    web.mkdir()
    with make_client(
        web, fresh_db, app_version="0.7.0", update_request_dir=request_dir, update_status_dir=status_dir
    ) as c:
        yield c


@pytest.fixture
def home(app, clock) -> Household:
    return Household(app, clock)


@needs_db
def test_check_saves_the_answer_and_keeps_it_when_github_is_down(app, home):
    home.setup()
    db, settings = db_of(app), app.app.state.settings
    updates.check(db, settings, github())
    assert app.get("/api/updates").json()["available"] is True
    updates.check(db, settings, github(status=500))
    body = app.get("/api/updates").json()
    assert body["available"] is True and body["latest"] == "0.9.0"
    assert body["check_failed"] is True
    assert [n["version"] for n in body["releases"]] == ["0.9.0", "0.8.0"]


@needs_db
def test_update_now_only_leaves_an_empty_request(app, home, shared):
    request_dir, status_dir = shared
    home.setup()
    updates.check(db_of(app), app.app.state.settings, github())
    h = csrf(app)

    # The server hasn't set up the path unit yet.
    r = app.post("/api/updates/install", headers=h)
    assert r.status_code == 409 and "isn't set up" in r.json()["detail"]
    assert app.get("/api/updates").json()["server"] == {"in_app": False, "nightly": False}

    (status_dir / "agent.json").write_text(json.dumps({"in_app": True, "nightly": True}))
    r = app.post("/api/updates/install", headers=h)
    assert r.status_code == 200, r.text
    assert (request_dir / "request").read_bytes() == b""
    assert r.json()["requested_at"] and not r.json()["stuck"]
    # Tapping again doesn't pile up requests.
    assert app.post("/api/updates/install", headers=h).status_code == 200
    assert os.listdir(request_dir) == ["request"]


@needs_db
def test_update_now_needs_a_newer_version_and_the_owner(app, home, shared):
    _, status_dir = shared
    (status_dir / "agent.json").write_text(json.dumps({"in_app": True}))
    home.setup()
    updates.check(db_of(app), app.app.state.settings, github(latest="v0.7.0"))
    r = app.post("/api/updates/install", headers=csrf(app))
    assert r.status_code == 409 and "latest" in r.json()["detail"]


@needs_db
def test_a_planted_symlink_is_not_followed(app, home, shared, tmp_path):
    request_dir, status_dir = shared
    (status_dir / "agent.json").write_text(json.dumps({"in_app": True}))
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    (request_dir / "request").symlink_to(target)
    home.setup()
    updates.check(db_of(app), app.app.state.settings, github())
    assert app.post("/api/updates/install", headers=csrf(app)).status_code == 200
    assert target.read_text() == "keep"


def test_server_status_is_read_strictly(tmp_path):
    settings = updates.Settings(update_status_dir=tmp_path, update_request_dir=tmp_path / "none")
    (tmp_path / "status.json").write_text(
        json.dumps({"state": "done", "from": "0.7.0", "to": "0.9.0", "at": "2026-10-21T10:00:00Z"})
    )
    assert updates.server_status(settings) == {
        "state": "done",
        "from": "0.7.0",
        "to": "0.9.0",
        "at": "2026-10-21T10:00:00+00:00",
    }
    (tmp_path / "status.json").write_text(json.dumps({"state": "done", "to": "<img src=x>", "at": "soon"}))
    assert updates.server_status(settings) == {"state": "done", "from": None, "to": None, "at": None}
    (tmp_path / "status.json").write_text(json.dumps({"state": "pwned"}))
    assert updates.server_status(settings) is None
    (tmp_path / "status.json").write_text("not json")
    assert updates.server_status(settings) is None


def test_demo_shows_the_version_but_cannot_install(tmp_path):
    with make_client(tmp_path, "", demo_mode=True, app_version="0.7.0") as c:
        body = c.get("/api/updates").json()
        assert body["current"] == "0.7.0" and body["available"] is False and body["can_install"] is False
        assert c.post("/api/updates/install").status_code == 403
