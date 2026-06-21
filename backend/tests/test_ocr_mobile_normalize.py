"""OCR mobile character normalization for Indian 10-digit numbers."""

from app.ocr_mobile_normalize import normalize_ocr_mobile_chars, parse_indian_mobile_from_ocr
from app.services.pre_ocr_service import _extract_mobile_from_text, _normalize_indian_mobile_hint
from app.services.sales_ocr_service import _map_key_value_pairs_to_details_customer


def test_parse_slash_as_one() -> None:
    assert parse_indian_mobile_from_ocr("96/03/0000") == "9610310000"


def test_parse_o_as_zero() -> None:
    assert parse_indian_mobile_from_ocr("961O31OO1O") == "9610310010"


def test_parse_rejects_placeholder() -> None:
    assert parse_indian_mobile_from_ocr("9876543210") is None
    assert parse_indian_mobile_from_ocr("9876501234") is None


def test_normalize_ocr_mobile_chars() -> None:
    assert normalize_ocr_mobile_chars("96/03/O000") == "9610310000"


def test_extract_prefers_customer_over_dealer_fallback() -> None:
    text = "Mobile Number: 96/03/0000\nDealer Tel 9314340211"
    assert _extract_mobile_from_text(text) == "9610310000"


def test_normalize_hint_with_ocr_noise() -> None:
    assert _normalize_indian_mobile_hint("96/03/0000") == "9610310000"


def test_textract_kv_mapping_with_garbled_mobile() -> None:
    pairs = [{"key": "Mobile Number", "value": "96/03/0000"}]
    out = _map_key_value_pairs_to_details_customer(pairs)
    assert out.get("mobile_number") == "9610310000"


def test_textract_kv_alt_phone_ocr_noise() -> None:
    pairs = [{"key": "Alternate No", "value": "701654321O"}]
    out = _map_key_value_pairs_to_details_customer(pairs)
    assert out.get("alt_phone_num") == "7016543210"
