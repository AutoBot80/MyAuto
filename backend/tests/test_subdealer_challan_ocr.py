"""Unit tests for subdealer challan OCR helpers (no AWS calls)."""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.subdealer_challan_ocr_service import (
    _challan_no_from_repeated_invoice,
    _collect_vehicle_lines_from_tables,
    _dominant_invoice_from_tokens,
    _find_engine_chassis_table,
    _find_loose_model_details_table,
    _header_cell_is_chassis_column,
    _invoice_from_table_column_zero,
    _parse_vertical_model_details_lines,
    _rows_from_table,
    _rows_from_table_merged_headers,
    dedupe_challan_lines,
    dedupe_raw_challan_lines,
    generate_default_challan_no,
    parse_challan_date_to_iso,
    parse_subdealer_challan,
    save_challan_scan_file,
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

    def test_collect_lines_from_header_table_plus_continuation(self) -> None:
        """Page 1 with headers + page 2 continuation without headers (multi-page PDF)."""
        page1 = [
            ["Frame No", "Engine No", "Material"],
            ["MBLHAW520THE03790", "HA11F6THE78918", "HSPLMTSSCFIRBK"],
            ["MBLHAW520THE04065", "HA11F6THE01808", "HSPLMTSSCFIRBK"],
        ]
        page2 = [
            ["MBLHAW523THE03766", "HA11F6THE78863", "HSPLMTSSCFIRBK"],
            ["MBLHAW523THE04030", "HA11F6THE78856", "HSPLMTSSCFITGB"],
        ]
        lines, used_strict, used_loose = _collect_vehicle_lines_from_tables([page1, page2])
        self.assertTrue(used_strict)
        self.assertTrue(used_loose)
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[-1]["chassis_no"], "MBLHAW523THE04030")

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


class TestLooseMergedModelDetailsTable(unittest.TestCase):
    """Textract TABLE with split headers (Frame vs No) — strict header match fails; loose path uses data rows."""

    def test_merged_header_grid(self) -> None:
        table = [
            ["Model Details Table", "", "", ""],
            ["Excise Invoice", "Frame", "", ""],
            ["5V2605002394 the", "No", "Engine No", "Material"],
            ["5V2605002394", "MBLHAW487T5E82253", "HA11F7T5E54973", "HSPLMDRSCFIBHG"],
            ["5V2605002394 to", "MBLHAW487T5E03681", "HA11F7T5E05296", "HSPLMDRSCFIRPB"],
            ["5V2605002394", "MBLHAW488T5E03723", "HA11F7T5E05556", "HSPLMDRSCFIBHG"],
            ["SV2605002394", "MBLHAW480T5E50020", "HA11F7T5E54770", "HSPLMDRSCFIBHG"],
            ["", "MBLHAW489T5E82271", "HA11F7T5E55099", "HSPLMDRSCFIBHG"],
        ]
        loose = _find_loose_model_details_table([table])
        self.assertIsNotNone(loose)
        grid, sr, ei, ci = loose
        self.assertEqual(sr, 3)
        rows = _rows_from_table_merged_headers(grid, sr, ei, ci)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["chassis_no"], "MBLHAW487T5E82253")
        self.assertEqual(rows[-1]["chassis_no"], "MBLHAW489T5E82271")
        inv = _invoice_from_table_column_zero(grid, sr)
        self.assertEqual(inv, "5V2605002394")


class TestVerticalTextractLineLayout(unittest.TestCase):
    """Textract LINE order: one printed cell per LINE (column-major), as in dealer Raw_OCR samples."""

    def test_pairs_mb_frame_with_ha_engine(self) -> None:
        text = """Model Details Table
Excise Invoice
5V2605002394
Frame No
Engine No
Material
5V2605002394
MBLHAW487T5E82253
HA11F7T5E54973
HSPLMDRSCFIBHG
5V2605002394
to
MBLHAW487T5E03681
HA11F7T5E05296
HSPLMDRSCFIRPB
the
MBLHAW488T5E03690
5V2605002394
HA11F7T5E05373
HSPLMDRSCFIRPB
SV2605002394
MBLHAW480T5E50020
HA11F7T5E54770
HSPLMDRSCFIBHG
"""
        lines, inv = _parse_vertical_model_details_lines(text)
        self.assertEqual(len(lines), 4)
        self.assertEqual(inv, "5V2605002394")
        self.assertEqual(lines[0]["chassis_no"], "MBLHAW487T5E82253")
        self.assertEqual(lines[0]["engine_no"], "HA11F7T5E54973")


class TestDominantInvoice(unittest.TestCase):
    def test_majority_wins_on_ocr_variant(self) -> None:
        invs = ["5V2605002394"] * 10 + ["SV2605002394"] * 2
        self.assertEqual(_dominant_invoice_from_tokens(invs), "5V2605002394")


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

    def test_hybrid_header_row_finds_invoice_column(self) -> None:
        """Excise label on row above ``<id> | Frame No | Engine No`` (Textract split layout)."""
        inv = "3U2603006451"
        grid = [
            ["Model Details Table", "", "", ""],
            ["Excise Invoice", "", "", ""],
            [inv, "Frame No", "Engine No", "Material"],
            [inv, "MBLHAW332THE08873", "HA11FBTHE09177", "HSPPLHRSCFIBHG"],
            [inv, "MBLHAW334THE07594", "HA11FBTHE08084", "HSPPLHRSCFIBHG"],
            [inv, "MBLHAW335THE08897", "HA11FBTHE09217", "HSPPLHRSCFIBHG"],
        ]
        self.assertEqual(_challan_no_from_repeated_invoice(grid, 2), inv)

    def test_mixed_full_and_truncated_invoice_majority(self) -> None:
        """Leading digit dropped on some rows; dominant token should be the full book number."""
        inv_full = "3U2603006451"
        inv_short = "U2603006451"
        body = [[inv_full, "MBLHAW332THE08873", "HA11FBTHE09177", "HSPPLHRSCFIBHG"]] * 8
        body += [
            [inv_short, "MBLHAW493THE02860", "HA11F9THE02913", "HSPLMTRSCFIRBK"],
            [inv_short, "MBLHAW495THE02519", "HA11F9THE02665", "HSPLMTRSCFIRBK"],
        ]
        grid = [
            ["Model Details Table", "", "", ""],
            ["Excise Invoice", "", "", ""],
            [inv_full, "Frame No", "Engine No", "Material"],
            *body,
        ]
        self.assertEqual(_challan_no_from_repeated_invoice(grid, 2), inv_full)


class TestParseSubdealerChallanMergedTable(unittest.TestCase):
    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    @patch("app.services.subdealer_challan_ocr_service._ist_now")
    def test_prefers_textract_table_loose_path(self, mock_ist, mock_tx) -> None:
        mock_ist.return_value = datetime(2026, 3, 1, 10, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        tbl = [
            ["Model Details Table", "", "", ""],
            ["Excise Invoice", "Frame", "", ""],
            ["5V2605002394 the", "No", "Engine No", "Material"],
            ["5V2605002394", "MBLHAW487T5E82253", "HA11F7T5E54973", "HSPLMDRSCFIBHG"],
            ["5V2605002394", "MBLHAW487T5E03681", "HA11F7T5E05296", "HSPLMDRSCFIRPB"],
        ]
        mock_tx.return_value = {
            "error": None,
            "full_text": "",
            "key_value_pairs": [],
            "tables": [tbl],
        }
        out = parse_subdealer_challan(b"x", write_artifacts=False)
        self.assertEqual(len(out["lines"]), 2)
        self.assertEqual(out["challan_no"], "5V2605002394")
        warns = " ".join(out.get("warnings") or [])
        self.assertIn("merged Model Details", warns)


class TestSaveChallanScanFile(unittest.TestCase):
    def test_save_scan_under_artifact_dir(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "0001AB12_15052026"
            scan = save_challan_scan_file(dest, b"%PDF-1.4", "delivery report.pdf")
            self.assertTrue(scan.is_file())
            self.assertEqual(scan.name, "delivery report.pdf")
            self.assertEqual(scan.read_bytes(), b"%PDF-1.4")


class TestDefaultChallanNo(unittest.TestCase):
    def test_prefix_from_dealer_id(self) -> None:
        with patch(
            "app.services.subdealer_challan_ocr_service.secrets.choice",
            side_effect=list("AB12"),
        ):
            self.assertEqual(generate_default_challan_no(100001), "0001AB12")

    def test_short_dealer_id_padded(self) -> None:
        with patch(
            "app.services.subdealer_challan_ocr_service.secrets.choice",
            side_effect=list("XY99"),
        ):
            self.assertEqual(generate_default_challan_no(42), "0042XY99")

    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    def test_parse_assigns_default_when_missing(self, mock_tx) -> None:
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
        with patch(
            "app.services.subdealer_challan_ocr_service.generate_default_challan_no",
            return_value="0001ZZ99",
        ):
            out = parse_subdealer_challan(
                b"x",
                write_artifacts=False,
                dealer_id=100001,
                assign_default_challan_no=True,
            )
        self.assertEqual(out["challan_no"], "5V2605002394")

    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    def test_parse_assigns_default_when_not_detected(self, mock_tx) -> None:
        mock_tx.return_value = {
            "error": None,
            "full_text": "Model Details Table\nFrame No\nEngine No\nCHASS1\nENG1",
            "key_value_pairs": [],
            "tables": [],
        }
        with patch(
            "app.services.subdealer_challan_ocr_service.generate_default_challan_no",
            return_value="0001ZZ99",
        ):
            out = parse_subdealer_challan(
                b"x",
                write_artifacts=False,
                dealer_id=100001,
                assign_default_challan_no=True,
            )
        self.assertEqual(out["challan_no"], "0001ZZ99")
        warns = " ".join(out.get("warnings") or [])
        self.assertIn("assigned default 0001ZZ99", warns)


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

    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    def test_parse_hybrid_model_details_table_book_number(self, mock_tx) -> None:
        """End-to-end: strict table match on hybrid header + dominant invoice (real OCR shape)."""
        inv = "3U2603006451"
        body_same = [
            [inv, "MBLHAW332THE08873", "HA11FBTHE09177", "HSPPLHRSCFIBHG"],
            [inv, "MBLHAW334THE07594", "HA11FBTHE08084", "HSPPLHRSCFIBHG"],
        ]
        body_same *= 4  # 8 rows with full book id (>=72% vs 2 truncated)
        body_mix = [
            ["U2603006451", "MBLHAW493THE02860", "HA11F9THE02913", "HSPLMTRSCFIRBK"],
            ["U2603006451", "MBLHAW495THE02519", "HA11F9THE02665", "HSPLMTRSCFIRBK"],
        ]
        tbl = [
            ["Model Details Table", "", "", ""],
            ["Excise Invoice", "", "", ""],
            [inv, "Frame No", "Engine No", "Material"],
            *body_same,
            *body_mix,
        ]
        mock_tx.return_value = {
            "error": None,
            "full_text": "",
            "key_value_pairs": [],
            "tables": [tbl],
        }
        with patch("app.services.subdealer_challan_ocr_service._ist_now") as mock_ist:
            mock_ist.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
            out = parse_subdealer_challan(b"x", write_artifacts=False)
        self.assertEqual(out.get("error"), None)
        self.assertEqual(out["challan_no"], inv)
        # Repeated MB/HA pairs dedupe to two rows; plus two distinct truncated-ID rows.
        self.assertEqual(len(out["lines"]), 4)


if __name__ == "__main__":
    unittest.main()
