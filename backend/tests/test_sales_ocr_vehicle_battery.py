"""Vehicle OCR helpers on the sales detail sheet path."""

from app.services.sales_ocr_service import _normalize_dedupe_battery_no_ocr


def test_battery_dedupe_concatenated_reading():
    assert _normalize_dedupe_battery_no_ocr("M7A6N415257 M7A6N 415257") == "M7A6N415257"


def test_battery_dedupe_no_false_positive_short():
    raw = "AB12CD34EF"
    assert _normalize_dedupe_battery_no_ocr(raw) == raw
