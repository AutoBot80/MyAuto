"""
Post-OCR: after Textract / ``process_uploaded_subfolder``, move ``for_OCR/*`` to the sale root
and compress each upload-facing document to a per-file size limit (default **200 KB**).

Environment:
  ``POST_OCR_MAX_FILE_BYTES`` — max size per managed document at sale root (default **204800**).
"""

from __future__ import annotations

import io
import logging
import os
import time
import shutil
from pathlib import Path

from app.services.page_classifier import FILENAME_AADHAR_FRONT

logger = logging.getLogger(__name__)

FOR_OCR_SUBDIR = "for_OCR"
PENCIL_MARK_FILENAME = "pencil_mark.jpeg"
POST_OCR_MAX_FILE_BYTES = int(os.getenv("POST_OCR_MAX_FILE_BYTES", str(200 * 1024)))
PDF_RASTER_DPI_MAX = 200
PDF_RASTER_DPI_MIN = 72


def _pdf_first_page_to_jpeg_bytes(pdf_path: Path, dpi: int = PDF_RASTER_DPI_MAX) -> bytes | None:
    """Rasterize first PDF page to JPEG bytes."""
    import fitz
    from PIL import Image

    doc = fitz.open(str(pdf_path))
    try:
        if doc.page_count < 1:
            return None
        page = doc[0]
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("post_ocr: PDF→JPEG failed %s: %s", pdf_path, e)
        return None
    finally:
        doc.close()


def _jpeg_bytes_to_single_page_pdf(jpeg_bytes: bytes) -> bytes:
    """Embed a JPEG as a single-page PDF (compressed)."""
    import fitz
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.width, img.height
    doc = fitz.open()
    try:
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=jpeg_bytes)
        out = io.BytesIO()
        doc.save(out, garbage=4, deflate=True)
        return out.getvalue()
    finally:
        doc.close()


def _jpeg_bytes_under_max(data: bytes, max_bytes: int) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.mode != "RGB":
        img = img.convert("RGB")
    cur = img
    for _ in range(14):
        q = 90
        while q >= 28:
            out = io.BytesIO()
            cur.save(out, format="JPEG", quality=q, optimize=True)
            b = out.getvalue()
            if len(b) <= max_bytes:
                return b
            q -= 3
        w, h = cur.size
        if w < 400 and h < 400:
            break
        cur = cur.resize(
            (max(360, w * 9 // 10), max(270, h * 9 // 10)),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    cur.save(out, format="JPEG", quality=25, optimize=True)
    return out.getvalue()


def _png_to_jpeg_bytes_under_max(data: bytes, max_bytes: int) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return _jpeg_bytes_under_max(buf.getvalue(), max_bytes)


def _pdf_file_under_max_bytes(pdf_path: Path, max_bytes: int) -> bytes:
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        buf = io.BytesIO()
        doc.save(buf, garbage=4, deflate=True, clean=True)
        b = buf.getvalue()
        if len(b) <= max_bytes:
            return b
    finally:
        doc.close()

    dpi = PDF_RASTER_DPI_MAX
    best = b
    while dpi >= PDF_RASTER_DPI_MIN:
        jb = _pdf_first_page_to_jpeg_bytes(pdf_path, dpi=dpi)
        if jb is None:
            break
        jb2 = _jpeg_bytes_under_max(jb, max_bytes)
        pb = _jpeg_bytes_to_single_page_pdf(jb2)
        if len(pb) <= max_bytes:
            return pb
        if len(pb) < len(best):
            best = pb
        dpi -= 24
    if len(best) > max_bytes:
        logger.warning(
            "post_ocr: PDF still over limit after shrink %s (%sB > %sB)",
            pdf_path.name,
            len(best),
            max_bytes,
        )
    return best


def _compress_file_to_max_bytes(src: Path, max_bytes: int) -> tuple[bytes, str]:
    """Return payload and destination basename (under sale root)."""
    suf = src.suffix.lower()
    if suf in (".jpg", ".jpeg"):
        return _jpeg_bytes_under_max(src.read_bytes(), max_bytes), src.name
    if suf == ".png":
        return _png_to_jpeg_bytes_under_max(src.read_bytes(), max_bytes), f"{src.stem}.jpg"
    if suf == ".pdf":
        return _pdf_file_under_max_bytes(src, max_bytes), src.name
    raise ValueError(f"unsupported type {suf}")


def _list_root_documents(sale_dir: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".pdf", ".docx"}
    return sorted(
        p for p in sale_dir.iterdir() if p.is_file() and p.suffix.lower() in exts
    )


def _total_size(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        try:
            if p.is_file():
                n += p.stat().st_size
        except OSError:
            continue
    return n


def _remove_redundant_for_ocr_pdfs(for_ocr: Path, actions: list[str]) -> None:
    """Drop ``Aadhar*.pdf`` in ``for_OCR`` when the corresponding JPEG is present."""
    skip: set[str] = set()
    if (for_ocr / FILENAME_AADHAR_FRONT).is_file():
        skip.add("Aadhar.pdf")
    if (for_ocr / "Aadhar_back.jpg").is_file():
        skip.add("Aadhar_back.pdf")
    for name in skip:
        p = for_ocr / name
        if p.is_file():
            try:
                p.unlink()
                actions.append(f"removed redundant {name} (JPEG present)")
            except OSError as e:
                logger.warning("post_ocr: unlink %s: %s", p, e)


def _compress_root_document_inplace(
    p: Path,
    max_bytes: int,
    actions: list[str],
) -> None:
    if p.name == PENCIL_MARK_FILENAME:
        return
    try:
        size = p.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    suf = p.suffix.lower()
    if suf == ".docx":
        logger.warning(
            "post_ocr: %s exceeds %sB; .docx not auto-compressed",
            p.name,
            max_bytes,
        )
        actions.append(f"warn_docx_oversize={p.name}")
        return
    try:
        data, dest_name = _compress_file_to_max_bytes(p, max_bytes)
        dest = p.parent / dest_name
        if dest != p and dest.exists():
            dest.unlink()
        if dest != p:
            p.unlink(missing_ok=True)
        dest.write_bytes(data)
        actions.append(f"compressed {dest_name} to {len(data)}B")
    except Exception as e:
        logger.warning("post_ocr: compress root file failed %s: %s", p, e)


def run_post_ocr(uploads_dir: Path, subfolder: str) -> dict[str, object]:
    """
    1. Remove redundant ``for_OCR`` PDFs when JPEGs exist; compress each remaining ``for_OCR`` file
       to ≤ ``POST_OCR_MAX_FILE_BYTES`` and write to the sale root (not under ``for_OCR/``).
    2. Remove empty ``for_OCR`` when possible.
    3. Enforce the same per-file limit on documents already at sale root (manual V2 uploads).

    Does not modify ``raw/`` (archival PDFs).
    """
    sale_dir = uploads_dir / subfolder
    if not sale_dir.is_dir():
        return {"ok": False, "error": "sale_dir missing"}

    for_ocr = sale_dir / FOR_OCR_SUBDIR
    actions: list[str] = []
    max_b = POST_OCR_MAX_FILE_BYTES

    if for_ocr.is_dir():
        _remove_redundant_for_ocr_pdfs(for_ocr, actions)
        for p in sorted(for_ocr.iterdir()):
            if not p.is_file():
                continue
            try:
                data, dest_name = _compress_file_to_max_bytes(p, max_b)
                dest = sale_dir / dest_name
                if dest.exists() and dest.resolve() != p.resolve():
                    dest.unlink()
                p.unlink(missing_ok=True)
                dest.write_bytes(data)
                actions.append(f"for_OCR→root {dest_name} ({len(data)}B)")
            except Exception as e:
                logger.warning("post_ocr: for_OCR file failed %s: %s", p, e)
                try:
                    dest = sale_dir / p.name
                    if dest.exists() and dest.resolve() != p.resolve():
                        dest.unlink()
                    shutil.move(str(p), str(dest))
                    actions.append(f"moved {p.name} → sale root (compress failed)")
                except Exception as e2:
                    logger.warning("post_ocr: move fallback failed %s: %s", p, e2)

        try:
            if for_ocr.is_dir() and not any(for_ocr.iterdir()):
                for_ocr.rmdir()
                actions.append("removed empty for_OCR")
        except OSError:
            pass

    managed = _list_root_documents(sale_dir)
    total_before = _total_size(managed)
    for p in managed:
        _compress_root_document_inplace(p, max_b, actions)
    managed_after = _list_root_documents(sale_dir)
    total_after = _total_size(managed_after)

    files_over: list[str] = []
    for p in managed_after:
        if p.name == PENCIL_MARK_FILENAME:
            continue
        try:
            if p.stat().st_size > max_b:
                files_over.append(p.name)
        except OSError:
            continue

    return {
        "ok": True,
        "total_bytes_before": total_before,
        "total_bytes_after": total_after,
        "max_file_bytes": max_b,
        "files_still_over_limit": files_over,
        "actions": actions,
    }


def run_deferred_post_ocr_for_sale(
    uploads_dir: Path,
    ocr_output_dir: Path,
    subfolder: str,
    dealer_id: int,
) -> None:
    """
    After ``process_uploaded_subfolder`` returns with post-OCR **deferred** (not blocking the HTTP
    response), run the same :func:`run_post_ocr` + S3 upload sync as the old synchronous path.

    ``dealer_id`` is used for ``sync_uploads_subfolder_to_s3`` / ``sync_ocr_subfolder_to_s3`` so
    Insurance and other steps see the **final** compressed, sale-root file layout.
    """
    from app.services.dealer_storage import sync_ocr_subfolder_to_s3, sync_uploads_subfolder_to_s3
    from app.services.ocr_extraction_log import append_ocr_extraction_log

    t0 = time.perf_counter()
    try:
        post_ocr_result = run_post_ocr(uploads_dir, subfolder)
    except Exception as e:
        logger.exception("deferred post_ocr run_post_ocr failed subfolder=%s", subfolder)
        append_ocr_extraction_log(ocr_output_dir, subfolder, "post", f"deferred failed error={e!r}")
        return
    ms = int((time.perf_counter() - t0) * 1000)
    if post_ocr_result.get("ok") and "total_bytes_before" in post_ocr_result:
        append_ocr_extraction_log(
            ocr_output_dir,
            subfolder,
            "post",
            (
                f"deferred-ok post_ocr_ms={ms} "
                f"bytes_before={post_ocr_result.get('total_bytes_before')} "
                f"bytes_after={post_ocr_result.get('total_bytes_after')} "
                f"max_file_bytes={post_ocr_result.get('max_file_bytes')} "
                f"still_over={len(post_ocr_result.get('files_still_over_limit') or [])} "
                f"actions={len(post_ocr_result.get('actions') or [])}"
            ),
        )
    else:
        append_ocr_extraction_log(
            ocr_output_dir,
            subfolder,
            "post",
            f"deferred run_post_ocr failed result={post_ocr_result!r} ms={ms}",
        )
    try:
        sync_uploads_subfolder_to_s3(dealer_id, subfolder)
        sync_ocr_subfolder_to_s3(dealer_id, subfolder)
    except Exception:
        logger.exception("deferred S3 sync after post_ocr subfolder=%s", subfolder)
