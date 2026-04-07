"""
Hero DMS: download / print forms and reports after DB persistence (facade).

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
    action_timeout_ms: int,
    content_frame_selector: str | None,
    downloads_dir: Path,
    note: Callable[[str], None] | None = None,
) -> tuple[bool, str | None, list[str], dict[str, Any]]:
    """
    Download Hero DMS forms (e.g. Form 22) after successful master persistence.

    Returns the same tuple as ``print_hero_dms_forms``: ``(ok, error, paths, reports)``.
    """
    from app.services.hero_dms_playwright_invoice import print_hero_dms_forms

    return print_hero_dms_forms(
        page,
        mobile=mobile,
        order_number=order_number,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note or (lambda _m: None),
        downloads_dir=downloads_dir,
    )
