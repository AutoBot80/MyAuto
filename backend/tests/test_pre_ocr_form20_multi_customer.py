"""Form 20 cover in multi-customer (alternate-mobile) pre-OCR path."""

from app.services.page_classifier import (
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_DETAILS,
    PAGE_TYPE_UNUSED,
)
from app.services.pre_ocr_service import _build_multi_customer_bundles, _resolve_form20_cover_idx


def _ocr_blocks(*page_texts: str) -> str:
    parts = []
    for i, text in enumerate(page_texts, start=1):
        parts.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(parts)


def test_resolve_form20_from_unused_garbled_ocr() -> None:
    full_text = _ocr_blocks(
        "S/O Test\nAddress: Lane 1\n8640 5066 8836",
        "SALES DETAIL SHEET\nMobile Number: 7240275304\nAlternate No.: 8875734306\n"
        "Frame No. 1\nChassis No. 41132\nEngine No. 00364",
        "Government of India\nDOB 10/03/2001\nMale\n8640 5066 8836",
        "91294 zio 20\n(PRIN 47 aRad)\nHero Motocorp M/S Ltd\n61501",
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR_BACK),
        (1, PAGE_TYPE_DETAILS),
        (2, PAGE_TYPE_AADHAR),
        (3, PAGE_TYPE_UNUSED),
    ]
    idx = _resolve_form20_cover_idx(classifications, full_text, None)
    assert idx == 3


def test_multi_customer_bundle_includes_form20_cover_idx() -> None:
    full_text = _ocr_blocks(
        "S/O Devendra Singh\nAddress: Barakhur\n8640 5066 8836",
        "SALES DETAIL SHEET\nMobile Number: 7240275304\nAlternate No.: 8875734306\n"
        "Devendra Singh\nChassis Number: 41132\nEngine Number: 00364",
        "Government of India\nDevendra Singh\nDOB 10/03/2001\nMale\n8640 5066 8836",
        "91294 zio 20\n(PRIN 47 aRad)\nHero Motocorp M/S Ltd\n61501",
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR_BACK),
        (1, PAGE_TYPE_DETAILS),
        (2, PAGE_TYPE_AADHAR),
        (3, PAGE_TYPE_UNUSED),
    ]
    bundles = _build_multi_customer_bundles(
        pdf_path=__import__("pathlib").Path("scan.jpg"),
        full_ocr_text=full_text,
        classifications=classifications,
        all_mobiles=["7240275304", "8875734306"],
    )
    assert bundles is not None
    assert len(bundles) == 1
    assert bundles[0]["form20_cover_idx"] == 3


def test_details_prefers_sales_sheet_over_form20_misclass_user_upload_order() -> None:
    """Back, Front, Form 20 (Details misclass), Sales PDF — sales sheet must win for details_idx."""
    full_text = _ocr_blocks(
        "S/O Devendra Singh\nAddress: Barakhur, Rajasthan, 321021\n8640 5066 8836",
        "Government of India\nDevendra Singh\nDOB 10/03/2001\nMale\n8640 5066 8836",
        "Form No. 20\nChassis Number: 41132\nEngine Number: 00364\nKey Number: 1965",
        "SALES DETAIL SHEET\nMobile Number: 7240275304\nAlternate No.: 8875734306\n"
        "Devendra Singh\nChassis Number: 41132\nEngine Number: 00364",
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR_BACK),
        (1, PAGE_TYPE_AADHAR),
        (2, PAGE_TYPE_DETAILS),
        (3, PAGE_TYPE_DETAILS),
    ]
    bundles = _build_multi_customer_bundles(
        pdf_path=__import__("pathlib").Path("scan.jpg"),
        full_ocr_text=full_text,
        classifications=classifications,
        all_mobiles=["7240275304", "8875734306"],
    )
    assert bundles is not None
    assert bundles[0]["details_idx"] == 3
    assert bundles[0]["aadhar_front_idx"] == 1
    assert bundles[0]["aadhar_back_idx"] == 0


def test_multi_customer_swaps_aadhar_when_front_back_slots_reversed() -> None:
    full_text = _ocr_blocks(
        "S/O Devendra Singh\nAddress: Barakhur\n8640 5066 8836",
        "Government of India\nDevendra Singh\nDOB 10/03/2001\nMale\n8640 5066 8836",
        "SALES DETAIL SHEET\nMobile Number: 7240275304\nDevendra Singh",
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR),
        (1, PAGE_TYPE_AADHAR_BACK),
        (2, PAGE_TYPE_DETAILS),
    ]
    bundles = _build_multi_customer_bundles(
        pdf_path=__import__("pathlib").Path("scan.jpg"),
        full_ocr_text=full_text,
        classifications=classifications,
        all_mobiles=["7240275304"],
    )
    assert bundles is not None
    assert bundles[0]["aadhar_front_idx"] == 1
    assert bundles[0]["aadhar_back_idx"] == 0
