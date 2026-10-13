import asyncio
import json
from types import SimpleNamespace

import pytest

import fastapi_app as app


class ProfileConnection:
    def __init__(self, profile=None):
        self.profile = profile
        self.query = ""
        self.saved = None
        self.committed = False
        self.history = []

    def cursor(self):
        return self

    def execute(self, query, params=()):
        self.query = query
        if "UPDATE student_profiles" in query:
            self.saved = params[:12]
            self.profile = (self.profile[0], *params[:8], params[9], params[11])
        elif "INSERT INTO student_profiles" in query:
            self.saved = params[1:]
            self.profile = (7, *params[1:9], params[10], params[12])
        elif "INSERT INTO course_recommendation_history" in query:
            self.history.append(params)

    def fetchone(self):
        if "SELECT id, student_number" in self.query:
            return self.profile
        if "INSERT INTO student_profiles" in self.query:
            return (7,)
        raise AssertionError(f"Unexpected query: {self.query}")

    def commit(self):
        self.committed = True

    def close(self):
        pass


def save_record(monkeypatch, connection, payload):
    recommendation_inputs = []
    comparison_inputs = []
    monkeypatch.setattr(app, "_db_conn", lambda: connection)

    def recommendations(subjects, course, strand):
        recommendation_inputs.append(subjects)
        return [{"course": "Test course"}]

    def comparison(course, subjects):
        comparison_inputs.append(subjects)
        return {"subjects": subjects}

    monkeypatch.setattr(app, "_configured_recommendations", recommendations)
    monkeypatch.setattr(app, "_build_student_performance_analytics", comparison)
    monkeypatch.setattr(app, "_record_student_activity", lambda *args, **kwargs: None)

    async def request_json():
        return payload

    request = SimpleNamespace(session={"user_id": "student"}, json=request_json)
    response = asyncio.run(app.save_profile(request))
    assert response.status_code == 200
    return json.loads(response.body), request.session, recommendation_inputs, comparison_inputs


def test_merge_preserves_numbered_subjects_across_report_cards():
    merged = app._merge_subject_entries(
        "Physical Education and Health 1 - 92\nGeneral Biology 1 - 88",
        "Physical Education and Health 3 - 86\nGeneral Biology 2 - 90",
    )
    assert merged.splitlines() == [
        "Physical Education and Health 1 - 92",
        "General Biology 1 - 88",
        "Physical Education and Health 3 - 86",
        "General Biology 2 - 90",
    ]


@pytest.mark.parametrize(("text", "expected"), [
    ("Physical Education and Health 1 - 92\nGeneral Biology 2 - 88", [92, 88]),
    ("21st Century Literature - 84\nFilipino Christian Living 3 - 93", [84, 93]),
    ("Subject 1 - 0", [0]),
    ("Subject 1 - 87.5 Passed", [87.5]),
    ("92, 88, 87.5", [92, 88, 87.5]),
])
def test_grade_extraction_excludes_subject_numbers(text, expected):
    assert app._extract_numeric_from_grades_text(text) == expected


@pytest.mark.parametrize("existing", [False, True])
def test_save_returns_and_analyzes_the_complete_saved_record(monkeypatch, existing):
    profile = (
        7, "20-0002-791", "Student", "", "Name", "", "92.00", "92",
        "Physical Education and Health 1 - 92", "STEM", None,
    ) if existing else None
    connection = ProfileConnection(profile)
    result, session, recommendation_inputs, comparison_inputs = save_record(
        monkeypatch, connection,
        {
            "studentNumber": "20-0002-791",
            "firstName": "Student",
            "lastName": "Name",
            "strand": "STEM",
            "subjects": "Physical Education and Health 3 - 86",
            "grades": "86",
            "gwa": "86.00",
        },
    )
    expected = (
        "Physical Education and Health 1 - 92\nPhysical Education and Health 3 - 86"
        if existing else "Physical Education and Health 3 - 86"
    )
    expected_gwa = "89.00" if existing else "86.00"
    expected_grades = "92, 86" if existing else "86"
    assert connection.committed
    assert connection.saved[7] == expected
    assert connection.saved[5:7] == (expected_gwa, expected_grades)
    assert recommendation_inputs == [expected]
    assert comparison_inputs == [expected]
    assert session["latest_subjects"] == expected
    assert result["profile"]["success"] is True
    assert result["profile"]["subjects"] == expected
    assert result["profile"]["gwa"] == expected_gwa
    assert result["profile"]["grades"] == expected_grades
    assert len(result["profile"]["subjectRows"]) == (2 if existing else 1)
    assert result["profile"]["recommendation"] == result["recommendation"]
    assert result["profile"]["comparisons"] == result["comparisons"]


def test_save_uses_reviewed_subjects_instead_of_original_ocr_table(monkeypatch):
    monkeypatch.setattr(app, "_subjects_from_table", lambda table: [
        {"subject_name": "Original OCR subject", "grade": 80},
    ])
    connection = ProfileConnection()
    result, _, _, _ = save_record(
        monkeypatch, connection,
        {
            "subjects": "Corrected subject - 90",
            "grades": "90",
            "gwa": "90.00",
            "ocrTable": [{"cells": []}],
        },
    )
    assert connection.saved[7] == "Corrected subject - 90"
    assert result["profile"]["subjects"] == "Corrected subject - 90"


def test_save_still_accepts_table_only_records(monkeypatch):
    monkeypatch.setattr(app, "_subjects_from_table", lambda table: [
        {"subject_name": "Table subject", "grade": 85},
    ])
    connection = ProfileConnection()
    result, _, _, _ = save_record(monkeypatch, connection, {"ocrTable": [{"cells": []}]})
    assert connection.saved[7] == "Table subject - 85"
    assert result["profile"]["gwa"] == "85.00"


def test_successive_uploads_preserve_existing_subjects_and_avoid_duplicates(monkeypatch):
    connection = ProfileConnection()
    first, _, _, _ = save_record(monkeypatch, connection, {
        "subjects": "Physical Education and Health 1 - 92",
    })
    second, _, _, _ = save_record(monkeypatch, connection, {
        "subjects": "Physical Education and Health 3 - 86",
    })
    third, _, _, _ = save_record(monkeypatch, connection, {
        "subjects": "Physical Education and Health 1 - 92\nGeneral Biology 2 - 89",
    })
    assert first["profile"]["subjects"] == "Physical Education and Health 1 - 92"
    assert second["profile"]["subjects"].splitlines() == [
        "Physical Education and Health 1 - 92",
        "Physical Education and Health 3 - 86",
    ]
    assert third["profile"]["subjects"].splitlines() == [
        "Physical Education and Health 1 - 92",
        "Physical Education and Health 3 - 86",
        "General Biology 2 - 89",
    ]
    assert third["profile"]["gwa"] == "89.00"
    assert connection.profile[6:9] == (
        "89.00", "92, 86, 89", third["profile"]["subjects"],
    )


def test_guest_recommendations_use_reviewed_subjects(monkeypatch):
    captured = []

    def recommendations(subjects, course, strand):
        captured.append(subjects)
        return []

    monkeypatch.setattr(app, "_configured_recommendations", recommendations)

    def unexpected_table_parse(table):
        raise AssertionError("Reviewed subjects must take precedence over the raw table")

    monkeypatch.setattr(app, "_subjects_from_table", unexpected_table_parse)

    async def request_json():
        return {"subjects": "Reviewed subject - 90", "ocrTable": [{"cells": []}]}

    request = SimpleNamespace(session={"is_guest": True}, json=request_json)
    result = asyncio.run(app.recommend_anonymous(request))
    assert json.loads(result.body)["success"] is True
    assert captured == ["Reviewed subject - 90"]


def test_new_upload_recalculates_real_recommendations_and_persists_them(monkeypatch):
    catalog = [
        {"course": "BS in Computer Science", "features": [95, 85, 80, 95, 70, 80],
         "description": "Computer science", "strands": ["STEM", "TVL_ICT"]},
        {"course": "BS in Hospitality Management", "features": [80, 80, 85, 70, 96, 80],
         "description": "Tourism and hospitality", "strands": ["TVL_HE", "TVL"]},
        {"course": "BS in Nursing", "features": [80, 95, 80, 70, 70, 80],
         "description": "Patient care", "strands": ["STEM"]},
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: catalog)
    monkeypatch.setattr(app, "_get_system_settings", lambda: {**app.SYSTEM_SETTING_DEFAULTS, "recommendation_limit": 3})
    monkeypatch.setattr(app, "_record_student_activity", lambda *args, **kwargs: None)
    old_subjects = "General Mathematics - 95\nOral Communication - 80"
    old_recommendations = app._configured_recommendations(old_subjects)
    assert old_recommendations[0]["course"] == "BS in Computer Science"
    connection = ProfileConnection((
        7, "20-0002-791", "Student", "", "Name", "", "87.50", "95, 80",
        old_subjects, "BSCS-DS", None,
    ))
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    added_subjects = "Tourism Production Services 1 - 96\nEntrepreneurship - 96\nNihongo 3 - 95"

    async def request_json():
        return {"subjects": added_subjects, "strand": "BSCS-DS"}

    request = SimpleNamespace(session={"user_id": "student"}, json=request_json)
    result = json.loads(asyncio.run(app.save_profile(request)).body)
    merged_subjects = f"{old_subjects}\n{added_subjects}"
    assert connection.committed
    assert result["profile"]["subjects"] == merged_subjects
    assert result["profile"]["strand"] == ""
    assert result["profile"]["course"] == "BSCS-DS"
    assert connection.saved[9] == ""
    assert len(result["recommendation"]) == 3
    assert result["recommendation"][0]["course"] == "BS in Hospitality Management"
    assert result["recommendation"] != old_recommendations
    assert result["recommendation"] == app._configured_recommendations(merged_subjects)
    assert json.loads(connection.saved[8]) == result["recommendation"]
    assert json.loads(request.session["latest_recommendations"]) == result["recommendation"]
    assert json.loads(connection.history[-1][1]) == result["recommendation"]
    assert result["profile"]["recommendation"] == result["recommendation"]
    assert set(result["comparisons"]) == {item["course"] for item in result["recommendation"]}


@pytest.mark.parametrize("degree", ["BSCS-DS", "BSIT-05", "BS in Computer Science", "Bachelor of Science in Nursing"])
def test_degree_program_is_not_used_as_shs_strand(monkeypatch, degree):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Hospitality Management", "features": [80, 80, 85, 70, 95, 80],
         "description": "Hospitality", "strands": ["TVL_HE"]},
    ])
    recommendations = app.recommend_course("Mathematics - 85\nEntrepreneurship - 95", strand=degree)
    assert len(recommendations) == 1
    assert recommendations[0]["strand_label"] == ""
    assert not recommendations[0]["strand_alignment"]
    assert "highest grade areas" in recommendations[0]["reason"].lower()


@pytest.mark.parametrize("strand", ["", "STEM", "Home Economics (HE)"])
def test_ocr_keeps_program_and_shs_strand_separate(strand):
    raw = {
        "raw_text": "Mathematics - 85\nEnglish - 90",
        "student_info_raw": [
            {"label": "Program", "value": "BSCS-DS"},
            *([{"label": "Strand", "value": strand}] if strand else []),
        ],
        "table": [],
    }
    parsed = app.parse_report_card_structure(raw)
    assert parsed["major"] == "BSCS-DS"
    assert parsed["strand"] == strand


def test_saving_college_course_does_not_infer_an_shs_strand(monkeypatch):
    connection = ProfileConnection()
    result, _, _, _ = save_record(monkeypatch, connection, {
        "course": "BS in Computer Science", "subjects": "Mathematics - 90",
    })
    assert result["profile"]["strand"] == ""
    assert connection.saved[9] == ""


def test_manual_regeneration_repairs_legacy_zero_matches(monkeypatch):
    catalog = [
        {"course": "BS in Hospitality Management", "features": [80, 80, 85, 70, 95, 80],
         "description": "Hospitality", "strands": ["TVL_HE"]},
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: catalog)
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    monkeypatch.setattr(app, "_get_user_latest_profile", lambda user_id: {
        "course": "", "strand": "BSCS-DS",
        "subjects": "Mathematics - 85\nEntrepreneurship - 95",
    })
    statements = []
    cursor = SimpleNamespace(execute=lambda query, params: statements.append((query, params)), fetchone=lambda: (7,))
    monkeypatch.setattr(app, "_db_conn", lambda: SimpleNamespace(
        cursor=lambda: cursor, commit=lambda: None, close=lambda: None,
    ))
    monkeypatch.setattr(app, "_record_student_activity", lambda *args, **kwargs: None)
    request = SimpleNamespace(session={"user_id": "student"})
    result = json.loads(asyncio.run(app.generate_recommendations(request)).body)
    assert result == {"success": True, "count": 1}
    assert len(json.loads(statements[0][1][0])) == 1
    assert len(json.loads(request.session["latest_recommendations"])) == 1
