"""Tests for add_sales_staging dms_state / insurance_state updates."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.repositories.add_sales_staging import (
    fetch_staging_dms_state,
    fetch_staging_insurance_state,
    update_staging_processing_state,
)
from app.services.add_sales_staging_state_service import (
    mark_staging_dms_state,
    mark_staging_insurance_state,
    resolved_staging_dms_state,
)


def test_update_staging_processing_state_insurance_only() -> None:
    sid = str(uuid4())
    cur = MagicMock()
    cur.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.repositories.add_sales_staging.get_connection", return_value=conn):
        ok = update_staging_processing_state(sid, 100001, insurance_state=2)

    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "insurance_state = %s" in sql
    assert "dms_state" not in sql
    params = cur.execute.call_args[0][1]
    assert params[0] == 2
    assert params[1] == sid
    assert params[2] == 100001
    conn.commit.assert_called_once()


def test_update_staging_processing_state_both_columns() -> None:
    sid = str(uuid4())
    cur = MagicMock()
    cur.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.repositories.add_sales_staging.get_connection", return_value=conn):
        ok = update_staging_processing_state(sid, 100001, dms_state=1, insurance_state=2)

    assert ok is True
    sql = cur.execute.call_args[0][0]
    assert "dms_state = %s" in sql
    assert "insurance_state = %s" in sql


def test_update_staging_processing_state_invalid_uuid() -> None:
    assert update_staging_processing_state("not-a-uuid", 100001, insurance_state=2) is False


def test_update_staging_processing_state_no_columns() -> None:
    assert update_staging_processing_state(str(uuid4()), 100001) is False


def test_mark_staging_insurance_state_delegates() -> None:
    sid = str(uuid4())
    with patch(
        "app.services.add_sales_staging_state_service.update_staging_processing_state",
        return_value=True,
    ) as upd:
        mark_staging_insurance_state(sid, 100001, 2)
    upd.assert_called_once_with(sid, 100001, insurance_state=2)


def test_mark_staging_insurance_state_empty_staging_id() -> None:
    with patch(
        "app.services.add_sales_staging_state_service.update_staging_processing_state",
    ) as upd:
        mark_staging_insurance_state("", 100001, 2)
    upd.assert_not_called()


def test_fetch_staging_insurance_state() -> None:
    sid = str(uuid4())
    cur = MagicMock()
    cur.fetchone.return_value = {"insurance_state": 2}
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.repositories.add_sales_staging.get_connection", return_value=conn):
        assert fetch_staging_insurance_state(sid, 100001) == 2


def test_fetch_staging_dms_state() -> None:
    sid = str(uuid4())
    cur = MagicMock()
    cur.fetchone.return_value = {"dms_state": 2}
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.repositories.add_sales_staging.get_connection", return_value=conn):
        assert fetch_staging_dms_state(sid, 100001) == 2


def test_mark_staging_dms_state_delegates() -> None:
    sid = str(uuid4())
    with patch(
        "app.services.add_sales_staging_state_service.update_staging_processing_state",
        return_value=True,
    ) as upd:
        mark_staging_dms_state(sid, 100001, 1)
    upd.assert_called_once_with(sid, 100001, dms_state=1)


def test_resolved_staging_dms_state_hint() -> None:
    assert resolved_staging_dms_state(staging_id=None, dealer_id=100001, dms_state_hint=2) == 2


def test_resolved_staging_dms_state_fetch() -> None:
    sid = str(uuid4())
    with patch(
        "app.repositories.add_sales_staging.fetch_staging_dms_state",
        return_value=1,
    ):
        assert resolved_staging_dms_state(staging_id=sid, dealer_id=100001, dms_state_hint=None) == 1
