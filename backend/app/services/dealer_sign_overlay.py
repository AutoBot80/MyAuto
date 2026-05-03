"""
Headless overlay: stamp dealer signature on Form 20 (page 1 only), GST Retail Invoice, and
Sale Certificate PDFs in a sale folder (GST / Sale: all pages).

Used from Electron before ``print-gate-pass``; failures are non-fatal for printing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Bottom-right stamp: max width/height as fraction of page; margin in points (1 pt = 1/72 inch).
_STAMP_MAX_W_FRAC = 0.28
_STAMP_MAX_H_FRAC = 0.35
# Side inset from right edge; bottom inset is larger so the stamp sits above typical page footers.
_MARGIN_SIDE_PT = 24.0
_MARGIN_BOTTOM_PT = 56.0


def _margin_side_pt() -> float:
    raw = (os.getenv("DEALER_SIGN_MARGIN_SIDE_PT") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _MARGIN_SIDE_PT


def _margin_bottom_pt() -> float:
    raw = (os.getenv("DEALER_SIGN_MARGIN_BOTTOM_PT") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _MARGIN_BOTTOM_PT


def knock_out_light_paper_background(src: Path, dst_png: Path) -> None:
    """
    Make near-white / cream scan backgrounds transparent so the stamp blends with white PDF pages.

    Uses luminance + low chroma to classify «paper» pixels without removing dark ink strokes.
    """
    import numpy as np
    from PIL import Image

    im = Image.open(src).convert("RGBA")
    arr = np.array(im, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("unsupported image shape")

    r = arr[..., 0].astype(np.float32)
    g = arr[..., 1].astype(np.float32)
    b = arr[..., 2].astype(np.float32)
    a_in = arr[..., 3] if arr.shape[2] >= 4 else np.full(r.shape, 255, dtype=np.uint8)

    luma = (0.299 * r + 0.587 * g + 0.114 * b)
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)

    luma_thr = float((os.getenv("DEALER_SIGN_KNOCKOUT_LUMA") or "230").strip() or "230")
    chroma_thr = float((os.getenv("DEALER_SIGN_KNOCKOUT_CHROMA") or "38").strip() or "38")

    paper = (luma >= luma_thr) & (chroma <= chroma_thr)
    arr[..., 3] = np.where(paper, np.uint8(0), a_in)

    Image.fromarray(arr, mode="RGBA").save(dst_png, format="PNG")


def _prepare_signature_image_for_overlay(signature_image: Path) -> tuple[Path, Path | None]:
    """
    Returns ``(path_to_use_for_fitz, temp_png_path_or_None_to_delete)``.
    """
    if (os.getenv("DEALER_SIGN_KNOCKOUT_DISABLED") or "").strip().lower() in ("1", "true", "yes"):
        return signature_image, None
    try:
        fd, name = tempfile.mkstemp(suffix=".png", prefix="dealer_sign_ko_")
        os.close(fd)
        dst = Path(name)
        try:
            knock_out_light_paper_background(signature_image, dst)
        except Exception:
            dst.unlink(missing_ok=True)
            raise

        import numpy as np
        from PIL import Image

        arr = np.asarray(Image.open(dst).convert("RGBA"))
        if arr.shape[2] >= 4 and not np.any(arr[..., 3] > 0):
            logger.warning("dealer_sign_overlay: knock-out removed entire image; using original")
            dst.unlink(missing_ok=True)
            return signature_image, None
        return dst, dst
    except Exception as exc:
        logger.warning("dealer_sign_overlay: knock-out skipped (%s); using original image", exc)
        return signature_image, None


def _digits_10(mobile: str) -> str | None:
    d = re.sub(r"\D", "", (mobile or "").strip())
    if len(d) < 10:
        return None
    return d[-10:]


def _mobile_from_subfolder(subfolder: str) -> str | None:
    m = re.match(r"^(\d{10})", (subfolder or "").strip())
    return m.group(1) if m else None


def overlay_signature_bottom_right_all_pages(
    src_pdf: Path,
    signature_image: Path,
    dst_pdf: Path,
    *,
    first_page_only: bool = False,
) -> None:
    """Draw ``signature_image`` on the bottom-right of ``src_pdf``; write ``dst_pdf``.

    When ``first_page_only`` is True (Form 20), only page 1 is stamped; otherwise every page.
    """
    import fitz  # PyMuPDF

    if not src_pdf.is_file():
        raise FileNotFoundError(str(src_pdf))
    if not signature_image.is_file():
        raise FileNotFoundError(str(signature_image))

    use_image, tmp_knockout = _prepare_signature_image_for_overlay(signature_image)
    try:
        pm = fitz.Pixmap(str(use_image))
        iw, ih = pm.width, pm.height
        if iw < 1 or ih < 1:
            raise ValueError("Invalid signature image dimensions")
        pm = None

        m_side = _margin_side_pt()
        m_bottom = _margin_bottom_pt()

        doc = fitz.open(str(src_pdf))
        try:
            if len(doc) < 1:
                raise ValueError("PDF has no pages")

            page_indices = [0] if first_page_only else range(len(doc))
            for i in page_indices:
                page = doc[i]
                pr = page.rect
                max_w = min(140.0, pr.width * _STAMP_MAX_W_FRAC)
                max_h = pr.height * _STAMP_MAX_H_FRAC
                scale = min(max_w / iw, max_h / ih)
                dw, dh = iw * scale, ih * scale
                x0 = pr.width - m_side - dw
                y0 = pr.height - m_bottom - dh
                tr = fitz.Rect(x0, y0, x0 + dw, y0 + dh)
                page.insert_image(tr, filename=str(use_image))

            dst_pdf.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(dst_pdf), garbage=4, deflate=True)
        finally:
            doc.close()
    finally:
        if tmp_knockout is not None:
            tmp_knockout.unlink(missing_ok=True)


def find_dealer_signature_file(dealer_id: int, candidate_dirs: list[Path]) -> Path | None:
    """First existing ``{dealer_id}_sign`` with .jpg / .jpeg (case variants)."""
    stem = f"{int(dealer_id)}_sign"
    exts = (".jpg", ".jpeg", ".JPG", ".JPEG")
    for base in candidate_dirs:
        if not base.is_dir():
            continue
        for ext in exts:
            p = base / f"{stem}{ext}"
            if p.is_file():
                return p
    return None


def signature_search_dirs_from_env() -> list[Path]:
    """
    Directories that may contain ``{dealer_id}_sign.jpg`` (Saathi **data root**, next to ``.env``).

    Uses ``SAATHI_BASE_DIR`` when set and the folder exists. On Windows, also tries ``D:\\Saath`` and
    ``D:\\Saathi`` so a typo install (``D:\\Saath\\Dealer Saathi``) is found even when ``.env`` / env
    still point at the wrong drive letter spelling.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    env_base = (os.getenv("SAATHI_BASE_DIR") or "").strip()
    if env_base:
        p = Path(env_base)
        if p.is_dir():
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(r)
    if platform.system() == "Windows":
        for raw in (r"D:\Saath", r"D:\Saathi"):
            p = Path(raw)
            if p.is_dir():
                r = p.resolve()
                if r not in seen:
                    seen.add(r)
                    out.append(r)
    return out


def _merged_signature_dirs(explicit: list[Path] | None) -> list[Path]:
    """Prefer caller-supplied dirs first, then :func:`signature_search_dirs_from_env` (deduped)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for part in list(explicit or []) + signature_search_dirs_from_env():
        if not part.is_dir():
            continue
        r = part.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _find_form20_pdf(sale_dir: Path, mobile_10: str) -> Path | None:
    from app.services.form20_pencil_overlay import find_form20_pdf

    return find_form20_pdf(sale_dir, mobile_10)


def _resolve_gst_and_sale_pdfs(sale_dir: Path, mobile_10: str | None) -> tuple[Path | None, Path | None]:
    from app.services.hero_dms_playwright_invoice import _mobile_report_pdf_filename

    gst: Path | None = None
    sale: Path | None = None

    if mobile_10:
        eg = sale_dir / _mobile_report_pdf_filename(mobile_10, "GST Retail Invoice")
        if eg.is_file():
            gst = eg
        es = sale_dir / _mobile_report_pdf_filename(mobile_10, "Sale Certificate")
        if es.is_file():
            sale = es

    if gst is None:
        for p in sorted(sale_dir.glob("*.pdf"), key=lambda x: x.name.lower()):
            low = p.name.lower()
            if "gst" in low and "retail" in low:
                gst = p
                break
    if sale is None:
        for p in sorted(sale_dir.glob("*.pdf"), key=lambda x: x.name.lower()):
            low = p.name.lower()
            if "sale" in low and "certificate" in low:
                sale = p
                break

    return gst, sale


def collect_pdfs_to_stamp(sale_dir: Path, subfolder: str, mobile_hint: str | None = None) -> list[tuple[Path, bool]]:
    """
    Ordered list: Form 20 (if any), GST (if any), Sale Certificate (if any).

    Each item is ``(path, first_page_only)``. Form 20 is stamped on **page 1 only**; GST and Sale
    Certificate use all pages.
    """
    mob = _digits_10(mobile_hint or "") or _mobile_from_subfolder(subfolder)
    mob_for_form20 = mob or "0000000000"

    seen: set[Path] = set()
    out: list[tuple[Path, bool]] = []

    f20 = _find_form20_pdf(sale_dir, mob_for_form20)
    if f20 and f20.is_file():
        r = f20.resolve()
        seen.add(r)
        out.append((r, True))

    gst, sale = _resolve_gst_and_sale_pdfs(sale_dir, mob)

    if gst and gst.is_file():
        r = gst.resolve()
        if r not in seen:
            seen.add(r)
            out.append((r, False))
    if sale and sale.is_file():
        r = sale.resolve()
        if r not in seen:
            out.append((r, False))

    return out


def _atomic_replace(src_tmp: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(src_tmp), str(final))


def apply_dealer_signatures_to_sale_folder(
    sale_dir: Path,
    dealer_id: int,
    signature_image: Path | None,
    *,
    candidate_dirs_for_signature: list[Path] | None = None,
) -> dict[str, object]:
    """
    Overlay dealer signature on Form 20 / GST / Sale Certificate PDFs if present.
    Writes atomically back to the same filenames.

    Returns a dict: ``ok`` (bool), ``stamped`` (list of names), ``skipped`` (optional str).
    """
    result: dict[str, object] = {"ok": True, "stamped": []}

    if not sale_dir.is_dir():
        result["skipped"] = "sale_dir_missing"
        logger.info("dealer_sign_overlay: sale dir not found: %s", sale_dir)
        return result

    sig = signature_image
    if sig is None:
        dirs = _merged_signature_dirs(candidate_dirs_for_signature)
        sig = find_dealer_signature_file(dealer_id, dirs)

    if sig is None or not sig.is_file():
        result["skipped"] = "no_signature_file"
        searched = _merged_signature_dirs(candidate_dirs_for_signature)
        logger.info(
            "dealer_sign_overlay: no signature file for dealer_id=%s (skipped). "
            "Put %s_sign.jpg next to .env under data root; searched: %s",
            dealer_id,
            dealer_id,
            [str(p) for p in searched] or "(no candidate dirs)",
        )
        return result

    leaf = sale_dir.name
    mob_hint = _mobile_from_subfolder(leaf)
    pdfs = collect_pdfs_to_stamp(sale_dir, leaf, mob_hint)

    if not pdfs:
        result["skipped"] = "no_pdfs"
        logger.info("dealer_sign_overlay: no Form 20 / GST / Sale PDFs in %s", sale_dir)
        return result

    stamped_names: list[str] = []

    try:
        import fitz  # noqa: F401
    except ImportError:
        logger.warning("dealer_sign_overlay: PyMuPDF not installed; skipping")
        result["skipped"] = "no_fitz"
        return result

    for pdf_path, first_page_only in pdfs:
        tmp = pdf_path.with_name(pdf_path.name + ".dealer_sign_tmp.pdf")
        try:
            try:
                overlay_signature_bottom_right_all_pages(
                    pdf_path, sig, tmp, first_page_only=first_page_only
                )
            except Exception as exc:
                logger.warning("dealer_sign_overlay: failed %s: %s", pdf_path.name, exc)
                continue
            if not tmp.is_file():
                logger.warning("dealer_sign_overlay: temp output missing: %s", tmp)
                continue
            try:
                _atomic_replace(tmp, pdf_path)
            except OSError as exc:
                logger.warning("dealer_sign_overlay: replace failed %s: %s", pdf_path.name, exc)
                tmp.unlink(missing_ok=True)
                continue
            stamped_names.append(pdf_path.name)
        finally:
            tmp.unlink(missing_ok=True)

    result["stamped"] = stamped_names
    logger.info("dealer_sign_overlay: stamped %s", stamped_names)
    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Headless dealer signature overlay on sale-folder PDFs.")
    p.add_argument("--sale-dir", required=True, help="Absolute path to Uploaded scans/{dealer_id}/{subfolder}/")
    p.add_argument("--dealer-id", required=True, type=int)
    p.add_argument("--signature", default="", help="Optional absolute path to signature JPEG/PNG")
    p.add_argument("--json", action="store_true", help="Print JSON result on stdout")
    args = p.parse_args(argv)

    sale_dir = Path(args.sale_dir).resolve()
    sig_path = Path(args.signature).resolve() if str(args.signature).strip() else None

    explicit: list[Path] = []
    base = os.getenv("SAATHI_BASE_DIR", "").strip()
    if base:
        explicit.append(Path(base))
    result = apply_dealer_signatures_to_sale_folder(
        sale_dir,
        int(args.dealer_id),
        sig_path if sig_path and sig_path.is_file() else None,
        candidate_dirs_for_signature=explicit if explicit else None,
    )

    if args.json:
        print(json.dumps(result, default=str))
    else:
        print("ok" if result.get("ok") else "fail", result.get("stamped"), result.get("skipped", ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
