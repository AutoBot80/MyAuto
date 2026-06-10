"""Tests for OCR run log missing-field detection and repository SQL."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.repositories import ocr_run_log as orl
from app.repositories import process_failure_log as pfl
from app.routers import admin as admin_router
from app.services import ocr_run_log_service as orls


def test_compute_missing_ocr_fields_sorted_and_partial() -> None:
    details = {
        "vehicle": {"frame_no": "ABC123", "engine_no": ""},
        "customer": {"name": "Test User", "mobile_number": "9876543210"},
        "insurance": {"nominee_name": "Nom"},
        "details_customer_name": "Test User",
        "name_mismatch_error": "Names do not match",
    }
    missing = orls.compute_missing_ocr_fields(details)
    assert "Engine no" in missing
    assert "Name match" in missing
    assert missing == sorted(missing, key=str.casefold)
    assert "Frame no" not in missing


def test_compute_missing_ocr_fields_empty_when_all_present() -> None:
    details = {
        "vehicle": {k: "x" for k, _ in orls._VEHICLE_FIELDS},
        "customer": {
            **{k: "x" for k, _ in __import__("app.services.sales_ocr_service", fromlist=["AADHAR_15_FIELDS"]).AADHAR_15_FIELDS},
            "address": "123 Main",
            "alt_phone_num": "9999999999",
        },
        "insurance": {k: "x" for k, _ in orls._INSURANCE_FIELDS},
        "details_customer_name": "Test",
    }
    assert orls.compute_missing_ocr_fields(details) == []


def test_record_safe_skips_insert_when_no_missing_fields() -> None:
    with patch.object(orls, "compute_missing_ocr_fields", return_value=[]):
        with patch("app.repositories.ocr_run_log.insert_ocr_run_log") as mock_insert:
            orls.record_safe(dealer_id=1, subfolder="9876543210_100625", details={"vehicle": {}})
            mock_insert.assert_not_called()


def test_record_safe_inserts_when_fields_missing() -> None:
    with patch.object(orls, "compute_missing_ocr_fields", return_value=["Engine no", "Frame no"]):
        with patch("app.repositories.ocr_run_log.insert_ocr_run_log") as mock_insert:
            orls.record_safe(
                dealer_id=100001,
                subfolder="9876543210_100625",
                details={"customer": {"mobile_number": "9876543210"}},
            )
            mock_insert.assert_called_once()
            kw = mock_insert.call_args.kwargs
            assert kw["dealer_id"] == 100001
            assert kw["customer_mobile"] == "9876543210"
            assert kw["ocr_failures"] == "Engine no, Frame no"


def test_insert_executes_append_only_sql() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur

    with patch.object(orl, "get_connection", return_value=mock_conn):
        orl.insert_ocr_run_log(
            dealer_id=100001,
            customer_mobile="9876543210",
            sale_subfolder="9876543210_100625",
            ocr_failures="Engine no",
        )

    sql = mock_cur.execute.call_args[0][0]
    assert "INSERT INTO ocr_run_log" in sql
    assert "ON CONFLICT" not in sql


def test_list_recent_for_admin_includes_day_window() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cur

    with patch.object(orl, "get_connection", return_value=mock_conn):
        orl.list_recent_for_admin(limit=50, days=15)

    sql = mock_cur.execute.call_args[0][0]
    params = mock_cur.execute.call_args[0][1]
    assert "INTERVAL '1 day'" in sql
    assert params[0] == 15
    assert params[1] == 50


def test_process_failure_log_list_includes_day_window() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cur

    with patch.object(pfl, "get_connection", return_value=mock_conn):
        pfl.list_recent_for_admin(limit=100, days=15)

    sql = mock_cur.execute.call_args[0][0]
    assert "INTERVAL '1 day'" in sql


def test_ocr_occurred_at_ist_display_ddmm_hhmm() -> None:
    dt = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)
    # 14:30 UTC = 20:00 IST (June 10)
    assert admin_router._occurred_at_to_ist_display_ddmm_hhmm(dt) == "10-06-2026 20:00"
