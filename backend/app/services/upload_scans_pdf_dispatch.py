"""
Local handling of PDFs under ``Uploaded scans/<dealer_id>/<subfolder>/``.

When ``ENVIRONMENT`` is exactly ``prod`` (case-insensitive), PDFs are sent to the default system printer.
For any other value (including empty), the PDF is opened with the default viewer (dev / non-prod).
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def environment_is_strict_prod() -> bool:
    """True only when ``ENVIRONMENT`` is ``prod`` (any casing). Not ``production``."""
    return (os.getenv("ENVIRONMENT") or "").strip().lower() == "prod"


def schedule_dispatch_local_pdf(pdf_path: Path) -> None:
    """Run :func:`dispatch_local_pdf` on a daemon thread so HTTP handlers return without waiting on print/UI."""

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
    """
    p = pdf_path.resolve()
    if not p.is_file():
        logger.warning("upload_scans_pdf_dispatch: file missing: %s", p)
        return
    if p.suffix.lower() != ".pdf":
        logger.warning("upload_scans_pdf_dispatch: not a PDF, skipping: %s", p)
        return
    try:
        if environment_is_strict_prod():
            _send_pdf_to_default_printer(p)
        else:
            _open_pdf_default_viewer(p)
    except Exception as exc:
        logger.warning("upload_scans_pdf_dispatch: failed for %s: %s", p, exc)


def _open_pdf_default_viewer(p: Path) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["open", str(p)], check=False, timeout=60)
    else:
        subprocess.run(["xdg-open", str(p)], check=False, timeout=60)


def _send_pdf_to_default_printer(p: Path) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(str(p), "print")  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["lp", str(p)], check=False, timeout=120)
    else:
        subprocess.run(["lp", str(p)], check=False, timeout=120)
