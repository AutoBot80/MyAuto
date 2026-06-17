"""Reconcile must not demote Aadhar_combined when sibling Aadhar_back is Form 20 mis-tag."""

from app.services.page_classifier import (
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_AADHAR_COMBINED,
    PAGE_TYPE_DETAILS,
)
from app.services.pre_ocr_service import reconcile_aadhar_classifications_multipage


def _ocr_blocks(*page_texts: str) -> str:
    parts = []
    for i, text in enumerate(page_texts, start=1):
        parts.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(parts)


AADHAAR_COMBINED_PAGE = """
Government of India
DOB: 08/04/2003
MALE
7563 1123 6803
Address:
C/O: Irfan, Mandor, Bharatpur,
Rajasthan 321024
"""

# Textract-style garbled body; Form 20 cues at bottom (9828293092 scan).
FORM20_GARBLED_BOTTOM = """
Th's garbled form body text here
Hero Motocorp Ltd.
more garbled instructions
(Pron 47 aRad)
20 OH2 horin
"""


def test_combined_kept_when_sibling_back_is_form20_garbled() -> None:
    full_text = _ocr_blocks(
        AADHAAR_COMBINED_PAGE,
        "sparse page",
        "Sales Detail Sheet\nMobile Number:\n9828293092\nChassis Number:\n57836\nEngine Number:\n58311",
        FORM20_GARBLED_BOTTOM,
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR_COMBINED),
        (1, PAGE_TYPE_AADHAR_BACK),
        (2, PAGE_TYPE_DETAILS),
        (3, PAGE_TYPE_AADHAR_BACK),
    ]
    out = reconcile_aadhar_classifications_multipage(classifications, full_text)
    by_idx = {i: p for i, p in out}
    assert by_idx[0] == PAGE_TYPE_AADHAR_COMBINED


def test_combined_demoted_when_sibling_back_is_credible_aadhaar() -> None:
    full_text = _ocr_blocks(
        AADHAAR_COMBINED_PAGE,
        "7563 1123 6803\nAddress:\nS/O Irfan, Bharatpur",
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR_COMBINED),
        (1, PAGE_TYPE_AADHAR_BACK),
    ]
    out = reconcile_aadhar_classifications_multipage(classifications, full_text)
    by_idx = {i: p for i, p in out}
    assert by_idx[0] == PAGE_TYPE_AADHAR
    assert by_idx[1] == PAGE_TYPE_AADHAR_BACK
