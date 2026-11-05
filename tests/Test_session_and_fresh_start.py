import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import fastapi_app as app


def signed_session(session):
    secret = app.app.user_middleware[0].kwargs["secret_key"]
    return TimestampSigner(secret).sign(base64.b64encode(json.dumps(session).encode())).decode()


@pytest.mark.parametrize("session", [
    {"user_id": 1, "role": "student"},
    {"user_id": 2, "role": app.ROLE_SUPER_ADMIN},
    {"user_id": 3, "role": app.ROLE_SEMI_ADMIN, "must_change_password": True},
    {"is_guest": True},
])
def test_all_sessions_expire_at_exactly_fifteen_idle_minutes(monkeypatch, session):
    clock = [10_000.0]
    monkeypatch.setattr(app, "time", SimpleNamespace(time=lambda: clock[0]))
    with TestClient(app.app) as client:
        client.cookies.set("session_", signed_session({**session, "last_activity": clock[0]}))
        clock[0] += 899
        response = client.get("/api/v1/session")
        assert response.status_code == 200
        assert response.json()["expires_at"] == 10_900
        clock[0] += 1
        expired = client.post("/api/v1/session")
        assert expired.status_code == 401
        assert expired.json()["code"] == "session_expired"
        assert client.get("/api/v1/session").status_code == 401
        assert "no-store" in expired.headers["cache-control"]


def test_polling_does_not_extend_session_but_user_heartbeat_does(monkeypatch):
    clock = [10_000.0]
    monkeypatch.setattr(app, "time", SimpleNamespace(time=lambda: clock[0]))
    with TestClient(app.app) as client:
        client.cookies.set("session_", signed_session({"is_guest": True, "last_activity": clock[0]}))
        clock[0] += 600
        assert client.get("/api/v1/session").json()["expires_at"] == 10_900
        assert client.post("/api/v1/session").json()["expires_at"] == 11_500
        clock[0] += 899
        assert client.get("/api/v1/session").status_code == 200
        clock[0] += 1
        assert client.get("/api/v1/session").status_code == 401


@pytest.mark.parametrize("last_activity", ["bad", None, float("inf"), 20_000.0])
def test_invalid_session_activity_cannot_bypass_expiry(monkeypatch, last_activity):
    monkeypatch.setattr(app, "time", SimpleNamespace(time=lambda: 10_000.0))
    with TestClient(app.app) as client:
        client.cookies.set("session_", signed_session({"is_guest": True, "last_activity": last_activity}))
        assert client.get("/api/v1/session").status_code == 401


def test_expired_api_cannot_run_and_html_redirects_to_timeout_notice(monkeypatch):
    monkeypatch.setattr(app, "time", SimpleNamespace(time=lambda: 10_000.0))
    monkeypatch.setattr(app, "_db_conn", lambda: pytest.fail("Expired request reached the database"))
    cookie = signed_session({"user_id": 1, "role": "student", "last_activity": 9_100})
    with TestClient(app.app) as client:
        client.cookies.set("session_", cookie)
        response = client.post("/api/v1/recommendations/reset", json={"confirmation": "RESET"})
        assert response.status_code == 401
        client.cookies.clear()
        client.cookies.set("session_", cookie)
        response = client.get("/home", headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login?reason=timeout"
        notice = client.get(response.headers["location"])
        assert "expired after 15 minutes of inactivity" in notice.text


def test_legacy_and_new_guest_sessions_receive_idle_deadline(monkeypatch):
    monkeypatch.setattr(app, "time", SimpleNamespace(time=lambda: 10_000.0))
    with TestClient(app.app) as client:
        client.cookies.set("session_", signed_session({"is_guest": True}))
        assert client.get("/api/v1/session").json()["expires_at"] == 10_900
        client.cookies.clear()
        assert client.get("/guest-login", follow_redirects=False).status_code == 302
        assert client.get("/api/v1/session").json()["expires_at"] == 10_900


class ResetConnection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT);
            CREATE TABLE student_profiles (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id));
            CREATE TABLE report_card_uploads (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id));
            CREATE TABLE course_recommendation_history (
                id INTEGER PRIMARY KEY, profile_id INTEGER REFERENCES student_profiles(id) ON DELETE CASCADE
            );
            INSERT INTO users VALUES (1, 'student'), (2, 'student'), (3, 'admin');
            INSERT INTO student_profiles VALUES (10, 1), (11, 1), (20, 2);
            INSERT INTO report_card_uploads VALUES (10, 1), (11, 1), (20, 2);
            INSERT INTO course_recommendation_history VALUES (10, 10), (11, 11), (20, 20);
        """)
        self.closed = False

    def cursor(self):
        return self.db.cursor()

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        self.closed = True


def reset_request(session, confirmation="RESET"):
    async def data():
        return {"confirmation": confirmation, "user_id": 2}
    return SimpleNamespace(session=session, json=data)


def test_fresh_start_persistently_clears_only_students_own_data_and_preserves_account(monkeypatch):
    connection = ResetConnection()
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    monkeypatch.setattr(app, "_record_student_activity", lambda *args, **kwargs: None)
    request = reset_request({
        "user_id": 1, "role": "student", "name": "Student", "last_activity": 123,
        "latest_subjects": "Math - 99", "latest_report_card_upload_id": 10, "latest_recommendations": [],
    })
    try:
        response = asyncio.run(app.reset_student_recommendations(request))
        assert response.status_code == 200
        assert json.loads(response.body)["success"]
        for table in ("student_profiles", "report_card_uploads", "course_recommendation_history"):
            assert connection.db.execute(f"SELECT id FROM {table}").fetchall() == [(20,)]
        assert connection.db.execute("SELECT id FROM users ORDER BY id").fetchall() == [(1,), (2,), (3,)]
        assert request.session == {"user_id": 1, "role": "student", "name": "Student", "last_activity": 123}
        assert connection.closed
        assert asyncio.run(app.reset_student_recommendations(request)).status_code == 200
    finally:
        connection.db.close()


@pytest.mark.parametrize(("session", "confirmation", "status"), [
    ({}, "RESET", 401),
    ({"user_id": 3, "role": "admin"}, "RESET", 403),
    ({"user_id": 3, "role": "semi_admin"}, "RESET", 403),
    ({"user_id": 1, "role": "student"}, "", 400),
])
def test_fresh_start_requires_student_session_and_explicit_confirmation(monkeypatch, session, confirmation, status):
    monkeypatch.setattr(app, "_db_conn", lambda: pytest.fail("Unauthorized reset touched database"))
    response = asyncio.run(app.reset_student_recommendations(reset_request(session, confirmation)))
    assert response.status_code == status


def test_fresh_start_checks_database_role_not_just_session(monkeypatch):
    connection = ResetConnection()
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    try:
        response = asyncio.run(app.reset_student_recommendations(reset_request({"user_id": 3})))
        assert response.status_code == 403
        assert connection.db.execute("SELECT COUNT(*) FROM student_profiles").fetchone()[0] == 3
        assert connection.closed
    finally:
        connection.db.close()


def test_guest_fresh_start_clears_session_results_without_touching_accounts(monkeypatch):
    monkeypatch.setattr(app, "_db_conn", lambda: pytest.fail("Guest reset touched database"))
    request = reset_request({"is_guest": True, "latest_subjects": "Business - 99", "latest_strand": "BE"})
    assert asyncio.run(app.reset_student_recommendations(request)).status_code == 200
    assert request.session == {"is_guest": True}


def test_reset_failure_rolls_back_and_keeps_session_state(monkeypatch, caplog):
    connection = ResetConnection()
    real_cursor = connection.db.cursor()
    real_execute = real_cursor.execute

    def execute(query, params=()):
        if "DELETE FROM report_card_uploads" in query:
            raise psycopg.OperationalError("test database failure")
        return real_execute(query, params)

    monkeypatch.setattr(connection, "cursor", lambda: SimpleNamespace(execute=execute, fetchone=real_cursor.fetchone))
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    request = reset_request({"user_id": 1, "latest_subjects": "Math - 99"})
    try:
        response = asyncio.run(app.reset_student_recommendations(request))
        assert response.status_code == 500
        assert request.session["latest_subjects"] == "Math - 99"
        assert connection.db.execute("SELECT COUNT(*) FROM student_profiles").fetchone()[0] == 3
        assert connection.db.execute("SELECT COUNT(*) FROM course_recommendation_history").fetchone()[0] == 3
        assert "Could not clear student recommendation records" in caplog.text
        assert connection.closed
    finally:
        connection.db.close()
