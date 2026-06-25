"""Hero insure reports: staging state=3 after INSERT (no local DB reload)."""

from unittest.mock import patch

from app.services import hero_insure_reports_service as hir


def test_insert_from_grid_marks_insurance_state_3() -> None:
    grid = {"policy_num": "POL123", "premium": 5000.0, "idv": 60000.0}
    with patch(
        "app.services.add_sales_staging_state_service.persist_staging_insurance_main_fields"
    ) as persist, patch(
        "app.repositories.add_sales_staging.fetch_staging_payload"
    ) as fetch_payload, patch(
        "app.services.add_sales_commit_service.insert_insurance_master_after_gi"
    ) as insert, patch(
        "app.services.add_sales_staging_state_service.mark_staging_insurance_state"
    ) as mark_state, patch.object(hir, "_ins_log"):
        err, out = hir._misp_insert_insurance_master_from_grid_scrape(
            grid_scrape=grid,
            customer_id=1,
            vehicle_id=2,
            fill_values={},
            staging_payload={"insurance": {}},
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
            ocr_output_dir=None,
            subfolder="sale1",
        )
    assert err is None
    assert out["policy_num"] == "POL123"
    persist.assert_called_once()
    fetch_payload.assert_not_called()
    insert.assert_called_once()
    merged = insert.call_args.kwargs.get("staging_payload") or {}
    assert merged.get("insurance", {}).get("policy_num") == "POL123"
    assert merged.get("insurance", {}).get("premium") == 5000.0
    mark_state.assert_called_once_with(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", 100001, 3
    )


def test_insert_from_grid_skips_duplicate_and_still_marks_state_3() -> None:
    grid = {"policy_num": "POL123"}
    with patch(
        "app.services.add_sales_staging_state_service.persist_staging_insurance_main_fields"
    ), patch(
        "app.repositories.add_sales_staging.fetch_staging_payload"
    ) as fetch_payload, patch(
        "app.services.add_sales_commit_service.insert_insurance_master_after_gi",
        side_effect=ValueError("Hero insurance (Main) already recorded for this customer"),
    ), patch(
        "app.services.add_sales_staging_state_service.mark_staging_insurance_state"
    ) as mark_state, patch.object(hir, "_ins_log"):
        err, _ = hir._misp_insert_insurance_master_from_grid_scrape(
            grid_scrape=grid,
            customer_id=1,
            vehicle_id=2,
            fill_values={},
            staging_payload={},
            staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            dealer_id=100001,
            ocr_output_dir=None,
            subfolder="sale1",
        )
    assert err is None
    fetch_payload.assert_not_called()
    mark_state.assert_called_once_with(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", 100001, 3
    )
