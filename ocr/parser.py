import re
from typing import Any, Dict, List


def normalize_ocr_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_student_metadata(metadata_items):
    info = {}
    entries = metadata_items or []
    for item in entries:
        if isinstance(item, dict):
            label = normalize_ocr_text(item.get("label", ""))
            value = normalize_ocr_text(item.get("value", item.get("text", "")))
            if not label and item.get("text"):
                text = normalize_ocr_text(item.get("text", ""))
                parts = re.split(r"\s*(?::|\||\t)\s*", text, maxsplit=1)
                if len(parts) == 2:
                    label, value = [part.strip() for part in parts]
        else:
            text = normalize_ocr_text(item)
            parts = re.split(r"\s*(?::|\||\t)\s*", text, maxsplit=1)
            if len(parts) != 2:
                continue
            label, value = [part.strip() for part in parts]

        label_lower = (label or "").lower()
        value_clean = normalize_ocr_text(value)
        if not value_clean:
            continue

        if any(token in label_lower for token in ["student id", "student no", "student number", "id number", "student d"]):
            info.setdefault("student_no", value_clean)
        elif label_lower in {"student name", "name of student", "full name", "name"}:
            if "student_name" not in info:
                info["student_name"] = value_clean
        elif "strand" in label_lower:
            info.setdefault("strand", value_clean)
            info.setdefault("major", value_clean)
        elif any(token in label_lower for token in ["course", "major", "program", "degree"]):
            info.setdefault("major", value_clean)

    if not info.get("student_no"):
        for item in entries:
            text = normalize_ocr_text(item.get("text", "") if isinstance(item, dict) else item)
            match = re.search(r"(?:student\s*(?:id|number|no)|\b(?:id|no)\b)\s*[:\-]?\s*([A-Z0-9\-]+)", text, flags=re.IGNORECASE)
            if match:
                info["student_no"] = match.group(1).strip()
                break
    if not info.get("student_name"):
        for item in entries:
            text = normalize_ocr_text(item.get("text", "") if isinstance(item, dict) else item)
            match = re.search(r"^\s*(?:student\s+name|name\s+of\s+student|full\s+name|name)\s*[:\-|\t]\s*(.+)$", text, flags=re.IGNORECASE)
            if match:
                info["student_name"] = match.group(1).strip()
                break
    if not info.get("strand"):
        for item in entries:
            text = normalize_ocr_text(item.get("text", "") if isinstance(item, dict) else item)
            match = re.search(r"strand\s*(?:/\s*track)?\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE)
            if match:
                info["strand"] = match.group(1).strip()
                break
    if not info.get("major"):
        for item in entries:
            text = normalize_ocr_text(item.get("text", "") if isinstance(item, dict) else item)
            match = re.search(r"(?:course|major|program|strand)\s*(?:/\s*major)?\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE)
            if match:
                info["major"] = match.group(1).strip()
                break
    return info


def validate_subject(raw_value):
    text = normalize_ocr_text(raw_value)
    if not text:
        return {"value": "", "needs_review": True, "errors": ["Missing subject name"]}
    if text.lower() in {"subject", "course", "description", "semester", "school year", "remarks", "gwa", "total", "grade", "instructor", "blank"}:
        return {"value": "", "needs_review": True, "errors": ["Non-subject row"]}
    if len(text) < 3:
        return {"value": text, "needs_review": True, "errors": ["Subject name too short"]}
    return {"value": re.sub(r"\s+", " ", text), "needs_review": False, "errors": []}


def validate_grade(raw_value):
    original = str(raw_value or "").strip()
    if not original:
        return {"normalized": None, "status": "INVALID", "reason": "missing grade value"}
    if re.search(r"[A-Za-z]", original):
        return {"normalized": None, "status": "REVIEW", "reason": "grade OCR contains letters and should be reviewed"}

    cleaned = original.replace("O", "0").replace("o", "0").replace(" ", "")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    if not cleaned or cleaned in {"-", "."}:
        return {"normalized": None, "status": "REVIEW", "reason": "grade OCR is unreadable"}

    try:
        num = float(cleaned)
    except ValueError:
        return {"normalized": None, "status": "REVIEW", "reason": "grade OCR is unreadable"}

    safe_num = round(num, 2)
    if 70.0 <= safe_num <= 100.0:
        return {"normalized": safe_num, "status": "VALID", "reason": "within valid school range"}
    if 0.0 <= safe_num <= 100.0:
        return {"normalized": safe_num, "status": "REVIEW", "reason": "grade falls outside expected range but may need confirmation"}
    return {"normalized": None, "status": "INVALID", "reason": "outside expected range 70.00–100.00"}


def calculate_gwa(subjects):
    valid_grades = []
    for item in subjects or []:
        if not isinstance(item, dict):
            continue
        grade = item.get("grade")
        if grade is None:
            continue
        try:
            numeric = float(grade)
        except (TypeError, ValueError):
            continue
        if 0 <= numeric <= 100:
            valid_grades.append(numeric)
    if not valid_grades:
        return None
    return round(sum(valid_grades) / len(valid_grades), 2)


IGNORED_PLAIN_SUBJECT_LABELS = {
    "year", "semester", "term", "section", "school year", "grade level",
    "grade", "gwa", "gpa", "course", "program", "major", "student", "name",
}


def identify_subject_column(table_rows):
    if not table_rows:
        return 0
    header_cells = sorted(table_rows[0].get("cells", []), key=lambda entry: int(entry.get("column", 0)))
    for idx, cell in enumerate(header_cells):
        text = str(cell.get("text", "") or "").strip().lower()
        if any(token in text for token in ["description", "subject name", "course title", "title"]):
            return idx
    return 0


def identify_grade_column(table_rows):
    if not table_rows:
        return -1
    header_cells = sorted(table_rows[0].get("cells", []), key=lambda entry: int(entry.get("column", 0)))
    for idx, cell in enumerate(header_cells):
        text = str(cell.get("text", "") or "").strip().lower()
        if any(token in text for token in ["grade", "rating", "mark", "score"]):
            return idx
    return -1


REMARKS_WORDS = {"passed", "failed", "incomplete", "conditional", "withdrawn", "inc", "drp", "dropped", "in progress"}
GRADE_SHAPE = r"(?:\d{1,3}\.\d{1,2}|\d{2,3})"


def _cell_looks_like_grade(text):
    return bool(re.fullmatch(GRADE_SHAPE, text.strip()))


def _cell_looks_like_remarks(text):
    return text.strip().lower() in REMARKS_WORDS


def _cell_looks_like_instructor(text):
    t = text.strip()
    return "," in t and len(t) <= 40 and bool(re.search(r"[A-Za-z]", t))


def _looks_like_ignored_metadata_line(text):
    first_word = text.strip().split(" ", 1)[0].lower() if text.strip() else ""
    return first_word in IGNORED_PLAIN_SUBJECT_LABELS


def _cell_looks_like_subject_code(text):
    t = text.strip()
    if not t or len(t) > 14:
        return False
    if re.fullmatch(r"\d{3,6}\s*[A-Za-z]{1,6}\d{0,3}", t):
        return True
    if re.fullmatch(r"[A-Za-z]{2,6}\s?\d{1,4}", t):
        return True
    return False


def _classify_row_cells(cells):
    """Pick out the subject-name and grade cell in a row by content shape (numeric grade,
    comma-formatted instructor, short alphanumeric subject code, Passed/Failed remarks)
    instead of a fixed column index, since the number of detected boxes per row can vary
    (e.g. a subject name that wraps onto two lines)."""
    texts = [normalize_ocr_text(cell.get("text", "")) for cell in cells]
    texts = [t for t in texts if t]
    if len(texts) < 2:
        return None, None

    grade_text = None
    remaining = []
    for t in texts:
        if grade_text is None and _cell_looks_like_grade(t):
            grade_text = t
            continue
        remaining.append(t)

    if grade_text is None:
        return None, None

    name_parts = [
        t for t in remaining
        if not _cell_looks_like_remarks(t) and not _cell_looks_like_instructor(t) and not _cell_looks_like_subject_code(t)
    ]
    if not name_parts:
        # Don't drop the row entirely if every remaining cell looked like code/instructor/remarks.
        name_parts = [t for t in remaining if not _cell_looks_like_remarks(t)]

    subject_name = " ".join(name_parts).strip()
    return subject_name, grade_text


def _extract_row_from_single_line(line):
    """Tries to pull [code] name [instructor] grade [remarks] out of ONE line, for the
    common case where PaddleOCR merges a whole visual table row into one text line."""
    text = line.strip()
    text = re.sub(
        r"\s+(?:passed|failed|incomplete|conditional|withdrawn|inc|drp|dropped)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    grade_match = re.search(rf"({GRADE_SHAPE})\s*$", text)
    if not grade_match:
        return None, None
    grade_text = grade_match.group(1)
    text = text[:grade_match.start()].strip()
    if not text:
        return None, None

    text = re.sub(r"^#?\d{3,6}\s*[A-Za-z]{1,6}\d{0,3}\s*", "", text).strip()
    text = re.sub(r"^#?\d{1,6}\s+", "", text).strip()
    # Drop a trailing "LASTNAME, First M." style instructor name.
    text = re.sub(r"\s*\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s*,\s*[A-Za-z.\s]+$", "", text).strip()
    # Some document exports remove the comma and punctuation from instructor names.
    text = re.sub(r"\s+\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s+[A-Z]\.?$", "", text).strip()
    if not text:
        return None, None
    return text, grade_text


def _parse_subject_rows_from_lines(lines):
    """Walks OCR text line-by-line (top-to-bottom reading order) and reconstructs one
    subject record per row, regardless of whether a whole table row landed on one merged
    line or each cell (code/name/instructor/grade/remarks) landed on its own line."""
    normalized = [normalize_ocr_text(line) for line in lines]
    normalized = [line for line in normalized if line and ':' not in line]

    subjects = []
    validation_errors = []

    def _finalize(subject_name, grade_text):
        subject_name = normalize_ocr_text(subject_name.strip(" -:|/"))
        if not subject_name or not re.search(r"[A-Za-z]", subject_name):
            return
        if subject_name.lower() in IGNORED_PLAIN_SUBJECT_LABELS:
            return
        grade_result = validate_grade(grade_text)
        if grade_result["status"] == "INVALID":
            validation_errors.append(f"Grade rejected for {subject_name}: {grade_result['reason']}")
            return
        subjects.append({
            "subject_name": subject_name,
            "grade": grade_result.get("normalized"),
            "needs_review": grade_result["status"] == "REVIEW",
            "confidence": 0.72 if grade_result["status"] == "VALID" else 0.45,
        })

    i = 0
    n = len(normalized)
    while i < n:
        line = normalized[i]

        if _cell_looks_like_grade(line) or _cell_looks_like_remarks(line) or _cell_looks_like_instructor(line):
            i += 1
            continue
        if _looks_like_ignored_metadata_line(line):
            i += 1
            continue

        single_name, single_grade = _extract_row_from_single_line(line)
        if single_grade is not None:
            _finalize(single_name, single_grade)
            i += 1
            if i < n and _cell_looks_like_remarks(normalized[i]):
                i += 1
            # Absorb a leftover wrapped continuation line (e.g. a subject name that
            # printed onto a second line with no instructor/grade of its own).
            if i < n and subjects:
                cont = normalized[i]
                cont_name, cont_grade = _extract_row_from_single_line(cont)
                if (
                    cont_grade is None
                    and not _cell_looks_like_grade(cont)
                    and not _cell_looks_like_remarks(cont)
                    and not _cell_looks_like_instructor(cont)
                    and not _cell_looks_like_subject_code(cont)
                    and len(cont) <= 60
                ):
                    subjects[-1]["subject_name"] = normalize_ocr_text(f"{subjects[-1]['subject_name']} {cont}")
                    i += 1
            continue

        # No grade found on this line alone; accumulate subsequent lines until one appears.
        name_parts = []
        code_stripped = re.sub(r"^\d{3,6}\s*[A-Za-z]{1,6}\d{0,3}\s*", "", line).strip()
        if not _cell_looks_like_subject_code(line):
            name_parts.append(code_stripped if code_stripped else line)

        grade_text = None
        j = i + 1
        lookahead_limit = min(n, i + 6)
        while j < lookahead_limit:
            nxt = normalized[j]
            if _cell_looks_like_grade(nxt):
                grade_text = nxt
                j += 1
                break
            if _cell_looks_like_instructor(nxt) or _cell_looks_like_subject_code(nxt):
                j += 1
                continue
            if _looks_like_ignored_metadata_line(nxt):
                j += 1
                continue
            nxt_name, nxt_grade = _extract_row_from_single_line(nxt)
            if nxt_grade is not None:
                if nxt_name:
                    name_parts.append(nxt_name)
                grade_text = nxt_grade
                j += 1
                break
            name_parts.append(nxt)
            j += 1

        if grade_text is None:
            i += 1
            continue

        _finalize(" ".join(name_parts), grade_text)
        i = j
        if i < n and _cell_looks_like_remarks(normalized[i]):
            i += 1

    return subjects, validation_errors



def parse_report_card_rows(table_rows, subject_column=0, grade_column=1):
    subjects = []
    validation_errors = []
    for row in table_rows[1:] if len(table_rows) > 1 else []:
        cells = sorted(row.get("cells", []), key=lambda entry: int(entry.get("column", 0)))
        if len(cells) < 2:
            continue

        subject_text, grade_text = _classify_row_cells(cells)
        if not subject_text or not grade_text:
            # Fall back to the header-driven column hint for row shapes content rules can't classify.
            subject_cell = cells[subject_column] if subject_column < len(cells) else {"text": ""}
            grade_cell = cells[grade_column] if grade_column < len(cells) else {"text": ""}
            subject_text = subject_text or normalize_ocr_text(subject_cell.get("text", ""))
            grade_text = grade_text or normalize_ocr_text(grade_cell.get("text", ""))
        if not subject_text or not grade_text:
            continue

        subject_result = validate_subject(subject_text)
        if not subject_result["value"]:
            continue

        grade_result = validate_grade(grade_text)
        if grade_result["status"] == "INVALID":
            subjects.append({"subject_name": subject_result["value"], "grade": None, "needs_review": True, "confidence": 0.52})
            validation_errors.append(f"Grade rejected for {subject_result['value']}: {grade_result['reason']}")
            continue

        entry = {
            "subject_name": subject_result["value"],
            "grade": grade_result.get("normalized"),
            "needs_review": grade_result["status"] == "REVIEW",
            "confidence": 0.96 if grade_result["status"] == "VALID" else 0.55,
        }
        if entry["needs_review"]:
            validation_errors.append(f"Needs review: {entry['subject_name']}")
        subjects.append(entry)

    return subjects, validation_errors


def parse_report_card_structure(raw_ocr):
    if not isinstance(raw_ocr, dict):
        return {
            "student_no": "",
            "student_name": "",
            "major": "",
            "subjects": [],
            "gwa": None,
            "needs_review": True,
            "validation_errors": ["No raw OCR was provided"],
        }

    student_info = parse_student_metadata(raw_ocr.get("student_info_raw", []))
    table_rows = raw_ocr.get("table", [])
    if isinstance(table_rows, dict):
        table_rows = table_rows.get("rows", []) or table_rows.get("cells", []) or []

    raw_text = str(raw_ocr.get("raw_text", "") or "").strip()
    if not student_info and raw_text:
        lines = raw_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if ":" in line:
                label, value = [part.strip() for part in line.split(":", 1)]
                label_lower = label.lower()
                value_clean = normalize_ocr_text(value)
                if any(token in label_lower for token in ["student id", "student no", "student number", "id number"]):
                    student_info["student_no"] = value_clean
                elif "student name" in label_lower:
                    student_info["student_name"] = value_clean
                elif "strand" in label_lower:
                    student_info["strand"] = value_clean
                    student_info.setdefault("major", value_clean)
                elif any(token in label_lower for token in ["course", "major", "program", "degree"]):
                    student_info["major"] = value_clean
                i += 1
                continue

            # Some OCR output puts a label on its own line with the value on the next line.
            label_lower = line.lower()
            has_next = i + 1 < len(lines)
            if has_next and any(token in label_lower for token in ["student id", "student no", "student number", "id number"]):
                student_info["student_no"] = normalize_ocr_text(lines[i + 1])
                i += 2
                continue
            if has_next and "student name" in label_lower:
                student_info["student_name"] = normalize_ocr_text(lines[i + 1])
                i += 2
                continue
            if has_next and "strand" in label_lower:
                student_info["strand"] = normalize_ocr_text(lines[i + 1])
                student_info.setdefault("major", student_info["strand"])
                i += 2
                continue
            if has_next and any(token in label_lower for token in ["course", "major", "program", "degree"]):
                student_info["major"] = normalize_ocr_text(lines[i + 1])
                i += 2
                continue
            i += 1

    if not table_rows:
        consumed_values = {
            normalize_ocr_text(student_info.get("student_no", "")),
            normalize_ocr_text(student_info.get("student_name", "")),
            normalize_ocr_text(student_info.get("major", "")),
        }
        consumed_values.discard("")
        candidate_lines = [
            line for line in (raw_text.splitlines() if raw_text else [])
            if normalize_ocr_text(line) not in consumed_values
        ]
        subjects, validation_errors = _parse_subject_rows_from_lines(candidate_lines)
        result = {
            "student_no": student_info.get("student_no", ""),
            "student_name": student_info.get("student_name", ""),
            "major": student_info.get("major", ""),
            "strand": student_info.get("strand", student_info.get("major", "")),
            "subjects": subjects,
            "gwa": calculate_gwa(subjects),
            "needs_review": bool(validation_errors or any(item.get("needs_review") for item in subjects)),
            "validation_errors": validation_errors,
        }
        return result

    normalized_rows = []
    for row in table_rows:
        if isinstance(row, dict) and isinstance(row.get("cells"), list):
            normalized_rows.append(row)
        elif isinstance(row, dict):
            cells = []
            for key in sorted(row.keys()):
                if key.startswith("col") or key.startswith("column"):
                    numeric = key.replace("col", "").replace("umn", "").replace("column", "").strip()
                    cells.append({"column": int(numeric or 0), "text": row.get(key)})
            if cells:
                normalized_rows.append({"row": row.get("row", len(normalized_rows)), "cells": cells})

    if not normalized_rows:
        return {
            "student_no": student_info.get("student_no", ""),
            "student_name": student_info.get("student_name", ""),
            "major": student_info.get("major", ""),
            "strand": student_info.get("strand", student_info.get("major", "")),
            "subjects": [],
            "gwa": None,
            "needs_review": True,
            "validation_errors": ["Table cells could not be reconstructed"],
        }

    subject_column = identify_subject_column(normalized_rows)
    grade_column = identify_grade_column(normalized_rows)
    subjects, errors = parse_report_card_rows(normalized_rows, subject_column=subject_column, grade_column=grade_column)
    return {
        "student_no": student_info.get("student_no", ""),
        "student_name": student_info.get("student_name", ""),
        "major": student_info.get("major", ""),
        "strand": student_info.get("strand", student_info.get("major", "")),
        "subjects": subjects,
        "gwa": calculate_gwa(subjects),
        "needs_review": bool(errors or any(item.get("needs_review") for item in subjects)),
        "validation_errors": errors,
    }
