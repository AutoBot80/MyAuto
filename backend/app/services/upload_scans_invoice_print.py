"""
After Create Invoice (DMS Run Report) and after Generate Insurance, dispatch PDFs from the sale folder.

DMS: ``GST Retail Invoice`` and ``Sale Certificate`` — filenames from ``hero_dms_playwright_invoice._mobile_report_pdf_filename``.

Insurance: best-effort resolve a policy / insurance PDF in the same folder (saved by automation or operator).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

from app.config import STORAGE_USE_S3, get_ocr_output_dir, get_uploads_dir
from app.services.dealer_storage import (
    presigned_ocr_get_by_rel_path,
    presigned_uploads_get_by_rel_path,
    sync_ocr_subfolder_to_s3,
    sync_uploads_subfolder_to_s3,
)
from app.services.hero_dms_playwright_invoice import _mobile_report_pdf_filename
from app.services.upload_scans_pdf_dispatch import dispatch_local_pdf

logger = logging.getLogger(__name__)

_DMS_REPORTS_AFTER_INVOICE = (
    "GST Retail Invoice",
    "Sale Certificate",
)


def _digits_10(mobile: str) -> str | None:
    d = re.sub(r"\D", "", (mobile or "").strip())
    if len(d) < 10:
        return None
    return d[-10:]


def _mobile_from_subfolder(subfolder: str) -> str | None:
    m = re.match(r"^(\d{10})", (subfolder or "").strip())
    return m.group(1) if m else None


def dispatch_pdfs_after_create_invoice(
    dealer_id: int,
    subfolder: str,
    mobile: str,
    pdfs_saved: list[str] | None = None,
) -> None:
    """
    Print or open GST Retail Invoice and Sale Certificate PDFs after a successful Create Invoice / DMS run.
    Resolves paths under ``Uploaded scans/<dealer_id>/<subfolder>/``.

    ``pdfs_saved`` should include paths from ``run_hero_dms_reports`` (returned on
    ``hero_dms_form22_print["paths"]`` in the DMS result) — ``result["pdfs_saved"]`` is often empty because
    Playwright stores downloads only on that object.
    """
    base = get_uploads_dir(dealer_id) / subfolder
    saved_list = [Path(s).resolve() for s in (pdfs_saved or []) if s and str(s).strip()]
    mob = _digits_10(mobile) or _mobile_from_subfolder(subfolder)
    if not mob and not saved_list:
        logger.info(
            "upload_scans_invoice_print: skip DMS dispatch (no 10-digit mobile and no saved paths): subfolder=%r",
            subfolder,
        )
        return
    for report in _DMS_REPORTS_AFTER_INVOICE:
        chosen: Path | None = None
        expected: Path | None = None
        if mob:
            expected = base / _mobile_report_pdf_filename(mob, report)
            if expected.is_file():
                chosen = expected
        if chosen is None:
            for p in saved_list:
                if not p.is_file():
                    continue
                low = p.name.lower()
                if report == "GST Retail Invoice" and "gst" in low and "retail" in low:
                    chosen = p
                    break
                if report == "Sale Certificate" and "sale" in low and "certificate" in low:
                    chosen = p
                    break
        if chosen is None:
            logger.info(
                "upload_scans_invoice_print: DMS PDF not found for %r (expected %s)",
                report,
                expected if expected is not None else f"(match from paths only; base={base})",
            )
            continue
        dispatch_local_pdf(chosen)

    if mob:
        try:
            from app.services.form20_pencil_overlay import form20_pencil_overlay_and_dispatch

            form20_pencil_overlay_and_dispatch(base, mob)
        except Exception as exc:
            # Missing optional pencil-mark file does not raise — only unexpected failures (e.g. corrupt PDF).
            logger.warning("upload_scans_invoice_print: Form 20 pencil overlay failed unexpectedly: %s", exc)


def _try_dispatch_insurance_pdf_from_dir(base: Path) -> bool:
    """Return True if a PDF was dispatched from ``base``."""
    if not base.is_dir():
        return False
    mob = _mobile_from_subfolder(str(base.name))

    if mob:
        for name in (f"{mob}_Insurance.pdf", f"{mob}_Insurance_Policy.pdf"):
            p = base / name
            if p.is_file():
                dispatch_local_pdf(p)
                return True
    for name in ("Insurance.pdf", "Insurance_Policy.pdf", "Hero_Insurance.pdf", "MISP_Insurance.pdf"):
        p = base / name
        if p.is_file():
            dispatch_local_pdf(p)
            return True

    skip_parts = ("gst_retail", "sale_certificate", "form22", "form_20", "booking_receipt", "invoice_details")
    scored: list[tuple[float, Path]] = []
    for p in base.glob("*.pdf"):
        low = p.name.lower()
        if any(sp in low.replace(" ", "_") for sp in skip_parts):
            continue
        if any(k in low for k in ("insurance", "proposal", "policy", "misp")):
            try:
                scored.append((p.stat().st_mtime, p))
            except OSError:
                continue
    if scored:
        dispatch_local_pdf(max(scored, key=lambda x: x[0])[1])
        return True
    return False


def dispatch_pdf_after_generate_insurance(dealer_id: int, subfolder: str) -> None:
    """
    Print or open the insurance policy PDF if present under the sale folder.
    Checks **Uploaded scans** and **ocr_output** (same ``<dealer_id>/<subfolder>`` leaf).

    Prefers ``<mobile>_Insurance.pdf``, then common names, then newest *insurance* / *proposal* / *policy* PDF.
    Retries briefly — downloads may finish just after the API returns.
    """
    uploads_base = get_uploads_dir(dealer_id) / subfolder
    ocr_base = get_ocr_output_dir(dealer_id) / subfolder

    for attempt in range(15):
        for folder in (uploads_base, ocr_base):
            if _try_dispatch_insurance_pdf_from_dir(folder):
                return
        time.sleep(0.35)

    logger.info(
        "upload_scans_invoice_print: no insurance PDF found under %s or %s (skipping dispatch)",
        uploads_base,
        ocr_base,
    )


def _print_job(filename: str, url: str, kind: str) -> dict:
    return {"filename": filename, "presigned_url": url, "kind": kind}


def collect_invoice_print_jobs_s3(
    dealer_id: int,
    subfolder: str,
    mobile: str,
    pdfs_saved: list[str] | None = None,
) -> list[dict]:
    """
    Sync sale folder to S3 and return presigned GET URLs for PDFs that would have been printed locally.
    Used when ``STORAGE_USE_S3`` — Electron downloads and prints on the dealer PC.
    """
    sync_uploads_subfolder_to_s3(dealer_id, subfolder)
    base = get_uploads_dir(dealer_id) / subfolder
    saved_list = [Path(s).resolve() for s in (pdfs_saved or []) if s and str(s).strip()]
    mob = _digits_10(mobile) or _mobile_from_subfolder(subfolder)
    jobs: list[dict] = []

    for report in _DMS_REPORTS_AFTER_INVOICE:
        chosen: Path | None = None
        expected: Path | None = None
        if mob:
            expected = base / _mobile_report_pdf_filename(mob, report)
            if expected.is_file():
                chosen = expected
        if chosen is None:
            for p in saved_list:
                if not p.is_file():
                    continue
                low = p.name.lower()
                if report == "GST Retail Invoice" and "gst" in low and "retail" in low:
                    chosen = p
                    break
                if report == "Sale Certificate" and "sale" in low and "certificate" in low:
                    chosen = p
                    break
        if chosen is None or not chosen.is_file():
            continue
        rel = f"{subfolder}/{chosen.name}"
        url = presigned_uploads_get_by_rel_path(dealer_id, rel)
        if url:
            kind = "gst_retail_invoice" if report == "GST Retail Invoice" else "sale_certificate"
            jobs.append(_print_job(chosen.name, url, kind))

    if mob:
        try:
            from app.services.form20_pencil_overlay import form20_pencil_overlay_write_only

            stamped = form20_pencil_overlay_write_only(base, mob)
            if stamped is not None and stamped.is_file():
                sync_uploads_subfolder_to_s3(dealer_id, subfolder)
                rel = f"{subfolder}/{stamped.name}"
                url = presigned_uploads_get_by_rel_path(dealer_id, rel)
                if url:
                    jobs.append(_print_job(stamped.name, url, "form20_pencil"))
        except Exception as exc:
            logger.warning("collect_invoice_print_jobs_s3: pencil overlay: %s", exc)

    return jobs


def collect_invoice_print_jobs_electron_local(
    dealer_id: int,
    subfolder: str,
    mobile: str,
    pdfs_saved: list[str] | None = None,
) -> list[dict]:
    """
    Same PDF selection as :func:`collect_invoice_print_jobs_s3`, but return absolute local paths
    in ``presigned_url`` for Electron to print without downloading from S3 (sidecar / dealer PC).
    """
    base = get_uploads_dir(dealer_id) / subfolder
    saved_list = [Path(s).resolve() for s in (pdfs_saved or []) if s and str(s).strip()]
    mob = _digits_10(mobile) or _mobile_from_subfolder(subfolder)
    jobs: list[dict] = []

    for report in _DMS_REPORTS_AFTER_INVOICE:
        chosen: Path | None = None
        expected: Path | None = None
        if mob:
            expected = base / _mobile_report_pdf_filename(mob, report)
            if expected.is_file():
                chosen = expected
        if chosen is None:
            for p in saved_list:
                if not p.is_file():
                    continue
                low = p.name.lower()
                if report == "GST Retail Invoice" and "gst" in low and "retail" in low:
                    chosen = p
                    break
                if report == "Sale Certificate" and "sale" in low and "certificate" in low:
                    chosen = p
                    break
        if chosen is None or not chosen.is_file():
            continue
        kind = "gst_retail_invoice" if report == "GST Retail Invoice" else "sale_certificate"
        jobs.append(_print_job(chosen.name, str(chosen.resolve()), kind))

    if mob:
        try:
            from app.services.form20_pencil_overlay import form20_pencil_overlay_write_only

            stamped = form20_pencil_overlay_write_only(base, mob)
            if stamped is not None and stamped.is_file():
                jobs.append(_print_job(stamped.name, str(stamped.resolve()), "form20_pencil"))
        except Exception as exc:
            logger.warning("collect_invoice_print_jobs_electron_local: pencil overlay: %s", exc)

    return jobs


def collect_insurance_print_jobs_electron_local(dealer_id: int, subfolder: str) -> list[dict]:
    """
    Same resolution as :func:`collect_insurance_print_jobs_s3`, but return a local path for Electron printing.
    """
    uploads_base = get_uploads_dir(dealer_id) / subfolder
    ocr_base = get_ocr_output_dir(dealer_id) / subfolder

    def _try_path(base: Path) -> dict | None:
        if not base.is_dir():
            return None
        mob = _mobile_from_subfolder(str(base.name))
        if mob:
            for name in (f"{mob}_Insurance.pdf", f"{mob}_Insurance_Policy.pdf"):
                p = base / name
                if p.is_file():
                    return {"path": p, "name": name}
        for name in ("Insurance.pdf", "Insurance_Policy.pdf", "Hero_Insurance.pdf", "MISP_Insurance.pdf"):
            p = base / name
            if p.is_file():
                return {"path": p, "name": name}
        skip_parts = ("gst_retail", "sale_certificate", "form22", "form_20", "booking_receipt", "invoice_details")
        scored: list[tuple[float, Path]] = []
        for p in base.glob("*.pdf"):
            low = p.name.lower()
            if any(sp in low.replace(" ", "_") for sp in skip_parts):
                continue
            if any(k in low for k in ("insurance", "proposal", "policy", "misp")):
                try:
                    scored.append((p.stat().st_mtime, p))
                except OSError:
                    continue
        if scored:
            best = max(scored, key=lambda x: x[0])[1]
            return {"path": best, "name": best.name}
        return None

    for attempt in range(15):
        for folder in (uploads_base, ocr_base):
            hit = _try_path(folder)
            if hit is None:
                continue
            p = hit["path"]
            return [_print_job(p.name, str(p.resolve()), "insurance")]
        time.sleep(0.35)

    return []


def collect_insurance_print_jobs_s3(dealer_id: int, subfolder: str) -> list[dict]:
    """After insurance automation, sync and return a presigned URL for the policy PDF if found."""
    sync_uploads_subfolder_to_s3(dealer_id, subfolder)
    sync_ocr_subfolder_to_s3(dealer_id, subfolder)
    uploads_base = get_uploads_dir(dealer_id) / subfolder
    ocr_base = get_ocr_output_dir(dealer_id) / subfolder

    def _try_path(base: Path) -> dict | None:
        if not base.is_dir():
            return None
        mob = _mobile_from_subfolder(str(base.name))
        if mob:
            for name in (f"{mob}_Insurance.pdf", f"{mob}_Insurance_Policy.pdf"):
                p = base / name
                if p.is_file():
                    return {"path": p, "name": name}
        for name in ("Insurance.pdf", "Insurance_Policy.pdf", "Hero_Insurance.pdf", "MISP_Insurance.pdf"):
            p = base / name
            if p.is_file():
                return {"path": p, "name": name}
        skip_parts = ("gst_retail", "sale_certificate", "form22", "form_20", "booking_receipt", "invoice_details")
        scored: list[tuple[float, Path]] = []
        for p in base.glob("*.pdf"):
            low = p.name.lower()
            if any(sp in low.replace(" ", "_") for sp in skip_parts):
                continue
            if any(k in low for k in ("insurance", "proposal", "policy", "misp")):
                try:
                    scored.append((p.stat().st_mtime, p))
                except OSError:
                    continue
        if scored:
            best = max(scored, key=lambda x: x[0])[1]
            return {"path": best, "name": best.name}
        return None

    for attempt in range(15):
        for folder, root in ((uploads_base, "uploads"), (ocr_base, "ocr")):
            hit = _try_path(folder)
            if hit is None:
                continue
            p = hit["path"]
            rel = f"{subfolder}/{p.name}"
            if root == "uploads":
                url = presigned_uploads_get_by_rel_path(dealer_id, rel)
            else:
                url = presigned_ocr_get_by_rel_path(dealer_id, rel)
            if url:
                return [_print_job(p.name, url, "insurance")]
        time.sleep(0.35)

    return []


def schedule_dispatch_pdfs_after_create_invoice(
    dealer_id: int,
    subfolder: str,
    mobile: str,
    pdfs_saved: list[str] | None = None,
) -> None:
    """
    Queue :func:`dispatch_pdfs_after_create_invoice` on a daemon thread so the HTTP response
    (and client UI) are not blocked by opening apps or printing.
    """
    if STORAGE_USE_S3:
        return

    paths_copy = list(pdfs_saved) if pdfs_saved else None

    def _run() -> None:
        try:
            dispatch_pdfs_after_create_invoice(dealer_id, subfolder, mobile, paths_copy)
        except Exception as exc:
            logger.warning("schedule_dispatch_pdfs_after_create_invoice: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def schedule_dispatch_pdf_after_generate_insurance(dealer_id: int, subfolder: str) -> None:
    """Queue insurance PDF dispatch on a daemon thread (non-blocking for API/UI)."""

    if STORAGE_USE_S3:
        return

    def _run() -> None:
        try:
            dispatch_pdf_after_generate_insurance(dealer_id, subfolder)
        except Exception as exc:
            logger.warning("schedule_dispatch_pdf_after_generate_insurance: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
