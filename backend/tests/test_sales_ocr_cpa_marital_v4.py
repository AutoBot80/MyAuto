"""Sales Detail Sheet v4: marital Y/N and CPA Required OCR normalizers and extraction."""

from app.services.sales_ocr_service import (
    _canonical_marital_status_from_text,
    _map_key_value_pairs_to_insurance,
    _normalize_cpa_required_value,
    _normalize_details_marital_status_value,
    _parse_cpa_required_from_ocr,
    _parse_insurance_from_full_text,
    _parse_sales_detail_checkbox_from_tables,
    _parse_sales_detail_checkbox_regions,
    _sanitize_details_financier_value,
)


def test_sanitize_financier_rejects_label_fragment():
    assert _sanitize_details_financier_value("Name (If Hypo):") is None
    assert _sanitize_details_financier_value("Financier Name (if Hypo):") is None
    assert _sanitize_details_financier_value("Name of Financier") is None
    assert _sanitize_details_financier_value("CPA Required (Yes/ No)?") is None


def test_sanitize_financier_keeps_real_name():
    assert _sanitize_details_financier_value("HDFC Bank") == "HDFC Bank"
    assert _sanitize_details_financier_value("Bajaj Finance") == "Bajaj Finance"


def test_map_key_value_pairs_to_insurance_rejects_financier_label_bleed():
    out = _map_key_value_pairs_to_insurance([{"key": "Financier", "value": "Name (If Hypo):"}])
    assert "financier" not in out


def test_parse_insurance_from_full_text_blank_hypo_financier_omitted():
    text = """SALES DETAIL SHEET
Payment Mode and Insurance Details
Payment:
Cash
Financier Name (if Hypo):
CPA Required (Yes/ No)?
"""
    out = _parse_insurance_from_full_text(text)
    assert "financier" not in out


def test_parse_insurance_from_full_text_keeps_real_financier():
    text = """SALES DETAIL SHEET
Financier Name: HDFC Bank
CPA Required (Yes/ No)? NO
"""
    out = _parse_insurance_from_full_text(text)
    assert out.get("financier") == "HDFC Bank"


def test_marital_yes_no_maps_to_married_single():
    assert _normalize_details_marital_status_value("Y") == "Married"
    assert _normalize_details_marital_status_value("Yes") == "Married"
    assert _normalize_details_marital_status_value("N") == "Single"
    assert _normalize_details_marital_status_value("No") == "Single"


def test_marital_blank_defaults_married():
    assert _normalize_details_marital_status_value("") == "Married"
    assert _normalize_details_marital_status_value(None) == "Married"


def test_cpa_required_yes_no():
    assert _normalize_cpa_required_value("Y") == "Y"
    assert _normalize_cpa_required_value("Yes") == "Y"
    assert _normalize_cpa_required_value("N") == "N"
    assert _normalize_cpa_required_value("No") == "N"


def test_cpa_required_blank_defaults_no():
    assert _normalize_cpa_required_value("") == "N"
    assert _normalize_cpa_required_value(None) == "N"


def test_cpa_required_from_ocr_blank_or_garbage_returns_none():
    assert _parse_cpa_required_from_ocr("") is None
    assert _parse_cpa_required_from_ocr(None) is None
    assert _parse_cpa_required_from_ocr("   ") is None
    assert _parse_cpa_required_from_ocr("maybe") is None


def test_cpa_required_from_ocr_explicit_yes_no():
    assert _parse_cpa_required_from_ocr("Y") == "Y"
    assert _parse_cpa_required_from_ocr("Yes") == "Y"
    assert _parse_cpa_required_from_ocr("N") == "N"
    assert _parse_cpa_required_from_ocr("No") == "N"
    assert _parse_cpa_required_from_ocr("CPA Required (Yes/ No)? YES") == "Y"
    assert _parse_cpa_required_from_ocr("CPA Required (Yes/ No)? NO") == "N"


def test_parse_insurance_from_full_text_cpa_blank_omitted():
    text = """SALES DETAIL SHEET
Profession: Job
Married (Yes/ No)? YES
Vehicle Details
Model: AKO+
CPA Required (Yes/ No)?
"""
    out = _parse_insurance_from_full_text(text)
    assert "cpa_reqd" not in out


def test_parse_insurance_from_full_text_cpa_garbage_omitted():
    text = """SALES DETAIL SHEET
CPA Required (Yes/ No)? maybe
"""
    out = _parse_insurance_from_full_text(text)
    assert "cpa_reqd" not in out


def test_v4_marital_line_strips_label_prefix():
    assert _canonical_marital_status_from_text("Married (Yes/ No)? YES") == "Married"
    assert _canonical_marital_status_from_text("Married (Yes/No)? NO") == "Single"


def test_v4_cpa_line_strips_label_prefix():
    assert _normalize_cpa_required_value("CPA Required (Yes/ No)? YES") == "Y"
    assert _normalize_cpa_required_value("CPA Required (Yes/ No)? NO") == "N"


def test_parse_insurance_from_full_text_v4_married_yes():
    text = """SALES DETAIL SHEET
Profession: Job
Married (Yes/ No)? YES
Vehicle Details
Model: AKO+
CPA Required (Yes/ No)? NO
"""
    out = _parse_insurance_from_full_text(text)
    assert out.get("marital_status") == "Married"
    assert out.get("cpa_reqd") == "N"


def test_parse_sales_detail_checkbox_regions_v4_marital():
    text = "SALES DETAIL SHEET\nMarried (Yes/ No)? YES\nVehicle Details"
    out = _parse_sales_detail_checkbox_regions(text)
    assert out.get("marital_status") == "Married"


def test_parse_sales_detail_checkbox_from_tables_v4_cpa():
    tables = [[["CPA Required (Yes/ No)?", "YES"]]]
    out = _parse_sales_detail_checkbox_from_tables(tables)
    assert out.get("cpa_reqd") == "Y"
