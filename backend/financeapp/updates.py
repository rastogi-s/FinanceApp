"""Is there a newer version, and "Update now".

Checking: every few hours the app asks GitHub for the latest release of this repository (a public, read-only request
with no account and nothing about the household in it) and fetches that release's whats-new.json for the notes. The
answer is saved in the preferences table. Everything that comes back is checked against a strict shape and shown
as plain text.

Installing is never done here. The app has no Docker socket and can't start, stop or pull anything. "Update now" only
leaves an empty request file in a folder the server shares with it. A systemd path unit on the server
(deploy/systemd/financeapp-update.path) notices the file and runs update.sh, which deletes the request without
reading it, installs the latest release only if its signature checks out, backs up first and rolls back if the new
version isn't healthy. update.sh reports how it went in a second folder that the app can only read.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session as DbSession

from financeapp.config import Settings
from financeapp.db import Database
from financeapp.models import Preference, now

log = logging.getLogger("financeapp")

REPO = "rastogi-s/FinanceApp"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
NOTES_URL = f"https://github.com/{REPO}/releases/download/v{{version}}/whats-new.json"
CHECK_EVERY = timedelta(hours=6)
MAX_BYTES = 256 * 1024
VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
STATES = {"checking", "installing", "done", "current", "failed", "rolled_back"}
# A request the server hasn't picked up after this long means the path unit isn't running.
STUCK_AFTER = timedelta(minutes=2)


def parse(version: str) -> tuple[int, int, int] | None:
    """1.4.2 -> (1, 4, 2). A pre-release (1.5.0-beta.1) counts as its base version; "dev" isn't a version."""
    base = version.split("-", 1)[0]
    return tuple(int(p) for p in base.split(".")) if VERSION.match(base) else None  # type: ignore[return-value]


def newer(candidate: str, current: str) -> bool:
    a, b = parse(candidate), parse(current)
    return a is not None and b is not None and a > b


# ---- the shape we accept from GitHub


class Feature(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(max_length=2000)


class Notes(BaseModel):
    version: str = Field(pattern=VERSION.pattern)
    title: str = Field(default="", max_length=200)
    features: list[Feature] = Field(default_factory=list, max_length=20)


def _get_json(client: httpx.Client, url: str) -> object | None:
    """GET a small JSON document, or None for a 404. Anything bigger than MAX_BYTES is refused."""
    with client.stream("GET", url) as r:
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = b""
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError("response too large")
    return json.loads(body)


def fetch_latest(current: str, client: httpx.Client) -> dict:
    """The newest release and the notes for every version between the running one and it, newest first."""
    release = _get_json(client, LATEST_URL)
    if not isinstance(release, dict):
        raise ValueError("no latest release")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v") or not VERSION.match(tag[1:]):
        raise ValueError("unexpected tag")
    latest = tag[1:]
    published = release.get("published_at")
    try:
        published_at = datetime.fromisoformat(published).isoformat() if isinstance(published, str) else None
    except ValueError:
        published_at = None

    releases: list[dict] = []
    if newer(latest, current):
        raw = _get_json(client, NOTES_URL.format(version=latest))
        if isinstance(raw, list):
            for item in raw[:200]:
                try:
                    n = Notes.model_validate(item)
                except ValidationError:
                    continue
                if newer(n.version, current) and not newer(n.version, latest):
                    releases.append(n.model_dump())
        releases.sort(key=lambda n: parse(n["version"]) or (0, 0, 0), reverse=True)
        releases = releases[:10]
        if not any(n["version"] == latest for n in releases):
            # An older release without whats-new.json attached: its name is all there is.
            name = release.get("name") if isinstance(release.get("name"), str) else ""
            title = name.split(": ", 1)[1] if ": " in name else ""
            releases.insert(0, {"version": latest, "title": title[:200], "features": []})
    return {"latest": latest, "published_at": published_at, "releases": releases}


# ---- saved state


def saved(db: DbSession) -> dict:
    p = db.get(Preference, "updates")
    return dict(p.value) if p else {}


def _save(db: DbSession, value: dict) -> None:
    p = db.get(Preference, "updates")
    if p is None:
        db.add(Preference(key="updates", value=value))
    else:
        p.value = value


def check(database: Database, settings: Settings, client: httpx.Client | None = None) -> dict:
    """Asks GitHub now and saves the answer. A failed check keeps the last good answer."""
    own = client is None
    client = client or httpx.Client(
        timeout=10,
        follow_redirects=True,
        headers={"User-Agent": "FinanceApp (self-hosted)", "Accept": "application/vnd.github+json"},
    )
    try:
        found = fetch_latest(settings.app_version, client)
        failed = False
    except (httpx.HTTPError, ValueError) as e:
        log.warning("update check failed (%s)", type(e).__name__)
        found, failed = {}, True
    finally:
        if own:
            client.close()
    with database.session() as db:
        at = now().isoformat()
        value = {**saved(db), "failed_at": at} if failed else {**found, "for_version": settings.app_version}
        value["checked_at"] = at
        _save(db, value)
        return value


def check_if_due(database: Database, settings: Settings) -> None:
    if not settings.update_check or parse(settings.app_version) is None:
        return
    with database.session() as db:
        value = saved(db)
    last = value.get("checked_at")
    # A different running version (just updated) means the saved answer is about some other version: ask again.
    fresh = last and datetime.fromisoformat(last) > now() - CHECK_EVERY
    if fresh and value.get("for_version") == settings.app_version:
        return
    check(database, settings)


# ---- the folders shared with the server


def _small_json(path: Path) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        raw = f.read(4096)
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def server_status(settings: Settings) -> dict | None:
    """What update.sh last reported: its state, the versions involved and when. Unknown values are dropped."""
    value = _small_json(settings.update_status_dir / "status.json")
    if value is None or value.get("state") not in STATES:
        return None
    out = {"state": value["state"]}
    for key in ("from", "to"):
        v = value.get(key)
        out[key] = v if isinstance(v, str) and parse(v) is not None and len(v) <= 40 else None
    try:
        out["at"] = datetime.fromisoformat(value["at"]).isoformat()
    except (KeyError, TypeError, ValueError):
        out["at"] = None
    return out


def server_agent(settings: Settings) -> dict:
    """Whether the server set up "Update now" (update.sh writes agent.json once the path unit is on)."""
    agent = _small_json(settings.update_status_dir / "agent.json") or {}
    request_dir = settings.update_request_dir
    ready = bool(agent.get("in_app")) and request_dir.is_dir() and os.access(request_dir, os.W_OK)
    return {"in_app": ready, "nightly": bool(agent.get("nightly"))}


def pending_since(settings: Settings) -> datetime | None:
    """When "Update now" was asked for, if the server hasn't picked the request up yet."""
    try:
        st = (settings.update_request_dir / "request").lstat()
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_mtime, UTC)


def request_install(settings: Settings) -> bool:
    """Leaves the request for the server. False if one is already waiting. The file is empty on purpose: the server
    deletes it without reading it and installs whatever the latest signed release is, so there is nothing in it for
    a broken app to get wrong."""
    path = settings.update_request_dir / "request"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def state(db: DbSession | None, settings: Settings) -> dict:
    """Everything the Updates card and banner show."""
    value = saved(db) if db is not None else {}
    current = settings.app_version
    if value.get("for_version") != current:
        # Saved before the last update; until the next check, all it can tell is whether that release is newer.
        value = {**value, "releases": [n for n in value.get("releases", []) if newer(n["version"], current)]}
    latest = value.get("latest")
    status = server_status(settings)
    since = pending_since(settings)
    return {
        "current": current,
        "latest": latest,
        "available": bool(latest) and newer(latest, current),
        "published_at": value.get("published_at"),
        "releases": value.get("releases", []),
        "checked_at": value.get("checked_at"),
        "check_failed": bool(value.get("failed_at")) and value.get("failed_at") == value.get("checked_at"),
        "check_enabled": settings.update_check,
        "server": server_agent(settings),
        "status": status,
        "requested_at": since.isoformat() if since else None,
        "stuck": bool(since and since < now() - STUCK_AFTER),
    }
