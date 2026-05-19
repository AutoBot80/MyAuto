"""
Local test wrapper: Hero DMS — Vehicle Sales / My Orders **Order#** search (challan resume path),
drill into the order, read VINs already on the line-item grid.

Uses the same private helpers as production:
  ``_run_vehicle_sales_my_orders_order_number_search``, ``_click_my_orders_jqgrid_order_for_mobile_or_order``,
  ``_read_order_line_row_vin_and_discount`` (VIN + line discount; same columns as ``attach_vehicle_to_bkg``).

Configure ``backend/.env`` (``DMS_BASE_URL``, ``DMS_MODE``, ``DMS_LOGIN_*``, ``DMS_SIEBEL_*``) like Fill DMS.

Environment (optional):
  CHALLAN_TEST_ORDER_NUMBER          default: 11870-02-SVSO-0526-408
  CHALLAN_TEST_MAX_VIN_ROWS          default: 70 (scan line rows 1..N; cap 120 for large orders)
  CHALLAN_TEST_POST_LOGIN_WAIT_SEC   default: 2 (seconds after browser opens before Vehicle Sales
                                     navigation; increase if you need more time to log in)
  CHALLAN_TEST_PAUSE_BEFORE_EXIT     default: 1 — wait for Enter in the console before exiting so
                                     the Playwright driver does not tear down the browser (set to 0
                                     for non-interactive runs; may still close launched Chromium)

Pager tuning (``backend/.env`` — read by ``hero_dms_playwright_invoice``):
  HERO_DMS_ORDER_LINE_PAGER_AFTER_SEEK_MS   default: 240 — ms to wait after each seek click
  HERO_DMS_ORDER_LINE_PAGER_VISIBLE_MS      default: 120 — ms after counter already shows target slice
  HERO_DMS_ORDER_LINE_PAGER_MAX_STEPS       default: 120 — max pager clicks per row ensure (large orders)

Double-click ``test_challan_order_resume_my_orders.bat`` or run:
  python test_challan_order_resume_my_orders.py
from this folder (repo root must be the parent of ``Testing Wrappers``).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_challan_order_resume_my_orders")

DEFAULT_ORDER_NUMBER = "11870-02-SVSO-0526-408"


def _goto_vehicle_sales_my_orders(page, *, action_timeout_ms: int, note) -> None:
    """Same Vehicle Sales URL + post-goto wait as ``_create_order`` / ``print_hero_dms_forms``."""
    from app.services.hero_dms_shared_utilities import _siebel_after_goto_wait

    try:
        from urllib.parse import urlparse as _up

        _purl = _up(page.url)
        _base_url = f"{_purl.scheme}://{_purl.netloc}{_purl.path}"
    except Exception:
        _base_url = "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    _vs_url = (
        f"{_base_url}?SWECmd=GotoView&SWEView=Order+Entry+-+My+Orders+View+(Sales)"
        f"&SWERF=1&SWEHo=&SWEBU=1"
    )
    note(f"Test: navigating to Vehicle Sales / My Orders. base={_base_url[:70]}")
    try:
        page.goto(_vs_url, timeout=min(action_timeout_ms * 3, 45000), wait_until="load")
    except Exception:
        try:
            page.goto(_vs_url, timeout=min(action_timeout_ms * 3, 45000), wait_until="domcontentloaded")
        except Exception as _e:
            note(f"Test: goto Vehicle Sales URL raised {_e!r} — continuing.")
    _siebel_after_goto_wait(page, floor_ms=4500)
    note(f"Test: arrived at Vehicle Sales. URL={page.url[:140]}")


def _enumerate_order_lines_vin_discount(
    page,
    *,
    content_frame_selector: str | None,
    max_rows: int,
    stop_after_consecutive_empty: int,
) -> tuple[list[tuple[int, str, str]], str | None]:
    from app.services.hero_dms_playwright_invoice import (
        _read_order_line_jqgrid_row_counter,
        _read_order_line_row_vin_and_discount,
    )

    # Stop at the real line count from ``#s_1_rc`` (``… of N``) so we do not burn time on rows 26..max_rows
    # after the last VIN (each would still run VIN+discount reads and long locator timeouts).
    _ctr0 = _read_order_line_jqgrid_row_counter(page, content_frame_selector=content_frame_selector)
    _tot = int(_ctr0[2]) if _ctr0 and len(_ctr0) > 2 else 0
    _upper = min(max_rows, _tot) if _tot > 0 else max_rows

    found: list[tuple[int, str, str]] = []
    consec_empty = 0
    for row_n in range(1, _upper + 1):
        vin, disc = _read_order_line_row_vin_and_discount(
            page,
            row_n=row_n,
            content_frame_selector=content_frame_selector,
        )
        vin = (vin or "").strip()
        disc = (disc or "").strip()
        if vin:
            found.append((row_n, vin, disc))
            consec_empty = 0
            logger.info("  row %2d  VIN=%s  discount=%s", row_n, vin, disc if disc else "—")
        else:
            consec_empty += 1
            if row_n == 1:
                return (
                    [],
                    "Row 1 VIN field empty — not on order line grid, or selectors mismatch.",
                )
            if consec_empty >= stop_after_consecutive_empty:
                break
    if not found:
        return [], "No VIN values read on any row."
    return found, None


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        dms_automation_is_real_siebel,
    )
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_invoice import (
        _click_my_orders_jqgrid_order_for_mobile_or_order,
        _run_vehicle_sales_my_orders_order_number_search,
    )
    from app.services.hero_dms_shared_utilities import _poll_and_handle_siebel_error_popup, _safe_page_wait

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set — add it to backend/.env.")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real / siebel / live / production / hero for this wrapper.")
        return 1

    order_number = (os.getenv("CHALLAN_TEST_ORDER_NUMBER") or DEFAULT_ORDER_NUMBER).strip()
    try:
        max_rows = max(5, min(120, int(os.getenv("CHALLAN_TEST_MAX_VIN_ROWS") or "70")))
    except ValueError:
        max_rows = 70
    try:
        post_login_wait = max(0.0, float(os.getenv("CHALLAN_TEST_POST_LOGIN_WAIT_SEC") or "2"))
    except ValueError:
        post_login_wait = 2.0

    _pause_before_exit = (os.getenv("CHALLAN_TEST_PAUSE_BEFORE_EXIT") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    _tmo = int(DMS_SIEBEL_ACTION_TIMEOUT_MS or 8000)
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None

    def note(msg: str) -> None:
        logger.info("%s", msg)

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

        page.set_default_timeout(_tmo)
        _install_playwright_js_dialog_handler(page)

        if post_login_wait > 0:
            logger.info(
                "Waiting %.1f s before Vehicle Sales navigation (complete Siebel login in the browser; "
                "set CHALLAN_TEST_POST_LOGIN_WAIT_SEC to change).",
                post_login_wait,
            )
            time.sleep(post_login_wait)

        _goto_vehicle_sales_my_orders(page, action_timeout_ms=_tmo, note=note)

        logger.info("--- Order# search (same as challan resume) ---")
        mos = _run_vehicle_sales_my_orders_order_number_search(
            page,
            order_number=order_number,
            action_timeout_ms=_tmo,
            content_frame_selector=frame_sel,
            note=note,
        )
        oc = (mos.outcome or "").strip()
        logger.info(
            "My Orders Order# search outcome=%r primary_order=%r error=%r rows=%d",
            oc,
            (mos.primary_order or "").strip(),
            mos.error,
            len(mos.rows or []),
        )
        if oc in ("error", "no_rows"):
            logger.error("Order# search did not produce a usable grid row for %r.", order_number)
            exit_code = 1
            return exit_code

        logger.info("--- Click Order# in jqGrid (mobile empty → order needle only) ---")
        clicked = _click_my_orders_jqgrid_order_for_mobile_or_order(
            page,
            mobile="",
            order_number=order_number,
            content_frame_selector=frame_sel,
            note=note,
            action_timeout_ms=_tmo,
        )
        if not clicked:
            logger.error("Could not click Order# link for %r.", order_number)
            exit_code = 1
            return exit_code

        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        _safe_page_wait(page, 1800, log_label="test_challan_resume_after_order_drill")

        err_pop = _poll_and_handle_siebel_error_popup(
            page,
            frame_sel,
            note,
            context="test_challan_order_resume after Order# drilldown",
            total_ms=1600,
            step_ms=320,
        )
        if err_pop:
            logger.error("Siebel error popup: %s", err_pop[:300])
            exit_code = 1
            return exit_code

        logger.info("--- Read VIN + line discount per row (attach_vehicle_to_bkg columns) ---")
        pairs, v_err = _enumerate_order_lines_vin_discount(
            page,
            content_frame_selector=frame_sel,
            max_rows=max_rows,
            # After pager fix, empty streaks should be rare; keep headroom for slow DOM / counter lag.
            stop_after_consecutive_empty=10,
        )
        if v_err:
            logger.error("%s", v_err)
            exit_code = 1
            return exit_code

        logger.info("Finished reading %d line(s) with VIN (rows logged above as each completed).", len(pairs))

        logger.info("Done. Leave the browser open to inspect; close the browser when finished.")
        exit_code = 0
        return exit_code
    finally:
        try:
            retain_automation_browser_for_operator_manual_close()
        except Exception as exc:
            logger.debug("retain_automation_browser_for_operator_manual_close: %s", exc)
        if _pause_before_exit:
            logger.info(
                "Press Enter in this console to exit the script (browser window stays open). "
                "Set CHALLAN_TEST_PAUSE_BEFORE_EXIT=0 to skip this pause."
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
