from pathlib import Path

import pytest

import fastapi_app as app


@pytest.mark.parametrize(("template", "page_class"), [
    ("StudentManagement.html", "student-page"),
    ("AdminDashboard.html", "admin-page"),
    ("Login.html", "login-page"),
])
def test_shared_ui_styles_load_after_page_styles(template, page_class):
    html = app.TEMPLATES.get_template(template).render(
        url_for=lambda name, filename="": f"/static/{filename}",
        name="Student",
        email="student@example.com",
        admin="Admin",
        profile_image="/static/profile_pictures/default.svg",
        home_mode=True,
        is_guest=False,
        account_role=app.ROLE_SUPER_ADMIN,
        current_user_id=1,
        can_manage_students=True,
        can_view_admin_data=True,
        system_settings=app.SYSTEM_SETTING_DEFAULTS,
    )

    assert f'class="{page_class}' in html
    assert html.count('href="/static/UIFlow.css"') == 1
    assert html.index("UIFlow.css") > html.index(
        {"StudentManagement.html": "Student.css", "AdminDashboard.html": "Admin.css", "Login.html": "Login.css"}[template]
    )


@pytest.mark.parametrize("script", ["Admin.js", "StudentApp.js"])
def test_navigation_defers_scroll_motion_to_accessible_css(script):
    source = (Path(__file__).resolve().parents[1] / "static" / script).read_text(encoding="utf-8")

    assert "behavior: 'smooth'" not in source
    assert "behavior:'smooth'" not in source


@pytest.mark.parametrize(("home_mode", "is_guest"), [(True, False), (True, True), (False, False)])
def test_student_logout_confirmation_is_available_in_every_layout(home_mode, is_guest):
    html = app.TEMPLATES.get_template("StudentManagement.html").render(
        name="Student",
        email="student@example.com",
        profile_image="/static/profile_pictures/default.svg",
        home_mode=home_mode,
        is_guest=is_guest,
    )

    assert html.count('id="logoutPopup"') == 1
    assert '<dialog id="logoutPopup"' in html
    assert 'data-student-logout' in html
    assert 'method="dialog" class="logout-content"' in html
    assert 'href="/logout" class="Logoutbtn2"' in html
    assert "Logout.js" in html


@pytest.mark.parametrize(("template", "script"), [
    ("StudentManagement.html", "StudentApp.js"),
    ("AdminDashboard.html", "Admin.js"),
])
def test_course_name_formatter_loads_before_page_script(template, script):
    source = (Path(__file__).resolve().parents[1] / "templates" / template).read_text(encoding="utf-8")

    assert source.index("CourseNames.js") < source.index(script)


def test_synthetic_model_scores_are_disclosed_in_student_and_admin_views():
    root = Path(__file__).resolve().parents[1]
    student = (root / "static" / "StudentApp.js").read_text(encoding="utf-8")
    admin = (root / "static" / "Admin.js").read_text(encoding="utf-8")

    assert student.count("Synthetic model support") >= 2
    assert "Not validated on real students" in student
    assert "85% strongest-area fit" not in student
    assert "Synthetic model support" in admin
    assert "Top model suggestion" in student
    assert "No courses have enough recognized course-specific grade evidence" in student


def test_student_match_meter_uses_profile_similarity_not_classifier_support():
    source = (Path(__file__).resolve().parents[1] / "static" / "StudentApp.js").read_text(encoding="utf-8")
    meter = source.split('<div class="match-meter"', 1)[1].split('<div class="recommendation-actions">', 1)[0]

    assert "<span>Recommendation match</span>" in meter
    assert "Number(item.confidence ?? 0).toFixed(2)" in meter
    assert "Math.min(100, Number(item.confidence ?? 0))" in meter
    assert "based on grade-profile similarity" in meter
    assert "item.match_score" not in meter
    assert "const matchScore = Number(item.confidence ?? 0).toFixed(2);" in source
    assert "does not determine ML ranking" in source


def test_subject_evidence_selections_are_not_labeled_as_model_predictions():
    source = (Path(__file__).resolve().parents[1] / "static" / "StudentApp.js").read_text(encoding="utf-8")
    assert "item.selection_basis === 'subject_evidence'" in source
    assert "isEvidenceSelected(item) ? 'Strong subject evidence'" in source
    assert source.count("isEvidenceSelected(item) ? 'Why this subject-based suggestion'") == 2
    assert "isEvidenceSelected(item) ? 'Subject-evidence suggestion'" in source


@pytest.mark.parametrize("template", ["StudentManagement.html", "AdminDashboard.html"])
def test_all_role_pages_load_inactivity_timeout_script(template):
    source = (Path(__file__).resolve().parents[1] / "templates" / template).read_text(encoding="utf-8")
    assert "filename='SessionTimeout.js'" in source


def test_student_fresh_start_has_explicit_destructive_confirmation():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "StudentManagement.html").read_text(encoding="utf-8")
    script = (root / "static" / "StudentApp.js").read_text(encoding="utf-8")
    assert 'class="fresh-start-card"' not in template
    assert template.count("data-fresh-start") == 1
    menu = template.split('id="profile-menu"', 1)[1].split('</header>', 1)[0]
    assert "data-fresh-start" in menu
    assert 'id="fresh-start-dialog"' in template
    assert "This cannot be undone." in template
    assert "recommendation history" in template
    assert '<h2 id="fresh-start-title">Remove profile?</h2>' in template
    assert '>Remove profile</button>' in template
    assert "Start fresh?" not in template
    assert "'/api/v1/recommendations/reset'" in script
    assert "JSON.stringify({confirmation: 'RESET'})" in script


@pytest.mark.parametrize("expired", [True, False])
def test_timeout_popup_only_appears_after_session_expiry(expired):
    template = app.TEMPLATES.get_template("Login.html").render(session_expired=expired)
    assert ('id="session-timeout-dialog"' in template) is expired
    assert ("document.getElementById('session-timeout-dialog').showModal()" in template) is expired
