"""
Local handling of PDFs under ``Uploaded scans/<dealer_id>/<subfolder>/``.

When ``ENVIRONMENT`` indicates production (``prod`` or ``production``, any casing, per
``ENVIRONMENT_IS_PRODUCTION`` in ``app.config``), PDFs are sent to the default system printer.
Otherwise the PDF is opened with the default viewer (dev / non-prod).

MISP **hero_insure** policy PDFs use :func:`schedule_misp_hero_post_pdf` so every saved policy also
opens the system print UI on the **operator workstation** (where Playwright wrote the file), even when
:data:`STORAGE_USE_S3` is true (S3 mirrors the same local path; generic :func:`dispatch_local_pdf` still
skips print on S3 for server-only paths that use presigned URLs).
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from app.config import ENVIRONMENT_IS_PRODUCTION, STORAGE_USE_S3

logger = logging.getLogger(__name__)


def schedule_misp_hero_post_pdf(pdf_path: Path) -> None:
    """
    After a policy PDF is saved from MISP: open the **system print** UI on a background thread so the
    operator can print for the customer (or cancel), without blocking the API. Runs on the
    **workstation** that holds the saved file (even with ``STORAGE_BACKEND=s3`` / a mirror upload to
    S3). On Windows: ``os.startfile(…, "print")``; on other OSes, same prod/non-prod rules as
    :func:`_print_or_view_local_pdf_for_operator_workstation` (same prod / non-prod as :func:`dispatch_local_pdf`
    but S3 does not skip this path).
    """
    p = pdf_path.resolve()
    if not p.is_file() or p.suffix.lower() != ".pdf":
        return

    if platform.system() == "Windows":

        def _win_print() -> None:
            try:
                # Brief pause so the PDF is fully closed/released on disk (short scripts that exit
                # immediately would otherwise risk racing with a daemon thread; see non-daemon join).
                time.sleep(0.15)
                # Opens the Windows print dialog for this file; user can print or cancel.
                os.startfile(str(p), "print")  # type: ignore[attr-defined]
            except OSError as exc:  # pragma: no cover
                logger.warning("schedule_misp_hero_post_pdf: Windows print: %s", exc)
                try:
                    _print_or_view_local_pdf_for_operator_workstation(p)
                except Exception as exc2:  # pragma: no cover
                    logger.warning("schedule_misp_hero_post_pdf: fallback: %s", exc2)

        # non-daemon: interpreter waits for this thread to finish on exit, so a one-shot test script
        # that returns right after save does not tear down the thread before startfile() runs
        threading.Thread(target=_win_print, daemon=False, name="misp_hero_post_print").start()
    else:
        def _misp_other_os() -> None:
            _print_or_view_local_pdf_for_operator_workstation(p)

        threading.Thread(target=_misp_other_os, daemon=False, name="misp_hero_post_print").start()


def _print_or_view_local_pdf_for_operator_workstation(p: Path) -> None:
    """
    Same production vs non-prod behavior as :func:`dispatch_local_pdf`, but **not** skipped when
    :data:`STORAGE_USE_S3` is true. MISP/Playwright always writes a real file on the same machine
    the operator uses; S3 is an additional copy, not a replacement for that local path.
    """
    p = p.resolve()
    if not p.is_file() or p.suffix.lower() != ".pdf":
        return
    try:
        if ENVIRONMENT_IS_PRODUCTION:
            _send_pdf_to_default_printer(p)
        else:
            _open_pdf_default_viewer(p)
    except Exception as exc:
        logger.warning("upload_scans_pdf_dispatch: operator print/view: %s: %s", p, exc)


def schedule_dispatch_local_pdf(pdf_path: Path) -> None:
    """Run :func:`dispatch_local_pdf` on a daemon thread so HTTP handlers return without waiting on print/UI."""

    if STORAGE_USE_S3:
        return

    def _run() -> None:
        try:
            dispatch_local_pdf(pdf_path)
        except Exception as exc:
            logger.warning("schedule_dispatch_local_pdf: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def dispatch_local_pdf(pdf_path: Path) -> None:
    """
    Non-prod: open PDF with the default application.
    Prod: send the file to the default printer (OS-specific).
    When ``STORAGE_USE_S3``, the API does not print on the server — use ``print_jobs`` presigned URLs in the client.
    """
    if STORAGE_USE_S3:
        return
    p = pdf_path.resolve()
    if not p.is_file():
        logger.warning("upload_scans_pdf_dispatch: file missing: %s", p)
        return
    if p.suffix.lower() != ".pdf":
        logger.warning("upload_scans_pdf_dispatch: not a PDF, skipping: %s", p)
        return
    try:
        if ENVIRONMENT_IS_PRODUCTION:
            _send_pdf_to_default_printer(p)
        else:
            _open_pdf_default_viewer(p)
    except Exception as exc:
        logger.warning("upload_scans_pdf_dispatch: failed for %s: %s", p, exc)


def _open_pdf_default_viewer(p: Path) -> None:
    import os

    system = platform.system()
    if system == "Windows":
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["open", str(p)], check=False, timeout=60)
    else:
        subprocess.run(["xdg-open", str(p)], check=False, timeout=60)


def _send_pdf_to_default_printer(p: Path) -> None:
    import os

    system = platform.system()
    if system == "Windows":
        os.startfile(str(p), "print")  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["lp", str(p)], check=False, timeout=120)
    else:
        subprocess.run(["lp", str(p)], check=False, timeout=120)
