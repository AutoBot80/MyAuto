"""Aadhaar front DOB and back address parsing from noisy Textract OCR."""

from app.services.sales_ocr_service import (
    _parse_aadhar_back_address_from_ocr,
    _parse_aadhar_front_textract_fallback,
)

FRONT_SNIPPET = """Male / bah
10/03/2011 BOO / OU HPF
Devendra Singh"""

BACK_SNIPPET = """Address S/O. Mormukat Singh, Barakhur,
221282, жауг. RTG Bharatpur, Barakhur, Rajasthan 321021.
321021
8640 5066 8836"""

BACK_WITH_COLON = """Address: S/O: Madan Lal, Village X, Bharatpur, Rajasthan, 321001
8640 5066 1234"""


def test_dob_within_two_lines_after_gender():
    got = _parse_aadhar_front_textract_fallback(FRONT_SNIPPET)
    assert got.get("date_of_birth") == "10/03/2011"
    assert got.get("year_of_birth") == "2011"


def test_dob_before_gender_still_works():
    text = "DOB: 01/05/1990\nMale\nRamesh Kumar"
    got = _parse_aadhar_front_textract_fallback(text)
    assert got.get("date_of_birth") == "01/05/1990"


def test_back_address_address_so_no_colon():
    got = _parse_aadhar_back_address_from_ocr(BACK_SNIPPET, name_hint="Devendra Singh")
    assert got.get("care_of") == "S/O Mormukat Singh"
    assert got.get("pin_code") == "321021"
    assert got.get("state") == "Rajasthan"
    assert got.get("address")
    assert "Bharatpur" in got["address"]


def test_back_address_colon_format_regression():
    got = _parse_aadhar_back_address_from_ocr(BACK_WITH_COLON)
    assert got.get("care_of") == "S/O Madan Lal"
    assert got.get("pin_code") == "321001"
    assert got.get("state") == "Rajasthan"
