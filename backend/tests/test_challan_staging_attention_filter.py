"""Tests for Failed-tab staging list attention filter (includes Pending invoice)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.repositories import challan_master_staging as cms


def test_default_processed_attention_sql_includes_pending_invoice() -> None:
    sql = cms._DEFAULT_PROCESSED_ATTENTION_SQL.lower()
    assert "invoice_status" in sql
    assert "'pending'" in sql or "= 'pending'" in sql
    assert "invoice_complete" in sql


def test_list_masters_recent_executes_sql_with_pending_branch() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    with patch.object(cms, "get_connection", return_value=mock_conn):
        cms.list_masters_recent(100001, days=15, challan_book_num=None)

    mock_conn.close.assert_called_once()
    assert mock_cur.execute.called
    call_sql = mock_cur.execute.call_args[0][0]
    combined = call_sql.lower() if isinstance(call_sql, str) else ""
    assert "pending" in combined
    assert "invoice_complete" in combined


def test_count_masters_needing_attention_uses_same_predicate() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchone.return_value = {"c": 0}

    with patch.object(cms, "get_connection", return_value=mock_conn):
        cms.count_masters_needing_attention_recent(100001, days=15)

    call_sql = mock_cur.execute.call_args[0][0]
    combined = call_sql.lower() if isinstance(call_sql, str) else ""
    assert "pending" in combined
