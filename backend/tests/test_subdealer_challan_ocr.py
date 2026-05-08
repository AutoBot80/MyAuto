"""Unit tests for subdealer challan OCR helpers (no AWS calls)."""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.subdealer_challan_ocr_service import (
    _challan_no_from_repeated_invoice,
    _find_engine_chassis_table,
    _header_cell_is_chassis_column,
    _rows_from_table,
    dedupe_challan_lines,
    dedupe_raw_challan_lines,
    parse_challan_date_to_iso,
    parse_subdealer_challan,
    sanitize_challan_line_field,
)


class TestChallanDate(unittest.TestCase):
    def test_dd_mm_yy(self) -> None:
        iso, ddmmyyyy = parse_challan_date_to_iso("03/04/26")
        self.assertEqual(iso, "2026-04-03")
        self.assertEqual(ddmmyyyy, "03042026")

    def test_dd_mm_yyyy(self) -> None:
        iso, ddmmyyyy = parse_challan_date_to_iso("15/12/2025")
        self.assertEqual(iso, "2025-12-15")
        self.assertEqual(ddmmyyyy, "15122025")

    def test_invalid(self) -> None:
        self.assertEqual(parse_challan_date_to_iso("not-a-date"), (None, None))


class TestSanitizeLineField(unittest.TestCase):
    def test_strips_edges_and_middle_junk(self) -> None:
        self.assertEqual(sanitize_challan_line_field("03432|"), "03432")
        self.assertEqual(sanitize_challan_line_field("|53768•"), "53768")
        self.assertEqual(sanitize_challan_line_field("12/34"), "1234")


class TestDedupeChallanLines(unittest.TestCase):
    def test_drops_duplicate_pairs(self) -> None:
        lines = [
            {"engine_no": "1", "chassis_no": "2", "status": "queued"},
            {"engine_no": "1", "chassis_no": "2", "status": "queued"},
            {"engine_no": "3", "chassis_no": "4", "status": "queued"},
        ]
        out, n = dedupe_challan_lines(lines)
        self.assertEqual(n, 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["engine_no"], "1")
        self.assertEqual(out[1]["engine_no"], "3")

    def test_case_insensitive(self) -> None:
        lines = [
            {"engine_no": "ab", "chassis_no": "cd", "status": "q"},
            {"engine_no": "AB", "chassis_no": "CD", "status": "q"},
        ]
        out, n = dedupe_challan_lines(lines)
        self.assertEqual(n, 1)
        self.assertEqual(len(out), 1)


class TestDedupeRawChallanLines(unittest.TestCase):
    def test_drops_duplicate_raw_pairs(self) -> None:
        lines = [
            {"raw_engine": "E1", "raw_chassis": "C1"},
            {"raw_engine": "e1", "raw_chassis": "c1"},
            {"raw_engine": "E2", "raw_chassis": "C2"},
        ]
        out, n = dedupe_raw_challan_lines(lines)
        self.assertEqual(n, 1)
        self.assertEqual(len(out), 2)


class TestEngineChassisTable(unittest.TestCase):
    def test_find_table(self) -> None:
        tables = [
            [["x", "y"]],
            [
                ["S. No.", "Engine No.", "Chassis No.", "Key"],
                ["1", "E1", "C1", ""],
            ],
        ]
        found = _find_engine_chassis_table(tables)
        self.assertIsNotNone(found)
        grid, hi = found
        self.assertEqual(hi, 0)
        self.assertEqual(len(_rows_from_table(grid, hi)), 1)

    def test_rows_pair(self) -> None:
        grid = [
            ["S.No", "Engine No.", "Chassis No."],
            ["1", "111", "222"],
            ["2", "333", "444"],
        ]
        rows = _rows_from_table(grid, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["engine_no"], "111")
        self.assertEqual(rows[0]["chassis_no"], "222")
        self.assertEqual(rows[0]["status"], "queued")

    def test_find_model_details_frame_no_table(self) -> None:
        grid = [
            ["Excise Invoice", "Frame No", "Engine No", "Material"],
            ["5V2605002394", "MBLHAW487T5E82253", "HA11F7T5E54973", "HSPLMDRSCFIBHG"],
            ["5V2605002394", "MBLHAW480T5E03716", "HA11F7T5E05296", "HSPLMDRSCFIRPB"],
        ]
        tables = [grid]
        found = _find_engine_chassis_table(tables)
        self.assertIsNotNone(found)
        g, hi = found
        self.assertEqual(hi, 0)
        rows = _rows_from_table(g, hi)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chassis_no"], "MBLHAW487T5E82253")
        self.assertEqual(rows[0]["engine_no"], "HA11F7T5E54973")

    def test_find_table_title_row_above_header(self) -> None:
        tables = [
            [
                ["Model Details Table"],
                ["Excise Invoice", "Frame No", "Engine No", "Material"],
                ["INV", "C1", "E1", "M1"],
            ],
        ]
        found = _find_engine_chassis_table(tables)
        self.assertIsNotNone(found)
        g, hi = found
        self.assertEqual(hi, 1)
        rows = _rows_from_table(g, hi)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["engine_no"], "E1")
        self.assertEqual(rows[0]["chassis_no"], "C1")


class TestHeaderChassisSynonyms(unittest.TestCase):
    def test_vin_word_boundary(self) -> None:
        self.assertTrue(_header_cell_is_chassis_column("vin no."))
        self.assertFalse(_header_cell_is_chassis_column("engine"))


class TestInvoiceChallanFallback(unittest.TestCase):
    def test_single_repeated_invoice(self) -> None:
        grid = [
            ["Excise Invoice", "Frame No", "Engine No", "Material"],
            ["5V2605002394", "C1", "E1", "M1"],
            ["5V2605002394", "C2", "E2", "M2"],
        ]
        self.assertEqual(_challan_no_from_repeated_invoice(grid, 0), "5V2605002394")

    def test_distinct_invoices_returns_none(self) -> None:
        grid = [
            ["Excise Invoice", "Engine No", "Chassis No"],
            ["A", "E1", "C1"],
            ["B", "E2", "C2"],
        ]
        self.assertIsNone(_challan_no_from_repeated_invoice(grid, 0))


class TestParseSubdealerChallanMocked(unittest.TestCase):
    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    @patch("app.services.subdealer_challan_ocr_service._ist_now")
    def test_ist_default_date_when_no_scan_date(self, mock_ist, mock_tx) -> None:
        # Bottom @patch is _ist_now -> first arg; top @patch is extract_challan_textract -> second arg.
        mock_ist.return_value = datetime(2026, 5, 8, 15, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        grid = [
            ["Excise Invoice", "Frame No", "Engine No", "Material"],
            ["5V2605002394", "CHASS1", "ENG1", "MAT"],
        ]
        mock_tx.return_value = {
            "error": None,
            "full_text": "",
            "key_value_pairs": [],
            "tables": [grid],
        }
        out = parse_subdealer_challan(b"x", write_artifacts=False)
        self.assertEqual(out.get("error"), None)
        self.assertEqual(out["challan_date_iso"], "2026-05-08")
        self.assertEqual(out["challan_ddmmyyyy"], "08052026")
        self.assertEqual(out["challan_date_raw"], "08/05/2026")
        warns = " ".join(out.get("warnings") or [])
        self.assertIn("IST", warns)

    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    def test_challan_from_invoice_when_no_challan_text(self, mock_tx) -> None:
        grid = [
            ["Excise Invoice", "Frame No", "Engine No", "Material"],
            ["5V2605002394", "C1", "E1", "M1"],
            ["5V2605002394", "C2", "E2", "M2"],
        ]
        mock_tx.return_value = {
            "error": None,
            "full_text": "",
            "key_value_pairs": [],
            "tables": [grid],
        }
        with patch("app.services.subdealer_challan_ocr_service._ist_now") as mock_ist:
            mock_ist.return_value = datetime(2026, 1, 2, 10, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
            out = parse_subdealer_challan(b"x", write_artifacts=False)
        self.assertEqual(out["challan_no"], "5V2605002394")
        self.assertEqual(len(out["lines"]), 2)


if __name__ == "__main__":
    unittest.main()
