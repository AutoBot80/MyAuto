"""OCR character correction for Indian mobile numbers (handwritten / Textract noise)."""

from __future__ import annotations

import re

from app.placeholder_mobile import is_placeholder_indian_mobile

OCR_MOBILE_CHAR_MAP = str.maketrans({
    "/": "1",
    "O": "0",
    "o": "0",
    "Q": "0",
    "l": "1",
    "I": "1",
    "|": "1",
    "S": "5",
    "s": "5",
    "B": "8",
    "Z": "2",
    "z": "2",
})

_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_ocr_mobile_chars(raw: str) -> str:
    """Map common OCR confusables to digits before extracting a mobile token."""
    return str(raw or "").translate(OCR_MOBILE_CHAR_MAP)


def parse_indian_mobile_from_ocr(raw: str) -> str | None:
    """
    Normalize OCR-noisy mobile text to a valid 10-digit Indian mobile, or None.

    Handles ``+91``, spaces, dashes, and confusables like ``/``→``1``, ``O``→``0``.
    """
    if not raw or not str(raw).strip():
        return None
    normalized = normalize_ocr_mobile_chars(str(raw))
    digits = "".join(c for c in normalized if c.isdigit())
    if len(digits) < 10:
        return None
    ten = digits[-10:]
    if not _INDIAN_MOBILE_RE.match(ten):
        return None
    if is_placeholder_indian_mobile(ten):
        return None
    return ten
