"""DMS Run Report downloads must use request dealer_id, not env DEALER_ID."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.fill_hero_dms_service import Playwright_Hero_DMS_fill
from app.services.hero_dms_shared_utilities import SiebelDmsUrls


def _test_urls() -> SiebelDmsUrls:
    return SiebelDmsUrls(
        contact="http://example/contact",
        vehicles="",
        precheck="",
        pdi="",
        vehicle="http://example/vehicle",
        enquiry="",
        line_items="",
        reports="",
    )


def _dms_values() -> dict:
    return {
        "mobile_phone": "7240275304",
        "first_name": "Devendra",
        "last_name": "Singh",
        "address_line_1": "Barakhur",
        "state": "RAJASTHAN",
        "pin_code": "321021",
        "dms_contact_path": "found",
    }


def test_run_hero_dms_reports_uses_request_dealer_not_env_default() -> None:
    """When dealer_id=100003 is passed, report PDF dir must not use DEALER_ID=100001."""
    page = MagicMock()
    sid = str(uuid4())
    saved_vehicle = {"full_chassis": "MBLHAW433T9E41132", "invoice_number": "INV-1"}
    captured: dict = {}

    def _persist(out, dms_values, *, order_scraped, preexisting_customer_id, preexisting_vehicle_id, dealer_id, log_fp, note):
        out["vehicle"] = {"invoice_number": "INV-1", "order_number": "ORD-1"}
        out["dms_master_persist_committed"] = True
        captured["persist_dealer_id"] = dealer_id

    def _reports(page, *, mobile, downloads_dir, **kwargs):
        captured["downloads_dir"] = Path(downloads_dir)
        return True, None, [], []

    with (
        patch("app.config.DEALER_ID", 100001),
        patch(
            "app.services.add_sales_staging_state_service.resolved_staging_dms_state",
            return_value=2,
        ),
        patch(
            "app.services.fill_hero_dms_service.vehicle_scrape_from_staging_or_db",
            return_value=saved_vehicle,
        ),
        patch(
            "app.services.fill_hero_dms_service.restore_customer_context_from_staging",
        ),
        patch(
            "app.services.fill_hero_dms_service.prepare_order",
            return_value={"order_number": "ORD-1"},
        ),
        patch(
            "app.services.fill_hero_dms_service.persist_masters_after_create_order",
            side_effect=_persist,
        ),
        patch(
            "app.services.fill_hero_dms_service.run_hero_dms_reports",
            side_effect=_reports,
        ) as run_reports,
        patch(
            "app.services.fill_hero_dms_service.get_uploaded_scans_sale_folder",
            return_value=Path("/Uploaded scans/100003/7240275304_120626"),
        ) as sale_folder,
    ):
        Playwright_Hero_DMS_fill(
            page,
            _dms_values(),
            _test_urls(),
            action_timeout_ms=1000,
            nav_timeout_ms=1000,
            content_frame_selector=None,
            mobile_aria_hints=[],
            staging_id=sid,
            dealer_id=100003,
            dms_state_hint=2,
        )

    sale_folder.assert_called_once()
    assert sale_folder.call_args[0][0] == 100003
    assert captured.get("persist_dealer_id") == 100003
    run_reports.assert_called_once()
    assert "100003" in str(captured.get("downloads_dir", ""))
