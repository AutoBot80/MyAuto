"""
Consolidated PDF pre-OCR failure: split pages to compressed JPEGs for manual slot assignment,
then materialize ``for_OCR/`` without running Textract/OCR extraction.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from uuid import uuid4

from PIL import Image

from app.config import get_add_sales_pre_ocr_work_dir, get_ocr_output_dir, get_uploaded_scans_sale_subfolder_leaf, get_uploads_dir
from app.services.ocr_extraction_log import append_ocr_extraction_log
from app.services.page_classifier import (
    FILENAME_AADHAR_BACK,
    FILENAME_AADHAR_FRONT,
    FILENAME_SALES_DETAIL_SHEET_PDF,
)
from app.services.post_ocr_service import POST_OCR_MAX_FILE_BYTES, _jpeg_bytes_to_single_page_pdf, _jpeg_bytes_under_max

logger = logging.getLogger(__name__)

FOR_OCR_SUBDIR = "for_OCR"
MANUAL_SESSIONS = "manual_sessions"
SESSION_ID_RE = re.compile(r"^[a-f0-9]{32}$")

ROLE_AADHAR_FRONT = "aadhar_front"
ROLE_AADHAR_BACK = "aadhar_back"
ROLE_DETAILS = "details"
ROLE_UNUSED = "unused"


def _session_base(dealer_id: int, session_id: str) -> Path:
    return get_add_sales_pre_ocr_work_dir(dealer_id) / MANUAL_SESSIONS / session_id


def validate_session_id(session_id: str) -> bool:
    return bool(session_id and SESSION_ID_RE.match(session_id))


def write_manual_session_jpegs(
    dealer_id: int,
    dest_pdf: Path,
    page_images: dict[int, Image.Image],
) -> tuple[str, int]:
    """
    Write one JPEG per page under manual_sessions/{uuid}/page_NN.jpg, each ≤ POST_OCR_MAX_FILE_BYTES.
    Returns (session_id, page_count).
    """
    from app.services.pre_ocr_service import _pdf_to_page_images, _pil_rgb_to_jpeg_bytes

    imgs = page_images
    if not imgs:
        pages, _osd = _pdf_to_page_images(dest_pdf)
        imgs = {idx: im for idx, im in pages}

    if not imgs:
        raise ValueError("No pages to split from PDF")

    session_id = uuid4().hex
    base = _session_base(dealer_id, session_id)
    base.mkdir(parents=True, exist_ok=True)

    for idx in sorted(imgs.keys()):
        raw = _pil_rgb_to_jpeg_bytes(imgs[idx])
        compressed = _jpeg_bytes_under_max(raw, POST_OCR_MAX_FILE_BYTES)
        (base / f"page_{idx + 1:02d}.jpg").write_bytes(compressed)

    return session_id, len(imgs)


def apply_manual_session(
    dealer_id: int,
    session_id: str,
    mobile: str,
    assignments: dict[str, str],
    *,
    ocr_output_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """
    Validate session, copy mapped JPEGs into ``for_OCR/``, embed details as single-page PDF,
    merge unused pages into ``unused.pdf``. Does not run OcrService.

    ``assignments`` maps "0".."n-1" to aadhar_front | aadhar_back | details | unused.
    Returns (subfolder_name e.g. mobile_ddmmyy, list of saved file basenames at sale root and under for_OCR).
    """
    import fitz

    if not validate_session_id(session_id):
        raise ValueError("Invalid session id")
    digits = "".join(c for c in mobile if c.isdigit())
    if len(digits) != 10:
        raise ValueError("Invalid mobile. Expected 10 digits.")

    session_dir = _session_base(dealer_id, session_id)
    if not session_dir.is_dir():
        raise ValueError("Session not found or expired")

    page_files = sorted(session_dir.glob("page_*.jpg"))
    page_count = len(page_files)
    if page_count < 3:
        raise ValueError("Session has fewer than 3 pages")

    valid_roles = {ROLE_AADHAR_FRONT, ROLE_AADHAR_BACK, ROLE_DETAILS, ROLE_UNUSED}
    for i in range(page_count):
        key = str(i)
        if key not in assignments:
            raise ValueError(f"Missing assignment for page {i + 1}")
        if assignments[key] not in valid_roles:
            raise ValueError(f"Invalid role for page {i + 1}")

    role_by_page = {i: assignments[str(i)] for i in range(page_count)}
    from collections import Counter

    c = Counter(role_by_page.values())
    if c[ROLE_AADHAR_FRONT] != 1 or c[ROLE_AADHAR_BACK] != 1 or c[ROLE_DETAILS] != 1:
        raise ValueError(
            "Assign exactly one page to Aadhar front, one to Aadhar back, and one to Details; "
            "other pages must be Unused."
        )

    subfolder = get_uploaded_scans_sale_subfolder_leaf(digits)
    uploads_dir = get_uploads_dir(dealer_id)
    sale_dir = uploads_dir / subfolder
    for_ocr = sale_dir / FOR_OCR_SUBDIR
    for_ocr.mkdir(parents=True, exist_ok=True)

    def page_path(i: int) -> Path:
        return session_dir / f"page_{i + 1:02d}.jpg"

    front_i = next(i for i, r in role_by_page.items() if r == ROLE_AADHAR_FRONT)
    back_i = next(i for i, r in role_by_page.items() if r == ROLE_AADHAR_BACK)
    det_i = next(i for i, r in role_by_page.items() if r == ROLE_DETAILS)

    shutil.copy2(page_path(front_i), for_ocr / FILENAME_AADHAR_FRONT)
    shutil.copy2(page_path(back_i), for_ocr / FILENAME_AADHAR_BACK)

    det_jpeg = page_path(det_i).read_bytes()
    det_pdf = _jpeg_bytes_to_single_page_pdf(det_jpeg)
    (for_ocr / FILENAME_SALES_DETAIL_SHEET_PDF).write_bytes(det_pdf)

    unused_indices = [i for i, r in role_by_page.items() if r == ROLE_UNUSED]
    saved: list[str] = [FOR_OCR_SUBDIR + "/" + FILENAME_AADHAR_FRONT, FOR_OCR_SUBDIR + "/" + FILENAME_AADHAR_BACK, FOR_OCR_SUBDIR + "/" + FILENAME_SALES_DETAIL_SHEET_PDF]

    if unused_indices:
        merged = fitz.open()
        try:
            for i in sorted(unused_indices):
                p = page_path(i)
                with Image.open(p) as im:
                    w, h = im.size
                page = merged.new_page(width=float(w), height=float(h))
                page.insert_image(page.rect, filename=str(p))
            merged.save(str(sale_dir / "unused.pdf"))
        finally:
            merged.close()
        saved.append("unused.pdf")

    try:
        shutil.rmtree(session_dir, ignore_errors=True)
    except OSError:
        logger.warning("Could not remove manual session dir %s", session_dir)

    ocr_dir = ocr_output_dir or get_ocr_output_dir(dealer_id)
    append_ocr_extraction_log(
        ocr_dir,
        subfolder,
        "pre",
        "Manual fallback: for_OCR populated from user-assigned pages (no Textract/OCR).",
    )

    return subfolder, saved


def manual_session_page_path(dealer_id: int, session_id: str, page_1based: int) -> Path | None:
    """Return path to page_NN.jpg if it exists and session is valid."""
    if not validate_session_id(session_id) or page_1based < 1:
        return None
    p = _session_base(dealer_id, session_id) / f"page_{page_1based:02d}.jpg"
    return p if p.is_file() else None
