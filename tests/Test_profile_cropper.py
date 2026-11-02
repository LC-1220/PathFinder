from pathlib import Path

import fastapi_app as app

STATIC = Path(__file__).resolve().parent.parent / "static"


def _render_student():
    return app.TEMPLATES.get_template("StudentManagement.html").render(
        name="Juan", email="student@gmail.com", profile_image="", home_mode=True
    )


def _render_admin():
    return app.TEMPLATES.get_template("AdminDashboard.html").render(
        url_for=lambda name, filename="": f"/static/{filename}",
        admin="Admin",
        name="Admin",
        username="admin",
        email="admin@example.com",
        profile_image="/static/profile_pictures/default.svg",
        account_role=app.ROLE_SUPER_ADMIN,
        current_user_id=1,
        can_manage_students=True,
        can_view_admin_data=True,
        password_change_required=False,
        system_settings=app.SYSTEM_SETTING_DEFAULTS,
    )


def test_profile_cropper_assets_load_before_page_scripts():
    for html, page_script in ((_render_student(), "StudentApp.js"), (_render_admin(), "Admin.js")):
        assert "ProfileCropper.css" in html
        assert "ProfileCropper.js" in html
        assert html.index("ProfileCropper.js") < html.index(page_script)


def test_picture_uploads_go_through_cropper_first():
    for script in ("StudentApp.js", "Admin.js"):
        source = (STATIC / script).read_text(encoding="utf-8")
        upload_start = source.index("PathFinderCropper.open(file)")
        assert upload_start < source.index("/api/v1/upload_profile_picture", upload_start)
        assert "'profile-picture.png'" in source


def test_cropper_exports_square_image_within_backend_limit():
    source = (STATIC / "ProfileCropper.js").read_text(encoding="utf-8")
    assert "const OUTPUT_SIZE = 512;" in source
    assert app.PROFILE_IMAGE_MAX_DIMENSION >= 512
    for control in ("data-cropper-save", "data-cropper-cancel", "data-cropper-reset", "data-cropper-rotate", "pf-cropper-zoom"):
        assert control in source
