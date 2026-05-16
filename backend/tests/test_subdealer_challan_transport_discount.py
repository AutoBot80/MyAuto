"""Unit tests for subdealer challan transport deduction from line discount."""

from __future__ import annotations

from app.services.add_subdealer_challan_service import line_discount_after_transport


def test_line_discount_flag_off_unchanged() -> None:
    assert line_discount_after_transport(1500.0, add_transport=False, per_vehicle=100.0) == 1500.0


def test_line_discount_flag_on_none_per_vehicle_unchanged() -> None:
    assert line_discount_after_transport(1500.0, add_transport=True, per_vehicle=None) == 1500.0


def test_line_discount_subtract_transport() -> None:
    assert line_discount_after_transport(1500.0, add_transport=True, per_vehicle=200.0) == 1300.0


def test_line_discount_zero_transport() -> None:
    assert line_discount_after_transport(1500.0, add_transport=True, per_vehicle=0.0) == 1500.0


def test_line_discount_floors_at_zero() -> None:
    assert line_discount_after_transport(100.0, add_transport=True, per_vehicle=500.0) == 0.0
