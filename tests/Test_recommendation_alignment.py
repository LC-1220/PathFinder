import pytest

import fastapi_app as app


def _use_catalog(monkeypatch, courses):
    monkeypatch.setattr(app, "_course_training_data", lambda: [
        {"description": "", "strands": ["STEM"], **course} for course in courses
    ])


def test_strongest_subject_alignment_beats_raw_grade_level_closeness(monkeypatch):
    # Closer overall level, but this course emphasizes English while the student is strongest in Math.
    _use_catalog(monkeypatch, [
        {"course": "BS in English-Leaning Program", "features": [86, 84, 89, 70, 70, 70]},
        {"course": "BS in Math-Leaning Program", "features": [99, 85, 88, 70, 70, 70]},
    ])

    recommendations = app.recommend_course("Mathematics - 90\nScience - 80\nEnglish - 85", strand="STEM")

    assert recommendations[0]["course"] == "BS in Math-Leaning Program"
    assert recommendations[0]["confidence"] < recommendations[1]["confidence"]
    assert "Your strongest area, Math, is one of this course's key subjects." in recommendations[0]["reason"]


def test_student_with_a_courses_exact_profile_gets_that_course(monkeypatch):
    profiles = {
        "BS in Math Heavy": [94, 82, 78, 70, 66, 70],
        "BS in Science Heavy": [80, 95, 80, 64, 66, 72],
        "BS in English Heavy": [76, 80, 94, 60, 70, 86],
        "BS in Technology Heavy": [84, 76, 78, 96, 66, 66],
    }
    _use_catalog(monkeypatch, [{"course": name, "features": features} for name, features in profiles.items()])
    labels = ["Mathematics", "Science", "English", "Computer Programming", "Accounting", "Social Studies"]

    for name, features in profiles.items():
        subjects = "\n".join(f"{label} - {grade}" for label, grade in zip(labels, features))
        assert app.recommend_course(subjects, strand="STEM")[0]["course"] == name


def test_missing_grades_for_a_courses_only_major_area_exclude_it(monkeypatch):
    _use_catalog(monkeypatch, [
        {"course": "BS in Technology Focused Program", "features": [84, 76, 78, 96, 64, 68]},
        {"course": "BS in Math Focused Program", "features": [92, 80, 78, 66, 62, 70]},
    ])

    recommendations = app.recommend_course("Mathematics - 86\nScience - 77\nEnglish - 79", strand="STEM")
    assert recommendations[0]["course"] == "BS in Math Focused Program"
    assert "BS in Technology Focused Program" not in {item["course"] for item in recommendations}


def test_general_mathematics_does_not_boost_every_engineering_program(monkeypatch):
    profile = [88, 84, 80, 80, 70, 70]
    _use_catalog(monkeypatch, [
        {"course": "BS in Civil Engineering", "features": profile, "description": "Civil engineering"},
        {"course": "BS in Geodetic Engineering", "features": profile, "description": "Surveying and mapping"},
    ])

    recommendations = app.recommend_course(
        "General Mathematics - 97\nScience - 84\nEnglish - 80", strand="STEM"
    )

    assert recommendations[0]["match_score"] == recommendations[1]["match_score"]
    assert all("highest math-related subject" not in item["reason"] for item in recommendations)


def test_subject_bonuses_do_not_pin_scores_at_100(monkeypatch):
    profile = [80, 96, 86, 62, 68, 72]
    _use_catalog(monkeypatch, [
        {"course": "BS in Nursing", "features": profile, "description": "Patient care"},
        {"course": "BS in Physical Therapy", "features": [80, 89, 81, 63, 64, 74], "description": "Rehabilitation therapy"},
    ])

    recommendations = app.recommend_course(
        "General Mathematics - 80\nGeneral Biology - 97\nGeneral Chemistry - 95\nOral Communication - 86",
        strand="STEM",
    )

    assert recommendations[0]["course"] == "BS in Nursing"
    assert recommendations[0]["match_score"] > recommendations[1]["match_score"]


@pytest.mark.parametrize(("course", "description", "expected"), [
    ("BS in Electronics Engineering major in Biomedical Engineering", "Medical devices", "Engineering"),
    ("BS in Mechanical Engineering", "Machines and computer-aided systems", "Engineering"),
    ("BS in Criminology", "Criminal justice systems and law enforcement", "Public Service & Governance"),
    ("BS in Aircraft Maintenance and Technology", "Aircraft operations and maintenance", "Engineering"),
    ("Bachelor of Physical Education", "Teaching physical education", "Sports & Physical Education"),
    ("BS in Nursing", "Patient care", "Allied Health"),
])
def test_course_names_take_priority_over_description_words(course, description, expected):
    assert app.categorize_course(course, description) == expected


def test_social_science_and_philosophy_subjects_count_as_social_studies():
    scores = app._extract_subject_scores(
        "Discipline and Ideas in the Social Sciences - 94\n"
        "Introduction to the Philosophy of the Human Person - 90\n"
        "Earth Science - 80"
    )

    assert scores["social"] == [94.0, 90.0]
    assert scores["science"] == [80.0]
