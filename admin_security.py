import hashlib
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

import httpx

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class EmailApiError(smtplib.SMTPException):
    """Raised when the HTTPS email provider rejects or cannot accept a message."""

    def __init__(self, status_code, provider_code=""):
        super().__init__(f"Email API returned HTTP {status_code}")
        self.status_code = status_code
        self.provider_code = provider_code


SECURITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_account_security (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    lockout_level INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    recovery_email TEXT UNIQUE,
    pending_email TEXT,
    verification_hash TEXT UNIQUE,
    verification_expires_at TIMESTAMPTZ,
    verification_sent_at TIMESTAMPTZ,
    reset_hash TEXT UNIQUE,
    reset_expires_at TIMESTAMPTZ,
    reset_sent_at TIMESTAMPTZ
)
"""


def utc_now():
    return datetime.now(timezone.utc)


def cooldown_seconds(level):
    return min(30 * (2 ** min(max(level - 1, 0), 5)), 900)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token():
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def lock_security_row(cursor, user_id):
    cursor.execute(
        "INSERT INTO admin_account_security (user_id) VALUES (?) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )
    cursor.execute(
        "SELECT failed_attempts, lockout_level, locked_until FROM admin_account_security WHERE user_id = ? FOR UPDATE",
        (user_id,),
    )
    return cursor.fetchone()


def record_failure(cursor, user_id, failures, level, now):
    failures += 1
    until = None
    if failures >= 3:
        failures = 0
        level += 1
        until = now + timedelta(seconds=cooldown_seconds(level))
    cursor.execute(
        "UPDATE admin_account_security SET failed_attempts = ?, lockout_level = ?, locked_until = ? WHERE user_id = ?",
        (failures, level, until, user_id),
    )
    return cooldown_seconds(level) if until else 0


def clear_lockout(cursor, user_id):
    cursor.execute(
        "UPDATE admin_account_security SET failed_attempts = 0, lockout_level = 0, locked_until = NULL WHERE user_id = ?",
        (user_id,),
    )


def clear_recovery_tokens(cursor, user_id):
    cursor.execute(
        """UPDATE admin_account_security SET reset_hash = NULL, reset_expires_at = NULL,
           verification_hash = NULL, verification_expires_at = NULL, pending_email = NULL
           WHERE user_id = ?""",
        (user_id,),
    )


def mail_configuration():
    """Return (sender, secret, base_url); the secret is the Brevo API key when set, else the Gmail App Password."""
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    username = (os.getenv("EMAIL_SENDER", "").strip() or os.getenv("SMTP_USERNAME", "").strip()) if api_key else os.getenv("SMTP_USERNAME", "").strip()
    password = api_key or "".join(os.getenv("SMTP_PASSWORD", "").split())
    base_url = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    parts = urlsplit(base_url)
    local_http = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        not username or not password or not parts.hostname
        or (parts.scheme != "https" and not local_http)
        or parts.username or parts.password or parts.query or parts.fragment or parts.path
    ):
        raise ValueError(
            "Configure BREVO_API_KEY with EMAIL_SENDER (or SMTP_USERNAME and SMTP_PASSWORD) "
            "and a valid APP_BASE_URL before sending email."
        )
    return username, password, base_url


def _deliver(destination, subject, text_body, html_body=None):
    username, secret, _ = mail_configuration()
    if os.getenv("BREVO_API_KEY", "").strip():
        payload = {
            "sender": {"email": username, "name": os.getenv("EMAIL_SENDER_NAME", "").strip() or "PathFinder"},
            "to": [{"email": destination}],
            "subject": subject,
            "textContent": text_body,
        }
        if html_body:
            payload["htmlContent"] = html_body
        try:
            response = httpx.post(
                BREVO_API_URL,
                json=payload,
                headers={"api-key": secret, "accept": "application/json"},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise EmailApiError(0, type(exc).__name__) from exc
        if response.status_code >= 300:
            try:
                provider_code = str(response.json().get("code") or "")
            except ValueError:
                provider_code = ""
            raise EmailApiError(response.status_code, provider_code)
        return
    message = EmailMessage()
    message["From"] = username
    message["To"] = destination
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username, secret)
        smtp.send_message(message)


def send_email(destination, subject, text_body, html_body=None):
    _deliver(destination, subject, text_body, html_body)


def send_security_email(destination, token, purpose):
    _, _, base_url = mail_configuration()
    verification = purpose == "verify"
    subject = "Verify your PathFinder recovery Gmail" if verification else "Reset your PathFinder admin password"
    minutes = 30 if verification else 15
    path = "/admin/recovery-verification" if verification else "/login"
    link = f"{base_url}{path}#{purpose}={token}"
    _deliver(
        destination,
        subject,
        f"{subject}\n\nOpen this link to continue:\n{link}\n\n"
        f"This link expires in {minutes} minutes and can only be used once. "
        "If you did not request this, ignore this email.",
    )
