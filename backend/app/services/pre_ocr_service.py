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

    bundles: list[dict] = []
    for slot in customer_slots:
        aadhar_name = slot["name"]

        # Find Details sheet matching this customer by name
        d_idx, mobile, d_name = None, None, None
        for i, (idx, m, dn) in enumerate(details_list):
            if idx in used_details:
                continue
            if _names_match(aadhar_name, dn):
                d_idx, mobile, d_name = idx, m, dn
                used_details.add(idx)
                break
        if d_idx is None or not mobile:
            # Fallback: use first unused Details (sequential) if no name match
            for idx, m, dn in details_list:
                if idx not in used_details and m:
                    d_idx, mobile, d_name = idx, m, dn
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


def _split_pdf_multi_customer(
    pdf_path: Path,
    bundles: list[dict],
    base_out_dir: Path,
) -> list[Path]:
    """
    Split PDF into multiple classified dirs, one per customer bundle.
    Returns list of classified_dir paths.
    """
    import fitz
    from PIL import Image
    import io

    pages = _pdf_to_images(pdf_path)
    page_bytes: dict[int, bytes] = {idx: b for idx, b in pages}
    out_dirs: list[Path] = []

    for i, bundle in enumerate(bundles):
        subdir = base_out_dir / f"customer_{i + 1}"
        subdir.mkdir(parents=True, exist_ok=True)

        for key, filename in [
            ("aadhar_front_idx", "Aadhar.jpg"),
            ("aadhar_back_idx", "Aadhar_back.jpg"),
            ("details_idx", "Details.jpg"),
            ("insurance_idx", "Insurance.jpg"),
        ]:
            idx = bundle.get(key)
            if idx is not None and idx in page_bytes:
                out_path = subdir / filename
                img = Image.open(io.BytesIO(page_bytes[idx]))
                img.save(out_path, "JPEG", quality=90)
                logger.info("Multi-customer: bundle %d page %d -> %s", i + 1, idx + 1, filename)

        out_dirs.append(subdir)

    return out_dirs


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
        elif ptype == PAGE_TYPE_AADHAR_COMBINED:
            # Combined page satisfies both Aadhar and Aadhar_back
            if PAGE_TYPE_AADHAR not in page_type_to_idx:
                page_type_to_idx[PAGE_TYPE_AADHAR] = idx
            if PAGE_TYPE_AADHAR_BACK not in page_type_to_idx:
                page_type_to_idx[PAGE_TYPE_AADHAR_BACK] = idx
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
) -> tuple[list[tuple[Path, str, str]] | None, str, str | None, Path | None, list[str] | None]:
    """
    Copy PDF to Processing, run pre-OCR, classify pages, split into Aadhar.jpg, etc.
    Validates BEFORE splitting: mobile + 3 critical classifications (Aadhar, Aadhar_back, Details).
    Returns (bundles | None, subfolder_stem, mobile_or_none, ocr_path, missing_list | None).
    bundles: list of (classified_dir, subfolder, mobile) - one per customer. Single customer = 1 bundle.
    If missing_list is non-empty, validation failed; do not split. bundles is None.
    """
    from app.services.upload_service import UploadService

    proc_dir = processing_dir or PROCESSING_DIR
    proc_dir.mkdir(parents=True, exist_ok=True)

    # Copy PDF to Processing (work on copy so original stays for .processed marker)
    dest_pdf = proc_dir / source_pdf.name
    dest_pdf.write_bytes(source_pdf.read_bytes())

    # Run pre-OCR
    full_text, ocr_path, mobile = pre_ocr_pdf(dest_pdf, processing_dir=proc_dir, use_textract=use_textract)

    classifications = classify_pages_from_ocr_text(full_text)

    # Multi-customer: only when multiple document sets in one PDF
    if _detect_multi_customer(classifications):
        bundles_data = _build_multi_customer_bundles(dest_pdf, full_text, classifications)
        if not bundles_data:
            return None, source_pdf.stem, mobile, ocr_path, ["Multi-customer: could not associate documents"]

        base_classified = proc_dir / f"classified_{source_pdf.stem}"
        base_classified.mkdir(parents=True, exist_ok=True)
        classified_dirs = _split_pdf_multi_customer(dest_pdf, bundles_data, base_classified)

        result_bundles: list[tuple[Path, str, str]] = []
        for i, (bundle, cdir) in enumerate(zip(bundles_data, classified_dirs)):
            m = bundle.get("mobile") or ""
            subfolder = UploadService().get_subdir_name_mobile(m) if m else f"{source_pdf.stem}_cust{i + 1}"
            result_bundles.append((cdir, subfolder, m))

        first_mobile = result_bundles[0][2] if result_bundles else mobile
        return result_bundles, source_pdf.stem, first_mobile, ocr_path, None

    # Single customer
    classified_types: set[str] = {ptype for _, ptype in classifications}
    has_aadhar_front = PAGE_TYPE_AADHAR in classified_types or PAGE_TYPE_AADHAR_COMBINED in classified_types
    has_aadhar_back = PAGE_TYPE_AADHAR_BACK in classified_types or PAGE_TYPE_AADHAR_COMBINED in classified_types
    missing: list[str] = []
    if not mobile:
        missing.append("mobile number")
    if not has_aadhar_front:
        missing.append("Aadhar front")
    if not has_aadhar_back:
        missing.append("Aadhar back")
    if PAGE_TYPE_DETAILS not in classified_types:
        missing.append("Details sheet")

    if missing:
        return None, source_pdf.stem, mobile, ocr_path, missing

    classified_subdir = f"classified_{source_pdf.stem}"
    classified_dir = proc_dir / classified_subdir
    _split_pdf_by_classification(dest_pdf, full_text, classified_dir)

    subfolder = UploadService().get_subdir_name_mobile(mobile) if mobile else source_pdf.stem
    return [(classified_dir, subfolder, mobile or "")], source_pdf.stem, mobile, ocr_path, None


def move_multi_customer_to_success_or_error(
    bundles: list[tuple[Path, str, str]],
    ocr_path: Path | None,
    original_filename_stem: str,
    results: list[bool],
    original_scan_path: Path | None = None,
) -> list[str]:
    """
    Move multi-customer processing output to Success or Error per customer.
    bundles: list of (classified_dir, subfolder, mobile)
    results: list of success bool per bundle
    Returns list of result_folder paths.
    """
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    result_folders: list[str] = []
    for i, ((classified_dir, subfolder, mobile), ok) in enumerate(zip(bundles, results)):
        if ok and mobile:
            dest_subdir = f"{mobile}_{ddmmyyyy}"
            dest_dir = SUCCESS_DIR / dest_subdir
        else:
            dest_subdir = f"{original_filename_stem}_cust{i + 1}_{ddmmyyyy}"
            dest_dir = ERROR_DIR / dest_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            rf = str(dest_dir.relative_to(BULK_UPLOAD_DIR))
        except ValueError:
            rf = f"{'Success' if ok and mobile else 'Error'}/{dest_subdir}"
        result_folders.append(rf)

        if classified_dir and classified_dir.exists():
            for f in classified_dir.iterdir():
                if f.is_file():
                    dest_f = dest_dir / f.name
                    shutil.move(str(f), str(dest_f))
                    logger.info("Moved %s -> %s (customer %d)", f.name, dest_dir, i + 1)
            try:
                classified_dir.rmdir()
            except OSError:
                pass

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
) -> str:
    """
    Move processing output and pre-OCR to Success or Error.
    processing_path: classified dir (with Aadhar.jpg, etc.) or PDF; contents moved to dest.
    Success: original scan from Input Scans -> Success/mobile_ddmmyyyy/
    Error: original scan from Input Scans -> Error/filename_ddmmyyyy/
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


def move_to_rejected(
    processing_path: Path | None,
    ocr_path: Path | None,
    original_filename_stem: str,
    original_scan_path: Path | None = None,
) -> str:
    """
    Move processing output and pre-OCR to Rejected scans (validation failed).
    dest: Rejected scans/filename_ddmmyyyy/
    """
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    dest_subdir = f"{original_filename_stem}_{ddmmyyyy}"
    dest_dir = REJECTED_DIR / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        result_folder = str(dest_dir.relative_to(BULK_UPLOAD_DIR))
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

    pdf_in_proc = PROCESSING_DIR / f"{original_filename_stem}.pdf"
    if pdf_in_proc.exists():
        shutil.move(str(pdf_in_proc), str(dest_dir / pdf_in_proc.name))
        logger.info("Moved %s -> Rejected %s", pdf_in_proc.name, dest_dir)

    if ocr_path and ocr_path.exists():
        dest_ocr = dest_dir / ocr_path.name
        shutil.move(str(ocr_path), str(dest_ocr))
        logger.info("Moved %s -> Rejected %s", ocr_path.name, dest_dir)

    return result_folder
