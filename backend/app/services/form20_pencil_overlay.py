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

_RASTER_STAMP_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"})


def _stamp_path_is_raster(stamp_path: Path) -> bool:
    return stamp_path.suffix.lower() in _RASTER_STAMP_SUFFIXES


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


def _place_stamp_top_right(page, pr, stamp_path: Path, max_w: float, max_h: float) -> None:
    """Top-right of page 1: raster stamps use ``insert_image``; PDF stamps use ``show_pdf_page``."""
    import fitz  # PyMuPDF

    if _stamp_path_is_raster(stamp_path):
        pix = fitz.Pixmap(str(stamp_path))
        try:
            iw, ih = pix.width, pix.height
            if iw < 1 or ih < 1:
                raise ValueError(f"stamp image empty: {stamp_path.name}")
            scale = min(max_w / iw, max_h / ih)
            dw, dh = iw * scale, ih * scale
            x1 = pr.width - _MARGIN_PT
            x0 = x1 - dw
            y0 = _MARGIN_PT
            tr = fitz.Rect(x0, y0, x0 + dw, y0 + dh)
            page.insert_image(tr, filename=str(stamp_path))
        finally:
            pix = None
        return

    src = fitz.open(str(stamp_path))
    try:
        if len(src) < 1:
            raise ValueError(f"stamp PDF has no pages: {stamp_path.name}")
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
        _place_stamp_top_right(page, pr, stamp_path, max_w, max_h)
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


def _existing_pencil_mark_ready(sale_dir: Path, *, min_bytes: int = 80) -> bool:
    """True when ``pencil_mark.*`` already exists (e.g. pulled from server) — skip re-crop from Details."""
    p = find_pencil_mark_file(sale_dir)
    if p is None:
        return False
    try:
        return p.is_file() and p.stat().st_size >= min_bytes
    except OSError:
        return False


def prepare_details_pencil_and_form20_overlay(sale_dir: Path) -> dict[str, object]:
    """
    **Electron / dealer PC (local sale folder):** crop the chassis pencil mark from the Details sheet
    (same logic as upload-time :func:`app.services.pre_ocr_service.try_write_pencil_mark_for_sale_folder`)
    into ``pencil_mark.jpeg``, then composite it onto **page 1 top-right** of the Form 20 PDF
    (writes ``{{Form20 stem}}_with_pencil_mark.pdf``). Run **after** dealer signature overlay so the
    stamped Form 20 on disk is the input to the composite.

    Non-fatal: missing Details/Form 20 yields empty ``form20_stamped_path`` without raising.
    """
    import re

    out: dict[str, object] = {
        "ok": True,
        "pencil_crop_written": False,
        "form20_stamped_path": None,
        "note": None,
    }
    if not sale_dir.is_dir():
        out["ok"] = False
        out["note"] = "sale_dir_missing"
        return out
    try:
        from app.services.pre_ocr_service import try_write_pencil_mark_for_sale_folder
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("prepare_details_pencil_and_form20_overlay: import pre_ocr_service: %s", exc)
        out["ok"] = False
        out["note"] = f"import_failed: {exc}"
        return out

    if _existing_pencil_mark_ready(sale_dir):
        out["pencil_crop_written"] = True
        out["note"] = "used_existing_pencil_mark"
    elif try_write_pencil_mark_for_sale_folder(sale_dir):
        out["pencil_crop_written"] = True

    m = re.match(r"^(\d{10})", sale_dir.name.strip())
    mob10 = m.group(1) if m else ""
    if len(mob10) != 10:
        out["note"] = "no_mobile_in_folder_name"
        return out

    stamped, fail_note = form20_pencil_overlay_write_only(sale_dir, mob10, inplace=True)
    out["form20_stamped_path"] = str(stamped) if stamped else None
    if fail_note and not stamped:
        out["note"] = fail_note
    return out


def form20_pencil_overlay_write_only(
    sale_dir: Path,
    mobile_10: str,
    *,
    inplace: bool = False,
) -> tuple[Path | None, str | None]:
    """
    Composite pencil mark onto Form 20 page 1 top-right.

    When ``inplace`` is True (Electron overlay path), updates the same Form 20 PDF that received
  dealer signatures. Otherwise writes ``{{stem}}_with_pencil_mark.pdf`` beside the original.

    Returns ``(stamped_path_or_none, failure_note_or_none)``.
    """
    import os

    try:
        import fitz  # noqa: F401
    except ImportError:
        logger.warning("form20_pencil_overlay: PyMuPDF (fitz) not installed; skipping overlay")
        return None, "no_fitz"

    form20 = find_form20_pdf(sale_dir, mobile_10)
    if form20 is None:
        logger.info(
            "form20_pencil_overlay: Form 20 PDF not found under %s; skipping overlay",
            sale_dir,
        )
        return None, "form20_missing"

    pencil = find_pencil_mark_file(sale_dir)
    if pencil is None:
        logger.info(
            "form20_pencil_overlay: optional pencil-mark file not present; skipping stamp"
        )
        return None, "pencil_mark_missing"

    if inplace:
        out = form20.with_name(form20.name + ".pencil_tmp.pdf")
    else:
        out = sale_dir / f"{form20.stem}_with_pencil_mark.pdf"
    try:
        composite_form20_first_page_with_stamp(form20, pencil, out)
    except Exception as exc:
        logger.warning("form20_pencil_overlay: composite failed: %s", exc)
        out.unlink(missing_ok=True)
        return None, f"composite_failed: {exc}"

    if not out.is_file():
        return None, "output_missing"

    if inplace:
        try:
            os.replace(str(out), str(form20))
            return form20, None
        except OSError as exc:
            logger.warning("form20_pencil_overlay: replace in place failed: %s", exc)
            out.unlink(missing_ok=True)
            return None, f"replace_failed: {exc}"

    return out, None
