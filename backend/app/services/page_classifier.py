"""
Classify individual PDF pages by OCR text for bulk upload.
Pages can be in any order; this module identifies Aadhar, Aadhar_back, Details, Insurance,
and puts unrecognized pages into 'unused'.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Page type identifiers (output filenames)
PAGE_TYPE_AADHAR = "Aadhar"
PAGE_TYPE_AADHAR_BACK = "Aadhar_back"
PAGE_TYPE_AADHAR_COMBINED = "Aadhar_combined"  # Single page with both front and back
PAGE_TYPE_DETAILS = "Details"
PAGE_TYPE_INSURANCE = "Insurance"
PAGE_TYPE_UNUSED = "unused"

# Sale-folder output names after pre-OCR / classification (root of mobile_ddmmyy/)
FILENAME_AADHAR_FRONT = "Aadhar_front.jpg"
FILENAME_AADHAR_BACK = "Aadhar_back.jpg"
FILENAME_SALES_DETAIL_SHEET_PDF = "Sales_Detail_Sheet.pdf"
FILENAME_INSURANCE = "Insurance.jpg"
# Legacy manual-upload names (still accepted downstream)
LEGACY_AADHAR_FRONT_JPG = "Aadhar.jpg"
LEGACY_DETAILS_JPG = "Details.jpg"

PAGE_TYPE_TO_FILENAME = {
    PAGE_TYPE_AADHAR: FILENAME_AADHAR_FRONT,
    PAGE_TYPE_AADHAR_BACK: FILENAME_AADHAR_BACK,
    PAGE_TYPE_DETAILS: FILENAME_SALES_DETAIL_SHEET_PDF,
    PAGE_TYPE_INSURANCE: FILENAME_INSURANCE,
}

# Patterns to identify each page type. Order matters: more specific first.
# Details: vehicle/customer sheet with Frame No, Chassis, Key No, etc.
_DETAILS_PATTERNS = [
    re.compile(r"frame\s*(?:no\.?|number)?\s*[:]?", re.IGNORECASE),
    re.compile(r"chassis\s*(?:no\.?|number)?\s*[:]?", re.IGNORECASE),
    re.compile(r"engine\s*(?:no\.?|number)?\s*[:]?", re.IGNORECASE),
    re.compile(r"key\s*(?:no\.?|number)?\s*[:]?", re.IGNORECASE),
    re.compile(r"battery\s*(?:no\.?|number)?\s*[:]?", re.IGNORECASE),
    re.compile(r"model\s*(?:&|and|\/)\s*colour", re.IGNORECASE),
    re.compile(r"nominee\s*name", re.IGNORECASE),
    re.compile(r"profession\s*[:]?", re.IGNORECASE),
    re.compile(r"tel\.?\s*name\s*no\.?", re.IGNORECASE),
    re.compile(r"buyer['\u2019]?s?\s*order", re.IGNORECASE),
    re.compile(r"vehicle\s*(?:details|info|no\.?)?", re.IGNORECASE),
    re.compile(r"customer\s*(?:name|mobile|details)", re.IGNORECASE),
]

# Insurance: policy document with Gross Premium, Cert. No., etc.
_INSURANCE_PATTERNS = [
    re.compile(r"gross\s*premium", re.IGNORECASE),
    re.compile(r"(?:policy|cert\.?)\s*no\.?\s*[:]?", re.IGNORECASE),
    re.compile(r"od\s*policy\s*period", re.IGNORECASE),
    re.compile(r"tp\s*valid\s*(?:from|to)", re.IGNORECASE),
    re.compile(r"insurance\s+company\s+(?:limited|ltd\.?)?", re.IGNORECASE),
    re.compile(r"national\s+insurance", re.IGNORECASE),
    re.compile(r"premium\s+of\s+rs\.?", re.IGNORECASE),
]

# Aadhar back: Address, www.uidai.gov.in (distinctive — back has this URL)
_AADHAR_BACK_PATTERNS = [
    re.compile(r"www\.uidai\.gov\.in|uidai\.gov\.in", re.IGNORECASE),
    re.compile(r"address\s+of\s+the\s+cardholder", re.IGNORECASE),
    re.compile(r"address\s+is\s+as\s+per\s+(?:the\s+)?uidai", re.IGNORECASE),
    re.compile(r"\baddress\b.*\buidai\b|\buidai\b.*\baddress\b", re.IGNORECASE | re.DOTALL),
]

# Aadhar front: Government of India, QR (not detectable from text); no uidai.gov.in
_AADHAR_FRONT_PATTERNS = [
    re.compile(r"government\s+of\s+india", re.IGNORECASE),
    re.compile(r"unique\s+identification\s+authority\s+of\s+india", re.IGNORECASE),
    re.compile(r"\b\d{4}\s+\d{4}\s+\d{4}\b"),  # 12-digit Aadhar format
    re.compile(r"(?:male|female)\s*/\s*(?:m|f)\b", re.IGNORECASE),
    re.compile(r"date\s+of\s+birth|year\s+of\s+birth|d\.?o\.?b\.?", re.IGNORECASE),
    re.compile(r"care\s+of\s*[:]?", re.IGNORECASE),
]


# Back has www.uidai.gov.in; front must NOT have it (front has Government of India)
_AADHAR_BACK_MARKER = re.compile(r"www\.uidai\.gov\.in|uidai\.gov\.in", re.IGNORECASE)
_AADHAR_FRONT_MARKER = re.compile(r"government\s+of\s+india", re.IGNORECASE)


def classify_page_by_text(text: str) -> str:
    """
    Classify a single page from its OCR text.
    Aadhar front: Government of India (no uidai.gov.in).
    Aadhar back: Address, www.uidai.gov.in.
    Returns one of: Aadhar, Aadhar_back, Details, Insurance, unused.
    """
    if not text or not isinstance(text, str):
        return PAGE_TYPE_UNUSED

    t = text.strip()
    if len(t) < 20:
        return PAGE_TYPE_UNUSED

    # Aadhar back vs front: back has uidai.gov.in; front has Government of India
    has_back_marker = _AADHAR_BACK_MARKER.search(t)
    has_front_marker = _AADHAR_FRONT_MARKER.search(t)
    if has_back_marker and has_front_marker:
        # Single page with both front and back (e.g. folded card scanned together)
        return PAGE_TYPE_AADHAR_COMBINED
    if has_back_marker:
        # www.uidai.gov.in is on the back — classify as back
        return PAGE_TYPE_AADHAR_BACK
    if has_front_marker:
        # Government of India without uidai.gov.in — front
        return PAGE_TYPE_AADHAR

    # Score other types
    scores: dict[str, int] = {
        PAGE_TYPE_DETAILS: 0,
        PAGE_TYPE_INSURANCE: 0,
        PAGE_TYPE_AADHAR_BACK: 0,
        PAGE_TYPE_AADHAR: 0,
    }

    for pat in _DETAILS_PATTERNS:
        if pat.search(t):
            scores[PAGE_TYPE_DETAILS] += 1
    for pat in _INSURANCE_PATTERNS:
        if pat.search(t):
            scores[PAGE_TYPE_INSURANCE] += 1
    for pat in _AADHAR_BACK_PATTERNS:
        if pat.search(t):
            scores[PAGE_TYPE_AADHAR_BACK] += 1
    for pat in _AADHAR_FRONT_PATTERNS:
        if pat.search(t):
            scores[PAGE_TYPE_AADHAR] += 1

    best_type = PAGE_TYPE_UNUSED
    best_score = 0
    for ptype, score in scores.items():
        if score > best_score:
            best_score = score
            best_type = ptype

    if best_score == 0:
        return PAGE_TYPE_UNUSED
    return best_type


def classify_pages_from_ocr_text(full_ocr_text: str) -> list[tuple[int, str]]:
    """
    Parse pre-OCR output (--- Page N --- blocks) and classify each page.
    Returns list of (page_index_0based, page_type).
    """
    page_blocks = re.split(r"---\s*Page\s+(\d+)\s*---", full_ocr_text)
    # page_blocks: ['', '1', 'text1', '2', 'text2', ...]
    result: list[tuple[int, str]] = []
    i = 1
    while i < len(page_blocks):
        try:
            page_num = int(page_blocks[i])
            page_text = page_blocks[i + 1] if i + 1 < len(page_blocks) else ""
            page_type = classify_page_by_text(page_text)
            result.append((page_num - 1, page_type))  # 0-based index
            logger.debug("Page %d classified as %s", page_num, page_type)
        except (ValueError, IndexError):
            pass
        i += 2
    return result


def extract_page_text_from_pre_ocr_blocks(full_ocr_text: str, page_idx_0based: int) -> str:
    """Return OCR text for one page from ``--- Page N ---`` blocks (pre-OCR output)."""
    pat = rf"---\s*Page\s+{page_idx_0based + 1}\s*---\s*(.*?)(?=\n---\s*Page\s+\d+\s*---|\Z)"
    m = re.search(pat, full_ocr_text or "", re.DOTALL)
    return (m.group(1).strip() if m else "")


def aadhar_text_has_dob_and_gender(text: str) -> bool:
    """
    Aadhaar **front** (photo side) reliably shows **DOB** and **Male/Female** on the same face;
    the **back** (address / uidai) usually does not. Used to assign front vs back when OCR order is wrong.
    """
    t = text or ""
    if not t.strip():
        return False
    has_dob = bool(
        re.search(r"(?i)d\.?o\.?b\.|date\s+of\s+birth|year\s+of\s+birth", t)
    )
    has_gender = bool(
        re.search(r"(?i)\b(male|female)\b|\bm\s*/\s*f\b|\bf\s*/\s*m\b", t)
    )
    return has_dob and has_gender


def should_swap_aadhar_pages_by_dob_gender(text_at_front_slot: str, text_at_back_slot: str) -> bool | None:
    """
    Decide if front/back **slots** should be swapped using DOB + Male/Female = front.

    Returns:
        ``False`` — keep order (first arg is really the front face).
        ``True`` — swap (second arg is the front face).
        ``None`` — inconclusive; caller may fall back to uidai/govt markers.
    """
    f_ok = aadhar_text_has_dob_and_gender(text_at_front_slot)
    b_ok = aadhar_text_has_dob_and_gender(text_at_back_slot)
    if f_ok and not b_ok:
        return False
    if b_ok and not f_ok:
        return True
    if f_ok and b_ok:
        # Rare OCR noise: prefer the side **without** uidai.gov.in as the photo front.
        uf = bool(_AADHAR_BACK_MARKER.search(text_at_front_slot or ""))
        ub = bool(_AADHAR_BACK_MARKER.search(text_at_back_slot or ""))
        if uf and not ub:
            return True
        if ub and not uf:
            return False
        return None
    return None


def should_swap_aadhar_front_back_indices(text_classified_front: str, text_classified_back: str) -> bool:
    """
    Detect swapped front/back: page marked front reads like back (uidai.gov.in) and back like front (Govt of India).
    """
    has_uidai_f = bool(_AADHAR_BACK_MARKER.search(text_classified_front or ""))
    has_gov_f = bool(_AADHAR_FRONT_MARKER.search(text_classified_front or ""))
    has_uidai_b = bool(_AADHAR_BACK_MARKER.search(text_classified_back or ""))
    has_gov_b = bool(_AADHAR_FRONT_MARKER.search(text_classified_back or ""))
    return bool(has_uidai_f and not has_gov_f and has_gov_b and not has_uidai_b)


def maybe_swap_aadhar_page_indices(page_type_to_idx: dict[str, int], full_ocr_text: str) -> None:
    """Mutates ``page_type_to_idx`` when front/back slots disagree with DOB/gender (front) vs the other (back)."""
    ia = page_type_to_idx.get(PAGE_TYPE_AADHAR)
    ib = page_type_to_idx.get(PAGE_TYPE_AADHAR_BACK)
    if ia is None or ib is None or ia == ib:
        return
    ta = extract_page_text_from_pre_ocr_blocks(full_ocr_text, ia)
    tb = extract_page_text_from_pre_ocr_blocks(full_ocr_text, ib)
    by_dob = should_swap_aadhar_pages_by_dob_gender(ta, tb)
    if by_dob is True or (by_dob is None and should_swap_aadhar_front_back_indices(ta, tb)):
        page_type_to_idx[PAGE_TYPE_AADHAR] = ib
        page_type_to_idx[PAGE_TYPE_AADHAR_BACK] = ia
        reason = "dob_gender" if by_dob is not None else "uidai_govt_markers"
        logger.info(
            "Swapped Aadhaar front/back page indices (pages %s <-> %s) [%s]",
            ia + 1,
            ib + 1,
            reason,
        )
