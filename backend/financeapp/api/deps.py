"""What every API route can depend on: the database, the signed-in person, and the audit log."""

import secrets
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from financeapp import auth
from financeapp.config import Settings
from financeapp.models import AuditEvent, Session, User

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[DbSession]:
    db = request.app.state.db
    if db is None:
        raise HTTPException(503, "No database is configured.")
    with db.session() as s:
        yield s


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(db: DbSession, request: Request | None, action: str, user_id: int | None = None, **detail) -> None:
    db.add(AuditEvent(action=action, user_id=user_id, detail=detail, ip=client_ip(request) if request else None))


@dataclass
class Signed:
    """The signed-in person and their session. In demo mode there is no session and the user isn't in the database."""

    user: User
    session: Session | None
    demo: bool = False


DEMO_USER = User(id=0, email="sample@example.com", name="Sample", role="owner")


def signed_in(request: Request) -> Iterator[Signed]:
    settings = settings_of(request)
    if settings.demo_mode:
        if request.method in UNSAFE:
            raise HTTPException(403, "Not available with sample data.")
        yield Signed(DEMO_USER, None, demo=True)
        return
    if request.app.state.db is None:
        raise HTTPException(503, "No database is configured.")
    # One transaction for the sign-in check and the route: committed when the route succeeds, rolled back if it
    # raises (FastAPI throws the route's exception in at the yield).
    with request.app.state.db.session() as db:
        token = request.cookies.get(auth.cookie_name(auth.SESSION_COOKIE, settings))
        s = auth.find_session(db, token, settings)
        user = db.get(User, s.user_id) if s else None
        if s is None or user is None:
            db.commit()  # keep the removal of an expired session
            raise HTTPException(401, "Sign in to continue.")
        if request.method in UNSAFE:
            sent = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(sent, s.csrf_token):
                raise HTTPException(403, "This request didn't come from the app. Reload the page and try again.")
        request.state.db = db
        yield Signed(user, s)


def live(who: Signed) -> Session:
    """The session of a real (not demo) sign-in."""
    if who.session is None:
        raise HTTPException(403, "Not available with sample data.")
    return who.session


def owner(who: Signed = Depends(signed_in)) -> Signed:
    if who.user.role != "owner":
        raise HTTPException(403, "Only the household's owner can do that.")
    return who


def recent(who: Signed = Depends(signed_in)) -> Signed:
    """For sensitive actions: the person must have confirmed it's them in the last few minutes."""
    if who.session is not None and not auth.recently_authenticated(who.session):
        raise HTTPException(403, "reauth_required")
    return who


def session_db(request: Request, who: Signed = Depends(signed_in)) -> DbSession:
    """The database session the sign-in check opened, so a route and its auth share one transaction."""
    return request.state.db
