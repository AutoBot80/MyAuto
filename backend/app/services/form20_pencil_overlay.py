"""
Overlay a *pencil mark* asset on the top-right of page 1 of the Form 20 PDF in a sale folder, then dispatch print/open.

Expects a file whose name contains both ``pencil`` and ``mark`` (case-insensitive), and a Form 20 PDF
(typically ``{mobile}_Form_20.pdf`` from DMS Run Report, or any ``*form*20*.pdf`` in the folder).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.hero_dms_playwright_invoice import _mobile_report_pdf_filename
from app.services.upload_scans_pdf_dispatch import dispatch_local_pdf

logger = logging.getLogger(__name__)

# Top-right stamp: max width/height as fraction of page; margin in points (1 pt = 1/72 inch).
_STAMP_MAX_W_FRAC = 0.28
_STAMP_MAX_H_FRAC = 0.35
_MARGIN_PT = 24.0


def find_pencil_mark_file(sale_dir: Path) -> Path | None:
    """Optional asset: first file whose name contains both ``pencil`` and ``mark`` (case-insensitive)."""
    if not sale_dir.is_dir():
        return None
    for p in sorted(sale_dir.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if "pencil" in low and "mark" in low:
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".webp", ".gif", ".tif", ".tiff"):
                return p
    return None


def find_form20_pdf(sale_dir: Path, mobile_10: str) -> Path | None:
    """Prefer DMS naming ``{mobile}_Form_20.pdf``; else first ``*form*20*.pdf`` excluding obvious non-Form20 names."""
    exp = sale_dir / _mobile_report_pdf_filename(mobile_10, "Form 20")
    if exp.is_file():
        return exp
    for p in sorted(sale_dir.glob("*.pdf"), key=lambda x: x.name.lower()):
        low = p.name.lower()
        if "with_pencil_mark" in low:
            continue
        if any(x in low for x in ("gst", "invoice_details", "booking_receipt", "retail_invoice")):
            continue
        if "gate" in low and "pass" in low:
            continue
        if "form" in low and "20" in low:
            return p
    return None


def composite_form20_first_page_with_stamp(form20_pdf: Path, stamp_path: Path, out_pdf: Path) -> None:
    """Draw ``stamp_path`` on the top-right of page 0 of ``form20_pdf``; write ``out_pdf``."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(form20_pdf))
    try:
        if len(doc) < 1:
            raise ValueError("Form 20 PDF has no pages")
        page = doc[0]
        pr = page.rect
        max_w = min(140.0, pr.width * _STAMP_MAX_W_FRAC)
        max_h = pr.height * _STAMP_MAX_H_FRAC

        src = None
        try:
            src = fitz.open(str(stamp_path))
        except Exception:
            src = None
        if src is not None and len(src) < 1:
            src.close()
            src = None
        if src is not None:
            try:
                sr = src[0].rect
                sw, sh = sr.width, sr.height
                scale = min(max_w / sw, max_h / sh)
                dw, dh = sw * scale, sh * scale
                x1 = pr.width - _MARGIN_PT
                x0 = x1 - dw
                y0 = _MARGIN_PT
                tr = fitz.Rect(x0, y0, x0 + dw, y0 + dh)
                page.show_pdf_page(tr, src, 0)
            finally:
                src.close()
        else:
            pix = fitz.Pixmap(str(stamp_path))
            try:
                iw, ih = pix.width, pix.height
                scale = min(max_w / iw, max_h / ih)
                dw, dh = iw * scale, ih * scale
                x1 = pr.width - _MARGIN_PT
                x0 = x1 - dw
                y0 = _MARGIN_PT
                tr = fitz.Rect(x0, y0, x0 + dw, y0 + dh)
                page.insert_image(tr, filename=str(stamp_path))
            finally:
                pass

        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out_pdf), garbage=4, deflate=True)
    finally:
        doc.close()


def form20_pencil_overlay_and_dispatch(sale_dir: Path, mobile_10: str) -> None:
    """
    If Form 20 and an optional pencil-mark file exist, composite and run :func:`dispatch_local_pdf` on the result.

    Missing pencil-mark file is normal — no error, no warning; the stamp step is skipped.
    """
    try:
        import fitz  # noqa: F401
    except ImportError:
        logger.warning("form20_pencil_overlay: PyMuPDF (fitz) not installed; skipping overlay")
        return

    form20 = find_form20_pdf(sale_dir, mobile_10)
    if form20 is None:
        logger.info(
            "form20_pencil_overlay: Form 20 PDF not found under %s; skipping overlay",
            sale_dir,
        )
        return

    pencil = find_pencil_mark_file(sale_dir)
    if pencil is None:
        logger.info(
            "form20_pencil_overlay: optional pencil-mark file not present (name should contain "
            "'pencil' and 'mark'); skipping stamp — Create Invoice continues normally"
        )
        return

    out = sale_dir / f"{form20.stem}_with_pencil_mark.pdf"
    try:
        composite_form20_first_page_with_stamp(form20, pencil, out)
    except Exception as exc:
        logger.warning("form20_pencil_overlay: composite failed: %s", exc)
        return

    if not out.is_file():
        logger.warning("form20_pencil_overlay: output missing: %s", out)
        return

    dispatch_local_pdf(out)


def form20_pencil_overlay_write_only(sale_dir: Path, mobile_10: str) -> Path | None:
    """
    Same as :func:`form20_pencil_overlay_and_dispatch` but does not print/open; returns the stamped PDF path if written.
    Used when ``STORAGE_USE_S3`` — caller syncs to S3 and returns presigned URLs to the Electron client.
    """
    try:
        import fitz  # noqa: F401
    except ImportError:
        logger.warning("form20_pencil_overlay: PyMuPDF (fitz) not installed; skipping overlay")
        return None

    form20 = find_form20_pdf(sale_dir, mobile_10)
    if form20 is None:
        logger.info(
            "form20_pencil_overlay: Form 20 PDF not found under %s; skipping overlay",
            sale_dir,
        )
        return None

    pencil = find_pencil_mark_file(sale_dir)
    if pencil is None:
        logger.info(
            "form20_pencil_overlay: optional pencil-mark file not present; skipping stamp"
        )
        return None

    out = sale_dir / f"{form20.stem}_with_pencil_mark.pdf"
    try:
        composite_form20_first_page_with_stamp(form20, pencil, out)
    except Exception as exc:
        logger.warning("form20_pencil_overlay: composite failed: %s", exc)
        return None

    return out if out.is_file() else None
