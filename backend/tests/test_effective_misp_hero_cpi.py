"""MISP Hero CPI: effective_cpi_reqd gates dealer hero_cpi for proposal fill."""

from unittest.mock import patch

from app.services.insurance_form_values import (
    build_insurance_fill_values,
    effective_misp_hero_cpi,
)


def test_effective_misp_hero_cpi_truth_table():
    assert effective_misp_hero_cpi(effective_cpi_reqd="N", dealer_hero_cpi="Y") == "N"
    assert effective_misp_hero_cpi(effective_cpi_reqd="N", dealer_hero_cpi="N") == "N"
    assert effective_misp_hero_cpi(effective_cpi_reqd="Y", dealer_hero_cpi="Y") == "Y"
    assert effective_misp_hero_cpi(effective_cpi_reqd="Y", dealer_hero_cpi="N") == "N"


@patch("app.services.insurance_form_values.load_latest_insurance_values")
@patch("app.services.insurance_form_values._apply_staging_insurance_overlay")
@patch("app.services.insurance_form_values.read_insurance_insurer_from_ocr_json", return_value="")
def test_build_insurance_fill_values_applies_cpi_reqd_override(
    _ocr,
    _overlay,
    mock_load,
):
    mock_load.return_value = {
        "insurer": "HDFC ERGO",
        "mobile_number": "9876543210",
        "customer_name": "Test User",
        "frame_no": "FRAME123",
        "engine_no": "ENG456",
        "hero_cpi": "Y",
        "insurance_pay": "APD",
    }
    values = build_insurance_fill_values(
        1,
        2,
        "9999999999_010126",
        effective_cpi_reqd="N",
    )
    assert values["hero_cpi_dealer"] == "Y"
    assert values["effective_cpi_reqd"] == "N"
    assert values["hero_cpi"] == "N"

    values_y = build_insurance_fill_values(
        1,
        2,
        "9999999999_010126",
        effective_cpi_reqd="Y",
    )
    assert values_y["hero_cpi"] == "Y"


@patch("app.services.insurance_form_values.fetch_effective_cpi_reqd", return_value="N")
@patch("app.services.insurance_form_values.load_latest_insurance_values")
@patch("app.services.insurance_form_values._apply_staging_insurance_overlay")
@patch("app.services.insurance_form_values.read_insurance_insurer_from_ocr_json", return_value="")
def test_build_insurance_fill_values_resolves_staging_cpi_reqd(
    _ocr,
    _overlay,
    mock_load,
    mock_fetch,
):
    mock_load.return_value = {
        "insurer": "HDFC ERGO",
        "mobile_number": "9876543210",
        "customer_name": "Test User",
        "frame_no": "FRAME123",
        "engine_no": "ENG456",
        "hero_cpi": "Y",
        "insurance_pay": "APD",
    }
    build_insurance_fill_values(
        1,
        2,
        "9999999999_010126",
        staging_id="00000000-0000-0000-0000-000000000001",
        dealer_id=100001,
    )
    mock_fetch.assert_called_once_with(
        staging_id="00000000-0000-0000-0000-000000000001",
        dealer_id=100001,
    )
