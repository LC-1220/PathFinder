import asyncio
import json
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


def test_course_average_profile_returns_subject_averages(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Computer Science with Specialization in Data Science",
        "features": [90, 84, 80, 92, 72, 74],
        "description": "Computer science profile",
        "strands": ["STEM"],
    }])
    course_data = _course_average_profile("BS in Computer Science with Specialization in Data Science")

    assert course_data["course"] == "BS in Computer Science with Specialization in Data Science"
    assert course_data["overall_average"] > 70
    assert "math" in course_data["by_subject"]
    assert course_data["by_subject"]["technology"] >= 80


def test_student_performance_analytics_computes_comparison(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Computer Science with Specialization in Data Science",
        "features": [90, 84, 80, 92, 72, 74],
        "description": "Computer science profile",
        "strands": ["STEM"],
    }])
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


def test_recommend_course_only_returns_courses_for_the_requested_strand(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Computer Science with Specialization in Data Science", "features": [90, 84, 80, 92, 72, 74], "description": "Computer science", "strands": ["STEM"]},
        {"course": "BS in Accountancy", "features": [79, 70, 78, 64, 97, 70], "description": "Accountancy", "strands": ["ABM"]},
    ])
    recommendations = recommend_course(
        "math 94\nscience 90\nenglish 88\ncomputer programming 96\naccounting 92\nsocial studies 86",
        strand="Science, Technology, Engineering and Mathematics (STEM)",
    )

    assert isinstance(recommendations, list)
    assert len(recommendations) > 0
    assert all(item["course"] == "BS in Computer Science with Specialization in Data Science" for item in recommendations)


def test_missing_strand_ranks_all_courses_from_highest_grades(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Accountancy", "features": [80, 80, 80, 80, 96, 80], "description": "Accounting and finance", "strands": ["ABM"]},
        {"course": "BS in Nursing", "features": [80, 96, 80, 60, 70, 80], "description": "Patient care", "strands": ["STEM"]},
        {"course": "BS in Information Technology with Specialization in Game Development", "features": [80, 80, 80, 96, 70, 80], "description": "Computing and software", "strands": ["TVL_ICT"]},
    ])
    subjects = "Mathematics - 80\nScience - 80\nEnglish - 80\nComputer Programming - 80\nAccounting - 96\nSocial Studies - 80"

    for missing_strand in ("", "Other", "Not detected", "N/A"):
        recommendations = recommend_course(subjects, strand=missing_strand)

        assert recommendations
        assert recommendations[0]["course"] == "BS in Accountancy"
        assert all(not item["strand_alignment"] for item in recommendations)
        assert all(not item["strand_label"] for item in recommendations)
        assert "highest grade areas" in recommendations[0]["reason"].lower()


def test_generic_tvl_recommends_union_of_tvl_strands(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Information Technology", "features": [80, 80, 80, 90, 80, 80], "description": "ICT", "strands": ["TVL_ICT", "TVL"]},
        {"course": "BS in Hospitality Management", "features": [80, 80, 80, 80, 90, 80], "description": "Home Economics", "strands": ["TVL_HE", "TVL"]},
        {"course": "BS in Mechanical Engineering", "features": [90, 90, 80, 80, 70, 70], "description": "Industrial Arts", "strands": ["TVL_IA", "TVL"]},
        {"course": "BS in Accountancy", "features": [80, 80, 80, 70, 95, 80], "description": "ABM", "strands": ["ABM"]},
    ])
    subjects = "math 85\nscience 85\nenglish 85\ntechnology 85\nbusiness 85\nsocial studies 85"

    broad_tvl = recommend_course(subjects, strand="TVL Track A")
    ict_only = recommend_course(subjects, strand="Information and Communications Technology (ICT)")

    assert {item["course"] for item in broad_tvl} == {
        "BS in Information Technology",
        "BS in Hospitality Management",
        "BS in Mechanical Engineering",
    }
    assert {item["course"] for item in ict_only} == {"BS in Information Technology"}


def test_high_nihongo_grade_supports_tourism_courses_for_tvl_home_economics(monkeypatch):
    profile = [80, 80, 85, 70, 88, 78]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Hospitality Management", "features": profile, "description": "Hospitality and tourism services", "strands": ["TVL_HE", "TVL"]},
        {"course": "BS in Tourism Management", "features": profile, "description": "Tourism and travel services", "strands": ["TVL_HE", "TVL"]},
    ])
    subjects = (
        "Nihongo 1 - 96\nGeneral Mathematics - 80\nBiology - 80\n"
        "English - 82\nAccounting - 80\nSocial Studies - 80"
    )

    recommendations = recommend_course(subjects, strand="Home Economics (HE)")
    language_fields = app._extract_subject_field_scores(subjects)

    assert language_fields["foreign_languages"] == [96.0]
    assert recommendations
    assert recommendations[0]["course"] in {"BS in Hospitality Management", "BS in Tourism Management"}
    assert "language" in recommendations[0]["reason"].lower()


def test_biology_and_chemistry_strengths_prioritize_related_courses(monkeypatch):
    profile = [80, 80, 80, 80, 80, 80]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Nursing", "features": profile, "description": "Patient care", "strands": ["STEM"]},
        {"course": "BS in Pharmacy", "features": profile, "description": "Medicines and pharmaceutical chemistry", "strands": ["STEM"]},
        {"course": "BS in Medical Technology", "features": profile, "description": "Clinical laboratory diagnostics", "strands": ["STEM"]},
        {"course": "BS in Mechanical Engineering", "features": profile, "description": "Mechanical systems", "strands": ["STEM"]},
    ])
    common_grades = "Mathematics - 80\nEnglish - 80\nComputer Programming - 80\nAccounting - 80\nSocial Studies - 80"

    biology_recommendations = recommend_course(
        f"Biology - 96\nChemistry - 80\n{common_grades}", strand="STEM"
    )
    chemistry_recommendations = recommend_course(
        f"Biology - 80\nChemistry - 96\n{common_grades}", strand="STEM"
    )

    assert biology_recommendations[0]["course"] == "BS in Nursing"
    assert "biology" in biology_recommendations[0]["reason"].lower()
    assert chemistry_recommendations[0]["course"] == "BS in Pharmacy"
    assert "chemistry" in chemistry_recommendations[0]["reason"].lower()


def test_high_tourism_grade_prioritizes_hospitality_management_for_tvl(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Hospitality Management", "features": [68, 72, 86, 63, 94, 82], "description": "Hospitality and tourism", "strands": ["TVL_HE", "TVL"]},
        {"course": "Aircraft Maintenance Technology", "features": [91, 93, 77, 66, 61, 71], "description": "Industrial Arts", "strands": ["TVL_IA", "TVL"]},
        {"course": "BS in Information Technology", "features": [78, 72, 78, 90, 70, 68], "description": "ICT", "strands": ["TVL_ICT", "TVL"]},
    ])
    subjects = (
        "Tourism Production Services 1 - 96\n"
        "Entrepreneurship - 93\n"
        "Physical Education and Health 3 - 96\n"
        "English for Academic and Professional Purposes - 92\n"
        "Pagsulat sa Filipino sa Piling Larangan - 91\n"
        "21st Century Literature from the Philippines and the World - 90"
    )

    recommendations = recommend_course(subjects, strand="TVL Track A")

    assert recommendations[0]["course"] == "BS in Hospitality Management"
    assert recommendations[0]["match_score"] == max(item["match_score"] for item in recommendations)
    assert recommendations[0]["match_score"] > recommendations[0]["confidence"]
    assert "tourism-related subject average is 96.0" in recommendations[0]["reason"]


def test_subject_field_fit_prioritizes_biology_for_health_courses(monkeypatch):
    profile = [85, 85, 0, 0, 0, 0]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Civil Engineering", "features": profile, "description": "Infrastructure engineering", "strands": ["STEM"]},
        {"course": "BS in Nursing", "features": profile, "description": "Patient-focused healthcare", "strands": ["STEM"]},
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


def test_math_subtopics_and_physics_prioritize_related_engineering(monkeypatch):
    profile = [80, 80, 80, 80, 80, 80]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Civil Engineering", "features": profile, "description": "Civil engineering", "strands": ["STEM"]},
        {"course": "BS in Industrial Engineering", "features": profile, "description": "Industrial engineering", "strands": ["STEM"]},
        {"course": "BS in Mechanical Engineering", "features": profile, "description": "Mechanical engineering", "strands": ["STEM"]},
        {"course": "BS in Electrical Engineering", "features": profile, "description": "Electrical engineering", "strands": ["STEM"]},
        {"course": "BS in Nursing", "features": profile, "description": "Healthcare", "strands": ["STEM"]},
    ])
    common = "General Mathematics - 80\nEnglish - 80\nComputer Programming - 80\nAccounting - 80\nSocial Studies - 80"

    calculus_recommendations = recommend_course(
        f"Calculus - 96\nGeometry - 84\nStatistics - 78\nPhysics - 80\n{common}", strand="STEM"
    )
    statistics_recommendations = recommend_course(
        f"Calculus - 78\nGeometry - 80\nStatistics - 96\nPhysics - 80\n{common}", strand="STEM"
    )
    physics_recommendations = recommend_course(
        f"Calculus - 78\nGeometry - 80\nStatistics - 80\nPhysics - 96\n{common}", strand="STEM"
    )

    assert calculus_recommendations[0]["course"] == "BS in Civil Engineering"
    assert "Calculus" in calculus_recommendations[0]["reason"]
    assert statistics_recommendations[0]["course"] == "BS in Industrial Engineering"
    assert "Statistics" in statistics_recommendations[0]["reason"]
    assert physics_recommendations[0]["course"] in {"BS in Mechanical Engineering", "BS in Electrical Engineering"}
    assert "Physics" in physics_recommendations[0]["reason"]


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
        {"course": "BS in Civil Engineering", "features": profile, "description": "Infrastructure engineering", "strands": ["STEM"]},
        {"course": "BS in Business Administration", "features": profile, "description": "Business operations", "strands": ["ABM"]},
        {"course": "Bachelor of Physical Education", "features": profile, "description": "Physical education and sports instruction", "strands": ["SPORTS"]},
        {"course": "Bachelor of Arts in Multimedia Arts", "features": profile, "description": "Digital media and creative design", "strands": ["ARTS_DESIGN"]},
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


def test_unrecognized_strand_returns_no_recommendations(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Mechanical Engineering",
        "features": [85, 85, 85, 85, 85, 85],
        "description": "Engineering course",
    }])

    recommendations = recommend_course("Mathematics - 85", strand="Unrecognized track")

    assert recommendations == []


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


def test_build_student_performance_analytics_rejects_placeholder_course_names(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Computer Science with Specialization in Data Science",
        "features": [90, 84, 80, 92, 72, 74],
        "description": "Computer science profile",
        "strands": ["STEM"],
    }])
    analytics = _build_student_performance_analytics(
        "General Education",
        "math - 95\nscience - 88\nenglish - 78\ntechnology - 92\nbusiness - 80\nsocial - 75",
    )

    assert analytics["selected_course"] not in {"General Education", "Other", "Recommended Course"}
    assert analytics["selected_course"] == "BS in Computer Science with Specialization in Data Science"


def test_university_catalog_loads_course_strand_assignments(monkeypatch):
    rows = [
        ("BS in Computer Science with Specialization in Data Science", [80, 80, 80, 80, 80, 80], "STEM profile", ["STEM", "TVL_ICT"]),
        ("BS in Accountancy", [80, 80, 80, 80, 80, 80], "ABM profile", ["ABM"]),
    ]
    executed = []

    def load():
        cursor = SimpleNamespace(execute=lambda *args: executed.append(args[0]), fetchall=lambda: rows)
        monkeypatch.setattr(app, "_db_conn", lambda: SimpleNamespace(cursor=lambda: cursor, close=lambda: None))
        monkeypatch.setattr(app, "_COURSE_TRAINING_DATA_CACHE", None)
        return app._course_training_data()

    catalog = load()
    expected = [item["course"] for item in catalog]
    assert expected == ["BS in Computer Science with Specialization in Data Science", "BS in Accountancy"]
    assert catalog[0]["strands"] == ["STEM", "TVL_ICT"]
    assert "FROM university_courses" in executed[0]
    assert "university_course_strands" in executed[0]
    assert app._sanitize_recommendations([{"course": "Software Engineering"}, {"course": expected[0]}]) == [
        {"course": expected[0], "description": "", "reason": "", "category": app.categorize_course(expected[0]),
            "confidence": 0, "match_score": 0, "core_grade_fit": 0, "strand_grade_based": False, "field_fit": None,
            "subject_field_evidence": [], "strand_alignment": False, "strand_label": ""}
    ]


def test_missing_catalog_returns_actionable_json_error_and_closes_connection(monkeypatch):
    closed = []

    def fail_query(*_args):
        raise app.psycopg.errors.UndefinedTable('relation "university_courses" does not exist')

    cursor = SimpleNamespace(execute=fail_query)
    connection = SimpleNamespace(cursor=lambda: cursor, close=lambda: closed.append(True))
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    monkeypatch.setattr(app, "_COURSE_TRAINING_DATA_CACHE", None)

    with pytest.raises(app.CourseCatalogSchemaMissing, match="Apply supabase_schema.sql") as error:
        app._course_training_data()

    response = asyncio.run(app.course_catalog_schema_missing_handler(None, error.value))
    assert response.status_code == 503
    assert json.loads(response.body)["success"] is False
    assert closed == [True]


def test_public_settings_exposes_recommendation_limit(monkeypatch):
    settings = {**app.SYSTEM_SETTING_DEFAULTS, "recommendation_limit": 5}
    monkeypatch.setattr(app, "_get_system_settings", lambda: settings)

    response = app.public_system_settings(SimpleNamespace())

    assert response.status_code == 200
    assert json.loads(response.body)["settings"]["recommendation_limit"] == 5


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
