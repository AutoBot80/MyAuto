"""Unit tests for dealer dashboard pivots (no live DB)."""

from __future__ import annotations

from datetime import date, timedelta

from app.repositories import dealer_dashboard as dd


def test_pivot_bucket_counts_sums_same_day() -> None:
    base = date(2026, 5, 1)
    days = [base + timedelta(days=i) for i in range(7)]
    raw = [{"bucket": days[2], "cnt": 2}, {"bucket": days[2], "cnt": 1}]
    assert dd.pivot_bucket_counts(raw, days) == [0, 0, 3, 0, 0, 0, 0]


def test_pivot_subdealer_sales_rows_drops_all_zero() -> None:
    base = date(2026, 5, 1)
    days = [base + timedelta(days=i) for i in range(7)]
    raw = [
        {"dealer_id": 1, "dealer_name": "A", "bucket": days[0], "cnt": 1},
        {"dealer_id": 2, "dealer_name": "B", "bucket": days[1], "cnt": 0},
    ]
    # dealer 2 has only a zero-count row in one bucket — still totals zero across window
    raw.append({"dealer_id": 2, "dealer_name": "B", "bucket": days[2], "cnt": 0})
    rows = dd.pivot_subdealer_sales_rows(raw, days)
    assert len(rows) == 1
    assert rows[0]["dealer_id"] == 1
    assert sum(rows[0]["counts"]) == 1


def test_pivot_subdealer_sales_rows_keeps_nonzero_child() -> None:
    base = date(2026, 5, 1)
    days = [base + timedelta(days=i) for i in range(7)]
    raw = [
        {"dealer_id": 10, "dealer_name": "X", "bucket": days[6], "cnt": 5},
        {"dealer_id": 11, "dealer_name": "Y", "bucket": days[0], "cnt": 0},
    ]
    rows = dd.pivot_subdealer_sales_rows(raw, days)
    ids = {r["dealer_id"] for r in rows}
    assert ids == {10}
