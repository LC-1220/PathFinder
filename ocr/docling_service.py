import os
import re
import tempfile


def _markdown_table_rows(markdown):
    rows = []
    for line in str(markdown or "").splitlines():
        text = line.strip()
        if not text.startswith("|") or text.count("|") < 2:
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if not cells or all(re.fullmatch(r"[-: ]+", cell or "") for cell in cells):
            continue
        rows.append({
            "row": len(rows),
            "cells": [{"column": index, "text": cell} for index, cell in enumerate(cells)],
        })
    return rows


def _normalize_report_card_table(rows):
    if len(rows) < 2:
        return rows

    header_index = 0
    header = rows[0].get("cells", [])
    for candidate_index, candidate in enumerate(rows[:5]):
        candidate_cells = candidate.get("cells", [])
        candidate_text = [str(cell.get("text", "")).strip().lower() for cell in candidate_cells]
        if any("subject" in text for text in candidate_text) and any("grade" in text for text in candidate_text):
            header_index = candidate_index
            header = candidate_cells
            break
    header_text = [str(cell.get("text", "")).strip().lower() for cell in header]
    subject_column = next(
        (index for index, text in enumerate(header_text)
         if any(token in text for token in ("subject name", "subject", "description", "course title"))
         and not any(token in text for token in ("code", "number", "grade", "instructor", "remark"))),
        None,
    )
    grade_column = next(
        (index for index, text in enumerate(header_text)
         if any(token in text for token in ("grade", "final grade", "rating", "mark", "score"))),
        None,
    )
    if grade_column is None:
        data_rows = rows[1:]
        grade_column = max(
            range(len(header)),
            key=lambda index: sum(
                bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", str(row.get("cells", [])[index].get("text", "")).strip()))
                for row in data_rows
                if index < len(row.get("cells", []))
            ),
            default=None,
        )
    if subject_column is None:
        candidate_columns = [index for index in range(len(header)) if index != grade_column]
        subject_column = max(
            candidate_columns,
            key=lambda index: sum(
                len(str(row.get("cells", [])[index].get("text", "")).strip())
                for row in rows[1:]
                if index < len(row.get("cells", []))
            ),
            default=None,
        )
    if subject_column is None or grade_column is None:
        return rows

    normalized = [{
        "row": 0,
        "cells": [
            {"column": 0, "text": header[subject_column].get("text", "Subject")},
            {"column": 1, "text": header[grade_column].get("text", "Grade")},
        ],
    }]
    for row_index, row in enumerate(rows[header_index + 1:], start=1):
        cells = row.get("cells", [])
        if subject_column >= len(cells) or grade_column >= len(cells):
            continue
        normalized.append({
            "row": row_index,
            "cells": [
                {"column": 0, "text": cells[subject_column].get("text", "")},
                {"column": 1, "text": cells[grade_column].get("text", "")},
            ],
        })
    return normalized


def _document_table_rows(document):
    rows = []
    for table in getattr(document, "tables", []) or []:
        try:
            dataframe = table.export_to_dataframe()
            header = [str(value).strip() for value in dataframe.columns]
            rows.append({
                "row": 0,
                "cells": [{"column": index, "text": value} for index, value in enumerate(header)],
            })
            for row_index, values in enumerate(dataframe.itertuples(index=False, name=None), start=1):
                rows.append({
                    "row": row_index,
                    "cells": [
                        {"column": index, "text": str(value).strip()}
                        for index, value in enumerate(values)
                    ],
                })
            if len(rows) > 1:
                continue
        except Exception:
            rows = []
        try:
            table_markdown = table.export_to_markdown()
        except Exception:
            continue
        rows.extend(_markdown_table_rows(table_markdown))
    return rows


def _table_rows_have_text(rows):
    return any(
        str(cell.get("text", "")).strip()
        for row in rows or []
        for cell in row.get("cells", [])
    )


def _table_grade_count(rows):
    count = 0
    for row in rows[1:] if rows else []:
        for cell in row.get("cells", []):
            value = str(cell.get("text", "")).strip()
            match = re.fullmatch(r"(?:grade\s*[:\-]?\s*)?(\d{1,3}(?:\.\d{1,2})?)", value, re.IGNORECASE)
            if match and 0 <= float(match.group(1)) <= 100:
                count += 1
                break
    return count


def _subjects_from_table(rows):
    subjects = []
    header_index = 0
    header_values = []
    for candidate_index, candidate in enumerate(rows[:5]):
        values = [re.sub(r"\s+", " ", str(cell.get("text", "")).strip()).lower() for cell in candidate.get("cells", [])]
        if any("subject" in value for value in values) and any("grade" in value for value in values):
            header_index = candidate_index
            header_values = values
            break
    if not header_values and rows:
        header_values = [
            re.sub(r"\s+", " ", str(cell.get("text", "")).strip()).lower()
            for cell in rows[0].get("cells", [])
        ]
    subject_column = next(
        (index for index, value in enumerate(header_values)
         if "subject" in value and "code" not in value),
        None,
    )
    grade_column = next(
        (index for index, value in enumerate(header_values)
         if any(token in value for token in ("grade", "rating", "mark", "score"))),
        None,
    )
    for row in rows[header_index + 1:]:
        cells = row.get("cells", [])
        if len(cells) < 2:
            continue
        values = [re.sub(r"\s+", " ", str(cell.get("text", "")).strip()) for cell in cells]
        valid_grade_matches = []
        for index, value in enumerate(values):
            for candidate in re.finditer(r"\b(\d{2,3}(?:[.,]\d{1,2})?)\b", value):
                numeric = float(candidate.group(1).replace(",", "."))
                if 70 <= numeric <= 100:
                    valid_grade_matches.append((index, candidate, numeric))
        if valid_grade_matches:
            grade_index, grade_match, grade = valid_grade_matches[-1]
            subject = values[subject_column] if subject_column is not None and subject_column < len(values) else ""
        elif subject_column is not None and grade_column is not None and max(subject_column, grade_column) < len(values):
            continue
        else:
            grade_matches = []
            for index, value in enumerate(values):
                numeric_match = re.search(r"\b(\d{1,3}(?:[.,]\d{1,2})?)\b", value)
                if numeric_match:
                    numeric_value = float(numeric_match.group(1).replace(",", "."))
                    if 0 <= numeric_value <= 100:
                        grade_matches.append((index, numeric_match, numeric_value))
            if not grade_matches:
                continue
            grade_index, grade_match, grade = grade_matches[-1]
            candidates = [
                value for index, value in enumerate(values)
                if index != grade_index
                and value
                and not re.fullmatch(r"#?\d{2,6}\s*[A-Za-z]{0,8}\d{0,4}", value)
                and value.lower() not in {"subject", "subject name", "grade", "remarks", "passed", "failed"}
                and not re.fullmatch(r"(?:passed|failed|incomplete|conditional|withdrawn|inc)", value, re.IGNORECASE)
            ]
            subject = values[subject_column] if subject_column is not None and subject_column < len(values) else ""
            if not subject or re.fullmatch(r"#?\d{3,6}\s*[A-Za-z]{1,8}\d{0,4}", subject):
                subject = next(
                    (value for value in candidates
                     if not re.fullmatch(r"#?\d{3,6}\s*[A-Za-z]{1,8}\d{0,4}", value)),
                    candidates[0] if candidates else "",
                )
        if not subject or not grade_match:
            continue
        subject = re.sub(r"&\s*#?124;?", " ", subject, flags=re.IGNORECASE)
        subject = re.sub(r"^#?\d{3,6}\s*[A-Za-z]{1,8}\d{0,4}\s+", "", subject).strip()
        subject = re.sub(r"^#?\d{1,6}\s+", "", subject).strip()
        subject = re.sub(r"\s+\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s*,\s*[A-Za-z.\s]+$", "", subject).strip()
        subject = re.sub(r"\s+\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s+[A-Z]\.?$", "", subject).strip()
        subject = re.sub(r"\s+\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\s+\d+$", "", subject).strip()
        subject = re.sub(r"\s+", " ", subject).strip(" -:|/")
        if not subject:
            continue
        grade = float(grade_match.group(1).replace(",", "."))
        if not 0 <= grade <= 100:
            continue
        subjects.append({
            "subject_name": subject,
            "grade": grade,
            "needs_review": not 70 <= grade <= 100,
            "confidence": 0.96,
        })
    return subjects


_DOCLING_CONVERTER = None


def _get_docling_converter():
    """Build the Docling converter once and reuse it; rebuilding reloads OCR/TableFormer models every call."""
    global _DOCLING_CONVERTER
    if _DOCLING_CONVERTER is not None:
        return _DOCLING_CONVERTER

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
        StandardPdfPipeline,
    )

    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        images_scale=2.0,
    )
    pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
    pipeline_options.table_structure_options.do_cell_matching = True
    _DOCLING_CONVERTER = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(
                pipeline_cls=StandardPdfPipeline,
                pipeline_options=pipeline_options,
            ),
        }
    )
    return _DOCLING_CONVERTER


def scan_report_card_docling(file_bytes, filename="report_card.pdf"):
    """Convert one report card with Docling and return the app's OCR payload shape."""
    try:
        converter = _get_docling_converter()
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Run 'python -m pip install -r requirements.txt' first."
        ) from exc

    suffix = os.path.splitext(filename or "report_card.pdf")[1].lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        suffix = ".pdf"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_file.write(file_bytes)
        temp_path = temp_file.name

    try:
        result = converter.convert(temp_path)
        markdown = result.document.export_to_markdown()
        table_rows = _markdown_table_rows(markdown)
        native_table_rows = _document_table_rows(result.document)
        if _table_rows_have_text(native_table_rows) and (
            not _table_rows_have_text(table_rows) or len(native_table_rows) > len(table_rows)
        ):
            table_rows = native_table_rows
    except Exception as exc:
        raise RuntimeError(f"Docling conversion failed: {exc}") from exc
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


    normalized_table = _normalize_report_card_table(table_rows)
    original_subjects = _subjects_from_table(table_rows)
    normalized_subjects = _subjects_from_table(normalized_table)
    if len(original_subjects) >= len(normalized_subjects):
        output_table = table_rows
        output_subjects = original_subjects
    else:
        output_table = normalized_table
        output_subjects = normalized_subjects
    lines = [line.strip() for line in str(markdown or "").splitlines() if line.strip()]
    return {
        "result": [],
        "raw_text": "\n".join(lines),
        "student_info_raw": [{"text": line} for line in lines],
        "table": output_table,
        "subjects": output_subjects,
        "provider": "docling",
    }