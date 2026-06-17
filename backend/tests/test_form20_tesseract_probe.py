"""Hindi Tesseract Form 20 probe reclassifies mis-tagged pages."""

from unittest.mock import patch

from PIL import Image

from app.services.page_classifier import (
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_FORM_20_COVER,
    PAGE_TYPE_UNUSED,
)
from app.services.pre_ocr_service import classify_pages_with_form20_tesseract


def _ocr_blocks(*page_texts: str) -> str:
    parts = []
    for i, text in enumerate(page_texts, start=1):
        parts.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(parts)


HINDI_HEADER = "प्रारूप सं. 20\n(नियम 47 देखिये)\nमोटरयान के रजिस्ट्रीकरण के लिए"


def test_probe_reclassifies_aadhar_back_to_form20() -> None:
    full_text = _ocr_blocks(
        "Government of India\nDOB: 01/01/1990\nMale",
        "garbled textract only\nS/O someone",
    )
    img = Image.new("RGB", (400, 600), color=(255, 255, 255))
    page_images = {0: img, 1: img}

    with patch(
        "app.services.pre_ocr_service._tesseract_form20_probe",
        return_value=HINDI_HEADER,
    ):
        out = classify_pages_with_form20_tesseract(full_text, page_images)

    by_idx = {i: p for i, p in out}
    assert by_idx[1] == PAGE_TYPE_FORM_20_COVER
    assert by_idx[0] != PAGE_TYPE_FORM_20_COVER


def test_probe_not_run_when_page_unused_without_images() -> None:
    full_text = _ocr_blocks("random noise only")
    out = classify_pages_with_form20_tesseract(full_text, None)
    assert out == [(0, PAGE_TYPE_UNUSED)]
