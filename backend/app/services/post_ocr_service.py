"""
Post-OCR: after Textract / ``process_uploaded_subfolder``, re-encode Aadhaar scans as JPEGs,
optionally shrink the sale folder document set to a total size budget, and move ``for_OCR/*``
artifacts into the sale root (outside ``for_OCR/``).

Environment:
  ``POST_OCR_MAX_TOTAL_BYTES`` — max combined size for managed root documents (default **200 MiB**).
"""

from __future__ import annotations

import io
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

FOR_OCR_SUBDIR = "for_OCR"
POST_OCR_MAX_TOTAL_BYTES = int(os.getenv("POST_OCR_MAX_TOTAL_BYTES", str(200 * 1024 * 1024)))
JPEG_START_QUALITY = 88
JPEG_MIN_QUALITY = 52
PDF_DPI = 200


def _pdf_first_page_to_jpeg_bytes(pdf_path: Path, dpi: int = PDF_DPI) -> bytes | None:
    """Rasterize first PDF page to JPEG bytes."""
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        if doc.page_count < 1:
            return None
        page = doc[0]
        pix = page.get_pixmap(dpi=dpi)
        from PIL import Image

        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("post_ocr: PDF→JPEG failed %s: %s", pdf_path, e)
        return None
    finally:
        doc.close()


def _jpeg_reencode(data: bytes, quality: int) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.mode != "RGB":
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def _compress_pdf_inplace(path: Path) -> None:
    import fitz

    try:
        doc = fitz.open(str(path))
        try:
            tmp = path.with_name(path.name + ".post_ocr_tmp")
            doc.save(
                str(tmp),
                garbage=4,
                deflate=True,
                clean=True,
            )
        finally:
            doc.close()
        os.replace(str(tmp), str(path))
    except Exception as e:
        logger.warning("post_ocr: PDF shrink failed %s: %s", path, e)
        try:
            tmp = path.with_name(path.name + ".post_ocr_tmp")
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        except OSError:
            pass


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


def _fit_documents_under_budget(managed: list[Path], actions: list[str]) -> None:
    """Re-encode JPEGs and deflate PDFs until combined size ≤ budget or limits hit."""
    total = _total_size(managed)
    if total <= POST_OCR_MAX_TOTAL_BYTES:
        actions.append(f"size_ok={total}B (<={POST_OCR_MAX_TOTAL_BYTES}B)")
        return

    quality = JPEG_START_QUALITY
    round_no = 0
    while total > POST_OCR_MAX_TOTAL_BYTES and round_no < 40:
        round_no += 1
        for p in list(managed):
            if not p.exists():
                continue
            suf = p.suffix.lower()
            try:
                if suf in (".jpg", ".jpeg"):
                    p.write_bytes(_jpeg_reencode(p.read_bytes(), quality))
                elif suf == ".png":
                    from PIL import Image

                    img = Image.open(io.BytesIO(p.read_bytes()))
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=quality, optimize=True)
                    new_p = p.with_suffix(".jpg")
                    new_p.write_bytes(buf.getvalue())
                    if new_p != p:
                        p.unlink(missing_ok=True)
                        if p in managed:
                            managed.remove(p)
                        if new_p not in managed:
                            managed.append(new_p)
                elif suf == ".pdf":
                    _compress_pdf_inplace(p)
            except Exception as e:
                logger.warning("post_ocr: compress failed %s: %s", p, e)

        total = _total_size(managed)
        actions.append(f"compress_round={round_no} q={quality} total={total}B")
        if total <= POST_OCR_MAX_TOTAL_BYTES:
            break
        quality -= 2
        if quality < JPEG_MIN_QUALITY:
            for p in list(managed):
                if p.exists() and p.suffix.lower() == ".pdf":
                    _compress_pdf_inplace(p)
            total = _total_size(managed)
            if total <= POST_OCR_MAX_TOTAL_BYTES:
                break
            logger.warning(
                "post_ocr: still over budget total=%sB max=%sB",
                total,
                POST_OCR_MAX_TOTAL_BYTES,
            )
            break


def run_post_ocr(uploads_dir: Path, subfolder: str) -> dict[str, object]:
    """
    1. Build fresh ``Aadhar.jpg`` / ``Aadhar_back.jpg`` at sale root from ``for_OCR/*.pdf`` or re-encode existing JPEGs.
    2. Move remaining ``for_OCR/*`` files into the sale root (not under ``for_OCR/``).
    3. If combined size of root document files exceeds the budget, recompress JPEGs and deflate PDFs.

    Does not modify ``raw/`` (archival PDFs).
    """
    sale_dir = uploads_dir / subfolder
    if not sale_dir.is_dir():
        return {"ok": False, "error": "sale_dir missing"}

    for_ocr = sale_dir / FOR_OCR_SUBDIR
    actions: list[str] = []

    def emit_aadhar(pdf_name: str, jpg_name: str) -> None:
        pdf_p = for_ocr / pdf_name
        jpg_p = sale_dir / jpg_name
        if pdf_p.is_file():
            b = _pdf_first_page_to_jpeg_bytes(pdf_p)
            if b:
                jpg_p.write_bytes(b)
                try:
                    pdf_p.unlink()
                except OSError:
                    pass
                actions.append(f"wrote {jpg_name} from {pdf_name}; removed {pdf_name}")
        elif jpg_p.is_file():
            jpg_p.write_bytes(_jpeg_reencode(jpg_p.read_bytes(), JPEG_START_QUALITY))
            actions.append(f"re-encoded {jpg_name}")

    emit_aadhar("Aadhar.pdf", "Aadhar.jpg")
    emit_aadhar("Aadhar_back.pdf", "Aadhar_back.jpg")

    if for_ocr.is_dir():
        for p in sorted(for_ocr.iterdir()):
            if not p.is_file():
                continue
            if p.name in ("Aadhar.pdf", "Aadhar_back.pdf"):
                continue
            dest = sale_dir / p.name
            try:
                if dest.exists() and dest.resolve() != p.resolve():
                    dest.unlink()
                shutil.move(str(p), str(dest))
                actions.append(f"moved {p.name} → sale root")
            except Exception as e:
                logger.warning("post_ocr: move failed %s → %s: %s", p, dest, e)

        try:
            if for_ocr.is_dir() and not any(for_ocr.iterdir()):
                for_ocr.rmdir()
                actions.append("removed empty for_OCR")
        except OSError:
            pass

    managed = _list_root_documents(sale_dir)
    total_before = _total_size(managed)
    _fit_documents_under_budget(managed, actions)
    total_after = _total_size(managed)
    over_budget = total_after > POST_OCR_MAX_TOTAL_BYTES
    if over_budget:
        logger.warning(
            "post_ocr: documents still over budget (%sB > %sB); .docx and other files are not lossy-compressed",
            total_after,
            POST_OCR_MAX_TOTAL_BYTES,
        )

    return {
        "ok": True,
        "total_bytes_before": total_before,
        "total_bytes_after": total_after,
        "max_bytes": POST_OCR_MAX_TOTAL_BYTES,
        "over_budget": over_budget,
        "actions": actions,
    }
