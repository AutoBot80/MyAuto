"""Tests for DMS resume when ``dms_state >= 1`` skips ``prepare_vehicle``."""

from __future__ import annotations

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


def test_playwright_fill_skips_prepare_vehicle_when_dms_state_1() -> None:
    page = MagicMock()
    saved_scrape = {"full_chassis": "CH123", "model": "Splendor"}
    sid = str(uuid4())

    with (
        patch(
            "app.services.add_sales_staging_state_service.resolved_staging_dms_state",
            return_value=1,
        ),
        patch(
            "app.services.fill_hero_dms_service.vehicle_scrape_from_staging_or_db",
            return_value=saved_scrape,
        ) as restore,
        patch("app.services.fill_hero_dms_service.prepare_vehicle") as pv,
        patch(
            "app.services.fill_hero_dms_service.prepare_customer",
            return_value=False,
        ),
    ):
        out = Playwright_Hero_DMS_fill(
            page,
            {"mobile_phone": "9876543210", "first_name": "Test", "dms_contact_path": "found"},
            _test_urls(),
            action_timeout_ms=1000,
            nav_timeout_ms=1000,
            content_frame_selector=None,
            mobile_aria_hints=[],
            staging_id=sid,
            dealer_id=100001,
            dms_state_hint=1,
        )

    pv.assert_not_called()
    restore.assert_called_once()
    assert out.get("vehicle") == saved_scrape
