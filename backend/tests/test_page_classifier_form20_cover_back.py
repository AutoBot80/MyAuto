"""Form 20 cover back page detection (inspection certificate Hindi block)."""

from app.services.page_classifier import form20_cover_back_detected, form20_cover_detected


def test_exact_hindi_inspection_certificate_header() -> None:
    text = "मोटरयान के निरीक्षण का प्रमाण पत्र"
    assert form20_cover_back_detected(text)
    assert not form20_cover_detected(text)


def test_garbled_tesseract_sample_ocr() -> None:
    text = "मोटरयान ब्हे निरीक्षण वा प्रमाण पतन"
    assert form20_cover_back_detected(text)


def test_token_fallback_when_strong_pattern_absent() -> None:
    text = "टिप्पणी मोटर यान ं\nनिरीक्षण प्राधिकारी\nप्रमाणित किया जाता है"
    assert form20_cover_back_detected(text)


def test_unrelated_hindi_does_not_match() -> None:
    text = "प्रमाणित किया जाता है कि आवेदन में अन्तर्विष्ट विशिष्टर्यो सही"
    assert not form20_cover_back_detected(text)
