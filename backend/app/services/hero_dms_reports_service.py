"""
Hero DMS: download forms and reports after DB persistence (facade).

# Physical printing / Electron ``print_jobs`` from DMS were deferred to Add Sales
# **Print Forms & Queue RTO** (ordered Sale Certificate → Insurance → Gate Pass).
# This module still only downloads Run Report PDFs via ``print_hero_dms_forms``.

Implementation remains in ``hero_dms_playwright_invoice.print_hero_dms_forms`` so Playwright
selectors stay in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page


def run_hero_dms_reports(
    page: Page,
    *,
    mobile: str,
    order_number: str,
    invoice_number: str = "",
    action_timeout_ms: int,
    content_frame_selector: str | None,
    downloads_dir: Path,
    note: Callable[[str], None] | None = None,
    execution_log_path: Path | str | None = None,
) -> tuple[bool, str | None, list[str], list[dict[str, Any]]]:
    """
    Download Hero DMS forms (e.g. Form 22) after successful master persistence.

    Returns the same tuple as ``print_hero_dms_forms``: ``(ok, error, paths, reports)``.

    ``execution_log_path``: optional ``Playwright_DMS_*.txt`` path; appends a
    ``run_hero_dms_reports`` section with download dir and per-report results.

    # Post-DMS auto-print / ``fill-forms`` ``print_jobs`` — see ``fill_forms_router`` (commented).
    """
    from app.services.hero_dms_playwright_invoice import print_hero_dms_forms

    # Downloads only; printing deferred to Print Forms button (see module docstring).
    return print_hero_dms_forms(
        page,
        mobile=mobile,
        order_number=order_number,
        invoice_number=invoice_number,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note or (lambda _m: None),
        downloads_dir=downloads_dir,
        execution_log_path=execution_log_path,
    )
