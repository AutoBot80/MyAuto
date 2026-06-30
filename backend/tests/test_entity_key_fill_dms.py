"""Tests for process_failure_log dedupe keys."""

from app.services.process_failure_log_service import entity_key_fill_dms


def test_entity_key_fill_dms_prefers_staging_over_mobile() -> None:
    sid = "880a28ca-e5ee-40bf-9eeb-036275a7e495"
    key = entity_key_fill_dms(
        staging_id=sid,
        customer_id=67,
        vehicle_id=90,
        mobile_digits="9116345075",
    )
    assert key == f"staging:{sid}"


def test_entity_key_fill_dms_falls_back_to_mobile_without_staging() -> None:
    key = entity_key_fill_dms(
        staging_id=None,
        customer_id=None,
        vehicle_id=None,
        mobile_digits="9116345075",
    )
    assert key == "m:9116345075"
