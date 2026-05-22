"""AWS Textract for subdealer Daily Delivery Report / challan scans — FORMS + TABLES via shared analyze path."""

from __future__ import annotations

import io
import logging
from typing import Any

from app.config import OCR_PRE_OCR_RASTER_DPI
from app.services.sales_textract_service import analyze_document_forms_and_tables

logger = logging.getLogger(__name__)

_TEXTRACT_MAX_BYTES = 5 * 1024 * 1024
# Sync AnalyzeDocument accepts single-page PDFs only; rasterize each page for multi-page uploads.
_CHALLAN_PDF_RASTER_DPI = min(200, OCR_PRE_OCR_RASTER_DPI)


def _is_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data[:4] == b"%PDF"


def _rasterize_pdf_page_to_jpeg_bytes(doc: Any, page_index: int, *, dpi: int = _CHALLAN_PDF_RASTER_DPI) -> bytes:
    """Rasterize one PDF page to JPEG bytes under Textract's 5 MB limit."""
    from PIL import Image

    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    for scale in (1.0, 0.85, 0.7, 0.55):
        cur = img
        if scale < 1.0:
            w, h = img.size
            cur = img.resize(
                (max(360, int(w * scale)), max(360, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        for quality in (92, 85, 75, 65):
            buf = io.BytesIO()
            cur.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _TEXTRACT_MAX_BYTES:
                return data

    buf = io.BytesIO()
    cur.save(buf, format="JPEG", quality=50, optimize=True)
    data = buf.getvalue()
    if len(data) > _TEXTRACT_MAX_BYTES:
        raise ValueError(f"Challan page {page_index + 1} JPEG still exceeds Textract size limit.")
    return data


def _pdf_multipage_jpegs_if_needed(document_bytes: bytes) -> list[bytes] | None:
    """
    When ``document_bytes`` is a multi-page PDF, return one JPEG per page.

    Returns ``None`` for non-PDF input or single-page PDF (caller passes bytes through to Textract).
    """
    if not _is_pdf_bytes(document_bytes):
        return None
    import fitz

    doc = fitz.open(stream=document_bytes, filetype="pdf")
    try:
        n = doc.page_count
        if n <= 1:
            return None
        return [_rasterize_pdf_page_to_jpeg_bytes(doc, i) for i in range(n)]
    finally:
        doc.close()


def merge_challan_textract_page_results(page_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-page Textract FORMS+TABLES results (multi-page challan PDF)."""
    if not page_results:
        return {
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
            "raw_response": None,
            "error": "No Textract page results.",
            "pages_processed": 0,
            "pages_failed": 0,
        }

    errors = [str(r["error"]) for r in page_results if r.get("error")]
    successful = [r for r in page_results if not r.get("error")]

    if not successful:
        return {
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
            "raw_response": None,
            "error": errors[0] if errors else "Textract failed on all pages.",
            "pages_processed": len(page_results),
            "pages_failed": len(errors),
        }

    full_text_parts: list[str] = []
    key_value_pairs: list[dict[str, str]] = []
    tables: list[Any] = []
    block_count = 0
    for r in successful:
        ft = (r.get("full_text") or "").strip()
        if ft:
            full_text_parts.append(ft)
        key_value_pairs.extend(r.get("key_value_pairs") or [])
        tables.extend(r.get("tables") or [])
        rr = r.get("raw_response") or {}
        block_count += int(rr.get("BlockCount") or 0)

    per_page = [
        {
            "full_text": (r.get("full_text") or "").strip(),
            "tables": r.get("tables") or [],
        }
        for r in successful
    ]

    return {
        "full_text": "\n".join(full_text_parts),
        "key_value_pairs": key_value_pairs,
        "tables": tables,
        "per_page": per_page,
        "raw_response": {
            "BlockCount": block_count,
            "PagesProcessed": len(page_results),
            "PagesSucceeded": len(successful),
        },
        "error": None,
        "pages_processed": len(page_results),
        "pages_failed": len(errors),
    }


def extract_challan_textract(document_bytes: bytes) -> dict[str, Any]:
    """
    Run Textract AnalyzeDocument (FORMS + TABLES) for a challan image/PDF.

    Multi-page PDFs are rasterized page-by-page (sync Textract rejects multi-page PDF bytes).
    Returns dict with full_text, key_value_pairs, tables, raw_response, error, and optional
    ``pages_processed`` / ``pages_failed`` when split.
    """
    page_jpegs = _pdf_multipage_jpegs_if_needed(document_bytes)
    if page_jpegs is None:
        result = analyze_document_forms_and_tables(document_bytes)
        if not result.get("error"):
            result["per_page"] = [
                {
                    "full_text": (result.get("full_text") or "").strip(),
                    "tables": result.get("tables") or [],
                }
            ]
        result.setdefault("pages_processed", 1 if not result.get("error") else 0)
        result.setdefault("pages_failed", 1 if result.get("error") else 0)
        return result

    logger.info("challan Textract: multi-page PDF split into %s page(s)", len(page_jpegs))
    page_results = [analyze_document_forms_and_tables(jpg) for jpg in page_jpegs]
    return merge_challan_textract_page_results(page_results)
