"""Updates: what's out, checking now, and "Update now" (which only leaves a request for the server; see updates.py)."""

import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from financeapp import notify, updates
from financeapp.api.deps import Signed, audit, owner, recent, settings_of, signed_in

router = APIRouter(prefix="/api")
# "Check now" asks GitHub at most this often, however many times it's tapped (GitHub allows 60 an hour per address).
CHECK_EVERY_SECONDS = 60
_last_check = 0.0
_check_lock = threading.Lock()


def _state(request: Request, who: Signed) -> dict:
    db = None if who.demo else request.state.db
    value = updates.state(db, settings_of(request))
    value["can_install"] = who.user.role == "owner" and not who.demo
    return value


@router.get("/updates")
def get_updates(request: Request, who: Signed = Depends(signed_in)) -> dict:
    return _state(request, who)


@router.post("/updates/check")
def check_now(request: Request, who: Signed = Depends(signed_in)) -> dict:
    global _last_check
    settings = settings_of(request)
    if not settings.update_check:
        raise HTTPException(409, "Checking for updates is turned off on the server (UPDATE_CHECK=false).")
    if updates.parse(settings.app_version) is None:
        raise HTTPException(409, "This is a development build, so there's nothing to compare against.")
    with _check_lock:
        due = time.monotonic() - _last_check >= CHECK_EVERY_SECONDS
        if due:
            _last_check = time.monotonic()
    if due:
        updates.check(request.app.state.db, settings)
    return _state(request, who)


@router.post("/updates/install")
def install(request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)) -> dict:
    settings = settings_of(request)
    value = updates.state(request.state.db, settings)
    if not value["available"]:
        raise HTTPException(409, "You're already on the latest version.")
    if not value["server"]["in_app"]:
        raise HTTPException(409, "The server isn't set up to update from the app yet.")
    try:
        fresh = updates.request_install(settings)
    except OSError:
        detail = "Couldn't reach the server's updater. Try again, or run ./update.sh on the server."
        raise HTTPException(503, detail) from None
    if fresh:
        audit(request.state.db, request, "update_requested", who.user.id, to=value["latest"], current=value["current"])
        notify.send(
            settings,
            "Update started",
            f"{who.user.name} asked to update the Finance App from {value['current']} to {value['latest']}.",
            tags="arrows_counterclockwise",
            link="/settings",
        )
    return _state(request, who)
