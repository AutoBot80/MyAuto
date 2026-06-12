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
        if "with_pencil_mark" in low or "with_cover" in low:
            continue
        if "cover_page" in low.replace(" ", "_"):
            continue
        if any(x in low for x in ("gst", "invoice_details", "booking_receipt", "retail_invoice")):
            continue
        if "gate" in low and "pass" in low:
            continue
        if "form" in low and "20" in low:
            return p
    return None


def find_form20_cover_page(sale_dir: Path) -> Path | None:
    """Scanned Form 20 cover page promoted to sale root after pre-OCR."""
    from app.services.page_classifier import FILENAME_FORM_20_COVER

    if not sale_dir.is_dir():
        return None
    for name in (FILENAME_FORM_20_COVER, "Form_20_Cover_Page.jpeg"):
        p = sale_dir / name
        if p.is_file():
            return p
    return None


def build_form20_with_cover_pdf(sale_dir: Path, mobile_10: str) -> Path | None:
    """
    Merge optional scanned cover (page 1) with DMS Form 20 pages for RTO upload.

    Returns ``{mobile}_Form_20_with_cover.pdf`` when cover exists; else signed DMS Form 20 only.
    """
    import fitz

    dms = find_form20_pdf(sale_dir, mobile_10)
    cover = find_form20_cover_page(sale_dir)
    if dms is None:
        return None
    if cover is None:
        return dms

    merged_name = f"{mobile_10}_Form_20_with_cover.pdf"
    out = sale_dir / merged_name
    try:
        cover_mtime = cover.stat().st_mtime
        dms_mtime = dms.stat().st_mtime
        if out.is_file() and out.stat().st_mtime >= max(cover_mtime, dms_mtime):
            return out
    except OSError:
        pass

    merged = fitz.open()
    try:
        cover_suffix = cover.suffix.lower()
        if cover_suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
            cover_pix = fitz.Pixmap(str(cover))
            try:
                w, h = cover_pix.width, cover_pix.height
                page = merged.new_page(width=float(w), height=float(h))
                page.insert_image(page.rect, filename=str(cover))
            finally:
                cover_pix = None
        else:
            cover_doc = fitz.open(str(cover))
            try:
                merged.insert_pdf(cover_doc, from_page=0, to_page=-1)
            finally:
                cover_doc.close()

        dms_doc = fitz.open(str(dms))
        try:
            merged.insert_pdf(dms_doc, from_page=0, to_page=-1)
        finally:
            dms_doc.close()

        out.parent.mkdir(parents=True, exist_ok=True)
        merged.save(str(out), garbage=4, deflate=True)
    finally:
        merged.close()

    return out if out.is_file() else dms


def form20_with_cover_pdf_path(sale_dir: Path, mobile_10: str) -> Path:
    """Path to merged Form 20 + scanned cover PDF required before Vahan upload."""
    return sale_dir / f"{mobile_10}_Form_20_with_cover.pdf"


def is_form20_with_cover_ready(sale_dir: Path, mobile_10: str) -> bool:
    return form20_with_cover_pdf_path(sale_dir, mobile_10).is_file()


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
    Legacy hook after dealer signature overlay. Pencil mark is **no longer** stamped onto DMS Form 20;
    returns crop status only when ``pencil_mark.jpeg`` already exists or can be written from Details.
    """
    import re

    out: dict[str, object] = {
        "ok": True,
        "pencil_crop_written": False,
        "form20_stamped_path": None,
        "note": "pencil_on_form20_disabled",
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
        out["note"] = "pencil_crop_only_no_form20_stamp"

    m = re.match(r"^(\d{10})", sale_dir.name.strip())
    if not m:
        out["note"] = "no_mobile_in_folder_name"
    return out


def form20_pencil_overlay_write_only(
    sale_dir: Path,
    mobile_10: str,
    *,
    inplace: bool = False,
) -> tuple[Path | None, str | None]:
    """
    Disabled: pencil mark is no longer composited onto DMS Form 20 (cover page + RTO merge replace this flow).
    """
    _ = sale_dir, mobile_10, inplace
    logger.info("form20_pencil_overlay: pencil-on-Form-20 disabled; skipping")
    return None, "pencil_on_form20_disabled"
