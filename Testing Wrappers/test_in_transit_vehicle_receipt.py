"""
Local test wrapper: open Hero DMS (same login path as Fill DMS), then run ``prepare_vehicle`` so the
full vehicle prep runs — Find→Vehicles (VIN + Engine), in-transit detection, HMCL **Vehicles Receipt /
In Transit** receipt when applicable, then Serial / Features / Pre-check / PDI after successful receipt.

Configure ``backend/.env`` (``DMS_BASE_URL``, ``DMS_MODE``, ``DMS_LOGIN_*``, ``DMS_REAL_URL_VEHICLE``,
``DMS_SIEBEL_*``, etc.) like staging / Fill DMS.

Default test partials (override with env)::

  TRANSIT_TEST_FRAME_PARTIAL   default MBLHAW47XTHE48605
  TRANSIT_TEST_ENGINE_PARTIAL  default HA11F6THE48477 (omit or leave empty for VIN-only Find — Engine# ``*``)

Frame dumps (``temp_frame_dump.txt``) when Siebel cannot activate receipt tabs or the Invoices jqGrid
is missing — under ``My Auto.AI/ocr_output/_in_transit_receipt_debug/<timestamp>/`` (override root with
``TRANSIT_TEST_DEBUG_DUMP_ROOT``).

Browser lifetime (same idea as ``test_challan_order_resume_my_orders``)::

  ``retain_automation_browser_for_operator_manual_close()`` in ``finally`` so Playwright does not tear
  down the headed window on exit.

  TRANSIT_TEST_PAUSE_BEFORE_EXIT   default 1 — wait for Enter before exiting (set 0 / false / no to skip).

  TRANSIT_TEST_POST_BROWSER_OPEN_WAIT_SEC   default 5 — seconds to sleep after the browser opens so
  the window and Siebel can finish loading before automation runs (set 0 to skip).

Double-click ``test_in_transit_vehicle_receipt.bat`` or run::

  python test_in_transit_vehicle_receipt.py

from this folder (repo root must be the parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_in_transit_vehicle_receipt")

_DEFAULT_FRAME = "MBLHAW47XTHE48605"
_DEFAULT_ENGINE = "HA11F6THE48477"


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_REAL_URL_VEHICLE,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        DMS_SIEBEL_NAV_TIMEOUT_MS,
        dms_automation_is_real_siebel,
    )
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_vehicle import prepare_vehicle
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set — add it to backend/.env (same as Fill DMS).")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real / siebel / live / production / hero for this wrapper.")
        return 1
    if not (DMS_REAL_URL_VEHICLE or "").strip():
        logger.error(
            "DMS_REAL_URL_VEHICLE is not set — add the Auto Vehicle List GotoView URL to backend/.env."
        )
        return 1

    _pause_before_exit = (os.getenv("TRANSIT_TEST_PAUSE_BEFORE_EXIT") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    try:
        post_open_wait = max(0.0, float(os.getenv("TRANSIT_TEST_POST_BROWSER_OPEN_WAIT_SEC") or "5"))
    except ValueError:
        post_open_wait = 5.0

    frame_p = (os.getenv("TRANSIT_TEST_FRAME_PARTIAL") or _DEFAULT_FRAME).strip()
    engine_p = (os.getenv("TRANSIT_TEST_ENGINE_PARTIAL") or _DEFAULT_ENGINE).strip()
    logger.info("Using frame_partial=%r engine_partial=%r", frame_p, engine_p or "(empty — VIN-only Find)")

    _dump_root = Path(
        (os.getenv("TRANSIT_TEST_DEBUG_DUMP_ROOT") or "").strip()
        or str(_REPO_ROOT / "ocr_output" / "_in_transit_receipt_debug")
    )
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dump_dir = _dump_root / run_stamp
    debug_dump_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Siebel frame dumps on receipt/tab failures → %s", debug_dump_dir)

    exit_code = 1
    try:
        page, open_err = get_or_open_site_page(
            (DMS_BASE_URL or "").strip(),
            "DMS",
            require_login_on_open=True,
        )
        if page is None:
            logger.error("Could not open DMS: %s", open_err)
            exit_code = 1
            return exit_code

        _install_playwright_js_dialog_handler(page)

        if post_open_wait > 0:
            logger.info(
                "Waiting %.1f s after browser open (complete login if needed; "
                "set TRANSIT_TEST_POST_BROWSER_OPEN_WAIT_SEC to change).",
                post_open_wait,
            )
            time.sleep(post_open_wait)

        dms_values: dict = {
            "frame_partial": frame_p,
            "engine_partial": engine_p,
        }
        urls = SiebelDmsUrls(
            contact="",
            vehicles="",
            precheck="",
            pdi="",
            vehicle=(DMS_REAL_URL_VEHICLE or "").strip(),
            enquiry="",
            line_items="",
            reports="",
        )
        frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None

        def note(msg: str) -> None:
            logger.info("%s", msg)

        def step(msg: str) -> None:
            logger.info("STEP: %s", msg)

        ok, err, merged, in_transit, crit, info = prepare_vehicle(
            page,
            dms_values,
            urls,
            nav_timeout_ms=int(DMS_SIEBEL_NAV_TIMEOUT_MS),
            action_timeout_ms=int(DMS_SIEBEL_ACTION_TIMEOUT_MS),
            content_frame_selector=frame_sel,
            note=note,
            step=step,
            debug_dump_dir=debug_dump_dir,
        )

        logger.info("--- prepare_vehicle result ---")
        logger.info("ok=%s in_transit_flag=%s", ok, in_transit)
        if err:
            logger.error("error=%s", err)
        if crit:
            logger.warning("critical_gaps: %s", "; ".join(crit))
        if info:
            logger.info("informational_notes: %s", "; ".join(info))
        if merged:
            keys = sorted(merged.keys())
            logger.info(
                "merged vehicle keys (%d): %s",
                len(keys),
                ", ".join(keys[:40]) + (" ..." if len(keys) > 40 else ""),
            )

        logger.info("Done. Leave the browser open to inspect; close the browser when finished.")
        exit_code = 0 if ok else 1
        return exit_code
    finally:
        try:
            retain_automation_browser_for_operator_manual_close()
        except Exception as exc:
            logger.debug("retain_automation_browser_for_operator_manual_close: %s", exc)
        if _pause_before_exit:
            logger.info(
                "Press Enter in this console to exit the script (browser window stays open). "
                "Set TRANSIT_TEST_PAUSE_BEFORE_EXIT=0 to skip this pause."
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
