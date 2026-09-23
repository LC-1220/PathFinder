import re
from typing import List, Dict, Any


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_table_rows(raw_rows):
    rows = []
    for item in raw_rows or []:
        if not isinstance(item, dict):
            continue
        cells = []
        for key in sorted(item.keys()):
            if key.startswith("cell") or key.startswith("column") or key.startswith("col"):
                cells.append({
                    "column": int(str(key).lower().replace("cell", "").replace("column", "").replace("col", "").strip() or 0),
                    "text": item.get(key, ""),
                })
        if cells:
            rows.append({"row": item.get("row", len(rows)), "cells": sorted(cells, key=lambda cell: cell.get("column", 0))})
    return rows


def identify_subject_column(table_rows: List[Dict[str, Any]]) -> int:
    if not table_rows:
        return 0

    for row in table_rows:
        labels = " ".join(str(cell.get("text", "")).strip() for cell in row.get("cells", []) if str(cell.get("text", "")).strip())
        if any(token in labels.lower() for token in ["subject", "course", "name", "description", "major"]):
            header_row = row
            break
    else:
        header_row = table_rows[0]

    normalized = [str(cell.get("text", "")).strip().lower() for cell in header_row.get("cells", [])]
    for idx, text in enumerate(normalized):
        if any(token in text for token in ["subject", "course", "name", "description"]) and not any(token in text for token in ["code", "credit", "remarks", "instructor", "grade", "year", "semester"]):
            return idx
    for idx, text in enumerate(normalized):
        if text and not any(char.isdigit() for char in text):
            return idx
    return 1 if len(normalized) > 1 else 0


def identify_grade_column(table_rows: List[Dict[str, Any]]) -> int:
    if not table_rows:
        return 0

    for row in table_rows:
        labels = " ".join(str(cell.get("text", "")).strip() for cell in row.get("cells", []) if str(cell.get("text", "")).strip())
        if any(token in labels.lower() for token in ["grade", "final", "rating", "mark"]):
            header_row = row
            break
    else:
        header_row = table_rows[0]

    normalized = [str(cell.get("text", "")).strip().lower() for cell in header_row.get("cells", [])]
    for idx, text in enumerate(normalized):
        if any(token in text for token in ["grade", "final", "rating", "mark"]):
            return idx
    for idx, text in enumerate(normalized):
        if any(char.isdigit() for char in text):
            return idx
    return max(len(normalized) - 1, 0)


def group_ocr_boxes_by_row(items):
    grouped = {}
    for item in items or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box, text_info = item[0], item[1]
        if isinstance(text_info, tuple) and len(text_info) == 2:
            text, conf = text_info
        elif isinstance(text_info, dict):
            text = text_info.get("text", "")
            conf = text_info.get("confidence", 0.0)
        else:
            text = str(text_info)
            conf = 0.0
        cleaned = normalize_text(text)
        if not cleaned:
            continue
        center_y = sum(point[1] for point in box) / 4
        grouped.setdefault(round(center_y / 10), []).append({
            "text": cleaned,
            "confidence": float(conf),
            "bbox": box,
            "center_y": center_y,
        })

    rows = []
    for _, row_items in sorted(grouped.items()):
        row = sorted(row_items, key=lambda entry: min(point[0] for point in entry["bbox"]))
        rows.append({
            "items": row,
            "text": " ".join(item["text"] for item in row),
            "y": min(item["center_y"] for item in row),
        })
    return sorted(rows, key=lambda row: row["y"])
