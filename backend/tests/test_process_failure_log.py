"""Tests for ``process_failure_log`` upsert SQL (no live DB required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.repositories import process_failure_log as pfl


def test_upsert_executes_insert_on_conflict_sql() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur

    with patch.object(pfl, "get_connection", return_value=mock_conn):
        pfl.upsert_process_failure(
            dealer_id=100001,
            process_label="Create Invoice",
            entity_dedupe_key="m:9876543210",
            error_text="first error",
            customer_mobile="9876543210",
        )
        pfl.upsert_process_failure(
            dealer_id=100001,
            process_label="Create Invoice",
            entity_dedupe_key="m:9876543210",
            error_text="second error",
            customer_mobile="9876543210",
        )

    mock_conn.commit.assert_called()
    mock_conn.close.assert_called()
    assert mock_cur.execute.call_count == 2
    sql0 = mock_cur.execute.call_args_list[0][0][0]
    assert "INSERT INTO process_failure_log" in sql0
    assert "ON CONFLICT" in sql0
    assert "DO UPDATE" in sql0
