import asyncio
import json
import os
import re
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bcrypt
import psycopg
import pytest
from fastapi.testclient import TestClient

import admin_security
import fastapi_app as app


@pytest.fixture(autouse=True)
def _default_to_smtp_mail(monkeypatch):
    for name in ("BREVO_API_KEY", "EMAIL_SENDER", "EMAIL_SENDER_NAME"):
        monkeypatch.delenv(name, raising=False)


class Request:
    def __init__(self, payload, session=None):
        self.payload = payload
        self.session = {} if session is None else session
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self.payload


class StoreCursor:
    def __init__(self, store):
        self.store = store
        self.cursor = store.connection.cursor()

    def execute(self, query, params=()):
        if self.store.postgres:
            query = query.replace("?", "%s")
        else:
            query = re.sub(r"\s+FOR UPDATE(?: OF s)?", "", query)
            params = tuple(value.isoformat() if isinstance(value, datetime) else value for value in params)
        try:
            self.cursor.execute(query, params)
        except sqlite3.IntegrityError as exc:
            raise psycopg.errors.UniqueViolation(str(exc)) from exc

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class Store:
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres

    def cursor(self):
        return StoreCursor(self)

    def execute(self, query, params=()):
        cursor = self.cursor()
        cursor.execute(query, params)
        return cursor

    def commit(self):
        self.connection.commit()

    def close(self):
        # Application connections close after each request; keep only the isolated test database.
        self.connection.rollback()


@pytest.fixture(scope="module")
def auth_store():
    postgres = os.getenv("RUN_POSTGRES_AUTH_TESTS") == "1"
    if postgres:
        connection = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    else:
        sqlite3.register_converter("TIMESTAMPTZ", lambda value: datetime.fromisoformat(value.decode()))
        connection = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    store = Store(connection, postgres)
    temporary = "TEMP" if postgres else ""
    store.execute(f"""
        CREATE {temporary} TABLE users (
            id INTEGER PRIMARY KEY, name TEXT, username TEXT, email TEXT,
            password_hash TEXT, role TEXT, profile_picture TEXT DEFAULT 'default.svg',
            is_active BOOLEAN DEFAULT TRUE, must_change_password BOOLEAN DEFAULT FALSE,
            last_login_at TIMESTAMPTZ, auth_version INTEGER NOT NULL DEFAULT 0
        )
    """)
    schema = admin_security.SECURITY_SCHEMA
    if postgres:
        schema = schema.replace("CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE")
    store.execute(schema)
    store.commit()
    yield store
    connection.close()


@pytest.fixture
def auth(monkeypatch, auth_store):
    store = auth_store
    store.execute("DELETE FROM admin_account_security")
    store.execute("DELETE FROM users")
    fast_hash = lambda password: bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    for user_id, role in [(1, "admin"), (2, "semi_admin"), (3, "student")]:
        store.execute(
            "INSERT INTO users (id, name, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, role, role, f"{role}@example.com", fast_hash("OldPassword!"), role),
        )
    store.commit()
    now = [datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)]
    sent = []
    monkeypatch.setattr(app, "_db_conn", lambda: store)
    monkeypatch.setattr(app, "_hash_password", fast_hash)
    monkeypatch.setattr(app, "utc_now", lambda: now[0])
    monkeypatch.setattr(app, "_record_admin_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_record_student_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "send_security_email", lambda address, token, purpose: sent.append((address, token, purpose)))
    monkeypatch.setenv("SMTP_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "test-app-password")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5000")
    return SimpleNamespace(store=store, now=now, sent=sent)


def call(handler, payload, session=None):
    return asyncio.run(handler(Request(payload, session)))


def body(response):
    return json.loads(response.body)


def login(username="admin", password="OldPassword!", session=None):
    return call(app.login, {"username": username, "password": password}, session)


def set_recovery(email="recovery@gmail.com", user_id=1):
    return call(
        app.admin_set_recovery_email,
        {"email": email, "current_password": "OldPassword!"},
        {"user_id": user_id, "role": "admin"},
    )


def verify_recovery(auth, user_id=1, email="recovery@gmail.com"):
    assert body(set_recovery(email, user_id))["success"]
    token = auth.sent[-1][1]
    assert body(call(app.admin_verify_recovery, {"token": token}))["success"]
    return token


def reset_payload(token, password="NewPassword1!"):
    return {"token": token, "new_password": password, "confirm_password": password}


@pytest.mark.parametrize("role", ["admin", "semi_admin"])
def test_three_failures_escalate_and_block_correct_password(auth, role):
    for expected in [30, 60, 120, 240, 480, 900, 900]:
        assert login(role, "wrong").status_code == 200
        assert "1 attempt(s)" in body(login(role, "wrong"))["message"]
        response = login(role, "wrong")
        assert response.status_code == 429
        assert body(response)["retry_after"] == expected
        assert response.headers["retry-after"] == str(expected)
        assert body(login(f"{role}@example.com"))["retry_after"] == expected
        auth.now[0] += timedelta(seconds=expected - 1)
        assert body(login(role))["retry_after"] == 1
        auth.now[0] += timedelta(seconds=1)
    assert body(login(role))["success"]
    row = auth.store.execute("SELECT failed_attempts, lockout_level, locked_until FROM admin_account_security WHERE user_id = ?", (1 if role == "admin" else 2,)).fetchone()
    assert row == (0, 0, None)


def test_success_clears_partial_failures_and_student_login_is_unchanged(auth):
    login("admin", "wrong")
    login("admin", "wrong")
    assert body(login())["success"]
    assert login("admin", "wrong").status_code == 200
    for _ in range(10):
        assert body(login("student", "wrong"))["message"] == "Incorrect Password"
    assert body(login("student"))["success"]
    assert auth.store.execute("SELECT user_id FROM admin_account_security WHERE user_id = 3").fetchone() is None


def test_recovery_requires_current_password_and_verified_gmail(auth):
    assert set_recovery("invalid@example.com").status_code == 400
    response = call(app.admin_set_recovery_email, {"email": "recovery@gmail.com", "current_password": "wrong"}, {"user_id": 1})
    assert response.status_code == 400
    assert not auth.sent
    assert body(set_recovery())["success"]
    row = auth.store.execute("SELECT recovery_email, pending_email, verification_hash, verification_expires_at FROM admin_account_security WHERE user_id = 1").fetchone()
    assert row[0] is None
    assert row[1] == "recovery@gmail.com"
    assert row[2] == admin_security.token_hash(auth.sent[-1][1])
    assert row[2] != auth.sent[-1][1]
    assert row[3] == auth.now[0] + timedelta(minutes=30)
    assert set_recovery().status_code == 429
    call(app.admin_forgot_password, {"username": "admin"})
    assert len(auth.sent) == 1


def test_replacement_does_not_replace_verified_email_until_confirmed(auth):
    old_token = verify_recovery(auth)
    assert call(app.admin_verify_recovery, {"token": old_token}).status_code == 400
    auth.now[0] += timedelta(seconds=60)
    assert body(set_recovery("replacement@gmail.com"))["success"]
    row = auth.store.execute("SELECT recovery_email, pending_email FROM admin_account_security WHERE user_id = 1").fetchone()
    assert row == ("recovery@gmail.com", "replacement@gmail.com")
    call(app.admin_forgot_password, {"username": "admin"})
    assert auth.sent[-1][0] == "recovery@gmail.com"
    new_verification = auth.sent[-2][1]
    assert body(call(app.admin_verify_recovery, {"token": new_verification}))["success"]
    assert call(app.admin_reset_password, reset_payload(auth.sent[-1][1])).status_code == 400


def test_reset_is_single_use_clears_lockout_and_revokes_sessions(auth):
    verify_recovery(auth)
    session = {}
    assert body(login(session=session))["success"]
    for _ in range(3):
        login("admin", "wrong")
    assert body(call(app.admin_forgot_password, {"username": "admin"}))["success"]
    token = auth.sent[-1][1]
    row = auth.store.execute("SELECT reset_hash, reset_expires_at FROM admin_account_security WHERE user_id = 1").fetchone()
    assert row == (admin_security.token_hash(token), auth.now[0] + timedelta(minutes=15))
    assert call(app.admin_verify_recovery, {"token": token}).status_code == 400
    assert body(call(app.admin_reset_password, reset_payload(token)))["success"]
    assert auth.store.execute(
        "SELECT failed_attempts, lockout_level, locked_until FROM admin_account_security WHERE user_id = 1"
    ).fetchone() == (0, 0, None)
    assert not app._is_admin_session(session)
    assert session == {}
    assert call(app.admin_reset_password, reset_payload(token)).status_code == 400
    assert not body(login())["success"]
    new_session = {}
    assert body(login(password="NewPassword1!", session=new_session))["success"]
    assert new_session["auth_version"] == 1
    assert app._is_admin_session(new_session)


@pytest.mark.parametrize("mode,minutes", [("verify", 30), ("reset", 15)])
def test_expired_tokens_are_rejected(auth, mode, minutes):
    verify_recovery(auth) if mode == "reset" else set_recovery()
    if mode == "reset":
        call(app.admin_forgot_password, {"username": "admin"})
    token = auth.sent[-1][1]
    auth.now[0] += timedelta(minutes=minutes)
    handler = app.admin_verify_recovery if mode == "verify" else app.admin_reset_password
    assert call(handler, reset_payload(token)).status_code == 400


@pytest.mark.parametrize("token", [None, "", "invalid", "x" * 44, ["bad"], 42])
def test_malformed_reset_tokens_are_rejected(auth, token):
    assert call(app.admin_reset_password, reset_payload(token)).status_code == 400


def test_unknown_student_deactivated_and_unverified_get_same_reset_response(auth):
    baseline = body(call(app.admin_forgot_password, {"username": "missing"}))
    assert body(call(app.admin_forgot_password, {"username": "student"})) == baseline
    assert body(call(app.admin_forgot_password, {"username": "admin"})) == baseline
    verify_recovery(auth)
    auth.store.execute("UPDATE users SET is_active = FALSE WHERE id = 1")
    auth.store.commit()
    assert body(call(app.admin_forgot_password, {"username": "admin"})) == baseline
    assert len(auth.sent) == 1


def test_reset_request_throttle_and_new_link_invalidates_old(auth):
    verify_recovery(auth)
    call(app.admin_forgot_password, {"username": "admin"})
    old = auth.sent[-1][1]
    call(app.admin_forgot_password, {"username": "admin@example.com"})
    assert len(auth.sent) == 2
    auth.now[0] += timedelta(seconds=60)
    call(app.admin_forgot_password, {"username": "admin"})
    assert len(auth.sent) == 3
    assert call(app.admin_reset_password, reset_payload(old)).status_code == 400
    assert body(call(app.admin_reset_password, reset_payload(auth.sent[-1][1])))["success"]


@pytest.mark.parametrize("role,user_id,identifier", [
    ("admin", 1, "admin"),
    ("admin", 1, "admin@example.com"),
    ("admin", 1, "RECOVERY@GMAIL.COM"),
    ("semi_admin", 2, "semi_admin"),
    ("semi_admin", 2, "semi_admin@example.com"),
    ("semi_admin", 2, "RECOVERY@GMAIL.COM"),
])
def test_reset_request_accepts_username_signin_email_or_verified_gmail(auth, role, user_id, identifier):
    verify_recovery(auth, user_id=user_id)
    response = call(app.admin_forgot_password, {"username": identifier})
    assert body(response)["success"]
    assert len(auth.sent) == 2
    assert auth.sent[-1][0] == "recovery@gmail.com"
    assert auth.sent[-1][2] == "reset"


def test_reset_request_does_not_accept_unverified_pending_gmail(auth):
    assert body(set_recovery())["success"]
    response = call(app.admin_forgot_password, {"username": "recovery@gmail.com"})
    assert body(response)["success"]
    assert len(auth.sent) == 1


def test_signin_identifier_keeps_precedence_over_another_admins_recovery_gmail(auth):
    verify_recovery(auth, user_id=1, email="shared@gmail.com")
    auth.store.execute("UPDATE users SET email = ? WHERE id = ?", ("shared@gmail.com", 2))
    auth.store.commit()
    verify_recovery(auth, user_id=2, email="second@gmail.com")
    call(app.admin_forgot_password, {"username": "shared@gmail.com"})
    assert auth.sent[-1][0] == "second@gmail.com"
    assert auth.sent[-1][2] == "reset"


@pytest.mark.parametrize("other_admin_state", ["not_configured", "pending", "inactive"])
def test_signin_identifier_does_not_fall_through_to_another_recovery_gmail(auth, other_admin_state):
    verify_recovery(auth, user_id=1, email="shared@gmail.com")
    auth.store.execute("UPDATE users SET email = ? WHERE id = ?", ("shared@gmail.com", 2))
    auth.store.commit()
    if other_admin_state == "pending":
        assert body(set_recovery("pending@gmail.com", user_id=2))["success"]
    elif other_admin_state == "inactive":
        verify_recovery(auth, user_id=2, email="second@gmail.com")
        auth.store.execute("UPDATE users SET is_active = FALSE WHERE id = 2")
        auth.store.commit()
    sent_before = len(auth.sent)
    response = call(app.admin_forgot_password, {"username": "shared@gmail.com"})
    assert body(response)["success"]
    assert len(auth.sent) == sent_before


@pytest.mark.parametrize("session,expected", [
    ({"user_id": 1, "role": "admin", "auth_version": 0}, True),
    ({}, False),
    ({"user_id": 2, "role": "semi_admin", "auth_version": 0}, False),
    ({"user_id": 1, "role": "admin", "auth_version": 1}, False),
    ({"user_id": 1, "role": "student", "auth_version": 0}, False),
])
def test_verification_returns_to_dashboard_only_for_current_matching_admin_session(auth, session, expected):
    assert body(set_recovery())["success"]
    response = call(app.admin_verify_recovery, {"token": auth.sent[-1][1]}, session)
    result = body(response)
    assert result["success"]
    assert result["return_to_dashboard"] is expected


def test_new_verification_link_replaces_previous_pending_link(auth):
    set_recovery()
    old_token = auth.sent[-1][1]
    auth.now[0] += timedelta(seconds=60)
    set_recovery("replacement@gmail.com")
    assert call(app.admin_verify_recovery, {"token": old_token}).status_code == 400
    assert body(call(app.admin_verify_recovery, {"token": auth.sent[-1][1]}))["success"]
    assert auth.store.execute(
        "SELECT recovery_email FROM admin_account_security WHERE user_id = 1"
    ).fetchone() == ("replacement@gmail.com",)


def test_reset_link_stops_working_when_admin_is_deactivated(auth):
    verify_recovery(auth)
    call(app.admin_forgot_password, {"username": "admin"})
    auth.store.execute("UPDATE users SET is_active = FALSE WHERE id = 1")
    auth.store.commit()
    assert call(app.admin_reset_password, reset_payload(auth.sent[-1][1])).status_code == 400


@pytest.mark.parametrize("password,confirmation", [
    ("weak", "weak"), ("NewPassword1!", "Different!"),
    ("OldPassword!", "OldPassword!"), ("PASSWORD1!", "PASSWORD1!"),
])
def test_reset_enforces_password_policy_and_confirmation(auth, password, confirmation):
    verify_recovery(auth)
    call(app.admin_forgot_password, {"username": "admin"})
    response = call(app.admin_reset_password, {
        "token": auth.sent[-1][1], "new_password": password, "confirm_password": confirmation,
    })
    assert response.status_code == 400
    assert body(login())["success"]


def test_password_change_invalidates_reset_link(auth):
    verify_recovery(auth)
    call(app.admin_forgot_password, {"username": "admin"})
    token = auth.sent[-1][1]
    response = call(app.admin_change_password, {
        "current_password": "OldPassword!", "new_password": "ChangedPassword1!", "confirm_password": "ChangedPassword1!",
    }, {"user_id": 1, "role": "admin"})
    assert body(response)["success"]
    assert call(app.admin_reset_password, reset_payload(token)).status_code == 400


def test_admin_password_override_invalidates_links_sessions_and_lockout(auth):
    verify_recovery(auth, user_id=2, email="coordinator@gmail.com")
    session = {}
    login("semi_admin", session=session)
    call(app.admin_forgot_password, {"username": "semi_admin"})
    token = auth.sent[-1][1]
    for _ in range(3):
        login("semi_admin", "wrong")
    response = call(app.admin_update_user, {
        "id": 2, "name": "Coordinator", "email": "semi_admin@example.com", "password": "Replacement1!",
    }, {"user_id": 1, "role": "admin"})
    assert body(response)["success"]
    assert not app._is_admin_session(session)
    assert call(app.admin_reset_password, reset_payload(token)).status_code == 400
    result = body(login("semi_admin", "Replacement1!"))
    assert result["success"] and result["must_change_password"]


def test_mail_failure_does_not_replace_existing_reset_link(auth, monkeypatch):
    verify_recovery(auth)
    call(app.admin_forgot_password, {"username": "admin"})
    token = auth.sent[-1][1]
    auth.now[0] += timedelta(seconds=60)
    def fail_mail(*args):
        raise smtplib.SMTPException("test failure")
    monkeypatch.setattr(app, "send_security_email", fail_mail)
    assert call(app.admin_forgot_password, {"username": "admin"}).status_code == 503
    assert body(call(app.admin_reset_password, reset_payload(token)))["success"]


def test_duplicate_verified_recovery_email_is_rejected(auth):
    verify_recovery(auth)
    assert body(set_recovery(user_id=2))["success"]
    assert call(app.admin_verify_recovery, {"token": auth.sent[-1][1]}).status_code == 400


def test_recovery_endpoints_require_admin_and_configuration(auth, monkeypatch):
    assert call(app.admin_set_recovery_email, {"email": "recovery@gmail.com"}).status_code == 401
    assert call(app.admin_set_recovery_email, {"email": "recovery@gmail.com"}, {"user_id": 3}).status_code == 401
    monkeypatch.delenv("SMTP_PASSWORD")
    for response in (call(app.admin_forgot_password, {"username": "admin"}), set_recovery()):
        assert response.status_code == 503
        assert body(response)["code"] == "email_not_configured"
        assert "Google App Password" in body(response)["message"]


def test_http_routes_and_login_recovery_ui(auth):
    with TestClient(app.app) as client:
        page = client.get("/login")
        assert 'id="forgot-password"' in page.text
        assert 'id="admin-recovery-dialog"' in page.text
        assert "verified recovery Gmail" in page.text
        verification_page = client.get("/admin/recovery-verification")
        assert verification_page.status_code == 200
        assert 'id="recovery-link-help"' in verification_page.text
        for path in ("verify-recovery", "reset-password"):
            response = client.post(f"/api/v1/admin/{path}", json={"token": "bad"})
            assert response.status_code == 400
        assert client.get("/api/v1/admin/recovery-email").status_code == 401
        assert client.post("/api/v1/login", json={"username": "admin", "password": "OldPassword!"}).json()["success"]
        assert client.get("/api/v1/admin/recovery-email").json()["recovery_email"] is None
        for expected in [200, 200, 429]:
            response = client.post("/api/v1/login", json={"username": "admin", "password": "wrong"})
            assert response.status_code == expected


def test_forced_password_change_does_not_block_public_recovery(auth):
    auth.store.execute("UPDATE users SET must_change_password = TRUE WHERE id = 2")
    auth.store.commit()
    with TestClient(app.app) as client:
        assert client.post("/api/v1/login", json={"username": "semi_admin", "password": "OldPassword!"}).json()["must_change_password"]
        assert client.get("/login").status_code == 200
        assert client.get("/admin/recovery-verification").status_code == 200
        assert client.post("/api/v1/admin/reset-password", json={"token": "bad"}).status_code == 400
        assert client.post("/api/v1/admin/verify-recovery", json={"token": "bad"}).status_code == 400
        assert client.get("/api/v1/admin/stats").status_code == 403


def test_gmail_smtp_uses_tls_and_fragment_links(monkeypatch):
    messages = []
    calls = []
    class SMTP:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def starttls(self, context):
            calls.append("tls")
        def login(self, username, password):
            calls.append(("login", username))
        def send_message(self, message):
            messages.append(message)
    monkeypatch.setenv("SMTP_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "test-password")
    monkeypatch.setenv("APP_BASE_URL", "https://pathfinder.example")
    monkeypatch.setattr(admin_security.smtplib, "SMTP", SMTP)
    admin_security.send_security_email("recovery@gmail.com", "test-token", "reset")
    assert calls == [("smtp.gmail.com", 587, 15), "tls", ("login", "sender@gmail.com")]
    assert messages[0]["To"] == "recovery@gmail.com"
    assert "https://pathfinder.example/login#reset=test-token" in messages[0].get_content()
    admin_security.send_security_email("recovery@gmail.com", "verification-token", "verify")
    assert "https://pathfinder.example/admin/recovery-verification#verify=verification-token" in messages[-1].get_content()


@pytest.mark.parametrize("origin", ["http://public.example", "https://example.com@evil.example", "https://example.com/path", "https://example.com?next=evil", ""])
def test_mail_configuration_rejects_unsafe_origins(monkeypatch, origin):
    monkeypatch.setenv("SMTP_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "test-password")
    monkeypatch.setenv("APP_BASE_URL", origin)
    with pytest.raises(ValueError):
        admin_security.mail_configuration()


def test_mail_configuration_removes_spaces_from_google_app_password(monkeypatch):
    monkeypatch.setenv("SMTP_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5000")
    assert admin_security.mail_configuration()[1] == "abcdefghijklmnop"


@pytest.mark.parametrize(("exception", "code", "detail"), [
    (smtplib.SMTPAuthenticationError(535, b"private server response"), "smtp_authentication_failed", "16-character Google App Password"),
    (smtplib.SMTPServerDisconnected("private detail"), "smtp_connection_closed", "port 587"),
    (OSError("private detail"), "smtp_connection_failed", "port 587"),
])
def test_mail_delivery_errors_are_actionable_and_do_not_leak_server_details(exception, code, detail):
    response = app._mail_delivery_error(exception)
    result = body(response)
    assert response.status_code == 503
    assert result["code"] == code
    assert detail in result["message"]
    assert "private" not in result["message"]


class _FakeBrevoResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


def _brevo_env(monkeypatch):
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setenv("SMTP_USERNAME", "fallback@gmail.com")
    monkeypatch.setenv("EMAIL_SENDER", "sender@gmail.com")
    monkeypatch.setenv("BREVO_API_KEY", "test-brevo-key")
    monkeypatch.setenv("APP_BASE_URL", "https://pathfinder.example/")


def test_brevo_api_sends_security_email_over_https_without_smtp(monkeypatch):
    _brevo_env(monkeypatch)
    posts = []
    monkeypatch.setattr(admin_security.smtplib, "SMTP", lambda *a, **k: pytest.fail("SMTP must not be used"))
    monkeypatch.setattr(admin_security.httpx, "post", lambda url, **kw: posts.append((url, kw)) or _FakeBrevoResponse(201, {"messageId": "x"}))
    assert admin_security.mail_configuration() == ("sender@gmail.com", "test-brevo-key", "https://pathfinder.example")
    admin_security.send_security_email("recovery@gmail.com", "test-token", "verify")
    url, kwargs = posts[0]
    assert url == "https://api.brevo.com/v3/smtp/email"
    assert kwargs["headers"]["api-key"] == "test-brevo-key"
    assert kwargs["timeout"] == 15
    payload = kwargs["json"]
    assert payload["sender"] == {"email": "sender@gmail.com", "name": "PathFinder"}
    assert payload["to"] == [{"email": "recovery@gmail.com"}]
    assert "https://pathfinder.example/admin/recovery-verification#verify=test-token" in payload["textContent"]
    admin_security.send_email("student@gmail.com", "Results", "plain", "<p>html</p>")
    assert posts[1][1]["json"]["htmlContent"] == "<p>html</p>"


@pytest.mark.parametrize(("status", "code"), [(401, "email_api_unauthorized"), (400, "email_api_rejected"), (500, "email_delivery_failed")])
def test_brevo_api_failures_map_to_actionable_errors(monkeypatch, status, code):
    _brevo_env(monkeypatch)
    monkeypatch.setattr(admin_security.httpx, "post", lambda url, **kw: _FakeBrevoResponse(status, {"code": "unauthorized", "message": "private"}))
    with pytest.raises(admin_security.EmailApiError) as raised:
        admin_security.send_email("student@gmail.com", "Subject", "Body")
    assert raised.value.status_code == status
    response = app._mail_delivery_error(raised.value)
    result = body(response)
    assert result["code"] == code
    assert "private" not in result["message"]
    assert "test-brevo-key" not in result["message"]


def test_brevo_network_failure_is_reported_as_unreachable(monkeypatch):
    _brevo_env(monkeypatch)
    def fail(url, **kw):
        raise admin_security.httpx.ConnectError("private detail")
    monkeypatch.setattr(admin_security.httpx, "post", fail)
    with pytest.raises(admin_security.EmailApiError) as raised:
        admin_security.send_email("student@gmail.com", "Subject", "Body")
    assert body(app._mail_delivery_error(raised.value))["code"] == "email_api_unreachable"
