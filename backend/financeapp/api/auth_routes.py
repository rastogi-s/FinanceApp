"""Sign-in, first-run setup, invitations, passkeys, sessions and the audit log: everything under /api/auth."""

import json
import secrets
from datetime import timedelta

import segno
import webauthn
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from financeapp import auth, crypto, notify
from financeapp.api.deps import (
    DEMO_USER,
    Signed,
    audit,
    client_ip,
    get_db,
    live,
    owner,
    recent,
    session_db,
    settings_of,
    signed_in,
)
from financeapp.models import AuditEvent, Device, Invite, Passkey, Session, User, now

router = APIRouter(prefix="/api/auth")


def rate_limited(request: Request) -> None:
    if not request.app.state.limiter.allow(client_ip(request)):
        raise HTTPException(429, "Too many attempts from here. Wait a few minutes and try again.")


# ---- shapes


class SetupIn(BaseModel):
    code: str = Field(max_length=40)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(max_length=auth.PASSWORD_MAX)


class InviteAcceptIn(BaseModel):
    code: str = Field(max_length=40)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(max_length=auth.PASSWORD_MAX)


class CodeIn(BaseModel):
    code: str = Field(max_length=40)


class TokenCodeIn(BaseModel):
    token: str = Field(max_length=100)
    code: str = Field(max_length=12)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=auth.PASSWORD_MAX)


class PasskeyOptionsIn(BaseModel):
    purpose: str = Field(default="signin", pattern="^(signin|reauth)$")


class PasskeyVerifyIn(BaseModel):
    token: str = Field(max_length=100)
    credential: dict
    purpose: str = Field(default="signin", pattern="^(signin|reauth)$")


class PasskeyRegisterIn(BaseModel):
    token: str = Field(max_length=100)
    credential: dict
    name: str = Field(default="", max_length=80)


class PasswordIn(BaseModel):
    password: str = Field(max_length=auth.PASSWORD_MAX)


class InviteIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


# ---- helpers


def user_json(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


def clean_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@") or any(c.isspace() for c in email):
        raise HTTPException(422, "That doesn't look like an email address.")
    return email


def relying_party(request: Request) -> tuple[str, list[str]]:
    """The passkey 'relying party' is the host name the app was opened at (already checked against ALLOWED_HOSTS)."""
    host = request.headers.get("host", "")
    rp_id = host.rsplit(":", 1)[0] if not host.startswith("[") else host
    origins = [f"https://{host}"]
    if not settings_of(request).hsts:
        origins.append(f"http://{host}")
    return rp_id, origins


def start_session(db: DbSession, request: Request, response: Response, user: User, method: str) -> dict:
    settings = settings_of(request)
    device_token = request.cookies.get(auth.cookie_name(auth.DEVICE_COOKIE, settings))
    had_devices = db.scalar(select(func.count()).select_from(Device).where(Device.user_id == user.id)) or 0
    device, fresh_device_token, is_new = auth.known_device(db, user, device_token)
    label = auth.device_label(request.headers.get("user-agent", ""))
    if is_new:
        device.label = label
    token, session = auth.create_session(db, user, device, method, settings)
    auth.record_success(user)
    audit(db, request, "signin", user.id, method=method, device=label, new_device=is_new)
    if is_new and had_devices:
        audit(db, request, "signin_new_device", user.id, device=label)
        notify.send(
            settings,
            "New sign-in",
            f"{user.name} signed in from a new device ({label}). Not you? Open "
            "Settings → Security and sign out everywhere.",
        )
    secure = settings.secure_cookies
    response.set_cookie(
        auth.cookie_name(auth.SESSION_COOKIE, settings),
        token,
        max_age=settings.max_session_hours * 3600,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    if fresh_device_token:
        response.set_cookie(
            auth.cookie_name(auth.DEVICE_COOKIE, settings),
            fresh_device_token,
            max_age=400 * 86400,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
        )
    return {"user": user_json(user), "csrf": session.csrf_token}


def enrollment(db: DbSession, invite: Invite, name: str, email: str, password: str, role: str) -> dict:
    problem = auth.password_problem(password)
    if problem:
        raise HTTPException(422, problem)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(409, "Someone already signs in with that email.")
    secret = auth.new_totp_secret()
    uri = auth.totp_uri(secret, email)
    token = auth.new_challenge(
        db,
        "enroll",
        invite_id=invite.id,
        name=name,
        email=email,
        role=role,
        password_hash=auth.hash_password(password),
        totp_enc=crypto.encrypt(secret, "totp"),
    )
    # Generous time to find the authenticator app.
    c = auth.find_challenge(db, token, "enroll")
    if c is not None:
        c.expires_at = now() + timedelta(minutes=20)
    qr = segno.make(uri, error="m").svg_data_uri(scale=5, border=2)
    return {"token": token, "totp_uri": uri, "secret": secret, "qr": qr}


def need_key() -> None:
    if not crypto.key_configured():
        raise HTTPException(503, "ENCRYPTION_KEY isn't set on the server, so nothing secret can be stored yet.")


# ---- state


@router.get("/state")
def state(request: Request) -> dict:
    settings = settings_of(request)
    if settings.demo_mode:
        return {"demo": True, "setup_required": False, "user": user_json(DEMO_USER), "csrf": "demo"}
    db_ = request.app.state.db
    if db_ is None:
        return {"demo": False, "setup_required": False, "user": None, "csrf": None}
    with db_.session() as db:
        setup_required = db.scalar(select(User.id).limit(1)) is None
        s = auth.find_session(db, request.cookies.get(auth.cookie_name(auth.SESSION_COOKIE, settings)), settings)
        user = db.get(User, s.user_id) if s else None
        return {
            "demo": False,
            "setup_required": setup_required,
            "user": user_json(user) if user else None,
            "csrf": s.csrf_token if s and user else None,
            "idle_minutes": settings.session_idle_minutes,
        }


# ---- first run and invitations


@router.post("/setup", dependencies=[Depends(rate_limited)])
def setup(body: SetupIn, request: Request, db: DbSession = Depends(get_db)) -> dict:
    need_key()
    invite = auth.find_invite(db, body.code, "setup")
    if invite is None or db.scalar(select(User.id).limit(1)) is not None:
        audit(db, request, "setup_code_rejected")
        db.commit()
        raise HTTPException(403, "That setup code isn't right, or has expired. Restart the app for a new one.")
    return enrollment(db, invite, body.name.strip(), clean_email(body.email), body.password, "owner")


@router.post("/invite/check", dependencies=[Depends(rate_limited)])
def invite_check(body: CodeIn, db: DbSession = Depends(get_db)) -> dict:
    invite = auth.find_invite(db, body.code, "invite")
    if invite is None:
        raise HTTPException(404, "That invitation isn't valid any more. Ask for a new one.")
    return {"name": invite.name}


@router.post("/invite/accept", dependencies=[Depends(rate_limited)])
def invite_accept(body: InviteAcceptIn, request: Request, db: DbSession = Depends(get_db)) -> dict:
    need_key()
    invite = auth.find_invite(db, body.code, "invite")
    if invite is None:
        raise HTTPException(404, "That invitation isn't valid any more. Ask for a new one.")
    return enrollment(db, invite, invite.name or "", clean_email(body.email), body.password, "member")


@router.post("/enroll", dependencies=[Depends(rate_limited)])
def enroll(body: TokenCodeIn, request: Request, response: Response, db: DbSession = Depends(get_db)) -> dict:
    c = auth.find_challenge(db, body.token, "enroll")
    if c is None:
        raise HTTPException(410, "This step expired. Start again.")
    secret = crypto.decrypt(c.data["totp_enc"], "totp")
    step = auth.totp_step(secret, body.code, None)
    if step is None:
        c.attempts += 1
        db.commit()
        raise HTTPException(401, "That code didn't match. Check the time on your phone is set automatically.")
    invite = db.get(Invite, c.data["invite_id"])
    if invite is None or invite.used_at is not None or invite.expires_at < now():
        raise HTTPException(410, "That code has expired. Start again.")
    if db.scalar(select(func.count()).select_from(User)) >= auth.MAX_USERS:
        raise HTTPException(409, "This household already has the most people it can.")
    if db.scalar(select(User.id).where(User.email == c.data["email"])) is not None:
        raise HTTPException(409, "Someone already signs in with that email.")
    user = User(
        email=c.data["email"],
        name=c.data["name"],
        role=c.data["role"],
        password_hash=c.data["password_hash"],
        totp_secret_enc=c.data["totp_enc"],
        totp_last_step=step,
        webauthn_user_id=secrets.token_bytes(32),
    )
    db.add(user)
    invite.used_at = now()
    db.delete(c)
    db.flush()
    audit(db, request, "account_created", user.id, role=user.role, via=invite.kind)
    return start_session(db, request, response, user, "password")


# ---- signing in with a password and code


@router.post("/login", dependencies=[Depends(rate_limited)])
def login(body: LoginIn, request: Request, db: DbSession = Depends(get_db)) -> dict:
    settings = settings_of(request)
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is not None and auth.locked(user):
        audit(db, request, "signin_locked", user.id)
        db.commit()
        raise HTTPException(
            423, "Too many wrong tries, so password sign-in is paused for a few minutes. Your passkey still works."
        )
    if not auth.check_password(user, body.password):
        if user is not None:
            just_locked = auth.record_failure(db, user, settings)
            audit(db, request, "signin_failed", user.id, step="password", locked=just_locked)
            if just_locked:
                notify.send(
                    settings,
                    "Sign-in locked",
                    f"Password sign-in for {user.name} was paused after several wrong tries.",
                )
        else:
            audit(db, request, "signin_failed", None, step="password")
        db.commit()
        raise HTTPException(401, "That email and password don't match.")
    return {"next": "code", "token": auth.new_challenge(db, "code", user.id)}


@router.post("/login/code", dependencies=[Depends(rate_limited)])
def login_code(body: TokenCodeIn, request: Request, response: Response, db: DbSession = Depends(get_db)) -> dict:
    settings = settings_of(request)
    c = auth.find_challenge(db, body.token, "code")
    user = db.get(User, c.user_id) if c and c.user_id else None
    if c is None or user is None:
        raise HTTPException(410, "This step expired. Sign in again.")
    if auth.locked(user):
        raise HTTPException(423, "Too many wrong tries, so sign-in is paused for a few minutes.")
    if not auth.check_totp(db, user, body.code):
        c.attempts += 1
        just_locked = auth.record_failure(db, user, settings)
        audit(db, request, "signin_failed", user.id, step="code", locked=just_locked)
        db.commit()
        raise HTTPException(401, "That code didn't match. Codes change every 30 seconds.")
    db.delete(c)
    return start_session(db, request, response, user, "password")


# ---- passkeys


@router.post("/passkey/options", dependencies=[Depends(rate_limited)])
def passkey_options(body: PasskeyOptionsIn, request: Request, db: DbSession = Depends(get_db)) -> dict:
    rp_id, _ = relying_party(request)
    allow = None
    user_id = None
    if body.purpose == "reauth":
        settings = settings_of(request)
        s = auth.find_session(db, request.cookies.get(auth.cookie_name(auth.SESSION_COOKIE, settings)), settings)
        if s is None:
            raise HTTPException(401, "Sign in to continue.")
        user_id = s.user_id
        allow = [
            PublicKeyCredentialDescriptor(id=p.credential_id)
            for p in db.scalars(select(Passkey).where(Passkey.user_id == s.user_id))
        ]
    options = webauthn.generate_authentication_options(
        rp_id=rp_id, allow_credentials=allow, user_verification=UserVerificationRequirement.REQUIRED
    )
    token = auth.new_challenge(db, "passkey", user_id, options.challenge)
    return {"token": token, "options": json.loads(webauthn.options_to_json(options))}


@router.post("/passkey/verify", dependencies=[Depends(rate_limited)])
def passkey_verify(body: PasskeyVerifyIn, request: Request, response: Response, db: DbSession = Depends(get_db)):
    settings = settings_of(request)
    c = auth.find_challenge(db, body.token, "passkey")
    if c is None or c.challenge is None:
        raise HTTPException(410, "This step expired. Try again.")
    db.delete(c)
    try:
        credential_id = webauthn.base64url_to_bytes(str(body.credential.get("rawId", "")))
    except Exception:
        credential_id = b""
    passkey = db.scalar(select(Passkey).where(Passkey.credential_id == credential_id))
    if passkey is None:
        audit(db, request, "signin_failed", None, step="passkey", reason="unknown passkey")
        db.commit()
        raise HTTPException(401, "This passkey isn't registered here. Sign in with your password and code.")
    rp_id, origins = relying_party(request)
    user = db.get(User, passkey.user_id)
    try:
        verified = webauthn.verify_authentication_response(
            credential=body.credential,
            expected_challenge=c.challenge,
            expected_rp_id=rp_id,
            expected_origin=origins,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse:
        audit(db, request, "signin_failed", passkey.user_id, step="passkey")
        db.commit()
        raise HTTPException(401, "That passkey couldn't be checked. Try again.") from None
    user_handle = (body.credential.get("response") or {}).get("userHandle")
    if user is None or (user_handle and user_handle != bytes_to_base64url(user.webauthn_user_id)):
        db.commit()
        raise HTTPException(401, "That passkey belongs to someone else.")
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = now()
    if body.purpose == "reauth":
        s = auth.find_session(db, request.cookies.get(auth.cookie_name(auth.SESSION_COOKIE, settings)), settings)
        if s is None or s.user_id != user.id or c.user_id != user.id:
            raise HTTPException(401, "Sign in to continue.")
        s.reauth_at = now()
        audit(db, request, "reauth", user.id, method="passkey")
        return {"ok": True}
    return start_session(db, request, response, user, "passkey")


@router.post("/passkeys/options")
def passkey_register_options(request: Request, who: Signed = Depends(signed_in), db: DbSession = Depends(session_db)):
    rp_id, _ = relying_party(request)
    existing = list(db.scalars(select(Passkey).where(Passkey.user_id == who.user.id)))
    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name="Finance",
        user_name=who.user.email,
        user_display_name=who.user.name,
        user_id=who.user.webauthn_user_id,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=p.credential_id) for p in existing],
    )
    token = auth.new_challenge(db, "passkey_new", who.user.id, options.challenge)
    return {"token": token, "options": json.loads(webauthn.options_to_json(options))}


@router.post("/passkeys")
def passkey_register(
    body: PasskeyRegisterIn, request: Request, who: Signed = Depends(signed_in), db: DbSession = Depends(session_db)
) -> dict:
    c = auth.find_challenge(db, body.token, "passkey_new")
    if c is None or c.challenge is None or c.user_id != who.user.id:
        raise HTTPException(410, "This step expired. Try again.")
    db.delete(c)
    rp_id, origins = relying_party(request)
    try:
        v = webauthn.verify_registration_response(
            credential=body.credential,
            expected_challenge=c.challenge,
            expected_rp_id=rp_id,
            expected_origin=origins,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse:
        raise HTTPException(400, "That passkey couldn't be added. Try again.") from None
    if db.scalar(select(Passkey.id).where(Passkey.credential_id == v.credential_id)) is not None:
        raise HTTPException(409, "That passkey is already added.")
    transports = (body.credential.get("response") or {}).get("transports") or []
    name = body.name.strip() or auth.device_label(request.headers.get("user-agent", ""))
    p = Passkey(
        user_id=who.user.id,
        credential_id=v.credential_id,
        public_key=v.credential_public_key,
        sign_count=v.sign_count,
        transports=[str(t)[:16] for t in transports][:6],
        name=name,
        backed_up=bool(v.credential_backed_up),
    )
    db.add(p)
    db.flush()
    audit(db, request, "passkey_added", who.user.id, name=name)
    return {"id": p.id, "name": p.name}


@router.get("/passkeys")
def passkeys(request: Request, who: Signed = Depends(signed_in)) -> list[dict]:
    if who.demo:
        return [{"id": 1, "name": "iPhone", "created_at": now().isoformat(), "last_used_at": now().isoformat()}]
    db: DbSession = request.state.db
    return [
        {
            "id": p.id,
            "name": p.name,
            "created_at": p.created_at.isoformat(),
            "last_used_at": auth.utc_iso(p.last_used_at),
        }
        for p in db.scalars(select(Passkey).where(Passkey.user_id == who.user.id).order_by(Passkey.created_at))
    ]


@router.delete("/passkeys/{passkey_id}")
def passkey_remove(passkey_id: int, request: Request, who: Signed = Depends(recent)) -> dict:
    db: DbSession = request.state.db
    p = db.get(Passkey, passkey_id)
    if p is None or p.user_id != who.user.id:
        raise HTTPException(404, "No such passkey.")
    db.delete(p)
    audit(db, request, "passkey_removed", who.user.id, name=p.name)
    return {"ok": True}


# ---- confirming it's you again, before something sensitive


@router.post("/reauth", dependencies=[Depends(rate_limited)])
def reauth(body: CodeIn, request: Request, who: Signed = Depends(signed_in)) -> dict:
    db: DbSession = request.state.db
    settings = settings_of(request)
    if auth.locked(who.user):
        raise HTTPException(423, "Too many wrong tries. Use your passkey, or wait a few minutes.")
    if not auth.check_totp(db, who.user, body.code):
        just_locked = auth.record_failure(db, who.user, settings)
        audit(db, request, "reauth_failed", who.user.id, locked=just_locked)
        db.commit()
        raise HTTPException(401, "That code didn't match.")
    live(who).reauth_at = now()
    who.user.failed_attempts = 0
    audit(db, request, "reauth", who.user.id, method="code")
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordIn, request: Request, who: Signed = Depends(recent)) -> dict:
    db: DbSession = request.state.db
    problem = auth.password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    who.user.password_hash = auth.hash_password(body.password)
    # Every other browser has to sign in again with the new password.
    db.execute(delete(Session).where(Session.user_id == who.user.id, Session.id != live(who).id))
    audit(db, request, "password_changed", who.user.id)
    return {"ok": True}


# ---- sessions


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    settings = settings_of(request)
    name = auth.cookie_name(auth.SESSION_COOKIE, settings)
    db_ = request.app.state.db
    token = request.cookies.get(name)
    if db_ is not None and token:
        with db_.session() as db:
            s = db.scalar(select(Session).where(Session.token_hash == auth.digest(token)))
            if s is not None:
                audit(db, request, "signout", s.user_id)
                db.delete(s)
    response.delete_cookie(name, path="/", secure=settings.secure_cookies, httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/sessions")
def sessions(request: Request, who: Signed = Depends(signed_in)) -> list[dict]:
    if who.demo:
        t = now().isoformat()
        return [
            {
                "id": 1,
                "device": "Safari on iPhone",
                "method": "passkey",
                "created_at": t,
                "last_seen_at": t,
                "current": True,
            }
        ]
    db: DbSession = request.state.db
    rows = db.execute(
        select(Session, Device.label)
        .outerjoin(Device, Device.id == Session.device_id)
        .where(Session.user_id == who.user.id, Session.expires_at > now())
        .order_by(Session.last_seen_at.desc())
    ).all()
    return [
        {
            "id": s.id,
            "device": label or "Unknown device",
            "method": s.method,
            "created_at": s.created_at.isoformat(),
            "last_seen_at": s.last_seen_at.isoformat(),
            "current": s.id == live(who).id,
        }
        for s, label in rows
    ]


@router.post("/sessions/sign-out-others")
def sign_out_others(request: Request, who: Signed = Depends(signed_in)) -> dict:
    db: DbSession = request.state.db
    n = db.execute(delete(Session).where(Session.user_id == who.user.id, Session.id != live(who).id)).rowcount
    audit(db, request, "signout_others", who.user.id, sessions=n)
    return {"signed_out": n}


# ---- people


@router.get("/people")
def people(request: Request, who: Signed = Depends(signed_in)) -> dict:
    if who.demo:
        return {
            "people": [
                {
                    "id": 0,
                    "name": "Sample",
                    "email": "sample@example.com",
                    "role": "owner",
                    "passkeys": 1,
                    "last_login_at": now().isoformat(),
                }
            ],
            "invites": [],
        }
    db: DbSession = request.state.db
    counts = dict(db.execute(select(Passkey.user_id, func.count()).group_by(Passkey.user_id)).all())
    out = [
        {**user_json(u), "passkeys": counts.get(u.id, 0), "last_login_at": auth.utc_iso(u.last_login_at)}
        for u in db.scalars(select(User).order_by(User.id))
    ]
    invites = []
    if who.user.role == "owner":
        invites = [
            {"id": i.id, "name": i.name, "expires_at": i.expires_at.isoformat()}
            for i in db.scalars(
                select(Invite).where(Invite.kind == "invite", Invite.used_at.is_(None), Invite.expires_at > now())
            )
        ]
    return {"people": out, "invites": invites}


@router.post("/people/invite")
def invite(body: InviteIn, request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)) -> dict:
    db: DbSession = request.state.db
    if db.scalar(select(func.count()).select_from(User)) >= auth.MAX_USERS:
        raise HTTPException(409, "This household already has the most people it can.")
    code = auth.new_code()
    expires = now() + timedelta(hours=auth.INVITE_HOURS)
    db.add(
        Invite(
            kind="invite",
            code_hash=auth.digest(code),
            name=body.name.strip(),
            created_by=who.user.id,
            expires_at=expires,
        )
    )
    audit(db, request, "invite_created", who.user.id, name=body.name.strip())
    return {"code": code, "path": f"/welcome?code={code}", "expires_at": expires.isoformat()}


@router.delete("/people/invites/{invite_id}")
def cancel_invite(invite_id: int, request: Request, who: Signed = Depends(owner)) -> dict:
    db: DbSession = request.state.db
    inv = db.get(Invite, invite_id)
    if inv is None or inv.kind != "invite":
        raise HTTPException(404, "No such invitation.")
    db.delete(inv)
    audit(db, request, "invite_cancelled", who.user.id, name=inv.name)
    return {"ok": True}


@router.delete("/people/{user_id}")
def remove_person(user_id: int, request: Request, who: Signed = Depends(recent), _: Signed = Depends(owner)) -> dict:
    db: DbSession = request.state.db
    if user_id == who.user.id:
        raise HTTPException(400, "You can't remove yourself.")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "No such person.")
    db.delete(u)
    audit(db, request, "person_removed", who.user.id, name=u.name)
    return {"ok": True}


# ---- audit log


ACTION_TEXT = {
    "signin": "Signed in",
    "signin_failed": "Sign-in failed",
    "signin_locked": "Sign-in blocked (locked)",
    "signin_new_device": "Signed in from a new device",
    "signout": "Signed out",
    "signout_others": "Signed out other devices",
    "account_created": "Account created",
    "setup_code_rejected": "Wrong setup code",
    "passkey_added": "Passkey added",
    "passkey_removed": "Passkey removed",
    "reauth": "Confirmed identity",
    "reauth_failed": "Identity check failed",
    "password_changed": "Password changed",
    "invite_created": "Invitation created",
    "invite_cancelled": "Invitation cancelled",
    "person_removed": "Person removed",
    "connection_added": "Account connected",
    "connection_removed": "Connection removed",
    "connection_stale": "Connection stopped working",
    "revoke_all": "Revoked every connection",
    "sync": "Synced",
    "import": "File imported",
    "recategorized": "Category changed",
}


@router.get("/audit")
def audit_log(request: Request, who: Signed = Depends(signed_in), limit: int = 50) -> list[dict]:
    if who.demo:
        return [
            {
                "id": 1,
                "at": now().isoformat(),
                "who": "Sample",
                "action": "signin",
                "text": "Signed in",
                "detail": {"method": "passkey", "device": "Safari on iPhone"},
            }
        ]
    db: DbSession = request.state.db
    q = select(AuditEvent, User.name).outerjoin(User, User.id == AuditEvent.user_id)
    if who.user.role != "owner":
        q = q.where(AuditEvent.user_id == who.user.id)
    rows = db.execute(q.order_by(AuditEvent.at.desc()).limit(max(1, min(limit, 200)))).all()
    return [
        {
            "id": e.id,
            "at": e.at.isoformat(),
            "who": name,
            "action": e.action,
            "text": ACTION_TEXT.get(e.action, e.action.replace("_", " ").capitalize()),
            "detail": e.detail,
        }
        for e, name in rows
    ]
