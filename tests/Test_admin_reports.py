import asyncio
import json
from datetime import datetime

import pytest

import fastapi_app as app


class ReportRequest:
    def __init__(self, role, payload):
        self.session = {"user_id": "report-test-user", "role": role}
        self.payload = payload

    async def json(self):
        return self.payload


class ReportCursor:
    def __init__(self, role):
        self.role = role
        self.query = ""

    def execute(self, query, params=()):
        self.query = query
        if "FROM users" in query and "LEFT JOIN LATERAL" in query:
            assert query.count("?") == len(params)

    def fetchone(self):
        return self.role, True, False

    def fetchall(self):
        if "FROM report_card_uploads" in self.query:
            return [("extracted", 2, 1, datetime(2025, 3, 1))]
        if "FROM course_recommendation_history" in self.query:
            return [(7, "[]")]
        return [("STEM", 2, 2, 90.0, 2, 2, 0)]


class ReportConnection:
    def __init__(self, role):
        self.cursor_instance = ReportCursor(role)

    def cursor(self):
        return self.cursor_instance

    def close(self):
        pass


def _call_report(monkeypatch, role, payload):
    monkeypatch.setattr(app, "_db_conn", lambda: ReportConnection(role))
    response = asyncio.run(app.admin_generate_report(ReportRequest(role, payload)))
    return response.status_code, json.loads(response.body)


def test_report_generator_is_registered_with_fastapi():
    assert any(
        route.path == "/admin/reports/generate" and "POST" in route.methods
        for route in app.api_app.routes
    )


@pytest.mark.parametrize("role", [app.ROLE_ADMIN, app.ROLE_SEMI_ADMIN])
@pytest.mark.parametrize(
    ("report_type", "expected_title"),
    [
        ("student_summary", "Student Summary Report"),
        ("report_cards", "Report Card Status Report"),
        ("recommendations", "Recommendation Frequency Report"),
    ],
)
def test_admin_roles_can_generate_each_report(monkeypatch, role, report_type, expected_title):
    status, response = _call_report(monkeypatch, role, {"report_type": report_type})

    assert status == 200
    assert response["success"] is True
    assert response["report"]["title"] == expected_title
    assert isinstance(response["report"]["rows"], list)


def test_report_generator_rejects_non_admin_roles(monkeypatch):
    status, response = _call_report(monkeypatch, "student", {"report_type": "student_summary"})

    assert status == 403
    assert response["success"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"report_type": "unknown"},
        {"report_type": "student_summary", "strand": "not-configured"},
        {"report_type": "report_cards", "from_date": "not-a-date"},
        {"report_type": "recommendations", "from_date": "2025-03-02", "to_date": "2025-03-01"},
    ],
)
def test_report_generator_rejects_invalid_filters(monkeypatch, payload):
    status, response = _call_report(monkeypatch, app.ROLE_SEMI_ADMIN, payload)

    assert status == 400
    assert response["success"] is False
