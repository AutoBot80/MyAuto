"""Regression tests for Aadhaar combined-page classification heuristics."""

from app.services.page_classifier import (
    PAGE_TYPE_AADHAR_COMBINED,
    PAGE_TYPE_AADHAR_BACK,
    classify_page_by_text,
)


def test_combined_detected_without_length_gate() -> None:
    text = """
    Pandit Upendra Kumar Sharma
    DOB: 04/03/1990
    MALE
    5612 6990 5800
    Download Date: 08/03/2021
    Address:
    S/O Rajendra Prasad, Bharatpur
    Rajasthan - 321203
    """
    assert len(text.strip()) < 400
    assert classify_page_by_text(text) == PAGE_TYPE_AADHAR_COMBINED


def test_back_only_stays_back_not_combined() -> None:
    text = """
    5612 6990 5800
    Download Date: 08/03/2021
    Address:
    S/O Rajendra Prasad, Bharatpur
    Rajasthan - 321203
    """
    assert classify_page_by_text(text) == PAGE_TYPE_AADHAR_BACK
