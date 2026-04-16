"""Placeholder / sample Indian mobiles must not be treated as customer numbers."""

from app.services.pre_ocr_service import (
    _extract_mobile_from_text,
    _normalize_indian_mobile_hint,
)


def test_normalize_hint_rejects_sample_9876543210() -> None:
    assert _normalize_indian_mobile_hint("9876543210") is None
    assert _normalize_indian_mobile_hint("+91 98765 43210") is None


def test_normalize_hint_accepts_real_mobile() -> None:
    assert _normalize_indian_mobile_hint("9876501234") == "9876501234"


def test_extract_from_text_skips_placeholder_prefers_next() -> None:
    text = "Mobile Number: 9876543210\nCustomer Tel 9876501234"
    assert _extract_mobile_from_text(text) == "9876501234"


def test_extract_from_text_none_when_only_placeholder() -> None:
    assert _extract_mobile_from_text("Mobile Number 9876543210") is None
