"""Create-invoice eligibility: insurance_state=2 resume, state=3 complete."""

from unittest.mock import patch

from app.routers.add_sales import (
    _apply_staging_insurance_resume_eligibility,
    _eligibility_by_customer_vehicle_ids,
)


def test_apply_resume_when_insurance_state_2() -> None:
    base = {
        "invoice_recorded": True,
        "generate_insurance_enabled": False,
        "generate_insurance_reason": "blocked",
    }
    with patch(
        "app.routers.add_sales.fetch_staging_insurance_state",
        return_value=2,
    ):
        out = _apply_staging_insurance_resume_eligibility(
            base,
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
        )
    assert out["generate_insurance_enabled"] is True
    assert "manually" in (out["generate_insurance_reason"] or "").lower()


def test_apply_resume_skipped_when_state_not_2() -> None:
    base = {"invoice_recorded": True, "generate_insurance_enabled": False}
    with patch(
        "app.routers.add_sales.fetch_staging_insurance_state",
        return_value=0,
    ):
        out = _apply_staging_insurance_resume_eligibility(
            base,
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
        )
    assert out["generate_insurance_enabled"] is False


def test_apply_resume_skipped_when_state_3_complete() -> None:
    base = {
        "invoice_recorded": True,
        "generate_insurance_enabled": False,
        "generate_insurance_reason": "A policy number is already stored for this sale",
    }
    with patch(
        "app.routers.add_sales.fetch_staging_insurance_state",
        return_value=3,
    ):
        out = _apply_staging_insurance_resume_eligibility(
            base,
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
        )
    assert out["generate_insurance_enabled"] is False


def test_apply_resume_skipped_when_policy_already_stored() -> None:
    base = {
        "invoice_recorded": True,
        "generate_insurance_enabled": False,
        "generate_insurance_reason": "A policy number is already stored for this sale",
    }
    with patch(
        "app.routers.add_sales.fetch_staging_insurance_state",
        return_value=2,
    ):
        out = _apply_staging_insurance_resume_eligibility(
            base,
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
        )
    assert out["generate_insurance_enabled"] is False


def test_eligibility_by_ids_applies_resume() -> None:
    with patch("app.routers.add_sales.get_connection") as mock_conn, patch(
        "app.routers.add_sales._has_cpa_insurance_master_row",
        return_value=False,
    ), patch(
        "app.routers.add_sales._cpa_alliance_insurance_eligibility",
        return_value={},
    ), patch(
        "app.routers.add_sales.fetch_staging_insurance_state",
        return_value=2,
    ):
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_conn.return_value.cursor.return_value.__exit__ = lambda *a: None
        mock_cur.fetchone.side_effect = [
            {"invoice_number": "INV-001"},
            None,
        ]
        out = _eligibility_by_customer_vehicle_ids(
            10,
            20,
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
        )
    assert out["invoice_recorded"] is True
    assert out["generate_insurance_enabled"] is True


def test_eligibility_by_ids_no_resume_when_main_policy_exists() -> None:
    with patch("app.routers.add_sales.get_connection") as mock_conn, patch(
        "app.routers.add_sales._has_cpa_insurance_master_row",
        return_value=False,
    ), patch(
        "app.routers.add_sales._cpa_alliance_insurance_eligibility",
        return_value={},
    ), patch(
        "app.routers.add_sales.fetch_staging_insurance_state",
        return_value=2,
    ):
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_conn.return_value.cursor.return_value.__exit__ = lambda *a: None
        mock_cur.fetchone.side_effect = [
            {"invoice_number": "INV-001"},
            {"insurance_id": 99},
        ]
        out = _eligibility_by_customer_vehicle_ids(
            10,
            20,
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
        )
    assert out["generate_insurance_enabled"] is False
    assert "already stored" in (out["generate_insurance_reason"] or "").lower()
