"""Tests for challan committed list date-range filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.repositories import challan_committed as repo


def test_list_committed_masters_date_range_sql() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    with patch.object(repo, "get_connection", return_value=mock_conn):
        repo.list_committed_masters_for_dealer(
            100001,
            date_from="01-04-2025",
            date_to="31-03-2026",
        )

    sql = mock_cur.execute.call_args[0][0].lower()
    assert "created_at is not null" in sql
    assert "at time zone 'asia/kolkata'" in sql
    assert "2025-04-01" in sql
    assert "2026-03-31" in sql
