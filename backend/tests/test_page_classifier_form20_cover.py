"""Form 20 cover page classification (before Sales Detail Sheet false positives)."""

from app.services.page_classifier import (
    PAGE_TYPE_DETAILS,
    PAGE_TYPE_FORM_20_COVER,
    classify_page_by_text,
    form20_cover_detected_in_top,
)


def test_form20_cover_beats_details_when_chassis_present() -> None:
    text = (
        "Form No. 20 (See Rule 47)\n"
        "Application for registration of motor vehicle\n"
        "Chassis No. 6158\n"
        "Engine No. ABC123\n"
        "Frame No. XYZ\n"
    )
    assert form20_cover_detected_in_top(text)
    assert classify_page_by_text(text) == PAGE_TYPE_FORM_20_COVER


def test_sales_detail_without_form20_header_stays_details() -> None:
    text = (
        "Sales Detail Sheet\n"
        "Frame No. 123\n"
        "Chassis No. 456\n"
        "Engine No. 789\n"
        "Key No. 1\n"
    )
    assert not form20_cover_detected_in_top(text)
    assert classify_page_by_text(text) == PAGE_TYPE_DETAILS


def test_hindi_form20_header() -> None:
    text = "प्रारूप सं. 20\n(नियम 47 देखिये)\nमोटरयान के रजिस्ट्रीकरण के लिए\nChassis No. 6158"
    assert form20_cover_detected_in_top(text)
    assert classify_page_by_text(text) == PAGE_TYPE_FORM_20_COVER


def test_noisy_hindi_form20_ocr_variants() -> None:
    noisy = "प्रारूप सं० 20\nनियम 47\nChassis No. 6158\nEngine No. X"
    assert form20_cover_detected_in_top(noisy)
    assert classify_page_by_text(noisy) == PAGE_TYPE_FORM_20_COVER

    english_noisy = "FORM 20\n(Rule 47)\nChassis No. 6158\nFrame No. XYZ"
    assert form20_cover_detected_in_top(english_noisy)
    assert classify_page_by_text(english_noisy) == PAGE_TYPE_FORM_20_COVER


def test_details_only_chassis_stays_details_without_form20_header() -> None:
    text = "Sales Detail Sheet\nFrame No. 123\nChassis No. 456\nEngine No. 789"
    assert not form20_cover_detected_in_top(text)
    assert classify_page_by_text(text) == PAGE_TYPE_DETAILS


def test_garbled_textract_form20_header() -> None:
    """Real-world Textract noise from consolidated scan (zio 20 / PRIN 47)."""
    text = (
        "91294 zio 20\n"
        "(PRIN 47 aRad)\n"
        "Hero Motocorp M/S Ltd\n"
        "61501\n"
    )
    assert form20_cover_detected_in_top(text)
    assert classify_page_by_text(text) == PAGE_TYPE_FORM_20_COVER
