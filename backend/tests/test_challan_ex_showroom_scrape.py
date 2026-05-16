"""Tests for best-effort ex-showroom apply from order-phase scrape (subdealer challan)."""

from __future__ import annotations

from unittest.mock import patch

from app.services.add_subdealer_challan_commit_service import update_inventory_ex_showroom_from_order_scrape


@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_update_ex_showroom_no_raise_bad_payload(mock_upd: object) -> None:
    update_inventory_ex_showroom_from_order_scrape([1, 2], {"order_line_ex_showroom": "notalist"})
    mock_upd.assert_not_called()


@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_vehicle_ex_showroom_cost_mapped(mock_upd: object) -> None:
    update_inventory_ex_showroom_from_order_scrape(
        [10, 11],
        {
            "order_line_ex_showroom": [
                {"full_chassis": "X", "vehicle_ex_showroom_cost": "95,000.50"},
                {"full_chassis": "Y", "vehicle_ex_showroom_cost": "88000"},
            ],
        },
    )
    assert mock_upd.call_count == 2
    mock_upd.assert_any_call(10, ex_showroom_price=95000.5)
    mock_upd.assert_any_call(11, ex_showroom_price=88000.0)


@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_legacy_keys_fallback_when_vehicle_key_empty(mock_upd: object) -> None:
    update_inventory_ex_showroom_from_order_scrape(
        [20],
        {"order_line_ex_showroom": [{"vehicle_ex_showroom_cost": "", "ex_showroom": "77000"}]},
    )
    mock_upd.assert_called_once_with(20, ex_showroom_price=77000.0)


@patch("app.services.add_subdealer_challan_commit_service.update_discount_and_ex_showroom")
def test_per_line_db_error_does_not_abort_other_lines(mock_upd: object) -> None:
    def _side(iid: int, **kwargs: object) -> None:
        if iid == 10:
            raise RuntimeError("db down")

    mock_upd.side_effect = _side
    update_inventory_ex_showroom_from_order_scrape(
        [10, 11],
        {
            "order_line_ex_showroom": [
                {"vehicle_ex_showroom_cost": "100"},
                {"vehicle_ex_showroom_cost": "200"},
            ],
        },
    )
    assert mock_upd.call_count == 2
