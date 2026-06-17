"""DMS resume: vehicle scrape from in-memory staging_payload (no local DATABASE_URL)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from app.services.fill_hero_dms_service import (
    Playwright_Hero_DMS_fill,
    restore_customer_context_from_staging,
    vehicle_scrape_from_staging_or_db,
)
from app.services.hero_dms_shared_utilities import SiebelDmsUrls


def test_vehicle_scrape_from_staging_payload_skips_db_fetch() -> None:
    saved = {"full_chassis": "MBLJAW570T9A06555", "model": "GLAMOUR X"}
    sid = str(uuid4())

    with patch("app.repositories.add_sales_staging.fetch_staging_payload") as fetch:
        fetch.side_effect = RuntimeError("fetch_staging_payload should not be called")
        out = vehicle_scrape_from_staging_or_db(
            sid,
            100001,
            staging_payload={"dms_vehicle_scrape": saved},
        )

    fetch.assert_not_called()
    assert out == saved


def test_restore_customer_context_from_staging_payload_skips_db_fetch() -> None:
    sid = str(uuid4())
    collated = {
        "fields": {"first_name": "Neeraj"},
        "notes": [],
        "mapping_unclear": [],
    }
    out: dict = {}

    with patch("app.repositories.add_sales_staging.fetch_staging_payload") as fetch:
        fetch.side_effect = RuntimeError("fetch_staging_payload should not be called")
        restore_customer_context_from_staging(
            out,
            sid,
            100001,
            staging_payload={"dms_customer_collated": collated},
        )

    fetch.assert_not_called()
    assert out["dms_customer_master_collated"]["fields"] == {"first_name": "Neeraj"}


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


def test_playwright_fill_resume_passes_staging_payload_to_vehicle_scrape() -> None:
    from unittest.mock import MagicMock

    page = MagicMock()
    saved_scrape = {"full_chassis": "CH123", "model": "Splendor"}
    sid = str(uuid4())
    staging_payload = {"dms_vehicle_scrape": saved_scrape, "vehicle_id": 34}

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
        Playwright_Hero_DMS_fill(
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
            staging_payload=staging_payload,
        )

    pv.assert_not_called()
    restore.assert_called_once()
    assert restore.call_args.kwargs.get("staging_payload") == staging_payload
