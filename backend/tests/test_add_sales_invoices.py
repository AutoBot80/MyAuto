"""Tests for Add Sales Invoices list (sales_master recent window)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.repositories import add_sales_invoices as repo


def test_search_pattern_wildcard_and_suffix() -> None:
    assert repo._search_pattern("AB*CD") == "AB%CD"
    assert repo._search_pattern("12345") == "%12345"
    assert repo._search_pattern("partial") == "%partial%"


def test_digits_mobile_last_10() -> None:
    assert repo._digits_mobile("919876543210") == 9876543210
    assert repo._digits_mobile("invalid") is None


def test_list_recent_sales_invoices_invalid_mobile_returns_empty() -> None:
    with patch.object(repo, "get_connection") as gc:
        rows = repo.list_recent_sales_invoices(dealer_id=1, days=7, mobile="not-a-number")
    gc.assert_not_called()
    assert rows == []


def test_list_recent_sales_invoices_sql_includes_dealer_and_window() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    with patch.object(repo, "get_connection", return_value=mock_conn):
        repo.list_recent_sales_invoices(dealer_id=42, days=7)

    mock_conn.close.assert_called_once()
    assert mock_cur.execute.called
    sql = mock_cur.execute.call_args[0][0].lower()
    params = mock_cur.execute.call_args[0][1]
    assert "sales_master" in sql
    assert "sm.dealer_id = %s" in sql
    assert "billing_date >=" in sql
    assert "insurance_type = 'main'" in sql or "insurance_type = 'Main'" in sql
    assert params[0] == 42


def test_list_recent_sales_invoices_mobile_filter_in_sql() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    with patch.object(repo, "get_connection", return_value=mock_conn):
        repo.list_recent_sales_invoices(dealer_id=1, days=7, mobile="9876543210")

    sql = mock_cur.execute.call_args[0][0].lower()
    params = mock_cur.execute.call_args[0][1]
    assert "cm.mobile_number = %s" in sql
    assert 9876543210 in params


def test_list_recent_sales_invoices_chassis_engine_filters() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    with patch.object(repo, "get_connection", return_value=mock_conn):
        repo.list_recent_sales_invoices(dealer_id=1, days=7, chassis="MB1", engine="EN2")

    sql = mock_cur.execute.call_args[0][0].lower()
    assert "vm.chassis" in sql
    assert "vm.engine" in sql


def test_serialize_row_formats_dates_and_mobile() -> None:
    from datetime import datetime

    row = {
        "sales_id": 1,
        "customer_name": " Test ",
        "mobile_number": 9876543210,
        "model": "Splendor",
        "billing_date": datetime(2026, 5, 20, 12, 0, 0),
        "invoice_number": " INV-1 ",
        "file_location": "folder1",
        "insurance_policy_num": "POL1",
        "cpa_policy_num": None,
    }
    out = repo._serialize_row(row)
    assert out["mobile"] == "9876543210"
    assert out["invoice_date"] == "20-05-2026"
    assert out["invoice_number"] == "INV-1"
    assert out["customer_name"] == "Test"
