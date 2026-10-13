import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import fastapi_app as app


class AuthRequest:
    def __init__(self, payload):
        self.payload = payload
        self.session = {"user_id": 1, "role": "admin"}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self.payload


class AuthCursor:
    def __init__(self, account=None):
        self.account = account
        self.statements = []

    def execute(self, query, params=()):
        self.statements.append((query, params))

    def fetchone(self):
        if "RETURNING id" in self.statements[-1][0]:
            return (77,)
        return self.account


class AuthConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def cursor(self):
        return self.cursor_instance

    def execute(self, query, params=()):
        self.cursor_instance.execute(query, params)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize("identifier", ["coordinator.one", "coordinator@example.com"])
def test_admin_login_accepts_username_or_existing_email(monkeypatch, identifier):
    cursor = AuthCursor((77, "Coordinator", "stored-hash", app.ROLE_SEMI_ADMIN, "default.svg", True, True,
                         "coordinator@example.com", "coordinator.one"))
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(cursor))
    monkeypatch.setattr(app, "_check_password", lambda stored, supplied: True)
    monkeypatch.setattr(app, "_record_admin_activity", lambda *args, **kwargs: None)
    request = AuthRequest({"username": identifier, "password": "StrongPass!"})

    response = asyncio.run(app.login(request))

    assert json.loads(response.body)["must_change_password"] is True
    assert request.session["username"] == "coordinator.one"
    assert cursor.statements[0][1] == (identifier, identifier)


def test_new_admin_account_requires_username_not_email(monkeypatch):
    cursor = AuthCursor()
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(cursor))
    monkeypatch.setattr(app, "_is_full_admin_session", lambda session: True)
    monkeypatch.setattr(app, "_hash_password", lambda password: "hashed-password")
    monkeypatch.setattr(app, "_record_admin_activity", lambda *args, **kwargs: None)

    response = asyncio.run(app.admin_create_user(AuthRequest({
        "name": "New Coordinator", "username": "new.coordinator", "password": "Password!", "role": "semi_admin",
    })))

    assert json.loads(response.body)["success"] is True
    query, params = next((query, params) for query, params in cursor.statements if "INSERT INTO users" in query)
    assert "username" in query and "email" not in query
    assert params[1] == "new.coordinator"
    assert params[-1] is True


@pytest.mark.parametrize("username", ["a", "invalid@email", "1badname"])
def test_admin_creation_rejects_invalid_usernames(monkeypatch, username):
    monkeypatch.setattr(app, "_is_full_admin_session", lambda session: True)

    response = asyncio.run(app.admin_create_user(AuthRequest({
        "name": "Coordinator", "username": username, "password": "Password!", "role": "semi_admin",
    })))

    assert response.status_code == 400


def test_first_login_dashboard_shows_modal_and_blocks_admin_data(monkeypatch):
    secret = app.app.user_middleware[0].kwargs["secret_key"]
    session = {"user_id": 12345, "role": app.ROLE_SEMI_ADMIN, "must_change_password": True, "name": "Coordinator"}
    cookie = TimestampSigner(secret).sign(base64.b64encode(json.dumps(session).encode())).decode()
    monkeypatch.setattr(app, "_is_admin_session", lambda session, allow_password_change=False: allow_password_change)
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(AuthCursor()))

    with TestClient(app.app) as client:
        client.cookies.set("session_", cookie)
        dashboard = client.get("/admin/dashboard", headers={"Accept": "text/html"})
        blocked = client.get("/api/v1/admin/stats", headers={"Accept": "application/json"})
        legacy = client.get("/admin/force-password-change", follow_redirects=False)

    assert dashboard.status_code == 200
    assert '<dialog id="first-password-dialog"' in dashboard.text
    assert 'data-can-view-admin-data="false"' in dashboard.text
    assert 'id="manage-reports"' not in dashboard.text
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "password_change_required"
    assert legacy.headers["location"] == "/admin/dashboard"


class PasswordCursor(AuthCursor):
    def __init__(self, role, password, forced=False):
        super().__init__()
        self.role = role
        self.password_hash = app._hash_password(password)
        self.forced = forced

    def execute(self, query, params=()):
        super().execute(query, params)
        if "UPDATE users SET password_hash" in query:
            self.password_hash = params[0]
            self.forced = False

    def fetchone(self):
        if "SELECT password_hash" in self.statements[-1][0]:
            return (self.password_hash,)
        return (self.role, True, self.forced)


@pytest.mark.parametrize("role", [app.ROLE_SUPER_ADMIN, app.ROLE_SEMI_ADMIN])
@pytest.mark.parametrize("forced", [False, True])
def test_admin_can_change_own_password(monkeypatch, role, forced):
    cursor = PasswordCursor(role, "OldPassword!", forced)
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(cursor))
    actions = []
    monkeypatch.setattr(app, "_record_admin_activity", lambda session, action, **kwargs: actions.append(action))
    request = AuthRequest({
        "current_password": "OldPassword!",
        "new_password": "NewPassword!",
        "confirm_password": "NewPassword!",
    })
    response = asyncio.run(app.admin_change_password(request))

    assert response.status_code == 200
    assert json.loads(response.body)["success"] is True
    assert app._check_password(cursor.password_hash, "NewPassword!")
    assert not app._check_password(cursor.password_hash, "OldPassword!")
    assert request.session["must_change_password"] is False
    assert request.session["role"] == role
    assert actions == ["temporary_password_changed" if forced else "password_changed"]
    update = next(params for query, params in cursor.statements if "UPDATE users SET password_hash" in query)
    assert update[1] == request.session["user_id"]


@pytest.mark.parametrize(("current", "new", "confirmation", "message"), [
    ("WrongPassword!", "NewPassword!", "NewPassword!", "Current password is incorrect."),
    ("OldPassword!", "NewPassword!", "DifferentPassword!", "New passwords do not match."),
    ("OldPassword!", "weak", "weak", app._password_policy_error("weak")),
    ("OldPassword!", "OldPassword!", "OldPassword!", "Choose a password different from your current password."),
    ("", "NewPassword!", "NewPassword!", "Complete all password fields."),
])
def test_super_admin_password_change_rejects_invalid_input(monkeypatch, current, new, confirmation, message):
    cursor = PasswordCursor(app.ROLE_SUPER_ADMIN, "OldPassword!")
    original_hash = cursor.password_hash
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(cursor))
    request = AuthRequest({
        "current_password": current, "new_password": new, "confirm_password": confirmation,
    })
    response = asyncio.run(app.admin_change_password(request))
    assert response.status_code == 400
    assert json.loads(response.body)["message"] == message
    assert cursor.password_hash == original_hash
    assert not any("UPDATE users" in query for query, _ in cursor.statements)


@pytest.mark.parametrize("role", [app.ROLE_SUPER_ADMIN, app.ROLE_SEMI_ADMIN])
def test_admin_dashboard_includes_password_change_option(monkeypatch, role):
    secret = app.app.user_middleware[0].kwargs["secret_key"]
    session = {"user_id": 12345, "role": role, "name": "Test Admin"}
    cookie = TimestampSigner(secret).sign(base64.b64encode(json.dumps(session).encode())).decode()
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(AuthCursor((role, True, False))))
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    with TestClient(app.app) as client:
        client.cookies.set("session_", cookie)
        dashboard = client.get("/admin/dashboard", headers={"Accept": "text/html"})
    assert dashboard.status_code == 200
    assert 'data-admin-settings="account"' in dashboard.text
    assert 'id="admin-password-form"' in dashboard.text
    assert 'name="current_password"' in dashboard.text
    assert 'name="new_password"' in dashboard.text
    assert 'name="confirm_password"' in dashboard.text


def test_password_change_requires_admin_account(monkeypatch):
    cursor = AuthCursor(("student", True, False))
    monkeypatch.setattr(app, "_db_conn", lambda: AuthConnection(cursor))
    request = AuthRequest({
        "current_password": "OldPassword!", "new_password": "NewPassword!", "confirm_password": "NewPassword!",
    })
    response = asyncio.run(app.admin_change_password(request))
    assert response.status_code == 401
    assert not any("UPDATE users" in query for query, _ in cursor.statements)
