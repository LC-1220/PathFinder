import asyncio
import json
import re
from datetime import datetime

import pytest

import fastapi_app as app


class ReportRequest:
    def __init__(self, role, payload, method="POST"):
        self.session = {"user_id": "report-test-user", "role": role}
        self.payload = payload
        self.method = method
        self.query_params = payload if method == "GET" else {}

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
            assert query.count("{0,1}") == 2

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
    assert any(
        route.path == "/admin/reports/download" and "POST" in route.methods
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


@pytest.mark.parametrize("role", [app.ROLE_ADMIN, app.ROLE_SEMI_ADMIN])
def test_admin_roles_can_generate_all_reports(monkeypatch, role):
    status, result = _call_report(monkeypatch, role, {"report_type": "all_reports", "strand": "TVL"})

    assert status == 200
    report = result["report"]
    assert report["title"] == "All Reports"
    assert [section["type"] for section in report["sections"]] == ["student_summary", "report_cards", "recommendations"]
    assert all(section["filters"]["strand"] == "TVL" for section in report["sections"])


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


@pytest.mark.parametrize("role", [app.ROLE_ADMIN, app.ROLE_SEMI_ADMIN])
@pytest.mark.parametrize("report_type", ["student_summary", "report_cards", "recommendations", "all_reports"])
def test_admin_roles_can_download_pdf(monkeypatch, role, report_type):
    monkeypatch.setattr(app, "_db_conn", lambda: ReportConnection(role))

    response = asyncio.run(app.admin_download_report(ReportRequest(role, {"report_type": report_type})))

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF-")
    assert response.body.rstrip().endswith(b"%%EOF")
    assert f"pathfinder-{report_type}-" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    if report_type == "all_reports":
        assert len(re.findall(rb"/Type\s*/Page\b", response.body)) >= 3


@pytest.mark.parametrize("role, payload, expected_status", [
    ("student", {"report_type": "student_summary"}, 403),
    (app.ROLE_SEMI_ADMIN, {"report_type": "invalid"}, 400),
])
def test_pdf_download_preserves_report_access_and_validation(monkeypatch, role, payload, expected_status):
    monkeypatch.setattr(app, "_db_conn", lambda: ReportConnection(role))

    response = asyncio.run(app.admin_download_report(ReportRequest(role, payload)))

    assert response.status_code == expected_status
    assert response.media_type == "application/json"


def test_long_pdf_report_spans_multiple_pages(monkeypatch):
    _, result = _call_report(monkeypatch, app.ROLE_ADMIN, {"report_type": "recommendations"})
    report = result["report"]
    report["rows"] = [
        {"course": f"Course {index} & Studies", "recommendations": index, "share": "1%"}
        for index in range(100)
    ]

    pdf = app._admin_report_pdf(report)

    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) > 1
