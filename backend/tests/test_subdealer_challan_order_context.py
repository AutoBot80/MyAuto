"""Tests for sidecar subdealer challan order-context package builder."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.add_subdealer_challan_service import sidecar_build_order_playwright_package
from app.services.hero_dms_shared_utilities import SiebelDmsUrls

_BATCH = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_DEALER = 11870
_TO_DEALER = 11871


def _batch_row(**overrides: object) -> dict:
    base = {
        "from_dealer_id": _DEALER,
        "to_dealer_id": _TO_DEALER,
        "challan_date": "01/06/2026",
        "challan_book_num": "00979",
        "inventory_line_id": 101,
        "status": "ready",
    }
    base.update(overrides)
    return base


def _master_row(**overrides: object) -> dict:
    base = {
        "num_vehicles": 1,
        "add_transport_cost": False,
        "transport_cost_per_vehicle": None,
        "reduce_discount_by_percent": None,
        "dms_order_number": None,
        "dms_attached_vin_count": 0,
    }
    base.update(overrides)
    return base


@patch("app.services.add_subdealer_challan_service.hero_dms_siebel_urls_for_dealer")
@patch("app.services.add_subdealer_challan_service.prepare_customer_for_challan")
@patch("app.services.add_subdealer_challan_service.fetch_lines_for_batch_inventory")
@patch("app.services.add_subdealer_challan_service.update_discount_and_ex_showroom")
@patch("app.services.add_subdealer_challan_service.get_subdealer_challan_discount")
@patch("app.services.add_subdealer_challan_service.get_by_id")
@patch("app.services.add_subdealer_challan_service.master_repo.fetch_master")
@patch("app.services.add_subdealer_challan_service.detail_repo.batch_all_prepared_for_order_retry")
@patch("app.services.add_subdealer_challan_service.detail_repo.fetch_batch_rows")
def test_sidecar_build_order_package_returns_urls_without_name_error(
    mock_fetch_rows: MagicMock,
    mock_all_prepared: MagicMock,
    mock_fetch_master: MagicMock,
    mock_get_by_id: MagicMock,
    mock_discount: MagicMock,
    mock_update_disc: MagicMock,
    mock_fetch_inv: MagicMock,
    mock_prepare_customer: MagicMock,
    mock_urls_for_dealer: MagicMock,
) -> None:
    mock_fetch_rows.return_value = [_batch_row()]
    mock_all_prepared.return_value = True
    mock_fetch_master.return_value = _master_row()
    mock_get_by_id.return_value = {"model": "Splendor", "inventory_line_id": 101}
    mock_discount.return_value = 1500.0
    mock_fetch_inv.return_value = [
        {"inventory_line_id": 101, "chassis_no": "CHASSIS001", "discount": 1500.0},
    ]
    mock_urls_for_dealer.return_value = SiebelDmsUrls(
        contact="https://example.test/contact",
        vehicles="",
        precheck="",
        pdi="",
        vehicle="https://example.test/vehicle",
        enquiry="",
        line_items="",
        reports="",
    )

    out = sidecar_build_order_playwright_package(
        challan_batch_id=_BATCH,
        dealer_id=_DEALER,
        last_vehicle_scrape={},
    )

    assert out["ok"] is True
    urls = out.get("urls")
    assert isinstance(urls, dict)
    assert urls.get("contact") == "https://example.test/contact"
    assert urls.get("vehicle") == "https://example.test/vehicle"
    mock_urls_for_dealer.assert_called_once_with(_DEALER)
    dms_values = out.get("dms_values")
    assert isinstance(dms_values, dict)
    assert len(dms_values.get("order_line_vehicles") or []) == 1


@patch("app.services.add_subdealer_challan_service.hero_dms_siebel_urls_for_dealer")
@patch("app.services.add_subdealer_challan_service.prepare_customer_for_challan")
@patch("app.services.add_subdealer_challan_service.fetch_lines_for_batch_inventory")
@patch("app.services.add_subdealer_challan_service.update_discount_and_ex_showroom")
@patch("app.services.add_subdealer_challan_service.get_subdealer_challan_discount")
@patch("app.services.add_subdealer_challan_service.get_by_id")
@patch("app.services.add_subdealer_challan_service.master_repo.fetch_master")
@patch("app.services.add_subdealer_challan_service.detail_repo.batch_all_prepared_for_order_retry")
@patch("app.services.add_subdealer_challan_service.detail_repo.fetch_batch_rows")
def test_sidecar_build_order_package_dealer_ref_missing_returns_error(
    mock_fetch_rows: MagicMock,
    mock_all_prepared: MagicMock,
    mock_fetch_master: MagicMock,
    mock_get_by_id: MagicMock,
    mock_discount: MagicMock,
    mock_update_disc: MagicMock,
    mock_fetch_inv: MagicMock,
    mock_prepare_customer: MagicMock,
    mock_urls_for_dealer: MagicMock,
) -> None:
    mock_fetch_rows.return_value = [_batch_row()]
    mock_all_prepared.return_value = True
    mock_fetch_master.return_value = _master_row()
    mock_get_by_id.return_value = {"model": "Splendor", "inventory_line_id": 101}
    mock_discount.return_value = 1500.0
    mock_fetch_inv.return_value = [
        {"inventory_line_id": 101, "chassis_no": "CHASSIS001", "discount": 1500.0},
    ]
    mock_prepare_customer.side_effect = ValueError(f"dealer_ref not found for to_dealer_id={_TO_DEALER}")

    out = sidecar_build_order_playwright_package(
        challan_batch_id=_BATCH,
        dealer_id=_DEALER,
        last_vehicle_scrape={},
    )

    assert out["ok"] is False
    assert "dealer_ref not found" in str(out.get("error") or "")
    mock_urls_for_dealer.assert_not_called()
