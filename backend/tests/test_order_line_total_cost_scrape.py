"""Unit tests for invoice-list ex-showroom scrape helpers and challan master total override."""

from __future__ import annotations

from unittest.mock import patch

from app.services.add_subdealer_challan_commit_service import (
    scraped_total_ex_showroom_from_vehicle_out,
    update_inventory_ex_showroom_from_order_scrape,
)
def test_coerce_ex_showroom_scalar_accepts_rs_total_cost() -> None:
    from app.services.add_subdealer_challan_commit_service import _coerce_ex_showroom_scalar

    assert _coerce_ex_showroom_scalar("Rs.78,993.00") == 78993.0
    assert _coerce_ex_showroom_scalar("Rs.109,327.00") == 109327.0
    assert _coerce_ex_showroom_scalar("78993.00") == 78993.0


def test_scraped_total_ex_showroom_from_vehicle_out() -> None:
    assert scraped_total_ex_showroom_from_vehicle_out({"vehicle_ex_showroom_cost": "Rs.78,993.00"}) == 78993.0
    assert scraped_total_ex_showroom_from_vehicle_out({"vehicle_price": "88000"}) == 88000.0
    assert scraped_total_ex_showroom_from_vehicle_out({}) is None


@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_single_line_inventory_from_vehicle_ex_showroom_cost(mock_upd: object) -> None:
    update_inventory_ex_showroom_from_order_scrape(
        [42],
        {"vehicle_ex_showroom_cost": "Rs.78,993.00"},
    )
    mock_upd.assert_called_once_with(42, ex_showroom_price=78993.0)


@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_multi_line_no_inventory_update_without_order_line_list(mock_upd: object) -> None:
    update_inventory_ex_showroom_from_order_scrape(
        [10, 11],
        {"vehicle_ex_showroom_cost": "Rs.78,993.00"},
    )
    mock_upd.assert_not_called()


@patch("app.services.add_subdealer_challan_commit_service.fetch_lines_for_batch_inventory")
@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_order_line_list_matches_by_chassis(mock_upd: object, mock_fetch: object) -> None:
    mock_fetch.return_value = [
        {"inventory_line_id": 10, "chassis_no": "MBLAAA11111111111"},
        {"inventory_line_id": 11, "chassis_no": "MBLBBB22222222222"},
    ]
    update_inventory_ex_showroom_from_order_scrape(
        [10, 11],
        {
            "order_line_ex_showroom": [
                {
                    "full_chassis": "MBLBBB22222222222",
                    "vehicle_ex_showroom_cost": "Rs.75,318.10",
                }
            ]
        },
    )
    mock_upd.assert_called_once_with(11, ex_showroom_price=75318.10)
