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
