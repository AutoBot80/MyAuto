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

# Fallback scoring only (not used for front vs back — see :func:`aadhar_front_face_ocr`).
# **Front** is defined only by DOB + Male/Female cues; **back** is inferred when not front.
_AADHAR_BACK_PATTERNS = [
    re.compile(r"address\s+of\s+the\s+cardholder", re.IGNORECASE),
    re.compile(r"address\s+is\s+as\s+per", re.IGNORECASE),
    re.compile(r"पता", re.IGNORECASE),
]

_AADHAR_FRONT_PATTERNS = [
    re.compile(r"government\s+of\s+india", re.IGNORECASE),
    re.compile(r"unique\s+identification\s+authority\s+of\s+india", re.IGNORECASE),
    re.compile(r"\b\d{4}\s+\d{4}\s+\d{4}\b"),
    re.compile(r"(?:male|female)\s*/\s*(?:m|f)\b", re.IGNORECASE),
    re.compile(r"date\s+of\s+birth|year\s+of\s+birth|d\.?o\.?b\.?", re.IGNORECASE),
    re.compile(r"care\s+of\s*[:]?", re.IGNORECASE),
]

# Optional: same-page folded card (front+back OCR in one page) — DOB row + long text + address/authority cues
_AADHAR_COMBINED_EXTRA = re.compile(
    r"(?i)(?:address|cardholder|पता|विशेष\s*पहचान|unique\s+identification)",
)


def aadhar_front_face_ocr(text: str) -> bool:
    """
    **Only** governing rule for the Aadhaar **photo front**: OCR must show both a **DOB** cue and **Male/Female**
    (English/Hindi, any common casing). Matches dealer cards like ``जन्म तिथि / DOB :`` and ``पुरुष / Male``.
    """
    t = text or ""
    if not t.strip():
        return False
    has_dob = bool(
        re.search(
            r"(?i)(?:\bD\s*O\s*B\b|\bDOB\b|d\.?\s*o\.?\s*b\.?|"
            r"date\s+of\s+birth|year\s+of\s+birth|जन्म\s*तिथि)",
            t,
        )
    )
    has_gender = bool(
        re.search(
            r"(?i)(?:\b(?:male|female)\b|पुरुष|स्त्री|महिला)",
            t,
        )
    )
    return has_dob and has_gender


def _aadhaar_combined_single_page_candidate(t: str) -> bool:
    """Folded / one-page scan with both faces: front row + extra back-like body text."""
    if not aadhar_front_face_ocr(t) or len(t) < 400:
        return False
    return bool(_AADHAR_COMBINED_EXTRA.search(t))


def classify_page_by_text(text: str) -> str:
    """
    Classify a single page from its OCR text.

    **Aadhaar photo front** is determined **only** by :func:`aadhar_front_face_ocr` (DOB + Male/Female cues).
    **Aadhaar back** is a non-front Aadhaar-like page (fallback scoring). **uidai.gov.in** is not used for front/back.
    """
    if not text or not isinstance(text, str):
        return PAGE_TYPE_UNUSED

    t = text.strip()
    if len(t) < 20:
        return PAGE_TYPE_UNUSED

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

    if scores[PAGE_TYPE_DETAILS] >= 2:
        return PAGE_TYPE_DETAILS
    if scores[PAGE_TYPE_INSURANCE] >= 2:
        return PAGE_TYPE_INSURANCE

    if _aadhaar_combined_single_page_candidate(t):
        return PAGE_TYPE_AADHAR_COMBINED
    if aadhar_front_face_ocr(t):
        return PAGE_TYPE_AADHAR

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


def should_swap_aadhar_pages_by_dob_gender(text_at_front_slot: str, text_at_back_slot: str) -> bool | None:
    """
    Decide if front/back **slots** should be swapped using **only** :func:`aadhar_front_face_ocr` (DOB + gender).

    Returns:
        ``False`` — keep order (first slot is the photo front).
        ``True`` — swap (second slot is the photo front).
        ``None`` — both or neither show DOB+gender; do **not** use uidai/URL fallbacks.
    """
    f_ok = aadhar_front_face_ocr(text_at_front_slot)
    b_ok = aadhar_front_face_ocr(text_at_back_slot)
    if f_ok and not b_ok:
        return False
    if b_ok and not f_ok:
        return True
    return None


def maybe_swap_aadhar_page_indices(page_type_to_idx: dict[str, int], full_ocr_text: str) -> None:
    """Mutates ``page_type_to_idx`` when slots disagree with DOB+Male/Female = front (see :func:`aadhar_front_face_ocr`)."""
    ia = page_type_to_idx.get(PAGE_TYPE_AADHAR)
    ib = page_type_to_idx.get(PAGE_TYPE_AADHAR_BACK)
    if ia is None or ib is None or ia == ib:
        return
    ta = extract_page_text_from_pre_ocr_blocks(full_ocr_text, ia)
    tb = extract_page_text_from_pre_ocr_blocks(full_ocr_text, ib)

    by_face = should_swap_aadhar_pages_by_dob_gender(ta, tb)
    if by_face is True:
        page_type_to_idx[PAGE_TYPE_AADHAR] = ib
        page_type_to_idx[PAGE_TYPE_AADHAR_BACK] = ia
        logger.info(
            "Swapped Aadhaar front/back page indices (pages %s <-> %s) [dob_gender_front]",
            ia + 1,
            ib + 1,
        )
