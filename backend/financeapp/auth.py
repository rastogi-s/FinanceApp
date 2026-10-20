"""Sign-in building blocks: password and code checks, sessions, devices, setup codes and rate limits.

The rules (from the plan's security standards):
- Passkeys first; password + TOTP code as the backup. A password alone never signs anyone in.
- Argon2id for passwords. Session tokens, setup and invite codes are random and stored only as SHA-256 hashes.
- 5 wrong passwords or codes in a row lock the account for 15 minutes; every attempt is also rate limited per IP.
- Sessions end after 15 idle minutes or 12 hours, whichever comes first.
- A sign-in from a browser that hasn't signed in before sends an alert.
"""

import hashlib
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as DbSession

from financeapp import crypto
from financeapp.config import Settings
from financeapp.models import Challenge, Device, Invite, Session, User, now

log = logging.getLogger("financeapp")

PASSWORD_MIN = 12
PASSWORD_MAX = 256
TOTP_ISSUER = "Finance"
SESSION_COOKIE = "fa_session"
DEVICE_COOKIE = "fa_device"
CHALLENGE_MINUTES = 5
CHALLENGE_ATTEMPTS = 5
SETUP_CODE_HOURS = 24
INVITE_HOURS = 48
REAUTH_MINUTES = 10
MAX_USERS = 4

_hasher = PasswordHasher()  # Argon2id, the library's (RFC 9106) defaults
# Checked against when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def cookie_name(base: str, settings: Settings) -> str:
    # __Host- cookies are only accepted over HTTPS, for this exact host, at path / (no subdomain can set them).
    return f"__Host-{base}" if settings.secure_cookies else base


def new_token() -> str:
    return secrets.token_urlsafe(32)


def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


# ---- passwords


def password_problem(password: str) -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters. A few unrelated words make a strong, memorable one."
    if len(password) > PASSWORD_MAX:
        return "That password is too long."
    if len(set(password)) < 5:
        return "That password is too easy to guess."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password(user: User | None, password: str) -> bool:
    try:
        return _hasher.verify(user.password_hash if user else _DUMMY_HASH, password) and user is not None
    except (VerificationError, InvalidHashError):
        return False


# ---- TOTP


def new_totp_secret() -> str:
    return pyotp.random_base32(32)


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def clock() -> float:
    return time.time()


def totp_step(secret: str, code: str, last_step: int | None) -> int | None:
    """The time step the code belongs to if it's valid (now, or 30 seconds either side) and not used before."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    current = int(clock()) // 30
    for step in (current, current - 1, current + 1):
        if secrets.compare_digest(totp.generate_otp(step), code) and (last_step is None or step > last_step):
            return step
    return None


def check_totp(db: DbSession, user: User, code: str) -> bool:
    if not user.totp_secret_enc:
        return False
    step = totp_step(crypto.decrypt(user.totp_secret_enc, "totp"), code, user.totp_last_step)
    if step is None:
        return False
    # Compare-and-set, so the same code can't win twice even from two requests at the same moment.
    won = db.execute(
        update(User)
        .where(User.id == user.id, (User.totp_last_step.is_(None)) | (User.totp_last_step < step))
        .values(totp_last_step=step)
    ).rowcount
    return bool(won)


# ---- lockout


def locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > now()


def record_failure(db: DbSession, user: User, settings: Settings) -> bool:
    """Counts a wrong password or code. Returns True if this one locked the account."""
    user.failed_attempts += 1
    if user.failed_attempts >= settings.lockout_failures:
        user.failed_attempts = 0
        user.locked_until = now() + timedelta(minutes=settings.lockout_minutes)
        return True
    return False


def record_success(user: User) -> None:
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now()


# ---- challenges: one short-lived, single-use step of a sign-in


def new_challenge(db: DbSession, kind: str, user_id: int | None = None, challenge: bytes | None = None, **data) -> str:
    token = new_token()
    db.add(
        Challenge(
            token_hash=digest(token),
            kind=kind,
            user_id=user_id,
            challenge=challenge,
            data=data,
            expires_at=now() + timedelta(minutes=CHALLENGE_MINUTES),
        )
    )
    db.execute(delete(Challenge).where(Challenge.expires_at < now()))
    return token


def find_challenge(db: DbSession, token: str, kind: str) -> Challenge | None:
    c = db.scalar(select(Challenge).where(Challenge.token_hash == digest(token), Challenge.kind == kind))
    if c is None or c.expires_at < now() or c.attempts >= CHALLENGE_ATTEMPTS:
        return None
    return c


# ---- sessions and devices


def create_session(
    db: DbSession, user: User, device: Device | None, method: str, settings: Settings
) -> tuple[str, Session]:
    token = new_token()
    s = Session(
        token_hash=digest(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        device_id=device.id if device else None,
        method=method,
        expires_at=now() + timedelta(hours=settings.max_session_hours),
    )
    db.add(s)
    db.flush()
    return token, s


def find_session(db: DbSession, token: str | None, settings: Settings) -> Session | None:
    if not token:
        return None
    s = db.scalar(select(Session).where(Session.token_hash == digest(token)))
    if s is None:
        return None
    t = now()
    if s.expires_at < t or s.last_seen_at < t - timedelta(minutes=settings.session_idle_minutes):
        db.delete(s)
        return None
    if s.last_seen_at < t - timedelta(seconds=30):
        s.last_seen_at = t
    return s


def recently_authenticated(s: Session) -> bool:
    return s.reauth_at > now() - timedelta(minutes=REAUTH_MINUTES)


def device_label(user_agent: str) -> str:
    ua = user_agent or ""
    browser = next((b for b in ("Edg", "Firefox", "CriOS", "Chrome", "Safari") if b in ua), "Browser")
    browser = {"Edg": "Edge", "CriOS": "Chrome"}.get(browser, browser)
    system = next(
        (
            name
            for key, name in (
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Android", "Android"),
                ("Mac OS", "Mac"),
                ("Windows", "Windows"),
                ("Linux", "Linux"),
            )
            if key in ua
        ),
        "unknown device",
    )
    return f"{browser} on {system}"


def known_device(db: DbSession, user: User, token: str | None) -> tuple[Device, str | None, bool]:
    """The device this browser is, making one if it's new. Returns (device, new cookie token or None, is_new)."""
    if token:
        d = db.scalar(select(Device).where(Device.token_hash == digest(token), Device.user_id == user.id))
        if d is not None:
            d.last_seen_at = now()
            return d, None, False
    fresh = new_token()
    d = Device(user_id=user.id, token_hash=digest(fresh), label="")
    db.add(d)
    db.flush()
    return d, fresh, True


# ---- setup and invite codes

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L, U


def new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_code(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def issue_setup_code(db: DbSession) -> str | None:
    """On a fresh install (nobody can sign in yet), replaces any earlier setup code with a new one."""
    if db.scalar(select(User.id).limit(1)) is not None:
        return None
    db.execute(delete(Invite).where(Invite.kind == "setup"))
    code = new_code()
    db.add(Invite(kind="setup", code_hash=digest(code), expires_at=now() + timedelta(hours=SETUP_CODE_HOURS)))
    return code


def find_invite(db: DbSession, code: str, kind: str) -> Invite | None:
    inv = db.scalar(select(Invite).where(Invite.code_hash == digest(normalize_code(code)), Invite.kind == kind))
    if inv is None or inv.used_at is not None or inv.expires_at < now():
        return None
    return inv


# ---- rate limiting (per IP, in memory: the app runs as one process)


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        t = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < t - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(t)
            if len(self.hits) > 10_000:  # don't grow without bound
                self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v})
            return True


def utc_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
