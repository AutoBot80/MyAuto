"""
Local test wrapper: open Hero DMS (same login path as Fill DMS), then run ``print_hero_dms_forms``
(Form22 download via My Orders → Report(s)).

Configure ``backend/.env`` (``DMS_BASE_URL``, ``DMS_MODE``, ``DMS_LOGIN_*``, etc.) like staging / Fill DMS.

Double-click ``test_DMS_form_downloads.bat`` or run:
  python test_DMS_form_downloads.py
from this folder (repo root must be the parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ``backend/`` on sys.path so ``import app`` works when this file is run from ``Testing Wrappers/``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_DMS_form_downloads")

# Same field family as staging ``payload_json`` → ``build_dms_fill_row`` → ``mobile_phone`` (10-digit).
mobile_num = "7062237798"


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        dms_automation_is_real_siebel,
    )
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import get_or_open_site_page
    from app.services.hero_dms_playwright_invoice import print_hero_dms_forms

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set — add it to backend/.env (same as Fill DMS).")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real / siebel / live / production / hero for this wrapper.")
        return 1

    # Optional: pin a specific Order# (digits); otherwise My Orders grid supplies primary_order.
    order_number = (os.getenv("FORM22_ORDER_NUMBER") or "").strip()

    page, open_err = get_or_open_site_page(
        (DMS_BASE_URL or "").strip(),
        "DMS",
        require_login_on_open=True,
    )
    if page is None:
        logger.error("Could not open DMS: %s", open_err)
        return 1

    _install_playwright_js_dialog_handler(page)

    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
    ok, err = print_hero_dms_forms(
        page,
        mobile=mobile_num,
        order_number=order_number,
        action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
        content_frame_selector=frame_sel,
        note=lambda m: logger.info("%s", m),
    )
    if not ok:
        logger.error("print_hero_dms_forms failed: %s", err)
        return 1

    logger.info("Form22 download finished (check Downloads folder or log path above).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
