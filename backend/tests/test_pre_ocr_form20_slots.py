"""Form 20 cover slot assignment and orphan Details rescue."""

from app.services.page_classifier import (
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_DETAILS,
    PAGE_TYPE_FORM_20_COVER,
)
from app.services.pre_ocr_service import assign_classified_page_slots


def _ocr_blocks(*page_texts: str) -> str:
    parts = []
    for i, text in enumerate(page_texts, start=1):
        parts.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(parts)


def test_orphan_details_rescued_as_form20_cover() -> None:
    full_text = _ocr_blocks(
        "Sales Detail Sheet\nFrame No. 1\nChassis No. 2\nEngine No. 3\nKey No. 4",
        "Government of India\nDOB: 01/01/1990\nMale",
        "S/O Test\nAddress: Lane 1",
        "Form No. 20 (See Rule 47)\nApplication for registration of motor vehicle\n"
        "Chassis No. 6158\nEngine No. ABC",
    )
    classifications = [
        (0, PAGE_TYPE_DETAILS),
        (1, PAGE_TYPE_AADHAR),
        (2, PAGE_TYPE_AADHAR_BACK),
        (3, PAGE_TYPE_DETAILS),
    ]
    slots, unused = assign_classified_page_slots(classifications, full_text, None)
    assert slots[PAGE_TYPE_DETAILS] == 0
    assert slots[PAGE_TYPE_FORM_20_COVER] == 3
    assert unused == []


def test_orphan_details_without_form20_goes_to_unused() -> None:
    full_text = _ocr_blocks(
        "Sales Detail Sheet\nFrame No. 1\nChassis No. 2\nEngine No. 3",
        "Extra sheet\nFrame No. 9\nChassis No. 8\nEngine No. 7",
    )
    classifications = [(0, PAGE_TYPE_DETAILS), (1, PAGE_TYPE_DETAILS)]
    slots, unused = assign_classified_page_slots(classifications, full_text, None)
    assert slots[PAGE_TYPE_DETAILS] == 0
    assert PAGE_TYPE_FORM_20_COVER not in slots
    assert unused == [1]


def test_duplicate_insurance_goes_to_unused() -> None:
    full_text = _ocr_blocks("Gross Premium\nPolicy No. 1\nOD policy period", "Gross Premium\nPolicy No. 2")
    from app.services.page_classifier import PAGE_TYPE_INSURANCE

    classifications = [(0, PAGE_TYPE_INSURANCE), (1, PAGE_TYPE_INSURANCE)]
    slots, unused = assign_classified_page_slots(classifications, full_text, None)
    assert slots[PAGE_TYPE_INSURANCE] == 0
    assert unused == [1]


def test_aadhar_back_rescued_as_form20_when_bottom_cues_present() -> None:
    garbled_top = "\n".join(f"noise {i}" for i in range(30))
    form20_page = (
        f"{garbled_top}\nHero Motocorp Ltd.\n(Pron 47 aRad)\n20 OH2 horin\n"
    )
    full_text = _ocr_blocks(
        "Government of India\nDOB: 01/01/1990\nMale\nAddress:\nC/O Test",
        form20_page,
        "Sales Detail Sheet\nMobile Number:\n9999999999\nChassis No. 1\nEngine No. 2",
    )
    classifications = [
        (0, PAGE_TYPE_AADHAR),
        (1, PAGE_TYPE_AADHAR_BACK),
        (2, PAGE_TYPE_DETAILS),
    ]
    slots, unused = assign_classified_page_slots(classifications, full_text, None)
    assert slots[PAGE_TYPE_FORM_20_COVER] == 1
    assert slots[PAGE_TYPE_DETAILS] == 2
    assert unused == []
