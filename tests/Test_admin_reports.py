import asyncio
import base64
import json
import re
from datetime import datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from openpyxl import load_workbook
from reportlab.pdfgen.canvas import Canvas

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
        return self.role, True, False, 0

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
    status, result = _call_report(monkeypatch, role, {"report_type": "all_reports", "strand": "HT"})

    assert status == 200
    report = result["report"]
    assert report["title"] == "All Reports"
    assert [section["type"] for section in report["sections"]] == ["student_summary", "report_cards", "recommendations"]
    assert all(section["filters"]["strand"] == "HT" for section in report["sections"])


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


@pytest.mark.parametrize("report_type", ["student_summary", "report_cards", "recommendations", "all_reports"])
def test_pdf_report_repeats_school_branding_on_every_page(monkeypatch, report_type):
    _, result = _call_report(monkeypatch, app.ROLE_ADMIN, {"report_type": report_type})
    report = result["report"]
    if report_type == "recommendations":
        report["rows"] = [
            {"course": f"Course {index}", "recommendations": index, "share": "1%"}
            for index in range(100)
        ]
    labels = []
    original_draw_string = Canvas.drawString

    def capture_label(canvas, x, y, text, *args, **kwargs):
        labels.append((canvas.getPageNumber(), y, text))
        return original_draw_string(canvas, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Canvas, "drawString", capture_label)
    pdf = app._admin_report_pdf(report)
    page_count = len(re.findall(rb"/Type\s*/Page\b", pdf))
    for page in range(1, page_count + 1):
        page_labels = {text: y for number, y, text in labels if number == page}
        assert "PathFinder" in page_labels
        assert "University of Perpetual Help System Dalta" in page_labels
        assert page_labels["University of Perpetual Help System Dalta"] < page_labels["PathFinder"]


class ActivityCursor:
    rows = [
        (3, "Admin One", "admin@example.com", "admin_account_updated", "=HYPERLINK(\"x\")", "<b>details</b>", "10.0.0.1", datetime(2026, 10, 23, 2, 0), "admin"),
        (2, "Student Two", "student@example.com", "login", "", "", "", datetime(2026, 10, 22, 2, 0), "student"),
        (1, "Admin Three", "", "logout", "", "", "", None, "admin"),
    ]

    def __init__(self, role):
        self.role = role

    def execute(self, query, params=()):
        assert "FROM admin_activity_logs" in query or "FROM users" in query

    def fetchone(self):
        return self.role, True, False, 0

    def fetchall(self):
        return self.rows


class ActivityConnection:
    def __init__(self, role):
        self.role = role

    def cursor(self):
        return ActivityCursor(self.role)

    def close(self):
        pass


def _export_activity(monkeypatch, payload, role=app.ROLE_SUPER_ADMIN):
    monkeypatch.setattr(app, "_db_conn", lambda: ActivityConnection(role))
    return asyncio.run(app.admin_export_activity(ReportRequest(role, payload)))


def test_activity_export_is_registered_with_fastapi():
    assert any(
        route.path == "/admin/activity/export" and "POST" in route.methods
        for route in app.api_app.routes
    )


def test_activity_export_xlsx_downloads_from_the_mounted_api(monkeypatch):
    secret = app.app.user_middleware[0].kwargs["secret_key"]
    session = {"user_id": 12345, "role": app.ROLE_SUPER_ADMIN}
    cookie = TimestampSigner(secret).sign(base64.b64encode(json.dumps(session).encode())).decode()
    monkeypatch.setattr(app, "_db_conn", lambda: ActivityConnection(app.ROLE_SUPER_ADMIN))

    with TestClient(app.app) as client:
        client.cookies.set("session_", cookie)
        response = client.post(
            "/api/v1/admin/activity/export",
            json={"format": "xlsx", "ids": [2], "filter_summary": "Student activity"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"].endswith('.xlsx"')
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet["B1"].value == "PathFinder"
    assert sheet["B2"].value == "University of Perpetual Help System Dalta"
    assert sheet["A5"].value == "Filters: Student activity"
    assert sheet["B9"].value == "Student Two"
    assert sheet["A6"].value == "1 entry"


def test_activity_export_pdf_contains_only_selected_entries(monkeypatch):
    response = _export_activity(monkeypatch, {"format": "pdf", "ids": [3, 1], "filter_summary": "Admin activity"})

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF-")
    assert 'filename="pathfinder-activity-log-' in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"


def test_activity_export_xlsx_keeps_order_labels_and_inert_text(monkeypatch):
    response = _export_activity(monkeypatch, {"format": "xlsx", "ids": [2, 3], "filter_summary": "Search: \"x\""})

    assert response.status_code == 200
    assert response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert 'filename="pathfinder-activity-log-' in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    sheet = load_workbook(BytesIO(response.body)).active
    data = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=9)]
    assert [row[1] for row in data] == ["Admin One", "Student Two"]
    assert data[0][0] == "2026-10-23 10:00:00 AM"
    assert data[0][4] == "Admin account updated"
    assert data[1][4] == "Signed in"
    assert data[0][5] == "=HYPERLINK(\"x\")"
    assert sheet.cell(row=9, column=6).data_type == "s"
    assert sheet["A5"].value == 'Filters: Search: "x"'


def test_activity_export_xlsx_has_branded_printable_header_and_filters(monkeypatch):
    response = _export_activity(monkeypatch, {"format": "xlsx", "ids": [2, 3], "filter_summary": "Admin & student activity"})
    sheet = load_workbook(BytesIO(response.body)).active

    assert sheet.title == "Activity Log"
    assert sheet["B1"].value == "PathFinder"
    assert sheet["B2"].value == "University of Perpetual Help System Dalta"
    assert sheet["A4"].value == "Activity Log"
    assert sheet["G4"].value.startswith("Generated ")
    assert sheet["A5"].value == "Filters: Admin & student activity"
    assert sheet["A6"].value == "2 entries"
    assert len(sheet._images) == 1
    assert [cell.value for cell in sheet[8]] == [label for label, _, _ in app.ADMIN_ACTIVITY_EXPORT_COLUMNS]
    assert sheet["A8"].fill.fgColor.rgb == "FF800000"
    assert sheet["A9"].font.sz == 11
    assert sheet["A9"].alignment.wrap_text is True
    assert sheet["A10"].fill.fgColor.rgb == "FFFAF8F8"
    assert sheet.freeze_panes == "A9"
    assert sheet.auto_filter.ref == "A8:H10"
    assert sheet.sheet_view.showGridLines is False
    assert sheet.print_title_rows == "$1:$8"
    assert str(sheet.print_area) == "'Activity Log'!$A$1:$H$10"
    assert sheet.page_setup.orientation == "landscape"
    assert sheet.page_setup.fitToWidth == 1
    assert sheet.page_setup.fitToHeight == 0
    assert sheet.sheet_properties.pageSetUpPr.fitToPage is True
    assert "University of Perpetual Help System Dalta" in sheet.oddFooter.left.text
    assert "Page &P of &N" in sheet.oddFooter.right.text


def test_activity_export_xlsx_handles_empty_and_long_filter_summaries(monkeypatch):
    response = _export_activity(monkeypatch, {"format": "xlsx", "ids": [], "filter_summary": "x" * 300})
    sheet = load_workbook(BytesIO(response.body)).active

    assert sheet["A5"].value == "Filters: " + "x" * 300
    assert sheet["A5"].alignment.wrap_text is True
    assert sheet.row_dimensions[5].height >= 45
    assert sheet["A6"].value == "0 entries"
    assert sheet["A9"].value == "No activity matches the selected filters."
    assert sheet.auto_filter.ref is None


def test_activity_export_xlsx_keeps_all_untrusted_cells_as_text():
    values = ["=1+1", "+1+1", "-1+1", "@SUM(1,1)", "=HYPERLINK(\"x\")", "=2+2", "=3+3", "=4+4"]
    row = {key: value for (_, key, _), value in zip(app.ADMIN_ACTIVITY_EXPORT_COLUMNS, values)}

    sheet = load_workbook(BytesIO(app._admin_activity_xlsx([row], "=1+1", datetime(2026, 11, 3)))).active

    assert sheet["A5"].value == "Filters: =1+1"
    assert sheet["A5"].data_type == "s"
    assert sheet["A6"].value == "1 entry"
    for cell, value in zip(sheet[9], values):
        assert cell.value == value
        assert cell.data_type == "s"


def test_activity_export_xlsx_print_range_includes_the_maximum_number_of_records():
    template = {key: key for _, key, _ in app.ADMIN_ACTIVITY_EXPORT_COLUMNS}
    rows = [{**template, "user": f"User {index:04}"} for index in range(app.ADMIN_ACTIVITY_LIMIT)]

    sheet = load_workbook(BytesIO(app._admin_activity_xlsx(rows, "All activity", datetime(2026, 11, 3)))).active
    last_row = 8 + app.ADMIN_ACTIVITY_LIMIT

    assert sheet["B9"].value == "User 0000"
    assert sheet.cell(row=last_row, column=2).value == f"User {app.ADMIN_ACTIVITY_LIMIT - 1:04}"
    assert sheet.auto_filter.ref == f"A8:H{last_row}"
    assert str(sheet.print_area) == f"'Activity Log'!$A$1:$H${last_row}"
    assert sheet["A6"].value == f"{app.ADMIN_ACTIVITY_LIMIT} entries"


@pytest.mark.parametrize("export_format", ["pdf", "xlsx"])
def test_activity_export_is_full_admin_only(monkeypatch, export_format):
    response = _export_activity(monkeypatch, {"format": export_format, "ids": [1]}, role=app.ROLE_SEMI_ADMIN)

    assert response.status_code == 403


@pytest.mark.parametrize("payload", [
    {"format": "csv", "ids": [1]},
    {"format": "pdf", "ids": "1"},
    {"format": "pdf", "ids": ["1"]},
    {"format": "pdf", "ids": [True]},
    {"format": "xlsx", "ids": list(range(app.ADMIN_ACTIVITY_LIMIT + 1))},
    [],
])
def test_activity_export_rejects_invalid_requests(monkeypatch, payload):
    response = _export_activity(monkeypatch, payload)

    assert response.status_code == 400
