"""Aadhaar_combined DDT must not populate aadhar_front / aadhar_back prefetch (split images need their own DDT)."""

from app.services.pre_ocr_service import _build_ddt_prefetch
from app.services.page_classifier import (
    PAGE_TYPE_AADHAR,
    PAGE_TYPE_AADHAR_BACK,
    PAGE_TYPE_AADHAR_COMBINED,
    PAGE_TYPE_INSURANCE,
)

sample_ddt = {"error": None, "full_text": "back only"}


def test_aadhaar_combined_omits_aadhar_prefetch() -> None:
    out = _build_ddt_prefetch(
        [(0, PAGE_TYPE_AADHAR_COMBINED)],
        {0: sample_ddt},
    )
    assert "aadhar_front" not in out
    assert "aadhar_back" not in out


def test_separate_front_back_still_prefetches() -> None:
    out = _build_ddt_prefetch(
        [
            (0, PAGE_TYPE_AADHAR),
            (1, PAGE_TYPE_AADHAR_BACK),
        ],
        {0: sample_ddt, 1: sample_ddt},
    )
    assert out.get("aadhar_front") is sample_ddt
    assert out.get("aadhar_back") is sample_ddt


def test_insurance_prefetch_unaffected_by_combined_skip() -> None:
    out = _build_ddt_prefetch(
        [
            (0, PAGE_TYPE_AADHAR_COMBINED),
            (1, PAGE_TYPE_INSURANCE),
        ],
        {0: sample_ddt, 1: sample_ddt},
    )
    assert "aadhar_front" not in out
    assert out.get("insurance") is sample_ddt
