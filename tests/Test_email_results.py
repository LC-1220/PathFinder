import asyncio
import json
import smtplib
from types import SimpleNamespace

import pytest

import fastapi_app as app


PROFILE = {
    "student_number": "20-0001-111",
    "first_name": "Juan",
    "middle_initial": "D",
    "last_name": "<Cruz>",
    "course": "",
    "gwa": "90.50",
    "grades": "92, 89",
    "subjects": "General Mathematics - 92\nOral Communication - 89",
    "recommendation": "[]",
    "strand": "STEM",
}
RECOMMENDATIONS = [
    {"course": "BS in Computer Science", "match_score": 91.234, "reason": "Strong <math> grades"},
    {"course": "BS in Civil Engineering", "match_score": 88},
]


def google_session(**extra):
    return {"user_id": 7, "email": "student@gmail.com", "auth_provider": "google", **extra}


def call(session):
    request = SimpleNamespace(session=session)
    response = asyncio.run(app.email_results(request))
    return response.status_code, json.loads(response.body)


@pytest.fixture
def sent(monkeypatch):
    messages = []
    activity = []
    monkeypatch.setattr(app, "_get_user_latest_profile", lambda user_id: dict(PROFILE))
    monkeypatch.setattr(app, "_configured_recommendations", lambda *args: list(RECOMMENDATIONS))
    monkeypatch.setattr(app, "send_email", lambda *args: messages.append(args))
    monkeypatch.setattr(app, "_record_student_activity", lambda req, action, details="": activity.append(action))
    return SimpleNamespace(messages=messages, activity=activity)


@pytest.mark.parametrize("session", [
    {},
    {"user_id": 7, "email": "student@gmail.com"},
    {"user_id": 7, "email": "student@gmail.com", "auth_provider": "password"},
    {"is_guest": True, "email": "Anonymous session", "auth_provider": "google"},
])
def test_only_google_signed_in_students_can_email_results(sent, session):
    status, body = call(session)
    assert status == 403
    assert body["success"] is False
    assert sent.messages == []
    assert app._can_email_results(session) is False


def test_results_are_sent_to_the_google_session_email(sent):
    session = google_session()
    status, body = call(session)
    assert status == 200 and body["success"] is True
    assert "student@gmail.com" in body["message"]
    destination, subject, text_body, html_body = sent.messages[0]
    assert destination == "student@gmail.com"
    assert subject == "Your PathFinder course recommendations"
    assert "1. BS in Computer Science - 91.23% match" in text_body
    assert "2. BS in Civil Engineering - 88.00% match" in text_body
    assert "Strong <math> grades" in text_body
    assert "General Mathematics" not in text_body and "Oral Communication" not in text_body
    assert "90.50" not in text_body and "Grades used" not in text_body
    assert "&lt;Cruz&gt;" in html_body and "<Cruz>" not in html_body
    assert "Strong &lt;math&gt; grades" in html_body
    assert "Your course matches" in html_body
    assert "BS in Computer Science" in html_body and "91.23%" in html_body
    assert "General Mathematics" not in html_body and "Oral Communication" not in html_body
    assert "90.50" not in html_body and "Grades used" not in html_body
    assert sent.activity == ["results_emailed"]
    assert session["results_email_sent_at"]


def test_repeat_email_requests_are_rate_limited(sent):
    session = google_session()
    assert call(session)[0] == 200
    status, body = call(session)
    assert status == 429
    assert "wait" in body["message"].lower()
    assert len(sent.messages) == 1


def test_email_requires_a_saved_profile(sent, monkeypatch):
    monkeypatch.setattr(app, "_get_user_latest_profile", lambda user_id: None)
    status, body = call(google_session())
    assert status == 400
    assert sent.messages == []


@pytest.mark.parametrize(("error", "code"), [
    (ValueError("not configured"), "email_not_configured"),
    (smtplib.SMTPAuthenticationError(535, b"bad"), "email_delivery_failed"),
    (OSError("offline"), "email_delivery_failed"),
])
def test_email_failures_do_not_start_the_cooldown(sent, monkeypatch, error, code):
    def fail(*args):
        raise error

    monkeypatch.setattr(app, "send_email", fail)
    session = google_session()
    status, body = call(session)
    assert status == 503
    assert body["code"] == code
    assert "results_email_sent_at" not in session
    assert sent.activity == []


def test_email_button_is_rendered_only_for_google_sign_in():
    template = app.TEMPLATES.get_template("StudentManagement.html")
    common = {"name": "Juan", "email": "student@gmail.com", "profile_image": "", "home_mode": True}
    assert 'id="email-results"' in template.render(**common, can_email_results=True)
    assert 'id="email-results"' not in template.render(**common, can_email_results=False)
