"""
Local test wrapper: Hero DMS — Vehicle Sales / My Orders mobile search, open invoice detail,
then scrape ex-showroom total from the **invoice list** grid ``s_2_l`` (Round Off Amount / Amount).

Matches production attach after **Create Invoice** (prod only). Use this wrapper to verify the
invoice grid scrape without running a full challan.

Production helpers:
  ``_run_vehicle_sales_my_orders_mobile_search``,
  ``_click_my_orders_jqgrid_invoice_for_mobile_or_order``,
  ``list_invoice_list_amount_cells``,
  ``_scrape_invoice_list_amount_after_create_invoice``.

Configure ``backend/.env`` (``DMS_BASE_URL``, ``DMS_MODE``, ``DMS_LOGIN_*``, ``DMS_SIEBEL_*``).

Environment (optional):
  TOTAL_COST_TEST_MOBILE              default: 9785562020
  TOTAL_COST_TEST_POST_LOGIN_SEC      default: 2
  TOTAL_COST_TEST_PAUSE_BEFORE_EXIT   default: 1
  TOTAL_COST_TEST_INVOICE_POLL_SEC    default: 90 (poll invoice grid after drill-down)

Run:
  python test_my_orders_total_cost_scrape.py
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
logger = logging.getLogger("test_invoice_ex_showroom_scrape")

DEFAULT_MOBILE = "9785562020"


def _goto_vehicle_sales_my_orders(page, *, action_timeout_ms: int, note) -> None:
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


def _poll_invoice_amount(
    page,
    *,
    frame_sel: str | None,
    poll_sec: float,
    interval_sec: float = 2.0,
) -> tuple[str, list[dict[str, str]]]:
    from app.services.hero_dms_playwright_invoice import (
        _scrape_invoice_list_amount_after_create_invoice,
        list_invoice_list_amount_cells,
    )

    deadline = time.monotonic() + max(5.0, poll_sec)
    last_cells: list[dict[str, str]] = []
    primary = ""
    while time.monotonic() < deadline:
        last_cells = list_invoice_list_amount_cells(page, content_frame_selector=frame_sel)
        primary = _scrape_invoice_list_amount_after_create_invoice(
            page, content_frame_selector=frame_sel
        )
        if primary:
            return primary, last_cells
        time.sleep(interval_sec)
    return primary, last_cells


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        dms_automation_is_real_siebel,
    )
    from app.services.add_subdealer_challan_commit_service import _coerce_ex_showroom_scalar
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_invoice import (
        _click_my_orders_jqgrid_invoice_for_mobile_or_invoice,
        _click_my_orders_jqgrid_order_for_mobile_or_order,
        _my_orders_invoice_meaningful,
        _run_vehicle_sales_my_orders_mobile_search,
    )
    from app.services.hero_dms_shared_utilities import _poll_and_handle_siebel_error_popup, _safe_page_wait

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set — add it to backend/.env.")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real / siebel / live / production / hero for this wrapper.")
        return 1

    mobile = (os.getenv("TOTAL_COST_TEST_MOBILE") or DEFAULT_MOBILE).strip()
    digits = "".join(c for c in mobile if c.isdigit())
    if not digits:
        logger.error("TOTAL_COST_TEST_MOBILE has no digits.")
        return 1

    try:
        post_login_wait = max(0.0, float(os.getenv("TOTAL_COST_TEST_POST_LOGIN_SEC") or "2"))
    except ValueError:
        post_login_wait = 2.0
    try:
        invoice_poll_sec = max(10.0, float(os.getenv("TOTAL_COST_TEST_INVOICE_POLL_SEC") or "90"))
    except ValueError:
        invoice_poll_sec = 90.0

    _pause_before_exit = (os.getenv("TOTAL_COST_TEST_PAUSE_BEFORE_EXIT") or "1").strip().lower() not in (
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
            return 1

        page.set_default_timeout(_tmo)
        _install_playwright_js_dialog_handler(page)

        if post_login_wait > 0:
            logger.info(
                "Waiting %.1f s before Vehicle Sales (complete Siebel login; "
                "TOTAL_COST_TEST_POST_LOGIN_SEC to change).",
                post_login_wait,
            )
            time.sleep(post_login_wait)

        _goto_vehicle_sales_my_orders(page, action_timeout_ms=_tmo, note=note)

        logger.info("--- My Orders mobile search: %s ---", digits)
        mos = _run_vehicle_sales_my_orders_mobile_search(
            page,
            mobile=digits,
            action_timeout_ms=_tmo,
            content_frame_selector=frame_sel,
            note=note,
        )
        oc = (mos.outcome or "").strip()
        inv_hint = (mos.primary_invoice or "").strip()
        ord_hint = (mos.primary_order or "").strip()
        logger.info(
            "My Orders outcome=%r primary_order=%r primary_invoice=%r rows=%d error=%r",
            oc,
            ord_hint,
            inv_hint,
            len(mos.rows or []),
            mos.error,
        )
        if oc in ("error", "no_rows"):
            logger.error("Mobile search did not produce a usable grid.")
            return 1

        _safe_page_wait(page, 800, log_label="test_after_mobile_search")
        _poll_and_handle_siebel_error_popup(
            page, frame_sel, note, context="test after mobile search", total_ms=800, step_ms=200
        )

        drilled = False
        if inv_hint and _my_orders_invoice_meaningful(inv_hint):
            note(f"Test: drilling Invoice# {inv_hint!r} from My Orders grid.")
            drilled = _click_my_orders_jqgrid_invoice_for_mobile_or_invoice(
                page,
                mobile=digits,
                invoice_number=inv_hint,
                content_frame_selector=frame_sel,
                note=note,
                action_timeout_ms=_tmo,
            )
        if not drilled and ord_hint:
            note(
                f"Test: no Invoice# drill — opening Order# {ord_hint!r}. "
                "Complete Create Invoice in the browser if the invoice list is not visible."
            )
            drilled = _click_my_orders_jqgrid_order_for_mobile_or_order(
                page,
                mobile=digits,
                order_number=ord_hint,
                content_frame_selector=frame_sel,
                note=note,
                action_timeout_ms=_tmo,
            )

        if drilled:
            _safe_page_wait(page, 2000, log_label="test_after_my_orders_drill")
        else:
            note(
                "Test: could not drill Order/Invoice from grid — open the invoice list view manually, "
                f"then polling up to {invoice_poll_sec:.0f}s."
            )

        primary, cells = _poll_invoice_amount(
            page, frame_sel=frame_sel, poll_sec=invoice_poll_sec
        )

        print("")
        print("========== Invoice list amount scrape (s_2_l, prod path) ==========")
        print(f"  Mobile searched:     {digits}")
        print(f"  Grid outcome:        {oc}")
        print(f"  Order / Invoice hint: {ord_hint!r} / {inv_hint!r}")
        print(f"  Cells found:         {len(cells)}")
        for i, c in enumerate(cells, 1):
            print(
                f"    [{i}] id={c.get('id')!r}  column={c.get('column')!r}  value={c.get('value')!r}"
            )
        print(f"  Primary scrape:      {primary!r}")
        _px = _coerce_ex_showroom_scalar(primary)
        print(f"  Coerced float:       {_px}")
        print("  (Production: same scrape runs after auto Create Invoice in ENVIRONMENT=prod.)")
        print("================================================================")
        print("")

        if primary and _px is not None and _px >= 5000:
            exit_code = 0
        else:
            logger.error(
                "No valid invoice list amount scraped. Ensure invoice list grid s_2_l is visible "
                "(Round_Off_Amount or Total_Amount on row 1)."
            )
            exit_code = 1

    except Exception as exc:
        logger.exception("Test failed: %s", exc)
        exit_code = 1
    finally:
        if _pause_before_exit:
            try:
                input("Press Enter to close the script (browser may stay open for manual inspection)... ")
            except EOFError:
                pass
        retain_automation_browser_for_operator_manual_close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
