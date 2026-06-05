"""Unit tests for subdealer challan discount reduction from line discount."""

from __future__ import annotations

from app.services.add_subdealer_challan_service import line_discount_after_transport


def test_line_discount_flag_off_unchanged() -> None:
    assert line_discount_after_transport(1500.0, add_transport=False, per_vehicle=100.0) == 1500.0


def test_line_discount_flag_on_none_per_vehicle_unchanged() -> None:
    assert line_discount_after_transport(1500.0, add_transport=True, per_vehicle=None) == 1500.0


def test_line_discount_subtract_cost_only() -> None:
    assert line_discount_after_transport(1500.0, add_transport=True, per_vehicle=200.0) == 1300.0


def test_line_discount_zero_cost() -> None:
    assert line_discount_after_transport(1500.0, add_transport=True, per_vehicle=0.0) == 1500.0


def test_line_discount_percent_and_cost() -> None:
    assert (
        line_discount_after_transport(
            1000.0, add_transport=True, per_vehicle=50.0, reduce_percent=10.0
        )
        == 850.0
    )


def test_line_discount_percent_only() -> None:
    assert (
        line_discount_after_transport(
            1000.0, add_transport=True, per_vehicle=0.0, reduce_percent=10.0
        )
        == 900.0
    )


def test_line_discount_negative_result_allowed() -> None:
    assert (
        line_discount_after_transport(100.0, add_transport=True, per_vehicle=500.0) == -400.0
    )


def test_line_discount_negative_inputs() -> None:
    assert (
        line_discount_after_transport(
            1000.0, add_transport=True, per_vehicle=-50.0, reduce_percent=-10.0
        )
        == 1150.0
    )


def test_line_discount_null_percent_treated_as_zero() -> None:
    assert (
        line_discount_after_transport(
            1500.0, add_transport=True, per_vehicle=200.0, reduce_percent=None
        )
        == 1300.0
    )
