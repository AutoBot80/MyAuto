"""Unit tests for subdealer challan OCR helpers (no AWS calls)."""

import unittest

from app.services.subdealer_challan_ocr_service import (
    _find_engine_chassis_table,
    _rows_from_table,
    dedupe_challan_lines,
    parse_challan_date_to_iso,
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


if __name__ == "__main__":
    unittest.main()
