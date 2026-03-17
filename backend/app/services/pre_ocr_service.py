"""
Pre-OCR for bulk upload: extract mobile number before folder structure and Add customer.
Uses Textract/Tesseract on full PDF (Details sheet has mobile). Saves OCR to Bulk Upload/Processing/filename_ddmmyyyy_pre_ocr.txt.
After processing: move scan + OCR to Success/mobile_ddmmyyyy or Error/filename_ddmmyyyy; clear Processing.
"""

import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

from app.config import BULK_UPLOAD_DIR, OCR_LANG, OCR_PSM, OCR_PREPROCESS
from app.services.page_classifier import (
    classify_pages_from_ocr_text,
    PAGE_TYPE_TO_FILENAME,
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_DETAILS,
    PAGE_TYPE_INSURANCE,
    PAGE_TYPE_UNUSED,
)

logger = logging.getLogger(__name__)

PROCESSING_DIR = BULK_UPLOAD_DIR / "Processing"
SUCCESS_DIR = BULK_UPLOAD_DIR / "Success"
ERROR_DIR = BULK_UPLOAD_DIR / "Error"

# Indian mobile: 10 digits starting with 6, 7, 8, or 9 (optional +91 prefix, spaces, dashes)
MOBILE_PATTERN = re.compile(r"(?:\+91[\s\-]*)?([6-9]\d{9})\b")
# Details sheet row: "Tel. Name No. 8955843403, 7733988365 Age 30/m" - prefer number after Tel/Mobile/Phone/No.
CUSTOMER_MOBILE_CONTEXT = re.compile(
    r"(?:Tel\.?|Mobile|Phone|No\.?)\s*(?:Name\s*)?(?:No\.?)?\s*[:\s]*([6-9]\d{9})",
    re.IGNORECASE,
)


def _pdf_to_images(pdf_path: Path, max_pages: int = 20) -> list[tuple[int, bytes]]:
    """Convert PDF pages to JPEG bytes. Returns list of (page_index, jpeg_bytes)."""
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
            result.append((i, buf.getvalue()))
    finally:
        doc.close()
    return result


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


def _textract_ocr(image_bytes: bytes) -> str:
    """Run AWS Textract on image bytes. Returns full text."""
    from app.services.textract_service import extract_text_from_bytes
    result = extract_text_from_bytes(image_bytes)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result.get("full_text") or ""


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


def pre_ocr_pdf(
    pdf_path: Path,
    processing_dir: Path | None = None,
    use_textract: bool = False,
) -> tuple[str, Path | None, str | None]:
    """
    Extract mobile from PDF using Textract/Tesseract (Details sheet has mobile).
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
            if use_textract:
                try:
                    text = _textract_ocr(jpeg_bytes)
                except Exception as e:
                    logger.warning("pre_ocr: Textract failed for page %s, fallback to Tesseract: %s", page_idx, e)
                    text = _tesseract_ocr(jpeg_bytes)
            else:
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
    out_dir: Path,
) -> Path:
    """
    Classify each page from OCR text, split PDF into Aadhar.jpg, Aadhar_back.jpg,
    Details.jpg, Insurance.jpg, and unused.pdf. Returns out_dir.
    """
    import fitz
    from PIL import Image
    import io

    classifications = classify_pages_from_ocr_text(full_ocr_text)
    page_type_to_idx: dict[str, int] = {}  # first page index per type
    unused_indices: list[int] = []
    for idx, ptype in classifications:
        if ptype == PAGE_TYPE_UNUSED:
            unused_indices.append(idx)
        elif ptype not in page_type_to_idx:
            page_type_to_idx[ptype] = idx

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = _pdf_to_images(pdf_path)
    page_bytes: dict[int, bytes] = {idx: b for idx, b in pages}

    # Write known types
    for ptype, filename in PAGE_TYPE_TO_FILENAME.items():
        if ptype in page_type_to_idx:
            idx = page_type_to_idx[ptype]
            if idx in page_bytes:
                out_path = out_dir / filename
                img = Image.open(io.BytesIO(page_bytes[idx]))
                img.save(out_path, "JPEG", quality=90)
                logger.info("Classified page %d -> %s", idx + 1, filename)

    # Merge unused pages into unused.pdf
    if unused_indices:
        doc = fitz.open(str(pdf_path))
        try:
            merged = fitz.open()
            for idx in sorted(unused_indices):
                if idx < doc.page_count:
                    merged.insert_pdf(doc, from_page=idx, to_page=idx)
            if merged.page_count > 0:
                unused_path = out_dir / "unused.pdf"
                merged.save(str(unused_path))
                logger.info("Wrote %d unused page(s) -> unused.pdf", len(unused_indices))
            merged.close()
        finally:
            doc.close()

    return out_dir


def run_pre_ocr_and_prepare(
    source_pdf: Path,
    processing_dir: Path | None = None,
    use_textract: bool = True,
) -> tuple[Path, str, str | None, Path | None]:
    """
    Copy PDF to Processing, run pre-OCR, classify pages, split into Aadhar.jpg,
    Aadhar_back.jpg, Details.jpg, Insurance.jpg, unused.pdf.
    Returns (classified_dir, subfolder, mobile_or_none, ocr_path).
    classified_dir contains the split files; subfolder = mobile_ddmmyy if mobile found.
    Mobile is required for Add Customer; caller must not proceed if None.
    """
    from app.services.upload_service import UploadService

    proc_dir = processing_dir or PROCESSING_DIR
    proc_dir.mkdir(parents=True, exist_ok=True)

    # Copy PDF to Processing (work on copy so original stays for .processed marker)
    dest_pdf = proc_dir / source_pdf.name
    dest_pdf.write_bytes(source_pdf.read_bytes())

    # Run pre-OCR
    full_text, ocr_path, mobile = pre_ocr_pdf(dest_pdf, processing_dir=proc_dir, use_textract=use_textract)

    # Classify and split pages into logical files (order-independent)
    classified_subdir = f"classified_{source_pdf.stem}"
    classified_dir = proc_dir / classified_subdir
    _split_pdf_by_classification(dest_pdf, full_text, classified_dir)

    if mobile:
        subfolder = UploadService().get_subdir_name_mobile(mobile)
    else:
        subfolder = source_pdf.stem

    return classified_dir, subfolder, mobile, ocr_path


def move_processing_to_success_or_error(
    processing_path: Path | None,
    ocr_path: Path | None,
    original_filename_stem: str,
    mobile: str | None,
    success: bool,
    original_scan_path: Path | None = None,
) -> str:
    """
    Move processing output and pre-OCR to Success or Error.
    processing_path: classified dir (with Aadhar.jpg, etc.) or PDF; contents moved to dest.
    Success: original scan from Scans -> Success/mobile_ddmmyyyy/
    Error: original scan from Scans -> Error/filename_ddmmyyyy/
    """
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    if success and mobile:
        dest_subdir = f"{mobile}_{ddmmyyyy}"
        dest_dir = SUCCESS_DIR / dest_subdir
    else:
        dest_subdir = f"{original_filename_stem}_{ddmmyyyy}"
        dest_dir = ERROR_DIR / dest_subdir

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        result_folder = str(dest_dir.relative_to(BULK_UPLOAD_DIR))
    except ValueError:
        result_folder = f"{'Success' if success and mobile else 'Error'}/{dest_subdir}"

    # Prefer original scan from Scans folder; move it to Success/Error so Scans is cleared
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
            try:
                processing_path.rmdir()
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

    return result_folder
