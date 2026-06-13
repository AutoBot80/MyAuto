"""Staging customer city/state/address overlay for Generate Insurance (MISP RTO)."""

from app.services.fill_hero_insurance_service import _misp_rto_fuzzy_query
from app.services.insurance_form_values import _apply_staging_insurance_overlay


def test_staging_city_wins_over_master_for_rto_query() -> None:
    values = {
        "city": "Paraua",
        "state": "Rajasthan",
        "address": "Old village address",
        "pin_code": "321642",
    }
    staging_payload = {
        "customer": {
            "city": "Bharatpur",
            "state": "Rajasthan",
            "address": "Bayana, Bharatpur, Rajasthan - 321001",
            "pin": "321001",
        }
    }
    _apply_staging_insurance_overlay(values, staging_payload)

    assert values["city"] == "Bharatpur"
    assert values["state"] == "Rajasthan"
    assert values["address"] == "Bayana, Bharatpur, Rajasthan - 321001"
    assert values["pin_code"] == "321001"
    assert _misp_rto_fuzzy_query(city=values["city"], state=values["state"]) == "RJ - Bharatpur"


def test_staging_overlay_skipped_when_no_staging_payload() -> None:
    values = {"city": "Paraua", "state": "Rajasthan"}
    _apply_staging_insurance_overlay(values, None)
    assert values["city"] == "Paraua"


def test_staging_empty_city_leaves_master_city() -> None:
    values = {"city": "Paraua", "state": "Rajasthan"}
    _apply_staging_insurance_overlay(values, {"customer": {"address": "Some line"}})
    assert values["city"] == "Paraua"
