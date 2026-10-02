import pytest
from types import SimpleNamespace

import fastapi_app as app

from fastapi_app import (
    _build_student_performance_analytics,
    _course_average_profile,
    _extract_subject_scores,
    _group_ocr_boxes_into_table_rows,
    _merge_subject_entries,
    _student_record_matches,
    recommend_course,
)
from ocr.parser import calculate_gwa, parse_report_card_structure, validate_grade


def _box(text, x, y, width=80, height=20):
    return {"text": text, "x": x, "y": y, "width": width, "height": height}


def test_group_ocr_boxes_into_table_rows_reconstructs_rows_and_columns():
    boxes = [
        _box("Subject", 0, 0),
        _box("Grade", 200, 0),
        _box("Remarks", 300, 0),
        _box("Mathematics", 0, 30),
        _box("90", 200, 30),
        _box("Passed", 300, 30),
        _box("English", 0, 60),
        _box("85", 200, 60),
        _box("Passed", 300, 60),
    ]

    table_rows = _group_ocr_boxes_into_table_rows(boxes)

    assert len(table_rows) == 3
    assert [cell["text"] for cell in table_rows[0]["cells"]] == ["Subject", "Grade", "Remarks"]
    assert [cell["text"] for cell in table_rows[1]["cells"]] == ["Mathematics", "90", "Passed"]


def test_report_card_with_remarks_column_no_longer_drops_subjects():
    boxes = [
        _box("Subject", 0, 0),
        _box("Grade", 200, 0),
        _box("Remarks", 300, 0),
        _box("Mathematics", 0, 30),
        _box("90", 200, 30),
        _box("Passed", 300, 30),
        _box("English", 0, 60),
        _box("85", 200, 60),
        _box("Passed", 300, 60),
        _box("Science", 0, 90),
        _box("92", 200, 90),
        _box("Passed", 300, 90),
    ]

    raw_ocr = {
        "raw_text": "\n".join(box["text"] for box in boxes),
        "student_info_raw": boxes,
        "table": _group_ocr_boxes_into_table_rows(boxes),
    }

    parsed = parse_report_card_structure(raw_ocr)

    assert len(parsed["subjects"]) == 3
    assert {s["subject_name"] for s in parsed["subjects"]} == {"Mathematics", "English", "Science"}


def test_report_card_with_code_instructor_and_remarks_columns_extracts_all_subjects():
    rows_data = [
        ("11101 ENG1", "Oral Communication in Context", "GAUDITE, VICTORLYN D.", "93.0"),
        ("11102 FIL1", "Komunikasyon at Pananaliksik sa Wika at Kulturang Pilipino", "CABANAG, CHEVIE MAY M.", "93.0"),
        ("11103 MAT1", "General Mathematics", "QUIOCHO, ROWIE A.", "87.0"),
        ("11104 ESC", "Earth and Life Science", "ARGOSO, REY B.", "83.0"),
        ("11105 IPP", "Introduction to the Philosophy of the Human Person", "EBERO, DARWIN", "89.0"),
        ("11106 PE1", "Physical Education and Health 1", "HERNANE, MARK PATRICK W.", "91.0"),
        ("11107 ET", "Empowerment Technologies", "PIMENTEL, NICOLE G.", "91.0"),
        ("11108 FCL1", "Filipino Christian Living 1", "RIVAS, JOSEPH S.", "92.0"),
        ("11114 TG", "Tour Guiding Services 1", "CAPARAS, HIGHESTIA G.", "93.0"),
        ("11115 NHG1", "Nihongo 1", "REYES, MINERVA M.", "89.0"),
        ("12160 BAP", "Bread and Pastry Production 1", "PIMENTEL, NICOLE G.", "93.0"),
    ]

    boxes = [
        _box("Subject Code", 0, 0),
        _box("Subject Name", 150, 0),
        _box("Instructor", 400, 0),
        _box("Grade", 600, 0),
        _box("Remarks", 700, 0),
    ]
    for row_index, (code, name, instructor, grade) in enumerate(rows_data):
        y = 30 * (row_index + 1)
        boxes.extend([
            _box(code, 0, y),
            _box(name, 150, y),
            _box(instructor, 400, y),
            _box(grade, 600, y),
            _box("Passed", 700, y),
        ])

    table_rows = _group_ocr_boxes_into_table_rows(boxes)
    raw_ocr = {
        "raw_text": "\n".join(box["text"] for box in boxes),
        "student_info_raw": boxes,
        "table": table_rows,
    }

    parsed = parse_report_card_structure(raw_ocr)

    assert len(parsed["subjects"]) == len(rows_data)
    extracted = {(s["subject_name"], s["grade"]) for s in parsed["subjects"]}
    expected = {(name, float(grade)) for _, name, _, grade in rows_data}
    assert extracted == expected


def test_report_card_with_each_row_merged_into_one_ocr_line_extracts_all_subjects():
    rows_data = [
        ("11101 ENG1", "Oral Communication in Context", "GAUDITE, VICTORLYN D.", "93.0"),
        ("11102 FIL1", "Komunikasyon at Pananaliksik sa Wika at Kulturang Pilipino", "CABANAG, CHEVIE MAY M.", "93.0"),
        ("11103 MAT1", "General Mathematics", "QUIOCHO, ROWIE A.", "87.0"),
        ("11104 ESC", "Earth and Life Science", "ARGOSO, REY B.", "83.0"),
        ("11105 IPP", "Introduction to the Philosophy of the Human Person", "EBERO, DARWIN", "89.0"),
        ("11106 PE1", "Physical Education and Health 1", "HERNANE, MARK PATRICK W.", "91.0"),
        ("11107 ET", "Empowerment Technologies", "PIMENTEL, NICOLE G.", "91.0"),
        ("11108 FCL1", "Filipino Christian Living 1", "RIVAS, JOSEPH S.", "92.0"),
        ("11114 TG", "Tour Guiding Services 1", "CAPARAS, HIGHESTIA G.", "93.0"),
        ("11115 NHG1", "Nihongo 1", "REYES, MINERVA M.", "89.0"),
        ("12160 BAP", "Bread and Pastry Production 1", "PIMENTEL, NICOLE G.", "93.0"),
    ]

    lines = [f"{code} {name} {instructor} {grade} Passed" for code, name, instructor, grade in rows_data]
    raw_text = "\n".join(lines)

    parsed = parse_report_card_structure({
        "raw_text": raw_text,
        "student_info_raw": [{"text": line} for line in lines],
        "table": [],
    })

    assert len(parsed["subjects"]) == len(rows_data)
    extracted = {(s["subject_name"], s["grade"]) for s in parsed["subjects"]}
    expected = {(name, float(grade)) for _, name, _, grade in rows_data}
    assert extracted == expected


def test_report_card_with_each_cell_on_its_own_line_extracts_all_subjects():
    rows_data = [
        ("11103 MAT1", "General Mathematics", "QUIOCHO, ROWIE A.", "87.0"),
        ("11107 ET", "Empowerment Technologies", "PIMENTEL, NICOLE G.", "91.0"),
        ("11115 NHG1", "Nihongo 1", "REYES, MINERVA M.", "89.0"),
    ]

    lines = []
    for code, name, instructor, grade in rows_data:
        lines.extend([code, name, instructor, grade, "Passed"])
    raw_text = "\n".join(lines)

    parsed = parse_report_card_structure({
        "raw_text": raw_text,
        "student_info_raw": [{"text": line} for line in lines],
        "table": [],
    })

    assert len(parsed["subjects"]) == len(rows_data)
    extracted = {(s["subject_name"], s["grade"]) for s in parsed["subjects"]}
    expected = {(name, float(grade)) for _, name, _, grade in rows_data}
    assert extracted == expected


def test_course_average_profile_returns_subject_averages():
    course_data = _course_average_profile("BS in Computer Science with Specialization in Data Science")

    assert course_data["course"] == "BS in Computer Science with Specialization in Data Science"
    assert course_data["overall_average"] > 70
    assert "math" in course_data["by_subject"]
    assert course_data["by_subject"]["technology"] >= 80


def test_student_performance_analytics_computes_comparison():
    analytics = _build_student_performance_analytics(
        "BS in Computer Science with Specialization in Data Science",
        "math - 95\nscience - 88\nenglish - 78\ntechnology - 92\nbusiness - 80\nsocial - 75",
    )

    assert analytics["student_overall"] > 80
    assert analytics["selected_course"] == "BS in Computer Science with Specialization in Data Science"
    assert "comparison" in analytics
    assert "subject_breakdown" in analytics
    assert len(analytics["subject_breakdown"]) == 6


def test_merge_subject_entries_keeps_existing_and_new_unique_subjects():
    merged = _merge_subject_entries(
        "Oral Communication in Context - 88\nGeneral Mathematics - 90",
        "Oral Communication in Context - 88\nStatistics and Probability - 85",
    )

    assert "Oral Communication in Context - 88" in merged
    assert "General Mathematics - 90" in merged
    assert "Statistics and Probability - 85" in merged
    assert merged.count("Oral Communication in Context - 88") == 1


def test_student_record_matches_random_student_names_and_numbers():
    existing = {
        "student_number": "21-0001-123",
        "first_name": "MARIA",
        "middle_initial": "D",
        "last_name": "SANTOS",
        "course": "BSCS-DS",
    }
    incoming = {
        "studentNumber": "21-0001-123",
        "firstName": "Maria",
        "middleInitial": "D.",
        "lastName": "Santos",
        "course": "BSCS-DS",
    }

    assert _student_record_matches(existing, incoming) is True
    assert _student_record_matches(existing, {"studentNumber": "21-0003-777", "firstName": "Jose", "lastName": "Reyes", "course": "BSCS"}) is False


def test_extract_subject_scores_accepts_common_ocr_variants_and_subject_names():
    scores = _extract_subject_scores(
        "MATH 94\nSCIENCE 90\nENGLISH 88\nPROGRAMMING 96\nACCOUNTING 92\nSOCIAL STUDIES 86"
    )

    assert scores["math"]
    assert scores["science"]
    assert scores["english"]
    assert scores["technology"]
    assert scores["business"]
    assert scores["social"]


def test_recommend_course_returns_real_courses_for_broader_subject_patterns():
    recommendations = recommend_course(
        "math 94\nscience 90\nenglish 88\ncomputer programming 96\naccounting 92\nsocial studies 86"
    )

    assert isinstance(recommendations, list)
    assert len(recommendations) > 0
    assert all(item["course"] in {name for name, _ in app.UNIVERSITY_COURSES} for item in recommendations)


def test_subject_field_fit_prioritizes_biology_for_health_courses(monkeypatch):
    profile = [85, 85, 0, 0, 0, 0]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Civil Engineering", "features": profile, "description": "Infrastructure engineering"},
        {"course": "BS in Nursing", "features": profile, "description": "Patient-focused healthcare"},
    ])
    monkeypatch.setattr(app, "_COURSE_TRAINING_DATA_CACHE", None)

    recommendations = recommend_course("Biology - 100\nPhysics - 70\nMathematics - 85", strand="STEM")

    assert recommendations[0]["course"] == "BS in Nursing"
    assert recommendations[0]["confidence"] == recommendations[1]["confidence"]
    assert "related subject fields" in recommendations[0]["reason"]
    saved_payload = app._sanitize_recommendations([recommendations[0]])[0]
    assert saved_payload["field_fit"] is not None
    assert saved_payload["subject_field_evidence"]
    assert saved_payload["strand_alignment"] is True
    assert saved_payload["strand_label"] == "STEM"
    assert any(entry["priority"] == "major" for entry in saved_payload["subject_field_evidence"])


def test_major_subject_fields_outweigh_supporting_fields():
    field_scores = {field: [] for field in app._extract_subject_field_scores("")}
    field_scores["life_sciences"] = [60]
    field_scores["health_studies"] = [80]
    field_scores["physical_sciences"] = [70]
    field_scores["communication"] = [100]

    evidence = app._course_subject_field_evidence("Allied Health", field_scores)

    assert app._course_subject_field_fit("Allied Health", field_scores) == 73
    assert all(entry["priority"] == "major" for entry in evidence if entry["field"] != "Communication and languages")
    assert next(entry for entry in evidence if entry["field"] == "Communication and languages")["priority"] == "supporting"


def test_physical_education_is_supporting_not_core_nursing_evidence():
    field_scores = app._extract_subject_field_scores("Physical Education and Health 3 - 96")
    evidence = app._course_subject_field_evidence("Allied Health", field_scores)

    assert field_scores["physical_education"] == [96]
    assert field_scores["health_studies"] == []
    assert next(entry for entry in evidence if entry["field"] == "Physical education and fitness")["priority"] == "supporting"


def test_known_strands_filter_out_non_aligned_courses(monkeypatch):
    profile = [85, 85, 85, 85, 85, 85]
    training_data = [
        {"course": "BS in Civil Engineering", "features": profile, "description": "Infrastructure engineering"},
        {"course": "BS in Business Administration", "features": profile, "description": "Business operations"},
        {"course": "Bachelor of Physical Education", "features": profile, "description": "Physical education and sports instruction"},
        {"course": "Bachelor of Arts in Multimedia Arts", "features": profile, "description": "Digital media and creative design"},
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: training_data)
    subjects = "Mathematics - 85\nEnglish - 85"

    for strand, expected in [
        ("STEM", "BS in Civil Engineering"),
        ("SPORTS", "Bachelor of Physical Education"),
        ("ARTS_DESIGN", "Bachelor of Arts in Multimedia Arts"),
    ]:
        recommendations = recommend_course(subjects, strand=strand)
        assert recommendations
        assert all(item["strand_alignment"] for item in recommendations)
        assert recommendations[0]["course"] == expected

    sports_fields = app._extract_subject_field_scores("Physical Education and Health 3 - 96")
    sports_evidence = app._course_subject_field_evidence("Sports & Physical Education", sports_fields)
    assert app.categorize_course("Bachelor of Physical Education") == "Sports & Physical Education"
    assert sports_evidence[0] == {
        "field": "Physical education and fitness", "grade": 96.0, "priority": "major"
    }


def test_unrecognized_strand_does_not_filter_recommendations(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Mechanical Engineering",
        "features": [85, 85, 85, 85, 85, 85],
        "description": "Engineering course",
    }])

    recommendations = recommend_course("Mathematics - 85", strand="Unrecognized track")

    assert len(recommendations) == 1
    assert recommendations[0]["strand_alignment"] is False


def test_observed_weighted_distance_ignores_missing_categories():
    subject_scores = {name: [] for name in app.CATEGORY_NAMES}
    subject_scores["math"] = [90]
    first_profile = [90, 0, 0, 0, 0, 0]
    second_profile = [90, 100, 100, 100, 100, 100]

    assert app._observed_weighted_distance(subject_scores, first_profile) == 0
    assert app._observed_weighted_distance(subject_scores, second_profile) == 0


def test_course_comparison_excludes_categories_without_student_grades(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Mechanical Engineering",
        "features": [80, 90, 70, 60, 50, 40],
        "description": "Machines and manufacturing systems",
    }])

    analytics = app._build_student_performance_analytics("BS in Mechanical Engineering", "Mathematics - 90")

    math_row = next(row for row in analytics["comparison"] if row["subject"] == "math")
    science_row = next(row for row in analytics["comparison"] if row["subject"] == "science")
    assert analytics["student_overall"] == 90
    assert analytics["course_average"] == 80
    assert analytics["overall_gap"] == 10
    assert analytics["compared_subject_count"] == 1
    assert math_row["difference"] == 10
    assert science_row["student"] is None
    assert science_row["difference"] is None
    assert science_row["status"] == "no grade available"


def test_build_student_performance_analytics_rejects_placeholder_course_names():
    analytics = _build_student_performance_analytics(
        "General Education",
        "math - 95\nscience - 88\nenglish - 78\ntechnology - 92\nbusiness - 80\nsocial - 75",
    )

    assert analytics["selected_course"] not in {"General Education", "Other", "Recommended Course"}
    assert analytics["selected_course"] in {name for name, _ in app.UNIVERSITY_COURSES}


def test_university_catalog_uses_legacy_profiles_and_migrated_rows(monkeypatch):
    source_rows = [(source, [80, 80, 80, 80, 80, 80], "Source description")
                   for source in {source for _, source in app.UNIVERSITY_COURSES}]

    def load(rows):
        cursor = SimpleNamespace(execute=lambda *args: None, fetchall=lambda: rows)
        monkeypatch.setattr(app, "_db_conn", lambda: SimpleNamespace(cursor=lambda: cursor, close=lambda: None))
        monkeypatch.setattr(app, "_COURSE_TRAINING_DATA_CACHE", None)
        return app._course_training_data()

    expected = [name for name, _ in app.UNIVERSITY_COURSES]
    assert [item["course"] for item in load(source_rows)] == expected
    migrated = [(name, [80, 80, 80, 80, 80, 80], "Migrated description") for name in expected]
    catalog = load(migrated)
    assert [item["course"] for item in catalog] == expected
    assert next(item for item in catalog if item["course"] == "Aircraft Maintenance Technology")["description"] == app.COURSE_DESCRIPTION_OVERRIDES["Aircraft Maintenance Technology"]
    assert app._sanitize_recommendations([{"course": "Software Engineering"}, {"course": expected[0]}]) == [
        {"course": expected[0], "description": "", "reason": "", "category": app.categorize_course(expected[0]),
            "confidence": 0, "core_grade_fit": 0, "strand_grade_based": False, "field_fit": None,
            "subject_field_evidence": [], "strand_alignment": False, "strand_label": ""}
    ]


def test_parse_report_card_handles_dynamic_student_data_and_variable_subject_count():
    raw = {
        "student_info_raw": [
            {"label": "Student Number", "value": "21-1004-512"},
            {"label": "Student Name", "value": "ROSE ANN D. CRUZ"},
            {"label": "Program", "value": "BSIT"},
        ],
        "table": [
            {
                "row": 0,
                "cells": [
                    {"column": 0, "text": "Course"},
                    {"column": 1, "text": "Course Description"},
                    {"column": 2, "text": "Grade"},
                ],
            },
            {
                "row": 1,
                "cells": [
                    {"column": 0, "text": "CS 101"},
                    {"column": 1, "text": "Intro to Programming"},
                    {"column": 2, "text": "90.0"},
                ],
            },
            {
                "row": 2,
                "cells": [
                    {"column": 0, "text": "CS 102"},
                    {"column": 1, "text": "Data Structures"},
                    {"column": 2, "text": "87.0"},
                ],
            },
        ],
    }

    parsed = parse_report_card_structure(raw)

    assert parsed["student_no"] == "21-1004-512"
    assert parsed["student_name"] == "ROSE ANN D. CRUZ"
    assert parsed["major"] == "BSIT"
    assert len(parsed["subjects"]) == 2
    assert parsed["subjects"][0]["subject_name"] == "Intro to Programming"
    assert parsed["subjects"][0]["grade"] == 90.0
    assert parsed["gwa"] == 88.5


def test_validate_grade_keeps_questionable_ocr_flagged_and_calculates_gwa_from_known_values():
    uncertain = validate_grade("8A.0")
    invalid = validate_grade("ZZZ")
    result = calculate_gwa([
        {"subject_name": "Intro to Programming", "grade": 90.0},
        {"subject_name": "Data Structures", "grade": 87.0},
        {"subject_name": "General Physics", "grade": None, "needs_review": True},
    ])

    assert uncertain["status"] in {"VALID", "REVIEW"}
    assert invalid["status"] in {"INVALID", "REVIEW"}
    assert result == 88.5


def test_parse_report_card_handles_plain_ocr_lines_from_new_report_cards():
    raw = {
        "raw_text": "Grade Per Exam Period\nStudent ID : 20-0002-791\nStudent name : CARLOS, LAUKE ROMMEL PATINIO\nCourse/Major : BSIT-05\nYear 4\nEnglish 88\nMath 90\nProgramming 95",
        "student_info_raw": [
            {"text": "Grade Per Exam Period"},
            {"text": "Student ID : 20-0002-791"},
            {"text": "Student name : CARLOS, LAUKE ROMMEL PATINIO"},
            {"text": "Course/Major : BSIT-05"},
            {"text": "Year 4"},
            {"text": "English 88"},
            {"text": "Math 90"},
            {"text": "Programming 95"},
        ],
        "table": [],
    }

    parsed = parse_report_card_structure(raw)

    assert parsed["student_no"] == "20-0002-791"
    assert parsed["student_name"] == "CARLOS, LAUKE ROMMEL PATINIO"
    assert parsed["major"] == "BSIT-05"
    assert len(parsed["subjects"]) == 3
    assert any(item["subject_name"] == "English" for item in parsed["subjects"])
    assert parsed["gwa"] == 91.0


def test_build_ocr_review_payload_converts_paddleocr_boxes_to_report_card_structure():
    paddle_result = [
        [[
            [10, 20], [200, 20], [200, 60], [10, 60]
        ], ("Student Number", 0.99)],
        [[
            [220, 20], [420, 20], [420, 60], [220, 60]
        ], ("2024-00123", 0.97)],
        [[
            [10, 80], [210, 80], [210, 120], [10, 120]
        ], ("Student Name", 0.98)],
        [[
            [220, 80], [520, 80], [520, 120], [220, 120]
        ], ("Juan Dela Cruz", 0.96)],
        [[
            [10, 160], [150, 160], [150, 200], [10, 200]
        ], ("English", 0.98)],
        [[
            [160, 160], [210, 160], [210, 200], [160, 200]
        ], ("85", 0.95)],
        [[
            [10, 220], [170, 220], [170, 260], [10, 260]
        ], ("Mathematics", 0.97)],
        [[
            [170, 220], [220, 220], [220, 260], [170, 260]
        ], ("90", 0.96)],
    ]

    payload = parse_report_card_structure({
        "raw_text": "Student Number\n2024-00123\nStudent Name\nJuan Dela Cruz\nEnglish\n85\nMathematics\n90",
        "student_info_raw": [
            {"text": "Student Number"},
            {"text": "2024-00123"},
            {"text": "Student Name"},
            {"text": "Juan Dela Cruz"},
            {"text": "English"},
            {"text": "85"},
            {"text": "Mathematics"},
            {"text": "90"},
        ],
        "table": [],
    })

    assert payload["student_no"] == "2024-00123"
    assert payload["student_name"] == "Juan Dela Cruz"
    assert len(payload["subjects"]) == 2
    assert any(subject["subject_name"] == "English" and subject["grade"] == 85 for subject in payload["subjects"])
    assert any(subject["subject_name"] == "Mathematics" and subject["grade"] == 90 for subject in payload["subjects"])
