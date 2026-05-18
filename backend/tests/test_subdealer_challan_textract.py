"""Unit tests for subdealer challan Textract helpers (no live AWS)."""

import unittest
from unittest.mock import patch

from app.services.subdealer_challan_ocr_service import parse_subdealer_challan
from app.services.subdealer_challan_textract import (
    _pdf_multipage_jpegs_if_needed,
    extract_challan_textract,
    merge_challan_textract_page_results,
)


def _two_page_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    try:
        doc.new_page(width=612, height=792)
        doc.new_page(width=612, height=792)
        return doc.tobytes()
    finally:
        doc.close()


class TestMergeChallanTextractPageResults(unittest.TestCase):
    def test_merges_text_tables_and_kvps(self) -> None:
        merged = merge_challan_textract_page_results(
            [
                {
                    "error": None,
                    "full_text": "page one",
                    "key_value_pairs": [{"key": "A", "value": "1"}],
                    "tables": [[["h"], ["r1"]]],
                    "raw_response": {"BlockCount": 10},
                },
                {
                    "error": None,
                    "full_text": "page two",
                    "key_value_pairs": [{"key": "B", "value": "2"}],
                    "tables": [[["h2"], ["r2"]]],
                    "raw_response": {"BlockCount": 5},
                },
            ]
        )
        self.assertIsNone(merged["error"])
        self.assertEqual(merged["full_text"], "page one\npage two")
        self.assertEqual(len(merged["key_value_pairs"]), 2)
        self.assertEqual(len(merged["tables"]), 2)
        self.assertEqual(merged["pages_processed"], 2)
        self.assertEqual(merged["pages_failed"], 0)
        self.assertEqual(merged["raw_response"]["BlockCount"], 15)

    def test_partial_page_failure_still_merges(self) -> None:
        merged = merge_challan_textract_page_results(
            [
                {"error": None, "full_text": "ok", "key_value_pairs": [], "tables": []},
                {"error": "boom", "full_text": "", "key_value_pairs": [], "tables": []},
            ]
        )
        self.assertIsNone(merged["error"])
        self.assertEqual(merged["full_text"], "ok")
        self.assertEqual(merged["pages_failed"], 1)

    def test_all_pages_failed(self) -> None:
        merged = merge_challan_textract_page_results(
            [{"error": "e1", "full_text": "", "key_value_pairs": [], "tables": []}]
        )
        self.assertEqual(merged["error"], "e1")
        self.assertEqual(merged["full_text"], "")


class TestPdfMultipageSplit(unittest.TestCase):
    def test_single_page_pdf_not_split(self) -> None:
        import fitz

        doc = fitz.open()
        try:
            doc.new_page()
            single = doc.tobytes()
        finally:
            doc.close()
        self.assertIsNone(_pdf_multipage_jpegs_if_needed(single))

    def test_two_page_pdf_returns_two_jpegs(self) -> None:
        jpegs = _pdf_multipage_jpegs_if_needed(_two_page_pdf_bytes())
        self.assertIsNotNone(jpegs)
        assert jpegs is not None
        self.assertEqual(len(jpegs), 2)
        self.assertTrue(jpegs[0][:2] == b"\xff\xd8")
        self.assertTrue(jpegs[1][:2] == b"\xff\xd8")

    def test_jpeg_input_not_split(self) -> None:
        self.assertIsNone(_pdf_multipage_jpegs_if_needed(b"\xff\xd8\xff"))


class TestExtractChallanTextractMultipage(unittest.TestCase):
    @patch("app.services.subdealer_challan_textract.analyze_document_forms_and_tables")
    def test_multipage_pdf_calls_textract_per_page(self, mock_analyze) -> None:
        mock_analyze.side_effect = [
            {
                "error": None,
                "full_text": "p1",
                "key_value_pairs": [],
                "tables": [[["Frame No", "Engine No"], ["C1", "E1"]]],
                "raw_response": {"BlockCount": 1},
            },
            {
                "error": None,
                "full_text": "p2",
                "key_value_pairs": [],
                "tables": [[["Frame No", "Engine No"], ["C2", "E2"]]],
                "raw_response": {"BlockCount": 1},
            },
        ]
        out = extract_challan_textract(_two_page_pdf_bytes())
        self.assertIsNone(out["error"])
        self.assertEqual(mock_analyze.call_count, 2)
        self.assertEqual(out["pages_processed"], 2)
        self.assertIn("p1", out["full_text"])
        self.assertIn("p2", out["full_text"])
        self.assertEqual(len(out["tables"]), 2)

    @patch("app.services.subdealer_challan_textract.analyze_document_forms_and_tables")
    def test_single_page_pdf_passes_through(self, mock_analyze) -> None:
        import fitz

        doc = fitz.open()
        try:
            doc.new_page()
            single = doc.tobytes()
        finally:
            doc.close()
        mock_analyze.return_value = {
            "error": None,
            "full_text": "one",
            "key_value_pairs": [],
            "tables": [],
            "raw_response": {},
        }
        extract_challan_textract(single)
        mock_analyze.assert_called_once_with(single)


class TestParseMultipageWarning(unittest.TestCase):
    @patch("app.services.subdealer_challan_ocr_service.extract_challan_textract")
    def test_warning_when_pages_processed_gt_one(self, mock_tx) -> None:
        mock_tx.return_value = {
            "error": None,
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
            "pages_processed": 2,
            "pages_failed": 0,
        }
        out = parse_subdealer_challan(b"x", write_artifacts=False)
        warns = " ".join(out.get("warnings") or [])
        self.assertIn("Multi-page PDF (2 pages)", warns)
