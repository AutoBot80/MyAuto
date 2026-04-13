"""
Pre-OCR for bulk upload: Tesseract pass on each PDF page (in-memory raster), classify (Aadhaar, Details, …),
then write normalized document files under ``Uploaded scans/{dealer_id}/{mobile}_ddmmyy/``.

**Raw folder:** ``…/{mobile}_ddmmyy/raw/`` holds only **PDF** artifacts: the consolidated PDF copy and
``page_01.pdf``, ``page_02.pdf``, … (one single-page PDF per source page, with OSD orientation applied
via PDF page rotation). **No** ``page_NN.jpg`` or other raster files are stored under ``raw/``.

**for_OCR folder:** ``…/{mobile}_ddmmyy/for_OCR/`` holds one **single-page PDF per document slot**
(``Aadhar.pdf``, ``Aadhar_back.pdf``, ``Details.pdf``, ``Insurance.pdf``) for :mod:`sales_ocr_service`
Textract. Populated by :func:`_sync_for_ocr_pdfs` after classification (copy from ``raw/page_NN.pdf`` or
embed split JPEGs as PDF for same-page Aadhaar). Root-level ``*.jpg`` copies remain for compatibility.

**Aadhaar handling (bulk):** separate front/back pages → copy as-is; same page top/bottom →
:func:`split_aadhar_consolidated` then letter scissor fallback via :func:`_process_same_page_aadhar`;
UIDAI letter layout → :func:`crop_aadhar_letter_below_scissors` / :func:`split_aadhar_letter_vertical`.

Add Sales uploads call :func:`normalize_aadhar_upload_files` before Textract; :mod:`sales_ocr_service`
does not repeat scissor splits.

**Chassis pencil mark:** :func:`write_pencil_mark_from_details_page` crops the top-right region of the
classified Details page (default: top-right quadrant) and saves ``pencil_mark.jpeg`` next to ``Details.jpg``.
"""

import os
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

from app.config import BULK_UPLOAD_DIR, OCR_LANG, OCR_PSM, OCR_PREPROCESS, UPLOADS_DIR, get_uploads_dir
from app.services.page_classifier import (
    classify_page_by_text,
    classify_pages_from_ocr_text,
    PAGE_TYPE_TO_FILENAME,
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_AADHAR_COMBINED,
    PAGE_TYPE_DETAILS,
    PAGE_TYPE_INSURANCE,
    PAGE_TYPE_UNUSED,
)

logger = logging.getLogger(__name__)

PROCESSING_DIR = BULK_UPLOAD_DIR / "Processing"
SUCCESS_DIR = BULK_UPLOAD_DIR / "Success"
ERROR_DIR = BULK_UPLOAD_DIR / "Error"
REJECTED_DIR = BULK_UPLOAD_DIR / "Rejected scans"

# Textract input PDFs after bulk pre-OCR (single-page, oriented); see :func:`_sync_for_ocr_pdfs`.
FOR_OCR_SUBDIR = "for_OCR"

# Details sheet — Chassis Pencil Mark crop (fractions of page width/height on the oriented JPEG).
# Default: **top-right quadrant** (right half × top half), where the pencil-mark box typically sits.
PENCIL_MARK_FILENAME = "pencil_mark.jpeg"
PENCIL_MARK_X0_FRAC = float(os.getenv("PENCIL_MARK_X0_FRAC", "0.5"))
PENCIL_MARK_X1_FRAC = float(os.getenv("PENCIL_MARK_X1_FRAC", "1.0"))
PENCIL_MARK_Y0_FRAC = float(os.getenv("PENCIL_MARK_Y0_FRAC", "0.0"))
PENCIL_MARK_Y1_FRAC = float(os.getenv("PENCIL_MARK_Y1_FRAC", "0.5"))

# Indian mobile: 10 digits starting with 6, 7, 8, or 9 (optional +91 prefix, spaces, dashes)
MOBILE_PATTERN = re.compile(r"(?:\+91[\s\-]*)?([6-9]\d{9})\b")
# Details sheet row: "Tel. Name No. 8955843403, 7733988365 Age 30/m" - prefer number after Tel/Mobile/Phone/No.
CUSTOMER_MOBILE_CONTEXT = re.compile(
    r"(?:Tel\.?|Mobile|Phone|No\.?)\s*(?:Name\s*)?(?:No\.?)?\s*[:\s]*([6-9]\d{9})",
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


def osd_deskew_clockwise_degrees(image_bytes: bytes) -> int:
    """
    Return clockwise degrees (0, 90, 180, or 270) to rotate a raster so text reads upright,
    using Tesseract **OSD** (``osd`` trained data). Returns **0** on failure, ambiguous output,
    or orientation confidence below **2.0**.
    """
    import io
    import re

    import pytesseract
    from PIL import Image

    if not image_bytes or len(image_bytes) < 800:
        return 0
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        osd = pytesseract.image_to_osd(img, lang="osd")
    except Exception as e:
        logger.debug("osd_deskew_clockwise_degrees: OSD skipped: %s", e)
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


def correct_image_orientation_upright(image_bytes: bytes) -> bytes:
    """
    Detect page orientation (upright / 90° / 180° / 270°) via Tesseract **OSD** (``osd`` language)
    and rotate the image so text reads normally. Requires ``tessdata/osd.traineddata``.

    On OSD failure or ambiguous output, returns the input bytes unchanged.
    """
    import io

    from PIL import Image

    rot_cw = osd_deskew_clockwise_degrees(image_bytes)
    if rot_cw == 0:
        return image_bytes

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Tesseract ``Rotate`` = degrees to turn **clockwise** to deskew. PIL positive angle = CCW.
    img2 = img.rotate(-rot_cw, expand=True, fillcolor="white")
    out = io.BytesIO()
    img2.save(out, "JPEG", quality=92)
    logger.info("correct_image_orientation_upright: applied %s° CW correction (PIL -%s)", rot_cw, rot_cw)
    return out.getvalue()


def _pdf_to_images(
    pdf_path: Path,
    max_pages: int = 20,
    *,
    fix_orientation: bool = True,
) -> list[tuple[int, bytes]]:
    """Convert PDF pages to JPEG bytes. Optionally deskew to upright via :func:`correct_image_orientation_upright`."""
    import fitz
    from PIL import Image
    import io

    result: list[tuple[int, bytes]] = []
    doc = fitz.open(str(pdf_path))
    try:
        for i in range(min(doc.page_count, max_pages)):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            data = buf.getvalue()
            if fix_orientation:
                data = correct_image_orientation_upright(data)
            result.append((i, data))
    finally:
        doc.close()
    return result


def extract_details_chassis_pencil_mark_jpeg(page_jpeg_bytes: bytes) -> bytes | None:
    """
    Crop the **Chassis Pencil Mark** region from a full Details sheet page (JPEG bytes).

    Uses the **top-right quadrant** by default: right half of the page (50–100% width) × top half
    (0–50% height), which matches common dealer forms (pencil-mark box above chassis fields).
    Bounds are set via :data:`PENCIL_MARK_*_FRAC` (override with env vars of the same name).
    """
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

    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return None
    return buf.tobytes()


def write_pencil_mark_from_details_page(sale_dir: Path, page_jpeg_bytes: bytes) -> bool:
    """
    Write ``pencil_mark.jpeg`` under ``sale_dir`` from the full Details page raster.

    Returns True if a non-empty crop was written.
    """
    out = extract_details_chassis_pencil_mark_jpeg(page_jpeg_bytes)
    if not out:
        logger.warning("pencil_mark: crop failed or empty for %s", sale_dir)
        return False
    sale_dir.mkdir(parents=True, exist_ok=True)
    dest = sale_dir / PENCIL_MARK_FILENAME
    dest.write_bytes(out)
    logger.info("Wrote %s (Chassis Pencil Mark crop)", dest.name)
    return True


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


def _export_single_page_pdfs_to_raw(pdf_path: Path, raw_dir: Path, max_pages: int = 20) -> None:
    """
    Split a multi-page PDF into ``page_01.pdf``, ``page_02.pdf``, … under ``raw_dir``
    (one PDF per original page). Applies **Tesseract OSD** on a raster render of each page and
    sets **PDF page rotation** so the saved single-page PDF is upright (no JPEG files under ``raw/``).
    """
    import fitz
    from PIL import Image
    import io

    raw_dir.mkdir(parents=True, exist_ok=True)
    src = fitz.open(str(pdf_path))
    try:
        n = min(src.page_count, max_pages)
        for i in range(n):
            page = src[i]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            rot = osd_deskew_clockwise_degrees(buf.getvalue())

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


def _tesseract_ocr(image_bytes: bytes) -> str:
    """Run Tesseract OCR on image bytes. Returns full text."""
    import pytesseract
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(image_bytes))
    if OCR_PREPROCESS:
        img = img.convert("L")
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
    return pytesseract.image_to_string(img, lang=OCR_LANG, config=f"--psm {OCR_PSM}")


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
    True when Tesseract (or equivalent) text matches **both** UIDAI front and back markers
    on one page: ``Government of India`` and ``uidai.gov.in`` (see ``classify_page_by_text``).
    """
    return classify_page_by_text(ocr_text or "") == PAGE_TYPE_AADHAR_COMBINED


def _consolidated_halves_need_swap(top_ocr: str, bottom_ocr: str) -> bool:
    """
    After a horizontal cut, decide whether the **top** image is actually the card **back**
    (so we swap before writing front/back files).

    Uses ``classify_page_by_text`` per half (same markers as bulk: uidai.gov.in vs Government of India),
    with keyword fallbacks when OCR is noisy.
    """
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


def _export_pdf_pages_to_raw(pdf_path: Path, raw_dir: Path) -> None:
    """
    Under ``raw_dir``: single-page PDFs only (``page_01.pdf``, …), each oriented via OSD
    (see :func:`_export_single_page_pdfs_to_raw`). Does **not** write raster previews; ``raw/`` stays PDF-only.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    _export_single_page_pdfs_to_raw(pdf_path, raw_dir)


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

    for ptype, jpg_name in PAGE_TYPE_TO_FILENAME.items():
        pdf_name = jpg_name.replace(".jpg", ".pdf")
        idx = page_type_to_idx.get(ptype)
        if idx is None:
            continue
        dest = for_ocr / pdf_name

        if idx in combined_indices and ptype in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_BACK):
            jpg_path = sale_dir / jpg_name
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


def _process_same_page_aadhar(page_bytes: bytes, sale_dir: Path) -> bool:
    """
    One physical page with both Aadhaar faces.

    1) Consolidated (top/bottom): :func:`split_aadhar_consolidated`.
    2) Letter (scissor lines): :func:`crop_aadhar_letter_below_scissors` +
       :func:`split_aadhar_letter_vertical`.

    Writes ``Aadhar.jpg`` and ``Aadhar_back.jpg`` under ``sale_dir``.
    Returns True if both files were produced.
    """
    sale_dir.mkdir(parents=True, exist_ok=True)
    if split_aadhar_consolidated(
        page_bytes,
        out_dir=sale_dir,
        front_name="Aadhar.jpg",
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
        (sale_dir / "Aadhar.jpg").write_bytes(split[0])
        (sale_dir / "Aadhar_back.jpg").write_bytes(split[1])
        logger.info("Same-page Aadhaar: letter (scissor) split into front/back")
        return True
    (sale_dir / "Aadhar.jpg").write_bytes(cropped)
    logger.warning("Same-page Aadhaar: letter crop only; missing Aadhar_back.jpg")
    return False


def orient_common_sale_jpegs(subdir: Path) -> None:
    """Run :func:`correct_image_orientation_upright` on Details / Insurance / Financing JPEGs (Add Sales)."""
    for name in ("Details.jpg", "Insurance.jpg", "Financing.jpg"):
        p = subdir / name
        if p.is_file():
            p.write_bytes(correct_image_orientation_upright(p.read_bytes()))


def normalize_aadhar_upload_files(subdir: Path) -> None:
    """
    OSD upright orientation on Aadhaar JPEGs, then physical layout fixes (letter / scissors) before Textract.
    Used by Add Sales upload; bulk pre-OCR already writes normalized card crops. Idempotent on plain scans.
    """
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


def _extract_mobile_from_text(text: str) -> str | None:
    """
    Extract Indian 10-digit mobile from text.
    Prefer number in Details sheet context: "Tel. Name No. 8955843403, 7733988365 Age 30/m".
    Falls back to first valid mobile if no context match.
    """
    if not text:
        return None
    # First try: number after Tel/Mobile/Phone/No. (Details sheet customer row)
    match = CUSTOMER_MOBILE_CONTEXT.search(text)
    if match:
        return match.group(1)
    # Fallback: first 10-digit number starting with 6-9
    match = MOBILE_PATTERN.search(text)
    return match.group(1) if match else None


def _extract_all_mobiles_from_text(text: str) -> list[str]:
    """Extract all distinct Indian 10-digit mobiles from text, in order of first appearance."""
    if not text:
        return []
    seen: set[str] = set()
    result: list[str] = []
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
) -> list[tuple[Path, str, str]]:
    """
    For each customer bundle, write into ``Uploaded scans/{dealer_id}/{mobile}_ddmmyy/``
    with ``raw/`` containing the consolidated PDF and per-page PDFs (``page_NN.pdf``).

    Same index for front+back → :func:`_process_same_page_aadhar`; else one raster per slot for outputs.
    Returns ``(sale_dir, subfolder, mobile)`` per bundle.
    """
    from PIL import Image
    import io

    from app.services.upload_service import UploadService

    us = UploadService()
    pages = _pdf_to_images(pdf_path)
    page_bytes: dict[int, bytes] = {idx: b for idx, b in pages}
    result: list[tuple[Path, str, str]] = []

    for i, bundle in enumerate(bundles):
        m = bundle.get("mobile") or ""
        subfolder = us.get_subdir_name_mobile(m) if m else f"{pdf_path.stem}_cust{i + 1}"
        sale_dir = get_uploads_dir(dealer_id) / subfolder
        raw_dir = sale_dir / "raw"
        sale_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, raw_dir / pdf_path.name)
        _export_pdf_pages_to_raw(pdf_path, raw_dir)

        fi = bundle.get("aadhar_front_idx")
        bi = bundle.get("aadhar_back_idx")
        if fi is not None and bi is not None and fi == bi and fi in page_bytes:
            if not _process_same_page_aadhar(page_bytes[fi], sale_dir):
                logger.warning("Multi bundle %d: same-page Aadhaar split failed; saving full page as Aadhar.jpg", i + 1)
                img = Image.open(io.BytesIO(page_bytes[fi]))
                img.save(sale_dir / "Aadhar.jpg", "JPEG", quality=90)
        else:
            for key, filename in [
                ("aadhar_front_idx", "Aadhar.jpg"),
                ("aadhar_back_idx", "Aadhar_back.jpg"),
                ("details_idx", "Details.jpg"),
                ("insurance_idx", "Insurance.jpg"),
            ]:
                idx = bundle.get(key)
                if idx is not None and idx in page_bytes:
                    out_path = sale_dir / filename
                    img = Image.open(io.BytesIO(page_bytes[idx]))
                    img.save(out_path, "JPEG", quality=90)
                    logger.info("Multi-customer: bundle %d page %d -> %s", i + 1, idx + 1, filename)

        didx = bundle.get("details_idx")
        if didx is not None and didx in page_bytes:
            write_pencil_mark_from_details_page(sale_dir, page_bytes[didx])

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
) -> tuple[str, Path | None, str | None]:
    """
    Extract mobile from PDF using Tesseract per page (Details sheet has mobile).
    Saves to Processing/filename_ddmmyyyy_pre_ocr.txt. Returns (full_text, ocr_file_path, mobile_or_none).
    """
    proc_dir = processing_dir or PROCESSING_DIR
    proc_dir.mkdir(parents=True, exist_ok=True)

    filename_stem = pdf_path.stem
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    ocr_basename = f"{filename_stem}_{ddmmyyyy}_pre_ocr.txt"
    ocr_path = proc_dir / ocr_basename

    try:
        pages = _pdf_to_images(pdf_path)
        all_text_parts: list[str] = []
        for page_idx, jpeg_bytes in pages:
            text = _tesseract_ocr(jpeg_bytes)
            if text.strip():
                all_text_parts.append(f"--- Page {page_idx + 1} ---\n{text}")

        full_text = "\n\n".join(all_text_parts)
        ocr_path.write_text(full_text, encoding="utf-8")
        mobile = _extract_mobile_from_text(full_text)
        return full_text, ocr_path, mobile
    except Exception as e:
        logger.exception("pre_ocr failed for %s", pdf_path)
        ocr_path.write_text(f"OCR error: {e}\n", encoding="utf-8")
        return "", ocr_path, None


def _split_pdf_by_classification(
    pdf_path: Path,
    full_ocr_text: str,
    sale_dir: Path,
    classifications_override: list[tuple[int, str]] | None = None,
    raw_dir: Path | None = None,
) -> Path:
    """
    Classify each page from OCR text; write card/sheet JPEGs under ``sale_dir``.
    If ``raw_dir`` is set, store the consolidated PDF copy and per-page PDFs under ``raw/`` (``page_NN.pdf``, no raster).

    Same-page Aadhaar: consolidated top/bottom split, then letter scissor split via :func:`_process_same_page_aadhar`.
    """
    import fitz
    from PIL import Image
    import io

    sale_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, raw_dir / pdf_path.name)
        _export_pdf_pages_to_raw(pdf_path, raw_dir)

    classifications = classifications_override if classifications_override is not None else classify_pages_from_ocr_text(full_ocr_text)
    page_type_to_idx: dict[str, int] = {}  # first page index per type
    unused_indices: list[int] = []
    for idx, ptype in classifications:
        if ptype == PAGE_TYPE_UNUSED:
            unused_indices.append(idx)
        elif ptype == PAGE_TYPE_AADHAR_COMBINED:
            # Combined page satisfies both Aadhar and Aadhar_back
            if PAGE_TYPE_AADHAR not in page_type_to_idx:
                page_type_to_idx[PAGE_TYPE_AADHAR] = idx
            if PAGE_TYPE_AADHAR_BACK not in page_type_to_idx:
                page_type_to_idx[PAGE_TYPE_AADHAR_BACK] = idx
        elif ptype not in page_type_to_idx:
            page_type_to_idx[ptype] = idx

    pages = _pdf_to_images(pdf_path)
    page_bytes: dict[int, bytes] = {idx: b for idx, b in pages}

    combined_indices = {idx for idx, ptype in classifications if ptype == PAGE_TYPE_AADHAR_COMBINED}
    for idx in combined_indices:
        if idx not in page_bytes:
            continue
        if not _process_same_page_aadhar(page_bytes[idx], sale_dir):
            logger.warning(
                "Aadhaar combined page %d: consolidated + letter split failed; copying full page to Aadhar.jpg only",
                idx + 1,
            )
            out_path = sale_dir / "Aadhar.jpg"
            img = Image.open(io.BytesIO(page_bytes[idx]))
            img.save(out_path, "JPEG", quality=90)

    # Write known types (skip Aadhar slots already produced from combined split)
    for ptype, filename in PAGE_TYPE_TO_FILENAME.items():
        if ptype in page_type_to_idx:
            idx = page_type_to_idx[ptype]
            if idx in combined_indices and ptype in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_BACK):
                continue
            if idx in page_bytes:
                out_path = sale_dir / filename
                img = Image.open(io.BytesIO(page_bytes[idx]))
                img.save(out_path, "JPEG", quality=90)
                logger.info("Classified page %d -> %s", idx + 1, filename)

    if PAGE_TYPE_DETAILS in page_type_to_idx:
        didx = page_type_to_idx[PAGE_TYPE_DETAILS]
        if didx in page_bytes:
            write_pencil_mark_from_details_page(sale_dir, page_bytes[didx])

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
) -> tuple[list[tuple[Path, str, str]] | None, str, str | None, Path | None, list[str] | None]:
    """
    Copy PDF to Processing, run pre-OCR (Tesseract on in-memory rasters), classify pages, write normalized
    JPEGs under ``Uploaded scans/{dealer_id}/{mobile}_ddmmyy/`` with ``raw/`` holding **PDF only**
    (consolidated copy + ``page_NN.pdf`` per page).

    Validates BEFORE splitting: mobile + 3 critical classifications (Aadhar, Aadhar_back, Details).
    Returns (bundles | None, subfolder_stem, mobile_or_none, ocr_path, missing_list | None).
    bundles: list of ``(sale_dir, subfolder, mobile)`` where ``sale_dir`` is the uploads folder for the sale.
    If missing_list is non-empty, validation failed; do not split. bundles is None.
    """
    from app.services.upload_service import UploadService

    proc_dir = processing_dir or PROCESSING_DIR
    proc_dir.mkdir(parents=True, exist_ok=True)

    # Copy PDF to Processing (work on copy so original stays for .processed marker)
    dest_pdf = proc_dir / source_pdf.name
    dest_pdf.write_bytes(source_pdf.read_bytes())

    # Run pre-OCR
    full_text, ocr_path, mobile = pre_ocr_pdf(dest_pdf, processing_dir=proc_dir)

    classifications = classify_pages_from_ocr_text(full_text)
    all_mobiles = _extract_all_mobiles_from_text(full_text)

    # Multi-customer: when multiple document sets OR 2+ distinct mobiles in full text
    is_multi = _detect_multi_customer(classifications) or len(all_mobiles) >= 2
    if is_multi:
        bundles_data = _build_multi_customer_bundles(dest_pdf, full_text, classifications, all_mobiles)
        if bundles_data:
            logger.info("Multi-customer: built %d bundles for %s", len(bundles_data), source_pdf.name)
            result_bundles = _split_pdf_multi_customer_to_sale_dirs(dest_pdf, bundles_data, dealer_id)

            first_mobile = result_bundles[0][2] if result_bundles else mobile
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
    if has_aadhar_front and not has_aadhar_back:
        aadhar_pages = [(i, p) for i, p in classifications if p in (PAGE_TYPE_AADHAR, PAGE_TYPE_AADHAR_COMBINED)]
        if len(aadhar_pages) == 1:
            idx, _ = aadhar_pages[0]
            classifications = [(i, PAGE_TYPE_AADHAR_COMBINED if i == idx else p) for i, p in classifications]
            has_aadhar_back = True
            logger.info("Treating single Aadhar page %d as Aadhar_combined (back may be blurred)", idx + 1)

    missing: list[str] = []
    if not mobile:
        missing.append("mobile number")
    if not has_aadhar_front:
        missing.append("Aadhar front")
    if not has_aadhar_back:
        missing.append("Aadhar back")
    if PAGE_TYPE_DETAILS not in classified_types:
        missing.append("sales details form (vehicle & customer info)")

    if missing:
        return None, source_pdf.stem, mobile, ocr_path, missing

    subfolder = UploadService().get_subdir_name_mobile(mobile) if mobile else source_pdf.stem
    sale_dir = get_uploads_dir(dealer_id) / subfolder
    raw_dir = sale_dir / "raw"
    _split_pdf_by_classification(
        dest_pdf, full_text, sale_dir, classifications_override=classifications, raw_dir=raw_dir
    )

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
