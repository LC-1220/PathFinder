import pytest

import fastapi_app as app


@pytest.fixture
def catalog(monkeypatch):
    courses = [
        {
            "course": "Bachelor of Physical Education",
            "features": [73, 69, 91, 59, 66, 90],
            "description": "Physical education and sports instruction",
            "strands": ["ARSSH"],
        },
        {
            "course": "BS in Criminology",
            "features": [70, 79, 90, 58, 76, 88],
            "description": "Crime and justice systems",
            "strands": ["ARSSH"],
        },
        {
            "course": "Bachelor of Secondary Education",
            "features": [77, 76, 92, 64, 69, 87],
            "description": "Teaching at the secondary school level",
            "strands": ["ARSSH"],
        },
        {
            "course": "BS in Entrepreneurship",
            "features": [75, 65, 87, 66, 99, 69],
            "description": "Business creation and management",
            "strands": ["BE"],
        },
        {
            "course": "BS in Tourism Management",
            "features": [68, 72, 88, 62, 92, 84],
            "description": "Tourism and travel",
            "strands": ["HT"],
        },
        {
            "course": "BS in Information Technology",
            "features": [80, 75, 75, 97, 75, 75],
            "description": "Information technology",
            "strands": ["ICTECH"],
        },
        {
            "course": "BS in Nursing",
            "features": [80, 90, 79, 60, 70, 74],
            "description": "Patient care",
            "strands": ["STEM"],
        },
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: courses)
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)
    return courses


@pytest.fixture
def screenshot_subjects():
    return "\n".join([
        "Filipino 1 (Wika at Komunikasyon sa Akademikong Filipino) - 95",
        "The Social Sciences in Theory and Practice - 90",
        "Philippine Politics and Governance - 84",
        "Contemporary Literature 1 - 88",
        "Contemporary Literature 2 - 87",
    ])


def test_cluster_matches_require_major_related_grades(catalog, screenshot_subjects):
    recommendations = app.recommend_course(screenshot_subjects, strand="ARSSH")
    assert recommendations == []
    recommendations = app.recommend_course(
        screenshot_subjects + "\nCriminal Justice - 91\nTeaching Methods - 90", strand="ARSSH"
    )

    assert {item["course"] for item in recommendations} == {
        "BS in Criminology", "Bachelor of Secondary Education",
    }
    assert all(
        any(field["priority"] == "major" for field in item["subject_field_evidence"])
        for item in recommendations
    )


def test_alternative_does_not_match_business_from_english_alone(catalog, screenshot_subjects):
    alternatives = app._grade_based_alternatives(screenshot_subjects, "ARSSH")

    assert "BS in Entrepreneurship" not in {item["course"] for item in alternatives}
    assert "BS in Tourism Management" not in {item["course"] for item in alternatives}
    alternatives = app._grade_based_alternatives(screenshot_subjects + "\nTour Guiding Services - 90", "ARSSH")
    tourism = next(item for item in alternatives if item["course"] == "BS in Tourism Management")
    assert any(
        field["field"] == "Tourism and hospitality" and field["priority"] == "major"
        for field in tourism["subject_field_evidence"]
    )
    assert tourism["field_fit"] is not None
    assert all(item["strand_alignment"] is False for item in alternatives)


@pytest.mark.parametrize(("subject", "strand", "expected"), [
    ("Entrepreneurship - 96", "ARSSH", "BS in Entrepreneurship"),
    ("Computer Programming - 97", "BE", "BS in Information Technology"),
    ("Criminal Justice - 93", "STEM", "BS in Criminology"),
])
def test_alternatives_use_non_core_major_subjects(catalog, subject, strand, expected):
    alternatives = app._grade_based_alternatives(subject, strand)

    match = next(item for item in alternatives if item["course"] == expected)
    assert any(field["priority"] == "major" for field in match["subject_field_evidence"])
    assert match["field_fit"] is not None
    assert match["recommendation_basis"] == "grade_alternative"


def test_no_major_evidence_does_not_fill_either_group(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {
            "course": "Bachelor of Physical Education",
            "features": [73, 69, 91, 59, 66, 90],
            "description": "Physical education",
            "strands": ["ARSSH"],
        },
        {
            "course": "BS in Entrepreneurship",
            "features": [75, 65, 87, 66, 99, 69],
            "description": "Business",
            "strands": ["BE"],
        },
    ])
    monkeypatch.setattr(app, "_get_system_settings", lambda: app.SYSTEM_SETTING_DEFAULTS)

    assert app._configured_recommendations("English - 87.5", strand="ARSSH") == []


def test_generic_science_and_social_science_do_not_imply_specific_majors(catalog):
    science = app._grade_based_alternatives("General Science - 90", "BE")
    social = app._grade_based_alternatives("Social Science - 90", "BE")
    biology = app._grade_based_alternatives("Biology - 90", "BE")

    assert "BS in Nursing" not in {item["course"] for item in science}
    assert "BS in Nursing" not in {item["course"] for item in social}
    assert "BS in Nursing" in {item["course"] for item in biology}
    assert "BS in Criminology" not in {item["course"] for item in social}


def test_earth_and_space_science_is_not_misclassified_as_arts(catalog):
    fields = app._extract_subject_field_scores(
        "Conceptual Biology and Earth and Space Science - 92"
    )

    assert fields["life_sciences"] == [92.0]
    assert fields["physical_sciences"] == [92.0]
    assert fields["arts_humanities"] == []


def test_marine_transportation_requires_navigation_related_major_grades(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Marine Transportation",
        "features": [69, 80, 84, 57, 90, 84],
        "description": "Prepare for maritime navigation and ship operations.",
        "strands": [],
    }])

    business_only = app._grade_based_alternatives(
        "Entrepreneurship - 88\nBusiness Finance - 87\nContemporary Marketing - 86",
        "BE",
    )
    navigation_grades = app._grade_based_alternatives(
        "Pre-Calculus - 88\nPhysics - 86\nBusiness Finance - 87",
        "BE",
    )

    assert app.categorize_course(
        "BS in Marine Transportation", "Maritime navigation and ship operations"
    ) == "Maritime & Transportation"
    assert business_only == []
    assert navigation_grades[0]["course"] == "BS in Marine Transportation"
    assert [entry["field"] for entry in navigation_grades[0]["subject_field_evidence"]] == [
        "Math and quantitative subjects", "Physics",
    ]


@pytest.mark.parametrize(("subject", "category"), [
    ("Political Science - 91", "social"),
    ("Computer Science - 91", "technology"),
])
def test_specialized_science_subjects_use_their_actual_category(subject, category):
    scores = app._extract_subject_scores(subject)

    assert scores[category] == [91.0]
    assert not scores["science"]


def test_unclassified_course_can_use_its_distinct_catalog_major_area(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "Bachelor of Library and Information Science",
        "features": [84, 78, 81, 93, 71, 61],
        "description": "",
        "strands": ["ICTECH"],
    }])

    assert app._grade_based_alternatives("English - 95", "BE") == []
    alternatives = app._grade_based_alternatives("Computer Programming - 95", "BE")
    assert alternatives[0]["course"] == "Bachelor of Library and Information Science"
    assert alternatives[0]["subject_field_evidence"] == [{
        "field": "Technology subjects", "grade": 95.0, "priority": "major",
    }]


def test_stronger_major_subject_grades_change_the_ranking(monkeypatch):
    profile = [85, 84, 80, 70, 70, 70]
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "BS in Civil Engineering", "features": profile, "description": "Engineering", "strands": ["STEM"]},
        {"course": "BS in Nursing", "features": profile, "description": "Healthcare", "strands": ["STEM"]},
    ])
    biology = "Mathematics - 85\nBiology - 97\nPhysics - 70\nEnglish - 80"
    physics = "Mathematics - 85\nBiology - 70\nPhysics - 97\nEnglish - 80"

    assert app.recommend_course(biology, strand="STEM")[0]["course"] == "BS in Nursing"
    assert app.recommend_course(physics, strand="STEM")[0]["course"] == "BS in Civil Engineering"


def test_missing_additional_key_area_is_explained_when_major_evidence_exists(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [{
        "course": "BS in Digital Engineering",
        "features": [84, 76, 78, 96, 64, 68],
        "description": "Engineering",
        "strands": ["STEM"],
    }])

    match = app.recommend_course("Mathematics - 90\nEnglish - 85", strand="STEM")[0]
    assert any(entry["field"] == "Math and quantitative subjects" and entry["priority"] == "major"
               for entry in match["subject_field_evidence"])
    assert "No Technology grades were found" in match["reason"]
    assert "less certain" in match["reason"]


def test_supporting_communication_cannot_qualify_physical_education(catalog):
    for subjects in ("English - 99", "Social Studies - 99\nFilipino - 99"):
        assert "Bachelor of Physical Education" not in {
            item["course"] for item in app.recommend_course(subjects, strand="ARSSH")
        }
