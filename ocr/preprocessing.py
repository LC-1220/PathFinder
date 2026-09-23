import os
from typing import Optional, Tuple

import cv2
import numpy as np


MAX_IMAGE_DIMENSION = 2200


def safe_image_path(path: str) -> str:
    return os.path.abspath(path)


def load_image_from_bytes(file_bytes: bytes):
    if not file_bytes:
        raise ValueError("Empty image payload.")
    image = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("Image could not be decoded.")
    return image


def validate_image_bytes(file_bytes: bytes, max_bytes: int = 10 * 1024 * 1024):
    if not file_bytes:
        return False, "Uploaded file is empty."
    if len(file_bytes) > max_bytes:
        return False, "Uploaded file exceeds the maximum allowed size."
    try:
        image = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return False, "Image could not be decoded."
    if image is None or image.size == 0:
        return False, "Image file is corrupted or unreadable."
    return True, image


def resize_to_limit(image, max_dimension: int = MAX_IMAGE_DIMENSION):
    height, width = image.shape[:2]
    max_dim = max(width, height)
    if max_dim <= max_dimension:
        return image
    scale = max_dimension / max_dim
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)


def preprocess_for_ocr(image):
    if image is None or image.size == 0:
        raise ValueError("Input image is empty.")

    working = resize_to_limit(image.copy())
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=15)

    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    sharpen = cv2.filter2D(adaptive, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32))
    return sharpen


def deskew_if_needed(image):
    return image


def find_table_contour(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < 0.1 * (image.shape[0] * image.shape[1]):
        return None
    x, y, w, h = cv2.boundingRect(largest)
    return {"x": x, "y": y, "width": w, "height": h, "area": area}


def detect_table_lines(image):
    if image is None or image.size == 0:
        return {"horizontal": [], "vertical": [], "table_bbox": None}

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_h, iterations=1)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_v, iterations=1)
    horiz_lines = cv2.HoughLinesP(horizontal, 1, np.pi / 180, threshold=50, minLineLength=50, maxLineGap=10)
    vert_lines = cv2.HoughLinesP(vertical, 1, np.pi / 180, threshold=50, minLineLength=50, maxLineGap=10)

    table_bbox = None
    if horiz_lines is not None and vert_lines is not None:
        xs = []
        ys = []
        for line in np.concatenate([horiz_lines, vert_lines]):
            x1, y1, x2, y2 = line[0]
            xs.extend([x1, x2])
            ys.extend([y1, y2])
        if xs and ys:
            table_bbox = (min(xs), min(ys), max(xs), max(ys))
    return {"horizontal": horiz_lines or [], "vertical": vert_lines or [], "table_bbox": table_bbox}
