"""Shared test setup. Tests that need Postgres use TEST_DATABASE_URL (CI starts a Postgres service; locally:
createdb finance_test) and are skipped when it isn't reachable."""

import base64
import hashlib
import json
import os
import secrets
import time

import cbor2
import pyotp
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from financeapp import auth
from financeapp.config import Settings
from financeapp.main import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://finance:dev@127.0.0.1:5432/finance_test")
KEY = base64.b64encode(b"k" * 32).decode()
PASSWORD = "correct horse battery staple"


def _reachable() -> bool:
    try:
        with create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 2}).connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        return False


DB_AVAILABLE = _reachable()
needs_db = pytest.mark.skipif(not DB_AVAILABLE, reason="no test database (set TEST_DATABASE_URL)")


@pytest.fixture
def fresh_db():
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as c:
        c.execute(text("drop schema public cascade; create schema public;"))
    engine.dispose()
    return TEST_DATABASE_URL


def make_client(tmp_path, database_url: str, **overrides) -> TestClient:
    (tmp_path / "index.html").write_text("<html><h1>Home</h1></html>")
    options = {"allowed_hosts": "testserver", "encryption_key": KEY, "sync_enabled": False, **overrides}
    settings = Settings(static_dir=tmp_path, database_url=database_url, **options)
    return TestClient(create_app(settings), base_url="https://testserver")


@pytest.fixture
def client(tmp_path, fresh_db):
    with make_client(tmp_path, fresh_db) as c:
        yield c


def db_of(client: TestClient):
    return client.app.state.db


class Clock:
    """A fake clock for TOTP: codes can't be reused, so tests move time forward instead of waiting."""

    def __init__(self) -> None:
        self.t = time.time()

    def code(self, secret: str) -> str:
        return pyotp.TOTP(secret).at(self.t)

    def tick(self) -> None:
        self.t += 30


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(auth, "clock", lambda: c.t)
    return c


class Household:
    """Signs people up and in through the real API, remembering their TOTP secrets."""

    def __init__(self, client: TestClient, clock: Clock) -> None:
        self.client = client
        self.clock = clock
        self.secrets: dict[str, str] = {}

    def code(self, email: str = "me@example.com") -> str:
        self.clock.tick()
        return self.clock.code(self.secrets[email])

    def enroll(self, enroll: dict, email: str) -> dict:
        self.secrets[email] = enroll["secret"]
        r = self.client.post("/api/auth/enroll", json={"token": enroll["token"], "code": self.code(email)})
        assert r.status_code == 200, r.text
        return r.json()

    def setup(self, name="Shubham", email="me@example.com") -> dict:
        with db_of(self.client).session() as db:
            code = auth.issue_setup_code(db)
        r = self.client.post("/api/auth/setup", json={"code": code, "name": name, "email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return self.enroll(r.json(), email)

    def login(self, email="me@example.com", password=PASSWORD) -> dict:
        r = self.client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        r2 = self.client.post("/api/auth/login/code", json={"token": r.json()["token"], "code": self.code(email)})
        assert r2.status_code == 200, r2.text
        return r2.json()


@pytest.fixture
def home(client, clock) -> Household:
    return Household(client, clock)


def csrf(client: TestClient) -> dict:
    token = client.get("/api/auth/state").json()["csrf"]
    return {"X-CSRF-Token": token}


class SoftAuthenticator:
    """A software passkey (ES256, 'none' attestation), standing in for Face ID or a security key."""

    def __init__(self, rp_id: str = "testserver", origin: str = "https://testserver") -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.count = 0
        self.user_handle: bytes | None = None

    @staticmethod
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin}).encode()

    def create(self, options: dict) -> dict:
        self.user_handle = base64.urlsafe_b64decode(options["user"]["id"] + "==")
        nums = self.key.public_key().public_numbers()
        cose = {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x45])  # user present, user verified, attested credential data
            + self.count.to_bytes(4, "big")
            + bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cbor2.dumps(cose)
        )
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.b64(self.credential_id),
            "rawId": self.b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": self.b64(self._client_data("webauthn.create", options["challenge"])),
                "attestationObject": self.b64(att),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict) -> dict:
        self.count += 1
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x05]) + self.count.to_bytes(4, "big")
        sig = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": self.b64(self.credential_id),
            "rawId": self.b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": self.b64(client_data),
                "authenticatorData": self.b64(auth_data),
                "signature": self.b64(sig),
                "userHandle": self.b64(self.user_handle or b""),
            },
            "clientExtensionResults": {},
        }
