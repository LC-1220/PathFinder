# FastAPI application configuration and imports
import asyncio
import json
import html
import inspect
import logging
from io import BytesIO
import math
import os
import re
import smtplib
import tempfile
import threading
import time
from json import dumps
from datetime import datetime, time as dt_time, timedelta, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from admin_security import (
    SECURITY_SCHEMA, EmailApiError, clear_lockout, clear_recovery_tokens, lock_security_row, mail_configuration,
    new_token, record_failure, send_email, send_security_email, token_hash, utc_now,
)

# Standard Library Imports
import cv2
import httpx
import numpy as np
from authlib.integrations.starlette_client import OAuth
import bcrypt
import psycopg
from psycopg.errors import UniqueViolation
from dotenv import load_dotenv
from fastapi import FastAPI, Request as FastAPIRequest
from fasthtml.common import fast_app, serve
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image as PILImage, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from starlette.middleware import Middleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

load_dotenv()

# Railway runs in UTC, so report timestamps use the school's local time explicitly.
try:
    APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Manila"))
except (ZoneInfoNotFoundError, ValueError):
    APP_TIMEZONE = timezone(timedelta(hours=8), "PHT")


def _app_now():
    return datetime.now(APP_TIMEZONE)


def _app_local(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(APP_TIMEZONE)

from ocr import scan_report_card_docling
from ocr.docling_service import _subjects_from_table
from ocr.parser import normalize_ocr_text, parse_report_card_structure


from course_ml import MODEL_BASIS, MODEL_PATH, NOTICE as MODEL_NOTICE, catalog_fingerprint, model_for_catalog

#Admin Configuration
UPLOAD_FOLDER = "static/profile_pictures"
ADMIN_USERNAME = (os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "").strip()
ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""
BOOTSTRAP_ADMIN_NAME = (os.getenv("BOOTSTRAP_ADMIN_NAME") or "Administrator").strip()
ROLE_ADMIN = "admin"
ROLE_SEMI_ADMIN = "semi_admin"
ROLE_SUPER_ADMIN = ROLE_ADMIN
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72
PASSWORD_POLICY_MESSAGE = "Use at least 8 characters with an uppercase letter and a symbol. Passwords must be no more than 72 UTF-8 bytes."
ADMIN_PASSWORD_POLICY_MESSAGE = "Use at least 8 characters with an uppercase letter, a lowercase letter, a number, and a symbol. Passwords must be no more than 72 UTF-8 bytes."
SYSTEM_SETTING_STRANDS = ("BE", "STEM", "ICTECH", "HT", "ARSSH")
SYSTEM_SETTING_DEFAULTS = {
    "university_name": "University of Perpetual Help System Dalta Las Pinas",
    "school_year": "",
    "available_strands": list(SYSTEM_SETTING_STRANDS),
    "max_file_size_mb": 10,
    "automatic_ocr_enabled": True,
    "recommendation_limit": 3,
    "admin_default_view": "dashboard",
    "admin_table_density": "comfortable",
}


#Subject Required Categories
CATEGORY_NAMES = ["math", "science", "english", "technology", "business", "social"]

_COURSE_MODEL_CACHE = None
_COURSE_TRAINING_DATA_CACHE = None


class CourseCatalogSchemaMissing(RuntimeError):
    pass

#Template Environment Setup
def _template_env():
    env = Environment(
        loader=FileSystemLoader("templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )

    def _url_for(name, filename=None):
        if name == "static" and filename:
            static_file_path = os.path.join("static", filename)
            try:
                
                version = int(os.path.getmtime(static_file_path))
                return f"/static/{filename}?v={version}"
            except OSError:
                return f"/static/{filename}"
        return "#"

    env.globals["url_for"] = _url_for
    return env

#Default Profile Image Handling
TEMPLATES = _template_env()


def _default_profile_image_url():
    return "/static/profile_pictures/default.svg"

#Profile Image Handling
def _profile_image_from_value(value):
    text = (value or "").strip()
    if not text:
        return _default_profile_image_url()

    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return text

    if lowered in ("default.jpg", "default.png", "default.svg", "none", "null"):
        return _default_profile_image_url()

    stored = _parse_profile_image_db_value(text)
    if stored:
        return f"/api/v1/profile-image/{stored[0]}/{stored[1]}"
    if lowered.startswith(PROFILE_IMAGE_DB_PREFIX):
        return _default_profile_image_url()

    return f"/static/profile_pictures/{text}"

#Session Profile Image Handling
def _set_session_profile_image(sess, profile_picture_value):
    sess["profile_picture"] = (profile_picture_value or "").strip()
    sess["profile_image"] = _profile_image_from_value(profile_picture_value)


# Profile images are stored in the database so they survive restarts and redeploys.
# users.profile_picture holds "db:<user_id>:<version>"; the version busts browser caches.
PROFILE_IMAGE_DB_PREFIX = "db:"
PROFILE_IMAGE_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PROFILE_IMAGE_MAX_DIMENSION = 512
_DEFAULT_PROFILE_VALUES = ("", "default.jpg", "default.png", "default.svg", "none", "null")


def _parse_profile_image_db_value(value):
    match = re.fullmatch(r"db:(\d+):(\w+)", (value or "").strip())
    return (int(match.group(1)), match.group(2)) if match else None


def _normalize_profile_image(data):
    if not data:
        raise ValueError("The selected file is empty.")
    if len(data) > PROFILE_IMAGE_MAX_UPLOAD_BYTES:
        raise ValueError("Profile pictures must be 5 MB or smaller.")
    try:
        with PILImage.open(BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P", "PA") else "RGB")
    except (OSError, PILImage.DecompressionBombError) as exc:
        raise ValueError("Upload a valid JPG, PNG, WEBP, or GIF image.") from exc
    image.thumbnail((PROFILE_IMAGE_MAX_DIMENSION, PROFILE_IMAGE_MAX_DIMENSION))
    output = BytesIO()
    image.save(output, "WEBP", quality=88, method=4)
    return output.getvalue(), "image/webp"


def _store_profile_image(cursor, user_id, data, source):
    image_bytes, content_type = _normalize_profile_image(data)
    cursor.execute(
        """
        INSERT INTO profile_images (user_id, content_type, image_data, source, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) DO UPDATE SET
            content_type = EXCLUDED.content_type,
            image_data = EXCLUDED.image_data,
            source = EXCLUDED.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, content_type, image_bytes, source),
    )
    value = f"{PROFILE_IMAGE_DB_PREFIX}{user_id}:{int(time.time() * 1000)}"
    cursor.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (value, user_id))
    return value


def _profile_image_source(cursor, user_id):
    cursor.execute("SELECT source FROM profile_images WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else None


async def _download_google_picture(url):
    url = (url or "").strip()
    if not url.lower().startswith("https://"):
        return None
    # Google avatar URLs end with a size suffix such as "=s96-c"; request a sharper copy.
    url = re.sub(r"=s\d+(-c)?$", "=s256-c", url)
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        return None
    content_type = response.headers.get("content-type", "")
    if response.status_code != 200 or not content_type.startswith("image/"):
        return None
    if len(response.content) > PROFILE_IMAGE_MAX_UPLOAD_BYTES:
        return None
    return response.content

#Postgres Database Connection Handling (SUPABASE)
class _PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        query = query.replace("?", "%s")
        return self._cursor.execute(query, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

#Course Training Data Handling
class _PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return _PostgresCursor(self._connection.cursor())

    def execute(self, query, params=None):
        return _PostgresCursor(self._connection.cursor()).execute(query, params)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()

#Database Connection Helper Function
def _db_conn():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the Supabase database")

    last_error = None
    for attempt in range(3):
        try:
            connection = psycopg.connect(database_url, connect_timeout=10)
            return _PostgresConnection(connection)
        except psycopg.OperationalError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)

    raise RuntimeError(
        "Unable to connect to Supabase after 3 attempts. Check your internet "
        "connection and use the transaction pooler DATABASE_URL from "
        "Supabase Project Settings > Database."
    ) from last_error


def _get_system_settings():
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT settings_json FROM system_settings WHERE settings_key = 'global'")
    row = cursor.fetchone()
    conn.close()
    settings = {**SYSTEM_SETTING_DEFAULTS, "available_strands": list(SYSTEM_SETTING_DEFAULTS["available_strands"])}
    if row:
        try:
            saved = json.loads(row[0])
            if isinstance(saved, dict):
                settings.update(saved)
        except (TypeError, ValueError):
            pass
    saved_strands = settings.get("available_strands")
    if not isinstance(saved_strands, list) or not saved_strands or any(strand not in SYSTEM_SETTING_STRANDS for strand in saved_strands):
        settings["available_strands"] = list(SYSTEM_SETTING_STRANDS)
    settings.pop("current_semester", None)
    return settings


def _validate_system_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Settings must be submitted as an object.")

    university_name = str(data.get("university_name", "")).strip()
    school_year = str(data.get("school_year", "")).strip()
    strands = data.get("available_strands")
    max_file_size = data.get("max_file_size_mb")
    automatic_ocr = data.get("automatic_ocr_enabled")
    recommendation_limit = data.get("recommendation_limit")
    default_view = data.get("admin_default_view")
    table_density = data.get("admin_table_density")

    if len(university_name) > 160 or len(school_year) > 24:
        raise ValueError("University name or school year is too long.")
    if not isinstance(strands, list) or not strands or any(strand not in SYSTEM_SETTING_STRANDS for strand in strands):
        raise ValueError("Select at least one valid academic cluster.")
    if type(max_file_size) is not int or not 1 <= max_file_size <= 50:
        raise ValueError("Maximum file size must be between 1 and 50 MB.")
    if type(automatic_ocr) is not bool:
        raise ValueError("Automatic OCR must be enabled or disabled.")
    if type(recommendation_limit) is not int or not 1 <= recommendation_limit <= 5:
        raise ValueError("Recommendation count must be between 1 and 5.")
    if default_view not in ("dashboard", "view-students", "manage-reports", "manage-recommendations", "admin-activity"):
        raise ValueError("Choose a valid default admin page.")
    if table_density not in ("comfortable", "compact"):
        raise ValueError("Choose a valid table density.")

    return {
        "university_name": university_name,
        "school_year": school_year,
        "available_strands": [strand for strand in SYSTEM_SETTING_STRANDS if strand in strands],
        "max_file_size_mb": max_file_size,
        "automatic_ocr_enabled": automatic_ocr,
        "recommendation_limit": recommendation_limit,
        "admin_default_view": default_view,
        "admin_table_density": table_density,
    }


# University course catalog retrieval
def _course_training_data():
    global _COURSE_TRAINING_DATA_CACHE
    if _COURSE_TRAINING_DATA_CACHE is not None:
        return _COURSE_TRAINING_DATA_CACHE

    conn = _db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT courses.course, courses.features, courses.description,
                   COALESCE(
                       ARRAY_AGG(DISTINCT course_strands.strand_code)
                           FILTER (WHERE course_strands.strand_code IS NOT NULL),
                       ARRAY[]::TEXT[]
                   )
            FROM university_courses courses
            LEFT JOIN university_course_strands course_strands
                ON course_strands.course = courses.course
            WHERE COALESCE(courses.is_active, TRUE)
            GROUP BY courses.id, courses.course, courses.features, courses.description
            ORDER BY courses.id
            """
        )
        rows = cursor.fetchall()
    except psycopg.errors.UndefinedTable as exc:
        raise CourseCatalogSchemaMissing(
            "The recommendation catalog is not installed. Apply supabase_schema.sql to the configured database."
        ) from exc
    finally:
        conn.close()
    _COURSE_TRAINING_DATA_CACHE = [
        {
            "course": row[0],
            "features": row[1],
            "description": row[2],
            # Upper-case the codes so legacy 'ICTech' tags keep matching the ICTECH cluster.
            "strands": [str(code).upper() for code in (row[3] if isinstance(row[3], (list, tuple)) else [])],
        }
        for row in rows
    ]
    return _COURSE_TRAINING_DATA_CACHE

#Password Hashing and Verification
def _hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _password_policy_error(password, *, admin=False):
    policy_message = ADMIN_PASSWORD_POLICY_MESSAGE if admin else PASSWORD_POLICY_MESSAGE
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        return "Password must be at least 8 characters long. " + policy_message
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return "Password is too long for secure hashing. " + policy_message
    if not any(character.isupper() for character in password):
        return "Password needs an uppercase letter. " + policy_message
    if admin and not any(character.islower() for character in password):
        return "Password needs a lowercase letter. " + policy_message
    if admin and not re.search(r"[0-9]", password):
        return "Password needs a number. " + policy_message
    if not any(not character.isalnum() and not character.isspace() for character in password):
        return "Password needs a symbol. " + policy_message
    return None

#Password Verification
def _check_password(stored_hash, password):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False


#Grade Normalization
def _normalize_grade(value):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

#Average Calculation
def _average(values):
    return sum(values) / len(values) if values else 0.0


#Subject Scores Extraction from Text(using docling OCR)
def _extract_subject_scores(subjects_text):
    score_map = {name: [] for name in CATEGORY_NAMES}
    if not subjects_text:
        return score_map

    for line in str(subjects_text).splitlines():
        raw_line = re.sub(r"\s+", " ", (line or "")).strip()
        if not raw_line:
            continue

        label = raw_line
        grade_match = None
        if "|" in raw_line:
            cells = [part.strip() for part in raw_line.split("|")]
            grade_candidates = []
            for cell_index, cell in enumerate(cells):
                match = re.search(r"\b(\d{1,3}(?:\.\d+)?)\b", cell)
                if match and 0 <= float(match.group(1)) <= 100:
                    grade_candidates.append((cell_index, match))
            if grade_candidates:
                grade_index, grade_match = grade_candidates[-1]
                grade_text = grade_match.group(1)
                label = cells[1] if len(cells) > 1 else cells[0]
                label = re.sub(r"^#?\d{3,6}\s*[A-Za-z]{1,8}\d{0,4}\s*", "", label).strip()

        for pattern in [
            r"^(?P<label>.*?)[-–:|/]\s*(?P<grade>\d{1,3}(?:\.\d+)?)\s*(?:passed|failed|remarks|inc|withdrawn|conditional)?\s*$",
            r"^(?P<label>[A-Za-z0-9][A-Za-z0-9\s&/().'-]*)\s+(?P<grade>\d{1,3}(?:\.\d+)?)\s*(?:passed|failed|remarks|inc|withdrawn|conditional)?\s*$",
        ]:
            if grade_match:
                break
            grade_match = re.search(pattern, raw_line, flags=re.IGNORECASE)
            if grade_match:
                label = grade_match.group("label").strip()
                grade_text = grade_match.group("grade")
                break

        if not grade_match:
            continue

        label = label.strip(" -:|/()[]{}")
        if not label:
            continue

        grade = _normalize_grade(grade_text)
        if grade <= 0:
            continue

        lowered = label.lower()
        if re.search(r"\b(?:social\s+(?:sciences?|studies)|political\s+sciences?)\b", lowered):
            score_map["social"].append(grade)
        elif re.search(r"\b(?:computer|computing|data|information)\s+sciences?\b|\bprogramming\s+languages?\b", lowered):
            score_map["technology"].append(grade)
        elif any(token in lowered for token in ["math", "algebra", "calculus", "statistics", "trigonometry", "geometry", "probability", "arithmetic", "precalculus", "equations", "analytics"]):
            score_map["math"].append(grade)
        elif any(token in lowered for token in ["science", "physics", "chemistry", "biology", "biological", "environment", "agriculture", "health", "nursing", "medical", "patient care", "first aid", "nutrition", "anatomy", "physiology", "botany", "zoology", "geology", "astronomy", "ecology", "ecosystem", "fisheries", "forestry", "crop", "soil", "biochemistry", "microbiology", "meteorology", "oceanography", "earth science", "life science", "natural science"]):
            score_map["science"].append(grade)
        elif any(token in lowered for token in ["english", "communication", "speech", "writing", "literature", "oral", "reading", "nihongo", "japanese", "chinese", "korean", "spanish", "french", "german", "language"]):
            score_map["english"].append(grade)
        elif any(token in lowered for token in ["program", "computer", "ict", "information", "technology", "software", "database", "digital", "web", "network", "coding", "engineering", "electronics", "robotics", "algorithm", "data structure", "python", "javascript", "java", "c++", "c#", "technical drawing", "drafting", "architectural design", "building design", "autocad", "computer aided design", "computer-aided design", "building information modeling"]) or _contains_subject_term(lowered, "cad"):
            score_map["technology"].append(grade)
        elif any(token in lowered for token in ["business", "accounting", "management", "marketing", "economics", "entrepreneur", "finance", "tourism", "hospitality", "tour operations", "tour guiding", "travel services", "public administration", "legal"]):
            score_map["business"].append(grade)
        elif any(token in lowered for token in ["history", "sociology", "psych", "political", "social", "criminology", "education", "culture", "governance", "filipino", "philosophy", "humanities", "religion", "pedagogy", "teaching", "child development", "criminal", "justice"]) or re.search(r"\b(?:arts?|design|music|media|creative|visual|performing)\b", lowered):
            score_map["social"].append(grade)

    return score_map


def _contains_subject_term(label, term):
    pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    return re.search(pattern, label) is not None


#Feature Vector Construction from Extracted Subject Scores(from OCR text). Helps with Course Recommendation
def _build_feature_vector(subjects_text):
    scores = _extract_subject_scores(subjects_text)
    return [
        _average(scores["math"]),
        _average(scores["science"]),
        _average(scores["english"]),
        _average(scores["technology"]),
        _average(scores["business"]),
        _average(scores["social"]),
    ]


def _extract_subject_field_scores(subjects_text):
    fields = {
        "quantitative": [],
        "general_sciences": [],
        "physical_sciences": [],
        "life_sciences": [],
        "health_studies": [],
        "physical_education": [],
        "environmental_sciences": [],
        "technology": [],
        "biology": [],
        "chemistry": [],
        "physics": [],
        "foreign_languages": [],
        "math_algebra": [],
        "math_calculus": [],
        "math_geometry": [],
        "math_trigonometry": [],
        "math_statistics": [],
        "math_general": [],
        "communication": [],
        "business": [],
        "entrepreneurship": [],
        "tourism_hospitality": [],
        "social_humanities": [],
        "arts_humanities": [],
        "teaching": [],
        "psychology": [],
        "civics": [],
        "criminal_justice": [],
        "navigation": [],
        "programming": [],
        "architectural_design": [],
    }
    source_text = str(subjects_text or "")
    cleaned_text = _clean_subjects_for_recommendation(source_text)
    lines = list(dict.fromkeys(cleaned_text.splitlines() + source_text.splitlines()))
    field_terms = {
        "quantitative": ("math", "mathematics", "algebra", "calculus", "statistics", "trigonometry", "geometry", "probability"),
        "physical_sciences": ("physics", "chemistry", "earth science", "earth and space science", "physical science", "geology", "astronomy"),
        "life_sciences": ("biology", "biological", "life science", "anatomy", "physiology", "botany", "zoology", "microbiology", "biochemistry"),
        "health_studies": ("health", "nursing", "medical", "patient care", "first aid", "nutrition"),
        "environmental_sciences": ("environment", "agriculture", "fisheries", "ecology", "ecosystem", "forestry", "crop", "soil"),
        "technology": ("technology", "computer", "programming", "coding", "software", "ict", "database", "network", "engineering", "electronics", "robotics"),
        "biology": ("biology", "biological", "life science"),
        "chemistry": ("chemistry", "chemical", "biochemistry"),
        "physics": ("physics",),
        "foreign_languages": ("nihongo", "japanese", "chinese", "korean", "spanish", "french", "german", "foreign language"),
        "math_algebra": ("algebra", "equation"),
        "math_calculus": ("calculus",),
        "math_geometry": ("geometry",),
        "math_trigonometry": ("trigonometry",),
        "math_statistics": ("statistics", "probability"),
        "math_general": ("general mathematics", "mathematics in the modern world", "mathematics", "math"),
        "communication": ("english", "filipino", "language", "communication", "speech", "writing", "literature", "reading", "oral", "nihongo", "japanese", "chinese", "korean", "spanish", "french", "german"),
        "business": ("business", "account", "accounting", "accountancy", "management", "marketing", "economics", "entrepreneur", "entrepreneurship", "finance", "tourism", "hospitality"),
        "entrepreneurship": ("entrepreneurship", "entrepreneurial"),
        "tourism_hospitality": ("tourism", "hospitality", "tour operations", "tour guiding", "travel services"),
        "social_humanities": ("history", "sociology", "psychology", "political", "social", "criminology", "education", "philosophy", "governance", "culture", "humanities", "religion"),
        "arts_humanities": ("art", "arts", "design", "music", "media", "creative", "visual", "performing"),
        "teaching": ("pedagogy", "teaching", "teacher education", "child development", "early childhood education", "elementary education", "secondary education", "special needs education", "inclusive education", "curriculum", "classroom instruction"),
        "psychology": ("psychology", "human behavior", "human behaviour", "mental processes", "personal development"),
        "civics": ("politics", "political", "governance", "civics", "public administration"),
        "criminal_justice": ("criminology", "criminal", "justice", "law enforcement", "forensic"),
        "navigation": ("navigation", "seamanship", "nautical", "ship operations"),
        "programming": ("programming", "coding", "software development", "software engineering", "algorithms", "algorithm", "data structures", "web development", "application development", "python", "java", "javascript", "c++", "c#"),
        "architectural_design": ("drafting", "technical drawing", "architectural design", "building design", "computer aided design", "computer-aided design", "autocad", "cad", "building information modeling"),
    }
    seen = set()
    for raw_line in lines:
        line = re.sub(r"\s+", " ", raw_line.strip())
        match = re.match(
            r"^(?P<label>.*?)(?:\s*[-–:|/]\s*|\s+)(?P<grade>\d{1,3}(?:\.\d+)?)\s*(?:passed|failed|remarks|inc|withdrawn|conditional)?\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        label = match.group("label").strip(" -:|/()[]{}").lower()
        grade = _normalize_grade(match.group("grade"))
        key = (label, grade)
        if not label or grade <= 0 or key in seen:
            continue
        seen.add(key)
        matched_fields = []
        for field, terms in field_terms.items():
            if any(_contains_subject_term(label, term) for term in terms):
                matched_fields.append(field)
        if re.fullmatch(r"(?:(?:general|integrated|natural|basic)\s+)?sciences?(?:\s*\d+)?", label):
            matched_fields.append("general_sciences")
        if "physical education" in label:
            matched_fields = [field for field in matched_fields if field != "health_studies"]
            matched_fields.append("physical_education")
        for field in matched_fields:
            fields[field].append(grade)
    return fields


COURSE_FIELD_PRIORITIES = {
    "Engineering": {
        "quantitative": "major", "general_sciences": "major", "physical_sciences": "major",
        "technology": "supporting", "environmental_sciences": "supporting",
    },
    "Maritime & Transportation": {
        "quantitative": "major", "physical_sciences": "major",
        "technology": "supporting", "communication": "supporting",
    },
    "Allied Health": {
        "life_sciences": "major", "health_studies": "major", "general_sciences": "major",
        "physical_sciences": "major",
        "communication": "supporting", "physical_education": "supporting",
    },
    "Agriculture": {
        "life_sciences": "major", "environmental_sciences": "major", "general_sciences": "major",
        "physical_sciences": "supporting", "quantitative": "supporting",
    },
    "Computer Studies": {
        "technology": "major", "quantitative": "major", "communication": "supporting",
    },
    "Business & Management": {
        "business": "major", "quantitative": "supporting", "communication": "supporting",
    },
    "Social Sciences & Education": {
        "social_humanities": "major", "communication": "major",
        "physical_education": "supporting",
    },
    "Hospitality & Tourism": {
        "business": "major", "communication": "major", "foreign_languages": "major",
        "arts_humanities": "supporting",
    },
    "Public Service & Governance": {
        "social_humanities": "major", "communication": "supporting", "business": "supporting",
    },
    "Arts & Design": {
        "arts_humanities": "major", "technology": "major", "communication": "supporting",
    },
    "Sports & Physical Education": {
        "physical_education": "major", "life_sciences": "major",
        "health_studies": "major", "communication": "supporting",
    },
}

COURSE_PROFILE_FIELD_LABELS = {
    "math": "Math and quantitative subjects",
    "science": "Science subjects",
    "english": "Communication and languages",
    "technology": "Technology subjects",
    "business": "Business subjects",
    "social": "Social sciences and humanities",
}


def _course_subject_field_evidence(category, field_scores, priorities=None):
    field_labels = {
        "quantitative": "Math and quantitative subjects",
        "general_sciences": "General sciences",
        "physical_sciences": "Physics and physical sciences",
        "life_sciences": "Biology and life sciences",
        "health_studies": "Health studies",
        "physical_education": "Physical education and fitness",
        "environmental_sciences": "Environmental sciences",
        "technology": "Technology subjects",
        "foreign_languages": "Foreign languages",
        "communication": "Communication and languages",
        "business": "Business subjects",
        "social_humanities": "Social sciences and humanities",
        "arts_humanities": "Arts and creative subjects",
        "teaching": "Teaching and learner development",
        "psychology": "Psychology and human behavior",
        "civics": "Politics and governance",
        "criminal_justice": "Criminal justice and law enforcement",
        "navigation": "Navigation and seamanship",
        "physics": "Physics",
        "chemistry": "Chemistry",
        "tourism_hospitality": "Tourism and hospitality",
        "programming": "Programming and software development",
        "architectural_design": "Architectural design and drafting",
    }
    field_priorities = COURSE_FIELD_PRIORITIES.get(category, {}) if priorities is None else priorities
    return [
        {
            "field": field_labels[field],
            "grade": round(_average(field_scores[field]), 2),
            "priority": priority,
        }
        for field, priority in field_priorities.items()
        if field_scores.get(field)
    ]


def _recommendation_field_evidence(category, field_scores, subject_scores, course_features, *, course_name=""):
    name = course_name.lower()
    priorities = None
    if any(term in name for term in ("computer science", "information technology", "software engineering", "data science", "bscs", "bsit")):
        priorities = {"programming": "major", "quantitative": "supporting", "technology": "supporting", "communication": "supporting"}
    elif "computer engineering" in name:
        priorities = {"programming": "major", "quantitative": "supporting", "physical_sciences": "supporting", "technology": "supporting", "communication": "supporting"}
    elif "architecture" in name and not any(term in name for term in ("naval", "marine")):
        priorities = {"architectural_design": "major", "quantitative": "supporting", "physical_sciences": "supporting", "arts_humanities": "supporting"}
    elif category == "Social Sciences & Education" and "education" in name:
        priorities = {"teaching": "major", "communication": "supporting", "social_humanities": "supporting"}
    elif "psychology" in name:
        priorities = {"psychology": "major", "life_sciences": "supporting", "communication": "supporting"}
    elif "political science" in name or "public administration" in name:
        priorities = {"civics": "major", "social_humanities": "supporting", "communication": "supporting"}
    elif "criminology" in name:
        priorities = {"criminal_justice": "major", "civics": "supporting", "social_humanities": "supporting", "communication": "supporting"}
    elif "communication" in name:
        priorities = {"communication": "major", "social_humanities": "supporting", "arts_humanities": "supporting"}
    elif category == "Hospitality & Tourism":
        priorities = {"tourism_hospitality": "major", "foreign_languages": "major", "communication": "supporting", "business": "supporting"}
    elif category == "Maritime & Transportation":
        if not field_scores["quantitative"] or not (field_scores["physics"] or field_scores["navigation"]):
            return []
        priorities = {"quantitative": "major", "physics": "major", "navigation": "major", "communication": "supporting"}
    elif category == "Allied Health" and course_name:
        priorities = {
            "life_sciences": "major", "health_studies": "major",
            "physical_sciences": "supporting", "communication": "supporting",
        }
        if "pharmacy" in name:
            priorities = {"chemistry": "major", "life_sciences": "supporting", "health_studies": "supporting", "communication": "supporting"}
        elif "medical technology" in name:
            priorities["chemistry"] = "major"
        elif "radiologic" in name:
            priorities["physics"] = "major"
    if priorities is not None:
        return _course_subject_field_evidence(category, field_scores, priorities)
    evidence = _course_subject_field_evidence(category, field_scores)
    if category in COURSE_FIELD_PRIORITIES:
        return evidence

    values = [float(value) for value in course_features]
    if not values or max(values) == min(values):
        return []
    highest = max(values)
    return [
        {
            "field": COURSE_PROFILE_FIELD_LABELS[subject],
            "grade": round(_average(subject_scores[subject]), 2),
            "priority": "major",
        }
        for subject, value in zip(CATEGORY_NAMES, values)
        if value == highest and subject_scores.get(subject)
    ]


def _field_evidence_fit(evidence):
    if not evidence:
        return None
    weights = {"major": 3.0, "supporting": 1.0}
    total_weight = sum(weights[item["priority"]] for item in evidence)
    return sum(item["grade"] * weights[item["priority"]] for item in evidence) / total_weight


def _course_subject_field_fit(category, field_scores):
    return _field_evidence_fit(_course_subject_field_evidence(category, field_scores))


def _relative_subject_strength(subject_average, overall_average):
    if subject_average is None or overall_average is None:
        return 0.0
    return max(-10.0, min(10.0, (subject_average - overall_average) * 0.5))

#Cleaning Subjects for Recommendation (removes instructor names and irrelevant text)
def _clean_subjects_for_recommendation(subjects_text):
    subject_terms = {
        "math", "mathematics", "science", "communication", "education", "technology",
        "health", "english", "filipino", "nihongo", "japanese", "chinese", "korean",
        "spanish", "french", "german", "literature", "person", "research", "entrepreneur",
        "services", "programming", "computer", "physical", "statistics", "biology",
    }
    # Helper function to determine if a value looks like an instructor's name
    def looks_like_instructor(value):
        words = re.findall(r"[A-Za-z]+", value)
        lowered = {word.lower() for word in words}
        return (
            len(words) >= 2
            and value == value.upper()
            and not lowered.intersection(subject_terms)
            and all(len(word) >= 1 for word in words)
        )
    # Process each line of the subjects text
    cleaned_rows = []
    pending_subject = ""
    for line in str(subjects_text or "").splitlines():
        standalone_grade = re.fullmatch(r"\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*", line.strip())
        if standalone_grade and pending_subject:
            grade = float(standalone_grade.group(1).replace(",", "."))
            if 0 <= grade <= 100:
                cleaned_rows.append(f"{pending_subject} - {grade:g}")
            pending_subject = ""
            continue
        direct_match = re.match(r"^(.+?)\s+-\s+(\d{1,3}(?:\.\d+)?)$", line.strip())
        if direct_match:
            subject = html.unescape(direct_match.group(1)).replace("|", " ").strip()
            if not looks_like_instructor(subject):
                cleaned_rows.append(f"{subject} - {float(direct_match.group(2)):g}")
            continue
        cells = [re.sub(r"\s+", " ", part).strip() for part in line.split("|")]
        if len(cells) < 2:
            continue
        grade_candidates = []
        for index, cell in enumerate(cells):
            match = re.fullmatch(r"(?:grade\s*[:\-]?\s*)?(\d{1,3}(?:\.\d+)?)", cell, re.IGNORECASE)
            if match and 0 <= float(match.group(1)) <= 100:
                grade_candidates.append((index, float(match.group(1))))
        if not grade_candidates:
            subject_only = html.unescape(cells[1] if len(cells) > 1 else cells[0]).strip()
            subject_only = re.sub(r"\s+\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s*,\s*[A-Z][A-Z.\s]+$", "", subject_only).strip()
            if subject_only and not looks_like_instructor(subject_only):
                pending_subject = subject_only
            continue
        grade_index, grade = grade_candidates[-1]
        subject = cells[1] if len(cells) > 1 else cells[0]
        if re.fullmatch(r"#?\d{3,6}\s*[A-Za-z]{1,8}\d{0,4}", subject):
            candidates = [
                value for index, value in enumerate(cells)
                if index != grade_index and value
                and not re.search(r"passed|failed|remarks|incomplete", value, re.IGNORECASE)
            ]
            subject = max(candidates, key=len, default="")
        subject = html.unescape(subject)
        subject = re.sub(r"\s*[|]+\s*", " ", subject).strip()
        subject = re.sub(r"^#?\d{3,6}\s*[A-Za-z]{1,8}\d{0,4}\s*", "", subject).strip()
        subject = re.sub(r"\s+\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s*,\s*[A-Z][A-Z.\s]+$", "", subject).strip()
        subject = re.sub(r"\s+[A-Za-z]+(?:\s+[A-Za-z]+)*,\s*[A-Za-z.\s]+$", "", subject).strip()
        if looks_like_instructor(subject):
            continue
        if subject and subject.lower() not in {"subject", "subject name", "grade", "remarks"}:
            cleaned_rows.append(f"{subject} - {grade:g}")
    # Return the cleaned rows as a single string, removing duplicates
    return "\n".join(dict.fromkeys(cleaned_rows))

#Prepare Subject Rows for Display (for the frontend) 
def _subject_rows_for_display(subjects_text):
    rows = []
    cleaned = _clean_subjects_for_recommendation(subjects_text)
    for line in cleaned.splitlines():
        match = re.match(r"^(.*?)\s+-\s+(\d{1,3}(?:\.\d+)?)$", line.strip())
        if not match:
            continue
        subject = match.group(1).strip()
        grade = float(match.group(2))
        rows.append({
            "subject": subject,
            "grade": f"{grade:g}",
            "status": "Passed" if grade >= 75 else "Review",
        })
    return rows

#Extract Numeric Grades from Text
def _extract_numeric_from_grades_text(grades_text):
    values = []
    if not grades_text:
        return values
    for line in str(grades_text).splitlines():
        if re.search(r"[A-Za-z]", line):
            match = re.search(
                r"(?:[-–:|/]\s*|\s+)(\d{1,3}(?:\.\d+)?)\s*(?:passed|failed|remarks|inc|withdrawn|conditional)?\s*$",
                line,
                flags=re.IGNORECASE,
            )
            tokens = [match.group(1)] if match else []
        else:
            tokens = re.findall(r"[0-9]{1,3}(?:\.[0-9]+)?", line)
        values.extend(float(token) for token in tokens)
    return values

#Compute General Weighted Average (GWA) from Subject Grades
def _compute_gwa(subjects_text, incoming_gwa="", incoming_grades=""):
    text_val = (incoming_gwa or "").strip()
    if text_val:
        return text_val
    
    # If incoming GWA is provided, use it directly
    grades = _extract_numeric_from_grades_text(subjects_text)
    if not grades:
        grades = _extract_numeric_from_grades_text(incoming_grades)
    if not grades:
        return ""
    return f"{(sum(grades) / len(grades)):.2f}"

#Coalesce Grades Text (combine subject grades and incoming grades into a single string)
def _coalesce_grades_text(subjects_text, incoming_grades=""):
    text_val = (incoming_grades or "").strip()
    if text_val:
        return text_val
    
    # If incoming grades text is provided, use it directly
    grades = _extract_numeric_from_grades_text(subjects_text)
    if not grades:
        return ""
    return ", ".join(f"{grade:g}" for grade in grades)

#Normalize Student Number (removes non-alphanumeric characters and converts to uppercase)
def _normalize_student_number(value):
    if value is None:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value).upper()).strip()


#Normalize Full Name (removes non-alphabetic characters and converts to uppercase)
def _normalize_full_name(value):
    if value is None:
        return ""
    tokens = re.findall(r"[A-Za-z]+", str(value))
    return "".join(tokens).upper()

#Validate Uploaded Image File
async def validate_upload(file_obj, max_file_size_mb=10):
    if file_obj is None:
        return False, "No file selected."

    filename = (getattr(file_obj, "filename", "") or "").strip()
    if not filename:
        return False, "Uploaded file is missing a filename."

    ext = os.path.splitext(filename)[1].lower()
    allowed_exts = {".jpg", ".jpeg", ".png", ".webp"}
    if ext not in allowed_exts:
        return False, "Unsupported file type. Use JPG, JPEG, PNG, or WEBP."

    content_type = (getattr(file_obj, "content_type", "") or "").lower()
    if content_type and not content_type.startswith("image/"):
        return False, "Uploaded file is not a valid image file."

    file_bytes = await file_obj.read()
    if not file_bytes:
        return False, "Uploaded file is empty."

    max_file_size_mb = max(1, min(50, int(max_file_size_mb)))
    if len(file_bytes) > max_file_size_mb * 1024 * 1024:
        return False, f"Uploaded file exceeds {max_file_size_mb} MB."

    try:
        image = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            return False, "Image could not be read or is corrupted."
    except Exception:
        return False, "Image could not be decoded."

    return True, {"filename": filename, "extension": ext, "size_bytes": len(file_bytes), "file_bytes": file_bytes}


def _student_record_matches(existing_record, incoming_record):
    if not existing_record or not incoming_record:
        return False

    existing = dict(existing_record)
    incoming = dict(incoming_record)

    existing_number = _normalize_student_number(existing.get("student_number") or existing.get("studentNumber"))
    incoming_number = _normalize_student_number(incoming.get("student_number") or incoming.get("studentNumber"))
    if existing_number and incoming_number and existing_number == incoming_number:
        return True

    existing_name = _normalize_full_name(
        f"{existing.get('first_name') or ''} {existing.get('middle_initial') or ''} {existing.get('last_name') or ''}"
    )
    incoming_name = _normalize_full_name(
        f"{incoming.get('first_name') or incoming.get('firstName') or ''} {incoming.get('middle_initial') or incoming.get('middleInitial') or ''} {incoming.get('last_name') or incoming.get('lastName') or ''}"
    )

    existing_course = (existing.get("course") or "").strip().upper()
    incoming_course = (incoming.get("course") or "").strip().upper()

    if existing_name and incoming_name and existing_name == incoming_name:
        return True

    if existing_name and incoming_name and existing_course and incoming_course and existing_course == incoming_course and SequenceMatcher(None, existing_name, incoming_name).ratio() >= 0.7:
        return True

    return False

#Merge Subject Entries (combine existing and incoming subjects, removing duplicates and cleaning text)
def _merge_subject_entries(existing_subjects, incoming_subjects):
    combined = []
    seen = {}

    # Iterate over both existing and incoming subjects, line by line
    for text in [existing_subjects or "", incoming_subjects or ""]:
        for raw_line in str(text).splitlines():
            line = raw_line.strip()
            if not line:
                continue

            cleaned = re.sub(r"\s+", " ", line).strip()
            label = cleaned
            grade_match = re.search(r"(?P<label>.*?)(?:\s*[-–:|/]\s*|\s+)(?P<grade>\d{1,3}(?:\.\d+)?)\s*$", cleaned)
            if grade_match:
                label = grade_match.group("label").strip()
                label = re.sub(r"^[-:|/]+\s*", "", label)
                label = re.sub(r"\s+[-:|/]+\s*$", "", label).strip()
                if not label:
                    continue
                final = f"{label} - {grade_match.group('grade')}"
            else:
                label = re.sub(r"^[-:|/]+\s*", "", cleaned).strip()
                label = re.sub(r"\s+[-:|/]+\s*$", "", label).strip()
                if not label:
                    continue
                final = label

            key = re.sub(r"\s+", " ", label).strip().lower()
            if key and key not in seen:
                seen[key] = final
                combined.append(final)

    return "\n".join(combined)

#Vector Distance Calculation (Euclidean distance between two vectors)
def _vector_distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


CORE_FEATURE_WEIGHTS = (3.0, 3.0, 3.0, 1.0, 1.0, 1.0)
ENGINEERING_SUBJECT_FOCUS = {
    "BS in Aeronautical Engineering": {"math_calculus", "math_geometry", "math_trigonometry", "physics"},
    "BS in Civil Engineering": {"math_algebra", "math_calculus", "math_geometry", "math_trigonometry", "math_general"},
    "BS in Mechanical Engineering": {"math_algebra", "math_calculus", "math_trigonometry", "math_general", "physics"},
    "BS in Electrical Engineering": {"math_algebra", "math_calculus", "math_trigonometry", "math_general", "physics"},
    "BS in Industrial Engineering": {"math_algebra", "math_statistics", "math_general"},
    "BS in Computer Engineering": {"math_algebra", "math_calculus", "math_general", "physics"},
    "BS in Digital Engineering": {"math_algebra", "math_statistics", "math_general"},
    "BS in Electronics Engineering major in Biomedical Engineering": {"math_algebra", "math_calculus", "math_general", "physics"},
    "BS in Marine Engineering": {"math_calculus", "math_trigonometry", "math_general", "physics"},
    "BS in Naval Architecture and Marine Engineering": {"math_calculus", "math_geometry", "math_trigonometry", "math_general", "physics"},
}

#Core Feature Weights for Weighted Calculations
def _weighted_features(features):
    return [value * math.sqrt(weight) for value, weight in zip(features, CORE_FEATURE_WEIGHTS)]

#Weighted Vector Distance Calculation (applies core feature weights to the distance computation)
def _weighted_vector_distance(a, b):
    return math.sqrt(
        sum(weight * (x - y) ** 2 for weight, x, y in zip(CORE_FEATURE_WEIGHTS, a, b))
    )


def _observed_weighted_distance(subject_scores, course_features):
    weighted_squared_error = 0.0
    observed_weight = 0.0
    for index, category in enumerate(CATEGORY_NAMES):
        grades = subject_scores.get(category, [])
        if not grades:
            continue
        weight = CORE_FEATURE_WEIGHTS[index]
        difference = _average(grades) - course_features[index]
        weighted_squared_error += weight * difference ** 2
        observed_weight += weight
    if not observed_weight:
        return None
    return math.sqrt(weighted_squared_error / observed_weight)


PROFILE_SHAPE_WEIGHT = 0.35
PROFILE_SHAPE_FULL_SPREAD = 4.0
FIELD_FIT_WEIGHT = 0.30
MISSING_KEY_SUBJECT_PENALTY = 12.0
MAX_SUBJECT_BONUS = 6.0
CATEGORY_LABELS = {
    "math": "Math", "science": "Science", "english": "English",
    "technology": "Technology", "business": "Business", "social": "Social Studies",
}


# Compares which subjects the student is strongest in with which subjects the course emphasizes,
# independent of overall grade level. Returns (fit 0-100 or None, student grade spread).
def _profile_shape_alignment(subject_scores, course_features):
    observed = [
        (index, _average(subject_scores[category]))
        for index, category in enumerate(CATEGORY_NAMES)
        if subject_scores.get(category)
    ]
    if len(observed) < 2:
        return None, 0.0
    student = [grade for _, grade in observed]
    course = [float(course_features[index]) for index, _ in observed]
    student_mean = _average(student)
    course_mean = _average(course)
    student_dev = [grade - student_mean for grade in student]
    course_dev = [value - course_mean for value in course]
    student_norm = math.sqrt(sum(value ** 2 for value in student_dev))
    course_norm = math.sqrt(sum(value ** 2 for value in course_dev))
    spread = student_norm / math.sqrt(len(student_dev))
    if student_norm < 1e-9 or course_norm < 1e-9:
        return None, spread
    cosine = sum(s * c for s, c in zip(student_dev, course_dev)) / (student_norm * course_norm)
    return 50.0 + 50.0 * max(-1.0, min(1.0, cosine)), spread


# Share of a course's above-average subject emphasis that the student has grades for.
def _course_key_subject_coverage(subject_scores, course_features):
    values = [float(value) for value in course_features]
    course_mean = _average(values)
    emphasis = [max(0.0, value - course_mean) for value in values]
    total = sum(emphasis)
    if total <= 0:
        return 1.0, []
    covered = sum(value for index, value in enumerate(emphasis) if subject_scores.get(CATEGORY_NAMES[index]))
    missing = [
        CATEGORY_LABELS[CATEGORY_NAMES[index]]
        for index in sorted(range(len(emphasis)), key=lambda i: emphasis[i], reverse=True)
        if emphasis[index] >= total * 0.2 and not subject_scores.get(CATEGORY_NAMES[index])
    ]
    return covered / total, missing


def _course_key_areas(course_features, count=2):
    ranked = sorted(range(len(CATEGORY_NAMES)), key=lambda index: float(course_features[index]), reverse=True)
    return [CATEGORY_NAMES[index] for index in ranked[:count]]


# Scores above 95 approach 100 smoothly, so strong matches stay distinguishable instead of tying at 100%.
def _display_match_score(ranking_score):
    if ranking_score <= 95.0:
        return round(max(0.0, ranking_score), 2)
    return round(min(99.99, 95.0 + 5.0 * (1.0 - math.exp(-(ranking_score - 95.0) / 5.0))), 2)


#Core Grade Fit Calculation (compares student's core feature grades with course core features)
def _core_grade_fit(student_features, course_features):
    observed = [value for value in student_features[:3] if value > 0]
    if not observed:
        return 0.0
    student_core = _average(observed)
    course_core = _average(course_features[:3])
    return max(0.0, 100.0 - abs(student_core - course_core))

#Course Categorization Based on Name and Description (assigns a course to a predefined category)
#TODO:try to move this area to SupaBase to Reduce hardcoded course categorization   
COURSE_CATEGORY_RULES = (
    ("Allied Health", ("nurse", "health", "medical", "pharm", "midwife", "clinical", "medtech", "radiologic", "respiratory", "nutrition", "dietetic", "biology", "biological"), ("health", "patient", "clinical", "nurse", "medical", "laboratory", "nutrition", "therapy", "biology", "biological science")),
    ("Computer Studies", ("computer", "information technology", "software", "ict", "bsit", "bscs", "data", "systems", "programming", "digital"), ("computer", "software", "programming", "information technology", "ict", "systems", "database", "digital")),
    ("Sports & Physical Education", ("sports", "sport science", "physical education", "athletic", "fitness", "exercise", "kinesiology"), ("sports science", "athletic conditioning", "physical education", "fitness instruction", "kinesiology")),
    ("Engineering", ("engineer", "civil", "mechanical", "electrical", "chemical", "architecture", "construction"), ("engineering", "infrastructure", "construction", "architecture")),
    ("Maritime & Transportation", ("marine transportation", "maritime transport", "nautical science"), ("maritime", "navigation", "ship operations", "seafaring")),
    ("Agriculture", ("agriculture", "fisheries", "farm", "crop", "soil", "environmental science", "ecosystem", "sustainability", "forest"), ("agriculture", "fisheries", "farm", "crop", "soil", "environmental science", "ecosystem", "sustainability", "forest")),
    ("Hospitality & Tourism", ("tourism", "hotel", "hospitality", "travel", "culinary"), ("tourism", "hospitality", "travel", "service")),
    ("Business & Management", ("business", "management", "account", "accounting", "marketing", "finance", "administration", "economics", "entrepreneur"), ("business", "management", "accounting", "finance", "marketing", "operations")),
    ("Social Sciences & Education", ("education", "teacher", "teaching", "psychology", "social", "humanities", "communication"), ("education", "teaching", "psychology", "social", "communication", "humanities")),
    ("Public Service & Governance", ("law", "political", "public administration", "governance", "criminology"), ("law", "public service", "governance", "politics")),
    ("Arts & Design", ("art", "design", "architecture", "creative", "media"), ("design", "creative", "art", "visual")),
)


def categorize_course(course_name, description=""):
    name = (course_name or "").lower()
    desc = (description or "").lower()

    # Degree names are more reliable than description words, so every name rule is checked first
    # (e.g. "Criminology" mentions justice "systems" and "Biomedical Engineering" contains "medical").
    if "engineer" in name or "aircraft" in name or "aviation" in name:
        return "Engineering"
    for category, name_tokens, _ in COURSE_CATEGORY_RULES:
        if any(tok in name for tok in name_tokens):
            return category
    for category, _, description_tokens in COURSE_CATEGORY_RULES:
        if any(tok in desc for tok in description_tokens):
            return category
    return "Other"

#  Infer Academic Cluster Based on Course Name (assigns a cluster like BE, STEM, ICTECH, HT, ARSSH, or Other) 
# help in recommending suitable courses for students based on their academic track
def _infer_strand(course_name=""):
    course_name = (course_name or "").lower()
    if any(tok in course_name for tok in ("tourism", "hospitality")):
        return "HT"
    if any(tok in course_name for tok in ("computer", "information", "technology", "multimedia")):
        return "ICTECH"
    if any(tok in course_name for tok in ("engineering", "science", "math", "nurs", "health", "medical", "clinical", "therapy", "pharmacy")):
        return "STEM"
    if any(tok in course_name for tok in ("business", "accounting", "accountancy", "management", "marketing", "economics", "entrepreneur")):
        return "BE"
    if any(tok in course_name for tok in ("psychology", "education", "communication", "social", "humanities", "arts", "political", "criminology")):
        return "ARSSH"
    return "Other"

# Infer Student's Strengths from Feature Scores (returns top 3 strengths with scores >= 75)
def _infer_strengths_from_features(features):
    ranked = sorted(zip(CATEGORY_NAMES, features), key=lambda t: t[1], reverse=True)
    return [name for name, value in ranked if value >= 75][:3]

# Generate Reason for Course Match (explains why a course is suitable based on category, student's strengths, and academic cluster)
#TODO:Enhance the reasoning by incorporating more nuanced analysis of student's strengths and course requirements
def _course_match_reason(course_name, category, strongest, strand):
    focus = {
        "Allied Health": "science, biology, and patient-focused learning",
        "Computer Studies": "technology, logic, data, and problem-solving",
        "Engineering": "mathematics, science, and applied problem-solving",
        "Business & Management": "business judgment, organization, and communication",
        "Social Sciences & Education": "communication, social understanding, and people-focused work",
        "Agriculture": "science, environmental systems, and practical resource management",
        "Hospitality & Tourism": "communication, service, and organized operations",
        "Public Service & Governance": "social awareness, communication, and civic problem-solving",
        "Arts & Design": "communication, creativity, and visual thinking",
        "Sports & Physical Education": "fitness, movement, and life and health sciences",
    }.get(category, "your overall academic profile")
    strength_text = ", ".join(strongest[:2]) if strongest else "your balanced grades"
    strand_text = f" It is also compatible with your {strand} cluster." if strand else ""
    return f"{course_name} draws on {focus}. Your strongest areas are {strength_text}.{strand_text}"

# Validate Recommendation Course Name (checks if the course name is suitable for recommendation)
def _valid_recommendation_course_name(course_name):
    if not course_name or not isinstance(course_name, str):
        return False
    cleaned = course_name.strip()
    if not cleaned or cleaned.lower() in {"general education", "other", "recommended course", "safe general option", "n/a", "na"}:
        return False
    if "no strong academic pattern" in cleaned.lower():
        return False
    return True


# Sanitize Recommendations (filters out invalid or unsuitable course recommendations)
def _sanitize_recommendations(payload):
    if not payload:
        return []
    try:
        decoded = json.loads(payload) if isinstance(payload, str) else payload
    except (TypeError, ValueError):
        return []

    if not isinstance(decoded, list):
        return []

    valid = []
    allowed_courses = {sample["course"] for sample in _course_training_data()}
    for item in decoded:
        if not isinstance(item, dict):
            continue
        course_name = item.get("course")
        if not _valid_recommendation_course_name(course_name) or course_name not in allowed_courses:
            continue
        valid.append({
            "course": course_name,
            "description": item.get("description") or "",
            "reason": item.get("reason") or "",
            "category": item.get("category") or categorize_course(course_name, item.get("description") or ""),
            "confidence": item.get("confidence", 0),
            "match_score": item.get("match_score", item.get("confidence", 0)),
            "model_basis": item.get("model_basis") if item.get("model_basis") == MODEL_BASIS else "",
            **({"selection_basis": "subject_evidence"} if item.get("selection_basis") == "subject_evidence" else {}),
            "core_grade_fit": item.get("core_grade_fit", 0),
            "strand_grade_based": bool(item.get("strand_grade_based", False)),
            "field_fit": item.get("field_fit"),
            "subject_field_evidence": [
                {"field": entry["field"], "grade": entry["grade"], "priority": entry.get("priority", "supporting")}
                for entry in item.get("subject_field_evidence", [])
                if isinstance(entry, dict)
                and isinstance(entry.get("field"), str)
                and isinstance(entry.get("grade"), (int, float))
                and 0 <= entry["grade"] <= 100
                and entry.get("priority", "supporting") in {"major", "supporting"}
            ],
            "strand_alignment": bool(item.get("strand_alignment", False)),
            "strand_label": item.get("strand_label") or "",
            "recommendation_basis": (
                item.get("recommendation_basis")
                if item.get("recommendation_basis") in {"strand", "grades", "grade_alternative"}
                else "strand" if item.get("strand_label") else "grades"
            ),
        })
    return valid

def _build_course_model(observed_indices=None, *, training_data=None):
    global _COURSE_MODEL_CACHE
    if training_data is None:
        training_data = _course_training_data()
    if not training_data:
        raise ValueError("The recommendation catalog has no active courses.")
    if observed_indices is None:
        observed_indices = tuple(range(len(CATEGORY_NAMES)))
    key = (
        tuple(observed_indices),
        catalog_fingerprint(training_data),
        MODEL_PATH.stat().st_mtime_ns if MODEL_PATH.exists() else None,
    )
    cached = _COURSE_MODEL_CACHE
    if cached is not None and cached[0] == key:
        return cached[1]

    model = model_for_catalog(training_data, observed_indices)
    _COURSE_MODEL_CACHE = (key, model)
    return model

def _is_degree_program(value):
    return bool(re.match(
        r"^(?:bachelor(?:'s)?\b|(?:bs|ba|bsc|beng|btech|bba|bsa|bscs|bsit|bshm|bstm|bsn|bsed|beed|bped)(?:$|[\s.-]))",
        str(value or "").strip(),
        flags=re.IGNORECASE,
    ))


def _strand_words(strand):
    tokens = re.findall(r"[a-z0-9]+", str(strand or "").lower())
    return set(tokens), " ".join(tokens)


# Older strand/track names map to the school's current clusters; retired labels (GAS, Sports, etc.) map to none.
def _recommendation_strand_code(strand):
    if _is_degree_program(strand):
        return ""
    tokens, words = _strand_words(strand)
    if "stem" in tokens or "science technology engineering and mathematics" in words:
        return "STEM"
    if tokens & {"ictech", "ict"} or "information and communications technology" in words or "information and communication technology" in words:
        return "ICTECH"
    if tokens & {"be", "abm"} or "business entrepreneurship" in words or "business and entrepreneurship" in words or "accountancy and business management" in words:
        return "BE"
    if tokens & {"ht", "he"} or "hospitality and tourism" in words or "home economics" in words:
        return "HT"
    if tokens & {"arssh", "humss", "humms", "hums"} or "arts social sciences and humanities" in words or "humanities and social sciences" in words:
        return "ARSSH"
    return ""


def _is_retired_strand(strand):
    tokens, words = _strand_words(strand)
    if tokens & {"gas", "sports", "tvl", "ia", "hr"}:
        return True
    return any(name in words for name in (
        "general academic", "arts and design", "arts design", "industrial arts", "physical education",
        "technical vocational",
    ))


def _recommendation_subjects_only(subjects_text):
    excluded = re.compile(
        r"\b(?:fcl|p\.?\s*e\.?)(?:\s*\d+)?\b|"
        r"\b(?:filipino\s+)?christian\s+living\b|\bphysical\s+education\b",
        re.IGNORECASE,
    )
    return "\n".join(
        line for line in str(subjects_text or "").splitlines()
        if not excluded.search(line)
    )


def _rank_course_recommendations(subjects_text, current_course="", strand="", *, outside_strand=False):
    subjects_text = _recommendation_subjects_only(subjects_text)
    training_data = _course_training_data()
    if _is_degree_program(strand):
        strand = ""
    strand_text = str(strand or "").strip()
    strand_key = _recommendation_strand_code(strand)
    if outside_strand and not strand_key:
        return []
    missing_strand_values = {"other", "unknown", "not detected", "not available", "not specified", "n/a", "na", "none"}
    if strand_text and not strand_key and strand_text.casefold() not in missing_strand_values and not _is_retired_strand(strand_text):
        return []

    subject_scores = _extract_subject_scores(subjects_text)
    features = _build_feature_vector(subjects_text)
    if not any(subject_scores.values()):
        return []

    observed_indices = tuple(
        index for index, category in enumerate(CATEGORY_NAMES) if subject_scores[category]
    )
    query = [[
        _average(subject_scores[CATEGORY_NAMES[index]])
        for index in observed_indices
    ]]
    model = _build_course_model(observed_indices, training_data=training_data)
    log_support = dict(zip(model.classes_, model.predict_log_proba(query)[0]))

    strongest = _infer_strengths_from_features(features)
    field_scores = _extract_subject_field_scores(subjects_text)
    tourism_average = _average(field_scores["tourism_hospitality"])
    foreign_language_average = _average(field_scores["foreign_languages"]) if field_scores["foreign_languages"] else None
    observed_category_averages = [
        _average(subject_scores[category])
        for category in CATEGORY_NAMES
        if subject_scores[category]
    ]
    overall_grade_average = _average(observed_category_averages) if observed_category_averages else None
    biology_average = _average(field_scores["biology"]) if field_scores["biology"] else None
    chemistry_average = _average(field_scores["chemistry"]) if field_scores["chemistry"] else None
    biology_strength = _relative_subject_strength(biology_average, overall_grade_average)
    chemistry_strength = _relative_subject_strength(chemistry_average, overall_grade_average)
    foreign_language_strength = _relative_subject_strength(foreign_language_average, overall_grade_average)
    math_subject_labels = {
        "math_algebra": "Algebra",
        "math_calculus": "Calculus",
        "math_geometry": "Geometry",
        "math_trigonometry": "Trigonometry",
        "math_statistics": "Statistics",
        "math_general": "General Mathematics",
        "physics": "Physics",
    }
    # General Mathematics is in every engineering focus, so only specific subtopics can single out a program.
    observed_math_subjects = {
        field: _average(field_scores[field])
        for field in math_subject_labels
        if field_scores[field] and field != "math_general"
    }
    highest_math_subject = max(observed_math_subjects, key=observed_math_subjects.get) if observed_math_subjects else None
    highest_math_grade = observed_math_subjects.get(highest_math_subject) if highest_math_subject else None
    math_subject_strength = _relative_subject_strength(highest_math_grade, overall_grade_average)
    observed_areas = [category for category in CATEGORY_NAMES if subject_scores[category]]
    student_top_area = max(observed_areas, key=lambda category: _average(subject_scores[category])) if len(observed_areas) > 1 else None
    recommendations = []
    for catalog_index, sample in enumerate(training_data):
        distance = _observed_weighted_distance(subject_scores, sample["features"])
        category = categorize_course(sample["course"], sample.get("description", ""))
        strand_alignment = bool(strand_key and strand_key in sample.get("strands", []))
        if strand_key and strand_alignment == outside_strand:
            continue
        field_evidence = _recommendation_field_evidence(
            category, field_scores, subject_scores, sample["features"], course_name=sample["course"]
        )
        major_evidence = [entry for entry in field_evidence if entry["priority"] == "major"]
        # A similar reference profile cannot qualify a course on supporting grades alone.
        if not major_evidence:
            continue
        field_fit = _field_evidence_fit(field_evidence)
        major_fit = _average([entry["grade"] for entry in major_evidence])
        grade_similarity = max(0.0, 100.0 - distance)
        reason = _course_match_reason(
            sample["course"], category, strongest, "" if outside_strand else strand
        )
        major_summary = ", ".join(
            f"{entry['field']} ({entry['grade']:.1f})" for entry in major_evidence
        )
        if category in COURSE_FIELD_PRIORITIES:
            reason += f" Your major-related grades include {major_summary}."
        else:
            reason += f" Your grades match the course's highest reference area: {major_summary}."
        shape_fit, student_spread = _profile_shape_alignment(subject_scores, sample["features"])
        # A flat report card says little about relative strengths, so shape counts less.
        shape_weight = PROFILE_SHAPE_WEIGHT * min(1.0, student_spread / PROFILE_SHAPE_FULL_SPREAD) if shape_fit is not None else 0.0
        ranking_score = (
            grade_similarity * (1.0 - shape_weight - FIELD_FIT_WEIGHT)
            + (shape_fit or 0.0) * shape_weight
            + major_fit * FIELD_FIT_WEIGHT
        )
        reason += f" Your grades in related subject fields average {field_fit:.1f}."
        if shape_fit is not None and shape_weight >= PROFILE_SHAPE_WEIGHT * 0.5 and student_top_area:
            key_areas = _course_key_areas(sample["features"])
            if student_top_area in key_areas:
                reason += f" Your strongest area, {CATEGORY_LABELS[student_top_area]}, is one of this course's key subjects."
            elif shape_fit < 50:
                reason += (
                    f" This course leans on {' and '.join(CATEGORY_LABELS[area] for area in key_areas)}, "
                    f"while your strongest area is {CATEGORY_LABELS[student_top_area]}."
                )
        coverage, missing_key_areas = _course_key_subject_coverage(subject_scores, sample["features"])
        ranking_score -= (1.0 - coverage) * MISSING_KEY_SUBJECT_PENALTY
        if missing_key_areas:
            reason += (
                f" No {' or '.join(missing_key_areas)} grades were found for this course's key "
                f"{'area' if len(missing_key_areas) == 1 else 'areas'}, so this match is less certain."
            )
        subject_bonus = 0.0
        if sample["course"] == "BS in Hospitality Management" and tourism_average >= 80:
            tourism_priority = min(12.0, (tourism_average - 80) * 0.6)
            subject_bonus += tourism_priority
            reason += f" Your tourism-related subject average is {tourism_average:.1f}, prioritizing this hospitality match."
        if category == "Hospitality & Tourism" and foreign_language_average is not None and foreign_language_strength > 0:
            subject_bonus += 4.0 + foreign_language_strength
            reason += f" Your foreign-language subject average is {foreign_language_average:.1f}, supporting this tourism and hospitality match."
        if category == "Allied Health" and biology_average is not None:
            subject_bonus += biology_strength
            if biology_strength:
                direction = "above" if biology_strength > 0 else "below"
                reason += f" Your Biology subject average of {biology_average:.1f} is {direction} your overall subject average, influencing this health-care match."
        course_text = f"{sample['course']} {sample.get('description', '')}".lower()
        chemistry_related_terms = ("chemistry", "chemical", "biochemistry", "pharmacy", "pharmaceutical", "medical technology")
        is_chemistry_related = any(term in course_text for term in chemistry_related_terms)
        if is_chemistry_related and chemistry_average is not None:
            subject_bonus += chemistry_strength
            if chemistry_strength:
                direction = "above" if chemistry_strength > 0 else "below"
                reason += f" Your Chemistry subject average of {chemistry_average:.1f} is {direction} your overall subject average, influencing this chemistry-related match."
        engineering_focus = ENGINEERING_SUBJECT_FOCUS.get(sample["course"], set())
        if highest_math_subject in engineering_focus and math_subject_strength > 0:
            subject_bonus += 4.0 + math_subject_strength
            reason += f" Your highest math-related subject is {math_subject_labels[highest_math_subject]} ({highest_math_grade:.1f}), supporting this engineering match."
        # Capped so specific-subject boosts reorder close matches without pinning scores at 100%.
        ranking_score += max(-MAX_SUBJECT_BONUS, min(MAX_SUBJECT_BONUS, subject_bonus))
        item = {
            "course": sample["course"],
            "description": sample.get("description", ""),
            "reason": f"{reason} Course-specific evidence checks are guidance safeguards, not admission requirements. {MODEL_NOTICE}",
            "category": category,
            "confidence": round(grade_similarity, 2),
            "match_score": round(float(np.exp(log_support[sample["course"]])) * 100, 2),
            "model_basis": MODEL_BASIS,
            "core_grade_fit": round(_core_grade_fit(features, sample["features"]), 2),
            "field_fit": round(field_fit, 2) if field_fit is not None else None,
            "subject_field_evidence": field_evidence,
            "strand_alignment": strand_alignment,
            "strand_label": strand_key,
            "_ranking_score": ranking_score,
            "_model_log_support": float(log_support[sample["course"]]),
            "_catalog_index": catalog_index,
        }
        if strand_alignment:
            item["reason"] += f" It aligns with the {strand_key} cluster."
        elif outside_strand:
            item["recommendation_basis"] = "grade_alternative"
            item["reason"] += (
                f" It is outside your {strand_key} cluster catalog; "
                "its ranking uses your grades rather than cluster eligibility."
            )
        recommendations.append(item)

    # Classifier support determines order; domain evidence only breaks exact ties.
    recommendations.sort(key=lambda item: (
        -item["_model_log_support"], -item["_ranking_score"], item["_catalog_index"],
    ))

    if recommendations and not outside_strand:
        recommendations[0]["strand_grade_based"] = bool(strand_key)
        if strand_key:
            recommendations[0]["reason"] += f" This result is limited to catalog courses for the {strand_key} cluster."
        else:
            area_labels = {
                "math": "Math", "science": "Science", "english": "English",
                "technology": "Technology", "business": "Business", "social": "Social Studies",
            }
            strongest_areas = sorted(
                (
                    (area_labels[category], _average(subject_scores[category]))
                    for category in CATEGORY_NAMES
                    if subject_scores[category]
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:2]
            area_summary = " and ".join(f"{name} ({grade:.1f})" for name, grade in strongest_areas)
            recommendations[0]["reason"] += f" No cluster was detected, so this ranking uses your highest grade areas: {area_summary}."
    for item in recommendations:
        item.pop("_ranking_score", None)
        item.pop("_model_log_support", None)
        item.pop("_catalog_index", None)
    return recommendations


# Recommend only courses cataloged for the student's cluster.
def recommend_course(subjects_text, current_course="", strand=""):
    return _select_recommendations(
        _rank_course_recommendations(subjects_text, current_course, strand), subjects_text, 5,
    )


def _select_recommendations(ranked, subjects_text, limit):
    selected = ranked[:limit]
    if not selected or len(ranked) <= limit:
        return selected
    fields = _extract_subject_field_scores(_recommendation_subjects_only(subjects_text))
    if not fields["entrepreneurship"]:
        return selected
    entrepreneurship_grade = _average(fields["entrepreneurship"])
    highest_grade = max(grade for grades in fields.values() for grade in grades)
    if entrepreneurship_grade < highest_grade:
        return selected
    if any(_contains_subject_term(item["course"].lower(), "entrepreneurship") for item in selected):
        return selected
    candidate = next((
        item for item in ranked[limit:]
        if _contains_subject_term(item["course"].lower(), "entrepreneurship")
    ), None)
    if candidate is None:
        return selected
    selected[-1] = {
        **candidate,
        "selection_basis": "subject_evidence",
        "reason": (
            f"Included because Entrepreneurship ({entrepreneurship_grade:.1f}) is one of your "
            "highest graded recognized subjects. This slot is selected by subject evidence, "
            "not model rank; other displayed courses retain their model order. "
            + candidate["reason"]
        ),
    }
    return selected


def _recommendation_limit():
    limit = _get_system_settings().get("recommendation_limit", 3)
    try:
        limit = max(1, min(5, int(limit)))
    except (TypeError, ValueError):
        limit = 3
    return limit


def _limit_recommendations(payload, limit=None):
    limit = _recommendation_limit() if limit is None else limit
    recommendations = _sanitize_recommendations(payload)
    primary = [item for item in recommendations if item["recommendation_basis"] != "grade_alternative"]
    alternatives = [item for item in recommendations if item["recommendation_basis"] == "grade_alternative"]
    primary = primary[:limit]
    primary_courses = {item["course"] for item in primary}
    return primary + [item for item in alternatives if item["course"] not in primary_courses][:1]


def _grade_based_alternatives(subjects_text, strand):
    return _rank_course_recommendations(subjects_text, strand=strand, outside_strand=True)


def _configured_recommendations(subjects_text, current_course="", strand=""):
    limit = _recommendation_limit()
    recommendations = _select_recommendations(
        _rank_course_recommendations(subjects_text, current_course, strand), subjects_text, limit,
    )
    for item in recommendations:
        item["recommendation_basis"] = "strand" if item.get("strand_label") else "grades"
    alternatives = _select_recommendations(_grade_based_alternatives(subjects_text, strand), subjects_text, 1)
    return _limit_recommendations(recommendations + alternatives, limit)

# Calculate the average profile for a given course based on historical training data
def _course_average_profile(course_name):
    training_data = _course_training_data()
    course_name = (course_name or "").strip()
    course_data = None
    for candidate in training_data:
        if candidate["course"].lower() == course_name.lower():
            course_data = candidate
            break
    # If no exact match is found, default to the first course in the training data
    if course_data is None:
        course_data = training_data[0]

    by_subject = {}
    for subject_name, value in zip(CATEGORY_NAMES, course_data["features"]):
        by_subject[subject_name] = round(float(value), 2)

    overall_average = round(sum(by_subject.values()) / len(by_subject), 2) if by_subject else 0.0
    return {
        "course": course_data["course"],
        "by_subject": by_subject,
        "overall_average": overall_average,
        "description": course_data.get("description", ""),
    }

# Build a detailed analytics report comparing a student's performance against the average course profile
def _build_student_performance_analytics(course_name, subjects_text):
    training_data = _course_training_data()
    requested_course = (course_name or "").strip()
    if not _valid_recommendation_course_name(requested_course):
        requested_course = ""

    if requested_course:
        course_match = next((item["course"] for item in training_data if item["course"].lower() == requested_course.lower()), None)
        selected_course = course_match or training_data[0]["course"]
    else:
        selected_course = training_data[0]["course"]

    course_profile = _course_average_profile(selected_course)
    student_scores = _extract_subject_scores(_recommendation_subjects_only(subjects_text))

    subject_breakdown = []
    for subject in CATEGORY_NAMES:
        student_value = round(_average(student_scores.get(subject, [])), 2) if student_scores.get(subject) else None
        course_value = course_profile["by_subject"].get(subject, 0.0)
        delta = round(student_value - course_value, 2) if student_value is not None else None
        status = "no grade available" if delta is None else "above reference" if delta >= 0 else "below reference"
        subject_breakdown.append({
            "subject": subject,
            "student": student_value,
            "course": course_value,
            "difference": delta,
            "status": status,
        })

    observed_comparisons = [item for item in subject_breakdown if item["student"] is not None]
    student_overall = round(_average([item["student"] for item in observed_comparisons]), 2) if observed_comparisons else None
    course_overall = round(_average([item["course"] for item in observed_comparisons]), 2) if observed_comparisons else None
    overall_gap = round(student_overall - course_overall, 2) if student_overall is not None and course_overall is not None else None
    comparison = [
        {
            "subject": item["subject"],
            "student": item["student"],
            "course": item["course"],
            "difference": item["difference"],
            "status": item["status"],
        }
        for item in subject_breakdown
    ]

    if overall_gap is None:
        narrative = "There are no recognized subject grades to compare with this course reference profile."
    else:
        narrative = (
            f"Across subjects with grades, your average is {student_overall:.2f}; the course reference is {course_overall:.2f}."
        )

    return {
        "selected_course": selected_course,
        "course_description": course_profile["description"],
        "course_average": course_overall,
        "student_overall": student_overall,
        "overall_gap": overall_gap,
        "narrative": narrative,
        "comparison": comparison,
        "subject_breakdown": subject_breakdown,
        "compared_subject_count": len(observed_comparisons),
    }

# Retrieve the latest profile of a user from the database
def _get_user_latest_profile(user_id):
    try:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, recommendation, strand
            FROM student_profiles
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()
    except psycopg.Error:
        return None
    # If no profile is found or an error occurs, return None
    if not row:
        return None
    return {
        "student_number": row[0],
        "first_name": row[1],
        "middle_initial": row[2],
        "last_name": row[3],
        "course": row[4],
        "gwa": row[5],
        "grades": row[6],
        "subjects": row[7],
        "recommendation": row[8],
        "strand": row[9],
    }

# Initialize the database and create necessary tables if they do not exist
def init_database():
    conn = _db_conn()
    cursor = conn.cursor()

    cursor.execute(
        "ALTER TABLE IF EXISTS university_courses ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            username TEXT,
            password_hash TEXT NOT NULL,
            profile_picture TEXT DEFAULT 'default.png',
            role TEXT DEFAULT 'student',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_login_at TIMESTAMPTZ,
            must_change_password BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 0")
    cursor.execute(SECURITY_SCHEMA)
    cursor.execute("ALTER TABLE users ALTER COLUMN email DROP NOT NULL")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_unique ON users (LOWER(username)) WHERE username IS NOT NULL")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            settings_key TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL,
            updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        "INSERT INTO system_settings (settings_key, settings_json) VALUES ('global', ?) ON CONFLICT (settings_key) DO NOTHING",
        (json.dumps(SYSTEM_SETTING_DEFAULTS),),
    )
    cursor.execute("SELECT settings_json FROM system_settings WHERE settings_key = 'global'")
    saved_settings_row = cursor.fetchone()
    if saved_settings_row:
        try:
            saved_settings = json.loads(saved_settings_row[0])
            saved_strands = saved_settings.get("available_strands", [])
            # Settings saved before the BE/STEM/ICTECH/HT/ARSSH clusters reset to the current cluster list.
            if not isinstance(saved_strands, list) or not saved_strands or any(strand not in SYSTEM_SETTING_STRANDS for strand in saved_strands):
                saved_settings["available_strands"] = list(SYSTEM_SETTING_STRANDS)
                cursor.execute(
                    "UPDATE system_settings SET settings_json = ?, updated_at = CURRENT_TIMESTAMP WHERE settings_key = 'global'",
                    (json.dumps(saved_settings),),
                )
        except (TypeError, ValueError, AttributeError):
            pass
    # Create the student_profiles table if it does not exist
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_profiles (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL,
            student_number TEXT,
            first_name TEXT,
            middle_initial TEXT,
            last_name TEXT,
            course TEXT,
            gwa TEXT,
            subjects TEXT,
            recommendation TEXT,
            strand TEXT,
            upload_date TEXT,
            grades TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS report_card_uploads (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            original_filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            file_data BYTEA NOT NULL,
            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ocr_status TEXT NOT NULL DEFAULT 'needs_review',
            is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
            flag_note TEXT,
            student_name TEXT NOT NULL DEFAULT '',
            student_number TEXT NOT NULL DEFAULT '',
            extracted_subjects TEXT NOT NULL DEFAULT '',
            extracted_gwa TEXT NOT NULL DEFAULT '',
            ocr_error TEXT,
            processed_at TIMESTAMPTZ
        )
        """
    )
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS ocr_status TEXT NOT NULL DEFAULT 'needs_review'")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS is_flagged BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS flag_note TEXT")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS student_name TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS student_number TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS extracted_subjects TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS extracted_gwa TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS ocr_error TEXT")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE report_card_uploads ADD COLUMN IF NOT EXISTS upload_batch TEXT")
    cursor.execute(
        """
        ALTER TABLE student_profiles
        ADD COLUMN IF NOT EXISTS report_card_upload_id BIGINT
        REFERENCES report_card_uploads(id) ON DELETE SET NULL
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS course_recommendation_history (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            profile_id BIGINT NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
            recommendations TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_activity_logs (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            actor_name TEXT NOT NULL DEFAULT '',
            actor_email TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            target_user_id BIGINT,
            target_label TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '',
            session_ip TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute("ALTER TABLE admin_activity_logs ADD COLUMN IF NOT EXISTS session_ip TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE admin_activity_logs ADD COLUMN IF NOT EXISTS actor_role TEXT NOT NULL DEFAULT 'admin'")
    cursor.execute(
        """
        INSERT INTO course_recommendation_history (profile_id, recommendations)
        SELECT profiles.id, profiles.recommendation
        FROM student_profiles profiles
        WHERE profiles.recommendation IS NOT NULL AND profiles.recommendation != ''
            AND NOT EXISTS (
                SELECT 1 FROM course_recommendation_history history
                WHERE history.profile_id = profiles.id
            )
        """
    )
    # Remove orphaned student profiles that do not have a corresponding user
    cursor.execute(
        """
                DELETE FROM student_profiles profile
                WHERE profile.user_id IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = profile.user_id)
        """
    )
    # Commit the changes and close the connection
    conn.commit()
    if ADMIN_USERNAME and ADMIN_PASSWORD:
        cursor.execute("SELECT id, COALESCE(role, 'student') FROM users WHERE email = ?", (ADMIN_USERNAME,))
        bootstrap_user = cursor.fetchone()
        if not bootstrap_user:
            password_error = _password_policy_error(ADMIN_PASSWORD, admin=True)
            if password_error:
                conn.close()
                raise RuntimeError(f"BOOTSTRAP_ADMIN_PASSWORD does not meet policy: {password_error}")
            cursor.execute(
                "INSERT INTO users (name, email, password_hash, profile_picture, role) VALUES (?, ?, ?, ?, ?)",
                (BOOTSTRAP_ADMIN_NAME, ADMIN_USERNAME, _hash_password(ADMIN_PASSWORD), "default.svg", ROLE_SUPER_ADMIN),
            )
            conn.commit()

    cursor.execute("SELECT id, name FROM users WHERE role IN (?, ?) AND username IS NULL ORDER BY id", (ROLE_ADMIN, ROLE_SEMI_ADMIN))
    for user_id, name in cursor.fetchall():
        base = re.sub(r"[^a-z0-9]+", ".", (name or "").casefold()).strip(".")[:24] or "admin"
        username = base if len(base) >= 3 else f"admin{user_id}"
        cursor.execute("SELECT 1 FROM users WHERE LOWER(username) = ?", (username,))
        if cursor.fetchone():
            username = f"{base}.{user_id}"
        cursor.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
    conn.commit()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_images (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            content_type TEXT NOT NULL,
            image_data BYTEA NOT NULL,
            source TEXT NOT NULL DEFAULT 'upload',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    # Move legacy uploads that only lived on the local disk into the database.
    cursor.execute("SELECT id, profile_picture FROM users WHERE profile_picture IS NOT NULL")
    for user_id, picture in cursor.fetchall():
        picture = (picture or "").strip()
        lowered = picture.lower()
        if lowered in _DEFAULT_PROFILE_VALUES or lowered.startswith(("http://", "https://", PROFILE_IMAGE_DB_PREFIX)):
            continue
        image_path = os.path.join(UPLOAD_FOLDER, os.path.basename(picture))
        if not os.path.isfile(image_path):
            continue
        try:
            with open(image_path, "rb") as image_file:
                _store_profile_image(cursor, user_id, image_file.read(), "upload")
            conn.commit()
        except (OSError, ValueError):
            conn.rollback()
    conn.close()
# Check if the current session belongs to an admin user
def _is_admin_session(sess, allow_password_change=False):
    user_id = sess.get("user_id")
    if not user_id:
        return False
    try:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE), auth_version FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
    except Exception:
        return False
    if not row or not row[1] or row[0] not in (ROLE_ADMIN, ROLE_SEMI_ADMIN) or row[3] != sess.get("auth_version", 0):
        sess.clear()
        return False
    sess["role"] = row[0]
    sess["is_admin"] = True
    sess["must_change_password"] = bool(row[2])
    if row[2] and not allow_password_change:
        return False
    return True

# Check if the current session belongs to a full admin user
def _is_full_admin_session(sess):
    if not _is_admin_session(sess):
        return False
    return sess.get("role") == ROLE_SUPER_ADMIN


def _record_admin_activity(sess, action, target_user_id=None, target_label="", details="", connection=None):
    if not sess or sess.get("role") not in (ROLE_ADMIN, ROLE_SEMI_ADMIN) or not sess.get("user_id"):
        return
    owns_connection = connection is None
    conn = connection
    try:
        if conn is None:
            conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO admin_activity_logs
                (actor_user_id, actor_name, actor_email, action, target_user_id, target_label, details, session_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sess["user_id"],
                str(sess.get("name") or "")[:200],
                str(sess.get("email") or "")[:320],
                str(action)[:100],
                target_user_id,
                str(target_label or "")[:320],
                str(details or "")[:1000],
                str(sess.get("admin_ip") or "")[:64],
            ),
        )
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _client_ip(req):
    forwarded = (req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (req.client.host if req.client else "")


def _record_student_activity(req, action, details="", target_label="", user_id=None, name=None, email=None):
    sess = req.session
    actor_id = user_id or sess.get("user_id")
    if not actor_id or (user_id is None and sess.get("role", "student") != "student"):
        return
    conn = None
    try:
        conn = _db_conn()
        conn.execute(
            """
            INSERT INTO admin_activity_logs
                (actor_user_id, actor_name, actor_email, action, target_label, details, session_ip, actor_role)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'student')
            """,
            (
                actor_id,
                str(name if name is not None else sess.get("name") or "")[:200],
                str(email if email is not None else sess.get("email") or "")[:320],
                str(action)[:100],
                str(target_label or "")[:320],
                str(details or "")[:1000],
                str(_client_ip(req))[:64],
            ),
        )
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()

# Render a template with the current session context and additional context variables
def _render(req, template_name, **ctx):
    sess = req.session
    if "user_id" in sess:
        if sess.get("role") in (ROLE_ADMIN, ROLE_SEMI_ADMIN) and not sess.get("admin_ip"):
            sess["admin_ip"] = req.client.host if req.client else ""
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT profile_picture, username FROM users WHERE id = ?", (sess["user_id"],))
        row = cursor.fetchone()
        conn.close()
        if row:
            _set_session_profile_image(sess, row[0])
            sess["username"] = row[1] or ""
    # Render the template with the session and additional context
    body = TEMPLATES.get_template(template_name).render(
        name=sess.get("name", "User"),
        email=sess.get("email") or "",
        username=sess.get("username") or "",
        admin=sess.get("admin_user", sess.get("name", "Administrator")),
        profile_image=sess.get("profile_image", _default_profile_image_url()),
        current_user_id=sess.get("user_id", ""),
        account_role=sess.get("role", ""),
        **ctx,
    )
    return HTMLResponse(body)

# FastHTML owns rendered pages; the FastAPI sub-app below owns the JSON API.
app, fast_html_route = fast_app(
    secret_key=os.getenv("APP_SECRET_KEY", "your_secret_key"),
    static_path=".",
    default_hdrs=False,
)

_HTML_PAGE_METHODS = {
    "/": {"GET"},
    "/login": {"GET"},
    "/home": {"GET"},
    "/guest-login": {"GET"},
    "/logout": {"GET"},
    "/admin/login": {"GET", "POST"},
    "/admin/logout": {"GET"},
    "/admin": {"GET"},
    "/admin/dashboard": {"GET"},
    "/admin/recovery-verification": {"GET"},
    "/admin/force-password-change": {"GET"},
    "/admin/students": {"GET"},
    "/google-login": {"GET"},
    "/authorize": {"GET"},
}


def fastapi_route(path, methods=None):
    def register(handler):
        route_methods = set(methods or ["GET"])
        if route_methods.issubset(_HTML_PAGE_METHODS.get(path, set())):
            return fast_html_route(path, methods=methods)(handler)
        return handler

    return register


class _FirstLoginPasswordChangeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            session = scope.get("session", {})
            path = scope.get("path", "")
            allowed_paths = {
                "/",
                "/login",
                "/admin/force-password-change",
                "/admin/dashboard",
                "/admin/recovery-verification",
                "/admin/change_password",
                "/api/v1/admin/change_password",
                "/api/v1/admin/forgot-password",
                "/api/v1/admin/verify-recovery",
                "/api/v1/admin/reset-password",
                "/admin/logout",
                "/logout",
            }
            if session.get("must_change_password") and path not in allowed_paths and not path.startswith(("/static/", "/api/v1/profile-image/")):
                headers = {key.lower(): value for key, value in scope.get("headers", [])}
                if b"text/html" in headers.get(b"accept", b""):
                    response = RedirectResponse("/admin/dashboard", status_code=303)
                else:
                    response = JSONResponse(
                        {"success": False, "code": "password_change_required", "message": "Change your temporary password before continuing."},
                        status_code=403,
                    )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Run after SessionMiddleware so the gate can inspect the authenticated session.
app.user_middleware.append(Middleware(_FirstLoginPasswordChangeMiddleware))
app.middleware_stack = None

# Configure OAuth providers for the application
oauth = OAuth()
# Register the Google OAuth provider
google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
# Define the route for the login page
@fastapi_route("/", methods=["GET"])
def login_page(req):
    return _render(req, "Login.html")

# Define the route for the login page alias
@fastapi_route("/login", methods=["GET"])
def login_page_alias(req):
    return _render(req, "Login.html")


@fastapi_route("/admin/recovery-verification", methods=["GET"])
def admin_recovery_verification_page(req):
    return _render(req, "Login.html", recovery_verification_page=True)


# Define the route for the home page
@fastapi_route("/home", methods=["GET"])
def home(req):
    sess = req.session
    return _render(
        req,
        "StudentManagement.html",
        home_mode=True,
        is_guest=bool(sess.get("is_guest")),
        can_email_results=_can_email_results(sess),
    )


def _can_email_results(sess):
    return bool(
        sess.get("user_id")
        and not sess.get("is_guest")
        and sess.get("auth_provider") == "google"
        and sess.get("email")
    )


# Define the route for guest login
@fastapi_route("/guest-login", methods=["GET"])
def guest_login(req):
    req.session.clear()
    req.session["is_guest"] = True
    req.session["name"] = "Guest"
    req.session["email"] = "Anonymous session"
    return RedirectResponse("/home", status_code=302)


# Define the route for generating course recommendations
@fastapi_route("/generate_recommendations", methods=["POST"])
async def generate_recommendations(req):
    sess = req.session
    user_id = sess.get("user_id")
    if not user_id:
        return JSONResponse({"success": False, "message": "Please sign in first."}, status_code=401)

    profile = _get_user_latest_profile(user_id)
    if not profile:
        return JSONResponse({"success": False, "message": "Save a student profile first."})

    recommendation_subjects = _clean_subjects_for_recommendation(profile.get("subjects", "")) or profile.get("subjects", "")
    recommendations = _configured_recommendations(
        recommendation_subjects,
        profile.get("course", ""),
        profile.get("strand", ""),
    )
    payload = json.dumps(recommendations)
    sess.pop("latest_recommendations", None)
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE student_profiles SET recommendation = ? WHERE id = (SELECT id FROM student_profiles WHERE user_id = ? ORDER BY id DESC LIMIT 1) RETURNING id",
        (payload, user_id),
    )
    profile_row = cursor.fetchone()
    if profile_row:
        cursor.execute(
            "INSERT INTO course_recommendation_history (profile_id, recommendations) VALUES (?, ?)",
            (profile_row[0], payload),
        )
    conn.commit()
    conn.close()
    top_course = (recommendations[0] or {}).get("course", "") if recommendations else ""
    _record_student_activity(req, "recommendations_generated", f"{len(recommendations)} courses; top match: {top_course}" if top_course else f"{len(recommendations)} courses")
    result = {"success": True, "count": len(recommendations)}
    if not recommendations:
        result["message"] = "No courses have enough course-specific grade evidence. Previous suggestions were cleared."
    return JSONResponse(result)


# Define the route for user registration
@fastapi_route("/register", methods=["POST"])
async def register(req):
    data = await req.json()
    name = data.get("name", "")
    email = data.get("email", "")
    password = str(data.get("password") or "")
    password_error = _password_policy_error(password)
    if password_error:
        return JSONResponse({"success": False, "message": password_error}, status_code=400)

    hashed_password = _hash_password(password)

    try:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users
            (name, email, password_hash, role, profile_picture)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (name, email, hashed_password, "student", "default.svg"),
        )
        new_user_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        _record_student_activity(req, "account_registered", "Email sign-up", user_id=new_user_id, name=name, email=email)
        return JSONResponse({"success": True, "message": "Account Created"})
    except UniqueViolation:
        return JSONResponse({"success": False, "message": "Email already exists"})


# Define the route for user login
@fastapi_route("/login", methods=["POST"])
async def login(req):
    data = await req.json()
    identifier = str(data.get("username") or data.get("email") or "").strip().lower()
    password = data.get("password", "")
    if not identifier or not isinstance(password, str) or not password:
        return JSONResponse({"success": False, "message": "Enter your username and password."}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, name, password_hash, COALESCE(role, 'student'), COALESCE(profile_picture, ''), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE), email, username, auth_version
        FROM users
        WHERE LOWER(email) = ? OR LOWER(username) = ?
        """,
        (identifier, identifier),
    )
    result = cursor.fetchone()
    if not result:
        conn.close()
        return JSONResponse({"success": False, "message": "Account not found"})

    user_id, name, stored_hash, role, profile_picture, is_active, must_change_password, email, username, auth_version = result

    if not is_active:
        conn.close()
        return JSONResponse({"success": False, "message": "This account has been deactivated."})

    is_admin = role in (ROLE_ADMIN, ROLE_SEMI_ADMIN)
    try:
        if is_admin:
            failures, level, locked_until = lock_security_row(cursor, user_id)
            now = utc_now()
            if locked_until and locked_until > now:
                return _lockout_response(math.ceil((locked_until - now).total_seconds()))
            # Re-read after acquiring the account lock in case recovery changed the password.
            cursor.execute("SELECT password_hash, auth_version FROM users WHERE id = ?", (user_id,))
            stored_hash, auth_version = cursor.fetchone()
        if not _check_password(stored_hash, password):
            retry_after = record_failure(cursor, user_id, failures, level, utc_now()) if is_admin else 0
            conn.commit()
            if retry_after:
                return _lockout_response(retry_after)
            message = f"Incorrect password. {3 - (failures + 1)} attempt(s) remaining before cooldown." if is_admin else "Incorrect Password"
            return JSONResponse({"success": False, "message": message})
        if is_admin:
            clear_lockout(cursor, user_id)
        cursor.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    sess = req.session
    sess.clear()
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email or ""
    sess["username"] = username or ""
    sess["role"] = role
    sess["must_change_password"] = bool(must_change_password)
    sess["auth_version"] = auth_version
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or username or email
        sess["admin_ip"] = req.client.host if req.client else ""
    _set_session_profile_image(sess, profile_picture)
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "login", target_label=username or email or "")
    else:
        _record_student_activity(req, "login", "Email and password")

    resp = {"success": True}
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        resp["admin"] = True
    if role == ROLE_SEMI_ADMIN:
        resp["semi_admin"] = True
        resp["must_change_password"] = bool(must_change_password)
    return JSONResponse(resp)


def _lockout_response(seconds):
    return JSONResponse(
        {"success": False, "code": "admin_locked", "retry_after": seconds,
         "message": f"Too many failed attempts. Try again in {seconds} seconds."},
        status_code=429,
        headers={"Retry-After": str(seconds)},
    )


def _mail_unavailable(*, not_configured=False):
    return JSONResponse(
        {"success": False,
         "code": "email_not_configured" if not_configured else "email_delivery_failed",
         "message": (
             "Recovery email is not configured. The system owner must set BREVO_API_KEY and EMAIL_SENDER "
             "(or SMTP_USERNAME and SMTP_PASSWORD, a Google App Password), and APP_BASE_URL, then restart the app."
             if not_configured else
             "Recovery email could not be sent. Please contact the system administrator or try again later."
         )},
        status_code=503,
    )


def _mail_delivery_error(exc):
    if isinstance(exc, EmailApiError):
        if exc.status_code in (401, 403):
            code = "email_api_unauthorized"
            message = (
                "Brevo rejected the request. Check that BREVO_API_KEY is a current Brevo API key "
                "and that EMAIL_SENDER is a verified sender in Brevo."
            )
        elif exc.status_code == 400:
            code = "email_api_rejected"
            message = (
                "Brevo did not accept the email. Confirm that EMAIL_SENDER is a verified sender "
                "in Brevo and that the recipient address is valid."
            )
        elif exc.status_code == 0:
            code = "email_api_unreachable"
            message = "Could not reach the Brevo email service. Please try again in a moment."
        else:
            code = "email_delivery_failed"
            message = "The email service could not send the message right now. Please try again later."
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        code = "smtp_authentication_failed"
        message = (
            "Google rejected the sender login. Check that SMTP_USERNAME is the Gmail account "
            "that generated SMTP_PASSWORD, and use that account's current 16-character Google App Password."
        )
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        code = "smtp_connection_closed"
        message = (
            "Google closed the SMTP connection during delivery. Verify the sender's Google App Password "
            "and check that the network allows outgoing Gmail SMTP on port 587."
        )
    elif isinstance(exc, (OSError, TimeoutError)):
        code = "smtp_connection_failed"
        message = (
            "Could not reach Gmail SMTP. Check the internet connection and confirm the network allows "
            "outgoing Gmail SMTP on port 587."
        )
    else:
        code = "email_delivery_failed"
        message = "Recovery email delivery failed. Please try again or contact the system administrator."
    return JSONResponse({"success": False, "code": code, "message": message}, status_code=503)


@fastapi_route("/admin/recovery-email", methods=["GET"])
def admin_recovery_email(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "message": "Admin session required."}, status_code=401)
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT recovery_email, pending_email FROM admin_account_security WHERE user_id = ?", (req.session["user_id"],))
        row = cursor.fetchone()
        return JSONResponse({"success": True, "recovery_email": row[0] if row else None, "pending_email": row[1] if row else None})
    finally:
        conn.close()


@fastapi_route("/admin/recovery-email", methods=["POST"])
async def admin_set_recovery_email(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "message": "Admin session required."}, status_code=401)
    data = await req.json()
    email = str(data.get("email") or "").strip().lower()
    password = data.get("current_password")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.]*[a-z0-9])?@gmail\.com", email) or len(email) > 254:
        return JSONResponse({"success": False, "message": "Enter a valid @gmail.com recovery address."}, status_code=400)
    if not isinstance(password, str) or not password:
        return JSONResponse({"success": False, "message": "Enter your current password to add or change recovery Gmail."}, status_code=400)
    try:
        mail_configuration()
    except ValueError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return _mail_unavailable(not_configured=True)
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        user_id = req.session["user_id"]
        failures, level, locked_until = lock_security_row(cursor, user_id)
        now = utc_now()
        if locked_until and locked_until > now:
            return _lockout_response(math.ceil((locked_until - now).total_seconds()))
        cursor.execute("SELECT password_hash, auth_version FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or row[1] != req.session.get("auth_version", 0):
            return JSONResponse({"success": False, "message": "Please sign in again."}, status_code=401)
        if not _check_password(row[0], password):
            retry_after = record_failure(cursor, user_id, failures, level, now)
            conn.commit()
            return _lockout_response(retry_after) if retry_after else JSONResponse(
                {"success": False, "message": "Current password is incorrect."}, status_code=400,
            )
        cursor.execute("SELECT verification_sent_at FROM admin_account_security WHERE user_id = ?", (user_id,))
        sent_at = cursor.fetchone()[0]
        if sent_at and (now - sent_at).total_seconds() < 60:
            return JSONResponse({"success": False, "message": "Wait one minute before requesting another verification email."}, status_code=429)
        token, digest = new_token()
        await asyncio.to_thread(send_security_email, email, token, "verify")
        cursor.execute(
            """UPDATE admin_account_security SET pending_email = ?, verification_hash = ?,
               verification_expires_at = ?, verification_sent_at = ? WHERE user_id = ?""",
            (email, digest, now + timedelta(minutes=30), now, user_id),
        )
        conn.commit()
        return JSONResponse({"success": True, "message": "Verification link sent. Open it within 30 minutes to confirm your recovery Gmail."})
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        logging.getLogger(__name__).error("Recovery verification email failed (%s %s).", type(exc).__name__, getattr(exc, "status_code", ""))
        return _mail_delivery_error(exc)
    finally:
        conn.close()


@fastapi_route("/admin/forgot-password", methods=["POST"])
async def admin_forgot_password(req):
    data = await req.json()
    identifier = str(data.get("username") or "").strip().lower()
    if not identifier or len(identifier) > 320:
        return JSONResponse({"success": False, "message": "Enter your admin username, sign-in email, or verified recovery Gmail."}, status_code=400)
    try:
        mail_configuration()
    except ValueError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return _mail_unavailable(not_configured=True)
    response = JSONResponse({
        "success": True,
        "message": "If an active admin account matches and has a verified recovery Gmail, a reset link will be sent. Check your inbox and spam folder. Allow one minute before requesting another link.",
    })
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        # Select the account before checking recovery eligibility so a login
        # identifier cannot fall through to another admin's recovery Gmail.
        cursor.execute(
            """SELECT s.user_id, s.recovery_email, s.reset_sent_at
               FROM admin_account_security s JOIN users u ON u.id = s.user_id
               WHERE s.user_id = (
                   SELECT candidate.id
                   FROM users candidate
                   LEFT JOIN admin_account_security recovery ON recovery.user_id = candidate.id
                   WHERE candidate.role IN (?, ?)
                     AND (LOWER(candidate.username) = ? OR LOWER(candidate.email) = ?
                          OR LOWER(recovery.recovery_email) = ?)
                   ORDER BY CASE WHEN LOWER(candidate.username) = ? OR LOWER(candidate.email) = ?
                                 THEN 0 ELSE 1 END, candidate.id
                   LIMIT 1
               )
                 AND u.is_active = TRUE AND s.recovery_email IS NOT NULL
               FOR UPDATE OF s""",
            (ROLE_ADMIN, ROLE_SEMI_ADMIN, identifier, identifier, identifier, identifier, identifier),
        )
        row = cursor.fetchone()
        now = utc_now()
        if not row:
            logging.getLogger(__name__).info("Password reset request did not match an active admin with verified recovery Gmail.")
            return response
        if row[2] and (now - row[2]).total_seconds() < 60:
            logging.getLogger(__name__).info("Password reset email request throttled.")
            return response
        token, digest = new_token()
        await asyncio.to_thread(send_security_email, row[1], token, "reset")
        cursor.execute(
            "UPDATE admin_account_security SET reset_hash = ?, reset_expires_at = ?, reset_sent_at = ? WHERE user_id = ?",
            (digest, now + timedelta(minutes=15), now, row[0]),
        )
        conn.commit()
        logging.getLogger(__name__).info("Password reset email accepted for delivery.")
        return response
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        logging.getLogger(__name__).error("Password reset email failed (%s %s).", type(exc).__name__, getattr(exc, "status_code", ""))
        return _mail_delivery_error(exc)
    finally:
        conn.close()


async def _complete_admin_recovery(req, verification):
    data = await req.json()
    token = data.get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return JSONResponse({"success": False, "message": "This link is invalid or expired. Request a new link."}, status_code=400)
    password = data.get("new_password")
    if not verification:
        error = _password_policy_error(password, admin=True) if isinstance(password, str) else "Enter a new password."
        if error or password != data.get("confirm_password"):
            return JSONResponse({"success": False, "message": error or "New passwords do not match."}, status_code=400)
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        hash_column = "verification_hash" if verification else "reset_hash"
        expiry_column = "verification_expires_at" if verification else "reset_expires_at"
        cursor.execute(
            f"""SELECT s.user_id, s.pending_email, s.{expiry_column}, u.password_hash, u.auth_version
                FROM admin_account_security s JOIN users u ON u.id = s.user_id
                WHERE s.{hash_column} = ? AND u.role IN (?, ?) AND u.is_active = TRUE
                FOR UPDATE OF s""",
            (token_hash(token), ROLE_ADMIN, ROLE_SEMI_ADMIN),
        )
        row = cursor.fetchone()
        if not row or not row[2] or row[2] <= utc_now() or (verification and not row[1]):
            return JSONResponse({"success": False, "message": "This link is invalid or expired. Request a new link."}, status_code=400)
        if verification:
            cursor.execute(
                """UPDATE admin_account_security SET recovery_email = pending_email, pending_email = NULL,
                   verification_hash = NULL, verification_expires_at = NULL,
                   reset_hash = NULL, reset_expires_at = NULL WHERE user_id = ?""",
                (row[0],),
            )
            message = "Recovery Gmail verified. You can now use Forgot password."
        else:
            if _check_password(row[3], password):
                return JSONResponse({"success": False, "message": "Choose a password different from your current password."}, status_code=400)
            cursor.execute(
                "UPDATE users SET password_hash = ?, must_change_password = FALSE, auth_version = auth_version + 1 WHERE id = ?",
                (_hash_password(password), row[0]),
            )
            clear_recovery_tokens(cursor, row[0])
            clear_lockout(cursor, row[0])
            message = "Password reset. Sign in with your new password."
        conn.commit()
        if not verification:
            req.session.clear()
            return JSONResponse({"success": True, "message": message})
        return_to_dashboard = (
            req.session.get("user_id") == row[0]
            and req.session.get("role") in (ROLE_ADMIN, ROLE_SEMI_ADMIN)
            and req.session.get("auth_version", 0) == row[4]
        )
        return JSONResponse({"success": True, "message": message, "return_to_dashboard": return_to_dashboard})
    except UniqueViolation:
        return JSONResponse({"success": False, "message": "That Gmail is already used by another admin. Add a different recovery Gmail in Account settings."}, status_code=400)
    finally:
        conn.close()


@fastapi_route("/admin/verify-recovery", methods=["POST"])
async def admin_verify_recovery(req):
    return await _complete_admin_recovery(req, verification=True)


@fastapi_route("/admin/reset-password", methods=["POST"])
async def admin_reset_password(req):
    return await _complete_admin_recovery(req, verification=False)


@fastapi_route("/logout", methods=["GET"])
def logout(req):
    _record_admin_activity(req.session, "logout")
    _record_student_activity(req, "logout")
    req.session.clear()
    return RedirectResponse("/", status_code=302)

# Define the route for admin login
@fastapi_route("/admin/login", methods=["GET", "POST"])
def admin_login(req):
    return RedirectResponse("/", status_code=302)

# Define the route for admin logout
@fastapi_route("/admin/logout", methods=["GET"])
def admin_logout(req):
    _record_admin_activity(req.session, "logout")
    req.session.clear()
    return RedirectResponse("/", status_code=302)


# Define the route for the admin home page
@fastapi_route("/admin", methods=["GET"])
def admin_home(req):
    if not _is_admin_session(req.session):
        return RedirectResponse("/", status_code=302)
    return _render(
        req,
        "AdminDashboard.html",
        can_manage_students=_is_full_admin_session(req.session),
        can_view_admin_data=True,
        system_settings=_get_system_settings(),
    )


# Define the route for the admin dashboard
@fastapi_route("/admin/dashboard", methods=["GET"])
def admin_dashboard(req):
    if not _is_admin_session(req.session, allow_password_change=True):
        return RedirectResponse("/", status_code=302)
    password_change_required = bool(req.session.get("must_change_password"))
    return _render(
        req,
        "AdminDashboard.html",
        can_manage_students=not password_change_required and _is_full_admin_session(req.session),
        can_view_admin_data=not password_change_required,
        password_change_required=password_change_required,
        system_settings=_get_system_settings(),
    )


@fastapi_route("/admin/force-password-change", methods=["GET"])
def admin_force_password_change(req):
    return RedirectResponse("/admin/dashboard" if req.session.get("user_id") else "/", status_code=303)


@fastapi_route("/system-settings/public", methods=["GET"])
def public_system_settings(req):
    settings = _get_system_settings()
    return JSONResponse({
        "success": True,
        "settings": {
            "university_name": settings["university_name"],
            "school_year": settings["school_year"],
            "available_strands": settings["available_strands"],
            "recommendation_limit": settings["recommendation_limit"],
        },
    })


# Define the route for fetching all users (admin only)
@fastapi_route("/admin/users", methods=["GET"])
def admin_get_users(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, profile_picture, COALESCE(role, 'student'), COALESCE(is_active, TRUE), last_login_at, username FROM users WHERE COALESCE(role, 'student') IN (?, ?) ORDER BY id DESC", (ROLE_ADMIN, ROLE_SEMI_ADMIN))
    rows = cursor.fetchall()
    conn.close()
    users = [{"id": r[0], "name": r[1], "email": r[2] or "", "profile_picture": r[3], "role": r[4], "is_active": bool(r[5]), "last_login_at": r[6].isoformat() if r[6] else "", "username": r[7] or ""} for r in rows]
    return JSONResponse({"users": users})


@fastapi_route("/admin/system-settings", methods=["GET"])
def admin_get_system_settings(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    return JSONResponse({"success": True, "settings": _get_system_settings()})


@fastapi_route("/admin/system-settings", methods=["POST"])
async def admin_save_system_settings(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        settings = _validate_system_settings(await req.json())
    except (ValueError, TypeError) as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO system_settings (settings_key, settings_json, updated_by, updated_at)
        VALUES ('global', ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (settings_key) DO UPDATE SET
            settings_json = EXCLUDED.settings_json,
            updated_by = EXCLUDED.updated_by,
            updated_at = CURRENT_TIMESTAMP
        """,
        (json.dumps(settings), req.session.get("user_id")),
    )
    conn.commit()
    conn.close()
    _record_admin_activity(req.session, "system_settings_updated", target_label="global system settings")
    return JSONResponse({"success": True, "settings": settings})


def _reset_course_catalog_cache():
    global _COURSE_TRAINING_DATA_CACHE, _COURSE_MODEL_CACHE
    _COURSE_TRAINING_DATA_CACHE = None
    _COURSE_MODEL_CACHE = None


def _validate_course_payload(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid course details.")
    course = " ".join(str(data.get("course") or "").split())
    description = " ".join(str(data.get("description") or "").split())
    if not 3 <= len(course) <= 200:
        raise ValueError("Course name must be 3 to 200 characters.")
    if not 10 <= len(description) <= 500:
        raise ValueError("Description must be 10 to 500 characters.")
    features = data.get("features")
    if not isinstance(features, list) or len(features) != len(CATEGORY_NAMES):
        raise ValueError("Enter a score for each of the six subject areas.")
    scores = []
    for name, value in zip(CATEGORY_NAMES, features):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"The {name} score must be a number from 0 to 100.")
        scores.append(round(float(value), 2))
    strands = data.get("strands")
    if not isinstance(strands, list) or not strands:
        raise ValueError("Select at least one academic cluster.")
    strands = sorted({str(code).upper() for code in strands})
    if any(code not in SYSTEM_SETTING_STRANDS for code in strands):
        raise ValueError("Select only supported academic clusters.")
    return course, description, scores, strands


def _parse_course_id(course_id):
    try:
        value = int(course_id)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _stored_strand_codes(cursor, codes):
    # Older databases store ICTECH as "ICTech"; match whatever spelling the check constraint allows.
    cursor.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'university_course_strands'::regclass AND contype = 'c'
        """
    )
    allowed = {}
    for row in cursor.fetchall() or []:
        for value in re.findall(r"'([^']+)'", str(row[0])):
            allowed.setdefault(value.upper(), value)
    return [allowed.get(code, code) for code in codes]


@fastapi_route("/admin/courses", methods=["GET"])
def admin_list_courses(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT courses.id, courses.course, courses.description, courses.features,
                   COALESCE(courses.is_active, TRUE),
                   COALESCE(
                       ARRAY_AGG(DISTINCT course_strands.strand_code)
                           FILTER (WHERE course_strands.strand_code IS NOT NULL),
                       ARRAY[]::TEXT[]
                   )
            FROM university_courses courses
            LEFT JOIN university_course_strands course_strands ON course_strands.course = courses.course
            GROUP BY courses.id, courses.course, courses.description, courses.features
            ORDER BY LOWER(courses.course)
            """
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    courses = [
        {
            "id": row[0],
            "course": row[1],
            "description": row[2] or "",
            "features": list(row[3] or []),
            "is_active": bool(row[4]),
            "strands": sorted(str(code).upper() for code in (row[5] or [])),
        }
        for row in rows
    ]
    return JSONResponse({"success": True, "courses": courses, "categories": CATEGORY_NAMES})


async def _save_course(req, course_id=None):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        course, description, scores, strands = _validate_course_payload(await req.json())
    except (ValueError, TypeError) as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    duplicate = JSONResponse({"success": False, "message": "A course with that name already exists."}, status_code=409)
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM university_courses WHERE LOWER(course) = LOWER(?) AND id <> ?",
            (course, course_id or 0),
        )
        if cursor.fetchone():
            return duplicate
        if course_id is None:
            cursor.execute(
                "INSERT INTO university_courses (course, features, description) VALUES (?, CAST(? AS JSONB), ?) RETURNING id",
                (course, json.dumps(scores), description),
            )
            course_id = cursor.fetchone()[0]
            action = "course_created"
        else:
            cursor.execute("SELECT course FROM university_courses WHERE id = ? FOR UPDATE", (course_id,))
            existing = cursor.fetchone()
            if not existing:
                return JSONResponse({"success": False, "message": "Course not found."}, status_code=404)
            # Cluster rows reference the course name without ON UPDATE CASCADE, so rewrite them around a rename.
            cursor.execute("DELETE FROM university_course_strands WHERE course = ?", (existing[0],))
            cursor.execute(
                "UPDATE university_courses SET course = ?, features = CAST(? AS JSONB), description = ? WHERE id = ?",
                (course, json.dumps(scores), description, course_id),
            )
            action = "course_updated"
        for code in _stored_strand_codes(cursor, strands):
            cursor.execute(
                "INSERT INTO university_course_strands (course, strand_code) VALUES (?, ?)",
                (course, code),
            )
        conn.commit()
    except UniqueViolation:
        conn.rollback()
        return duplicate
    finally:
        conn.close()
    _reset_course_catalog_cache()
    _record_admin_activity(req.session, action, target_label=course)
    return JSONResponse({
        "success": True,
        "course": {"id": course_id, "course": course, "description": description, "features": scores, "strands": strands},
    })


@fastapi_route("/admin/courses", methods=["POST"])
async def admin_create_course(req):
    return await _save_course(req)


async def admin_update_course(req, course_id):
    parsed_id = _parse_course_id(course_id)
    if parsed_id is None:
        return JSONResponse({"success": False, "message": "Course not found."}, status_code=404)
    return await _save_course(req, parsed_id)


def _set_course_active(req, course_id, is_active):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    parsed_id = _parse_course_id(course_id)
    if parsed_id is None:
        return JSONResponse({"success": False, "message": "Course not found."}, status_code=404)
    conn = _db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT course, COALESCE(is_active, TRUE) FROM university_courses WHERE id = ? FOR UPDATE", (parsed_id,))
        existing = cursor.fetchone()
        if not existing:
            return JSONResponse({"success": False, "message": "Course not found."}, status_code=404)
        if not is_active and existing[1]:
            cursor.execute("SELECT COUNT(*) FROM university_courses WHERE COALESCE(is_active, TRUE)")
            if cursor.fetchone()[0] <= 1:
                return JSONResponse({"success": False, "message": "Keep at least one active course in the recommendation catalog."}, status_code=400)
        cursor.execute("UPDATE university_courses SET is_active = ? WHERE id = ?", (is_active, parsed_id))
        conn.commit()
    finally:
        conn.close()
    _reset_course_catalog_cache()
    action = "course_reactivated" if is_active else "course_deactivated"
    _record_admin_activity(req.session, action, target_label=existing[0])
    return JSONResponse({"success": True, "is_active": is_active})


def admin_deactivate_course(req, course_id):
    return _set_course_active(req, course_id, False)


def admin_activate_course(req, course_id):
    return _set_course_active(req, course_id, True)


ADMIN_ACTIVITY_LIMIT = 1000
STUDENT_ACTIVITY_LABELS = {
    "login": "Signed in",
    "logout": "Signed out",
    "account_registered": "Created an account",
    "report_card_uploaded": "Uploaded a report card",
    "report_card_upload_failed": "Report card upload failed",
    "grades_saved": "Saved grades and profile",
    "recommendations_generated": "Generated course recommendations",
    "results_emailed": "Emailed course recommendations",
    "account_name_changed": "Changed account name",
    "profile_picture_changed": "Changed profile picture",
}
ADMIN_ACTIVITY_EXPORT_COLUMNS = (
    ("When", "when", 22),
    ("User", "user", 21),
    ("Email", "email", 26),
    ("Role", "role", 9),
    ("Action", "action", 24),
    ("Target", "target", 22),
    ("Session IP", "session_ip", 16),
    ("Details", "details", 40),
)
PATHFINDER_SCHOOL_NAME = "University of Perpetual Help System Dalta"


def _maroon_pathfinder_logo():
    logo_path = os.path.join(os.path.dirname(__file__), "static", "images", "PathFinder2.png")
    with PILImage.open(logo_path) as source:
        alpha = source.convert("RGBA").getchannel("A")
        tinted = PILImage.new("RGBA", source.size, (128, 0, 0, 0))
        tinted.putalpha(alpha)
        logo_bytes = BytesIO()
        tinted.save(logo_bytes, format="PNG")
    logo_bytes.seek(0)
    return logo_bytes


def _fetch_admin_activity():
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT id, actor_name, actor_email, action, target_label, details, session_ip, created_at,
               COALESCE(actor_role, 'admin')
        FROM admin_activity_logs
        ORDER BY created_at DESC, id DESC
        LIMIT {ADMIN_ACTIVITY_LIMIT}
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row[0], "actor_name": row[1] or ("Unknown student" if row[8] == "student" else "Unknown admin"),
            "actor_email": row[2] or "", "action": row[3],
            "target_label": row[4] or "", "details": row[5] or "",
            "session_ip": row[6] or "", "created_at": row[7],
            "actor_role": row[8],
        }
        for row in rows
    ]


def _admin_activity_export_row(item):
    created_at = item["created_at"]
    action = str(item["action"] or "")
    if item["actor_role"] == "student" and action in STUDENT_ACTIVITY_LABELS:
        action_label = STUDENT_ACTIVITY_LABELS[action]
    else:
        action_label = action.replace("_", " ").capitalize()
    return {
        "when": _app_local(created_at).strftime("%Y-%m-%d %I:%M:%S %p") if isinstance(created_at, datetime) else str(created_at or ""),
        "user": item["actor_name"],
        "email": item["actor_email"],
        "role": "Student" if item["actor_role"] == "student" else "Admin",
        "action": action_label,
        "target": item["target_label"] or "—",
        "session_ip": item["session_ip"] or "—",
        "details": item["details"] or "—",
    }


def _admin_activity_pdf(rows, filter_summary, generated):
    stream = BytesIO()
    page_size = (A4[1], A4[0])
    document = SimpleDocTemplate(
        stream, pagesize=page_size, leftMargin=30, rightMargin=30, topMargin=104,
        bottomMargin=40, title="Activity Log", author="PathFinder",
    )
    maroon = colors.HexColor("#800000")
    text_color = colors.HexColor("#262122")
    muted = colors.HexColor("#766e70")
    line_color = colors.HexColor("#e4dddd")
    label_style = ParagraphStyle("Activity label", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=maroon)
    body_style = ParagraphStyle("Activity body", fontName="Helvetica", fontSize=7.5, leading=10, textColor=text_color, wordWrap="CJK")
    muted_style = ParagraphStyle("Activity muted", fontName="Helvetica", fontSize=8, leading=11, textColor=muted)
    proportions = [0.11, 0.12, 0.15, 0.06, 0.14, 0.14, 0.09, 0.19]
    column_widths = [document.width * fraction for fraction in proportions]
    table_rows = [[Paragraph(label, label_style) for label, _, _ in ADMIN_ACTIVITY_EXPORT_COLUMNS]]
    table_rows.extend(
        [Paragraph(html.escape(str(row[key])), body_style) for _, key, _ in ADMIN_ACTIVITY_EXPORT_COLUMNS]
        for row in rows
    )
    if not rows:
        table_rows.append([Paragraph("No activity matches the selected filters.", muted_style)] + [""] * (len(ADMIN_ACTIVITY_EXPORT_COLUMNS) - 1))
    table = Table(table_rows, colWidths=column_widths, repeatRows=1, hAlign="LEFT")
    table_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5e9e9")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf8f8")]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, maroon),
        ("BOX", (0, 0), (-1, -1), 0.4, line_color),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if not rows:
        table_styles.append(("SPAN", (0, 1), (-1, 1)))
    table.setStyle(TableStyle(table_styles))
    generated_label = generated.strftime("%b %d, %Y %I:%M %p")

    def draw_page_frame(canvas, doc):
        canvas.saveState()
        left = doc.leftMargin
        right = left + doc.width
        top = page_size[1]
        canvas.setFillColor(maroon)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(left, top - 40, "PathFinder")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        canvas.drawRightString(right, top - 40, f"Generated {generated_label}")
        canvas.setFont("Helvetica-Bold", 16)
        canvas.setFillColor(text_color)
        canvas.drawString(left, top - 64, "Activity Log")
        canvas.setStrokeColor(maroon)
        canvas.line(left, top - 74, right, top - 74)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        canvas.drawString(left, top - 88, f"{filter_summary}  |  {len(rows)} {'entry' if len(rows) == 1 else 'entries'}")
        canvas.drawRightString(right, 22, f"Page {doc.page}")
        canvas.restoreState()

    document.build([table], onFirstPage=draw_page_frame, onLaterPages=draw_page_frame)
    return stream.getvalue()


def _admin_activity_xlsx(rows, filter_summary, generated):
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as SpreadsheetImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.units import pixels_to_EMU
    from openpyxl.worksheet.page import PageMargins
    from openpyxl.worksheet.properties import PageSetupProperties

    workbook = Workbook()
    workbook.properties.creator = "PathFinder"
    workbook.properties.title = "Activity Log"
    sheet = workbook.active
    sheet.title = "Activity Log"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    last_column = get_column_letter(len(ADMIN_ACTIVITY_EXPORT_COLUMNS))
    for index, (_, _, width) in enumerate(ADMIN_ACTIVITY_EXPORT_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    logo = SpreadsheetImage(_maroon_pathfinder_logo())
    logo.width = logo.height = 60
    logo.anchor = OneCellAnchor(
        _from=AnchorMarker(col=0, row=0, colOff=pixels_to_EMU(75), rowOff=pixels_to_EMU(1)),
        ext=XDRPositiveSize2D(cx=pixels_to_EMU(logo.width), cy=pixels_to_EMU(logo.height)),
    )
    sheet.add_image(logo)
    sheet.merge_cells(f"B1:{last_column}1")
    sheet.merge_cells(f"B2:{last_column}2")
    sheet["B1"] = "PathFinder"
    sheet["B1"].font = Font(name="Calibri", bold=True, size=20, color="FF800000")
    sheet["B1"].alignment = Alignment(vertical="center")
    sheet["B2"] = PATHFINDER_SCHOOL_NAME
    sheet["B2"].font = Font(name="Calibri", bold=True, size=11, color="FF262122")
    sheet["B2"].alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 25
    sheet.row_dimensions[2].height = 21
    sheet.row_dimensions[3].height = 3
    for cell in sheet[3]:
        cell.fill = PatternFill("solid", fgColor="FF800000")

    sheet.merge_cells("A4:F4")
    sheet.merge_cells(f"G4:{last_column}4")
    sheet["A4"] = "Activity Log"
    sheet["A4"].font = Font(name="Calibri", bold=True, size=16, color="FF262122")
    sheet["A4"].alignment = Alignment(vertical="center")
    sheet["G4"] = f"Generated {generated:%b %d, %Y %I:%M %p}"
    sheet["G4"].font = Font(name="Calibri", size=11, color="FF766E70")
    sheet["G4"].alignment = Alignment(horizontal="right", vertical="center")
    sheet.row_dimensions[4].height = 28

    sheet.merge_cells(f"A5:{last_column}5")
    filter_text = f"Filters: {filter_summary}"
    sheet["A5"] = filter_text
    sheet["A5"].font = Font(name="Calibri", size=11, color="FF766E70")
    sheet["A5"].alignment = Alignment(vertical="center", wrap_text=True)
    filter_lines = sum(max(1, math.ceil(len(line) / 150)) for line in filter_text.split("\n"))
    sheet.row_dimensions[5].height = min(200, 8 + 14 * filter_lines)
    sheet.merge_cells(f"A6:{last_column}6")
    sheet["A6"] = f"{len(rows)} {'entry' if len(rows) == 1 else 'entries'}"
    sheet["A6"].font = Font(name="Calibri", bold=True, size=11, color="FF766E70")
    sheet["A6"].alignment = Alignment(vertical="center")
    sheet.row_dimensions[6].height = 18
    sheet.row_dimensions[7].height = 8

    header_row = 8
    for index, (label, _, _) in enumerate(ADMIN_ACTIVITY_EXPORT_COLUMNS, start=1):
        sheet.cell(row=header_row, column=index, value=label)
    for cell in sheet[header_row]:
        cell.font = Font(name="Calibri", bold=True, size=11, color="FFFFFFFF")
        cell.fill = PatternFill("solid", fgColor="FF800000")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[header_row].height = 24

    body_font = Font(name="Calibri", size=11, color="FF262122")
    body_alignment = Alignment(vertical="top", wrap_text=True)
    stripe_fill = PatternFill("solid", fgColor="FFFAF8F8")
    row_border = Border(bottom=Side(style="thin", color="FFE4DDDD"))
    for row_number, row in enumerate(rows, start=header_row + 1):
        for column_number, (_, key, _) in enumerate(ADMIN_ACTIVITY_EXPORT_COLUMNS, start=1):
            cell = sheet.cell(row=row_number, column=column_number, value=row[key])
            # Log text is user-controlled; keep values like "=..." as inert text, never formulas.
            if cell.data_type == "f":
                cell.data_type = "s"
            cell.font = body_font
            cell.alignment = body_alignment
            cell.border = row_border
            if row_number % 2 == 0:
                cell.fill = stripe_fill
    if not rows:
        sheet.merge_cells(f"A{header_row + 1}:{last_column}{header_row + 1}")
        sheet.cell(row=header_row + 1, column=1, value="No activity matches the selected filters.")
        sheet.cell(row=header_row + 1, column=1).font = Font(name="Calibri", italic=True, size=11, color="FF766E70")
        sheet.row_dimensions[header_row + 1].height = 22
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    if rows:
        sheet.auto_filter.ref = f"A{header_row}:{last_column}{header_row + len(rows)}"
    sheet.print_title_rows = f"1:{header_row}"
    sheet.print_area = f"A1:{last_column}{header_row + max(1, len(rows))}"
    sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_margins = PageMargins(left=0.3, right=0.3, top=0.4, bottom=0.4, header=0.15, footer=0.2)
    sheet.oddFooter.left.text = f"PathFinder | {PATHFINDER_SCHOOL_NAME}"
    sheet.oddFooter.right.text = "Page &P of &N"
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


@fastapi_route("/admin/activity", methods=["GET"])
def admin_get_activity(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    return JSONResponse({"success": True, "activity": [
        {**item, "created_at": item["created_at"].isoformat() if item["created_at"] else ""}
        for item in _fetch_admin_activity()
    ]})


@fastapi_route("/admin/activity/export", methods=["POST"])
async def admin_export_activity(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        payload = await req.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse({"success": False, "message": "Invalid export request."}, status_code=400)
    export_format = payload.get("format")
    ids = payload.get("ids")
    if export_format not in ("pdf", "xlsx"):
        return JSONResponse({"success": False, "message": "Choose PDF or Excel."}, status_code=400)
    if (
        not isinstance(ids, list) or len(ids) > ADMIN_ACTIVITY_LIMIT
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in ids)
    ):
        return JSONResponse({"success": False, "message": "Invalid activity selection."}, status_code=400)
    filter_summary = str(payload.get("filter_summary") or "All activity")[:300]

    selected = set(ids)
    rows = [_admin_activity_export_row(item) for item in _fetch_admin_activity() if item["id"] in selected]
    generated = _app_now()
    filename = f"pathfinder-activity-log-{generated:%Y-%m-%d}.{export_format}"
    if export_format == "pdf":
        body, media_type = _admin_activity_pdf(rows, filter_summary, generated), "application/pdf"
    else:
        body = _admin_activity_xlsx(rows, filter_summary, generated)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


# Define the route for updating a user (admin only)
@fastapi_route("/admin/update_user", methods=["POST"])
async def admin_update_user(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    uid = data.get("id")
    name = data.get("name")
    email = data.get("email")
    password = data.get("password")
    if not uid:
        return JSONResponse({"success": False, "message": "Missing id"})
    if password:
        password_error = _password_policy_error(password, admin=True)
        if password_error:
            return JSONResponse({"success": False, "message": password_error}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COALESCE(role, 'student') FROM users WHERE id = ?", (uid,))
        account = cursor.fetchone()
        if not account or account[0] not in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
            conn.close()
            return JSONResponse({"success": False, "message": "Admin account not found."}, status_code=404)
        if password:
            lock_security_row(cursor, uid)
            hashed = _hash_password(password)
            cursor.execute('UPDATE users SET name = ?, email = ?, password_hash = ?, must_change_password = ?, auth_version = auth_version + 1 WHERE id = ?', (name, email, hashed, account[0] == ROLE_SEMI_ADMIN, uid))
            clear_recovery_tokens(cursor, uid)
            clear_lockout(cursor, uid)
        else:
            cursor.execute('UPDATE users SET name = ?, email = ? WHERE id = ?', (name, email, uid))
        conn.commit()
    except UniqueViolation:
        conn.close()
        return JSONResponse({"success": False, "message": "Email already exists"})
    conn.close()
    _record_admin_activity(req.session, "admin_account_updated", uid, email or "")
    return JSONResponse({"success": True})


# Define the route for deleting a user (admin only)
@fastapi_route("/admin/delete_user", methods=["POST"])
async def admin_delete_user(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    uid = data.get("id")
    if not uid:
        return JSONResponse({"success": False, "message": "Missing id"})

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT email, profile_picture, COALESCE(role, 'student'), name FROM users WHERE id = ?", (uid,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "message": "User not found"})

        email = (row[0] or "").strip()
        profile_picture = (row[1] or "").strip()
        target_role = row[2]
        target_name = row[3] or email

        if int(uid) == int(req.session.get("user_id")):
            conn.close()
            return JSONResponse({"success": False, "message": "You cannot remove your own account."}, status_code=400)
        if target_role == ROLE_SUPER_ADMIN:
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = ? AND COALESCE(is_active, TRUE)", (ROLE_SUPER_ADMIN,))
            if cursor.fetchone()[0] <= 1:
                conn.close()
                return JSONResponse({"success": False, "message": "Cannot remove the last active admin."}, status_code=400)

        cursor.execute("DELETE FROM student_profiles WHERE user_id = ?", (uid,))
        cursor.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        return JSONResponse({"success": False, "message": "Delete failed"})
    conn.close()

    _record_admin_activity(req.session, "admin_account_removed", uid, target_name, f"role={target_role}")

    lowered = profile_picture.lower()
    if profile_picture and not lowered.startswith("http://") and not lowered.startswith("https://") and lowered not in ("default.jpg", "default.png", "default.svg"):
        image_path = os.path.join(UPLOAD_FOLDER, profile_picture)
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError:
                pass

    return JSONResponse({"success": True})


@fastapi_route("/admin/remove_admin_account", methods=["POST"])
async def admin_remove_admin_account(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    try:
        user_id = int(data.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid admin account id."}, status_code=400)
    if user_id == int(req.session.get("user_id")):
        return JSONResponse({"success": False, "message": "You cannot remove your own account."}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT email, profile_picture, COALESCE(role, 'student'), name FROM users WHERE id = ?",
        (user_id,),
    )
    account = cursor.fetchone()
    if not account or account[2] not in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        conn.close()
        return JSONResponse({"success": False, "message": "Admin account not found."}, status_code=404)

    email = (account[0] or "").strip()
    profile_picture = (account[1] or "").strip()
    role = account[2]
    target_name = account[3] or email
    if role == ROLE_SUPER_ADMIN:
        cursor.execute(
            "SELECT COUNT(*) FROM users WHERE role = ? AND COALESCE(is_active, TRUE)",
            (ROLE_SUPER_ADMIN,),
        )
        if cursor.fetchone()[0] <= 1:
            conn.close()
            return JSONResponse({"success": False, "message": "Cannot remove the last active admin."}, status_code=400)

    try:
        cursor.execute("DELETE FROM student_profiles WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        return JSONResponse({"success": False, "message": "Admin account removal failed."}, status_code=500)
    conn.close()

    _record_admin_activity(req.session, "admin_account_removed", user_id, target_name, f"role={role}")
    lowered = profile_picture.lower()
    if profile_picture and not lowered.startswith(("http://", "https://")) and lowered not in ("default.jpg", "default.png", "default.svg"):
        image_path = os.path.abspath(os.path.join(UPLOAD_FOLDER, os.path.basename(profile_picture)))
        upload_root = os.path.abspath(UPLOAD_FOLDER)
        if os.path.commonpath((upload_root, image_path)) == upload_root and os.path.isfile(image_path):
            try:
                os.remove(image_path)
            except OSError:
                pass
    return JSONResponse({"success": True, "message": "Admin account removed."})


# Define the route for creating a coordinator (admin only)
@fastapi_route("/admin/create_coordinator", methods=["POST"])
async def admin_create_coordinator(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    password = str(data.get("password") or "")

    if not name or not email or not password:
        return JSONResponse({"success": False, "message": "Name, email, and password are required"}, status_code=400)

    password_error = _password_policy_error(password, admin=True)
    if password_error:
        return JSONResponse({"success": False, "message": password_error}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        hashed = _hash_password(password)
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, profile_picture, role, must_change_password)
            VALUES (?, ?, ?, ?, ?, TRUE)
            """,
            (name, email, hashed, "default.svg", ROLE_SEMI_ADMIN),
        )
        conn.commit()
    except UniqueViolation:
        conn.close()
        return JSONResponse({"success": False, "message": "Email already exists"}, status_code=409)

    conn.close()
    _record_admin_activity(req.session, "admin_account_created", target_label=email, details=f"role={ROLE_SEMI_ADMIN}")
    return JSONResponse({"success": True, "message": "Level coordinator account created"})


@fastapi_route("/admin/create_admin_user", methods=["POST"])
async def admin_create_user(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    name = " ".join(str(data.get("name") or "").split())
    username = str(data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    role = str(data.get("role") or ROLE_SEMI_ADMIN).strip()
    if not name or not username or not password:
        return JSONResponse({"success": False, "message": "Name, username, and password are required."}, status_code=400)
    if not re.fullmatch(r"[a-z][a-z0-9._-]{2,31}", username):
        return JSONResponse({"success": False, "message": "Username must start with a letter and contain 3-32 letters, numbers, dots, underscores, or hyphens."}, status_code=400)
    if role not in (ROLE_SUPER_ADMIN, ROLE_SEMI_ADMIN):
        return JSONResponse({"success": False, "message": "Choose Admin or Sub Admin."}, status_code=400)
    password_error = _password_policy_error(password, admin=True)
    if password_error:
        return JSONResponse({"success": False, "message": password_error}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (name, username, password_hash, profile_picture, role, is_active, must_change_password) VALUES (?, ?, ?, ?, ?, TRUE, ?) RETURNING id",
            (name, username, _hash_password(password), "default.svg", role, role == ROLE_SEMI_ADMIN),
        )
        created = cursor.fetchone()
        conn.commit()
    except UniqueViolation:
        conn.close()
        return JSONResponse({"success": False, "message": "Username already exists."}, status_code=409)
    conn.close()
    _record_admin_activity(req.session, "admin_account_created", created[0], username, f"role={role}")
    return JSONResponse({"success": True, "message": f"{role.replace('_', ' ').title()} account created."})


@fastapi_route("/admin/update_admin_role", methods=["POST"])
async def admin_update_admin_role(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    try:
        user_id = int(data.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid admin account id."}, status_code=400)
    role = str(data.get("role") or "").strip()
    if role not in (ROLE_SUPER_ADMIN, ROLE_SEMI_ADMIN):
        return JSONResponse({"success": False, "message": "Choose Admin or Sub Admin."}, status_code=400)
    if user_id == int(req.session["user_id"]) and role != ROLE_SUPER_ADMIN:
        return JSONResponse({"success": False, "message": "You cannot remove your own admin access."}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name, email, COALESCE(role, 'student') FROM users WHERE id = ?", (user_id,))
    account = cursor.fetchone()
    if not account or account[2] not in (ROLE_SUPER_ADMIN, ROLE_SEMI_ADMIN):
        conn.close()
        return JSONResponse({"success": False, "message": "Admin account not found."}, status_code=404)
    old_role = account[2]
    if old_role == ROLE_SUPER_ADMIN and role != ROLE_SUPER_ADMIN:
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = ? AND COALESCE(is_active, TRUE)", (ROLE_SUPER_ADMIN,))
        if cursor.fetchone()[0] <= 1:
            conn.close()
            return JSONResponse({"success": False, "message": "At least one active admin must remain."}, status_code=400)
    cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    conn.close()
    _record_admin_activity(req.session, "admin_role_changed", user_id, account[1], f"{old_role} -> {role}")
    return JSONResponse({"success": True, "message": "Admin role updated."})

# Define the route for fetching all student profiles (admin only)
@fastapi_route("/admin/student_profiles", methods=["GET"])
def admin_get_student_profiles(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, strand
        FROM student_profiles
        ORDER BY id DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    profiles = []
    for r in rows:
        profiles.append(
            {
                "id": r[0],
                "student_number": r[1] or "",
                "first_name": r[2] or "",
                "middle_initial": r[3] or "",
                "last_name": r[4] or "",
                "course": r[5] or "",
                "gwa": r[6] or "",
                "grades": r[7] or "",
                "subjects": r[8] or "",
                "strand": r[9] or "",
            }
        )

    return JSONResponse({"success": True, "profiles": profiles})

# Define the route for updating a student profile (admin only)
@fastapi_route("/admin/update_student_profile", methods=["POST"])
async def admin_update_student_profile(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    sid = data.get("id")
    conn = _db_conn()
    cursor = conn.cursor()
    values = (
        (data.get("first_name") or "").strip(),
        (data.get("middle_initial") or "").strip(),
        (data.get("last_name") or "").strip(),
        (data.get("student_number") or "").strip(),
        (data.get("gwa") or "").strip(),
        (data.get("grades") or "").strip(),
        (data.get("subjects") or "").strip(),
        (data.get("strand") or "").strip(),
    )
    if sid:
        try:
            profile_id = int(sid)
        except (TypeError, ValueError):
            conn.close()
            return JSONResponse({"success": False, "message": "Invalid profile id"}, status_code=400)
        cursor.execute(
            """
            UPDATE student_profiles
            SET first_name = ?, middle_initial = ?, last_name = ?, student_number = ?, gwa = ?, grades = ?, subjects = ?, strand = ?
            WHERE id = ? AND user_id IN (SELECT id FROM users WHERE COALESCE(role, 'student') = 'student')
            RETURNING id
            """,
            (*values, profile_id),
        )
        updated = cursor.fetchone()
        if not updated:
            conn.close()
            return JSONResponse({"success": False, "message": "Student profile not found"}, status_code=404)
    else:
        try:
            student_user_id = int(data.get("user_id"))
        except (TypeError, ValueError):
            conn.close()
            return JSONResponse({"success": False, "message": "Missing student account id"}, status_code=400)
        cursor.execute(
            """
            INSERT INTO student_profiles
                (user_id, first_name, middle_initial, last_name, student_number, gwa, grades, subjects, strand, upload_date)
            SELECT id, ?, ?, ?, ?, ?, ?, ?, ?, ?
            FROM users
            WHERE id = ? AND COALESCE(role, 'student') = 'student'
            RETURNING id
            """,
            (*values, datetime.utcnow().isoformat(), student_user_id),
        )
        created = cursor.fetchone()
        if not created:
            conn.close()
            return JSONResponse({"success": False, "message": "Student account not found"}, status_code=404)
    conn.commit()
    conn.close()
    message = "Student profile updated" if sid else "Student profile created"
    _record_admin_activity(
        req.session,
        "student_profile_updated" if sid else "student_profile_created",
        data.get("user_id"),
        values[3] or f"profile:{sid or ''}",
    )
    return JSONResponse({"success": True, "message": message})

# Define the route for the admin students page (admin only)
@fastapi_route("/admin/students", methods=["GET"])
def admin_get_students(req):
    if not _is_admin_session(req.session):
        return RedirectResponse("/", status_code=302)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, student_number, first_name, middle_initial, last_name, strand FROM student_profiles ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    students = []
    for r in rows:
        students.append(
            {
                "id": r[0],
                "student_number": r[1],
                "first_name": r[2],
                "middle_initial": r[3],
                "last_name": r[4],
                "strand": r[5],
            }
        )
    return _render(
        req,
        "AdminDashboard.html",
        can_manage_students=_is_full_admin_session(req.session),
        can_view_admin_data=True,
        system_settings=_get_system_settings(),
    )


# Define the route for fetching all students via API (admin only)
@fastapi_route("/admin/students/api", methods=["GET"])
def admin_get_students_api(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT users.id, users.email, users.name, COALESCE(users.is_active, TRUE),
               profile.id, profile.student_number, profile.first_name,
             profile.middle_initial, profile.last_name, profile.gwa,
             profile.grades, profile.subjects,
               profile.recommendation, profile.strand, profile.upload_date,
               profile.report_card_upload_id
        FROM users
        LEFT JOIN LATERAL (
            SELECT id, student_number, first_name, middle_initial, last_name,
                     gwa, grades, subjects, recommendation, strand,
                   upload_date, report_card_upload_id
            FROM student_profiles
            WHERE user_id = users.id
            ORDER BY id DESC
            LIMIT 1
        ) profile ON TRUE
        WHERE COALESCE(users.role, 'student') = 'student'
        ORDER BY users.id DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()
    students = []
    recommendation_limit = _recommendation_limit()
    for row in rows:
        profile_name = " ".join(part for part in (
            row[6] or "",
            f"{row[7]}" if row[7] else "",
            row[8] or "",
        ) if part).strip()
        recommendation_value = _limit_recommendations(row[12], recommendation_limit)
        students.append({
            "id": row[0],
            "email": row[1] or "",
            "account_name": row[2] or "",
            "is_active": bool(row[3]),
            "profile_id": row[4],
            "student_number": row[5] or "",
            "first_name": row[6] or "",
            "middle_initial": row[7] or "",
            "last_name": row[8] or "",
            "student_name": profile_name or row[2] or "Unnamed student",
            "gwa": row[9] or "",
            "grades": row[10] or "",
            "subjects": row[11] or "",
            "recommendation": recommendation_value,
            "strand": row[13] or "",
            "upload_date": row[14] or "",
            "report_card_upload_id": row[15],
        })
    return JSONResponse({"success": True, "students": students})


@fastapi_route("/admin/students/{user_id}", methods=["GET"])
def admin_get_student_detail(req, user_id: str):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)

    try:
        requested_user_id = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid student id"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT users.id, users.email, users.name, COALESCE(users.is_active, TRUE),
               COALESCE(users.role, 'student'), profile.id, profile.student_number,
               profile.first_name, profile.middle_initial, profile.last_name,
               profile.gwa, profile.grades, profile.subjects,
               profile.recommendation, profile.strand, profile.upload_date,
               profile.report_card_upload_id
        FROM users
        LEFT JOIN LATERAL (
            SELECT id, student_number, first_name, middle_initial, last_name,
                     gwa, grades, subjects, recommendation, strand,
                   upload_date, report_card_upload_id
            FROM student_profiles
            WHERE user_id = users.id
            ORDER BY id DESC
            LIMIT 1
        ) profile ON TRUE
        WHERE users.id = ? AND COALESCE(users.role, 'student') = 'student'
        """,
        (requested_user_id,),
    )
    row = cursor.fetchone()
    report_card_upload_ids = []
    if row and row[16]:
        cursor.execute(
            """
            SELECT uploads.id
            FROM report_card_uploads linked
            JOIN report_card_uploads uploads
              ON uploads.user_id = linked.user_id
             AND (uploads.id = linked.id
                  OR (linked.upload_batch IS NOT NULL AND uploads.upload_batch = linked.upload_batch))
            WHERE linked.id = ?
            ORDER BY uploads.id
            """,
            (row[16],),
        )
        report_card_upload_ids = [item[0] for item in cursor.fetchall()]
    conn.close()
    if not row:
        return JSONResponse({"success": False, "message": "Student account not found"}, status_code=404)

    profile_name = " ".join(part for part in (
        row[7] or "",
        row[8] or "",
        row[9] or "",
    ) if part).strip()
    _record_admin_activity(
        req.session,
        "student_profile_viewed",
        requested_user_id,
        row[6] or profile_name or row[2] or f"student:{requested_user_id}",
        f"profile_id={row[5]}" if row[5] else "no profile saved",
    )
    return JSONResponse({
        "success": True,
        "student": {
            "id": row[0],
            "email": row[1] or "",
            "account_name": row[2] or "",
            "is_active": bool(row[3]),
            "role": row[4],
            "profile_id": row[5],
            "student_number": row[6] or "",
            "first_name": row[7] or "",
            "middle_initial": row[8] or "",
            "last_name": row[9] or "",
            "student_name": profile_name or row[2] or "Unnamed student",
            "gwa": row[10] or "",
            "grades": row[11] or "",
            "subjects": row[12] or "",
            "recommendation": _limit_recommendations(row[13]),
            "strand": row[14] or "",
            "upload_date": row[15] or "",
            "report_card_upload_id": row[16],
            "report_card_upload_ids": report_card_upload_ids,
        },
    })


@fastapi_route("/admin/deactivate_student", methods=["POST"])
async def admin_deactivate_student(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    try:
        user_id = int(data.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid student id"}, status_code=400)
    is_active = data.get("is_active") is True
    if user_id == req.session.get("user_id"):
        return JSONResponse({"success": False, "message": "You cannot deactivate your own account."}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_active = ? WHERE id = ? AND COALESCE(role, 'student') = 'student' RETURNING id",
        (is_active, user_id),
    )
    updated = cursor.fetchone()
    conn.commit()
    conn.close()
    if not updated:
        return JSONResponse({"success": False, "message": "Student account not found"}, status_code=404)
    message = "Student account reactivated." if is_active else "Student account deactivated."
    _record_admin_activity(req.session, "student_account_reactivated" if is_active else "student_account_deactivated", user_id, f"student:{user_id}")
    return JSONResponse({"success": True, "is_active": is_active, "message": message})


# Define the route for deleting a student (admin only)
@fastapi_route("/admin/delete_student", methods=["POST"])
async def admin_delete_student(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    sid = data.get("id")
    if not sid:
        return JSONResponse({"success": False, "message": "Missing id"})

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM student_profiles WHERE id = ? RETURNING user_id, student_number", (sid,))
    deleted = cursor.fetchone()
    conn.commit()
    conn.close()
    if not deleted:
        return JSONResponse({"success": False, "message": "Student profile not found"}, status_code=404)
    _record_admin_activity(req.session, "student_profile_removed", deleted[0], deleted[1] or f"profile:{sid}")
    return JSONResponse({"success": True})


# Define the route for fetching admin statistics (admin only)
@fastapi_route("/admin/stats", methods=["GET"])
def admin_stats(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)

    conn = _db_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT course, COUNT(*) FROM student_profiles GROUP BY course")
    course_counts = {row[0] or "Unknown": row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) FROM student_profiles WHERE subjects IS NOT NULL AND subjects != ''")
    total_report_cards = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COUNT(DISTINCT profile.user_id)
        FROM student_profiles profile
        JOIN users ON users.id = profile.user_id
        WHERE profile.subjects IS NOT NULL AND profile.subjects != ''
            AND COALESCE(users.role, 'student') = 'student'
        """
    )
    students_who_uploaded_reports = cursor.fetchone()[0]

    cursor.execute("SELECT recommendation FROM student_profiles WHERE recommendation IS NOT NULL AND recommendation != ''")
    rec_rows = cursor.fetchall()
    rec_first_counts = {}
    rec_all_counts = {}
    recommendation_limit = _recommendation_limit()
    for (rec_val,) in rec_rows:
        try:
            rec = _limit_recommendations(rec_val, recommendation_limit)
            if len(rec) > 0:
                first = rec[0].get("course")
                if first:
                    rec_first_counts[first] = rec_first_counts.get(first, 0) + 1
                for item in rec:
                    course_name = item.get("course")
                    if course_name:
                        rec_all_counts[course_name] = rec_all_counts.get(course_name, 0) + 1
        except Exception:
            continue

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE COALESCE(role, 'student') = 'student'")
    total_students = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM users WHERE last_login_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'"
    )
    active_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM student_profiles")
    total_profiles = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM student_profiles WHERE recommendation IS NOT NULL AND recommendation != ''")
    total_recommendations = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM student_profiles WHERE recommendation IS NULL OR recommendation = ''")
    pending_evaluations = cursor.fetchone()[0]
    total_available_courses = len(_course_training_data())

    cursor.execute("SELECT gwa FROM student_profiles WHERE gwa IS NOT NULL AND gwa != ''")
    gwa_rows = [r[0] for r in cursor.fetchall()]
    gwa_nums = []
    for g in gwa_rows:
        try:
            val = float(str(g).strip())
            if 0 <= val <= 100:
                gwa_nums.append(val)
        except Exception:
            try:
                val = float(str(g).replace(',', '.'))
                if 0 <= val <= 100:
                    gwa_nums.append(val)
            except Exception:
                continue

    avg_gwa = round(sum(gwa_nums) / len(gwa_nums), 2) if gwa_nums else None
    # Calculate the average GWA and distribution buckets for admin statistics
    buckets = {"0-59": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0}
    for v in gwa_nums:
        if v < 60:
            buckets["0-59"] += 1
        elif v < 70:
            buckets["60-69"] += 1
        elif v < 80:
            buckets["70-79"] += 1
        elif v < 90:
            buckets["80-89"] += 1
        else:
            buckets["90-100"] += 1

    cursor.execute(
        """
        SELECT COALESCE(NULLIF(BTRIM(strand), ''), 'Other'), COUNT(*)
        FROM student_profiles
        GROUP BY COALESCE(NULLIF(BTRIM(strand), ''), 'Other')
        """
    )
    strand_counts = {row[0] or "Other": row[1] for row in cursor.fetchall()}
    # Define the route for fetching admin statistics (admin only)
    cursor.execute("SELECT TO_CHAR(NULLIF(upload_date, '')::timestamp, 'MM'), COUNT(*) FROM student_profiles WHERE upload_date IS NOT NULL AND upload_date != '' GROUP BY TO_CHAR(NULLIF(upload_date, '')::timestamp, 'MM') ORDER BY TO_CHAR(NULLIF(upload_date, '')::timestamp, 'MM')")
    month_rows = cursor.fetchall()
    month_map = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }
    monthly_uploads = {month_map[row[0]]: row[1] for row in month_rows if row[0] in month_map}

    cursor.execute("SELECT COUNT(*) FROM student_profiles WHERE recommendation IS NULL OR recommendation = ''")
    missing_recs = cursor.fetchone()[0]

    top_recs = sorted(rec_all_counts.items(), key=lambda x: (-x[1], x[0].casefold()))[:10]
    top_recommended = [{"course": k, "count": v} for k, v in top_recs]

    all_recommended = [{"course": k, "count": v} for k, v in sorted(rec_all_counts.items(), key=lambda x: (-x[1], x[0]))]
    most_recommended_course = top_recs[0][0] if top_recs else ""
    most_recommended_course_count = top_recs[0][1] if top_recs else 0
    most_common_strand = min(
        strand_counts,
        key=lambda strand: (-strand_counts[strand], strand.casefold()),
        default="",
    )
    most_common_strand_count = strand_counts.get(most_common_strand, 0)

    conn.close()
    # Return the compiled admin statistics as a JSON response
    return JSONResponse(
        {
            "by_profile_course": course_counts,
            "by_recommended_first": rec_first_counts,
            "total_users": total_users,
            "total_students": total_students,
            "total_profiles": total_profiles,
            "total_report_cards_uploaded": total_report_cards,
            "students_who_uploaded_reports": students_who_uploaded_reports,
            "total_recommendations_generated": total_recommendations,
            "total_course_recommendations": sum(rec_all_counts.values()),
            "total_available_courses": total_available_courses,
            "pending_evaluations": pending_evaluations,
            "avg_gwa": avg_gwa,
            "active_users": active_users,
            "most_recommended_course": most_recommended_course,
            "most_recommended_course_count": most_recommended_course_count,
            "gwa_buckets": buckets,
            "strand_counts": strand_counts,
            "most_common_strand": most_common_strand,
            "most_common_strand_count": most_common_strand_count,
            "monthly_uploads": monthly_uploads,
            "profiles_missing_recommendation": missing_recs,
            "top_recommended": top_recommended,
            "all_recommended_courses": all_recommended,
        }
    )


@fastapi_route("/admin/reports/generate", methods=["POST"])
async def admin_generate_report(req, data=None):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    if data is None:
        try:
            data = await req.json()
        except Exception:
            return JSONResponse({"success": False, "message": "Invalid report request."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"success": False, "message": "Invalid report request."}, status_code=400)

    report_type = str(data.get("report_type") or "student_summary").strip()
    report_titles = {
        "all_reports": "All Reports",
        "student_summary": "Student Summary Report",
        "report_cards": "Report Card Status Report",
        "recommendations": "Recommendation Frequency Report",
    }
    if report_type not in report_titles:
        return JSONResponse({"success": False, "message": "Choose a supported report type."}, status_code=400)

    strand = str(data.get("strand") or "").strip()
    if strand and strand not in SYSTEM_SETTING_STRANDS and strand != "Unspecified":
        return JSONResponse({"success": False, "message": "Choose a configured academic cluster."}, status_code=400)

    def parse_report_date(value, field_name):
        if not value:
            return None, None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date(), None
        except ValueError:
            return None, f"{field_name} must be a valid date."

    from_date, date_error = parse_report_date(data.get("from_date"), "Start date")
    if date_error:
        return JSONResponse({"success": False, "message": date_error}, status_code=400)
    to_date, date_error = parse_report_date(data.get("to_date"), "End date")
    if date_error:
        return JSONResponse({"success": False, "message": date_error}, status_code=400)
    if from_date and to_date and from_date > to_date:
        return JSONResponse({"success": False, "message": "Start date must be on or before end date."}, status_code=400)
    # Inclusive local-day range expressed as [start, end) instants.
    from_instant = datetime.combine(from_date, dt_time.min, APP_TIMEZONE) if from_date else None
    to_instant = datetime.combine(to_date + timedelta(days=1), dt_time.min, APP_TIMEZONE) if to_date else None

    filters = {
        "strand": strand or "All clusters",
        "from_date": from_date.isoformat() if from_date else "Any",
        "to_date": to_date.isoformat() if to_date else "Any",
    }
    if report_type == "all_reports":
        sections = []
        for section_type in ("student_summary", "report_cards", "recommendations"):
            response = await admin_generate_report(req, {**data, "report_type": section_type})
            if response.status_code != 200:
                return response
            sections.append(json.loads(response.body)["report"])
        return JSONResponse({
            "success": True,
            "report": {
                "type": report_type,
                "title": report_titles[report_type],
                "generated_at": _app_now().isoformat(timespec="seconds"),
                "filters": filters,
                "sections": sections,
            },
        })
    columns = []
    rows = []
    summary = []
    conn = _db_conn()
    cursor = conn.cursor()

    if report_type == "student_summary":
        conditions = ["COALESCE(users.role, 'student') = 'student'"]
        params = []
        if strand:
            conditions.append("COALESCE(NULLIF(BTRIM(profile.strand), ''), 'Unspecified') = ?")
            params.append(strand)
        # upload_date is stored as naive UTC ISO text.
        profile_saved_at = (
            "(CASE WHEN profile.upload_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
            "THEN profile.upload_date::timestamp AT TIME ZONE 'UTC' END)"
        )
        if from_instant:
            conditions.append(f"{profile_saved_at} >= ?")
            params.append(from_instant)
        if to_instant:
            conditions.append(f"{profile_saved_at} < ?")
            params.append(to_instant)
        cursor.execute(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(profile.strand), ''), 'Unspecified') AS strand,
                   COUNT(users.id), COUNT(profile.id),
                   AVG(CASE WHEN BTRIM(COALESCE(profile.gwa, '')) ~ '^[0-9]+([.][0-9]+){{0,1}}$'
                       THEN BTRIM(profile.gwa)::NUMERIC END),
                   COUNT(CASE WHEN BTRIM(COALESCE(profile.gwa, '')) ~ '^[0-9]+([.][0-9]+){{0,1}}$' THEN 1 END),
                   COUNT(*) FILTER (WHERE COALESCE(users.is_active, TRUE)),
                   COUNT(*) FILTER (WHERE NOT COALESCE(users.is_active, TRUE))
            FROM users
            LEFT JOIN LATERAL (
                SELECT id, strand, gwa, upload_date
                FROM student_profiles
                WHERE user_id = users.id
                ORDER BY id DESC
                LIMIT 1
            ) profile ON TRUE
            WHERE {' AND '.join(conditions)}
            GROUP BY 1
            ORDER BY 1
            """,
            tuple(params),
        )
        data_rows = cursor.fetchall()
        total_students = sum(row[1] for row in data_rows)
        total_profiles = sum(row[2] for row in data_rows)
        gwa_count = sum(row[4] for row in data_rows)
        average_gwa = (
            round(sum(float(row[3]) * row[4] for row in data_rows if row[3] is not None) / gwa_count, 2)
            if gwa_count else "—"
        )
        summary = [
            {"label": "Student accounts", "value": total_students},
            {"label": "Profiles with academic records", "value": total_profiles},
            {"label": "Average GWA", "value": average_gwa},
            {"label": "Active accounts", "value": sum(row[5] for row in data_rows)},
            {"label": "Deactivated accounts", "value": sum(row[6] for row in data_rows)},
        ]
        columns = [
            {"key": "strand", "label": "Academic cluster"},
            {"key": "students", "label": "Student accounts"},
            {"key": "profiles", "label": "Profiles"},
            {"key": "average_gwa", "label": "Average GWA"},
            {"key": "active", "label": "Active"},
            {"key": "inactive", "label": "Deactivated"},
        ]
        rows = [
            {
                "strand": row[0], "students": row[1], "profiles": row[2],
                "average_gwa": round(float(row[3]), 2) if row[3] is not None else "—",
                "active": row[5], "inactive": row[6],
            }
            for row in data_rows
        ]

    elif report_type == "report_cards":
        conditions = ["COALESCE(users.role, 'student') = 'student'"]
        params = []
        if from_instant:
            conditions.append("uploads.uploaded_at >= ?")
            params.append(from_instant)
        if to_instant:
            conditions.append("uploads.uploaded_at < ?")
            params.append(to_instant)
        if strand:
            conditions.append(
                "EXISTS (SELECT 1 FROM student_profiles profile WHERE profile.user_id = users.id "
                "AND COALESCE(NULLIF(BTRIM(profile.strand), ''), 'Unspecified') = ?)"
            )
            params.append(strand)
        cursor.execute(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(uploads.ocr_status), ''), 'unknown'),
                   COUNT(*),
                   COUNT(*) FILTER (WHERE COALESCE(uploads.is_flagged, FALSE)),
                   MAX(uploads.uploaded_at)
            FROM report_card_uploads uploads
            JOIN users ON users.id = uploads.user_id
            WHERE {' AND '.join(conditions)}
            GROUP BY 1
            ORDER BY 1
            """,
            tuple(params),
        )
        data_rows = cursor.fetchall()
        total_uploads = sum(row[1] for row in data_rows)
        total_flagged = sum(row[2] for row in data_rows)
        summary = [
            {"label": "Report-card uploads", "value": total_uploads},
            {"label": "Flagged uploads", "value": total_flagged},
            {"label": "OCR statuses", "value": len(data_rows)},
        ]
        columns = [
            {"key": "status", "label": "OCR status"},
            {"key": "uploads", "label": "Uploads"},
            {"key": "flagged", "label": "Flagged"},
            {"key": "latest_upload", "label": "Latest upload"},
        ]
        rows = [
            {
                "status": row[0], "uploads": row[1], "flagged": row[2],
                "latest_upload": _app_local(row[3]).strftime("%b %d, %Y %I:%M %p") if row[3] else "—",
            }
            for row in data_rows
        ]

    else:
        conditions = ["COALESCE(users.role, 'student') = 'student'"]
        params = []
        if from_instant:
            conditions.append("history.generated_at >= ?")
            params.append(from_instant)
        if to_instant:
            conditions.append("history.generated_at < ?")
            params.append(to_instant)
        if strand:
            conditions.append("COALESCE(NULLIF(BTRIM(profiles.strand), ''), 'Unspecified') = ?")
            params.append(strand)
        cursor.execute(
            f"""
            SELECT profiles.id, history.recommendations
            FROM course_recommendation_history history
            JOIN student_profiles profiles ON profiles.id = history.profile_id
            JOIN users ON users.id = profiles.user_id
            WHERE {' AND '.join(conditions)}
            ORDER BY history.generated_at DESC, history.id DESC
            """,
            tuple(params),
        )
        data_rows = cursor.fetchall()
        course_counts = {}
        profile_ids = set()
        total_suggestions = 0
        recommendation_limit = _recommendation_limit()
        for profile_id, payload in data_rows:
            profile_ids.add(profile_id)
            for item in _limit_recommendations(payload, recommendation_limit):
                course_name = item.get("course")
                if course_name:
                    course_counts[course_name] = course_counts.get(course_name, 0) + 1
                    total_suggestions += 1
        sorted_courses = sorted(course_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
        summary = [
            {"label": "Recommendation snapshots", "value": len(data_rows)},
            {"label": "Student profiles", "value": len(profile_ids)},
            {"label": "Course suggestions", "value": total_suggestions},
        ]
        columns = [
            {"key": "course", "label": "Recommended course"},
            {"key": "recommendations", "label": "Times recommended"},
            {"key": "share", "label": "Share"},
        ]
        rows = [
            {
                "course": course,
                "recommendations": count,
                "share": f"{(count / total_suggestions * 100):.1f}%" if total_suggestions else "0%",
            }
            for course, count in sorted_courses
        ]

    conn.close()
    return JSONResponse({
        "success": True,
        "report": {
            "type": report_type,
            "title": report_titles[report_type],
            "generated_at": _app_now().isoformat(timespec="seconds"),
            "filters": filters,
            "summary": summary,
            "columns": columns,
            "rows": rows,
        },
    })


def _admin_report_pdf(report):
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=170,
        bottomMargin=48, title=report["title"], author="PathFinder",
    )
    content_width = document.width - 12
    maroon = colors.HexColor("#800000")
    text_color = colors.HexColor("#262122")
    muted = colors.HexColor("#766e70")
    line_color = colors.HexColor("#e4dddd")
    heading_style = ParagraphStyle("Report heading", fontName="Helvetica-Bold", fontSize=17, leading=22, textColor=text_color)
    label_style = ParagraphStyle("Report label", fontName="Helvetica-Bold", fontSize=9, leading=13, textColor=maroon)
    body_style = ParagraphStyle("Report body", fontName="Helvetica", fontSize=9, leading=13, textColor=text_color)
    numeric_style = ParagraphStyle("Report numeric", parent=body_style, alignment=TA_RIGHT)
    numeric_heading_style = ParagraphStyle("Report numeric heading", parent=label_style, fontSize=8, leading=11, alignment=TA_RIGHT)
    muted_style = ParagraphStyle("Report muted", fontName="Helvetica", fontSize=8, leading=12, textColor=muted)

    logo_image = ImageReader(_maroon_pathfinder_logo())
    generated = datetime.fromisoformat(report["generated_at"]).strftime("%b %d, %Y %I:%M %p")
    filters = report["filters"]
    story = []
    for index, section in enumerate(report.get("sections", [report])):
        if report.get("sections"):
            if index:
                story.append(PageBreak())
            story.extend([Paragraph(html.escape(section["title"]), heading_style), Spacer(1, 12)])
        summary_rows = [
            [Paragraph(html.escape(item["label"]), muted_style), Paragraph(html.escape(str(item["value"])), numeric_style)]
            for item in section["summary"]
        ]
        if summary_rows:
            summary_table = Table(summary_rows, colWidths=[content_width * 0.7, content_width * 0.3], hAlign="LEFT")
            summary_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f5f5")),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, line_color),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.extend([summary_table, Spacer(1, 18)])

        columns = section["columns"]
        proportions = {
            "student_summary": [0.225, 0.175, 0.13, 0.165, 0.14, 0.165],
            "report_cards": [0.28, 0.15, 0.15, 0.42],
            "recommendations": [0.64, 0.20, 0.16],
        }[section["type"]]
        column_widths = [content_width * fraction for fraction in proportions]
        numeric_columns = {"students", "profiles", "average_gwa", "active", "inactive", "uploads", "flagged", "latest_upload", "recommendations", "share"}
        table_rows = [[
            Paragraph(html.escape(column["label"]), numeric_heading_style if column["key"] in numeric_columns else label_style)
            for column in columns
        ]]
        table_rows.extend(
            [Paragraph(html.escape(str(row.get(column["key"], ""))), numeric_style if column["key"] in numeric_columns else body_style) for column in columns]
            for row in section["rows"]
        )
        if not section["rows"]:
            table_rows.append([Paragraph("No records match the selected filters.", muted_style)] + [""] * (len(columns) - 1))
        detail_table = Table(table_rows, colWidths=column_widths, repeatRows=1, hAlign="LEFT")
        table_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5e9e9")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf8f8")]),
            ("LINEBELOW", (0, 0), (-1, 0), 1, maroon),
            ("BOX", (0, 0), (-1, -1), 0.4, line_color),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]
        if not section["rows"]:
            table_styles.append(("SPAN", (0, 1), (-1, 1)))
        detail_table.setStyle(TableStyle(table_styles))
        story.append(detail_table)

    def draw_page_frame(canvas, doc):
        canvas.saveState()
        left = doc.leftMargin + 6
        right = left + content_width
        canvas.drawImage(logo_image, left, A4[1] - 86, width=48, height=48, mask="auto")
        canvas.setFillColor(maroon)
        canvas.setFont("Helvetica-Bold", 20)
        canvas.drawString(left + 58, A4[1] - 58, "PathFinder")
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColor(text_color)
        canvas.drawString(left + 58, A4[1] - 76, PATHFINDER_SCHOOL_NAME)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(right, A4[1] - 58, f"Generated {generated}")
        canvas.setStrokeColor(line_color)
        canvas.setLineWidth(0.6)
        canvas.line(left, A4[1] - 92, right, A4[1] - 92)
        canvas.setFont("Helvetica-Bold", 17)
        canvas.setFillColor(text_color)
        canvas.drawString(left, A4[1] - 116, report["title"])
        canvas.setStrokeColor(maroon)
        canvas.setLineWidth(1.2)
        canvas.line(left, A4[1] - 128, right, A4[1] - 128)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        if filters["from_date"] == "Any" and filters["to_date"] == "Any":
            date_range = "All dates"
        else:
            date_range = (
                f"{'Beginning' if filters['from_date'] == 'Any' else filters['from_date']} to "
                f"{'Today' if filters['to_date'] == 'Any' else filters['to_date']}"
            )
        canvas.drawString(left, A4[1] - 144, f"Cluster: {filters['strand']}  |  Dates: {date_range}")
        canvas.drawString(left, 25, f"PathFinder  |  {PATHFINDER_SCHOOL_NAME}")
        canvas.drawRightString(right, 25, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page_frame, onLaterPages=draw_page_frame)
    return stream.getvalue()


@fastapi_route("/admin/reports/download", methods=["POST"])
async def admin_download_report(req):
    result = await admin_generate_report(req)
    if result.status_code != 200:
        return result
    report = json.loads(result.body)["report"]
    filename = f"pathfinder-{report['type']}-{datetime.fromisoformat(report['generated_at']):%Y-%m-%d}.pdf"
    return Response(
        _admin_report_pdf(report), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@fastapi_route("/admin/recommendations", methods=["GET"])
def admin_recommendation_history(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT history.id, history.generated_at, profiles.id, profiles.student_number,
               profiles.first_name, profiles.middle_initial, profiles.last_name,
               profiles.strand, profiles.gwa, profiles.subjects, history.recommendations,
               users.email, users.name
        FROM course_recommendation_history history
        JOIN student_profiles profiles ON profiles.id = history.profile_id
        JOIN users ON users.id = profiles.user_id
        WHERE COALESCE(users.role, 'student') = 'student'
        ORDER BY history.generated_at DESC, history.id DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    history_items = []
    course_counts = {}
    recommendation_limit = _recommendation_limit()
    for row in rows:
        profile_name = " ".join(part for part in (
            row[4] or "",
            row[5] or "",
            row[6] or "",
        ) if part).strip() or row[12] or "Unknown student"
        recommendations = _limit_recommendations(row[10], recommendation_limit)
        for item in recommendations:
            course_name = item.get("course")
            if course_name:
                course_counts[course_name] = course_counts.get(course_name, 0) + 1
        history_items.append({
            "history_id": row[0],
            "generated_at": row[1].isoformat() if row[1] else "",
            "profile_id": row[2],
            "student_number": row[3] or "",
            "student_name": profile_name,
            "strand": row[7] or "",
            "gwa": row[8] or "",
            "subjects": row[9] or "",
            "email": row[11] or "",
            "recommendations": recommendations,
        })

    frequent_courses = [
        {"course": name, "count": count}
        for name, count in sorted(course_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]
    return JSONResponse({
        "success": True,
        "history": history_items,
        "frequent_courses": frequent_courses,
        "history_count": len(history_items),
    })


@fastapi_route("/admin/recommendations/{profile_id}/recalculate", methods=["POST"])
def admin_recalculate_recommendations(req, profile_id: str):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid student profile id"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT profiles.subjects, profiles.course, profiles.strand
        FROM student_profiles profiles
        JOIN users ON users.id = profiles.user_id
        WHERE profiles.id = ? AND COALESCE(users.role, 'student') = 'student'
        """,
        (profile_id,),
    )
    profile = cursor.fetchone()
    if not profile:
        conn.close()
        return JSONResponse({"success": False, "message": "Student profile not found"}, status_code=404)

    recommendation_subjects = _clean_subjects_for_recommendation(profile[0] or "") or profile[0] or ""
    recommendations = _configured_recommendations(
        recommendation_subjects,
        profile[1] or "",
        profile[2] or "",
    )
    payload = json.dumps(recommendations)
    cursor.execute("UPDATE student_profiles SET recommendation = ? WHERE id = ?", (payload, profile_id))
    cursor.execute(
        "INSERT INTO course_recommendation_history (profile_id, recommendations) VALUES (?, ?)",
        (profile_id, payload),
    )
    conn.commit()
    conn.close()
    _record_admin_activity(req.session, "recommendations_recalculated", target_label=f"profile:{profile_id}", details=f"count={len(recommendations)}")
    result = {"success": True, "count": len(recommendations), "recommendations": recommendations}
    if not recommendations:
        result["message"] = "No courses have enough course-specific grade evidence. Previous suggestions were cleared."
    return JSONResponse(result)


# Define the route for saving a student profile
@fastapi_route("/save_profile", methods=["POST"])
async def save_profile(req):
    sess = req.session
    if "user_id" not in sess:
        return JSONResponse({"success": False, "message": "User not logged in"})

    user_id = sess["user_id"]
    data = await req.json()
    if _is_degree_program(data.get("strand")):
        data["course"] = data.get("course") or data["strand"]
        data["strand"] = ""
    subjects_text = data.get("subjects", "")
    table_subjects = _subjects_from_table(data.get("ocrTable", [])) if "subjects" not in data and data.get("ocrTable") else []
    if table_subjects:
        subjects_text = "\n".join(
            f"{item['subject_name']} - {item['grade']:g}"
            for item in table_subjects
        )
        data["subjects"] = subjects_text
    normalized_gwa = _compute_gwa(subjects_text, data.get("gwa", ""), data.get("grades", ""))
    normalized_grades = _coalesce_grades_text(subjects_text, data.get("grades", ""))

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, strand, report_card_upload_id
        FROM student_profiles
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    existing_profile = cursor.fetchone()
    pending_upload_id = req.session.get("latest_report_card_upload_id")
    if pending_upload_id:
        cursor.execute(
            "SELECT id FROM report_card_uploads WHERE id = ? AND user_id = ?",
            (pending_upload_id, user_id),
        )
        pending_upload = cursor.fetchone()
        pending_upload_id = pending_upload[0] if pending_upload else None
    report_card_upload_id = pending_upload_id or (existing_profile[10] if existing_profile else None)
    if pending_upload_id:
        student_name = " ".join(part for part in (
            (data.get("firstName") or "").strip(),
            (data.get("middleInitial") or "").strip().rstrip("."),
            (data.get("lastName") or "").strip(),
        ) if part)
        cursor.execute(
            """
            UPDATE report_card_uploads
            SET ocr_status = 'verified', student_name = ?, student_number = ?,
                extracted_subjects = ?, extracted_gwa = ?, ocr_error = NULL,
                processed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (
                student_name,
                (data.get("studentNumber") or "").strip(),
                subjects_text,
                normalized_gwa,
                pending_upload_id,
                user_id,
            ),
        )
        batch_upload_ids = [
            upload_id for upload_id in (req.session.get("latest_report_card_upload_ids") or [])
            if isinstance(upload_id, int) and upload_id != pending_upload_id
        ]
        for batch_upload_id in batch_upload_ids:
            cursor.execute(
                """
                UPDATE report_card_uploads
                SET ocr_status = 'verified', student_name = ?, student_number = ?,
                    ocr_error = NULL, processed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (student_name, (data.get("studentNumber") or "").strip(), batch_upload_id, user_id),
            )

    target_student = None
    if existing_profile:
        target_student = {
            "student_number": existing_profile[1],
            "first_name": existing_profile[2],
            "middle_initial": existing_profile[3],
            "last_name": existing_profile[4],
            "course": existing_profile[5],
            "gwa": existing_profile[6],
            "grades": existing_profile[7],
            "subjects": existing_profile[8],
            "strand": existing_profile[9],
        }
        if _is_degree_program(target_student["strand"]):
            target_student["course"] = target_student["course"] or target_student["strand"]
            target_student["strand"] = ""
    # Merge the existing student profile with the new data and prepare it for saving
    merged_student = data.copy()
    merged_student["gwa"] = normalized_gwa
    merged_student["grades"] = normalized_grades
    merged_student["subjects"] = subjects_text
    if target_student:
        merged_subjects = _merge_subject_entries(target_student.get("subjects", ""), data.get("subjects", ""))
        merged_grades = _coalesce_grades_text(merged_subjects)
        merged_gwa = _compute_gwa(merged_subjects, incoming_grades=merged_grades)
        merged_student = {
            "studentNumber": target_student.get("student_number", ""),
            "firstName": target_student.get("first_name", ""),
            "middleInitial": target_student.get("middle_initial", ""),
            "lastName": target_student.get("last_name", ""),
            "course": target_student.get("course", "") or data.get("course", ""),
            "strand": target_student.get("strand", "") or data.get("strand", ""),
            "gwa": merged_gwa,
            "grades": merged_grades,
            "subjects": merged_subjects,
        }
    subjects_text = merged_student.get("subjects", "")
    normalized_gwa = merged_student.get("gwa", "")
    recommendation_subjects = _clean_subjects_for_recommendation(subjects_text) or subjects_text
    recommendation = _configured_recommendations(
        recommendation_subjects, merged_student.get("course", ""), merged_student.get("strand", "")
    )
    recommendation_payload = json.dumps(recommendation)
    # If a target student exists, update their profile in the database; otherwise, insert a new profile
    if target_student:
        recommendation_profile_id = existing_profile[0]
        cursor.execute(
            """
            UPDATE student_profiles
            SET student_number = ?, first_name = ?, middle_initial = ?, last_name = ?, course = ?, gwa = ?, grades = ?, subjects = ?, recommendation = ?, strand = ?, upload_date = ?, report_card_upload_id = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                merged_student.get("studentNumber", ""),
                merged_student.get("firstName", ""),
                merged_student.get("middleInitial", ""),
                merged_student.get("lastName", ""),
                merged_student.get("course", ""),
                merged_student.get("gwa", ""),
                merged_student.get("grades", ""),
                merged_student.get("subjects", ""),
                recommendation_payload,
                merged_student.get("strand", ""),
                datetime.utcnow().isoformat(),
                report_card_upload_id,
                user_id,
                existing_profile[0],
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO student_profiles
            (user_id, student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, recommendation, strand, upload_date, report_card_upload_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                user_id,
                data.get("studentNumber", ""),
                data.get("firstName", ""),
                data.get("middleInitial", ""),
                data.get("lastName", ""),
                data.get("course", ""),
                normalized_gwa,
                normalized_grades,
                data.get("subjects", ""),
                recommendation_payload,
                data.get("strand", ""),
                datetime.utcnow().isoformat(),
                report_card_upload_id,
            ),
        )
        recommendation_profile_id = cursor.fetchone()[0]
    if recommendation:
        cursor.execute(
            "INSERT INTO course_recommendation_history (profile_id, recommendations) VALUES (?, ?)",
            (recommendation_profile_id, recommendation_payload),
        )
    # Commit the changes to the database and close the connection
    conn.commit()
    conn.close()
    sess.pop("latest_recommendations", None)
    sess["latest_subjects"] = subjects_text
    sess.pop("latest_report_card_upload_id", None)
    sess.pop("latest_report_card_upload_ids", None)
    sess.pop("latest_report_card_batch", None)
    saved_subject_count = len([line for line in str(subjects_text or "").splitlines() if line.strip()])
    top_course = (recommendation[0] or {}).get("course", "") if recommendation else ""
    _record_student_activity(
        req,
        "grades_saved",
        "; ".join(part for part in (
            f"{saved_subject_count} subjects",
            f"GWA {normalized_gwa}" if normalized_gwa else "",
            f"top match: {top_course}" if top_course else "",
        ) if part),
    )
    # Build the comparisons for the top 3 recommended courses based on student performance analytics
    comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], subjects_text)
        for item in recommendation
    }
    saved_profile = {
        key: merged_student.get(key, "")
        for key in ("studentNumber", "firstName", "middleInitial", "lastName", "course", "gwa", "grades", "subjects")
    }
    saved_profile.update({
        "success": True,
        "strand": merged_student.get("strand", ""),
        "subjectRows": _subject_rows_for_display(subjects_text),
        "recommendation": recommendation,
        "comparisons": comparisons,
    })
    return JSONResponse({
        "success": True,
        "message": "Profile saved successfully",
        "recommendation": recommendation,
        "comparisons": comparisons,
        "profile": saved_profile,
    })

# Define the route for updating the user account
@fastapi_route("/update_account", methods=["POST"])
async def update_account(req):
    sess = req.session
    if "user_id" not in sess:
        return JSONResponse({"success": False, "message": "User not logged in"}, status_code=401)

    data = await req.json()
    name = " ".join(str(data.get("name", "")).split())
    if not name:
        return JSONResponse({"success": False, "message": "Enter your name."}, status_code=400)

    conn = _db_conn()
    conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, sess["user_id"]))
    conn.commit()
    conn.close()
    sess["name"] = name
    if sess.get("role") in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "profile_name_changed", target_label=sess.get("email", ""))
    else:
        _record_student_activity(req, "account_name_changed", f"New name: {name}")
    return JSONResponse({"success": True, "name": name})

# Define the route for changing the admin password
@fastapi_route("/admin/change_password", methods=["POST"])
async def admin_change_password(req):
    sess = req.session
    if not _is_admin_session(sess, allow_password_change=True) or not sess.get("user_id"):
        return JSONResponse({"success": False, "message": "Admin session required."}, status_code=401)

    data = await req.json()
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not current_password or not new_password or not confirm_password:
        return JSONResponse({"success": False, "message": "Complete all password fields."}, status_code=400)
    password_error = _password_policy_error(new_password, admin=True)
    if password_error:
        return JSONResponse({"success": False, "message": password_error}, status_code=400)
    if new_password != confirm_password:
        return JSONResponse({"success": False, "message": "New passwords do not match."}, status_code=400)
    forced_change = bool(sess.get("must_change_password"))

    conn = _db_conn()
    cursor = conn.cursor()
    lock_security_row(cursor, sess["user_id"])
    cursor.execute("SELECT password_hash, auth_version FROM users WHERE id = ?", (sess["user_id"],))
    row = cursor.fetchone()
    if not row or row[1] != sess.get("auth_version", 0):
        conn.close()
        return JSONResponse({"success": False, "message": "Please sign in again."}, status_code=401)
    if not row or not _check_password(row[0], current_password):
        conn.close()
        return JSONResponse({"success": False, "message": "Current password is incorrect."}, status_code=400)
    if _check_password(row[0], new_password):
        conn.close()
        return JSONResponse({"success": False, "message": "Choose a password different from your current password."}, status_code=400)

    cursor.execute("UPDATE users SET password_hash = ?, must_change_password = FALSE WHERE id = ?", (_hash_password(new_password), sess["user_id"]))
    clear_recovery_tokens(cursor, sess["user_id"])
    conn.commit()
    conn.close()
    sess["must_change_password"] = False
    _record_admin_activity(sess, "temporary_password_changed" if forced_change else "password_changed", target_label=sess.get("email", ""))
    return JSONResponse({"success": True, "message": "Password updated."})

# Define the route for recommending courses to anonymous users
@fastapi_route("/recommend_anonymous", methods=["POST"])
async def recommend_anonymous(req):
    if not req.session.get("is_guest"):
        return JSONResponse({"success": False, "message": "Guest session required."}, status_code=401)

    data = await req.json()
    subjects_text = data.get("subjects", "")
    table_subjects = _subjects_from_table(data.get("ocrTable", [])) if "subjects" not in data and data.get("ocrTable") else []
    if table_subjects:
        subjects_text = "\n".join(
            f"{item['subject_name']} - {item['grade']:g}"
            for item in table_subjects
        )

    recommendation_subjects = _clean_subjects_for_recommendation(subjects_text) or subjects_text
    recommendations = _configured_recommendations(recommendation_subjects, "", data.get("strand", ""))
    req.session.pop("latest_recommendations", None)
    req.session["latest_subjects"] = subjects_text
    req.session["latest_strand"] = data.get("strand", "")
    comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], subjects_text)
        for item in recommendations
    }
    return JSONResponse({"success": True, "recommendation": recommendations, "comparisons": comparisons})

# Define the route for fetching the user's profile
@fastapi_route("/get_profile", methods=["GET"])
def get_profile(req):
    sess = req.session
    if "user_id" not in sess:
        return JSONResponse({"success": False})

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, recommendation, strand, id
        FROM student_profiles
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (sess["user_id"],),
    )
    profile = cursor.fetchone()

    if not profile:
        conn.close()
        return JSONResponse({"success": False})

    recommendation_value = profile[8]
    try:
        recommendation_data = json.loads(recommendation_value) if recommendation_value else None
    except (TypeError, ValueError):
        recommendation_data = recommendation_value
    saved_recommendations = _limit_recommendations(recommendation_data)
    if profile[7]:
        # Saved results may come from an older ranking model, so rebuild them from the saved grades.
        subjects = _clean_subjects_for_recommendation(profile[7]) or profile[7]
        current_recommendations = _configured_recommendations(subjects, profile[4] or "", profile[9] or "")
        if current_recommendations != saved_recommendations:
            saved_recommendations = current_recommendations
            payload = json.dumps(current_recommendations)
            cursor.execute("UPDATE student_profiles SET recommendation = ? WHERE id = ?", (payload, profile[10]))
            cursor.execute(
                "INSERT INTO course_recommendation_history (profile_id, recommendations) VALUES (?, ?)",
                (profile[10], payload),
            )
            conn.commit()
    conn.close()
    saved_comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], profile[7] or "")
        for item in saved_recommendations
    }

    return JSONResponse(
        {
            "success": True,
            "studentNumber": profile[0],
            "firstName": profile[1],
            "middleInitial": profile[2],
            "lastName": profile[3],
            "course": profile[4],
            "gwa": profile[5],
            "grades": profile[6],
            "subjects": profile[7],
            "subjectRows": _subject_rows_for_display(profile[7]),
            "recommendation": saved_recommendations,
            "comparisons": saved_comparisons,
            "strand": profile[9],
        }
    )


def _results_email_content(profile, recommendations):
    name = " ".join(part for part in (
        profile.get("first_name") or "",
        f"{profile['middle_initial']}." if profile.get("middle_initial") else "",
        profile.get("last_name") or "",
    ) if part).strip() or "Student"

    def score_text(item):
        try:
            return f"{float(item.get('match_score', 0)):.2f}%"
        except (TypeError, ValueError):
            return ""

    text_lines = [
        f"Hi {name},",
        "",
        "Here are your PathFinder course recommendations:",
        "",
    ]
    for index, item in enumerate(recommendations, start=1):
        score_label = "synthetic model support" if item.get("model_basis") == MODEL_BASIS else "match"
        text_lines.append(f"{index}. {item.get('course', '')} - {score_text(item)} {score_label}")
        if item.get("reason"):
            text_lines.append(f"   {item['reason']}")
    text_lines += [
        "",
        "Course matches are academic guidance based on the grades provided. They do not guarantee "
        "admission, eligibility, or career outcomes. Consider your interests and speak with a guidance "
        "counselor before deciding.",
    ]

    esc = html.escape

    def recommendation_card(index, item):
        course = esc(item.get("course", ""))
        score = esc(score_text(item))
        score_label = "synthetic model support" if item.get("model_basis") == MODEL_BASIS else "match"
        rank_color = "#b8c2ff" if index == 1 else "#d7dcf2"
        rank_text = "#273bc5" if index == 1 else "#4b5875"
        reason = (
                f"<p style='margin:8px 0 0;color:#58657d;font-size:13px;"
                f"line-height:1.5'>{esc(item['reason'])}</p>"
            ) if item.get("reason") else ""
        return (
            "<tr><td style='padding:0 0 12px'>"
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid #e3e7f4;border-radius:12px;background:#ffffff'>"
            "<tr>"
            f"<td width='52' valign='top' style='padding:16px 0 16px 16px'>"
            f"<div style='width:34px;height:34px;line-height:34px;text-align:center;"
            f"border-radius:10px;background:{rank_color};color:{rank_text};"
            f"font-size:14px;font-weight:700'>{index:02d}</div></td>"
            f"<td valign='top' style='padding:16px 8px 16px 12px'>"
            f"<div style='color:#17213b;font-size:16px;font-weight:700;line-height:1.35'>{course}</div>"
            f"{reason}</td>"
            f"<td width='96' valign='top' align='right' style='padding:16px 16px 16px 8px'>"
            f"<div style='display:inline-block;padding:7px 10px;border-radius:20px;"
            f"background:#eef0ff;color:#3548d4;font-size:13px;font-weight:700;white-space:nowrap'>"
            f"{score}</div><div style='margin-top:5px;color:#7a849a;font-size:10px;"
            f"text-transform:uppercase;letter-spacing:.5px'>{score_label}</div></td>"
            "</tr></table></td></tr>"
        )

    cards = "".join(
        recommendation_card(index, item)
        for index, item in enumerate(recommendations, start=1)
    )
    html_body = f"""<!doctype html>
<html lang="en"><body style="margin:0;padding:0;background:#f3f5fb;font-family:Arial,Helvetica,sans-serif;color:#1d2433">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5fb;padding:28px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px">
<tr><td style="padding:24px 26px;border-radius:16px 16px 0 0;background:#3548d4;color:#ffffff">
<div style="font-size:12px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;opacity:.85">PATHFINDER</div>
<h1 style="margin:10px 0 0;font-size:25px;line-height:1.25">Your course matches</h1>
</td></tr>
<tr><td style="padding:26px;background:#ffffff">
<p style="margin:0 0 8px;color:#26324a;font-size:16px">Hi {esc(name)},</p>
<p style="margin:0 0 20px;color:#667085;font-size:14px;line-height:1.5">Here are the courses that best match your profile:</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{cards}</table>
<div style="margin-top:10px;padding:14px 16px;border-radius:10px;background:#f5f7ff;color:#52607a;font-size:12px;line-height:1.6">
These recommendations are for academic guidance only. They do not guarantee admission, eligibility, or career outcomes. Consider your interests and speak with a guidance counselor before deciding.
</div>
</td></tr>
<tr><td align="center" style="padding:16px;color:#8992a6;font-size:11px">Sent by PathFinder</td></tr>
</table>
</td></tr></table>
</body></html>"""
    return "\n".join(text_lines), html_body


RESULTS_EMAIL_COOLDOWN_SECONDS = 60


@fastapi_route("/email_results", methods=["POST"])
async def email_results(req):
    sess = req.session
    if not _can_email_results(sess):
        return JSONResponse(
            {"success": False, "message": "Emailing results is available only when you sign in with Google."},
            status_code=403,
        )
    last_sent = sess.get("results_email_sent_at") or 0
    wait = RESULTS_EMAIL_COOLDOWN_SECONDS - int(time.time() - float(last_sent))
    if wait > 0:
        return JSONResponse(
            {"success": False, "message": f"Please wait {wait} seconds before sending another email."},
            status_code=429,
        )
    profile = _get_user_latest_profile(sess["user_id"])
    if not profile or not profile.get("subjects"):
        return JSONResponse({"success": False, "message": "Save your report card first to get recommendations."}, status_code=400)
    subjects = _clean_subjects_for_recommendation(profile["subjects"]) or profile["subjects"]
    recommendations = _configured_recommendations(subjects, profile.get("course") or "", profile.get("strand") or "")
    if not recommendations:
        return JSONResponse({"success": False, "message": "No course recommendations are available to email yet."}, status_code=400)
    text_body, html_body = _results_email_content(profile, recommendations)
    destination = sess["email"]
    try:
        await asyncio.to_thread(send_email, destination, "Your PathFinder course recommendations", text_body, html_body)
    except ValueError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return JSONResponse(
            {"success": False, "code": "email_not_configured",
             "message": "Email is not configured on the server yet. Please contact the administrator."},
            status_code=503,
        )
    except (smtplib.SMTPException, OSError) as exc:
        logging.getLogger(__name__).error("Results email failed (%s %s).", type(exc).__name__, getattr(exc, "status_code", ""))
        return JSONResponse(
            {"success": False, "code": "email_delivery_failed",
             "message": "Your results could not be emailed right now. Please try again later."},
            status_code=503,
        )
    sess["results_email_sent_at"] = time.time()
    _record_student_activity(req, "results_emailed", f"{len(recommendations)} recommendations")
    return JSONResponse({"success": True, "message": f"Your results were sent to {destination}."})

# Define the route for uploading a profile picture
@fastapi_route("/upload_profile_picture", methods=["POST"])
async def upload_profile_picture(req):
    sess = req.session
    if "user_id" not in sess:
        return JSONResponse({"success": False})

    form = await req.form()
    up_file = form.get("profile_picture")
    if up_file is None:
        return JSONResponse({"success": False})

    filename_in = getattr(up_file, "filename", "") or ""
    if not filename_in:
        return JSONResponse({"success": False, "message": "Choose an image to upload."})

    file_bytes = await up_file.read(PROFILE_IMAGE_MAX_UPLOAD_BYTES + 1)
    conn = _db_conn()
    cursor = conn.cursor()
    try:
        stored_value = _store_profile_image(cursor, sess["user_id"], file_bytes, "upload")
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        conn.close()
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    except psycopg.Error:
        conn.rollback()
        conn.close()
        return JSONResponse({"success": False, "message": "Unable to save the picture. Try again."}, status_code=500)
    conn.close()

    _set_session_profile_image(sess, stored_value)
    if sess.get("role") in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "profile_picture_changed", target_label=sess.get("email", ""))
    else:
        _record_student_activity(req, "profile_picture_changed", filename_in)
    return JSONResponse({"success": True, "picture": stored_value, "profile_image": _profile_image_from_value(stored_value)})


def get_profile_image(req, user_id: str, version: str = ""):
    if "user_id" not in req.session:
        return Response(status_code=401)
    try:
        requested_id = int(user_id)
    except (TypeError, ValueError):
        return Response(status_code=404)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT content_type, image_data FROM profile_images WHERE user_id = ?", (requested_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return RedirectResponse(_default_profile_image_url(), status_code=302)
    return Response(
        bytes(row[1]),
        media_type=row[0],
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


# Define the route for fetching the profile picture 
@fastapi_route("/get_profile_picture", methods=["GET"])
def get_profile_picture(req):
    sess = req.session
    if "user_id" not in sess:
        return JSONResponse({"success": False})

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT profile_picture FROM users WHERE id = ?", (sess["user_id"],))
    picture = cursor.fetchone()
    conn.close()

    if picture:
        _set_session_profile_image(sess, picture[0])
        return JSONResponse({"success": True, "picture": picture[0], "profile_image": _profile_image_from_value(picture[0])})

    return JSONResponse({"success": False})

# Define the route for Google login and authorization
@fastapi_route("/google-login", methods=["GET"])
async def google_login(req):
    redirect_uri = str(req.url_for("google_authorize"))
    return await google.authorize_redirect(req, redirect_uri)


@fastapi_route("/authorize", methods=["GET"])
async def google_authorize(req):
    token = await google.authorize_access_token(req)
    userinfo = token.get("userinfo") or {}
    name = userinfo.get("name", "User")
    email = userinfo.get("email", "")
    google_picture = (userinfo.get("picture") or "").strip()

    if not email:
        return RedirectResponse("/", status_code=302)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE), username FROM users WHERE email = ?",
        (email,),
    )
    existing_user = cursor.fetchone()

    if existing_user:
        if not existing_user[3]:
            conn.close()
            return RedirectResponse("/", status_code=302)
        user_id = existing_user[0]
        current_picture = (existing_user[1] or "").strip()
        role = existing_user[2] or "student"
        must_change_password = bool(existing_user[4])
        username = existing_user[5] or ""
    else:
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, profile_picture, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, email, "GOOGLE_ACCOUNT", "default.svg", "student"),
        )
        conn.commit()
        cursor.execute(
            "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE), username FROM users WHERE email = ?",
            (email,),
        )
        inserted = cursor.fetchone()
        if not inserted[3]:
            conn.close()
            return RedirectResponse("/", status_code=302)
        user_id = inserted[0]
        current_picture = inserted[1] or ""
        role = inserted[2] or "student"
        must_change_password = bool(inserted[4])
        username = inserted[5] or ""

    # Cache the Google avatar in the database unless the user uploaded their own picture.
    lowered_picture = current_picture.lower()
    stored_picture = _parse_profile_image_db_value(current_picture)
    legacy_file_missing = (
        lowered_picture not in _DEFAULT_PROFILE_VALUES
        and not lowered_picture.startswith(("http://", "https://", PROFILE_IMAGE_DB_PREFIX))
        and not os.path.isfile(os.path.join(UPLOAD_FOLDER, os.path.basename(current_picture)))
    )
    uses_google_picture = (
        lowered_picture in _DEFAULT_PROFILE_VALUES
        or lowered_picture.startswith(("http://", "https://"))
        or legacy_file_missing
        or (stored_picture is not None and _profile_image_source(cursor, user_id) == "google")
    )
    if google_picture and uses_google_picture:
        picture_bytes = await _download_google_picture(google_picture)
        try:
            if not picture_bytes:
                raise ValueError("Google picture unavailable")
            current_picture = _store_profile_image(cursor, user_id, picture_bytes, "google")
            conn.commit()
        except (ValueError, psycopg.Error):
            conn.rollback()
            if stored_picture is None:
                cursor.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (google_picture, user_id))
                conn.commit()
                current_picture = google_picture

    cursor.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    cursor.execute("SELECT auth_version FROM users WHERE id = ?", (user_id,))
    auth_version = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    # Set the session variables for the logged-in user
    sess = req.session
    sess.clear()
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email
    sess["username"] = username
    sess["role"] = role
    sess["must_change_password"] = must_change_password
    sess["auth_version"] = auth_version
    sess["auth_provider"] = "google"
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or email
        sess["admin_ip"] = req.client.host if req.client else ""
    _set_session_profile_image(sess, current_picture)
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "google_login", target_label=email)
    else:
        if not existing_user:
            _record_student_activity(req, "account_registered", "Google sign-up")
        _record_student_activity(req, "login", "Google account")

    return RedirectResponse("/admin/dashboard" if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN) else "/home", status_code=302)

# Group OCR boxes into table rows for report card processing
def _group_ocr_boxes_into_table_rows(boxes):
    items = [box for box in (boxes or []) if str(box.get("text", "")).strip()]
    if len(items) < 4:
        return []

    items.sort(key=lambda b: (b.get("y", 0), b.get("x", 0)))
    heights = [b.get("height", 0) for b in items if b.get("height", 0) > 0]
    avg_height = (sum(heights) / len(heights)) if heights else 20
    row_threshold = max(10, avg_height * 0.6)

    rows = []
    current_row = []
    current_y = None
    for box in items:
        y = box.get("y", 0)
        if current_y is None or abs(y - current_y) <= row_threshold:
            current_row.append(box)
            current_y = y if current_y is None else current_y
        else:
            rows.append(current_row)
            current_row = [box]
            current_y = y
    if current_row:
        rows.append(current_row)

    table_rows = []
    for row_index, row in enumerate(rows):
        row.sort(key=lambda b: b.get("x", 0))
        cells = [{"column": col_index, "text": box.get("text", "")} for col_index, box in enumerate(row)]
        table_rows.append({"row": row_index, "cells": cells})

    if len(table_rows) < 2 or all(len(row["cells"]) < 2 for row in table_rows):
        return []
    return table_rows

# Process a report image through Docling and normalize the parsed grade rows.
def _process_report_card_bytes(file_bytes, filename):
    ocr_payload = scan_report_card_docling(file_bytes, filename=filename)
    raw_ocr = {
        "raw_text": ocr_payload.get("raw_text", ""),
        "student_info_raw": ocr_payload.get("student_info_raw", []),
        "table": ocr_payload.get("table", []),
        "paddle_boxes": [],
        "image_width": 0,
        "image_height": 0,
    }
    parse_started = time.perf_counter()
    parsed = parse_report_card_structure(raw_ocr)
    if ocr_payload.get("subjects"):
        parsed["subjects"] = ocr_payload["subjects"]

        def _review_key(value):
            return f" {' '.join(re.findall(r'[a-z0-9]+', str(value or '').lower()))} "

        parsed_keys = [
            _review_key(item.get("subject_name"))
            for item in parsed["subjects"]
            if item.get("subject_name") and not item.get("needs_review") and item.get("grade") is not None
        ]
        remaining_errors = []
        for error in parsed.get("validation_errors") or []:
            match = re.match(r"^(?:Needs review:\s*|Grade rejected for\s+)(.+)$", str(error or ""), re.I)
            if match:
                error_key = _review_key(match.group(1))
                if not re.search(r"[a-z]{3,}", error_key) or any(key.strip() and key in error_key for key in parsed_keys):
                    continue
            remaining_errors.append(error)
        parsed["validation_errors"] = remaining_errors
        parsed["needs_review"] = bool(remaining_errors) or any(
            item.get("needs_review") for item in parsed["subjects"]
        )

    parsed_subjects = [
        item for item in parsed.get("subjects", [])
        if item.get("subject_name") and item.get("grade") is not None
    ]
    structured_text = "\n".join(
        f"{item['subject_name']} - {item['grade']:g}"
        for item in parsed_subjects
    )
    grades = [float(item["grade"]) for item in parsed_subjects]
    extracted_gwa = f"{sum(grades) / len(grades):.2f}" if grades else ""
    needs_review = bool(parsed.get("needs_review")) or not parsed_subjects
    parsed["needs_review"] = needs_review
    parsed["gwa"] = float(extracted_gwa) if extracted_gwa else ""
    return {
        "ocr_payload": ocr_payload,
        "raw_ocr": raw_ocr,
        "parsed": parsed,
        "structured_text": structured_text,
        "extracted_gwa": extracted_gwa,
        "ocr_status": "needs_review" if needs_review else "extracted",
        "app_parse_seconds": round(time.perf_counter() - parse_started, 3),
    }


INVALID_REPORT_CARD_MESSAGE = "Upload a valid report card image."
_REPORT_CARD_ACADEMIC_TERMS = re.compile(
    r"\b(?:math(?:ematics)?|algebra|calculus|statistics|probability|geometry|trigonometry|"
    r"science|physics|chemistry|biology|english|filipino|literature|oral\s+communication|"
    r"reading\s+and\s+writing|creative\s+writing|komunikasyon|pagbasa|history|philosophy|"
    r"araling\s+panlipunan|mapeh|physical\s+education|p\.?\s?e\b|values\s+education|"
    r"edukasyon|esp|religion|christian\s+living|economics|entrepreneurship|accounting|"
    r"business\s+(?:math|finance|ethics)|organization\s+and\s+management|research|ict|"
    r"programming|computer|empowerment\s+technologies|personal\s+development|humanities|"
    r"social\s+sciences?|tle|technology\s+and\s+livelihood|contemporary|"
    r"media\s+and\s+information|understanding\s+culture|disciplines?\s+and\s+ideas)\b",
    re.IGNORECASE,
)
_REPORT_CARD_WORDING = re.compile(
    r"\b(?:report\s+card|progress\s+report|school\s+year|s\.\s?y\.|grade\s+level|final\s+(?:grade|rating)|"
    r"general\s+(?:average|weighted\s+average)|gen\.?\s+ave|gwa|quarter|semester|learners?|lrn|"
    r"subjects?|remarks|adviser|principal|registrar|student\s+(?:no|number|name)|deped|"
    r"senior\s+high|junior\s+high|school\s+form|sf\s?9|form\s+138|promoted|transcript|"
    r"strand|track|section)\b",
    re.IGNORECASE,
)


def _looks_like_report_card(processed):
    """Reject photos, logos, receipts, and other images that contain no report-card evidence."""
    parsed = processed.get("parsed") or {}
    raw_ocr = processed.get("raw_ocr") or {}
    graded_names = [
        str(item.get("subject_name") or "")
        for item in parsed.get("subjects") or []
        if item.get("subject_name") and item.get("grade") is not None
    ]
    if any(_REPORT_CARD_ACADEMIC_TERMS.search(name) for name in graded_names):
        return True
    text = " ".join([
        str(raw_ocr.get("raw_text") or ""),
        " ".join(str(value) for value in raw_ocr.get("student_info_raw") or []),
    ])
    wording = {match.group(0).lower() for match in _REPORT_CARD_WORDING.finditer(text)}
    if parsed.get("student_no"):
        wording.add("detected student number")
    if parsed.get("strand"):
        wording.add("detected cluster")
    return len(wording) >= 2 or (bool(wording) and bool(graded_names))


def _stream_ocr_response(process):
    async def events():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def report(stage, message):
            loop.call_soon_threadsafe(queue.put_nowait, {
                "type": "progress", "stage": stage, "message": message,
            })

        task = asyncio.create_task(asyncio.to_thread(process, report))
        try:
            yield dumps({
                "type": "progress", "stage": "received",
                "message": "Upload received. Waiting for OCR to start...",
            }) + "\n"
            while not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                yield dumps(event) + "\n"
            while not queue.empty():
                yield dumps(queue.get_nowait()) + "\n"
            response = await task
            yield dumps({"type": "result", "data": json.loads(response.body)}) + "\n"
        finally:
            # A disconnected browser must not cancel an upload already being saved.
            if not task.done():
                await asyncio.shield(task)

    return StreamingResponse(
        events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@fastapi_route("/ocr_report_card", methods=["POST"])
async def ocr_report_card(req):
    form = await req.form()
    upload_batch = str(form.get("upload_batch") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", upload_batch):
        upload_batch = ""
    if not upload_batch or req.session.get("latest_report_card_batch") != upload_batch:
        req.session.pop("latest_report_card_upload_id", None)
        req.session["latest_report_card_upload_ids"] = []
        req.session["latest_report_card_batch"] = upload_batch
    uploaded = form.get("report_card") or form.get("file") or form.get("image")
    if uploaded is None:
        return JSONResponse({"success": False, "message": "No report card uploaded."})

    system_settings = _get_system_settings()
    automatic_ocr_enabled = system_settings.get("automatic_ocr_enabled", True)
    valid, result = await validate_upload(uploaded, system_settings.get("max_file_size_mb", 10))
    if not valid:
        return JSONResponse({"success": False, "message": result})

    file_bytes = result["file_bytes"]
    filename = re.split(r"[\\/]", getattr(uploaded, "filename", "") or "")[-1].strip()
    if not filename:
        filename = f"report-card{result['extension']}"
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[result["extension"]]
    upload_id = None
    user_id = req.session.get("user_id")
    if user_id:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO report_card_uploads (user_id, original_filename, content_type, file_data, ocr_status, upload_batch)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (user_id, filename, content_type, file_bytes, "processing" if automatic_ocr_enabled else "needs_review", upload_batch or None),
        )
        upload_id = cursor.fetchone()[0]
        batch_upload_ids = [*(req.session.get("latest_report_card_upload_ids") or []), upload_id]
        req.session["latest_report_card_upload_ids"] = batch_upload_ids
        req.session["latest_report_card_upload_id"] = batch_upload_ids[0]
        conn.commit()
        conn.close()

    if not automatic_ocr_enabled:
        return JSONResponse({
            "success": True,
            "message": "Report card saved. Automatic OCR is disabled; enter the details manually.",
            "automatic_ocr_disabled": True,
            "provider": "Manual review",
            "raw_ocr": {"table": []},
            "parsed": {"subjects": [], "needs_review": True},
            "structured_text": "",
            "review_required": True,
        })

    def process(on_progress):
        return _finish_report_card_upload(req, file_bytes, filename, upload_id, user_id, on_progress)

    if "application/x-ndjson" in req.headers.get("accept", ""):
        return _stream_ocr_response(process)
    return await asyncio.to_thread(process, None)


def _finish_report_card_upload(req, file_bytes, filename, upload_id, user_id, on_progress):
    try:
        if on_progress:
            on_progress("processing", "Running Docling OCR and TableFormer...")
        processed = _process_report_card_bytes(file_bytes, filename)
        if not _looks_like_report_card(processed):
            return _reject_non_report_card(req, upload_id, filename, "No report card subjects or details were found.")
        if on_progress:
            on_progress("preparing", "OCR finished. Preparing extracted results...")
        ocr_payload = processed["ocr_payload"]
        raw_ocr = processed["raw_ocr"]
        parsed = processed["parsed"]
        if upload_id:
            conn = _db_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE report_card_uploads
                SET ocr_status = ?, student_name = ?, student_number = ?, extracted_subjects = ?,
                    extracted_gwa = ?, ocr_error = NULL, processed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (
                    processed["ocr_status"],
                    parsed.get("student_name") or "",
                    parsed.get("student_no") or "",
                    processed["structured_text"],
                    processed["extracted_gwa"],
                    upload_id,
                    user_id,
                ),
            )
            conn.commit()
            conn.close()
        subject_count = len(parsed.get("subjects", []))
        _record_student_activity(
            req,
            "report_card_uploaded",
            f"{subject_count} subjects extracted" + ("; needs review" if parsed.get("needs_review") else ""),
            target_label=filename,
        )
        return JSONResponse({
            "success": True,
            "message": "Report card extraction completed using Docling.",
            "raw_ocr": raw_ocr,
            "parsed": parsed,
            "structured_text": processed["structured_text"],
            "provider": "docling",
            "diagnostics": {
                "table_rows": len(raw_ocr.get("table", [])),
                "docling_subjects": len(ocr_payload.get("subjects", [])),
                "parsed_subjects": len(parsed.get("subjects", [])),
                "raw_text_characters": len(raw_ocr.get("raw_text", "")),
                "ocr_engine": ocr_payload.get("ocr_engine"),
                "table_preview": dumps(raw_ocr.get("table", [])[1:3], default=str)[:500],
                "timings_seconds": {
                    **ocr_payload.get("timings_seconds", {}),
                    "app_parse_seconds": processed["app_parse_seconds"],
                },
            },
            "review_required": parsed.get("needs_review", False),
        })
    except Exception as exc:
        if "could not load image" in str(exc).lower():
            return _reject_non_report_card(req, upload_id, filename, str(exc))
        if upload_id:
            conn = _db_conn()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE report_card_uploads SET ocr_status = 'failed', ocr_error = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (str(exc)[:1000], upload_id),
            )
            conn.commit()
            conn.close()
        _record_student_activity(req, "report_card_upload_failed", str(exc)[:300], target_label=filename)
        return JSONResponse({"success": False, "message": f"Report card OCR failed: {exc}"})


def _reject_non_report_card(req, upload_id, filename, reason):
    if upload_id:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE report_card_uploads SET ocr_status = 'failed', ocr_error = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (f"Not a report card: {reason}"[:1000], upload_id),
        )
        conn.commit()
        conn.close()
    _record_student_activity(req, "report_card_upload_failed", f"Not a report card: {reason}"[:300], target_label=filename)
    return JSONResponse({"success": False, "code": "invalid_report_card", "message": INVALID_REPORT_CARD_MESSAGE})


@fastapi_route("/admin/report_cards", methods=["GET"])
def admin_list_report_cards(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT uploads.id, uploads.original_filename, uploads.uploaded_at,
               uploads.ocr_status, uploads.is_flagged, uploads.flag_note,
               uploads.student_name, uploads.student_number, uploads.extracted_subjects,
               uploads.extracted_gwa, uploads.ocr_error, profile.id, profile.student_number,
               profile.first_name, profile.middle_initial, profile.last_name,
               profile.gwa, profile.subjects, users.name, users.email
        FROM report_card_uploads uploads
        JOIN users ON users.id = uploads.user_id
        LEFT JOIN LATERAL (
            SELECT id, student_number, first_name, middle_initial, last_name,
                   gwa, subjects
            FROM student_profiles
            WHERE report_card_upload_id = uploads.id
            ORDER BY id DESC
            LIMIT 1
        ) profile ON TRUE
        WHERE COALESCE(users.role, 'student') = 'student'
        ORDER BY uploads.uploaded_at DESC, uploads.id DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    reports = []
    for row in rows:
        profile_name = " ".join(part for part in (
            row[13] or "",
            row[14] or "",
            row[15] or "",
        ) if part).strip()
        reports.append({
            "id": row[0],
            "original_filename": row[1] or "report-card",
            "uploaded_at": row[2].isoformat() if row[2] else "",
            "ocr_status": row[3] or "needs_review",
            "is_flagged": bool(row[4]),
            "flag_note": row[5] or "",
            "student_name": profile_name or row[6] or row[18] or "Unknown student",
            "student_number": row[12] or row[7] or "",
            "extracted_subjects": row[8] or row[17] or "",
            "extracted_subject_count": len([line for line in (row[8] or row[17] or "").splitlines() if line.strip()]),
            "extracted_gwa": row[9] or row[16] or "",
            "ocr_error": row[10] or "",
            "profile_id": row[11],
            "email": row[19] or "",
        })
    return JSONResponse({"success": True, "reports": reports})


@fastapi_route("/admin/report_cards/{upload_id}/edit", methods=["POST"])
async def admin_edit_report_card(req, upload_id: str):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid report card id"}, status_code=400)

    data = await req.json()
    input_rows = data.get("subjects")
    if not isinstance(input_rows, list):
        return JSONResponse({"success": False, "message": "Subject rows are required"}, status_code=400)
    subjects = []
    for row in input_rows:
        if not isinstance(row, dict):
            continue
        subject_name = str(row.get("subject_name") or "").strip()
        grade_value = str(row.get("grade") or "").strip()
        if not subject_name and not grade_value:
            continue
        try:
            grade = float(grade_value)
        except (TypeError, ValueError):
            return JSONResponse({"success": False, "message": f"Enter a numeric grade for {subject_name or 'each subject'}."}, status_code=400)
        if not subject_name or not 0 <= grade <= 100:
            return JSONResponse({"success": False, "message": "Each subject needs a name and a grade from 0 to 100."}, status_code=400)
        subjects.append({"subject_name": subject_name, "grade": grade})
    if not subjects:
        return JSONResponse({"success": False, "message": "Add at least one subject grade before verifying this report."}, status_code=400)

    subjects_text = "\n".join(f"{item['subject_name']} - {item['grade']:g}" for item in subjects)
    grades_text = ", ".join(f"{item['grade']:g}" for item in subjects)
    gwa = f"{_average([item['grade'] for item in subjects]):.2f}" if subjects else ""
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT profiles.id, profiles.course, profiles.strand
        FROM report_card_uploads uploads
        JOIN users ON users.id = uploads.user_id
        LEFT JOIN LATERAL (
            SELECT id, course, strand
            FROM student_profiles
            WHERE report_card_upload_id = uploads.id
            ORDER BY id DESC
            LIMIT 1
        ) profiles ON TRUE
        WHERE uploads.id = ? AND COALESCE(users.role, 'student') = 'student'
        """,
        (upload_id,),
    )
    report = cursor.fetchone()
    if not report:
        conn.close()
        return JSONResponse({"success": False, "message": "Report card not found"}, status_code=404)

    cursor.execute(
        """
        UPDATE report_card_uploads
        SET extracted_subjects = ?, extracted_gwa = ?, ocr_status = 'verified',
            ocr_error = NULL, processed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (subjects_text, gwa, upload_id),
    )
    if report[0]:
        recommendation = _configured_recommendations(
            subjects_text,
            report[1] or "",
            report[2] or "",
        )
        cursor.execute(
            """
            UPDATE student_profiles
            SET gwa = ?, grades = ?, subjects = ?, recommendation = ?
            WHERE id = ?
            """,
            (gwa, grades_text, subjects_text, json.dumps(recommendation), report[0]),
        )
        if recommendation:
            cursor.execute(
                "INSERT INTO course_recommendation_history (profile_id, recommendations) VALUES (?, ?)",
                (report[0], json.dumps(recommendation)),
            )
    conn.commit()
    conn.close()
    _record_admin_activity(req.session, "report_card_grades_verified", target_label=f"report-card:{upload_id}", details=f"subject_count={len(subjects)}")
    return JSONResponse({"success": True, "message": "Extracted grades saved and verified."})


@fastapi_route("/admin/report_cards/{upload_id}/flag", methods=["POST"])
async def admin_flag_report_card(req, upload_id: str):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid report card id"}, status_code=400)

    data = await req.json()
    is_flagged = data.get("is_flagged") is True
    note = str(data.get("note") or "").strip()[:500] if is_flagged else ""
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE report_card_uploads uploads
        SET is_flagged = ?, flag_note = ?
        WHERE id = ? AND EXISTS (
            SELECT 1 FROM users
            WHERE users.id = uploads.user_id AND COALESCE(users.role, 'student') = 'student'
        )
        RETURNING id
        """,
        (is_flagged, note or None, upload_id),
    )
    updated = cursor.fetchone()
    conn.commit()
    conn.close()
    if not updated:
        return JSONResponse({"success": False, "message": "Report card not found"}, status_code=404)
    _record_admin_activity(req.session, "report_card_flagged" if is_flagged else "report_card_flag_cleared", target_label=f"report-card:{upload_id}", details=note)
    return JSONResponse({"success": True, "is_flagged": is_flagged})


@fastapi_route("/admin/report_cards/{upload_id}/reprocess", methods=["POST"])
def admin_reprocess_report_card(req, upload_id: str):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid report card id"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT uploads.file_data, uploads.original_filename
        FROM report_card_uploads uploads
        JOIN users ON users.id = uploads.user_id
        WHERE uploads.id = ? AND COALESCE(users.role, 'student') = 'student'
        """,
        (upload_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return JSONResponse({"success": False, "message": "Report card not found"}, status_code=404)

    try:
        processed = _process_report_card_bytes(row[0], row[1])
    except Exception as exc:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE report_card_uploads SET ocr_status = 'failed', ocr_error = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(exc)[:1000], upload_id),
        )
        conn.commit()
        conn.close()
        _record_admin_activity(req.session, "report_card_reprocess_failed", target_label=f"report-card:{upload_id}", details=str(exc)[:500])
        return JSONResponse({"success": False, "message": f"OCR reprocessing failed: {exc}"}, status_code=500)

    parsed = processed["parsed"]
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE report_card_uploads
        SET ocr_status = ?, student_name = ?, student_number = ?, extracted_subjects = ?,
            extracted_gwa = ?, ocr_error = NULL, processed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            processed["ocr_status"],
            parsed.get("student_name") or "",
            parsed.get("student_no") or "",
            processed["structured_text"],
            processed["extracted_gwa"],
            upload_id,
        ),
    )
    conn.commit()
    conn.close()
    _record_admin_activity(req.session, "report_card_reprocessed", target_label=f"report-card:{upload_id}", details=f"ocr_status={processed['ocr_status']}")
    return JSONResponse({"success": True, "message": "Report card reprocessed.", "ocr_status": processed["ocr_status"]})


@fastapi_route("/admin/report_cards/{upload_id}", methods=["DELETE"])
def admin_delete_report_card(req, upload_id: str):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid report card id"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM report_card_uploads uploads
        WHERE id = ? AND EXISTS (
            SELECT 1 FROM users
            WHERE users.id = uploads.user_id AND COALESCE(users.role, 'student') = 'student'
        )
        RETURNING id
        """,
        (upload_id,),
    )
    deleted = cursor.fetchone()
    conn.commit()
    conn.close()
    if not deleted:
        return JSONResponse({"success": False, "message": "Report card not found"}, status_code=404)
    _record_admin_activity(req.session, "report_card_deleted", target_label=f"report-card:{upload_id}")
    return JSONResponse({"success": True, "message": "Report card deleted."})


@fastapi_route("/admin/report_cards/{upload_id}", methods=["GET"])
def admin_get_report_card(req, upload_id: str):
    actor_id = req.session.get("user_id")
    if not actor_id:
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Invalid report card id"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT uploads.content_type, uploads.file_data,
               COALESCE(actor.role, 'student'), COALESCE(actor.is_active, TRUE),
               COALESCE(actor.must_change_password, FALSE)
        FROM report_card_uploads uploads
        JOIN users target ON target.id = uploads.user_id
        LEFT JOIN users actor ON actor.id = ?
        WHERE uploads.id = ? AND COALESCE(target.role, 'student') = 'student'
        """,
        (actor_id, upload_id),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return JSONResponse({"success": False, "message": "Report card not found"}, status_code=404)

    if not row[3] or row[2] not in (ROLE_ADMIN, ROLE_SEMI_ADMIN) or row[4]:
        conn.close()
        req.session.clear()
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)

    req.session["role"] = row[2]
    req.session["is_admin"] = True
    _record_admin_activity(req.session, "report_card_viewed", target_label=f"report-card:{upload_id}", connection=conn)
    conn.close()
    content_type = row[0] if row[0] in {"image/jpeg", "image/png", "image/webp"} else "application/octet-stream"
    return Response(
        content=row[1],
        media_type=content_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
        },
    )


api_app = FastAPI(
    title="PathFinder API",
    version="1.0.0",
    description="Versioned JSON and report-card API for the PathFinder web application.",
    docs_url="/docs",
    redoc_url="/redoc",
)


@api_app.exception_handler(CourseCatalogSchemaMissing)
async def course_catalog_schema_missing_handler(request, exc):
    return JSONResponse({"success": False, "message": str(exc)}, status_code=503)


def _fastapi_handler(handler, path_parameters=()):
    if inspect.iscoroutinefunction(handler):
        async def endpoint(request: FastAPIRequest, **route_values):
            values = {name: request.path_params[name] for name in path_parameters}
            return await handler(request, **values)
    else:
        def endpoint(request: FastAPIRequest, **route_values):
            values = {name: request.path_params[name] for name in path_parameters}
            return handler(request, **values)

    endpoint.__name__ = f"api_{handler.__name__}"
    endpoint.__doc__ = handler.__doc__ or f"Calls the {handler.__name__} application handler."
    endpoint.__signature__ = inspect.Signature(
        [
            inspect.Parameter(
                "request",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=FastAPIRequest,
            ),
            *[
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=str,
                )
                for name in path_parameters
            ],
        ]
    )
    return endpoint


_FASTAPI_ENDPOINTS = (
    ("/register", ["POST"], register, ()),
    ("/login", ["POST"], login, ()),
    ("/admin/recovery-email", ["GET"], admin_recovery_email, ()),
    ("/admin/recovery-email", ["POST"], admin_set_recovery_email, ()),
    ("/admin/forgot-password", ["POST"], admin_forgot_password, ()),
    ("/admin/verify-recovery", ["POST"], admin_verify_recovery, ()),
    ("/admin/reset-password", ["POST"], admin_reset_password, ()),
    ("/generate_recommendations", ["POST"], generate_recommendations, ()),
    ("/system-settings/public", ["GET"], public_system_settings, ()),
    ("/admin/users", ["GET"], admin_get_users, ()),
    ("/admin/system-settings", ["GET"], admin_get_system_settings, ()),
    ("/admin/system-settings", ["POST"], admin_save_system_settings, ()),
    ("/admin/courses", ["GET"], admin_list_courses, ()),
    ("/admin/courses", ["POST"], admin_create_course, ()),
    ("/admin/courses/{course_id}", ["PUT"], admin_update_course, ("course_id",)),
    ("/admin/courses/{course_id}", ["DELETE"], admin_deactivate_course, ("course_id",)),
    ("/admin/courses/{course_id}/activate", ["POST"], admin_activate_course, ("course_id",)),
    ("/admin/activity", ["GET"], admin_get_activity, ()),
    ("/admin/activity/export", ["POST"], admin_export_activity, ()),
    ("/admin/update_user", ["POST"], admin_update_user, ()),
    ("/admin/delete_user", ["POST"], admin_delete_user, ()),
    ("/admin/remove_admin_account", ["POST"], admin_remove_admin_account, ()),
    ("/admin/create_coordinator", ["POST"], admin_create_coordinator, ()),
    ("/admin/create_admin_user", ["POST"], admin_create_user, ()),
    ("/admin/update_admin_role", ["POST"], admin_update_admin_role, ()),
    ("/admin/student_profiles", ["GET"], admin_get_student_profiles, ()),
    ("/admin/update_student_profile", ["POST"], admin_update_student_profile, ()),
    ("/admin/students/api", ["GET"], admin_get_students_api, ()),
    ("/admin/students/{user_id}", ["GET"], admin_get_student_detail, ("user_id",)),
    ("/admin/deactivate_student", ["POST"], admin_deactivate_student, ()),
    ("/admin/delete_student", ["POST"], admin_delete_student, ()),
    ("/admin/stats", ["GET"], admin_stats, ()),
    ("/admin/reports/generate", ["POST"], admin_generate_report, ()),
    ("/admin/reports/download", ["POST"], admin_download_report, ()),
    ("/admin/recommendations", ["GET"], admin_recommendation_history, ()),
    ("/admin/recommendations/{profile_id}/recalculate", ["POST"], admin_recalculate_recommendations, ("profile_id",)),
    ("/save_profile", ["POST"], save_profile, ()),
    ("/update_account", ["POST"], update_account, ()),
    ("/admin/change_password", ["POST"], admin_change_password, ()),
    ("/recommend_anonymous", ["POST"], recommend_anonymous, ()),
    ("/get_profile", ["GET"], get_profile, ()),
    ("/email_results", ["POST"], email_results, ()),
    ("/upload_profile_picture", ["POST"], upload_profile_picture, ()),
    ("/get_profile_picture", ["GET"], get_profile_picture, ()),
    ("/profile-image/{user_id}/{version}", ["GET"], get_profile_image, ("user_id", "version")),
    ("/ocr_report_card", ["POST"], ocr_report_card, ()),
    ("/admin/report_cards", ["GET"], admin_list_report_cards, ()),
    ("/admin/report_cards/{upload_id}/edit", ["POST"], admin_edit_report_card, ("upload_id",)),
    ("/admin/report_cards/{upload_id}/flag", ["POST"], admin_flag_report_card, ("upload_id",)),
    ("/admin/report_cards/{upload_id}/reprocess", ["POST"], admin_reprocess_report_card, ("upload_id",)),
    ("/admin/report_cards/{upload_id}", ["DELETE"], admin_delete_report_card, ("upload_id",)),
    ("/admin/report_cards/{upload_id}", ["GET"], admin_get_report_card, ("upload_id",)),
)

for api_path, methods, handler, path_parameters in _FASTAPI_ENDPOINTS:
    api_app.add_api_route(
        api_path,
        _fastapi_handler(handler, path_parameters),
        methods=methods,
        response_model=None,
        name=f"api_{handler.__name__}",
    )

app.mount("/api/v1", api_app)
app.middleware_stack = None

def _ocr_startup_self_test():
    try:
        from ocr.docling_service import run_ocr_self_test
        result = run_ocr_self_test()
        status = "OK" if result["ok"] else "FAILED (OCR read no text)"
        print(
            f"[OCR] Self-test {status}: engine={result['engine']}, "
            f"characters={result['characters']}, seconds={result['seconds']}",
            flush=True,
        )
    except Exception as exc:
        print(f"[OCR] Self-test FAILED: {exc}", flush=True)


# Main entry point for the FastHTML frontend and mounted FastAPI backend.
def main():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_database()
    threading.Thread(target=_ocr_startup_self_test, name="ocr-self-test", daemon=True).start()
    port = int(os.getenv("PORT", "5000"))
    serve(port=port, reload=False, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
