"""
After Create Invoice (DMS Run Report) and after Generate Insurance, dispatch PDFs from the sale folder.

DMS: ``GST Retail Invoice`` and ``Sale Certificate`` — filenames from ``hero_dms_playwright_invoice._mobile_report_pdf_filename``.

Insurance: best-effort resolve a policy / insurance PDF in the same folder (saved by automation or operator).
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from app.config import get_uploads_dir
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
    """
    mob = _digits_10(mobile) or _mobile_from_subfolder(subfolder)
    if not mob:
        logger.info(
            "upload_scans_invoice_print: skip DMS dispatch (no 10-digit mobile): subfolder=%r",
            subfolder,
        )
        return
    base = get_uploads_dir(dealer_id) / subfolder
    saved_list = [Path(s).resolve() for s in (pdfs_saved or []) if s and str(s).strip()]
    for report in _DMS_REPORTS_AFTER_INVOICE:
        expected = base / _mobile_report_pdf_filename(mob, report)
        chosen: Path | None = None
        if expected.is_file():
            chosen = expected
        else:
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
                expected,
            )
            continue
        dispatch_local_pdf(chosen)

    try:
        from app.services.form20_pencil_overlay import form20_pencil_overlay_and_dispatch

        form20_pencil_overlay_and_dispatch(base, mob)
    except Exception as exc:
        # Missing optional pencil-mark file does not raise — only unexpected failures (e.g. corrupt PDF).
        logger.warning("upload_scans_invoice_print: Form 20 pencil overlay failed unexpectedly: %s", exc)


def dispatch_pdf_after_generate_insurance(dealer_id: int, subfolder: str) -> None:
    """
    Print or open the insurance policy PDF if present under the sale folder.
    Prefers ``<mobile>_Insurance.pdf``, then common names, then newest *insurance* / *proposal* / *policy* PDF.
    """
    base = get_uploads_dir(dealer_id) / subfolder
    if not base.is_dir():
        logger.info("upload_scans_invoice_print: insurance folder missing: %s", base)
        return
    mob = _mobile_from_subfolder(subfolder)

    if mob:
        for name in (f"{mob}_Insurance.pdf", f"{mob}_Insurance_Policy.pdf"):
            p = base / name
            if p.is_file():
                dispatch_local_pdf(p)
                return
    for name in ("Insurance.pdf", "Insurance_Policy.pdf", "Hero_Insurance.pdf", "MISP_Insurance.pdf"):
        p = base / name
        if p.is_file():
            dispatch_local_pdf(p)
            return

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
        return

    logger.info(
        "upload_scans_invoice_print: no insurance PDF found under %s (skipping dispatch)",
        base,
    )


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
    paths_copy = list(pdfs_saved) if pdfs_saved else None

    def _run() -> None:
        try:
            dispatch_pdfs_after_create_invoice(dealer_id, subfolder, mobile, paths_copy)
        except Exception as exc:
            logger.warning("schedule_dispatch_pdfs_after_create_invoice: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def schedule_dispatch_pdf_after_generate_insurance(dealer_id: int, subfolder: str) -> None:
    """Queue insurance PDF dispatch on a daemon thread (non-blocking for API/UI)."""

    def _run() -> None:
        try:
            dispatch_pdf_after_generate_insurance(dealer_id, subfolder)
        except Exception as exc:
            logger.warning("schedule_dispatch_pdf_after_generate_insurance: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
