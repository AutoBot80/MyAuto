"""Screen 3d Save and File Movement — validation vs proceed-without-id."""

from app.services.fill_rto_service import (
    _scrape_application_id_from_dialog_text,
    _screen_3_dialog_is_validation_alert,
    _screen_3_extract_validation_error_messages,
    _screen_3_fail_save_and_file_movement,
    _screen_3_insurance_upto_input_enabled,
    _screen_3_wait_insurance_upto_enabled,
    _screen_3d_dialog_is_proceed_signal,
    _screen_3d_is_entry_details_dialog_text,
)
import pytest


def test_scrape_application_id_from_success_dialog() -> None:
    text = "Application generated successfully\nApplication No. :RJ26072013853306\nOk"
    assert _scrape_application_id_from_dialog_text(text) == "RJ26072013853306"


def test_validation_alert_dialog_detected() -> None:
    text = "Alert!\nClose\nBlank Insurance To Date\nInvalid Relation With Nominee"
    assert _screen_3_dialog_is_validation_alert(text) is True
    errs = _screen_3_extract_validation_error_messages(text)
    assert "Blank Insurance To Date" in errs
    assert "Invalid Relation With Nominee" in errs


def test_success_dialog_not_treated_as_validation_alert() -> None:
    text = "Application No. :RJ26072013853306"
    assert _screen_3_dialog_is_validation_alert(text) is False


def test_fail_save_and_file_movement_raises_with_validation_detail() -> None:
    text = "Alert!\nClose\nBlank Insurance To Date\nInvalid Relation With Nominee"
    with pytest.raises(RuntimeError, match="Blank Insurance To Date"):
        _screen_3_fail_save_and_file_movement(text)


def test_fail_save_and_file_movement_empty_dialog() -> None:
    with pytest.raises(RuntimeError, match="no confirmation dialog"):
        _screen_3_fail_save_and_file_movement("")


def test_numbers_popup_is_proceed_without_parseable_app_id() -> None:
    """Second post-Yes popup with numbers (not a fields-missing alert) = proceed."""
    text = "Information\n12345 67890\nClose"
    assert _screen_3_dialog_is_validation_alert(text) is False
    assert _screen_3_extract_validation_error_messages(text) == []
    assert _screen_3d_dialog_is_proceed_signal(text) is True
    assert _scrape_application_id_from_dialog_text(text) == ""


def test_entry_details_dialog_is_proceed_without_app_id() -> None:
    """Entry Details (Sale Amount / category) after Yes = proceed; no Application No. required."""
    text = (
        "Entry Details\nSale Amount\n72178\nVehicle Category\nTWO WHEELER(NT)\n"
        "Vehicle Class\nM-Cycle/Scooter\nVehicle Type\nNon-Transport\nAre You Sure?"
    )
    assert _screen_3d_is_entry_details_dialog_text(text) is True
    assert _screen_3_dialog_is_validation_alert(text) is False
    assert _screen_3d_dialog_is_proceed_signal(text) is True
    assert _scrape_application_id_from_dialog_text(text) == ""


def test_generated_application_dialog_text_is_proceed_with_scrape() -> None:
    """Third popup after Entry Details — Application No. is scraped; not a validation alert."""
    text = (
        "Generated Application No\n"
        "Application generated successfully. Application No. :RJ26072153884517 "
        "Vehicle Registration No will be Generated from the Series RJ05BE.\nOk"
    )
    assert _screen_3_dialog_is_validation_alert(text) is False
    assert _scrape_application_id_from_dialog_text(text) == "RJ26072153884517"


def test_validation_dialog_is_not_proceed_signal() -> None:
    text = "Alert!\nClose\nBlank Insurance To Date\nInvalid Relation With Nominee"
    assert _screen_3d_dialog_is_proceed_signal(text) is False
    assert _screen_3d_is_entry_details_dialog_text(text) is False


def test_empty_dialog_is_not_proceed_signal() -> None:
    assert _screen_3d_dialog_is_proceed_signal("") is False
    assert _screen_3d_dialog_is_proceed_signal("   ") is False
    assert _screen_3d_is_entry_details_dialog_text("") is False


class _FakePageAlreadyEnabled:
    """Page stub: Insurance Upto already enabled — wait helper returns immediately."""

    def evaluate(self, _script: str) -> bool:
        return True


def test_wait_insurance_upto_enabled_noop_when_already_enabled() -> None:
    page = _FakePageAlreadyEnabled()
    assert _screen_3_insurance_upto_input_enabled(page) is True  # type: ignore[arg-type]
    assert _screen_3_wait_insurance_upto_enabled(page, budget_ms=50) is True  # type: ignore[arg-type]
