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
