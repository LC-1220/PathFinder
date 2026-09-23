import json
import html
import math
import os
import re
import tempfile
import time
from json import dumps
from datetime import datetime
from difflib import SequenceMatcher

import cv2
import httpx
import numpy as np
from authlib.integrations.starlette_client import OAuth
import bcrypt
import psycopg
from psycopg.errors import UniqueViolation
from dotenv import load_dotenv
from fasthtml.common import *
from jinja2 import Environment, FileSystemLoader, select_autoescape

load_dotenv()

from ocr import scan_report_card_docling
from ocr.docling_service import _subjects_from_table
from ocr.parser import normalize_ocr_text, parse_report_card_structure

try:
    from sklearn.neighbors import NearestNeighbors
except Exception:
    NearestNeighbors = None

UPLOAD_FOLDER = "static/profile_pictures"
ADMIN_USERNAME = "UPHSDAdmin2026"
ADMIN_PASSWORD = "UPHSD2026"
ROLE_ADMIN = "admin"
ROLE_SEMI_ADMIN = "semi_admin"

CATEGORY_NAMES = ["math", "science", "english", "technology", "business", "social"]

_NEAREST_NEIGHBOR_MODEL = None
_COURSE_TRAINING_DATA_CACHE = None


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


TEMPLATES = _template_env()


def _default_profile_image_url():
    return "/static/profile_pictures/default.svg"


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


def _set_session_profile_image(sess, profile_picture_value):
    sess["profile_picture"] = (profile_picture_value or "").strip()
    sess["profile_image"] = _profile_image_from_value(profile_picture_value)


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


def _course_training_data():
    global _COURSE_TRAINING_DATA_CACHE
    if _COURSE_TRAINING_DATA_CACHE is not None:
        return _COURSE_TRAINING_DATA_CACHE

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT course, features, description FROM course_training_data ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    _COURSE_TRAINING_DATA_CACHE = [
        {"course": row[0], "features": row[1], "description": row[2]}
        for row in rows
    ]
    return _COURSE_TRAINING_DATA_CACHE


def _hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(stored_hash, password):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False


def _normalize_grade(value):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _average(values):
    return sum(values) / len(values) if values else 0.0


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


def _clean_subjects_for_recommendation(subjects_text):
    subject_terms = {
        "math", "mathematics", "science", "communication", "education", "technology",
        "health", "english", "filipino", "literature", "person", "research", "entrepreneur",
        "services", "programming", "computer", "physical", "statistics", "biology",
    }

    def looks_like_instructor(value):
        words = re.findall(r"[A-Za-z]+", value)
        lowered = {word.lower() for word in words}
        return (
            len(words) >= 2
            and value == value.upper()
            and not lowered.intersection(subject_terms)
            and all(len(word) >= 1 for word in words)
        )

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
    return "\n".join(dict.fromkeys(cleaned_rows))


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


def _compute_gwa(subjects_text, incoming_gwa="", incoming_grades=""):
    text_val = (incoming_gwa or "").strip()
    if text_val:
        return text_val

    grades = _extract_numeric_from_grades_text(subjects_text)
    if not grades:
        grades = _extract_numeric_from_grades_text(incoming_grades)
    if not grades:
        return ""
    return f"{(sum(grades) / len(grades)):.2f}"


def _coalesce_grades_text(subjects_text, incoming_grades=""):
    text_val = (incoming_grades or "").strip()
    if text_val:
        return text_val

    grades = _extract_numeric_from_grades_text(subjects_text)
    if not grades:
        return ""
    return ", ".join(str(g).rstrip("0").rstrip(".") for g in grades)


def _normalize_student_number(value):
    if value is None:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value).upper()).strip()


def _normalize_full_name(value):
    if value is None:
        return ""
    tokens = re.findall(r"[A-Za-z]+", str(value))
    return "".join(tokens).upper()


async def validate_upload(file_obj):
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

    if len(file_bytes) > 10 * 1024 * 1024:
        return False, "Uploaded file exceeds 10 MB."

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


def _merge_subject_entries(existing_subjects, incoming_subjects):
    combined = []
    seen = {}

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


def _vector_distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


CORE_FEATURE_WEIGHTS = (3.0, 3.0, 3.0, 1.0, 1.0, 1.0)


def _weighted_features(features):
    return [value * math.sqrt(weight) for value, weight in zip(features, CORE_FEATURE_WEIGHTS)]


def _weighted_vector_distance(a, b):
    return math.sqrt(
        sum(weight * (x - y) ** 2 for weight, x, y in zip(CORE_FEATURE_WEIGHTS, a, b))
    )


def _core_grade_fit(student_features, course_features):
    observed = [value for value in student_features[:3] if value > 0]
    if not observed:
        return 0.0
    student_core = _average(observed)
    course_core = _average(course_features[:3])
    return max(0.0, 100.0 - abs(student_core - course_core))


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
    if any(tok in name for tok in ("business", "management", "accounting", "marketing", "finance", "administration", "economics", "entrepreneur")) or any(tok in desc for tok in ("business", "management", "accounting", "marketing", "finance", "operations")):
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


def _infer_strand(course_name=""):
    course_name = (course_name or "").lower()
    if any(tok in course_name for tok in ("computer", "information", "technology", "it", "engineering", "science", "math")):
        return "STEM"
    if any(tok in course_name for tok in ("business", "accounting", "management", "marketing", "economics")):
        return "ABM"
    if any(tok in course_name for tok in ("psychology", "education", "communication", "social", "humanities")):
        return "HUMSS"
    if any(tok in course_name for tok in ("nurse", "health", "medical", "hospital", "clinical", "care")):
        return "TVL"
    if any(tok in course_name for tok in ("gas", "general", "service", "tourism", "hospitality", "arts")):
        return "GAS"
    return "Other"


def _infer_strengths_from_features(features):
    ranked = sorted(zip(CATEGORY_NAMES, features), key=lambda t: t[1], reverse=True)
    return [name for name, value in ranked if value >= 70][:3]


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


def _valid_recommendation_course_name(course_name):
    if not course_name or not isinstance(course_name, str):
        return False
    cleaned = course_name.strip()
    if not cleaned or cleaned.lower() in {"general education", "other", "recommended course", "safe general option", "n/a", "na"}:
        return False
    if "no strong academic pattern" in cleaned.lower():
        return False
    return True


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
        if not _valid_recommendation_course_name(course_name):
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


def recommend_course(subjects_text, current_course="", strand=""):
    training_data = _course_training_data()
    features = _build_feature_vector(subjects_text)
    if not any(feature > 0 for feature in features):
        return []

    strongest = _infer_strengths_from_features(features)
    reason = "Your grades show a balanced academic profile, which fits the most similar historical pattern." if not strongest else f"Your strongest areas are {', '.join(strongest)}."

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

    if not recommendations:
        distances = []
        for sample in training_data:
            distance = _weighted_vector_distance(features, sample["features"])
            distances.append((distance, sample["course"], sample.get("description", ""), sample["features"]))

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

    strand_groups = {
        "stem": {"Computer Studies", "Engineering", "Allied Health", "Agriculture"},
        "abm": {"Business & Management", "Hospitality & Tourism"},
        "humss": {"Social Sciences & Education", "Public Service & Governance", "Arts & Design"},
        "tvl": {"Computer Studies", "Allied Health", "Hospitality & Tourism", "Agriculture"},
        "gas": set(),
    }
    strand_key = (strand or "").strip().lower()
    preferred_groups = strand_groups.get(strand_key, set())
    strand_course_terms = {
        "stem": ("computer", "information", "data", "software", "engineering", "science", "biology", "medical", "pharmacy", "technology"),
        "abm": ("business", "account", "marketing", "management", "finance", "economics", "entrepreneur", "hospitality", "tourism"),
        "humss": ("psychology", "education", "communication", "political", "criminology", "public administration", "social", "legal", "tourism"),
        "tvl": ("technology", "computer", "nursing", "medical", "pharmacy", "hospitality", "tourism", "agriculture", "fisheries"),
        "gas": (),
    }
    preferred_terms = strand_course_terms.get(strand_key, ())
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
    if recommendations and strand:
        recommendations[0]["strand_grade_based"] = True
        recommendations[0]["reason"] += f" This top match combines your {strand.upper()} strand with your Math, Science, and English grade profile."
    # Confidence remains an internal ranking signal; every usable profile gets
    # the five strongest available course matches without a visible cutoff.
    return recommendations[:5]


def _course_average_profile(course_name):
    training_data = _course_training_data()
    course_name = (course_name or "").strip()
    course_data = None
    for candidate in training_data:
        if candidate["course"].lower() == course_name.lower():
            course_data = candidate
            break

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


def _course_alias_slug(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _extract_requested_course(message):
    text = (message or "").strip()
    if not text:
        return ""
    quoted = re.findall(r'"([^"]{3,})"', text)
    if quoted:
        return quoted[0].strip()
    m = re.search(r"(?:what\s+about|recommend|for)\s+([A-Za-z][A-Za-z\s&\-/]{2,})", text, re.IGNORECASE)
    return (m.group(1).strip(" .?!,") if m else "")


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


def _build_chat_response(message, recommendations, profile):
    message = (message or "").strip()
    if not message:
        return "Ask me about your recommended courses, why they were suggested, or mention a course you want so I can compare it with your current profile."
    if not recommendations:
        return "No recommendations are saved yet. Upload or update your student profile first, then ask again and I can explain the course matches."

    profile = profile or {}
    gwa = profile.get("gwa") or "not available"
    strand = profile.get("strand") or "not specified"
    scores = _extract_subject_scores(profile.get("subjects", ""))
    strengths = _infer_strengths_from_features(_build_feature_vector(profile.get("subjects", "")))
    asked = next(
        (
            item.get("course", "")
            for item in recommendations
            if item.get("course") and item.get("course", "").lower() in message.lower()
        ),
        _extract_requested_course(message),
    )
    if asked:
        asked_slug = _course_alias_slug(asked)
        matching = next((item for item in recommendations if _course_alias_slug(item.get("course", "")) == asked_slug), None)
        if not matching:
            catalog_course = next(
                (item for item in _course_training_data() if _course_alias_slug(item.get("course", "")) == asked_slug),
                None,
            )
            if catalog_course:
                features = _build_feature_vector(profile.get("subjects", ""))
                distance = _vector_distance(features, catalog_course["features"])
                confidence = round(max(1.0, min(100.0, 100.0 - distance * 10.0)), 2)
                category = categorize_course(catalog_course["course"], catalog_course.get("description", ""))
                strand_note = f" It aligns with your {strand} strand." if category in {"Computer Studies", "Engineering", "Allied Health"} and strand.lower() == "stem" else ""
                return f"{catalog_course['course']} fits your current profile. {catalog_course.get('description', '')}{strand_note}"
        if matching:
            return (
                f"{matching.get('course')} is one of the strongest matches for your profile. "
                f"Your GWA is {gwa}, your strand is {strand}, and your strongest areas are {', '.join(strengths) or 'still developing'}. "
                f"{matching.get('description', '')} {matching.get('reason', '')}"
            ).strip()
        return f"{asked.title()} is not in your current top recommendations. Your closest matches are: {', '.join(item.get('course', '') for item in recommendations[:3])}."

    lowered = message.lower()
    if any(token in lowered for token in ("improve", "weak", "better", "prepare")):
        weakest = sorted(
            ((name, _average(values)) for name, values in scores.items() if values),
            key=lambda item: item[1],
        )[:2]
        focus = ", ".join(f"{name} ({score:.1f})" for name, score in weakest) or "your subject scores"
        return f"To strengthen your options, focus first on {focus}. Your current strand is {strand} and your GWA is {gwa}."

    if any(token in lowered for token in ("why", "recommend", "fit", "match")):
        best = recommendations[0]
        return f"{best.get('course')} is currently your strongest match. It fits your {strand} strand, GWA of {gwa}, and strengths in {', '.join(strengths) or 'your overall grade profile'}."

    top_names = ", ".join([r.get('course', '') for r in recommendations[:5] if r.get("course")])
    return f"Based on your {strand} strand, GWA {gwa}, and strongest areas in {', '.join(strengths) or 'your overall profile'}, your current top matches are: {top_names}."


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
            role TEXT DEFAULT 'student'
        )
        """
    )

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
                DELETE FROM student_profiles profile
                WHERE profile.user_id IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = profile.user_id)
        """
    )

    conn.commit()
    try:
        cursor.execute("SELECT id FROM users WHERE email = ?", (ADMIN_USERNAME,))
        row = cursor.fetchone()
        if not row:
            hashed = _hash_password(ADMIN_PASSWORD)
            cursor.execute(
                "INSERT INTO users (name, email, password_hash, profile_picture, role) VALUES (?, ?, ?, ?, ?)",
                ("Administrator", ADMIN_USERNAME, hashed, "default.svg", ROLE_ADMIN),
            )
            conn.commit()
        else:
            cursor.execute("UPDATE users SET role = ? WHERE email = ?", (ROLE_ADMIN, ADMIN_USERNAME))
            conn.commit()
    except Exception:
        pass

    conn.close()


def _is_admin_session(sess):
    role = sess.get("role")
    return bool(sess.get("is_admin")) and role in (ROLE_ADMIN, ROLE_SEMI_ADMIN)


def _is_full_admin_session(sess):
    return sess.get("role") == ROLE_ADMIN


def _render(req, template_name, **ctx):
    sess = req.session
    if "user_id" in sess:
        conn = _db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT profile_picture FROM users WHERE id = ?", (sess["user_id"],))
        row = cursor.fetchone()
        conn.close()
        if row:
            _set_session_profile_image(sess, row[0])

    body = TEMPLATES.get_template(template_name).render(
        name=sess.get("name", "User"),
        email=sess.get("email", "User"),
        admin=sess.get("admin_user", sess.get("name", "Administrator")),
        profile_image=sess.get("profile_image", _default_profile_image_url()),
        **ctx,
    )
    return HTMLResponse(body)


app, rt = fast_app(
    secret_key=os.getenv("APP_SECRET_KEY", "your_secret_key"),
    static_path=".",
    default_hdrs=False,
)

oauth = OAuth()

google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

github = oauth.register(
    name="github",
    client_id=os.getenv("GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "user:email"},
)


@rt("/", methods=["GET"])
def login_page(req):
    return _render(req, "Login.html")


@rt("/login", methods=["GET"])
def login_page_alias(req):
    return _render(req, "Login.html")


@rt("/home", methods=["GET"])
def home(req):
    sess = req.session
    return _render(
        req,
        "StudentManagement.html",
        home_mode=True,
        is_guest=bool(sess.get("is_guest")),
    )


@rt("/guest-login", methods=["GET"])
def guest_login(req):
    req.session.clear()
    req.session["is_guest"] = True
    req.session["name"] = "Guest"
    req.session["email"] = "Anonymous session"
    return RedirectResponse("/home", status_code=302)


@rt("/generate_recommendations", methods=["POST"])
async def generate_recommendations(req):
    sess = req.session
    user_id = sess.get("user_id")
    if not user_id:
        return JSONResponse({"success": False, "message": "Please sign in first."}, status_code=401)

    profile = _get_user_latest_profile(user_id)
    if not profile:
        return JSONResponse({"success": False, "message": "Save a student profile first."})

    recommendation_subjects = _clean_subjects_for_recommendation(profile.get("subjects", "")) or profile.get("subjects", "")
    recommendations = _sanitize_recommendations(json.dumps(recommend_course(
        recommendation_subjects,
        profile.get("course", ""),
        profile.get("strand", ""),
    )))[:5]
    if not recommendations:
        return JSONResponse({"success": False, "message": "No course matches could be generated from the saved grades."})

    payload = json.dumps(recommendations)
    sess["latest_recommendations"] = payload
    conn = _db_conn()
    conn.execute(
        "UPDATE student_profiles SET recommendation = ? WHERE id = (SELECT id FROM student_profiles WHERE user_id = ? ORDER BY id DESC LIMIT 1)",
        (payload, user_id),
    )
    conn.commit()
    conn.close()
    return JSONResponse({"success": True, "count": len(recommendations)})


@rt("/register", methods=["POST"])
async def register(req):
    data = await req.json()
    name = data.get("name", "")
    email = data.get("email", "")
    password = data.get("password", "")

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


@rt("/login", methods=["POST"])
async def login(req):
    data = await req.json()
    email = data.get("email", "")
    password = data.get("password", "")

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, name, password_hash, COALESCE(role, 'student'), COALESCE(profile_picture, '')
        FROM users
        WHERE email = ?
        """,
        (email,),
    )
    result = cursor.fetchone()
    conn.close()

    if not result:
        return JSONResponse({"success": False, "message": "Email not found"})

    user_id, name, stored_hash, role, profile_picture = result

    try:
        valid = _check_password(stored_hash, password)
    except Exception:
        valid = False

    if not valid:
        return JSONResponse({"success": False, "message": "Incorrect Password"})

    sess = req.session
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email
    sess["role"] = role
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or email
    _set_session_profile_image(sess, profile_picture)

    resp = {"success": True}
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        resp["admin"] = True
    if role == ROLE_SEMI_ADMIN:
        resp["semi_admin"] = True
    return JSONResponse(resp)


@rt("/logout", methods=["GET"])
def logout(req):
    req.session.clear()
    return RedirectResponse("/", status_code=302)


@rt("/admin/login", methods=["GET", "POST"])
def admin_login(req):
    return RedirectResponse("/", status_code=302)


@rt("/admin/logout", methods=["GET"])
def admin_logout(req):
    req.session.clear()
    return RedirectResponse("/", status_code=302)


@rt("/admin", methods=["GET"])
def admin_home(req):
    if not _is_admin_session(req.session):
        return RedirectResponse("/", status_code=302)
    return _render(req, "AdminDashboard.html", can_manage_students=(_is_full_admin_session(req.session)))


@rt("/admin/dashboard", methods=["GET"])
def admin_dashboard(req):
    if not _is_admin_session(req.session):
        return RedirectResponse("/", status_code=302)
    return _render(req, "AdminDashboard.html", can_manage_students=(_is_full_admin_session(req.session)))


@rt("/admin/users", methods=["GET"])
def admin_get_users(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, profile_picture, COALESCE(role, 'student') FROM users ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    users = [{"id": r[0], "name": r[1], "email": r[2], "profile_picture": r[3], "role": r[4]} for r in rows]
    return JSONResponse({"users": users})


@rt("/admin/update_user", methods=["POST"])
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

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        if password:
            hashed = _hash_password(password)
            cursor.execute('UPDATE users SET name = ?, email = ?, password_hash = ? WHERE id = ?', (name, email, hashed, uid))
        else:
            cursor.execute('UPDATE users SET name = ?, email = ? WHERE id = ?', (name, email, uid))
        conn.commit()
    except UniqueViolation:
        conn.close()
        return JSONResponse({"success": False, "message": "Email already exists"})
    conn.close()
    return JSONResponse({"success": True})


@rt("/admin/delete_user", methods=["POST"])
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
        cursor.execute("SELECT email, profile_picture FROM users WHERE id = ?", (uid,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"success": False, "message": "User not found"})

        email = (row[0] or "").strip()
        profile_picture = (row[1] or "").strip()

        if email == ADMIN_USERNAME:
            conn.close()
            return JSONResponse({"success": False, "message": "Cannot delete the main admin account"})

        cursor.execute("DELETE FROM student_profiles WHERE user_id = ?", (uid,))
        cursor.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        return JSONResponse({"success": False, "message": "Delete failed"})
    conn.close()

    lowered = profile_picture.lower()
    if profile_picture and not lowered.startswith("http://") and not lowered.startswith("https://") and lowered not in ("default.jpg", "default.png", "default.svg"):
        image_path = os.path.join(UPLOAD_FOLDER, profile_picture)
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError:
                pass

    return JSONResponse({"success": True})


@rt("/admin/create_coordinator", methods=["POST"])
async def admin_create_coordinator(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()

    if not name or not email or not password:
        return JSONResponse({"success": False, "message": "Name, email, and password are required"}, status_code=400)

    if len(password) < 6:
        return JSONResponse({"success": False, "message": "Password must be at least 6 characters"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    try:
        hashed = _hash_password(password)
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, profile_picture, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, email, hashed, "default.svg", ROLE_SEMI_ADMIN),
        )
        conn.commit()
    except UniqueViolation:
        conn.close()
        return JSONResponse({"success": False, "message": "Email already exists"}, status_code=409)

    conn.close()
    return JSONResponse({"success": True, "message": "Level coordinator account created"})


@rt("/admin/student_profiles", methods=["GET"])
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


@rt("/admin/update_student_profile", methods=["POST"])
async def admin_update_student_profile(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    sid = data.get("id")
    if not sid:
        return JSONResponse({"success": False, "message": "Missing student id"}, status_code=400)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE student_profiles
        SET first_name = ?, middle_initial = ?, last_name = ?, student_number = ?, course = ?, gwa = ?, grades = ?, subjects = ?, strand = ?
        WHERE id = ?
        """,
        (
            (data.get("first_name") or "").strip(),
            (data.get("middle_initial") or "").strip(),
            (data.get("last_name") or "").strip(),
            (data.get("student_number") or "").strip(),
            (data.get("course") or "").strip(),
            (data.get("gwa") or "").strip(),
            (data.get("grades") or "").strip(),
            (data.get("subjects") or "").strip(),
            (data.get("strand") or "").strip(),
            sid,
        ),
    )
    conn.commit()
    conn.close()
    return JSONResponse({"success": True, "message": "Student profile updated"})


@rt("/admin/students", methods=["GET"])
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
    return _render(req, "AdminDashboard.html", can_manage_students=(_is_full_admin_session(req.session)))


@rt("/admin/students/api", methods=["GET"])
def admin_get_students_api(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, student_number, first_name, middle_initial, last_name, strand FROM student_profiles ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return JSONResponse({"students": [
        {
            "id": row[0],
            "student_number": row[1],
            "first_name": row[2],
            "middle_initial": row[3],
            "last_name": row[4],
            "strand": row[5],
        }
        for row in rows
    ]})


@rt("/admin/delete_student", methods=["POST"])
async def admin_delete_student(req):
    if not _is_full_admin_session(req.session):
        return JSONResponse({"success": False, "error": "forbidden"}, status_code=403)

    data = await req.json()
    sid = data.get("id")
    if not sid:
        return JSONResponse({"success": False, "message": "Missing id"})

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM student_profiles WHERE id = ?", (sid,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@rt("/admin/stats", methods=["GET"])
def admin_stats(req):
    if not _is_admin_session(req.session):
        return JSONResponse({"success": False, "error": "unauthorized"}, status_code=401)

    conn = _db_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT course, COUNT(*) FROM student_profiles GROUP BY course")
    course_counts = {row[0] or "Unknown": row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) FROM student_profiles WHERE subjects IS NOT NULL AND subjects != ''")
    total_report_cards = cursor.fetchone()[0]

    cursor.execute("SELECT recommendation FROM student_profiles WHERE recommendation IS NOT NULL AND recommendation != ''")
    rec_rows = cursor.fetchall()
    rec_first_counts = {}
    rec_all_counts = {}
    for (rec_val,) in rec_rows:
        try:
            rec = _sanitize_recommendations(rec_val)
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

    cursor.execute("SELECT strand, COUNT(*) FROM student_profiles GROUP BY strand")
    strand_counts = {row[0] or "Other": row[1] for row in cursor.fetchall()}

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

    top_recs = sorted(rec_all_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_recommended = [{"course": k, "count": v} for k, v in top_recs]

    all_recommended = [{"course": k, "count": v} for k, v in sorted(rec_all_counts.items(), key=lambda x: (-x[1], x[0]))]

    conn.close()

    return JSONResponse(
        {
            "by_profile_course": course_counts,
            "by_recommended_first": rec_first_counts,
            "total_users": total_users,
            "total_profiles": total_profiles,
            "total_report_cards_uploaded": total_report_cards,
            "total_recommendations_generated": total_recommendations,
            "total_available_courses": total_available_courses,
            "pending_evaluations": pending_evaluations,
            "avg_gwa": avg_gwa,
            "gwa_buckets": buckets,
            "strand_counts": strand_counts,
            "monthly_uploads": monthly_uploads,
            "profiles_missing_recommendation": missing_recs,
            "top_recommended": top_recommended,
            "all_recommended_courses": all_recommended,
        }
    )


@rt("/save_profile", methods=["POST"])
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
    recommendation = recommend_course(recommendation_subjects, data.get("course", ""), data.get("strand", ""))
    recommendation = _sanitize_recommendations(json.dumps(recommendation))
    recommendation_payload = json.dumps(recommendation)
    sess["latest_recommendations"] = recommendation_payload
    sess["latest_subjects"] = subjects_text

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, strand
        FROM student_profiles
        WHERE user_id = ?
        ORDER BY upload_date DESC
        LIMIT 1
        """,
        (user_id,),
    )
    existing_profile = cursor.fetchone()

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

    if target_student:
        cursor.execute(
            """
            UPDATE student_profiles
            SET student_number = ?, first_name = ?, middle_initial = ?, last_name = ?, course = ?, gwa = ?, grades = ?, subjects = ?, recommendation = ?, strand = ?, upload_date = ?
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
                user_id,
                existing_profile[0],
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO student_profiles
            (user_id, student_number, first_name, middle_initial, last_name, course, gwa, grades, subjects, recommendation, strand, upload_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

    conn.commit()
    conn.close()

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


@rt("/update_account", methods=["POST"])
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
    return JSONResponse({"success": True, "name": name})


@rt("/recommend_anonymous", methods=["POST"])
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
    recommendations = _sanitize_recommendations(json.dumps(recommend_course(
        recommendation_subjects,
        "",
        data.get("strand", ""),
    )))[:5]
    req.session["latest_recommendations"] = json.dumps(recommendations)
    req.session["latest_subjects"] = subjects_text
    req.session["latest_strand"] = data.get("strand", "")
    comparisons = {
        item["course"]: _build_student_performance_analytics(item["course"], subjects_text)
        for item in recommendations[:3]
    }
    return JSONResponse({"success": True, "recommendation": recommendations, "comparisons": comparisons})


@rt("/get_profile", methods=["GET"])
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
    saved_recommendations = _sanitize_recommendations(recommendation_data)
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


@rt("/course_chat", methods=["POST"])
async def course_chat(req):
    data = await req.json()
    message = (data.get("message") or "").strip()

    user_id = req.session.get("user_id")
    profile = _get_user_latest_profile(user_id) if user_id else None
    if not profile and req.session.get("is_guest"):
        guest_subjects = req.session.get("latest_subjects", "")
        profile = {
            "subjects": guest_subjects,
            "gwa": _compute_gwa(guest_subjects),
            "strand": req.session.get("latest_strand") or "current academic",
            "recommendation": req.session.get("latest_recommendations", ""),
        }

    recommendations = data.get("recommendations") if isinstance(data.get("recommendations"), list) else None

    if not recommendations:
        rec_payload = req.session.get("latest_recommendations")
        if rec_payload:
            try:
                recommendations = json.loads(rec_payload)
            except (TypeError, ValueError):
                recommendations = []

    if not recommendations and profile and profile.get("recommendation"):
        try:
            recommendations = json.loads(profile.get("recommendation"))
        except (TypeError, ValueError):
            recommendations = []

    if not isinstance(recommendations, list):
        recommendations = []

    reply = _build_chat_response(message, recommendations, profile)
    return JSONResponse({"success": True, "reply": reply})


@rt("/upload_profile_picture", methods=["POST"])
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
    return JSONResponse({"success": True, "picture": filename, "profile_image": _profile_image_from_value(filename)})


@rt("/get_profile_picture", methods=["GET"])
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


@rt("/google-login", methods=["GET"])
async def google_login(req):
    redirect_uri = str(req.url_for("google_authorize"))
    return await google.authorize_redirect(req, redirect_uri)


@rt("/authorize", methods=["GET"])
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
        "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student') FROM users WHERE email = ?",
        (email,),
    )
    existing_user = cursor.fetchone()

    if existing_user:
        user_id = existing_user[0]
        current_picture = (existing_user[1] or "").strip()
        role = existing_user[2] or "student"
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
            "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student') FROM users WHERE email = ?",
            (email,),
        )
        inserted = cursor.fetchone()
        user_id = inserted[0]
        current_picture = inserted[1] or ""
        role = inserted[2] or "student"

    conn.close()

    sess = req.session
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email
    sess["role"] = role
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or email
    _set_session_profile_image(sess, current_picture)

    return RedirectResponse("/home", status_code=302)


@rt("/github-login", methods=["GET"])
async def github_login(req):
    redirect_uri = str(req.url_for("github_authorize"))
    return await github.authorize_redirect(req, redirect_uri)


@rt("/github-authorize", methods=["GET"])
async def github_authorize(req):
    token = await github.authorize_access_token(req)
    resp = await github.get("user", token=token)
    user = resp.json() if resp is not None else {}

    username = user.get("login", "")
    name = user.get("name") or username or "User"
    github_id = user.get("id")
    email = (user.get("email") or "").strip()
    avatar_url = (user.get("avatar_url") or "").strip()

    if not email:
        # GitHub may hide primary email; fetch via /user/emails
        emails_resp = await github.get("user/emails", token=token)
        emails = emails_resp.json() if emails_resp is not None else []
        for e in emails:
            if isinstance(e, dict) and e.get("primary") and e.get("verified"):
                email = (e.get("email") or "").strip()
                break
        if not email:
            for e in emails:
                if isinstance(e, dict) and e.get("verified"):
                    email = (e.get("email") or "").strip()
                    if email:
                        break

    if not email:
        return RedirectResponse("/", status_code=302)

    conn = _db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student') FROM users WHERE email = ?",
        (email,),
    )
    existing_user = cursor.fetchone()

    if existing_user:
        user_id = existing_user[0]
        current_picture = (existing_user[1] or "").strip()
        role = existing_user[2] or "student"
        if avatar_url and (not current_picture or current_picture.lower() in ("default.jpg", "default.png", "default.svg")):
            cursor.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (avatar_url, user_id))
            conn.commit()
            current_picture = avatar_url
    else:
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, profile_picture, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, email, "GITHUB_ACCOUNT", avatar_url or "default.svg", "student"),
        )
        conn.commit()
        cursor.execute(
            "SELECT id, COALESCE(profile_picture, ''), COALESCE(role, 'student') FROM users WHERE email = ?",
            (email,),
        )
        inserted = cursor.fetchone()
        user_id = inserted[0]
        current_picture = inserted[1] or ""
        role = inserted[2] or "student"

    conn.close()

    sess = req.session
    sess["user_id"] = user_id
    sess["name"] = name
    sess["email"] = email
    sess["role"] = role
    if github_id is not None:
        sess["github_id"] = github_id
    if username:
        sess["username"] = username
    if role in (ROLE_ADMIN, ROLE_SEMI_ADMIN):
        sess["is_admin"] = True
        sess["admin_user"] = name or email
    _set_session_profile_image(sess, current_picture)

    return RedirectResponse("/home", status_code=302)


def _group_ocr_boxes_into_table_rows(boxes):
    # Reconstructs table rows/columns from the OCR engine's per-box coordinates so the
    # structured parser can ignore extra columns (units, remarks) instead of the
    # line-based fallback, which breaks on any row with trailing non-numeric text.
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

    # Need at least a header row plus one data row with more than one column to be useful.
    if len(table_rows) < 2 or all(len(row["cells"]) < 2 for row in table_rows):
        return []
    return table_rows


@rt("/ocr_report_card", methods=["POST"])
async def ocr_report_card(req):
    form = await req.form()
    uploaded = form.get("report_card") or form.get("file") or form.get("image")
    if uploaded is None:
        return JSONResponse({"success": False, "message": "No report card uploaded."})

    valid, result = await validate_upload(uploaded)
    if not valid:
        return JSONResponse({"success": False, "message": result})

    file_bytes = result["file_bytes"]
    try:
        ocr_payload = scan_report_card_docling(
            file_bytes,
            filename=getattr(uploaded, "filename", "report_card.pdf"),
        )
        raw_ocr = {
            "raw_text": ocr_payload.get("raw_text", ""),
            "student_info_raw": ocr_payload.get("student_info_raw", []),
            "table": ocr_payload.get("table", []),
            "paddle_boxes": [],
            "image_width": 0,
            "image_height": 0,
        }
        parsed = parse_report_card_structure(raw_ocr)
        if ocr_payload.get("subjects"):
            parsed["subjects"] = ocr_payload["subjects"]
            parsed["gwa"] = round(
                sum(item["grade"] for item in parsed["subjects"]) / len(parsed["subjects"]),
                2,
            )
            parsed["needs_review"] = any(item.get("needs_review") for item in parsed["subjects"])
        structured_text = "\n".join(
            f"{item['subject_name']} - {item['grade']:g}"
            for item in parsed.get("subjects", [])
            if item.get("subject_name") and item.get("grade") is not None
        )
        return JSONResponse({
            "success": True,
            "message": "Report card extraction completed using Docling.",
            "raw_ocr": raw_ocr,
            "parsed": parsed,
            "structured_text": structured_text,
            "provider": "docling",
            "diagnostics": {
                "table_rows": len(raw_ocr.get("table", [])),
                "docling_subjects": len(ocr_payload.get("subjects", [])),
                "parsed_subjects": len(parsed.get("subjects", [])),
                "raw_text_characters": len(raw_ocr.get("raw_text", "")),
                "table_preview": dumps(raw_ocr.get("table", [])[1:3], default=str)[:500],
            },
            "review_required": parsed.get("needs_review", False),
        })
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"Report card OCR failed: {exc}"})


def main():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_database()
    port = int(os.getenv("PORT", "5000"))
    serve(port=port)


if __name__ == "__main__":
    main()