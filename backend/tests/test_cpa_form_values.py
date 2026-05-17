"""Unit tests for CPA form value overlay (no DB)."""

from app.services.cpa_form_values import (
    CPA_PLAN_TOTAL_AMOUNT_DEFAULT,
    _apply_staging_cpa_overlay,
    _resolve_subfolder_for_cpa,
    cpa_fill_values_to_alliance_payload,
)


def test_apply_staging_cpa_overlay_fills_empty_view_fields():
    values = {
        "customer_name": "Committed Name",
        "mobile_number": "",
        "full_chassis": "",
        "model": "",
        "nominee_name": "",
    }
    staging = {
        "customer": {"mobile": "9876543210", "gender": "Male"},
        "vehicle": {"full_chassis": "ME4JF4850K0123456", "model": "Splendor+"},
        "insurance": {"nominee_name": "Ravi Kumar", "nominee_age": "32"},
    }
    _apply_staging_cpa_overlay(values, staging)
    assert values["customer_name"] == "Committed Name"
    assert values["mobile_number"] == "9876543210"
    assert values["gender"] == "Male"
    assert values["full_chassis"] == "ME4JF4850K0123456"
    assert values["frame_no"] == "ME4JF4850K0123456"
    assert values["model"] == "Splendor+"
    assert values["nominee_name"] == "Ravi Kumar"
    assert values["nominee_age"] == "32"


def test_resolve_subfolder_for_cpa_ignores_default_placeholder():
    out = _resolve_subfolder_for_cpa(
        "default",
        None,
        100001,
        {"file_location": "Sale-123"},
    )
    assert out == "Sale-123"


def test_cpa_fill_values_to_alliance_payload_maps_mobile_and_defaults():
    payload = cpa_fill_values_to_alliance_payload(
        {
            "customer_name": "A B",
            "mobile_number": "9988776655",
            "full_chassis": "CH123",
            "full_engine": "EN456",
            "vehicle_type": "New",
            "client_type": "Individual",
        }
    )
    assert payload["mobile"] == "9988776655"
    assert payload["frame_no"] == "CH123"
    assert payload["plan_total_amount"] == CPA_PLAN_TOTAL_AMOUNT_DEFAULT
