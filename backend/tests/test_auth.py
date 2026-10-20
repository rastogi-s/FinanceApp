from datetime import timedelta

from sqlalchemy import select, update

from financeapp import auth, crypto, notify
from financeapp.models import AuditEvent, Passkey, Session, User, now
from tests.conftest import PASSWORD, SoftAuthenticator, csrf, db_of, needs_db

pytestmark = needs_db


def test_fresh_install_needs_the_setup_code(client, home):
    assert client.get("/api/auth/state").json()["setup_required"] is True
    assert client.get("/api/household").status_code == 401
    r = client.post(
        "/api/auth/setup", json={"code": "AAAA-BBBB-CCCC", "name": "x", "email": "a@b.c", "password": PASSWORD}
    )
    assert r.status_code == 403
    me = home.setup()
    assert me["user"]["role"] == "owner"
    state = client.get("/api/auth/state").json()
    assert state["setup_required"] is False and state["user"]["email"] == "me@example.com"
    # The setup code works once.
    with db_of(client).session() as db:
        assert auth.issue_setup_code(db) is None


def test_setup_rejects_weak_passwords_and_wrong_codes(client, home, clock):
    with db_of(client).session() as db:
        code = auth.issue_setup_code(db)
    r = client.post("/api/auth/setup", json={"code": code, "name": "A", "email": "a@b.co", "password": "short"})
    assert r.status_code == 422
    r = client.post(
        "/api/auth/setup",
        json={"code": code.lower().replace("-", ""), "name": "A", "email": "a@b.co", "password": PASSWORD},
    )
    assert r.status_code == 200  # codes are forgiving about case and dashes
    enroll = r.json()
    assert enroll["qr"].startswith("data:image/svg+xml")
    assert client.post("/api/auth/enroll", json={"token": enroll["token"], "code": "000000"}).status_code == 401
    assert client.get("/api/auth/state").json()["user"] is None


def test_cookies_are_locked_down(client, home):
    home.setup()
    cookies = {c.name: c for c in client.cookies.jar}
    session = cookies["__Host-fa_session"]
    assert session.secure and session.path == "/"
    assert session.has_nonstandard_attr("HttpOnly")
    assert session.get_nonstandard_attr("SameSite").lower() == "strict"
    with db_of(client).session() as db:
        stored = db.scalar(select(Session))
        assert stored.token_hash != session.value.encode()  # only the hash is stored
        assert stored.token_hash == auth.digest(session.value)


def test_password_and_code_sign_in(client, home):
    home.setup()
    client.post("/api/auth/logout", headers=csrf(client))
    assert client.get("/api/auth/state").json()["user"] is None
    r = client.post("/api/auth/login", json={"email": "me@example.com", "password": "wrong password!!"})
    assert r.status_code == 401
    me = home.login()
    assert me["user"]["name"] == "Shubham"
    assert client.get("/api/household").status_code == 200


def test_a_code_works_only_once(client, home):
    home.setup()
    client.post("/api/auth/logout", headers=csrf(client))
    code = home.code()
    for expected in (200, 401):
        t = client.post("/api/auth/login", json={"email": "me@example.com", "password": PASSWORD}).json()["token"]
        assert client.post("/api/auth/login/code", json={"token": t, "code": code}).status_code == expected


def test_five_wrong_tries_lock_the_account(client, home, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda s, title, body, tags="lock": sent.append(title))
    home.setup()
    client.post("/api/auth/logout", headers=csrf(client))
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": "me@example.com", "password": "not the password"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "me@example.com", "password": PASSWORD})
    assert r.status_code == 423  # even the right password waits
    assert sent == ["Sign-in locked"]
    with db_of(client).session() as db:
        db.execute(update(User).values(locked_until=now() - timedelta(seconds=1)))
    home.login()


def test_unknown_emails_and_wrong_passwords_look_the_same(client, home):
    home.setup()
    a = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    b = client.post("/api/auth/login", json={"email": "me@example.com", "password": "wrong password!!"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_changes_need_the_csrf_token_and_same_origin(client, home):
    home.setup()
    body = {"name": "Spouse"}
    assert client.post("/api/auth/people/invite", json=body).status_code == 403
    assert client.post("/api/auth/people/invite", json=body, headers={"X-CSRF-Token": "nope"}).status_code == 403
    cross = {**csrf(client), "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/auth/people/invite", json=body, headers=cross).status_code == 403
    assert client.post("/api/auth/people/invite", json=body, headers=csrf(client)).status_code == 200


def test_sessions_end_when_idle(client, home):
    home.setup()
    assert client.get("/api/household").status_code == 200
    with db_of(client).session() as db:
        db.execute(update(Session).values(last_seen_at=now() - timedelta(minutes=16)))
    assert client.get("/api/household").status_code == 401


def test_sensitive_actions_need_a_recent_check(client, home):
    home.setup()
    with db_of(client).session() as db:
        db.execute(update(Session).values(reauth_at=now() - timedelta(minutes=30)))
    r = client.post("/api/auth/people/invite", json={"name": "Spouse"}, headers=csrf(client))
    assert r.status_code == 403 and r.json()["detail"] == "reauth_required"
    assert client.post("/api/auth/reauth", json={"code": "000000"}, headers=csrf(client)).status_code == 401
    assert client.post("/api/auth/reauth", json={"code": home.code()}, headers=csrf(client)).status_code == 200
    assert client.post("/api/auth/people/invite", json={"name": "Spouse"}, headers=csrf(client)).status_code == 200


def test_inviting_a_second_person(client, home, tmp_path):
    home.setup()
    r = client.post("/api/auth/people/invite", json={"name": "Priya"}, headers=csrf(client))
    code = r.json()["code"]
    client.post("/api/auth/logout", headers=csrf(client))
    client.cookies.clear()
    assert client.post("/api/auth/invite/check", json={"code": code}).json() == {"name": "Priya"}
    r = client.post(
        "/api/auth/invite/accept",
        json={"code": code, "email": "priya@example.com", "password": "another long passphrase"},
    )
    me = home.enroll(r.json(), "priya@example.com")
    assert me["user"] == {"id": me["user"]["id"], "name": "Priya", "email": "priya@example.com", "role": "member"}
    # Members can't invite or remove people, and the invitation is used up.
    assert client.post("/api/auth/people/invite", json={"name": "X"}, headers=csrf(client)).status_code == 403
    assert client.post("/api/auth/invite/check", json={"code": code}).status_code == 404
    people = client.get("/api/auth/people").json()["people"]
    assert [p["name"] for p in people] == ["Shubham", "Priya"]


def test_a_new_device_sends_an_alert(client, home, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda s, title, body, tags="lock": sent.append(title))
    home.setup()
    client.post("/api/auth/logout", headers=csrf(client))
    home.login()  # same browser: no alert
    assert sent == []
    client.post("/api/auth/logout", headers=csrf(client))
    client.cookies.clear()  # a different browser
    home.login()
    assert sent == ["New sign-in"]
    with db_of(client).session() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "signin_new_device")) is not None


def test_passkeys(client, home):
    home.setup()
    key = SoftAuthenticator()
    r = client.post("/api/auth/passkeys/options", headers=csrf(client)).json()
    assert r["options"]["authenticatorSelection"]["residentKey"] == "required"
    assert r["options"]["authenticatorSelection"]["userVerification"] == "required"
    added = client.post(
        "/api/auth/passkeys",
        json={"token": r["token"], "credential": key.create(r["options"]), "name": "iPhone"},
        headers=csrf(client),
    )
    assert added.status_code == 200, added.text
    client.post("/api/auth/logout", headers=csrf(client))

    # Sign in with nothing but the passkey.
    r = client.post("/api/auth/passkey/options", json={}).json()
    assert "allowCredentials" not in r["options"] or r["options"]["allowCredentials"] == []
    me = client.post("/api/auth/passkey/verify", json={"token": r["token"], "credential": key.get(r["options"])})
    assert me.status_code == 200, me.text
    assert client.get("/api/auth/state").json()["user"]["email"] == "me@example.com"

    # The same challenge can't be replayed, and a passkey from elsewhere is refused.
    replay = client.post("/api/auth/passkey/verify", json={"token": r["token"], "credential": key.get(r["options"])})
    assert replay.status_code == 410
    r = client.post("/api/auth/passkey/options", json={}).json()
    stranger = SoftAuthenticator()
    stranger.user_handle = b"x"
    bad = client.post("/api/auth/passkey/verify", json={"token": r["token"], "credential": stranger.get(r["options"])})
    assert bad.status_code == 401

    # A passkey made for another site doesn't work here.
    phish = SoftAuthenticator(rp_id="evil.example", origin="https://evil.example")
    phish.credential_id = key.credential_id
    phish.key = key.key
    phish.user_handle = key.user_handle
    r = client.post("/api/auth/passkey/options", json={}).json()
    bad = client.post("/api/auth/passkey/verify", json={"token": r["token"], "credential": phish.get(r["options"])})
    assert bad.status_code == 401
    with db_of(client).session() as db:
        assert db.scalar(select(Passkey)).sign_count == 1  # only the one real sign-in counted


def test_secrets_are_encrypted_at_rest(client, home):
    home.setup()
    with db_of(client).session() as db:
        user = db.scalar(select(User))
        assert user.totp_secret_enc.startswith("v1:")
        assert home.secrets["me@example.com"] not in user.totp_secret_enc
        assert crypto.decrypt(user.totp_secret_enc, "totp") == home.secrets["me@example.com"]
        assert user.password_hash.startswith("$argon2id$")
    try:
        crypto.decrypt(user.totp_secret_enc, "connection:plaid")
        raise AssertionError("decrypted under the wrong purpose")
    except crypto.DecryptError:
        pass


def test_audit_log_records_sign_ins(client, home):
    home.setup()
    actions = [e["action"] for e in client.get("/api/auth/audit").json()]
    assert "account_created" in actions and "signin" in actions


def test_health_needs_the_encryption_key(tmp_path, fresh_db):
    from tests.conftest import make_client

    with make_client(tmp_path, fresh_db, encryption_key="") as c:
        r = c.get("/health")
        assert r.status_code == 503 and r.json()["encryption_key"] == "missing"
