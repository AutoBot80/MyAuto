"""Tests for IST date range presets and parsing."""

from __future__ import annotations

from datetime import date

from app.repositories import ist_date_ranges as dr


def test_parse_dd_mm_yyyy() -> None:
    assert dr.parse_dd_mm_yyyy("01-04-2026") == date(2026, 4, 1)
    assert dr.parse_dd_mm_yyyy("bad") is None


def test_validate_date_range_order() -> None:
    assert dr.validate_date_range("01-06-2026", "04-06-2026") == (date(2026, 6, 1), date(2026, 6, 4))
    assert dr.validate_date_range("10-06-2026", "01-06-2026") is None


def test_preset_current_month() -> None:
    ref = date(2026, 6, 5)
    start, end = dr.preset_bounds("current_month", ref=ref)
    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 4)


def test_preset_previous_fy() -> None:
    ref = date(2026, 6, 5)
    start, end = dr.preset_bounds("previous_fy", ref=ref)
    assert start == date(2025, 4, 1)
    assert end == date(2026, 3, 31)

