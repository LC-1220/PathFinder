import asyncio
import json
from types import SimpleNamespace

import pytest

import fastapi_app as app


@pytest.fixture
def catalog(monkeypatch):
    courses = [
        {"course": "BS in Business Administration", "features": [75, 75, 85, 75, 95, 85],
         "description": "Business", "strands": ["GAS", "ABM"]},
        {"course": "Bachelor of Elementary Education", "features": [75, 80, 95, 70, 75, 90],
         "description": "Education", "strands": ["GAS", "HUMSS"]},
        {"course": "BS in Civil Engineering", "features": [97, 80, 75, 75, 75, 75],
         "description": "Engineering", "strands": ["STEM"]},
        {"course": "BS in Pharmacy", "features": [80, 97, 75, 75, 75, 75],
         "description": "Pharmacy", "strands": ["STEM"]},
        {"course": "BS in Information Technology", "features": [80, 75, 75, 97, 75, 75],
         "description": "Information technology", "strands": ["TVL_ICT"]},
    ]
    monkeypatch.setattr(app, "_course_training_data", lambda: courses)
    monkeypatch.setattr(app, "_get_system_settings", lambda: {
        **app.SYSTEM_SETTING_DEFAULTS, "recommendation_limit": 2,
    })
    return courses


def test_high_math_gas_student_gets_engineering_as_separate_alternative(catalog):
    subjects = "Mathematics - 98\nScience - 80\nEnglish - 75\nBusiness - 75"
    result = app._configured_recommendations(subjects, strand="GAS")
    primary = [item for item in result if item["recommendation_basis"] == "strand"]
    alternatives = [item for item in result if item["recommendation_basis"] == "grade_alternative"]

    assert [item["course"] for item in primary] == [
        item["course"] for item in app.recommend_course(subjects, strand="GAS")[:2]
    ]
    assert alternatives[0]["course"] == "BS in Civil Engineering"
    assert len(primary) == 2
    assert len(alternatives) == 1
    assert all(item["strand_alignment"] for item in primary)
    assert all(not item["strand_alignment"] and item["strand_label"] == "GAS" for item in alternatives)
    assert {item["course"] for item in primary}.isdisjoint(item["course"] for item in alternatives)
    assert {item["course"] for item in result} <= {item["course"] for item in catalog}
    assert "Math (98.0)" in alternatives[0]["reason"]


def test_alternatives_follow_database_profiles_not_course_names(catalog):
    subjects = "Mathematics - 98\nScience - 80\nEnglish - 75"
    first = app._grade_based_alternatives(subjects, "GAS")
    assert first[0]["course"] == "BS in Civil Engineering"
    catalog[2]["features"], catalog[3]["features"] = catalog[3]["features"], catalog[2]["features"]
    assert app._grade_based_alternatives(subjects, "GAS")[0]["course"] == "BS in Pharmacy"
    catalog[3]["course"] = "Newly renamed database program"
    assert app._grade_based_alternatives(subjects, "GAS")[0]["course"] == "Newly renamed database program"


def test_changing_highest_grade_changes_alternative(catalog):
    science = app._grade_based_alternatives("Mathematics - 80\nScience - 98\nEnglish - 75", "GAS")
    assert science[0]["course"] == "BS in Pharmacy"


@pytest.mark.parametrize("subject", [
    "Computer Programming", "Computer Science", "Business", "Accounting",
    "Entrepreneurship", "Social Studies", "Social Science", "Political Science",
    "Filipino", "Nihongo", "Foreign Language", "Tourism", "Hospitality",
])
def test_non_core_grades_do_not_influence_best_alternative(catalog, subject):
    baseline = "Mathematics - 98\nScience - 80\nEnglish - 75"
    expected = app._grade_based_alternatives(baseline, "GAS")
    for grade in (70, 100):
        assert app._grade_based_alternatives(f"{baseline}\n{subject} - {grade}", "GAS") == expected


@pytest.mark.parametrize(("subject", "category"), [
    ("Algebra", "math"), ("Calculus", "math"), ("Geometry", "math"),
    ("Statistics and Probability", "math"), ("Trigonometry", "math"),
    ("Physics", "science"), ("Chemistry", "science"), ("General Biology", "science"),
    ("Earth and Life Science", "science"), ("Reading and Writing", "english"),
    ("Oral Communication", "english"), ("English Literature", "english"),
])
def test_core_branches_are_used_for_alternatives(catalog, subject, category):
    result = app._grade_based_alternatives(f"{subject} - 95", "GAS")
    assert result
    assert f"{category.title()} (95.0)" in result[0]["reason"]


def test_non_core_only_record_has_no_alternative_but_keeps_strand_matches(catalog):
    subjects = "Business - 95\nComputer Programming - 98\nSocial Studies - 90"
    assert app._grade_based_alternatives(subjects, "GAS") == []
    assert app.recommend_course(subjects, strand="GAS")


def test_non_core_database_features_do_not_affect_alternatives(catalog):
    subjects = "Mathematics - 98\nScience - 80\nEnglish - 75"
    expected = app._grade_based_alternatives(subjects, "GAS")
    for course in catalog:
        course["features"][3:] = [100, 0, 100]
    assert app._grade_based_alternatives(subjects, "GAS") == expected


def test_close_strength_matches_prefer_better_full_profile(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "Alphabetically first but weak overall", "features": [98, 50, 50, 0, 0, 0],
         "description": "", "strands": ["STEM"]},
        {"course": "Stronger overall profile", "features": [97, 80, 75, 0, 0, 0],
         "description": "", "strands": ["STEM"]},
    ])
    results = app._grade_based_alternatives("Mathematics - 98\nScience - 80\nEnglish - 75", "GAS")
    assert results[0]["course"] == "Stronger overall profile"
    assert results[0]["match_score"] > results[1]["match_score"]
    assert results[0]["confidence"] > results[1]["confidence"]
    assert "85%" in results[0]["reason"] and "15%" in results[0]["reason"]


def test_alternative_score_uses_both_distances_without_rounding_before_sort(monkeypatch):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"course": "A slightly weaker match", "features": [97.001, 0, 0, 0, 0, 0],
         "description": "", "strands": []},
        {"course": "Z slightly stronger match", "features": [97.002, 0, 0, 0, 0, 0],
         "description": "", "strands": []},
    ])
    results = app._grade_based_alternatives("Mathematics - 98", "GAS")
    assert results[0]["course"] == "Z slightly stronger match"
    assert results[0]["match_score"] == results[1]["match_score"] == 99.0
    assert all(not any(key.startswith("_") for key in item) for item in results)


@pytest.mark.parametrize("minor", [
    "FCL - 100", "FCL1 - 100", "FCL 2 - 100",
    "Filipino Christian Living 3 - 100", "Christian Living - 100",
    "PE - 100", "PE1 - 100", "P.E. 2 - 100",
    "Physical Education and Health 3 - 100",
    "11106 PE1 | Physical Education and Health 1 | 100",
])
def test_minor_subjects_do_not_change_either_recommendation_group(catalog, minor):
    subjects = "Mathematics - 98\nScience - 80\nEnglish - 75\nFilipino - 82"
    assert app.recommend_course(subjects, strand="GAS") == app.recommend_course(
        f"{subjects}\n{minor}", strand="GAS",
    )
    assert app._configured_recommendations(subjects, strand="GAS") == app._configured_recommendations(
        f"{subjects}\n{minor}", strand="GAS",
    )


def test_minor_only_record_has_no_recommendations(catalog):
    assert app._configured_recommendations(
        "Filipino Christian Living - 100\nPhysical Education and Health - 100", strand="GAS",
    ) == []


def test_minor_filter_keeps_major_subjects_and_original_text():
    subjects = "Filipino - 85\nPhysics - 98\nPersonal Development - 82\nEntrepreneurship - 90"
    assert app._recommendation_subjects_only(subjects) == subjects


def test_unobserved_categories_do_not_influence_alternative_ranking(catalog):
    first = app._grade_based_alternatives("Mathematics - 98", "GAS")
    catalog[2]["features"][1:] = [0, 0, 0, 0, 0]
    second = app._grade_based_alternatives("Mathematics - 98", "GAS")
    assert [(item["course"], item["match_score"]) for item in first] == [
        (item["course"], item["match_score"]) for item in second
    ]


def test_tied_grades_have_finite_scores(catalog):
    for subjects in ("Mathematics - 80\nScience - 80", "Mathematics - 80"):
        result = app._grade_based_alternatives(subjects, "GAS")
        assert result
        assert all(0 <= item["match_score"] <= 100 for item in result)


def test_zero_only_grades_preserve_existing_unusable_grade_behavior(catalog):
    assert app._grade_based_alternatives("Mathematics - 0\nScience - 0", "GAS") == []


@pytest.mark.parametrize("strand", ["", "Unknown", "BS in Computer Science"])
def test_missing_or_unrecognized_strand_does_not_duplicate_grade_only_list(catalog, strand):
    assert app._grade_based_alternatives("Mathematics - 98", strand) == []
    result = app._configured_recommendations("Mathematics - 98", strand=strand)
    assert all(item["recommendation_basis"] != "grade_alternative" for item in result)


def test_no_observed_grades_or_outside_courses_yields_no_alternatives(catalog):
    assert app._grade_based_alternatives("No grades", "GAS") == []
    for item in catalog:
        item["strands"].append("GAS")
    assert app._grade_based_alternatives("Mathematics - 98", "GAS") == []


def test_group_limits_and_basis_survive_serialization(catalog):
    result = app._configured_recommendations("Mathematics - 98\nEnglish - 75", strand="GAS")
    restored = app._limit_recommendations(json.dumps(result))
    assert restored == result
    limited = app._limit_recommendations(json.dumps(result), 1)
    assert [item["recommendation_basis"] for item in limited] == ["strand", "grade_alternative"]
    legacy = [{"course": catalog[0]["course"], "strand_label": "GAS"}]
    assert app._limit_recommendations(legacy)[0]["recommendation_basis"] == "strand"
    duplicate = {**result[0], "recommendation_basis": "grade_alternative"}
    assert len({item["course"] for item in app._limit_recommendations(result + [duplicate])}) == len(result)


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5])
def test_existing_saved_lists_keep_only_best_alternative(catalog, limit):
    subjects = "Mathematics - 98\nEnglish - 75"
    primary = app.recommend_course(subjects, strand="GAS")
    alternatives = app._grade_based_alternatives(subjects, "GAS")
    assert len(alternatives) > 1
    restored = app._limit_recommendations(json.dumps(primary + alternatives), limit)
    assert [item["course"] for item in restored if item["recommendation_basis"] == "grade_alternative"] == [
        alternatives[0]["course"],
    ]
    assert [item["course"] for item in restored if item["recommendation_basis"] != "grade_alternative"] == [
        item["course"] for item in primary[:limit]
    ]


def test_saved_profile_restores_both_groups_and_all_comparisons(monkeypatch, catalog):
    subjects = "Mathematics - 98\nEnglish - 75"
    recommendations = app._configured_recommendations(subjects, strand="GAS")
    profile = ("1", "Student", "", "Name", "", "86.5", "98, 75", subjects,
               json.dumps(recommendations), "GAS")
    cursor = SimpleNamespace(execute=lambda *args: None, fetchone=lambda: profile)
    monkeypatch.setattr(app, "_db_conn", lambda: SimpleNamespace(cursor=lambda: cursor, close=lambda: None))
    response = app.get_profile(SimpleNamespace(session={"user_id": "student"}))
    restored = json.loads(response.body)
    assert restored["recommendation"] == recommendations
    assert set(restored["comparisons"]) == {item["course"] for item in recommendations}


def test_guest_endpoint_returns_both_groups(monkeypatch, catalog):
    async def request_json():
        return {"subjects": "Mathematics - 98\nEnglish - 75", "strand": "GAS"}

    request = SimpleNamespace(session={"is_guest": True}, json=request_json)
    result = json.loads(asyncio.run(app.recommend_anonymous(request)).body)
    assert {item["recommendation_basis"] for item in result["recommendation"]} == {"strand", "grade_alternative"}
    assert set(result["comparisons"]) == {item["course"] for item in result["recommendation"]}
    assert "latest_recommendations" not in request.session
