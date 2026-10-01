# FastAPI application configuration and imports
import json
import html
import inspect
import math
import os
import re
import tempfile
import time
from json import dumps
from datetime import datetime
from difflib import SequenceMatcher

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
from starlette.middleware import Middleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

load_dotenv()

from ocr import scan_report_card_docling
from ocr.docling_service import _subjects_from_table
from ocr.parser import normalize_ocr_text, parse_report_card_structure


#Optional Nearest Neighbor Model for Course Recommendations
try:
    from sklearn.neighbors import NearestNeighbors
except Exception:
    NearestNeighbors = None

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
SYSTEM_SETTING_STRANDS = ("STEM", "ABM", "HUMSS", "GAS", "TVL", "ICT", "SPORTS", "ARTS_DESIGN")
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

_NEAREST_NEIGHBOR_MODEL = None
_COURSE_TRAINING_DATA_CACHE = None

UNIVERSITY_COURSES = (
    ("BS in Medical Technology", "Medical Technology"),
    ("BS in Nursing", "Nursing"),
    ("BS in Occupational Therapy", "Occupational Therapy"),
    ("BS in Pharmacy", "Pharmacy"),
    ("BS in Physical Therapy", "Physical Therapy"),
    ("BS in Radiologic Technology", "Radiologic Technology"),
    ("BS in Respiratory Therapy", "Respiratory Therapy"),
    ("BS in Architecture", "Architecture"),
    ("Bachelor of Arts in Communication", "Communication"),
    ("Bachelor of Arts major in Political Science", "Political Science"),
    ("Bachelor of Arts in Psychology", "Psychology"),
    ("Bachelor of Arts in Multimedia Arts", "Multimedia Arts"),
    ("Bachelor of Science in Psychology", "Psychology"),
    ("BS in Aircraft Maintenance and Technology", "Mechanical Engineering"),
    ("BS in Aviation Electronics Technology", "Electrical Engineering"),
    ("Aircraft Maintenance Technology", "Mechanical Engineering"),
    ("Aviation Electronics Technology", "Electrical Engineering"),
    ("BS in Accountancy", "Accountancy"),
    ("BS in Business Administration", "Business Administration"),
    ("BS in Business Administration major in Human Resource Management", "Human Resource Management"),
    ("BS in Business Administration major in Marketing Management", "Marketing Management"),
    ("BS in Entrepreneurship", "Entrepreneurship"),
    ("BS in Criminology", "Criminology"),
    ("BS in Aeronautical Engineering", "Aerospace Engineering"),
    ("BS in Civil Engineering", "Civil Engineering"),
    ("BS in Mechanical Engineering", "Mechanical Engineering"),
    ("BS in Computer Engineering", "Computer Engineering"),
    ("BS in Digital Engineering", "Data Engineering"),
    ("BS in Electrical Engineering", "Electrical Engineering"),
    ("BS in Electronics Engineering major in Biomedical Engineering", "Biomedical Engineering"),
    ("BS in Industrial Engineering", "Industrial Engineering"),
    ("BS in Information Technology with Specialization in Game Development", "Game Development"),
    ("BS in Computer Science with Specialization in Data Science", "Data Science"),
    ("Bachelor of Library and Information Science", "Library and Information Science"),
    ("BS in Tourism Management", "Tourism Management"),
    ("BS in Hospitality Management", "Hospitality Management"),
    ("BS in Marine Transportation", "Marine Transportation"),
    ("BS in Marine Engineering", "Marine Engineering"),
    ("BS in Naval Architecture and Marine Engineering", "Naval Architecture"),
    ("Bachelor of Early Childhood Education", "Early Childhood Education"),
    ("Bachelor of Elementary Education", "Early Childhood Education"),
    ("Bachelor of Physical Education", "Physical Education"),
    ("Bachelor of Secondary Education", "Secondary Education - Mathematics"),
    ("Bachelor of Special Needs Education", "Special Needs Education"),
)

COURSE_DESCRIPTION_OVERRIDES = {
    "BS in Aircraft Maintenance and Technology": "Study aircraft inspection, maintenance, and repair for safe aviation operations.",
    "Aircraft Maintenance Technology": "Study aircraft inspection, maintenance, and repair for safe aviation operations.",
    "BS in Aviation Electronics Technology": "Study aircraft electrical systems, avionics, and aviation electronics maintenance.",
    "Aviation Electronics Technology": "Study aircraft electrical systems, avionics, and aviation electronics maintenance.",
    "Bachelor of Elementary Education": "Prepare to teach and support learners across elementary school subjects.",
    "Bachelor of Secondary Education": "Prepare to teach and support learners at the secondary school level.",
    "BS in Digital Engineering": "Apply digital tools, data, and engineering methods to design technical systems.",
    "BS in Naval Architecture and Marine Engineering": "Study ship design, vessel structures, and marine engineering systems.",
}

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

    return f"/static/profile_pictures/{text}"

#Session Profile Image Handling
def _set_session_profile_image(sess, profile_picture_value):
    sess["profile_picture"] = (profile_picture_value or "").strip()
    sess["profile_image"] = _profile_image_from_value(profile_picture_value)

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
        raise ValueError("Select at least one valid academic strand.")
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


#Course Training Data Retrieval (from the database)
def _course_training_data():
    global _COURSE_TRAINING_DATA_CACHE
    if _COURSE_TRAINING_DATA_CACHE is not None:
        return _COURSE_TRAINING_DATA_CACHE

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT course, features, description FROM course_training_data ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    profiles = {row[0]: {"features": row[1], "description": row[2]} for row in rows}
    catalog = []
    for name, source in UNIVERSITY_COURSES:
        profile = profiles.get(name) or profiles.get(source)
        if profile:
            catalog.append({"course": name, "features": profile["features"],
                            "description": COURSE_DESCRIPTION_OVERRIDES.get(name, profile["description"])})
    _COURSE_TRAINING_DATA_CACHE = catalog
    return _COURSE_TRAINING_DATA_CACHE

#Password Hashing and Verification
def _hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _password_policy_error(password):
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        return "Password must be at least 8 characters long. " + PASSWORD_POLICY_MESSAGE
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return "Password is too long for secure hashing. " + PASSWORD_POLICY_MESSAGE
    if not any(character.isupper() for character in password):
        return "Password needs an uppercase letter. " + PASSWORD_POLICY_MESSAGE
    if not any(not character.isalnum() and not character.isspace() for character in password):
        return "Password needs a symbol. " + PASSWORD_POLICY_MESSAGE
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
        if any(token in lowered for token in ["math", "algebra", "calculus", "statistics", "trigonometry", "geometry", "probability", "arithmetic", "precalculus", "equations", "analytics"]):
            score_map["math"].append(grade)
        elif any(token in lowered for token in ["science", "physics", "chemistry", "biology", "environment", "agriculture", "health", "anatomy", "physiology", "botany", "zoology", "geology", "astronomy", "ecology", "biochemistry", "microbiology", "meteorology", "oceanography", "earth science", "life science", "natural science"]):
            score_map["science"].append(grade)
        elif any(token in lowered for token in ["english", "communication", "speech", "writing", "literature", "oral", "reading"]):
            score_map["english"].append(grade)
        elif any(token in lowered for token in ["program", "computer", "ict", "information", "technology", "software", "database", "digital", "web", "network", "coding"]):
            score_map["technology"].append(grade)
        elif any(token in lowered for token in ["business", "accounting", "management", "marketing", "economics", "entrepreneur", "finance", "tourism", "hospitality", "public administration", "legal"]):
            score_map["business"].append(grade)
        elif any(token in lowered for token in ["history", "sociology", "psych", "political", "social", "criminology", "education", "culture", "governance", "filipino"]):
            score_map["social"].append(grade)

    return score_map

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

#Cleaning Subjects for Recommendation (removes instructor names and irrelevant text)
def _clean_subjects_for_recommendation(subjects_text):
    subject_terms = {
        "math", "mathematics", "science", "communication", "education", "technology",
        "health", "english", "filipino", "literature", "person", "research", "entrepreneur",
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
    for token in re.findall(r"[0-9]{1,3}(?:\.[0-9]+)?", grades_text):
        try:
            values.append(float(token))
        except (TypeError, ValueError):
            continue
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
    return ", ".join(str(g).rstrip("0").rstrip(".") for g in grades)

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
                label = re.sub(r"\s+\d{1,3}(?:\.\d+)?$", "", label).strip()
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

#Core Feature Weights for Weighted Calculations
def _weighted_features(features):
    return [value * math.sqrt(weight) for value, weight in zip(features, CORE_FEATURE_WEIGHTS)]

#Weighted Vector Distance Calculation (applies core feature weights to the distance computation)
def _weighted_vector_distance(a, b):
    return math.sqrt(
        sum(weight * (x - y) ** 2 for weight, x, y in zip(CORE_FEATURE_WEIGHTS, a, b))
    )


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
def categorize_course(course_name, description=""):
    name = (course_name or "").lower()
    desc = (description or "").lower()

    if any(tok in name for tok in ("nurse", "health", "medical", "pharm", "midwife", "clinical", "medtech", "radiologic", "respiratory", "nutrition", "dietetic", "biology", "biological")) or any(tok in desc for tok in ("health", "patient", "clinical", "nurse", "medical", "laboratory", "nutrition", "therapy", "biology", "biological science")):
        return "Allied Health"
    if any(tok in name for tok in ("computer", "information technology", "software", "ict", "bsit", "bscs", "data", "systems", "programming", "digital")) or any(tok in desc for tok in ("computer", "software", "programming", "information technology", "ict", "systems", "database", "digital")):
        return "Computer Studies"
    if any(tok in name for tok in ("engineer", "civil", "mechanical", "electrical", "chemical", "architecture", "construction")) or any(tok in desc for tok in ("engineering", "infrastructure", "construction", "architecture")):
        return "Engineering"
    if any(tok in name for tok in ("agriculture", "fisheries", "farm", "crop", "soil", "environmental science", "ecosystem", "sustainability", "forest")) or any(tok in desc for tok in ("agriculture", "fisheries", "farm", "crop", "soil", "environmental science", "ecosystem", "sustainability", "forest")):
        return "Agriculture"
    if any(tok in name for tok in ("business", "management", "account", "accounting", "marketing", "finance", "administration", "economics", "entrepreneur")) or any(tok in desc for tok in ("business", "management", "accounting", "finance", "marketing", "operations")):
        return "Business & Management"
    if any(tok in name for tok in ("education", "teacher", "teaching", "psychology", "social", "humanities", "communication")) or any(tok in desc for tok in ("education", "teaching", "psychology", "social", "communication", "humanities")):
        return "Social Sciences & Education"
    if any(tok in name for tok in ("tourism", "hotel", "hospitality", "travel", "culinary")) or any(tok in desc for tok in ("tourism", "hospitality", "travel", "service")):
        return "Hospitality & Tourism"
    if any(tok in name for tok in ("law", "political", "public administration", "governance", "criminology")) or any(tok in desc for tok in ("law", "public service", "governance", "politics")):
        return "Public Service & Governance"
    if any(tok in name for tok in ("art", "design", "architecture", "creative", "media")) or any(tok in desc for tok in ("design", "creative", "art", "visual")):
        return "Arts & Design"
    return "Other"

#  Infer Academic Strand Based on Course Name (assigns a strand like STEM, ABM, HUMSS, TVL, GAS, or Other) 
# help in recommending suitable courses for students based on their academic track
def _infer_strand(course_name=""):
    course_name = (course_name or "").lower()
    if any(tok in course_name for tok in ("engineering", "science", "math")):
        return "STEM"
    if any(tok in course_name for tok in ("business", "accounting", "management", "marketing", "economics")):
        return "ABM"
    if any(tok in course_name for tok in ("psychology", "education", "communication", "social", "humanities")):
        return "HUMSS"
    if any(tok in course_name for tok in ("nurse", "health", "medical", "hospital", "clinical", "care")):
        return "TVL"
    if any(tok in course_name for tok in ("gas", "general", "service", "tourism", "hospitality", "arts")):
        return "GAS"
    if any(tok in course_name for tok in ("computer", "information", "technology", "it")):
        return "ICT"
    return "Other"

# Infer Student's Strengths from Feature Scores (returns top 3 strengths with scores >= 75)
def _infer_strengths_from_features(features):
    ranked = sorted(zip(CATEGORY_NAMES, features), key=lambda t: t[1], reverse=True)
    return [name for name, value in ranked if value >= 75][:3]

# Generate Reason for Course Match (explains why a course is suitable based on category, student's strengths, and academic strand)
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
    }.get(category, "your overall academic profile")
    strength_text = ", ".join(strongest[:2]) if strongest else "your balanced grades"
    strand_text = f" It is also compatible with your {strand} strand." if strand else ""
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
    for item in decoded:
        if not isinstance(item, dict):
            continue
        course_name = item.get("course")
        if not _valid_recommendation_course_name(course_name) or course_name not in {name for name, _ in UNIVERSITY_COURSES}:
            continue
        valid.append({
            "course": course_name,
            "description": item.get("description") or "",
            "reason": item.get("reason") or "",
            "category": item.get("category") or categorize_course(course_name, item.get("description") or ""),
            "confidence": item.get("confidence", 0),
            "core_grade_fit": item.get("core_grade_fit", 0),
            "strand_grade_based": bool(item.get("strand_grade_based", False)),
        })
    return valid

# Build Course Recommendation Model (constructs a nearest neighbor model based on course features) Machine Learning Approach
def _build_course_model():
    global _NEAREST_NEIGHBOR_MODEL
    if NearestNeighbors is None:
        return None
    if _NEAREST_NEIGHBOR_MODEL is not None:
        return _NEAREST_NEIGHBOR_MODEL

    training_data = _course_training_data()
    feature_matrix = np.asarray([_weighted_features(sample["features"]) for sample in training_data], dtype=float)
    model = NearestNeighbors(n_neighbors=min(8, len(training_data)), metric="euclidean")
    model.fit(feature_matrix)
    _NEAREST_NEIGHBOR_MODEL = model
    return model

# Recommend Courses Based on Student's Academic Profile (uses the nearest neighbor model and feature analysis to suggest suitable courses)
def recommend_course(subjects_text, current_course="", strand=""):
    training_data = _course_training_data()
    features = _build_feature_vector(subjects_text)
    if not any(feature > 0 for feature in features):
        return []

    strongest = _infer_strengths_from_features(features)
    reason = "Your grades show a balanced academic profile, which fits the most similar historical pattern." if not strongest else f"Your strongest areas are {', '.join(strongest)}."
    
    # Build the course recommendation model and generate recommendations based on the student's features
    model = _build_course_model()
    recommendations = []
    if model is not None:
        try:
            distances, indices = model.kneighbors(np.asarray([_weighted_features(features)], dtype=float), n_neighbors=min(8, len(training_data)))
            for distance, index in zip(distances[0], indices[0]):
                sample = training_data[int(index)]
                category = categorize_course(sample["course"], sample.get("description", ""))
                confidence = max(1.0, 100.0 - (distance * 10.0))
                recommendations.append({
                    "course": sample["course"],
                    "description": sample.get("description", ""),
                    "reason": _course_match_reason(sample["course"], category, strongest, strand),
                    "category": category,
                    "confidence": round(confidence, 2),
                    "core_grade_fit": round(_core_grade_fit(features, sample["features"]), 2),
                })
        except Exception:
            pass
    # If an exception occurs during model-based recommendation, it is silently ignored.
    if not recommendations:
        distances = []
        for sample in training_data:
            distance = _weighted_vector_distance(features, sample["features"])
            distances.append((distance, sample["course"], sample.get("description", ""), sample["features"]))
        # Sort the distances to find the nearest courses
        nearest = sorted(distances, key=lambda item: item[0])[:8]
        for distance, course_name, description, course_features in nearest:
            category = categorize_course(course_name, description)
            confidence = max(1.0, 100.0 - distance)
            recommendations.append({
                "course": course_name,
                "description": description,
                "reason": _course_match_reason(course_name, category, strongest, strand),
                "category": category,
                "confidence": round(confidence, 2),
                "core_grade_fit": round(_core_grade_fit(features, course_features), 2),
            })
    # Group the recommended courses by strand and adjust confidence based on strand fit
    strand_groups = {
        "stem": {"Engineering", "Allied Health"},
        "ict": {"Computer Studies"},
        "abm": {"Business & Management", "Hospitality & Tourism"},
        "humss": {"Social Sciences & Education", "Public Service & Governance", "Arts & Design"},
        "tvl": {"Computer Studies", "Allied Health", "Hospitality & Tourism", "Agriculture"},
        "gas": {"Social Sciences & Education", "Business & Management", "Hospitality & Tourism", "Arts & Design"},
    }
    strand_key = (strand or "").strip().lower()
    preferred_groups = strand_groups.get(strand_key, set())
    strand_course_terms = {
        "stem": ("engineering", "science", "biology", "medical", "pharmacy", "technology"),
        "ict": ("computer", "information", "technology", "it"),
        "abm": ("business", "account", "marketing", "management", "finance", "economics", "entrepreneur", "hospitality", "tourism"),
        "humss": ("psychology", "education", "communication", "political", "criminology", "public administration", "social", "legal", "tourism"),
        "tvl": ("technology", "computer", "nursing", "medical", "pharmacy", "hospitality", "tourism", "agriculture", "fisheries"),
        "gas": ("communication", "business", "education", "hospitality", "tourism", "arts", "psychology"),
    }
    preferred_terms = strand_course_terms.get(strand_key, ())

    def matches_strand(item):
        course_text = f"{item['course']} {item.get('description', '')}".lower()
        return (
            (preferred_groups and item["category"] in preferred_groups)
            or (preferred_terms and any(term in course_text for term in preferred_terms))
        )

    # Ensure the final results contain a strand-aligned course even when it is
    # outside the nearest-neighbor sample selected by the grade profile.
    if strand_key and preferred_groups and not any(matches_strand(item) for item in recommendations):
        strand_candidates = [sample for sample in training_data if matches_strand({
            "course": sample["course"],
            "description": sample.get("description", ""),
            "category": categorize_course(sample["course"], sample.get("description", "")),
        })]
        if strand_candidates:
            sample = min(strand_candidates, key=lambda item: _weighted_vector_distance(features, item["features"]))
            category = categorize_course(sample["course"], sample.get("description", ""))
            distance = _weighted_vector_distance(features, sample["features"])
            recommendations.append({
                "course": sample["course"],
                "description": sample.get("description", ""),
                "reason": _course_match_reason(sample["course"], category, strongest, strand),
                "category": category,
                "confidence": round(max(1.0, 100.0 - distance), 2),
                "core_grade_fit": round(_core_grade_fit(features, sample["features"]), 2),
            })

    for item in recommendations:
        course_text = f"{item['course']} {item.get('description', '')}".lower()
        strand_fit = 0
        if preferred_groups and item["category"] in preferred_groups:
            item["confidence"] += 12
            strand_fit = 1
            item["reason"] += f" It also aligns with the {strand.upper()} strand."
        if preferred_terms and any(term in course_text for term in preferred_terms):
            item["confidence"] += 8
            strand_fit = 1
            if "aligns with" not in item["reason"]:
                item["reason"] += f" It is closely related to the {strand.upper()} strand."
        item["strand_grade_score"] = round(
            (item.get("core_grade_fit", 0) * 0.7) + (100.0 if strand_fit else 0.0) * 0.3,
            2,
        )
        item["confidence"] += item["core_grade_fit"] * 0.15
        item["confidence"] = round(min(100.0, item["confidence"]), 2)
    recommendations = sorted(
        recommendations,
        key=lambda item: (item.get("strand_grade_score", 0), item["confidence"]),
        reverse=True,
    )
    if strand_key and preferred_groups:
        strand_match = next((item for item in recommendations if matches_strand(item)), None)
        if strand_match and not any(matches_strand(item) for item in recommendations[:5]):
            recommendations = recommendations[:4] + [strand_match]
            strand_match["strand_grade_based"] = True

    if recommendations and strand:
        recommendations[0]["strand_grade_based"] = True
        recommendations[0]["reason"] += f" This top match combines your {strand.upper()} strand with your Math, Science, and English grade profile."
    return recommendations[:5]


def _recommendation_limit():
    limit = _get_system_settings().get("recommendation_limit", 3)
    try:
        limit = max(1, min(5, int(limit)))
    except (TypeError, ValueError):
        limit = 3
    return limit


def _limit_recommendations(payload, limit=None):
    limit = _recommendation_limit() if limit is None else limit
    return _sanitize_recommendations(payload)[:limit]


def _configured_recommendations(subjects_text, current_course="", strand=""):
    limit = _recommendation_limit()
    recommendations = recommend_course(subjects_text, current_course, strand)
    return _sanitize_recommendations(json.dumps(recommendations))[:limit]

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
    student_scores = _extract_subject_scores(subjects_text)

    subject_breakdown = []
    for subject in CATEGORY_NAMES:
        student_value = round(_average(student_scores.get(subject, [])), 2) if student_scores.get(subject) else 0.0
        course_value = course_profile["by_subject"].get(subject, 0.0)
        delta = round(student_value - course_value, 2)
        status = "above average" if delta >= 0 else "below average"
        subject_breakdown.append({
            "subject": subject,
            "student": student_value,
            "course": course_value,
            "difference": delta,
            "status": status,
        })

    student_overall = round(_average([item["student"] for item in subject_breakdown]), 2) if subject_breakdown else 0.0
    course_overall = course_profile["overall_average"]
    overall_gap = round(student_overall - course_overall, 2)
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

    narrative = (
        "You are above the recommended course average overall."
        if overall_gap >= 0
        else "You are below the recommended course average overall."
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
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
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
            if isinstance(saved_strands, list) and "ICT" not in saved_strands:
                saved_settings["available_strands"] = [*saved_strands, "ICT"]
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
            password_error = _password_policy_error(ADMIN_PASSWORD)
            if password_error:
                conn.close()
                raise RuntimeError(f"BOOTSTRAP_ADMIN_PASSWORD does not meet policy: {password_error}")
            cursor.execute(
                "INSERT INTO users (name, email, password_hash, profile_picture, role) VALUES (?, ?, ?, ?, ?)",
                (BOOTSTRAP_ADMIN_NAME, ADMIN_USERNAME, _hash_password(ADMIN_PASSWORD), "default.svg", ROLE_SUPER_ADMIN),
            )
            conn.commit()

    conn.close()
# Check if the current session belongs to an admin user
def _is_admin_session(sess, allow_password_change=False):
    user_id = sess.get("user_id")
    if not user_id:
        return False
    try:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE) FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
    except Exception:
        return False
    if not row or not row[1] or row[0] not in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
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

# Render a template with the current session context and additional context variables
def _render(req, template_name, **ctx):
    sess = req.session
    if "user_id" in sess:
        if sess.get("role") in (ROLE_ADMIN, ROLE_SEMI_ADMIN) and not sess.get("admin_ip"):
            sess["admin_ip"] = req.client.host if req.client else ""
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT profile_picture FROM users WHERE id = ?", (sess["user_id"],))
        row = cursor.fetchone()
        conn.close()
        if row:
            _set_session_profile_image(sess, row[0])
    # Render the template with the session and additional context
    body = TEMPLATES.get_template(template_name).render(
        name=sess.get("name", "User"),
        email=sess.get("email", "User"),
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
                "/admin/force-password-change",
                "/admin/change_password",
                "/api/v1/admin/change_password",
                "/admin/logout",
                "/logout",
            }
            if session.get("must_change_password") and path not in allowed_paths and not path.startswith("/static/"):
                headers = {key.lower(): value for key, value in scope.get("headers", [])}
                if b"text/html" in headers.get(b"accept", b""):
                    response = RedirectResponse("/admin/force-password-change", status_code=303)
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


# Define the route for the home page
@fastapi_route("/home", methods=["GET"])
def home(req):
    sess = req.session
    return _render(
        req,
        "StudentManagement.html",
        home_mode=True,
        is_guest=bool(sess.get("is_guest")),
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
    if not recommendations:
        return JSONResponse({"success": False, "message": "No course matches could be generated from the saved grades."})

    payload = json.dumps(recommendations)
    sess["latest_recommendations"] = payload
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
    return JSONResponse({"success": True, "count": len(recommendations)})


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
            """,
            (name, email, hashed_password, "student", "default.svg"),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"success": True, "message": "Account Created"})
    except UniqueViolation:
        return JSONResponse({"success": False, "message": "Email already exists"})


# Define the route for user login
@fastapi_route("/login", methods=["POST"])
async def login(req):
    data = await req.json()
    email = data.get("email", "")
    password = data.get("password", "")

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, name, password_hash, COALESCE(role, 'student'), COALESCE(profile_picture, ''), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE)
        FROM users
        WHERE email = ?
        """,
        (email,),
    )
    result = cursor.fetchone()
    conn.close()

    if not result:
        return JSONResponse({"success": False, "message": "Email not found"})

    user_id, name, stored_hash, role, profile_picture, is_active, must_change_password = result

    if not is_active:
        return JSONResponse({"success": False, "message": "This account has been deactivated."})

    try:
        valid = _check_password(stored_hash, password)
    except Exception:
        valid = False

    if not valid:
        return JSONResponse({"success": False, "message": "Incorrect Password"})

    conn = _db_conn()
    conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    sess = req.session
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email
    sess["role"] = role
    sess["must_change_password"] = bool(must_change_password)
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or email
        sess["admin_ip"] = req.client.host if req.client else ""
    _set_session_profile_image(sess, profile_picture)
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "login", target_label=email)

    resp = {"success": True}
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        resp["admin"] = True
    if role == ROLE_SEMI_ADMIN:
        resp["semi_admin"] = True
        resp["must_change_password"] = bool(must_change_password)
    return JSONResponse(resp)


@fastapi_route("/logout", methods=["GET"])
def logout(req):
    _record_admin_activity(req.session, "logout")
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
    if not _is_admin_session(req.session):
        return RedirectResponse("/", status_code=302)
    return _render(
        req,
        "AdminDashboard.html",
        can_manage_students=_is_full_admin_session(req.session),
        can_view_admin_data=True,
        system_settings=_get_system_settings(),
    )


@fastapi_route("/admin/force-password-change", methods=["GET"])
def admin_force_password_change(req):
    user_id = req.session.get("user_id")
    if not user_id:
        return RedirectResponse("/", status_code=303)
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE) FROM users WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row or not row[1] or row[0] != ROLE_SEMI_ADMIN:
        req.session.clear()
        return RedirectResponse("/", status_code=303)
    if not row[2]:
        req.session["must_change_password"] = False
        return RedirectResponse("/admin/dashboard", status_code=303)
    req.session["must_change_password"] = True
    req.session["role"] = row[0]
    req.session["is_admin"] = True
    return _render(req, "ForcePasswordChange.html")


@fastapi_route("/system-settings/public", methods=["GET"])
def public_system_settings(req):
    settings = _get_system_settings()
    return JSONResponse({
        "success": True,
        "settings": {
            "university_name": settings["university_name"],
            "school_year": settings["school_year"],
            "available_strands": settings["available_strands"],
        },
    })


# Define the route for fetching all users (admin only)
@fastapi_route("/admin/users", methods=["GET"])
def admin_get_users(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, profile_picture, COALESCE(role, 'student'), COALESCE(is_active, TRUE), last_login_at FROM users WHERE COALESCE(role, 'student') IN (?, ?) ORDER BY id DESC", (ROLE_ADMIN, ROLE_SEMI_ADMIN))
    rows = cursor.fetchall()
    conn.close()
    users = [{"id": r[0], "name": r[1], "email": r[2], "profile_picture": r[3], "role": r[4], "is_active": bool(r[5]), "last_login_at": r[6].isoformat() if r[6] else ""} for r in rows]
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


@fastapi_route("/admin/activity", methods=["GET"])
def admin_get_activity(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)
    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, actor_name, actor_email, action, target_label, details, session_ip, created_at
        FROM admin_activity_logs
        ORDER BY created_at DESC, id DESC
        LIMIT 500
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return JSONResponse({"success": True, "activity": [
        {
            "id": row[0], "actor_name": row[1] or "Unknown admin",
            "actor_email": row[2] or "", "action": row[3],
            "target_label": row[4] or "", "details": row[5] or "",
            "session_ip": row[6] or "", "created_at": row[7].isoformat() if row[7] else "",
        }
        for row in rows
    ]})


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
        password_error = _password_policy_error(password)
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
            hashed = _hash_password(password)
            cursor.execute('UPDATE users SET name = ?, email = ?, password_hash = ?, must_change_password = ? WHERE id = ?', (name, email, hashed, account[0] == ROLE_SEMI_ADMIN, uid))
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
                return JSONResponse({"success": False, "message": "Cannot remove the last active super admin."}, status_code=400)

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
            return JSONResponse({"success": False, "message": "Cannot remove the last active super admin."}, status_code=400)

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

    password_error = _password_policy_error(password)
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
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    role = str(data.get("role") or ROLE_SEMI_ADMIN).strip()
    if not name or not email or not password:
        return JSONResponse({"success": False, "message": "Name, email, and password are required."}, status_code=400)
    if role not in (ROLE_SUPER_ADMIN, ROLE_SEMI_ADMIN):
        return JSONResponse({"success": False, "message": "Choose Super Admin or Semi Admin."}, status_code=400)
    password_error = _password_policy_error(password)
    if password_error:
        return JSONResponse({"success": False, "message": password_error}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (name, email, password_hash, profile_picture, role, is_active, must_change_password) VALUES (?, ?, ?, ?, ?, TRUE, ?) RETURNING id",
            (name, email, _hash_password(password), "default.svg", role, role == ROLE_SEMI_ADMIN),
        )
        created = cursor.fetchone()
        conn.commit()
    except UniqueViolation:
        conn.close()
        return JSONResponse({"success": False, "message": "Email already exists."}, status_code=409)
    conn.close()
    _record_admin_activity(req.session, "admin_account_created", created[0], email, f"role={role}")
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
        return JSONResponse({"success": False, "message": "Choose Super Admin or Semi Admin."}, status_code=400)
    if user_id == int(req.session["user_id"]) and role != ROLE_SUPER_ADMIN:
        return JSONResponse({"success": False, "message": "You cannot remove your own super-admin access."}, status_code=400)

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
            return JSONResponse({"success": False, "message": "At least one active super admin must remain."}, status_code=400)
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
async def admin_generate_report(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid report request."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"success": False, "message": "Invalid report request."}, status_code=400)

    report_type = str(data.get("report_type") or "student_summary").strip()
    report_titles = {
        "student_summary": "Student Summary Report",
        "report_cards": "Report Card Status Report",
        "recommendations": "Recommendation Frequency Report",
    }
    if report_type not in report_titles:
        return JSONResponse({"success": False, "message": "Choose a supported report type."}, status_code=400)

    strand = str(data.get("strand") or "").strip()
    if strand and strand not in SYSTEM_SETTING_STRANDS and strand != "Unspecified":
        return JSONResponse({"success": False, "message": "Choose a configured academic strand."}, status_code=400)

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

    filters = {
        "strand": strand or "All strands",
        "from_date": from_date.isoformat() if from_date else "Any",
        "to_date": to_date.isoformat() if to_date else "Any",
    }
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
        cursor.execute(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(profile.strand), ''), 'Unspecified') AS strand,
                   COUNT(users.id), COUNT(profile.id),
                   AVG(CASE WHEN BTRIM(COALESCE(profile.gwa, '')) ~ '^[0-9]+([.][0-9]+){0,1}$'
                       THEN BTRIM(profile.gwa)::NUMERIC END),
                   COUNT(CASE WHEN BTRIM(COALESCE(profile.gwa, '')) ~ '^[0-9]+([.][0-9]+){0,1}$' THEN 1 END),
                   COUNT(*) FILTER (WHERE COALESCE(users.is_active, TRUE)),
                   COUNT(*) FILTER (WHERE NOT COALESCE(users.is_active, TRUE))
            FROM users
            LEFT JOIN LATERAL (
                SELECT id, strand, gwa
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
            {"key": "strand", "label": "Academic strand"},
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
        if from_date:
            conditions.append("uploads.uploaded_at::date >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("uploads.uploaded_at::date <= ?")
            params.append(to_date)
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
                "latest_upload": row[3].isoformat() if row[3] else "—",
            }
            for row in data_rows
        ]

    else:
        conditions = ["COALESCE(users.role, 'student') = 'student'"]
        params = []
        if from_date:
            conditions.append("history.generated_at::date >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("history.generated_at::date <= ?")
            params.append(to_date)
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
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "filters": filters,
            "summary": summary,
            "columns": columns,
            "rows": rows,
        },
    })


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
    if not recommendations:
        conn.close()
        return JSONResponse({"success": False, "message": "No recommendations could be generated from this profile's saved grades."}, status_code=400)

    payload = json.dumps(recommendations)
    cursor.execute("UPDATE student_profiles SET recommendation = ? WHERE id = ?", (payload, profile_id))
    cursor.execute(
        "INSERT INTO course_recommendation_history (profile_id, recommendations) VALUES (?, ?)",
        (profile_id, payload),
    )
    conn.commit()
    conn.close()
    _record_admin_activity(req.session, "recommendations_recalculated", target_label=f"profile:{profile_id}", details=f"count={len(recommendations)}")
    return JSONResponse({"success": True, "count": len(recommendations), "recommendations": recommendations})


# Define the route for saving a student profile
@fastapi_route("/save_profile", methods=["POST"])
async def save_profile(req):
    sess = req.session
    if "user_id" not in sess:
        return JSONResponse({"success": False, "message": "User not logged in"})

    user_id = sess["user_id"]
    data = await req.json()
    subjects_text = data.get("subjects", "")
    table_subjects = _subjects_from_table(data.get("ocrTable", [])) if data.get("ocrTable") else []
    if table_subjects:
        subjects_text = "\n".join(
            f"{item['subject_name']} - {item['grade']:g}"
            for item in table_subjects
        )
        data["subjects"] = subjects_text
    normalized_gwa = _compute_gwa(subjects_text, data.get("gwa", ""), data.get("grades", ""))
    normalized_grades = _coalesce_grades_text(subjects_text, data.get("grades", ""))

    recommendation_subjects = _clean_subjects_for_recommendation(subjects_text) or subjects_text
    recommendation = _configured_recommendations(recommendation_subjects, data.get("course", ""), data.get("strand", ""))
    recommendation_payload = json.dumps(recommendation)
    sess["latest_recommendations"] = recommendation_payload
    sess["latest_subjects"] = subjects_text

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, strand, report_card_upload_id
        FROM student_profiles
        WHERE user_id = ?
        ORDER BY upload_date DESC
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
    # Merge the existing student profile with the new data and prepare it for saving
    merged_student = data.copy()
    if target_student:
        merged_subjects = _merge_subject_entries(target_student.get("subjects", ""), data.get("subjects", ""))
        merged_grades = _coalesce_grades_text(merged_subjects, data.get("grades", "") or target_student.get("grades", ""))
        merged_gwa = _compute_gwa(merged_subjects, data.get("gwa", "") or target_student.get("gwa", ""), merged_grades)
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
                merged_student.get("strand") or _infer_strand(merged_student.get("course", "")),
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
                data.get("strand") or _infer_strand(data.get("course", "")),
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
    sess.pop("latest_report_card_upload_id", None)
    # Build the comparisons for the top 3 recommended courses based on student performance analytics
    comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], subjects_text)
        for item in recommendation[:3]
    }
    return JSONResponse({
        "success": True,
        "message": "Profile saved successfully",
        "recommendation": recommendation,
        "comparisons": comparisons,
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
    password_error = _password_policy_error(new_password)
    if password_error:
        return JSONResponse({"success": False, "message": password_error}, status_code=400)
    if new_password != confirm_password:
        return JSONResponse({"success": False, "message": "New passwords do not match."}, status_code=400)
    forced_change = bool(sess.get("must_change_password"))

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE id = ?", (sess["user_id"],))
    row = cursor.fetchone()
    if not row or not _check_password(row[0], current_password):
        conn.close()
        return JSONResponse({"success": False, "message": "Current password is incorrect."}, status_code=400)
    if _check_password(row[0], new_password):
        conn.close()
        return JSONResponse({"success": False, "message": "Choose a password different from your temporary password."}, status_code=400)

    cursor.execute("UPDATE users SET password_hash = ?, must_change_password = FALSE WHERE id = ?", (_hash_password(new_password), sess["user_id"]))
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
    table_subjects = _subjects_from_table(data.get("ocrTable", [])) if data.get("ocrTable") else []
    if table_subjects:
        subjects_text = "\n".join(
            f"{item['subject_name']} - {item['grade']:g}"
            for item in table_subjects
        )

    recommendation_subjects = _clean_subjects_for_recommendation(subjects_text) or subjects_text
    recommendations = _configured_recommendations(recommendation_subjects, "", data.get("strand", ""))
    req.session["latest_recommendations"] = json.dumps(recommendations)
    req.session["latest_subjects"] = subjects_text
    req.session["latest_strand"] = data.get("strand", "")
    comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], subjects_text)
        for item in recommendations[:3]
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
        SELECT student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, recommendation, strand
        FROM student_profiles
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (sess["user_id"],),
    )
    profile = cursor.fetchone()
    conn.close()

    if not profile:
        return JSONResponse({"success": False})

    recommendation_value = profile[8]
    try:
        recommendation_data = json.loads(recommendation_value) if recommendation_value else None
    except (TypeError, ValueError):
        recommendation_data = recommendation_value
    saved_recommendations = _limit_recommendations(recommendation_data)
    if not saved_recommendations and profile[7]:
        subjects = _clean_subjects_for_recommendation(profile[7]) or profile[7]
        saved_recommendations = _configured_recommendations(subjects, profile[4] or "", profile[9] or "")
    saved_comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], profile[7] or "")
        for item in saved_recommendations[:3]
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
        return JSONResponse({"success": False})

    _, ext = os.path.splitext(filename_in)
    filename = f"user_{sess['user_id']}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    file_bytes = await up_file.read()
    with open(filepath, "wb") as f:
        f.write(file_bytes)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (filename, sess["user_id"]))
    conn.commit()
    conn.close()

    _set_session_profile_image(sess, filename)
    if sess.get("role") in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "profile_picture_changed", target_label=sess.get("email", ""))
    return JSONResponse({"success": True, "picture": filename, "profile_image": _profile_image_from_value(filename)})


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
        "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE) FROM users WHERE email = ?",
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
        if google_picture and (not current_picture or current_picture.lower() in ("default.jpg", "default.png", "default.svg")):
            cursor.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (google_picture, user_id))
            conn.commit()
            current_picture = google_picture
    else:
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, profile_picture, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, email, "GOOGLE_ACCOUNT", google_picture or "default.svg", "student"),
        )
        conn.commit()
        cursor.execute(
            "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student'), COALESCE(is_active, TRUE), COALESCE(must_change_password, FALSE) FROM users WHERE email = ?",
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

    cursor.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    # Set the session variables for the logged-in user
    sess = req.session
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email
    sess["role"] = role
    sess["must_change_password"] = must_change_password
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or email
        sess["admin_ip"] = req.client.host if req.client else ""
    _set_session_profile_image(sess, current_picture)
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        _record_admin_activity(sess, "google_login", target_label=email)

    if must_change_password:
        return RedirectResponse("/admin/force-password-change", status_code=303)
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
        parsed["needs_review"] = bool(parsed.get("needs_review")) or any(
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


# OCR endpoint for processing report cards
@fastapi_route("/ocr_report_card", methods=["POST"])
async def ocr_report_card(req):
    req.session.pop("latest_report_card_upload_id", None)
    form = await req.form()
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
            INSERT INTO report_card_uploads (user_id, original_filename, content_type, file_data, ocr_status)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (user_id, filename, content_type, file_bytes, "processing" if automatic_ocr_enabled else "needs_review"),
        )
        upload_id = cursor.fetchone()[0]
        req.session["latest_report_card_upload_id"] = upload_id
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

    try:
        processed = _process_report_card_bytes(file_bytes, filename)
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
                "table_preview": dumps(raw_ocr.get("table", [])[1:3], default=str)[:500],
                "timings_seconds": {
                    **ocr_payload.get("timings_seconds", {}),
                    "app_parse_seconds": processed["app_parse_seconds"],
                },
            },
            "review_required": parsed.get("needs_review", False),
        })
    except Exception as exc:
        if upload_id:
            conn = _db_conn()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE report_card_uploads SET ocr_status = 'failed', ocr_error = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (str(exc)[:1000], upload_id),
            )
            conn.commit()
            conn.close()
        return JSONResponse({"success": False, "message": f"Report card OCR failed: {exc}"})


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
    ("/generate_recommendations", ["POST"], generate_recommendations, ()),
    ("/system-settings/public", ["GET"], public_system_settings, ()),
    ("/admin/users", ["GET"], admin_get_users, ()),
    ("/admin/system-settings", ["GET"], admin_get_system_settings, ()),
    ("/admin/system-settings", ["POST"], admin_save_system_settings, ()),
    ("/admin/activity", ["GET"], admin_get_activity, ()),
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
    ("/admin/recommendations", ["GET"], admin_recommendation_history, ()),
    ("/admin/recommendations/{profile_id}/recalculate", ["POST"], admin_recalculate_recommendations, ("profile_id",)),
    ("/save_profile", ["POST"], save_profile, ()),
    ("/update_account", ["POST"], update_account, ()),
    ("/admin/change_password", ["POST"], admin_change_password, ()),
    ("/recommend_anonymous", ["POST"], recommend_anonymous, ()),
    ("/get_profile", ["GET"], get_profile, ()),
    ("/upload_profile_picture", ["POST"], upload_profile_picture, ()),
    ("/get_profile_picture", ["GET"], get_profile_picture, ()),
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

# Main entry point for the FastHTML frontend and mounted FastAPI backend.
def main():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_database()
    try:
        from ocr.docling_service import _get_docling_converter
        _get_docling_converter()
    except Exception as exc:
        print(f"Docling warm-up skipped: {exc}")
    port = int(os.getenv("PORT", "5000"))
    serve(port=port, reload=False, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()

