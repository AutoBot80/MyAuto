"""
Pre-OCR for bulk upload: Tesseract pass on each PDF page (in-memory raster), classify (Aadhaar, Details, …),
then write normalized document files under ``Uploaded scans/{dealer_id}/{mobile}_ddmmyy/``.

**Raw folder:** ``…/{mobile}_ddmmyy/raw/`` holds **PDFs only**: the consolidated scan copy and per-page
``page_NN.pdf`` files. Rasters are **not** stored under ``raw/``; pre-OCR renders each PDF page to **PIL RGB**
(PyMuPDF) and passes pixels straight to Tesseract (no intermediate JPEG). Upload-facing JPEGs/PDFs for
:mod:`sales_ocr_service` are written under ``for_OCR/`` after classification.

**for_OCR folder:** ``…/{mobile}_ddmmyy/for_OCR/`` holds classified outputs (JPEGs such as
``Aadhar_front.jpg``, ``Insurance.jpg``, and ``Sales_Detail_Sheet.pdf``) plus one **single-page PDF per
document slot** (``Aadhar.pdf``, ``Aadhar_back.pdf``, …) for :mod:`sales_ocr_service` Textract, populated
by classification and :func:`_sync_for_ocr_pdfs` (copy from ``raw/page_NN.pdf`` or embed split JPEGs for
same-page Aadhaar). :mod:`post_ocr_service` then compresses and moves upload-facing files to the sale root.

**Aadhaar handling (bulk):** separate front/back pages → copy as-is; same page top/bottom →
:func:`split_aadhar_consolidated` then letter scissor fallback via :func:`_process_same_page_aadhar`;
UIDAI letter layout → :func:`crop_aadhar_letter_below_scissors` / :func:`split_aadhar_letter_vertical`.

Add Sales uploads call :func:`orient_and_normalize_sale_documents` before Textract; :mod:`sales_ocr_service`
does not repeat scissor splits.

**Chassis pencil mark:** :func:`write_pencil_mark_from_details_page` crops the top-right region of the
classified Details page (default: top-right quadrant) and saves ``pencil_mark.jpeg`` at the sale folder root
(not under ``for_OCR/``).
"""

import os
import re
import shutil
import logging
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from app.config import (
    BULK_UPLOAD_DIR,
    OCR_LANG,
    OCR_PSM,
    OCR_PREPROCESS,
    OCR_PRE_OCR_TEXTRACT_AADHAR_FALLBACK,
    OCR_PRE_OCR_TEXTRACT_DETAILS,
    UPLOADS_DIR,
    get_add_sales_pre_ocr_work_dir,
    get_uploads_dir,
)
from app.services.ocr_extraction_log import append_pre_ocr_step_lines
from app.services.page_classifier import (
    aadhar_combined_ocr_looks_ok,
    aadhar_front_face_ocr,
    classify_page_by_text,
    classify_pages_from_ocr_text,
    extract_page_text_from_pre_ocr_blocks,
    maybe_swap_aadhar_page_indices,
    should_swap_aadhar_pages_by_dob_gender,
    PAGE_TYPE_TO_FILENAME,
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_AADHAR_COMBINED,
    PAGE_TYPE_DETAILS,
    PAGE_TYPE_INSURANCE,
    PAGE_TYPE_UNUSED,
    FILENAME_AADHAR_FRONT,
    FILENAME_SALES_DETAIL_SHEET_PDF,
)

logger = logging.getLogger(__name__)

PROCESSING_DIR = BULK_UPLOAD_DIR / "Processing"
SUCCESS_DIR = BULK_UPLOAD_DIR / "Success"
ERROR_DIR = BULK_UPLOAD_DIR / "Error"
REJECTED_DIR = BULK_UPLOAD_DIR / "Rejected scans"

# Textract input PDFs after bulk pre-OCR (single-page, oriented); see :func:`_sync_for_ocr_pdfs`.
FOR_OCR_SUBDIR = "for_OCR"


def _for_ocr_pdf_basename(root_slot_filename: str) -> str:
    """PDF filename under ``for_OCR/``; front Aadhaar slot keeps ``Aadhar.pdf`` for Textract callers."""
    if root_slot_filename == FILENAME_AADHAR_FRONT:
        return "Aadhar.pdf"
    if root_slot_filename.lower().endswith(".pdf"):
        return Path(root_slot_filename).name
    return f"{Path(root_slot_filename).stem}.pdf"

# Details sheet — Chassis Pencil Mark crop (fractions of page width/height on the oriented JPEG).
# Default: **top-right quadrant** (right half × top half), where the pencil-mark box typically sits.
PENCIL_MARK_FILENAME = "pencil_mark.jpeg"
PENCIL_MARK_X0_FRAC = float(os.getenv("PENCIL_MARK_X0_FRAC", "0.5"))
PENCIL_MARK_X1_FRAC = float(os.getenv("PENCIL_MARK_X1_FRAC", "1.0"))
PENCIL_MARK_Y0_FRAC = float(os.getenv("PENCIL_MARK_Y0_FRAC", "0.0"))
PENCIL_MARK_Y1_FRAC = float(os.getenv("PENCIL_MARK_Y1_FRAC", "0.5"))
# When "1", find the dense dark horizontal rubbing inside the quadrant ROI (see :func:`_detect_pencil_rubbing_tight_bbox`).
PENCIL_MARK_TIGHT_DETECT = os.getenv("PENCIL_MARK_TIGHT_DETECT", "1").lower() not in ("0", "false", "no")

# Indian mobile: 10 digits starting with 6, 7, 8, or 9 (optional +91 prefix, spaces, dashes)
MOBILE_PATTERN = re.compile(r"(?:\+91[\s\-]*)?([6-9]\d{9})\b")
# Primary customer row on Details sheet (must win over "Alternate No." below).
DETAILS_SHEET_MOBILE_NUMBER_LABEL = re.compile(
    r"(?i)mobile\s*number\s*[:\s]*([6-9]\d{9})\b",
)
# Details sheet row: "Tel. Name No. 8955843403, …" — do **not** use a bare ``No.`` token: it matches
# "Alternate No.: …" and wrongly picks the alternate mobile before the primary.
CUSTOMER_MOBILE_CONTEXT = re.compile(
    r"(?:Tel\.?\s*Name\s*No\.?|Mobile(?:\s+Number)?|Phone)\s*[:\s]*([6-9]\d{9})",
    re.IGNORECASE,
)
# Aadhar number: nnnn nnnn nnnn or 12 consecutive digits
AADHAR_PATTERN = re.compile(r"\b(\d{4}\s+\d{4}\s+\d{4})\b|\b(\d{12})\b")
# Name patterns: Aadhar (before Male/Female), Details (near Tel), Insurance (Nominee Name)
NAME_BEFORE_GENDER = re.compile(r"([A-Za-z][A-Za-z\s\.]{2,40}?)\s+(?:Male|Female)\s*/\s*(?:M|F)\b", re.IGNORECASE)
NAME_AFTER_LABEL = re.compile(r"(?:Name|Nominee\s+Name)\s*[:\s]+([A-Za-z][A-Za-z\s\.]{2,60})", re.IGNORECASE)
# Sniff patterns: avoid promoting UNUSED pages that are actually Details/Insurance
_DETAILS_OR_INSURANCE_SNIFF = [
    re.compile(r"frame\s*no\.?|chassis|engine\s*no\.?|key\s*no\.?", re.IGNORECASE),
    re.compile(r"gross\s*premium|policy\s*no\.?|cert\.?\s*no\.?", re.IGNORECASE),
]


def osd_deskew_clockwise_degrees_from_image(img: Image.Image) -> int:
    """
    Return clockwise degrees (0, 90, 180, or 270) to rotate a raster so text reads upright,
    using Tesseract **OSD** (``osd`` trained data). Returns **0** on failure, ambiguous output,
    or orientation confidence below **2.0**.
    """
    import re

    import pytesseract

    if img.width < 12 or img.height < 12:
        return 0
    try:
        work = img.convert("RGB") if img.mode != "RGB" else img
        osd = pytesseract.image_to_osd(work, lang="osd")
    except Exception as e:
        logger.debug("osd_deskew_clockwise_degrees_from_image: OSD skipped: %s", e)
        return 0

    m_rot = re.search(r"Rotate:\s*(\d+)", osd)
    if not m_rot:
        return 0
    rot_cw = int(m_rot.group(1)) % 360
    if rot_cw == 0:
        return 0

    m_conf = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
    if m_conf and float(m_conf.group(1)) < 2.0:
        logger.info(
            "OSD orientation confidence low (%s); not rotating (Rotate would be %s°)",
            m_conf.group(1),
            rot_cw,
        )
        return 0
    return rot_cw


def osd_deskew_clockwise_degrees(image_bytes: bytes) -> int:
    """Same as :func:`osd_deskew_clockwise_degrees_from_image` but for encoded image bytes (e.g. JPEG on disk)."""
    import io

    if not image_bytes or len(image_bytes) < 800:
        return 0
    try:
        return osd_deskew_clockwise_degrees_from_image(Image.open(io.BytesIO(image_bytes)))
    except Exception as e:
        logger.debug("osd_deskew_clockwise_degrees: OSD skipped: %s", e)
        return 0


def correct_image_orientation_upright_image(img: Image.Image) -> Image.Image:
    """
    Rotate a PIL image to upright using Tesseract OSD. No JPEG encode/decode — use this on PDF renders.

    On OSD failure or ambiguous output, returns the input image unchanged.
    """
    rot_cw = osd_deskew_clockwise_degrees_from_image(img)
    if rot_cw == 0:
        return img

    work = img.convert("RGB") if img.mode != "RGB" else img
    # Tesseract ``Rotate`` = degrees to turn **clockwise** to deskew. PIL positive angle = CCW.
    img2 = work.rotate(-rot_cw, expand=True, fillcolor="white")
    logger.info("correct_image_orientation_upright_image: applied %s° CW correction (PIL -%s)", rot_cw, rot_cw)
    return img2


def correct_image_orientation_upright(image_bytes: bytes) -> bytes:
    """
    Detect page orientation via Tesseract **OSD** and rotate so text reads normally (JPEG in / JPEG out).

    For PDF → Tesseract, prefer :func:`correct_image_orientation_upright_image` on a PIL RGB render
    to avoid a lossy JPEG round-trip.
    """
    import io

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return image_bytes

    img2 = correct_image_orientation_upright_image(img)
    if img2 is img:
        return image_bytes

    out = io.BytesIO()
    img2.save(out, "JPEG", quality=92)
    return out.getvalue()


def _pil_rgb_to_jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    """Encode RGB (or convertible) PIL image to JPEG for APIs that still expect bytes (cv2, Aadhaar splits)."""
    import io

    work = img.convert("RGB") if img.mode != "RGB" else img
    buf = io.BytesIO()
    work.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _pdf_to_page_images(
    pdf_path: Path,
    max_pages: int = 20,
    *,
    fix_orientation: bool = True,
) -> tuple[list[tuple[int, Image.Image]], dict[int, int]]:
    """
    Render PDF pages to **PIL RGB** (PyMuPDF pixmap). Tesseract needs pixels, not vectors; this avoids
    an intermediate **JPEG** encode/decode — pass images straight to :func:`_tesseract_ocr_image`.

    Returns ``(page_list, osd_rotations)`` where ``osd_rotations`` maps page index to the clockwise
    OSD rotation applied (0/90/180/270). Downstream code that writes PDFs can reuse these values
    instead of re-running OSD.
    """
    import fitz

    result: list[tuple[int, Image.Image]] = []
    osd_rotations: dict[int, int] = {}
    doc = fitz.open(str(pdf_path))
    try:
        for i in range(min(doc.page_count, max_pages)):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            if fix_orientation:
                rot_cw = osd_deskew_clockwise_degrees_from_image(img)
                osd_rotations[i] = rot_cw
                if rot_cw:
                    work = img.convert("RGB") if img.mode != "RGB" else img
                    img = work.rotate(-rot_cw, expand=True, fillcolor="white")
            else:
                osd_rotations[i] = 0
            result.append((i, img))
    finally:
        doc.close()
    return result, osd_rotations


def _rasterize_single_pdf_page(pdf_path: Path, page_0: int, *, dpi: int = 150) -> Image.Image:
    """Render one PDF page to PIL RGB via PyMuPDF."""
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        pix = doc[page_0].get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return correct_image_orientation_upright_image(img)
    finally:
        doc.close()


def _pil_image_to_jpeg_bytes(img: Image.Image, quality: int = 92) -> bytes:
    """Encode a PIL image to JPEG bytes (Textract accepts JPEG/PNG ≤ 5 MB)."""
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _textract_ddt_for_page(
    pdf_path: Path,
    page_0: int,
    page_image: Image.Image | None = None,
) -> str | None:
    """
    Run Textract DetectDocumentText on a single PDF page.
    When ``page_image`` is provided (pre-rendered by :func:`_pdf_to_page_images`), skips re-rasterization.
    Returns the full text (lines joined) or ``None`` on error.
    """
    from app.services.sales_textract_service import extract_text_from_bytes

    try:
        img = page_image if page_image is not None else _rasterize_single_pdf_page(pdf_path, page_0)
        jpeg_bytes = _pil_image_to_jpeg_bytes(img)
        result = extract_text_from_bytes(jpeg_bytes)
        if result.get("error"):
            logger.warning("Textract DDT page %d failed: %s", page_0 + 1, result["error"])
            return None
        text = result.get("full_text", "")
        if text.strip():
            return text
        logger.info("Textract DDT page %d returned empty text, keeping Tesseract output", page_0 + 1)
        return None
    except Exception:
        logger.exception("Textract DDT page %d exception", page_0 + 1)
        return None


# Aadhaar back: enough signal that Tesseract likely captured address / UIDAI footer (DDT fallback if not).
_AADHAR_BACK_OCR_OK = re.compile(
    r"(?i)(?:uidai\.gov|address\s+of\s+the\s+cardholder|address\s+is\s+as\s+per|"
    r"पता|virtual\s*id|help@uidai)",
)


def _aadhar_page_needs_textract_fallback(page_text: str, page_type: str) -> bool:
    """True when Tesseract output for an Aadhaar-classified page is too weak; try Textract DDT."""
    t = (page_text or "").strip()
    if not t:
        return True
    if page_type == PAGE_TYPE_AADHAR:
        if not aadhar_front_face_ocr(t):
            return True
        if sum(1 for c in t if c.isdigit()) < 8:
            return True
        return False
    if page_type == PAGE_TYPE_AADHAR_BACK:
        if len(t) < 50:
            return True
        if not _AADHAR_BACK_OCR_OK.search(t):
            return True
        if sum(1 for c in t if c.isdigit()) < 4 and len(t) < 200:
            return True
        return False
    if page_type == PAGE_TYPE_AADHAR_COMBINED:
        return not aadhar_combined_ocr_looks_ok(t)
    return False


def _replace_page_block_in_full_text(full_text: str, page_0: int, new_page_text: str) -> str:
    """
    Replace the ``--- Page N ---`` block in ``full_text`` with new content.
    If the block doesn't exist, appends it.
    """
    page_num = page_0 + 1
    pat = re.compile(
        rf"(---\s*Page\s+{page_num}\s*---\s*)(.*?)(?=\n---\s*Page\s+\d+\s*---|\Z)",
        re.DOTALL,
    )
    replacement = rf"\g<1>{new_page_text}"
    new, n = pat.subn(replacement, full_text, count=1)
    if n:
        return new
    return full_text + f"\n\n--- Page {page_num} ---\n{new_page_text}"


def _trim_pencil_bbox_to_dark_ink(roi_bgr, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Shrink a bbox to pixels that are not near-white (drops thin outer frame / margins)."""
    import cv2
    import numpy as np

    rh, rw = roi_bgr.shape[:2]
    x = max(0, min(x, rw - 1))
    y = max(0, min(y, rh - 1))
    w = max(1, min(w, rw - x))
    h = max(1, min(h, rh - y))
    patch = roi_bgr[y : y + h, x : x + w]
    g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    _, bin_inv = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ys = np.where(bin_inv.sum(axis=1) > 0)[0]
    xs = np.where(bin_inv.sum(axis=0) > 0)[0]
    if ys.size == 0 or xs.size == 0:
        return x, y, w, h
    pad = max(1, min(3, w // 60))
    y0, y1 = int(ys[0]), int(ys[-1])
    x0, x1 = int(xs[0]), int(xs[-1])
    nx = x + x0 - pad
    ny = y + y0 - pad
    nw = x1 - x0 + 1 + 2 * pad
    nh = y1 - y0 + 1 + 2 * pad
    nx = max(0, nx)
    ny = max(0, ny)
    nw = min(nw, rw - nx)
    nh = min(nh, rh - ny)
    return nx, ny, max(1, nw), max(1, nh)


def _detect_pencil_rubbing_tight_bbox(roi_bgr) -> tuple[int, int, int, int] | None:
    """
    Inside the quadrant ROI, find the dark grey **pencil rubbing** block: dense, horizontal, in the upper band.

    Returns ``(x, y, w, h)`` relative to ``roi_bgr``, or None to fall back to the full ROI crop.
    """
    import cv2
    import numpy as np

    rh, rw = roi_bgr.shape[:2]
    if rh < 40 or rw < 40:
        return None

    # Upper band: rubbing sits above "DETAIL SHEET" / form rows in this quadrant.
    y_cut = max(rh // 4, int(rh * 0.72))
    search = roi_bgr[0:y_cut, :]
    sh, sw = search.shape[:2]

    def pick_from_binary(bin255) -> tuple[int, int, int, int] | None:
        kx = max(12, sw // 20)
        ky = max(4, sh // 25)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
        closed = cv2.morphologyEx(bin255, cv2.MORPH_CLOSE, kernel, iterations=2)
        closed = cv2.morphologyEx(
            closed,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        )
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = max(200, (sw * sh) * 0.012)
        best: tuple[int, int, int, int] | None = None
        best_score = 0.0
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < 20 or bh < 8:
                continue
            ar = bw / float(max(1, bh))
            if ar < 1.8 or ar > 40:
                continue
            rect_a = bw * bh
            extent = area / float(rect_a) if rect_a > 0 else 0
            if extent < 0.12:
                continue
            cy = by + bh / 2.0
            # Prefer candidates in the upper part of the search strip (label + box live here).
            upper_bias = 1.0 + 0.9 * max(0.0, (sh * 0.55 - cy) / (sh * 0.55 + 1e-6))
            score = area * (0.25 + 0.75 * min(extent, 0.95)) * upper_bias
            if score > best_score:
                best_score = score
                best = (bx, by, bw, bh)
        return best

    gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    box = pick_from_binary(otsu)
    if box is None:
        t = float(np.percentile(blur, 28))
        pct = ((blur < t).astype(np.uint8) * 255)
        box = pick_from_binary(pct)
    if box is None:
        return None
    bx, by, bw, bh = box
    if bw * bh > 0.88 * sw * sh:
        return None
    return bx, by, bw, bh


def extract_details_chassis_pencil_mark_jpeg(page_jpeg_bytes: bytes) -> bytes | None:
    """
    Crop the **Chassis Pencil Mark** region from a full Details sheet page (JPEG bytes).

    First selects the **top-right quadrant** (right half × top half by default) via
    :data:`PENCIL_MARK_*_FRAC`, then — when :data:`PENCIL_MARK_TIGHT_DETECT` is enabled — locates the
    dark horizontal **rubbing** block inside that ROI (threshold + morphology + contour scoring) and
    trims to non-white ink. If detection fails, keeps the full quadrant crop.

    Never raises: failures return ``None`` (optional artifact; must not abort upload/OCR).
    """
    try:
        import cv2
        import numpy as np

        if not page_jpeg_bytes or len(page_jpeg_bytes) < 800:
            return None
        nparr = np.frombuffer(page_jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        if h < 80 or w < 80:
            return None

        x0 = int(max(0, min(w - 1, PENCIL_MARK_X0_FRAC * w)))
        x1 = int(max(x0 + 1, min(w, PENCIL_MARK_X1_FRAC * w)))
        y0 = int(max(0, min(h - 1, PENCIL_MARK_Y0_FRAC * h)))
        y1 = int(max(y0 + 1, min(h, PENCIL_MARK_Y1_FRAC * h)))

        crop = img[y0:y1, x0:x1]
        if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
            return None

        if PENCIL_MARK_TIGHT_DETECT:
            tight = _detect_pencil_rubbing_tight_bbox(crop)
            if tight is not None:
                tx, ty, tw, th = _trim_pencil_bbox_to_dark_ink(crop, *tight)
                if tw >= 12 and th >= 8:
                    crop = crop[ty : ty + th, tx : tx + tw]

        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return None
        return buf.tobytes()
    except Exception as e:
        logger.warning("pencil_mark: extract failed (non-fatal): %s", e)
        return None


def write_pencil_mark_from_details_page(sale_dir: Path, page_jpeg_bytes: bytes) -> bool:
    """
    Write ``pencil_mark.jpeg`` under ``sale_dir`` from the full Details page raster.

    Returns True if a non-empty crop was written. Never raises — optional crop must not fail the pipeline.
    """
    try:
        out = extract_details_chassis_pencil_mark_jpeg(page_jpeg_bytes)
        if not out:
            logger.warning("pencil_mark: crop failed or empty for %s", sale_dir)
            return False
        sale_dir.mkdir(parents=True, exist_ok=True)
        dest = sale_dir / PENCIL_MARK_FILENAME
        dest.write_bytes(out)
        logger.info("Wrote %s (Chassis Pencil Mark crop)", dest.name)
        return True
    except OSError as e:
        logger.warning("pencil_mark: could not write %s: %s", sale_dir, e)
        return False
    except Exception as e:
        logger.warning("pencil_mark: unexpected error for %s: %s", sale_dir, e)
        return False


def try_write_pencil_mark_from_details_jpeg_file(sale_dir: Path, details_jpeg_path: Path) -> bool:
    """
    If ``Details.jpg`` (or path) exists, crop and write ``pencil_mark.jpeg`` (e.g. Add Sales uploads).
    """
    if not details_jpeg_path.is_file():
        return False
    try:
        return write_pencil_mark_from_details_page(sale_dir, details_jpeg_path.read_bytes())
    except OSError as e:
        logger.warning("pencil_mark: read %s: %s", details_jpeg_path, e)
        return False


def sale_folder_has_details_for_pencil_crop(sale_dir: Path) -> bool:
    """
    True when a Details page file is present (JPEG or PDF), so a missing ``pencil_mark.jpeg`` may warrant
    a user-visible warning — not an error.
    """
    if (sale_dir / "Details.jpg").is_file():
        return True
    if (sale_dir / FILENAME_SALES_DETAIL_SHEET_PDF).is_file():
        return True
    return (sale_dir / FOR_OCR_SUBDIR / FILENAME_SALES_DETAIL_SHEET_PDF).is_file()


def try_write_pencil_mark_for_sale_folder(sale_dir: Path) -> bool:
    """
    Chassis pencil-mark crop: from ``Details.jpg`` if present, else rasterize ``Sales_Detail_Sheet.pdf`` page 1
    (sale root or ``for_OCR/`` after consolidated pre-OCR).
    """
    legacy = sale_dir / "Details.jpg"
    if legacy.is_file():
        return try_write_pencil_mark_from_details_jpeg_file(sale_dir, legacy)
    pdfp = sale_dir / FILENAME_SALES_DETAIL_SHEET_PDF
    if not pdfp.is_file():
        alt = sale_dir / FOR_OCR_SUBDIR / FILENAME_SALES_DETAIL_SHEET_PDF
        if alt.is_file():
            pdfp = alt
    if not pdfp.is_file():
        return False
    try:
        import io

        import fitz
        from PIL import Image

        doc = fitz.open(str(pdfp))
        try:
            if doc.page_count < 1:
                return False
            pix = doc[0].get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            return write_pencil_mark_from_details_page(sale_dir, buf.getvalue())
        finally:
            doc.close()
    except Exception as e:
        logger.warning("pencil_mark: rasterize %s: %s", pdfp, e)
        return False


def _export_single_page_pdfs_to_raw(
    pdf_path: Path,
    raw_dir: Path,
    max_pages: int = 20,
    osd_rotations: dict[int, int] | None = None,
) -> None:
    """
    Split a multi-page PDF into ``page_01.pdf``, ``page_02.pdf``, … under ``raw_dir``
    (one PDF per original page). Sets **PDF page rotation** so the saved single-page PDF is upright.

    When ``osd_rotations`` is provided (pre-computed by :func:`_pdf_to_page_images`), skips the
    expensive per-page rasterization + Tesseract OSD call; otherwise falls back to computing OSD here.
    Does **not** write JPEGs to ``raw/`` (PDF archival only).
    """
    import fitz

    raw_dir.mkdir(parents=True, exist_ok=True)
    src = fitz.open(str(pdf_path))
    try:
        n = min(src.page_count, max_pages)
        for i in range(n):
            if osd_rotations is not None and i in osd_rotations:
                rot = osd_rotations[i]
            else:
                page = src[i]
                pix = page.get_pixmap(dpi=150)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                rot = osd_deskew_clockwise_degrees_from_image(img)

            dst = fitz.open()
            try:
                dst.insert_pdf(src, from_page=i, to_page=i)
                p = dst[0]
                if rot:
                    new_r = (int(p.rotation) + rot) % 360
                    p.set_rotation(new_r)
                    logger.info(
                        "raw page_%02d.pdf: OSD +%s° → page rotation %s°",
                        i + 1,
                        rot,
                        new_r,
                    )
                out_path = raw_dir / f"page_{i + 1:02d}.pdf"
                dst.save(str(out_path))
            finally:
                dst.close()
    finally:
        src.close()


def _tesseract_ocr_image(img: Image.Image) -> str:
    """Run Tesseract OCR on a PIL image (RGB or L). Prefer this over bytes to skip JPEG encode/decode."""
    import pytesseract
    from PIL import ImageEnhance

    work = img
    if OCR_PREPROCESS:
        work = work.convert("L")
        enhancer = ImageEnhance.Contrast(work)
        work = enhancer.enhance(2.0)
    return pytesseract.image_to_string(work, lang=OCR_LANG, config=f"--psm {OCR_PSM}")


def _tesseract_ocr(image_bytes: bytes) -> str:
    """Run Tesseract OCR on encoded image bytes (JPEG/PNG, etc.)."""
    import io

    return _tesseract_ocr_image(Image.open(io.BytesIO(image_bytes)))


def crop_aadhar_letter_below_scissors(image_bytes: bytes) -> bytes | None:
    """
    Aadhaar *letter* (A4 printout from UIDAI) has scissors marks on the left and
    right margins with a dashed/dotted cut-line across the page (~55% down).
    Below the line is a compact **mini-card strip** with name, DOB, gender, photo,
    address, and Aadhaar number in a clean standard layout.

    Returns cropped JPEG bytes of the mini-card portion,
    or ``None`` when the image is not a letter format or no cut-line is found.
    """
    import cv2
    import numpy as np

    nparr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    h, w = img.shape[:2]
    if h < 300 or w < 200:
        return None

    if h < w * 1.15:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    y_top = int(h * 0.38)
    y_bot = int(h * 0.63)
    band = gray[y_top:y_bot, :]
    band_h = y_bot - y_top

    _, bw = cv2.threshold(band, 175, 255, cv2.THRESH_BINARY_INV)

    seg_len = max(int(w * 0.04), 12)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (seg_len, 1))
    h_morph = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)

    row_cov = np.sum(h_morph > 0, axis=1).astype(float) / w

    line_rows = np.where(row_cov >= 0.08)[0]
    if len(line_rows) < 1:
        return None

    clusters: list[list[int]] = []
    cur = [line_rows[0]]
    for i in range(1, len(line_rows)):
        if line_rows[i] - line_rows[i - 1] <= 10:
            cur.append(line_rows[i])
        else:
            clusters.append(cur)
            cur = [line_rows[i]]
    clusters.append(cur)

    best_cluster = None
    best_score = -999.0
    for cl in clusters:
        median_y = float(np.median(cl))
        center_dist = abs(median_y - band_h * 0.5) / band_h
        avg_cov = float(np.mean(row_cov[cl]))
        score = avg_cov * 2.0 - center_dist
        if score > best_score:
            best_score = score
            best_cluster = cl

    if best_cluster is None or best_score < 0.02:
        return None

    cut_y = y_top + max(best_cluster)
    margin = max(int(h * 0.012), 8)
    crop_y = min(cut_y + margin, h - 80)

    if h - crop_y < int(h * 0.20):
        return None

    cropped = img[crop_y:, :]
    ok, buf = cv2.imencode(".jpg", cropped, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return None

    return buf.tobytes()


def split_aadhar_letter_vertical(strip_bytes: bytes) -> tuple[bytes, bytes] | None:
    """
    The mini-card strip from an Aadhaar letter has front (left) and back (right)
    side-by-side, separated by a vertical dashed/dotted line with scissors.
    Sending the full strip to Textract causes it to merge text from both sides
    into garbled rows.

    Detects the vertical cut-line and returns ``(left_bytes, right_bytes)``
    (front card, back card) or ``None`` if no vertical split is found.
    """
    import cv2
    import numpy as np

    nparr = np.frombuffer(strip_bytes, dtype=np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    h, w = img.shape[:2]
    if w < 200 or h < 100:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    x_left = int(w * 0.35)
    x_right = int(w * 0.65)
    band = gray[:, x_left:x_right]
    band_w = x_right - x_left

    _, bw = cv2.threshold(band, 175, 255, cv2.THRESH_BINARY_INV)

    seg_len = max(int(h * 0.04), 12)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, seg_len))
    v_morph = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)

    col_cov = np.sum(v_morph > 0, axis=0).astype(float) / h

    line_cols = np.where(col_cov >= 0.08)[0]
    if len(line_cols) < 1:
        return None

    clusters: list[list[int]] = []
    cur = [line_cols[0]]
    for i in range(1, len(line_cols)):
        if line_cols[i] - line_cols[i - 1] <= 10:
            cur.append(line_cols[i])
        else:
            clusters.append(cur)
            cur = [line_cols[i]]
    clusters.append(cur)

    best_cluster = None
    best_score = -999.0
    for cl in clusters:
        median_x = float(np.median(cl))
        center_dist = abs(median_x - band_w * 0.5) / band_w
        avg_cov = float(np.mean(col_cov[cl]))
        score = avg_cov * 2.0 - center_dist
        if score > best_score:
            best_score = score
            best_cluster = cl

    if best_cluster is None or best_score < 0.02:
        return None

    split_x = x_left + int(np.median(best_cluster))
    margin = max(int(w * 0.008), 4)

    left_end = max(split_x - margin, 50)
    right_start = min(split_x + margin, w - 50)

    left_img = img[:, :left_end]
    right_img = img[:, right_start:]

    if left_img.shape[1] < 50 or right_img.shape[1] < 50:
        return None

    ok_l, buf_l = cv2.imencode(".jpg", left_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    ok_r, buf_r = cv2.imencode(".jpg", right_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok_l or not ok_r:
        return None

    return buf_l.tobytes(), buf_r.tobytes()


def detect_aadhar_consolidated_from_ocr(ocr_text: str) -> bool:
    """
    True when ``classify_page_by_text`` returns **Aadhar_combined** (DOB+gender row plus long text
    with address/authority cues on one page — see :mod:`page_classifier`).
    """
    return classify_page_by_text(ocr_text or "") == PAGE_TYPE_AADHAR_COMBINED


def _consolidated_halves_need_swap(top_ocr: str, bottom_ocr: str) -> bool:
    """
    After a horizontal cut, decide whether the **top** image is actually the card **back**
    (so we swap before writing front/back files).

    **Only** :func:`should_swap_aadhar_pages_by_dob_gender` (DOB + Male/Female on one half = front).
    If inconclusive, uses ``classify_page_by_text`` / ink heuristics (no uidai URL rule).
    """
    by_dob = should_swap_aadhar_pages_by_dob_gender(top_ocr or "", bottom_ocr or "")
    if by_dob is not None:
        return by_dob

    t_top = classify_page_by_text(top_ocr or "")
    t_bot = classify_page_by_text(bottom_ocr or "")

    if t_top == PAGE_TYPE_AADHAR and t_bot == PAGE_TYPE_AADHAR_BACK:
        return False
    if t_top == PAGE_TYPE_AADHAR_BACK and t_bot == PAGE_TYPE_AADHAR:
        return True

    tl = (top_ocr or "").lower()
    bl = (bottom_ocr or "").lower()

    def _uidai_hits(s: str) -> int:
        return len(re.findall(r"uidai\.gov\.in", s, re.IGNORECASE))

    def _goi(s: str) -> bool:
        return bool(re.search(r"government\s+of\s+india", s, re.IGNORECASE))

    def _uidai_authority(s: str) -> bool:
        return "unique identification authority of india" in s

    # Strong back signal vs strong front signal
    top_back = _uidai_hits(tl) + (1 if _uidai_authority(tl) else 0)
    bot_back = _uidai_hits(bl) + (1 if _uidai_authority(bl) else 0)
    top_front = 2 if _goi(tl) else 0
    bot_front = 2 if _goi(bl) else 0

    if top_back >= 1 and bot_front > top_front and bot_front >= 2:
        return True
    if bot_back >= 1 and top_front > bot_front and top_front >= 2:
        return False
    if top_back > bot_back + 1 and bot_front >= top_front:
        return True
    if bot_back > top_back + 1 and top_front >= bot_front:
        return False

    return False


def _find_consolidated_horizontal_split_y(img_bgr) -> int | None:
    """
    Find a horizontal cut between two vertically stacked card regions.
    Looks for a band of low ink (whitespace) in the middle of the page.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    if h < 200 or w < 200:
        return None

    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    row_ink = np.sum(bw > 0, axis=1).astype(np.float64) / max(w, 1)

    k = max(11, min(51, h // 60 | 1))
    if k % 2 == 0:
        k += 1
    pad = k // 2
    padded = np.pad(row_ink, (pad, pad), mode="edge")
    smoothed = np.convolve(padded, np.ones(k) / k, mode="valid")

    y_lo, y_hi = int(h * 0.20), int(h * 0.80)
    if y_hi <= y_lo + 30:
        return None
    region = smoothed[y_lo:y_hi]
    rel_min = int(np.argmin(region)) + y_lo

    # Require a meaningful dip vs page mean (avoid noisy uniform pages)
    mid_mean = float(np.mean(region))
    min_val = float(smoothed[rel_min])
    if mid_mean < 1e-6:
        return None
    if (mid_mean - min_val) / mid_mean < 0.08:
        return None

    if rel_min < h * 0.14 or rel_min > h * 0.86:
        return None
    return rel_min


def split_aadhar_consolidated(
    image_bytes: bytes,
    *,
    out_dir: Path | None = None,
    front_name: str = "aadhar_front.jpeg",
    back_name: str = "aadhar_back.jpeg",
    require_tesseract_match: bool = True,
) -> tuple[bytes, bytes] | None:
    """
    Single scan with **two card faces** stacked vertically (either order).

    1. Runs Tesseract on the full image; requires combined Aadhaar markers unless
       ``require_tesseract_match`` is False (e.g. caller already classified the page).
    2. Finds a horizontal split (whitespace valley) and crops **top** and **bottom** halves.
    3. Runs Tesseract on each half and assigns **front** vs **back** using UIDAI cues
       (same logic as ``classify_page_by_text``), swapping halves when the back is on top.
    4. Optionally writes JPEGs under ``out_dir`` using ``front_name`` / ``back_name``.

    Returns ``(front_jpeg_bytes, back_jpeg_bytes)`` or ``None`` if not consolidated or split fails.
    """
    import cv2
    import numpy as np

    if not image_bytes or len(image_bytes) < 500:
        return None

    ocr_text = ""
    if require_tesseract_match:
        ocr_text = _tesseract_ocr(image_bytes)
        if not detect_aadhar_consolidated_from_ocr(ocr_text):
            return None

    nparr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    split_y = _find_consolidated_horizontal_split_y(img)
    if split_y is None:
        h0 = img.shape[0]
        split_y = int(h0 * 0.50)
        logger.info(
            "split_aadhar_consolidated: no clear whitespace gap; using horizontal mid-split at y=%s",
            split_y,
        )

    h = img.shape[0]
    margin = max(4, int(h * 0.004))
    mt = max(1, min(margin, split_y // 4))
    mb = max(1, min(margin, (h - split_y) // 4))
    y_cut_top = max(split_y - mt, 1)
    y_cut_bot = min(split_y + mb, h - 1)
    top = img[:y_cut_top, :]
    bot = img[y_cut_bot:, :]

    if top.shape[0] < 80 or bot.shape[0] < 80:
        return None

    ok_t, buf_t = cv2.imencode(".jpg", top, [cv2.IMWRITE_JPEG_QUALITY, 92])
    ok_b, buf_b = cv2.imencode(".jpg", bot, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok_t or not ok_b:
        return None

    top_txt = _tesseract_ocr(buf_t.tobytes())
    bot_txt = _tesseract_ocr(buf_b.tobytes())
    swap = _consolidated_halves_need_swap(top_txt, bot_txt)
    if swap:
        logger.info(
            "split_aadhar_consolidated: detected back-on-top; swapping halves for %s / %s",
            front_name,
            back_name,
        )
        front_img, back_img = bot, top
    else:
        front_img, back_img = top, bot

    ok_f, buf_f = cv2.imencode(".jpg", front_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    ok_k, buf_k = cv2.imencode(".jpg", back_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok_f or not ok_k:
        return None

    front_bytes = buf_f.tobytes()
    back_bytes = buf_k.tobytes()

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / front_name).write_bytes(front_bytes)
        (out_dir / back_name).write_bytes(back_bytes)
        logger.info(
            "split_aadhar_consolidated: wrote %s / %s (split_y=%s, swap=%s)",
            front_name,
            back_name,
            split_y,
            swap,
        )

    return front_bytes, back_bytes


def _export_pdf_pages_to_raw(
    pdf_path: Path,
    raw_dir: Path,
    osd_rotations: dict[int, int] | None = None,
) -> None:
    """
    Under ``raw_dir``: single-page PDFs only (``page_01.pdf``, …), each oriented via OSD
    (see :func:`_export_single_page_pdfs_to_raw`). Does **not** write raster previews; ``raw/`` stays PDF-only.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    _export_single_page_pdfs_to_raw(pdf_path, raw_dir, osd_rotations=osd_rotations)


def _jpeg_bytes_to_single_page_pdf(jpeg_bytes: bytes) -> bytes:
    """Embed a JPEG as a single-page PDF (for ``for_OCR`` when a page was split from raster)."""
    import io

    import fitz
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.width, img.height
    doc = fitz.open()
    try:
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=jpeg_bytes)
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()
    finally:
        doc.close()


def _sync_for_ocr_pdfs(
    sale_dir: Path,
    raw_dir: Path | None,
    page_type_to_idx: dict[str, int],
    combined_indices: set[int],
) -> None:
    """
    Populate ``sale_dir/FOR_OCR_SUBDIR`` with one PDF per classified slot for downstream Textract.

    - Normal pages: copy ``raw/page_NN.pdf`` (same index as the JPEG slot).
    - Same-page combined Aadhaar: build single-page PDFs from the split ``Aadhar.jpg`` / ``Aadhar_back.jpg`` on disk.
    """
    for_ocr = sale_dir / FOR_OCR_SUBDIR
    for_ocr.mkdir(parents=True, exist_ok=True)

    for ptype, root_name in PAGE_TYPE_TO_FILENAME.items():
        pdf_name = _for_ocr_pdf_basename(root_name)
        idx = page_type_to_idx.get(ptype)
        if idx is None:
            continue
        dest = for_ocr / pdf_name

        if idx in combined_indices and ptype in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_BACK):
            jpg_path = for_ocr / root_name
            if jpg_path.is_file():
                dest.write_bytes(_jpeg_bytes_to_single_page_pdf(jpg_path.read_bytes()))
                logger.info("for_OCR: %s from split JPEG", pdf_name)
            continue

        src_pdf = raw_dir / f"page_{idx + 1:02d}.pdf" if raw_dir else None
        if src_pdf and src_pdf.is_file():
            shutil.copy2(src_pdf, dest)
            logger.info("for_OCR: %s <- raw page %02d", pdf_name, idx + 1)
        else:
            logger.warning("for_OCR: missing raw PDF for %s (page index %s)", pdf_name, idx)


def _process_same_page_aadhar(page_bytes: bytes, out_dir: Path) -> bool:
    """
    One physical page with both Aadhaar faces.

    1) Consolidated (top/bottom): :func:`split_aadhar_consolidated`.
    2) Letter (scissor lines): :func:`crop_aadhar_letter_below_scissors` +
       :func:`split_aadhar_letter_vertical`.

    Writes ``Aadhar_front.jpg`` and ``Aadhar_back.jpg`` under ``out_dir`` (typically ``for_OCR/``).
    Returns True if both files were produced.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if split_aadhar_consolidated(
        page_bytes,
        out_dir=out_dir,
        front_name=FILENAME_AADHAR_FRONT,
        back_name="Aadhar_back.jpg",
        require_tesseract_match=False,
    ):
        return True

    cropped = crop_aadhar_letter_below_scissors(page_bytes)
    if not cropped:
        logger.warning("Same-page Aadhaar: consolidated + letter crop both failed")
        return False
    split = split_aadhar_letter_vertical(cropped)
    if split:
        (out_dir / FILENAME_AADHAR_FRONT).write_bytes(split[0])
        (out_dir / "Aadhar_back.jpg").write_bytes(split[1])
        logger.info("Same-page Aadhaar: letter (scissor) split into front/back")
        return True
    (out_dir / FILENAME_AADHAR_FRONT).write_bytes(cropped)
    logger.warning("Same-page Aadhaar: letter crop only; missing Aadhar_back.jpg")
    return False


def orient_common_sale_jpegs(subdir: Path) -> None:
    """Run :func:`correct_image_orientation_upright` on Details (legacy JPEG) / Insurance / Financing."""
    for name in ("Details.jpg", "Insurance.jpg", "Financing.jpg"):
        p = subdir / name
        if p.is_file():
            p.write_bytes(correct_image_orientation_upright(p.read_bytes()))


def orient_and_normalize_sale_documents(sale_dir: Path) -> None:
    """
    Run orientation + Aadhaar normalization on ``for_OCR/`` when consolidated pre-OCR wrote there;
    otherwise on the sale root (manual V2 uploads).
    """
    for_ocr = sale_dir / FOR_OCR_SUBDIR
    if for_ocr.is_dir() and any(p.is_file() for p in for_ocr.iterdir()):
        orient_common_sale_jpegs(for_ocr)
        normalize_aadhar_upload_files(for_ocr)
    else:
        orient_common_sale_jpegs(sale_dir)
        normalize_aadhar_upload_files(sale_dir)


def normalize_aadhar_upload_files(subdir: Path) -> None:
    """
    OSD upright orientation on Aadhaar JPEGs, then physical layout fixes (letter / scissors) before Textract.
    Used by Add Sales upload; bulk pre-OCR already writes normalized card crops. Idempotent on plain scans.
    """
    front_p = subdir / FILENAME_AADHAR_FRONT
    if not front_p.is_file():
        front_p = subdir / "Aadhar.jpg"
    if not front_p.is_file():
        return
    front_p.write_bytes(correct_image_orientation_upright(front_p.read_bytes()))
    back_p = subdir / "Aadhar_back.jpg"
    if back_p.is_file():
        back_p.write_bytes(correct_image_orientation_upright(back_p.read_bytes()))

    front_bytes = front_p.read_bytes()
    back_bytes = back_p.read_bytes() if back_p.is_file() else None

    is_letter = False
    if front_bytes:
        front_cropped = crop_aadhar_letter_below_scissors(front_bytes)
        if front_cropped:
            is_letter = True
            split = split_aadhar_letter_vertical(front_cropped)
            if split:
                front_p.write_bytes(split[0])
                back_p.write_bytes(split[1])
                return
            front_p.write_bytes(front_cropped)
    if not is_letter and back_bytes:
        back_cropped = crop_aadhar_letter_below_scissors(back_bytes)
        if back_cropped:
            split = split_aadhar_letter_vertical(back_cropped)
            if split:
                front_p.write_bytes(split[0])
                back_p.write_bytes(split[1])
                return
            back_p.write_bytes(back_cropped)


def _pre_ocr_text_from_sales_detail_sheet_onward(full_text: str) -> str:
    """
    Return OCR text from the first **Sales Detail Sheet** heading onward (same page and following pages).

    Dealer phone / other numbers often appear *above* that heading on the Details page; mobile extraction
    must ignore them. If the heading is found, text **before** that phrase on the first matching page is
    dropped; Aadhaar/insurance pages above the Details page are excluded entirely.
    If no heading is found, returns ``full_text`` unchanged.
    """
    if not full_text or not full_text.strip():
        return full_text
    anchor = re.compile(r"(?i)sales\s*detail\s*sheet")
    headers = list(re.finditer(r"(?ms)^---\s*Page\s+\d+\s*---\s*$", full_text))
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(full_text)
        block = full_text[start:end]
        mline = anchor.search(block)
        if mline:
            cut = start + mline.start()
            return full_text[cut:].strip()
    return full_text


def _normalize_indian_mobile_hint(raw: str | None) -> str | None:
    """Return a valid 10-digit Indian mobile from user/form input (handles spaces, +91)."""
    if not raw or not str(raw).strip():
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) < 10:
        return None
    ten = digits[-10:]
    if ten[0] in "6789":
        return ten
    return None


def _extract_mobile_from_text(text: str) -> str | None:
    """
    Extract Indian 10-digit mobile from text.
    Prefer explicit **Mobile Number** on the Details sheet, then legacy "Tel. Name No. …" / Mobile / Phone.
    Never treat **Alternate No.** as the customer mobile (see ``CUSTOMER_MOBILE_CONTEXT``).
    Falls back to first valid mobile if no context match.
    """
    if not text:
        return None
    match = DETAILS_SHEET_MOBILE_NUMBER_LABEL.search(text)
    if match:
        return match.group(1)
    match = CUSTOMER_MOBILE_CONTEXT.search(text)
    if match:
        return match.group(1)
    match = MOBILE_PATTERN.search(text)
    return match.group(1) if match else None


def _extract_all_mobiles_from_text(text: str) -> list[str]:
    """Extract all distinct Indian 10-digit mobiles from text, in order of first appearance."""
    if not text:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in DETAILS_SHEET_MOBILE_NUMBER_LABEL.finditer(text):
        m = match.group(1)
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    for match in CUSTOMER_MOBILE_CONTEXT.finditer(text):
        m = match.group(1)
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    for match in MOBILE_PATTERN.finditer(text):
        m = match.group(1)
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _extract_aadhar_number(text: str) -> str | None:
    """Extract 12-digit Aadhar number (nnnn nnnn nnnn format). Returns normalized 12 digits or None."""
    if not text:
        return None
    match = AADHAR_PATTERN.search(text)
    if match:
        raw = (match.group(1) or match.group(2) or "").replace(" ", "")
        if len(raw) == 12 and raw.isdigit():
            return raw
    return None


def _normalize_name_for_match(name: str | None) -> str:
    """Normalize name for matching: lowercase, strip, collapse spaces."""
    if not name or not str(name).strip():
        return ""
    return " ".join(str(name).lower().strip().split())


def _names_match(name1: str | None, name2: str | None) -> bool:
    """Return True if names likely refer to same person. Handles OCR variations."""
    n1 = _normalize_name_for_match(name1)
    n2 = _normalize_name_for_match(name2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    if n1 in n2 or n2 in n1:
        return True
    # First word (often first name) match as fallback
    w1 = n1.split()[0] if n1 else ""
    w2 = n2.split()[0] if n2 else ""
    return len(w1) >= 2 and len(w2) >= 2 and w1 == w2


def _extract_name_from_text(text: str, page_type: str) -> str | None:
    """Extract customer name from page text. Returns trimmed name or None."""
    if not text or not text.strip():
        return None
    t = text.strip()
    # Aadhar: name often appears before Male/Female
    m = NAME_BEFORE_GENDER.search(t)
    if m:
        name = m.group(1).strip()
        if len(name) >= 2 and len(name) <= 80:
            return name
    # Name: or Nominee Name: label
    m = NAME_AFTER_LABEL.search(t)
    if m:
        name = m.group(1).strip()
        if len(name) >= 2 and len(name) <= 80:
            return name
    return None


def _extract_mobile_from_page_text(text: str) -> str | None:
    """Extract mobile from a single page (Details sheet)."""
    return _extract_mobile_from_text(text)


def _parse_page_texts(full_ocr_text: str) -> list[tuple[int, str]]:
    """Parse pre-OCR output into (page_index_0based, page_text) list."""
    page_blocks = re.split(r"---\s*Page\s+(\d+)\s*---", full_ocr_text)
    result: list[tuple[int, str]] = []
    i = 1
    while i < len(page_blocks):
        try:
            page_num = int(page_blocks[i])
            page_text = page_blocks[i + 1] if i + 1 < len(page_blocks) else ""
            result.append((page_num - 1, page_text))
        except (ValueError, IndexError):
            pass
        i += 2
    return result


def _detect_multi_customer(classifications: list[tuple[int, str]]) -> bool:
    """Return True if scan has multiple document sets (2+ Aadhars, 2+ Details, or 2+ Insurance)."""
    count_fronts = sum(1 for _, p in classifications if p in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_COMBINED))
    count_backs = sum(1 for _, p in classifications if p in (PAGE_TYPE_AADHAR_BACK, PAGE_TYPE_AADHAR_COMBINED))
    count_details = sum(1 for _, p in classifications if p == PAGE_TYPE_DETAILS)
    count_insurance = sum(1 for _, p in classifications if p == PAGE_TYPE_INSURANCE)
    return count_fronts >= 2 or count_backs >= 2 or count_details >= 2 or count_insurance >= 2


def _build_multi_customer_bundles(
    pdf_path: Path,
    full_ocr_text: str,
    classifications: list[tuple[int, str]],
    all_mobiles: list[str] | None = None,
) -> list[dict] | None:
    """
    Build per-customer bundles when multi-customer detected.
    Returns list of {aadhar_front_idx, aadhar_back_idx, details_idx, insurance_idx, mobile, name} or None if invalid.
    Each bundle must have: mobile, aadhar front, aadhar back, details.
    """
    page_texts = {idx: text for idx, text in _parse_page_texts(full_ocr_text)}

    # Collect pages by type with extracted data
    fronts: list[tuple[int, str | None, str | None]] = []  # (idx, aadhar_num, name)
    backs: list[tuple[int, str | None]] = []  # (idx, aadhar_num)
    details_list: list[tuple[int, str | None, str | None]] = []  # (idx, mobile, name)
    insurance_list: list[tuple[int, str | None]] = []  # (idx, name)

    for idx, ptype in classifications:
        text = page_texts.get(idx, "")
        aadhar_num = _extract_aadhar_number(text)
        name = _extract_name_from_text(text, ptype)

        if ptype == PAGE_TYPE_AADHAR:
            fronts.append((idx, aadhar_num, name))
        elif ptype == PAGE_TYPE_AADHAR_BACK:
            backs.append((idx, aadhar_num))
        elif ptype == PAGE_TYPE_AADHAR_COMBINED:
            fronts.append((idx, aadhar_num, name))
            backs.append((idx, aadhar_num))
        elif ptype == PAGE_TYPE_DETAILS:
            mobile = _extract_mobile_from_page_text(text)
            details_list.append((idx, mobile, name))
        elif ptype == PAGE_TYPE_INSURANCE:
            insurance_list.append((idx, name))

    # Fallback: when 2+ mobiles and 2+ Details but only 1 Aadhar pair, promote UNUSED pages with Aadhar number
    need_more = 2 - min(len(fronts), len(backs))
    if need_more > 0 and len(all_mobiles or []) >= 2 and len(details_list) >= 2:
        added = 0
        for idx, ptype in classifications:
            if ptype != PAGE_TYPE_UNUSED or added >= need_more:
                continue
            text = page_texts.get(idx, "")
            aadhar_num = _extract_aadhar_number(text)
            if not aadhar_num:
                continue
            # Avoid promoting pages that look like Details/Insurance (have their markers)
            if any(p.search(text) for p in _DETAILS_OR_INSURANCE_SNIFF):
                continue
            name = _extract_name_from_text(text, PAGE_TYPE_AADHAR)
            fronts.append((idx, aadhar_num, name))
            backs.append((idx, aadhar_num))
            added += 1
            logger.info("Promoted UNUSED page %d to Aadhar_combined (aadhar=%s) for multi-customer", idx, aadhar_num[:4] + "****")

    # Match fronts to backs by Aadhar number
    # Build customer slots: each needs (front_idx, back_idx) matched by aadhar_num
    used_fronts: set[int] = set()
    used_backs: set[int] = set()
    customer_slots: list[dict] = []

    for f_idx, f_aadhar, f_name in sorted(fronts, key=lambda x: x[0]):
        if f_idx in used_fronts:
            continue
        # Find matching back by Aadhar number
        back_idx = None
        for b_idx, b_aadhar in backs:
            if b_idx in used_backs:
                continue
            if f_aadhar and b_aadhar and f_aadhar == b_aadhar:
                back_idx = b_idx
                break
            if not f_aadhar and not b_aadhar:
                # No number on either - use first unused back (sequential fallback)
                back_idx = b_idx
                break
        if back_idx is None:
            # Try any unused back (sequential)
            for b_idx, _ in backs:
                if b_idx not in used_backs:
                    back_idx = b_idx
                    break
        if back_idx is None:
            continue
        used_fronts.add(f_idx)
        used_backs.add(back_idx)
        customer_slots.append({
            "front_idx": f_idx,
            "back_idx": back_idx,
            "aadhar_num": f_aadhar,
            "name": f_name,
        })

    # Sort customer slots by page order
    customer_slots.sort(key=lambda c: min(c["front_idx"], c["back_idx"]))

    # Assign Details and Insurance to customers by NAME matching (Aadhar name links to Details and Insurance)
    used_details: set[int] = set()
    used_insurance: set[int] = set()

    all_mobiles = all_mobiles or []
    mobile_fallback_idx = 0

    bundles: list[dict] = []
    for slot in customer_slots:
        aadhar_name = slot["name"]

        # Find Details sheet matching this customer by name
        d_idx, mobile, d_name = None, None, None
        for i, (idx, m, dn) in enumerate(details_list):
            if idx in used_details:
                continue
            if _names_match(aadhar_name, dn):
                d_idx, mobile, d_name = idx, m or (all_mobiles[mobile_fallback_idx] if mobile_fallback_idx < len(all_mobiles) else None), dn
                if mobile:
                    mobile_fallback_idx += 1
                used_details.add(idx)
                break
        if d_idx is None or not mobile:
            # Fallback: use first unused Details (sequential) if no name match
            for idx, m, dn in details_list:
                if idx not in used_details:
                    d_idx, d_name = idx, dn
                    mobile = m or (all_mobiles[mobile_fallback_idx] if mobile_fallback_idx < len(all_mobiles) else None)
                    if mobile:
                        mobile_fallback_idx += 1
                    used_details.add(idx)
                    break
        if d_idx is None or not mobile:
            continue

        # Find Insurance matching this customer by name
        insurance_idx = None
        for idx, ins_name in insurance_list:
            if idx in used_insurance:
                continue
            if _names_match(aadhar_name, ins_name) or _names_match(d_name, ins_name):
                insurance_idx = idx
                used_insurance.add(idx)
                break
        if insurance_idx is None:
            # Fallback: first unused Insurance
            for idx, _ in insurance_list:
                if idx not in used_insurance:
                    insurance_idx = idx
                    used_insurance.add(idx)
                    break

        name = aadhar_name or d_name
        bundles.append({
            "aadhar_front_idx": slot["front_idx"],
            "aadhar_back_idx": slot["back_idx"],
            "details_idx": d_idx,
            "insurance_idx": insurance_idx,
            "mobile": mobile,
            "name": name,
        })

    if len(bundles) < len(customer_slots) or not bundles:
        return None
    return bundles


def _split_pdf_multi_customer_to_sale_dirs(
    pdf_path: Path,
    bundles: list[dict],
    dealer_id: int,
    page_images_cache: dict[int, Image.Image] | None = None,
    osd_rotations: dict[int, int] | None = None,
) -> list[tuple[Path, str, str]]:
    """
    For each customer bundle, write into ``Uploaded scans/{dealer_id}/{mobile}_ddmmyy/``
    with ``raw/`` containing the consolidated PDF and per-page PDFs (``page_NN.pdf``).

    Same index for front+back → :func:`_process_same_page_aadhar`; else one raster per slot for outputs.
    When ``page_images_cache`` / ``osd_rotations`` are provided, skip re-rasterization.
    Returns ``(sale_dir, subfolder, mobile)`` per bundle.
    """
    from app.services.upload_service import UploadService

    us = UploadService()
    if page_images_cache is not None:
        page_images = page_images_cache
    else:
        pages, _osd = _pdf_to_page_images(pdf_path)
        page_images = {idx: im for idx, im in pages}
    result: list[tuple[Path, str, str]] = []

    for i, bundle in enumerate(bundles):
        m = bundle.get("mobile") or ""
        subfolder = us.get_subdir_name_mobile(m) if m else f"{pdf_path.stem}_cust{i + 1}"
        sale_dir = get_uploads_dir(dealer_id) / subfolder
        for_ocr_dir = sale_dir / FOR_OCR_SUBDIR
        raw_dir = sale_dir / "raw"
        sale_dir.mkdir(parents=True, exist_ok=True)
        for_ocr_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, raw_dir / pdf_path.name)
        _export_pdf_pages_to_raw(pdf_path, raw_dir, osd_rotations=osd_rotations)

        fi = bundle.get("aadhar_front_idx")
        bi = bundle.get("aadhar_back_idx")
        if fi is not None and bi is not None and fi == bi and fi in page_images:
            if not _process_same_page_aadhar(_pil_rgb_to_jpeg_bytes(page_images[fi]), for_ocr_dir):
                logger.warning(
                    "Multi bundle %d: same-page Aadhaar split failed; saving full page as %s",
                    i + 1,
                    FILENAME_AADHAR_FRONT,
                )
                page_images[fi].save(for_ocr_dir / FILENAME_AADHAR_FRONT, "JPEG", quality=90)
        else:
            for key, filename in [
                ("aadhar_front_idx", FILENAME_AADHAR_FRONT),
                ("aadhar_back_idx", "Aadhar_back.jpg"),
                ("insurance_idx", "Insurance.jpg"),
            ]:
                idx = bundle.get(key)
                if idx is not None and idx in page_images:
                    out_path = for_ocr_dir / filename
                    page_images[idx].save(out_path, "JPEG", quality=90)
                    logger.info("Multi-customer: bundle %d page %d -> %s", i + 1, idx + 1, filename)
            didx_b = bundle.get("details_idx")
            if didx_b is not None:
                src_d = raw_dir / f"page_{didx_b + 1:02d}.pdf"
                if src_d.is_file():
                    shutil.copy2(src_d, for_ocr_dir / FILENAME_SALES_DETAIL_SHEET_PDF)
                    logger.info(
                        "Multi-customer: bundle %d page %d -> %s",
                        i + 1,
                        didx_b + 1,
                        FILENAME_SALES_DETAIL_SHEET_PDF,
                    )

        didx = bundle.get("details_idx")
        if didx is not None and didx in page_images:
            write_pencil_mark_from_details_page(sale_dir, _pil_rgb_to_jpeg_bytes(page_images[didx]))

        page_type_to_idx_m: dict[str, int] = {}
        if bundle.get("aadhar_front_idx") is not None:
            page_type_to_idx_m[PAGE_TYPE_AADHAR] = bundle["aadhar_front_idx"]
        if bundle.get("aadhar_back_idx") is not None:
            page_type_to_idx_m[PAGE_TYPE_AADHAR_BACK] = bundle["aadhar_back_idx"]
        if bundle.get("details_idx") is not None:
            page_type_to_idx_m[PAGE_TYPE_DETAILS] = bundle["details_idx"]
        if bundle.get("insurance_idx") is not None:
            page_type_to_idx_m[PAGE_TYPE_INSURANCE] = bundle["insurance_idx"]
        combined_m: set[int] = set()
        if fi is not None and bi is not None and fi == bi:
            combined_m.add(fi)
        _sync_for_ocr_pdfs(sale_dir, raw_dir, page_type_to_idx_m, combined_m)

        result.append((sale_dir, subfolder, m))

    return result


def pre_ocr_pdf(
    pdf_path: Path,
    processing_dir: Path | None = None,
) -> tuple[str, Path | None, str | None, list[tuple[str, int | None, str]], dict[int, Image.Image], dict[int, int]]:
    """
    Extract mobile from PDF using Tesseract per page (Details sheet has mobile).
    Saves to Processing/filename_ddmmyyyy_pre_ocr.txt.

    Returns ``(full_text, ocr_file_path, mobile_or_none, step_log, page_images, osd_rotations)``
    where ``step_log`` is a list of ``(step_id, elapsed_ms, detail)`` for
    :func:`append_pre_ocr_step_lines`, ``page_images`` maps ``{page_idx: PIL_Image}`` (oriented),
    and ``osd_rotations`` maps ``{page_idx: clockwise_degrees}`` (for reuse by downstream PDF writers).
    """
    proc_dir = processing_dir or PROCESSING_DIR
    proc_dir.mkdir(parents=True, exist_ok=True)

    filename_stem = pdf_path.stem
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    ocr_basename = f"{filename_stem}_{ddmmyyyy}_pre_ocr.txt"
    ocr_path = proc_dir / ocr_basename
    step_log: list[tuple[str, int | None, str]] = []

    def _step(t: tuple[str, int | None, str]) -> None:
        step_log.append(t)

    try:
        t0 = time.perf_counter()
        pages, osd_rotations = _pdf_to_page_images(pdf_path)
        raster_ms = int((time.perf_counter() - t0) * 1000)
        _step(
            ("pdf_to_page_rasters", raster_ms, f"pages={len(pages)} file={pdf_path.name}"),
        )
        page_images: dict[int, Image.Image] = {idx: im for idx, im in pages}

        all_text_parts: list[str] = []
        tess_total = 0
        for page_idx, page_img in pages:
            t1 = time.perf_counter()
            text = _tesseract_ocr_image(page_img)
            p_ms = int((time.perf_counter() - t1) * 1000)
            tess_total += p_ms
            ch = len(text.strip())
            px = page_img.width * page_img.height * 3
            _step(
                (f"tesseract_page_{page_idx + 1}", p_ms, f"chars={ch} rgb_bytes≈{px}"),
            )
            if text.strip():
                all_text_parts.append(f"--- Page {page_idx + 1} ---\n{text}")

        _step(("tesseract_all_pages_total", tess_total, f"pages={len(pages)}"))

        t_merge0 = time.perf_counter()
        full_text = "\n\n".join(all_text_parts)
        t_after_merge = time.perf_counter()
        _step(
            ("merge_page_text", int((t_after_merge - t_merge0) * 1000), f"chars={len(full_text)}"),
        )

        t_wr = time.perf_counter()
        ocr_path.write_text(full_text, encoding="utf-8")
        _step(
            ("write_pre_ocr_txt", int((time.perf_counter() - t_wr) * 1000), f"path={ocr_basename}"),
        )

        t3 = time.perf_counter()
        mobile_scope = _pre_ocr_text_from_sales_detail_sheet_onward(full_text)
        _step(
            ("scope_text_sales_detail_onward", int((time.perf_counter() - t3) * 1000), f"chars={len(mobile_scope)}"),
        )

        t4 = time.perf_counter()
        mobile = _extract_mobile_from_text(mobile_scope)
        if not mobile:
            # Scope starts at "Sales Detail Sheet", so it drops earlier pages (e.g. Aadhaar with a phone)
            # and header lines above the heading on the Details page (dealer Ph.). When the customer
            # mobile row is OCR-garbled, a valid 10-digit may only appear in ``full_text``.
            mobile = _extract_mobile_from_text(full_text)
        _step(
            ("extract_mobile", int((time.perf_counter() - t4) * 1000), f"mobile={'set' if mobile else 'none'}"),
        )

        return full_text, ocr_path, mobile, step_log, page_images, osd_rotations
    except Exception as e:
        logger.exception("pre_ocr failed for %s", pdf_path)
        try:
            ocr_path.write_text(f"OCR error: {e}\n", encoding="utf-8")
        except OSError:
            pass
        _step(("pre_ocr_error", None, str(e)[:200]))
        return "", ocr_path, None, step_log, {}, {}


def _split_pdf_by_classification(
    pdf_path: Path,
    full_ocr_text: str,
    sale_dir: Path,
    classifications_override: list[tuple[int, str]] | None = None,
    raw_dir: Path | None = None,
    page_images_cache: dict[int, Image.Image] | None = None,
    osd_rotations: dict[int, int] | None = None,
) -> Path:
    """
    Classify each page from OCR text; write rotated/cut/named outputs under ``sale_dir/for_OCR/``.
    ``pencil_mark.jpeg`` and ``unused.pdf`` stay at ``sale_dir`` root.
    If ``raw_dir`` is set, store the consolidated PDF copy and per-page PDFs under ``raw/`` (``page_NN.pdf``, no raster).

    When ``page_images_cache`` is provided, skip ``_pdf_to_page_images`` (already rendered in pre-OCR).
    When ``osd_rotations`` is provided, pass it to ``_export_pdf_pages_to_raw`` to skip re-running OSD.

    Same-page Aadhaar: consolidated top/bottom split, then letter scissor split via :func:`_process_same_page_aadhar`.
    """
    import fitz

    sale_dir.mkdir(parents=True, exist_ok=True)
    for_ocr_dir = sale_dir / FOR_OCR_SUBDIR
    for_ocr_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, raw_dir / pdf_path.name)
        _export_pdf_pages_to_raw(pdf_path, raw_dir, osd_rotations=osd_rotations)

    classifications = classifications_override if classifications_override is not None else classify_pages_from_ocr_text(full_ocr_text)
    page_type_to_idx: dict[str, int] = {}  # first page index per type
    unused_indices: list[int] = []
    for idx, ptype in classifications:
        if ptype == PAGE_TYPE_UNUSED:
            unused_indices.append(idx)
        elif ptype == PAGE_TYPE_AADHAR_COMBINED:
            if PAGE_TYPE_AADHAR not in page_type_to_idx:
                page_type_to_idx[PAGE_TYPE_AADHAR] = idx
            if PAGE_TYPE_AADHAR_BACK not in page_type_to_idx:
                page_type_to_idx[PAGE_TYPE_AADHAR_BACK] = idx
        elif ptype not in page_type_to_idx:
            page_type_to_idx[ptype] = idx

    maybe_swap_aadhar_page_indices(page_type_to_idx, full_ocr_text)

    if page_images_cache is not None:
        page_images = page_images_cache
    else:
        pages, _osd = _pdf_to_page_images(pdf_path)
        page_images = {idx: im for idx, im in pages}

    # Same per-page rasters as pre-OCR ``--- Page N ---`` (Tesseract order) for operator review.
    ci_dir = for_ocr_dir / "classify_inputs"
    try:
        ci_dir.mkdir(parents=True, exist_ok=True)
        (ci_dir / "README.txt").write_text(
            "PNG files here are the per-page raster cuts (PDF → RGB, lossless save) used with pre-OCR text "
            "for page classification.\n"
            "Aadhaar photo **front** is detected only when OCR contains both a DOB cue and Male/Female "
            "(English/Hindi; any common casing).\n"
            "Sibling folder ../raw/ holds PDF copies only (no rasters).\n",
            encoding="utf-8",
        )
        for idx, pim in sorted(page_images.items()):
            pim.save(ci_dir / f"page_{idx + 1:02d}.png", "PNG")
    except OSError as e:
        logger.warning("for_OCR/classify_inputs: could not write: %s", e)

    combined_indices = {idx for idx, ptype in classifications if ptype == PAGE_TYPE_AADHAR_COMBINED}
    for idx in combined_indices:
        if idx not in page_images:
            continue
        if not _process_same_page_aadhar(_pil_rgb_to_jpeg_bytes(page_images[idx]), for_ocr_dir):
            logger.warning(
                "Aadhaar combined page %d: consolidated + letter split failed; copying full page to %s only",
                idx + 1,
                FILENAME_AADHAR_FRONT,
            )
            out_path = for_ocr_dir / FILENAME_AADHAR_FRONT
            page_images[idx].save(out_path, "JPEG", quality=90)

    # Write known types (skip Aadhar slots already produced from combined split)
    for ptype, filename in PAGE_TYPE_TO_FILENAME.items():
        if ptype not in page_type_to_idx:
            continue
        idx = page_type_to_idx[ptype]
        if idx in combined_indices and ptype in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_BACK):
            continue
        if idx not in page_images:
            continue
        if filename == FILENAME_SALES_DETAIL_SHEET_PDF:
            src_pdf = raw_dir / f"page_{idx + 1:02d}.pdf" if raw_dir else None
            if src_pdf and src_pdf.is_file():
                shutil.copy2(src_pdf, for_ocr_dir / filename)
                logger.info("Classified page %d -> %s (PDF only)", idx + 1, filename)
            else:
                logger.warning("Details page %d: missing raw PDF for Sales_Detail_Sheet.pdf", idx + 1)
            continue
        out_path = for_ocr_dir / filename
        page_images[idx].save(out_path, "JPEG", quality=90)
        logger.info("Classified page %d -> %s", idx + 1, filename)

    if PAGE_TYPE_DETAILS in page_type_to_idx:
        didx = page_type_to_idx[PAGE_TYPE_DETAILS]
        if didx in page_images:
            write_pencil_mark_from_details_page(sale_dir, _pil_rgb_to_jpeg_bytes(page_images[didx]))

    # Merge unused pages into unused.pdf
    if unused_indices:
        doc = fitz.open(str(pdf_path))
        try:
            merged = fitz.open()
            for idx in sorted(unused_indices):
                if idx < doc.page_count:
                    merged.insert_pdf(doc, from_page=idx, to_page=idx)
            if merged.page_count > 0:
                unused_path = sale_dir / "unused.pdf"
                merged.save(str(unused_path))
                logger.info("Wrote %d unused page(s) -> unused.pdf", len(unused_indices))
            merged.close()
        finally:
            doc.close()

    if raw_dir is not None:
        _sync_for_ocr_pdfs(sale_dir, raw_dir, page_type_to_idx, combined_indices)

    return sale_dir


def run_pre_ocr_and_prepare(
    source_pdf: Path,
    processing_dir: Path | None = None,
    dealer_id: int = 100001,
    *,
    mobile_hint: str | None = None,
) -> tuple[list[tuple[Path, str, str]] | None, str, str | None, Path | None, list[str] | None]:
    """
    Copy PDF to Processing, run pre-OCR (Tesseract on in-memory rasters), classify pages, write normalized
    document files under ``Uploaded scans/{dealer_id}/{mobile}_ddmmyy/for_OCR/`` (with ``raw/`` holding
    **PDF only**: consolidated copy + ``page_NN.pdf`` per page).

    Validates BEFORE splitting: mobile + 3 critical classifications (Aadhar, Aadhar_back, Details).
    Returns (bundles | None, subfolder_stem, mobile_or_none, ocr_path, missing_list | None).
    bundles: list of ``(sale_dir, subfolder, mobile)`` where ``sale_dir`` is the uploads folder for the sale.
    If missing_list is non-empty, validation failed; do not split. bundles is None.

    ``mobile_hint``: optional 10-digit mobile from Add Sales (consolidated PDF upload) when Tesseract cannot
    read the customer mobile from the scan (common when the Details row is smudged or OCR-garbled).
    """
    from app.config import get_ocr_output_dir
    from app.services.upload_service import UploadService

    proc_dir = processing_dir or PROCESSING_DIR
    proc_dir.mkdir(parents=True, exist_ok=True)

    is_add_sales_work = False
    try:
        is_add_sales_work = proc_dir.resolve() == get_add_sales_pre_ocr_work_dir(dealer_id).resolve()
    except OSError:
        is_add_sales_work = False

    us = UploadService()

    def _log_subfolder(mob: str | None) -> str:
        return us.get_subdir_name_mobile(mob) if mob else source_pdf.stem

    def _flush_pre(steps: list[tuple[str, int | None, str]], mob: str | None) -> None:
        if not steps:
            return
        append_pre_ocr_step_lines(get_ocr_output_dir(dealer_id), _log_subfolder(mob), steps)

    # Copy PDF to Processing (work on copy so original stays for .processed marker)
    dest_pdf = proc_dir / source_pdf.name
    t_copy0 = time.perf_counter()
    dest_pdf.write_bytes(source_pdf.read_bytes())
    copy_ms = int((time.perf_counter() - t_copy0) * 1000)

    # Run pre-OCR (Tesseract per page — usually dominant cost). Also returns the
    # rendered page images and OSD rotations so downstream steps skip re-rasterization.
    t_pre0 = time.perf_counter()
    full_text, ocr_path, mobile, pre_steps, page_images, osd_rotations = pre_ocr_pdf(dest_pdf, processing_dir=proc_dir)
    # Consolidated Add Sales: user-entered mobile must win over OCR (letter/other pages may show a different number).
    hint = _normalize_indian_mobile_hint(mobile_hint)
    if hint:
        mobile = hint
    pre_ocr_wall_ms = int((time.perf_counter() - t_pre0) * 1000)

    orchestration: list[tuple[str, int | None, str]] = []
    if is_add_sales_work:
        orchestration.append(
            (
                "add_sales_pre_ocr_session",
                None,
                f"work_dir={proc_dir.resolve()} dealer_id={dealer_id} source_pdf={source_pdf.name}",
            ),
        )
    orchestration.extend(
        [
            ("copy_pdf_to_processing", copy_ms, f"bytes={dest_pdf.stat().st_size} name={dest_pdf.name}"),
            ("pre_ocr_pdf_total_wall", pre_ocr_wall_ms, "includes_tesseract_and_io"),
        ]
    )
    orchestration.extend(pre_steps)

    t_cls0 = time.perf_counter()
    classifications = classify_pages_from_ocr_text(full_text)
    cls_ms = int((time.perf_counter() - t_cls0) * 1000)
    orchestration.append(
        ("classify_pages_from_ocr_text", cls_ms, f"slots={len(classifications)}"),
    )

    # ── Textract DDT enhancement for the Details page ──
    # Tesseract struggles with handwritten Details sheets. If enabled, re-OCR just the
    # Details page via Textract DetectDocumentText (much better handwriting recognition),
    # replace its block in full_text, and rewrite the _pre_ocr.txt artifact.
    if OCR_PRE_OCR_TEXTRACT_DETAILS:
        details_pages = [
            idx for idx, ptype in classifications if ptype == PAGE_TYPE_DETAILS
        ]
        for dp_idx in details_pages:
            t_td0 = time.perf_counter()
            ddt_text = _textract_ddt_for_page(dest_pdf, dp_idx, page_image=page_images.get(dp_idx))
            td_ms = int((time.perf_counter() - t_td0) * 1000)
            if ddt_text:
                full_text = _replace_page_block_in_full_text(full_text, dp_idx, ddt_text)
                orchestration.append(
                    ("textract_ddt_details_page", td_ms, f"page={dp_idx + 1} chars={len(ddt_text)} replaced=yes"),
                )
                logger.info(
                    "Pre-OCR: Textract DDT replaced Tesseract text for Details page %d (%d chars, %d ms)",
                    dp_idx + 1, len(ddt_text), td_ms,
                )
                # Re-extract mobile from the improved text
                mobile_from_ddt = _extract_mobile_from_text(ddt_text)
                if mobile_from_ddt and not hint:
                    mobile = mobile_from_ddt
                    logger.info("Pre-OCR: mobile updated from Textract DDT: %s", mobile)
            else:
                orchestration.append(
                    ("textract_ddt_details_page", td_ms, f"page={dp_idx + 1} replaced=no"),
                )
        # Rewrite _pre_ocr.txt with enhanced text
        if details_pages and ocr_path:
            try:
                ocr_path.write_text(full_text, encoding="utf-8")
                orchestration.append(("rewrite_pre_ocr_txt_with_ddt", None, f"path={ocr_path.name}"))
            except OSError:
                logger.warning("Could not rewrite pre_ocr.txt after Textract DDT")

    # ── Textract DDT fallback for Aadhaar front / back / combined when Tesseract text is too weak ──
    aadhar_ddt_touched = False
    if OCR_PRE_OCR_TEXTRACT_AADHAR_FALLBACK:
        aadhar_indices = [
            (idx, ptype)
            for idx, ptype in classifications
            if ptype
            in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_BACK, PAGE_TYPE_AADHAR_COMBINED)
        ]
        for ah_idx, ah_type in aadhar_indices:
            per_page = extract_page_text_from_pre_ocr_blocks(full_text, ah_idx)
            if not _aadhar_page_needs_textract_fallback(per_page, ah_type):
                continue
            t_ah0 = time.perf_counter()
            ah_ddt = _textract_ddt_for_page(dest_pdf, ah_idx, page_image=page_images.get(ah_idx))
            ah_ms = int((time.perf_counter() - t_ah0) * 1000)
            if ah_ddt:
                full_text = _replace_page_block_in_full_text(full_text, ah_idx, ah_ddt)
                aadhar_ddt_touched = True
                orchestration.append(
                    (
                        "textract_ddt_aadhar_page",
                        ah_ms,
                        f"page={ah_idx + 1} type={ah_type} chars={len(ah_ddt)} replaced=yes",
                    ),
                )
                logger.info(
                    "Pre-OCR: Textract DDT replaced Tesseract text for Aadhaar page %d (%s, %d chars, %d ms)",
                    ah_idx + 1,
                    ah_type,
                    len(ah_ddt),
                    ah_ms,
                )
            else:
                orchestration.append(
                    (
                        "textract_ddt_aadhar_page",
                        ah_ms,
                        f"page={ah_idx + 1} type={ah_type} replaced=no",
                    ),
                )
        if aadhar_ddt_touched and ocr_path:
            try:
                ocr_path.write_text(full_text, encoding="utf-8")
                orchestration.append(("rewrite_pre_ocr_txt_with_aadhar_ddt", None, f"path={ocr_path.name}"))
            except OSError:
                logger.warning("Could not rewrite pre_ocr.txt after Textract DDT (Aadhaar)")

    t_ms0 = time.perf_counter()
    mobile_scope = _pre_ocr_text_from_sales_detail_sheet_onward(full_text)
    ms_ms = int((time.perf_counter() - t_ms0) * 1000)
    orchestration.append(("sales_detail_onward_scope", ms_ms, f"chars={len(mobile_scope)}"))

    t_am0 = time.perf_counter()
    all_mobiles = _extract_all_mobiles_from_text(mobile_scope)
    am_ms = int((time.perf_counter() - t_am0) * 1000)
    orchestration.append(("extract_all_mobiles", am_ms, f"count={len(all_mobiles)}"))

    t_mc0 = time.perf_counter()
    is_multi = _detect_multi_customer(classifications) or len(all_mobiles) >= 2
    orchestration.append(("detect_multi_customer", int((time.perf_counter() - t_mc0) * 1000), f"is_multi={is_multi}"))

    # Multi-customer: when multiple document sets OR 2+ distinct mobiles in full text
    if is_multi:
        t_bd0 = time.perf_counter()
        bundles_data = _build_multi_customer_bundles(dest_pdf, full_text, classifications, all_mobiles)
        bd_ms = int((time.perf_counter() - t_bd0) * 1000)
        orchestration.append(("build_multi_customer_bundles", bd_ms, f"bundles_data={len(bundles_data) if bundles_data else 0}"))
        if bundles_data:
            logger.info("Multi-customer: built %d bundles for %s", len(bundles_data), source_pdf.name)
            t_sp0 = time.perf_counter()
            result_bundles = _split_pdf_multi_customer_to_sale_dirs(
                dest_pdf, bundles_data, dealer_id,
                page_images_cache=page_images,
                osd_rotations=osd_rotations,
            )
            sp_ms = int((time.perf_counter() - t_sp0) * 1000)
            orchestration.append(
                ("split_pdf_multi_customer_to_sale_dirs", sp_ms, f"customers={len(result_bundles)}"),
            )

            first_mobile = result_bundles[0][2] if result_bundles else mobile
            # Same pre-OCR timings apply to all customers; log full timeline under first sale folder.
            sf0 = result_bundles[0][1] if result_bundles else _log_subfolder(first_mobile)
            orchestration.append(
                ("run_pre_ocr_and_prepare_done", None, f"path=multi_customer bundles={len(result_bundles)}"),
            )
            _flush_pre(orchestration, first_mobile)
            return result_bundles, source_pdf.stem, first_mobile, ocr_path, None
        # Fall back to single-customer (e.g. scan has 2 mobiles but only 1 customer)
        logger.info("Multi-customer detected but could not build bundles; falling back to single-customer (classifications=%s, mobiles=%s)",
                    [(i, t) for i, t in classifications], all_mobiles)

    # Single customer
    classifications = list(classifications)  # mutable copy
    classified_types: set[str] = {ptype for _, ptype in classifications}
    has_aadhar_front = PAGE_TYPE_AADHAR in classified_types or PAGE_TYPE_AADHAR_COMBINED in classified_types
    has_aadhar_back = PAGE_TYPE_AADHAR_BACK in classified_types or PAGE_TYPE_AADHAR_COMBINED in classified_types

    # Fallback: Aadhar back may be blurred/darker on same page; OCR might miss uidai.gov.in.
    # If we have front but no back, and exactly one Aadhar page, treat it as combined.
    t_fb0 = time.perf_counter()
    if has_aadhar_front and not has_aadhar_back:
        aadhar_pages = [(i, p) for i, p in classifications if p in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_COMBINED)]
        if len(aadhar_pages) == 1:
            idx, _ = aadhar_pages[0]
            classifications = [(i, PAGE_TYPE_AADHAR_COMBINED if i == idx else p) for i, p in classifications]
            has_aadhar_back = True
            logger.info("Treating single Aadhar page %d as Aadhar_combined (back may be blurred)", idx + 1)
    orchestration.append(
        ("aadhar_combined_fallback", int((time.perf_counter() - t_fb0) * 1000), ""),
    )

    t_val0 = time.perf_counter()
    missing: list[str] = []
    if not mobile:
        missing.append("mobile number")
    if not has_aadhar_front:
        missing.append("Aadhar front")
    if not has_aadhar_back:
        missing.append("Aadhar back")
    if PAGE_TYPE_DETAILS not in classified_types:
        missing.append("sales details form (vehicle & customer info)")
    orchestration.append(
        ("validate_required_pages", int((time.perf_counter() - t_val0) * 1000), f"missing={len(missing)}"),
    )

    if missing:
        orchestration.append(("run_pre_ocr_and_prepare_rejected", None, f"missing={','.join(missing)}"))
        _flush_pre(orchestration, mobile)
        return None, source_pdf.stem, mobile, ocr_path, missing

    subfolder = us.get_subdir_name_mobile(mobile) if mobile else source_pdf.stem
    sale_dir = get_uploads_dir(dealer_id) / subfolder
    raw_dir = sale_dir / "raw"
    t_sp0 = time.perf_counter()
    _split_pdf_by_classification(
        dest_pdf, full_text, sale_dir,
        classifications_override=classifications,
        raw_dir=raw_dir,
        page_images_cache=page_images,
        osd_rotations=osd_rotations,
    )
    orchestration.append(
        (
            "split_pdf_by_classification",
            int((time.perf_counter() - t_sp0) * 1000),
            f"sale_dir={subfolder}",
        ),
    )
    orchestration.append(("run_pre_ocr_and_prepare_done", None, "path=single_customer"))
    _flush_pre(orchestration, mobile)

    return [(sale_dir, subfolder, mobile or "")], source_pdf.stem, mobile, ocr_path, None


def move_multi_customer_to_success_or_error(
    bundles: list[tuple[Path, str, str]],
    ocr_path: Path | None,
    original_filename_stem: str,
    results: list[bool],
    original_scan_path: Path | None = None,
    bulk_upload_dir: Path | None = None,
) -> list[str]:
    """
    Move multi-customer processing output to Success or Error per customer.
    bundles: list of ``(sale_dir, subfolder, mobile)`` — when ``sale_dir`` is already under
    ``Uploaded scans``, JPEGs are not moved (only PDF/OCR archive).
    results: list of success bool per bundle
    Returns list of result_folder paths.
    """
    base = bulk_upload_dir or BULK_UPLOAD_DIR
    success_dir = base / "Success"
    error_dir = base / "Error"
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    result_folders: list[str] = []
    uploads_root = UPLOADS_DIR.resolve()
    for i, ((sale_dir, subfolder, mobile), ok) in enumerate(zip(bundles, results)):
        if ok and mobile:
            dest_subdir = f"{mobile}_{ddmmyyyy}"
            dest_dir = success_dir / dest_subdir
        else:
            dest_subdir = f"{original_filename_stem}_cust{i + 1}_{ddmmyyyy}"
            dest_dir = error_dir / dest_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            rf = str(dest_dir.relative_to(base))
        except ValueError:
            rf = f"{'Success' if ok and mobile else 'Error'}/{dest_subdir}"
        result_folders.append(rf)

        under_uploads = False
        try:
            sale_dir.resolve().relative_to(uploads_root)
            under_uploads = True
        except ValueError:
            pass

        if sale_dir and sale_dir.exists() and not under_uploads:
            for f in sale_dir.iterdir():
                if f.is_file():
                    dest_f = dest_dir / f.name
                    shutil.move(str(f), str(dest_f))
                    logger.info("Moved %s -> %s (customer %d)", f.name, dest_dir, i + 1)
            try:
                sale_dir.rmdir()
            except OSError:
                pass
        elif under_uploads:
            logger.info(
                "Multi-customer archive: sale assets stay in %s (customer %d); archiving PDF/OCR only",
                sale_dir,
                i + 1,
            )

        if i == 0 and original_scan_path and original_scan_path.exists():
            dest_pdf = dest_dir / original_scan_path.name
            shutil.move(str(original_scan_path), str(dest_pdf))
            logger.info("Moved original scan %s -> %s", original_scan_path.name, dest_dir)

        if i == 0 and ocr_path and ocr_path.exists():
            dest_ocr = dest_dir / ocr_path.name
            shutil.move(str(ocr_path), str(dest_ocr))
            logger.info("Moved OCR %s -> %s", ocr_path.name, dest_dir)

    return result_folders


def move_processing_to_success_or_error(
    processing_path: Path | None,
    ocr_path: Path | None,
    original_filename_stem: str,
    mobile: str | None,
    success: bool,
    original_scan_path: Path | None = None,
    bulk_upload_dir: Path | None = None,
) -> str:
    """
    Move processing output and pre-OCR to Success or Error.
    processing_path: classified dir (with Aadhar.jpg, etc.) or PDF; contents moved to dest.
    Success: original scan from Input Scans -> Success/mobile_ddmmyyyy/
    Error: original scan from Input Scans -> Error/filename_ddmmyyyy/
    """
    base = bulk_upload_dir or BULK_UPLOAD_DIR
    success_dir = base / "Success"
    error_dir = base / "Error"
    proc_dir = base / "Processing"
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    if success and mobile:
        dest_subdir = f"{mobile}_{ddmmyyyy}"
        dest_dir = success_dir / dest_subdir
    else:
        dest_subdir = f"{original_filename_stem}_{ddmmyyyy}"
        dest_dir = error_dir / dest_subdir

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        result_folder = str(dest_dir.relative_to(base))
    except ValueError:
        result_folder = f"{'Success' if success and mobile else 'Error'}/{dest_subdir}"

    # Prefer original scan from Input Scans folder; move it to Success/Error so Input Scans is cleared
    if original_scan_path and original_scan_path.exists():
        dest_pdf = dest_dir / original_scan_path.name
        shutil.move(str(original_scan_path), str(dest_pdf))
        logger.info("Moved original scan %s -> %s", original_scan_path.name, dest_dir)

    # Move processing output (classified dir contents or PDF)
    if processing_path and processing_path.exists():
        if processing_path.is_dir():
            for f in processing_path.iterdir():
                if f.is_file():
                    dest_f = dest_dir / f.name
                    shutil.move(str(f), str(dest_f))
                    logger.info("Moved %s -> %s", f.name, dest_dir)
            # Force-remove classified dir (handles subdirs, leftover files)
            try:
                shutil.rmtree(processing_path, ignore_errors=True)
                logger.info("Removed classified dir %s", processing_path.name)
            except OSError:
                pass
        else:
            dest_pdf = dest_dir / processing_path.name
            shutil.move(str(processing_path), str(dest_pdf))
            logger.info("Moved %s -> %s", processing_path.name, dest_dir)

    if ocr_path and ocr_path.exists():
        dest_ocr = dest_dir / ocr_path.name
        shutil.move(str(ocr_path), str(dest_ocr))
        logger.info("Moved %s -> %s", ocr_path.name, dest_dir)

    # Clear Processing PDF if present (original already moved to dest; remove duplicate to clear Processing)
    proc_pdf = proc_dir / f"{original_filename_stem}.pdf"
    if proc_pdf.exists():
        proc_pdf.unlink(missing_ok=True)
        logger.info("Removed Processing PDF %s (cleared Processing folder)", proc_pdf.name)

    # Ensure classified dir is removed (in case processing_path was PDF or path mismatch)
    classified_dir = proc_dir / f"classified_{original_filename_stem}"
    if classified_dir.exists() and classified_dir.is_dir():
        try:
            shutil.rmtree(classified_dir, ignore_errors=True)
            logger.info("Removed classified dir %s (cleared Processing folder)", classified_dir.name)
        except OSError:
            pass

    return result_folder


def move_to_rejected(
    processing_path: Path | None,
    ocr_path: Path | None,
    original_filename_stem: str,
    original_scan_path: Path | None = None,
    bulk_upload_dir: Path | None = None,
) -> str:
    """
    Move processing output and pre-OCR to Rejected scans (validation failed).
    dest: Rejected scans/filename_ddmmyyyy/
    """
    base = bulk_upload_dir or BULK_UPLOAD_DIR
    rejected_dir = base / "Rejected scans"
    proc_dir = base / "Processing"
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    dest_subdir = f"{original_filename_stem}_{ddmmyyyy}"
    dest_dir = rejected_dir / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        result_folder = str(dest_dir.relative_to(base))
    except ValueError:
        result_folder = f"Rejected scans/{dest_subdir}"

    if original_scan_path and original_scan_path.exists():
        dest_pdf = dest_dir / original_scan_path.name
        shutil.move(str(original_scan_path), str(dest_pdf))
        logger.info("Moved original scan %s -> Rejected %s", original_scan_path.name, dest_dir)

    if processing_path and processing_path.exists():
        if processing_path.is_dir():
            for f in processing_path.iterdir():
                if f.is_file():
                    dest_f = dest_dir / f.name
                    shutil.move(str(f), str(dest_f))
                    logger.info("Moved %s -> Rejected %s", f.name, dest_dir)
            try:
                processing_path.rmdir()
            except OSError:
                pass
        else:
            dest_pdf = dest_dir / processing_path.name
            shutil.move(str(processing_path), str(dest_pdf))
            logger.info("Moved %s -> Rejected %s", processing_path.name, dest_dir)

    pdf_in_proc = proc_dir / f"{original_filename_stem}.pdf"
    if pdf_in_proc.exists():
        shutil.move(str(pdf_in_proc), str(dest_dir / pdf_in_proc.name))
        logger.info("Moved %s -> Rejected %s", pdf_in_proc.name, dest_dir)

    if ocr_path and ocr_path.exists():
        dest_ocr = dest_dir / ocr_path.name
        shutil.move(str(ocr_path), str(dest_ocr))
        logger.info("Moved %s -> Rejected %s", ocr_path.name, dest_dir)

    return result_folder
