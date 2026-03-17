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
PAGE_TYPE_DETAILS = "Details"
PAGE_TYPE_INSURANCE = "Insurance"
PAGE_TYPE_UNUSED = "unused"

# Output filename for each type (excluding unused, which goes to unused.pdf)
PAGE_TYPE_TO_FILENAME = {
    PAGE_TYPE_AADHAR: "Aadhar.jpg",
    PAGE_TYPE_AADHAR_BACK: "Aadhar_back.jpg",
    PAGE_TYPE_DETAILS: "Details.jpg",
    PAGE_TYPE_INSURANCE: "Insurance.jpg",
}

# Patterns to identify each page type. Order matters: more specific first.
# Details: vehicle/customer sheet with Frame No, Chassis, Key No, etc.
_DETAILS_PATTERNS = [
    re.compile(r"frame\s*no\.?\s*[:]?", re.IGNORECASE),
    re.compile(r"chassis\s*(?:no\.?)?\s*[:]?", re.IGNORECASE),
    re.compile(r"engine\s*no\.?\s*[:]?", re.IGNORECASE),
    re.compile(r"key\s*no\.?\s*[:]?", re.IGNORECASE),
    re.compile(r"battery\s*no\.?\s*[:]?", re.IGNORECASE),
    re.compile(r"model\s*(?:&|and)\s*colour", re.IGNORECASE),
    re.compile(r"nominee\s*name", re.IGNORECASE),
    re.compile(r"profession\s*[:]?", re.IGNORECASE),
    re.compile(r"tel\.?\s*name\s*no\.?", re.IGNORECASE),
    re.compile(r"buyer['\u2019]?s?\s*order", re.IGNORECASE),
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

# Aadhar back: "Address of the cardholder", instructions
_AADHAR_BACK_PATTERNS = [
    re.compile(r"address\s+of\s+the\s+cardholder", re.IGNORECASE),
    re.compile(r"address\s+is\s+as\s+per\s+(?:the\s+)?uidai", re.IGNORECASE),
    re.compile(r"your\s+aadha?ar", re.IGNORECASE),
]

# Aadhar front: Government of India, Unique Identification, main Aadhaar text
_AADHAR_FRONT_PATTERNS = [
    re.compile(r"unique\s+identification\s+authority\s+of\s+india", re.IGNORECASE),
    re.compile(r"government\s+of\s+india", re.IGNORECASE),
    re.compile(r"\b(?:aadha?ar|aadhar|aadhaar)\b", re.IGNORECASE),
    re.compile(r"\buidai\b", re.IGNORECASE),
    re.compile(r"\b\d{4}\s+\d{4}\s+\d{4}\b"),  # 12-digit Aadhar format
    re.compile(r"(?:male|female)\s*/\s*(?:m|f)\b", re.IGNORECASE),  # Gender on Aadhar
    re.compile(r"date\s+of\s+birth|year\s+of\s+birth|d\.?o\.?b\.?", re.IGNORECASE),
    re.compile(r"care\s+of\s*[:]?", re.IGNORECASE),  # Care of field
]


def classify_page_by_text(text: str) -> str:
    """
    Classify a single page from its OCR text.
    Returns one of: Aadhar, Aadhar_back, Details, Insurance, unused.
    """
    if not text or not isinstance(text, str):
        return PAGE_TYPE_UNUSED

    t = text.strip()
    if len(t) < 20:
        return PAGE_TYPE_UNUSED

    # Score each type by number of pattern matches
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

    # Pick best match; require at least 1 hit
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
