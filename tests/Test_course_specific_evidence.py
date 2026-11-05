import asyncio
import json
import math
from types import SimpleNamespace

import pytest

import fastapi_app as app


@pytest.fixture
def entrepreneurship_catalog(monkeypatch):
    courses = [
        "BS in Business Administration major in Marketing Management",
        "BS in Business Administration",
        "BS in Business Administration major in Human Resource Management",
        "BS in Accountancy",
        "BS in Business Administration major in Financial Management",
        "BS in Entrepreneurship",
    ]
    catalog = [
        {"course": name, "description": "", "features": [75, 65, 85, 65, 95, 75], "strands": ["BE"]}
        for name in courses
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: catalog)
    monkeypatch.setattr(app, "_build_course_model", lambda *args, **kwargs: SimpleNamespace(
        classes_=courses,
        predict_log_proba=lambda query: [[math.log(value / 63) for value in (32, 16, 8, 4, 2, 1)]],
    ))
    return catalog


@pytest.mark.parametrize("limit", [1, 2, 3, 5])
def test_strongest_entrepreneurship_gets_a_slot_without_changing_other_model_ranks(monkeypatch, entrepreneurship_catalog, limit):
    monkeypatch.setattr(app, "_get_system_settings", lambda: {
        **app.SYSTEM_SETTING_DEFAULTS, "recommendation_limit": limit,
    })
    subjects = "Entrepreneurship - 99\nBusiness Finance - 90\nBusiness Economics - 80\nEnglish - 91"
    ranked = app._rank_course_recommendations(subjects, strand="BE")
    assert ranked[-1]["course"] == "BS in Entrepreneurship"
    result = app._configured_recommendations(subjects, strand="BE")
    assert len(result) == limit
    assert [item["course"] for item in result[:-1]] == [item["course"] for item in ranked[:limit - 1]]
    assert result[-1]["course"] == "BS in Entrepreneurship"
    assert result[-1]["selection_basis"] == "subject_evidence"
    assert "Included because Entrepreneurship" in result[-1]["reason"]
    assert result[-1]["match_score"] == ranked[-1]["match_score"]
    assert result[-1]["confidence"] == ranked[-1]["confidence"]
    assert app.recommend_course(subjects, strand="BE")[-1]["course"] == "BS in Entrepreneurship"


@pytest.mark.parametrize("subjects", [
    "Business Finance - 99\nEconomics - 90",
    "Entrepreneurship - 80\nBusiness Finance - 99",
    "Entrepreneurship 1 - 99\nEntrepreneurship 2 - 75\nEnglish - 90",
])
def test_missing_or_weaker_entrepreneurship_does_not_override_model(monkeypatch, entrepreneurship_catalog, subjects):
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    ranked = app._rank_course_recommendations(subjects, strand="BE")
    result = app._configured_recommendations(subjects, strand="BE")
    assert [item["course"] for item in result] == [item["course"] for item in ranked[:3]]
    assert all(item.get("selection_basis") != "subject_evidence" for item in result)


def test_entrepreneurship_selection_respects_cluster_and_alternative_limit(monkeypatch, entrepreneurship_catalog):
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    result = app._configured_recommendations("Entrepreneurship - 99\nMathematics - 99", strand="STEM")
    assert [item["course"] for item in result] == ["BS in Entrepreneurship"]
    assert result[0]["recommendation_basis"] == "grade_alternative"
    assert result[0]["selection_basis"] == "subject_evidence"
    entrepreneurship_catalog.pop()
    result = app._configured_recommendations("Entrepreneurship - 99", strand="BE")
    assert len(result) == 3
    assert not any(item["course"] == "BS in Entrepreneurship" for item in result)


def test_entrepreneurship_already_displayed_is_not_duplicated_or_reordered(monkeypatch, entrepreneurship_catalog):
    entrepreneurship_catalog[:] = entrepreneurship_catalog[-1:] + entrepreneurship_catalog[:2]
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    subjects = "Entrepreneurship - 99"
    ranked = app._rank_course_recommendations(subjects, strand="BE")
    result = app._configured_recommendations(subjects, strand="BE")
    assert [item["course"] for item in result] == [item["course"] for item in ranked]
    assert len({item["course"] for item in result}) == len(result)
    assert all(item.get("selection_basis") != "subject_evidence" for item in result)


@pytest.mark.parametrize(("course", "description", "broad", "specific", "field"), [
    ("Bachelor of Early Childhood Education", "", "Social Studies - 99", "Child Development - 90", "Teaching and learner development"),
    ("Bachelor of Elementary Education", "", "Philippine Politics and Governance - 99", "Teaching Methods - 90", "Teaching and learner development"),
    ("Bachelor of Secondary Education", "", "English - 99", "Teacher Education - 90", "Teaching and learner development"),
    ("Bachelor of Special Needs Education", "", "Social Studies - 99", "Inclusive Education - 90", "Teaching and learner development"),
    ("Bachelor of Arts in Psychology", "", "Social Studies - 99", "Psychology - 90", "Psychology and human behavior"),
    ("Bachelor of Science in Psychology", "", "Biology - 99", "Personal Development - 90", "Psychology and human behavior"),
    ("Bachelor of Arts major in Political Science", "", "Social Studies - 99", "Philippine Politics and Governance - 90", "Politics and governance"),
    ("BS in Criminology", "", "Philippine Politics and Governance - 99", "Criminal Justice - 90", "Criminal justice and law enforcement"),
    ("BS in Nursing", "Patient care", "Earth Science - 99", "Biology - 90", "Biology and life sciences"),
    ("BS in Physical Therapy", "Patient rehabilitation", "Earth and Space Science - 99", "Anatomy - 90", "Biology and life sciences"),
    ("BS in Pharmacy", "", "English - 99\nEarth Science - 99", "Chemistry - 90", "Chemistry"),
    ("BS in Radiologic Technology", "", "Earth Science - 99", "Physics - 90", "Physics"),
    ("Bachelor of Arts in Communication", "", "Philosophy - 99", "Oral Communication - 90", "Communication and languages"),
    ("BS in Tourism Management", "", "English - 99\nBusiness - 99", "Tour Guiding Services - 90", "Tourism and hospitality"),
    ("BS in Hospitality Management", "", "English - 99", "Nihongo - 90", "Foreign languages"),
    ("BS in Computer Science with Specialization in Data Science", "", "Pre-Calculus - 90\nAdvanced Mathematics - 87\nGeneral Mathematics - 77", "Computer Programming - 90", "Programming and software development"),
    ("BS in Information Technology with Specialization in Game Development", "", "Empowerment Technologies - 99\nMathematics - 99", "Coding - 90", "Programming and software development"),
    ("Bachelor of Science in Software Engineering", "", "Physics - 99\nMathematics - 99", "Software Development - 90", "Programming and software development"),
    ("BS in Computer Engineering", "", "Pre-Calculus - 99\nAdvanced Mathematics - 99\nStatistics and Probability - 99\nPhysics - 99\nEmpowerment Technologies - 89", "Computer Programming - 90", "Programming and software development"),
    ("Bachelor of Science in Computer Engineering", "", "Mathematics - 99\nPhysics - 99", "Coding - 90", "Programming and software development"),
    ("BS in Architecture", "", "Conceptual Biology and Earth and Space Science - 92\nGeneral Chemistry 1 - 91\nPre-Calculus - 90", "Technical Drafting - 90", "Architectural design and drafting"),
])
def test_program_requires_specific_evidence(monkeypatch, course, description, broad, specific, field):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": course, "description": description,
        "features": [80, 90, 85, 65, 70, 88], "strands": ["ARSSH"],
    }])
    assert app.recommend_course(broad, strand="ARSSH") == []
    assert app._grade_based_alternatives(broad, "STEM") == []
    for matches in (
        app.recommend_course(f"{broad}\n{specific}", strand="ARSSH"),
        app._grade_based_alternatives(f"{broad}\n{specific}", "STEM"),
    ):
        assert [item["course"] for item in matches] == [course]
        assert any(item["field"] == field and item["priority"] == "major"
                   for item in matches[0]["subject_field_evidence"])


@pytest.mark.parametrize(("course", "subject", "field"), [
    ("BS in Computer Science", "Python Programming 1", "programming"),
    ("BS in Computer Science", "Java", "programming"),
    ("BS in Computer Science", "Data Structures and Algorithms", "programming"),
    ("BS in Computer Science", "C++", "programming"),
    ("BS in Computer Science", "C#", "programming"),
    ("BS in Architecture", "Technical Drawing", "architectural_design"),
    ("BS in Architecture", "Architectural Design", "architectural_design"),
    ("BS in Architecture", "Computer-Aided Design", "architectural_design"),
    ("BS in Architecture", "AutoCAD", "architectural_design"),
    ("BS in Architecture", "CAD", "architectural_design"),
])
def test_specialized_subject_aliases_provide_real_grade_evidence(monkeypatch, course, subject, field):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": course, "description": "",
        "features": [90, 85, 80, 90, 70, 70], "strands": ["STEM"],
    }])
    subjects = f"{subject} - 90"
    assert app._extract_subject_field_scores(subjects)[field] == [90]
    assert app._extract_subject_scores(subjects)["technology"] == [90]
    assert app.recommend_course(subjects, strand="STEM")


@pytest.mark.parametrize("subject", ["Empowerment Technologies", "Computer Literacy", "ICT", "Mathematics", "Computer Science"])
@pytest.mark.parametrize("course", ["BS in Computer Science", "BS in Computer Engineering"])
def test_generic_technology_or_math_does_not_imply_programming(monkeypatch, subject, course):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": course, "description": "",
        "features": [90, 84, 80, 92, 72, 74], "strands": ["STEM"],
    }])
    assert app.recommend_course(f"{subject} - 99", strand="STEM") == []


def test_computer_engineering_requires_programming_without_blocking_civil_engineering(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": name, "description": "", "features": features, "strands": ["STEM"]}
        for name, features in [
            ("BS in Computer Engineering", [91, 95, 81, 72, 65, 68]),
            ("BS in Civil Engineering", [90, 95, 78, 60, 60, 72]),
        ]
    ])
    subjects = "Pre-Calculus - 99\nPhysics - 99\nEmpowerment Technologies - 89"
    assert [item["course"] for item in app.recommend_course(subjects, strand="STEM")] == ["BS in Civil Engineering"]
    results = app.recommend_course(subjects + "\nComputer Programming - 90", strand="STEM")
    assert {item["course"] for item in results} == {"BS in Civil Engineering", "BS in Computer Engineering"}
    evidence = next(item["subject_field_evidence"] for item in results if item["course"] == "BS in Computer Engineering")
    assert {item["field"] for item in evidence if item["priority"] == "major"} == {"Programming and software development"}
    assert {item["field"] for item in evidence if item["priority"] == "supporting"} >= {
        "Math and quantitative subjects", "Physics and physical sciences",
    }


@pytest.mark.parametrize("subject", ["Art Appreciation", "Creative Writing", "Design Thinking", "Fashion Design"])
def test_generic_art_does_not_imply_architectural_design(monkeypatch, subject):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Architecture", "description": "",
        "features": [82, 86, 80, 66, 68, 72], "strands": ["STEM"],
    }])
    assert app.recommend_course(f"{subject} - 99\nPre-Calculus - 90", strand="STEM") == []


def test_screenshot_math_and_science_only_qualify_pharmacy_not_cs_or_architecture(monkeypatch):
    courses = [
        ("BS in Computer Science with Specialization in Data Science", [90, 84, 80, 92, 72, 74]),
        ("BS in Pharmacy", [84, 91, 78, 66, 71, 72]),
        ("BS in Architecture", [82, 86, 80, 66, 68, 72]),
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": name, "features": features, "description": "", "strands": ["STEM"]}
        for name, features in courses
    ])
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    subjects = (
        "Pre-Calculus - 90\nAdvanced Mathematics - 87\nGeneral Mathematics - 77\n"
        "Conceptual Biology and Earth and Space Science - 92\n"
        "General Chemistry 1 - 91\nGeneral Chemistry 2 - 89\n"
        "Contemporary Literature 1 - 91\nContemporary Literature 2 - 90\n"
        "English for Academic and Professional Purposes - 91"
    )
    results = app._configured_recommendations(subjects, strand="STEM")
    assert [item["course"] for item in results] == ["BS in Pharmacy"]
    results = app._configured_recommendations(
        subjects + "\nComputer Programming - 92\nTechnical Drawing - 90", strand="STEM",
    )
    assert {item["course"] for item in results} == {name for name, _ in courses}


@pytest.mark.parametrize("subjects", [
    "Physics - 99\nChemistry - 99\nEarth Science - 83",
    "Mathematics - 99\nChemistry - 99\nEarth Science - 83",
    "Navigation - 99",
])
def test_marine_transportation_requires_quantitative_and_navigation_evidence(monkeypatch, subjects):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Marine Transportation", "description": "Ship operations",
        "features": [69, 80, 84, 57, 90, 84], "strands": ["STEM"],
    }])
    assert app.recommend_course(subjects, strand="STEM") == []
    assert app._grade_based_alternatives(subjects, "ARSSH") == []
    assert app.recommend_course(f"{subjects}\nMathematics - 90\nPhysics - 90", strand="STEM")


def test_screenshot_subjects_do_not_fill_slots_with_unsupported_programs(monkeypatch):
    courses = [
        ("Bachelor of Early Childhood Education", [74, 82, 92, 58, 66, 88], "ARSSH"),
        ("Bachelor of Elementary Education", [74, 82, 92, 58, 66, 88], "ARSSH"),
        ("BS in Criminology", [70, 79, 90, 58, 76, 88], "ARSSH"),
        ("Bachelor of Arts major in Political Science", [70, 76, 90, 60, 84, 82], "ARSSH"),
        ("BS in Marine Transportation", [69, 80, 84, 57, 90, 84], "STEM"),
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": name, "features": features, "strands": [strand], "description": ""}
        for name, features, strand in courses
    ])
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    subjects = (
        "Philippine Politics and Governance - 99\n"
        "The Social Sciences in Theory and Practice - 99\n"
        "Introduction to the Philosophy of the Human Person - 99\n"
        "Filipino 1 - 95\nEnglish for Academic and Professional Purposes - 91\n"
        "Contemporary Literature 1 - 88\nGeneral Physics 2 - 99\n"
        "General Chemistry 2 - 99\nEarth Science - 83"
    )
    results = app._configured_recommendations(subjects, strand="ARSSH")
    assert [item["course"] for item in results] == ["Bachelor of Arts major in Political Science"]
    majors = [item["field"] for item in results[0]["subject_field_evidence"] if item["priority"] == "major"]
    assert majors == ["Politics and governance"]


def test_guest_returns_empty_list_without_success_shaped_course_filler(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "Bachelor of Elementary Education", "description": "",
        "features": [74, 82, 92, 58, 66, 88], "strands": ["ARSSH"],
    }])
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)

    async def request_json():
        return {"subjects": "Social Studies - 99\nEnglish - 99", "strand": "ARSSH"}

    response = asyncio.run(app.recommend_anonymous(SimpleNamespace(
        session={"is_guest": True}, json=request_json,
    )))
    result = json.loads(response.body)
    assert result["success"] is True
    assert result["recommendation"] == []
    assert result["comparisons"] == {}


def test_saved_student_results_remove_stale_unsupported_program(monkeypatch):
    course = "Bachelor of Elementary Education"
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": course, "description": "",
        "features": [74, 82, 92, 58, 66, 88], "strands": ["ARSSH"],
    }])
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    old_results = json.dumps([{"course": course, "strand_label": "ARSSH", "reason": "Old broad category match"}])
    row = ("1", "Test", "", "Student", "", "99", "99", "Social Studies - 99",
           old_results, "ARSSH", 7)
    writes = []
    cursor = SimpleNamespace(
        execute=lambda *args: writes.append(args),
        fetchone=lambda: row,
    )
    connection = SimpleNamespace(
        cursor=lambda: cursor, close=lambda: None,
        commit=lambda: writes.append(("commit",)),
    )
    monkeypatch.setattr(app, "_db_conn", lambda: connection)

    response = app.get_profile(SimpleNamespace(session={"user_id": 1}))
    result = json.loads(response.body)
    assert result["recommendation"] == []
    assert result["comparisons"] == {}
    update = next(args for args in writes if "UPDATE student_profiles" in args[0])
    assert json.loads(update[1][0]) == []
    assert ("commit",) in writes


@pytest.mark.parametrize("admin", [False, True])
def test_recalculation_clears_previous_results_when_evidence_is_insufficient(monkeypatch, admin):
    course = {
        "course": "Bachelor of Elementary Education", "description": "",
        "features": [74, 82, 92, 58, 66, 88], "strands": ["ARSSH"],
    }
    monkeypatch.setattr(app, "_course_training_data", lambda: [course])
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    monkeypatch.setattr(app, "_is_full_admin_session", lambda session: True)
    monkeypatch.setattr(app, "_record_admin_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_record_student_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_get_user_latest_profile", lambda user_id: {
        "subjects": "Social Studies - 99", "course": "", "strand": "ARSSH",
    })
    writes = []
    cursor = SimpleNamespace(
        execute=lambda *args: writes.append(args),
        fetchone=lambda: ("Social Studies - 99", "", "ARSSH") if admin else (7,),
    )
    connection = SimpleNamespace(
        cursor=lambda: cursor, close=lambda: None,
        commit=lambda: writes.append(("commit",)),
    )
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    request = SimpleNamespace(session={"user_id": 1})
    response = (
        app.admin_recalculate_recommendations(request, "7") if admin
        else asyncio.run(app.generate_recommendations(request))
    )
    result = json.loads(response.body)
    assert result["success"] is True and result["count"] == 0
    assert "Previous suggestions were cleared" in result["message"]
    update = next(args for args in writes if "UPDATE student_profiles" in args[0])
    assert json.loads(update[1][0]) == []
    assert any("INSERT INTO course_recommendation_history" in args[0] for args in writes)
    assert ("commit",) in writes
