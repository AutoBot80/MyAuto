"""AWS Textract for subdealer Daily Delivery Report / challan scans — FORMS + TABLES via shared analyze path."""

from __future__ import annotations

from typing import Any

from app.services.sales_textract_service import analyze_document_forms_and_tables


def extract_challan_textract(document_bytes: bytes) -> dict[str, Any]:
    """
    Run Textract AnalyzeDocument (FORMS + TABLES) for a challan image/PDF.
    Returns dict with full_text, key_value_pairs, tables, raw_response, error.
    """
    return analyze_document_forms_and_tables(document_bytes)
