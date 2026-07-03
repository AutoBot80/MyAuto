"""
Local test wrapper: Hero DMS — My Orders mobile search → order drill →
``_attach_vehicle_to_bkg`` **Apply Campaign** + **Create Invoice** (prod only) →
``print_hero_dms_forms`` Run Report PDF downloads.

Defaults:
  Mobile:     9351244099
  First name: SHRUTI  (name + 7-day invoice-date invoiced-row guard)
  Order#:     (empty — no restrict; shows all rows for duplicate-mobile readback tests)

Production helpers (no edits to base code):
  ``_run_vehicle_sales_my_orders_mobile_search``,
  ``_click_my_orders_jqgrid_order_for_mobile_or_order``,
  ``_attach_vehicle_to_bkg``,
  ``print_hero_dms_forms``,
  ``_scrape_invoice_list_amount_after_create_invoice``,
  ``_scrape_ex_showroom_into_scraped_after_invoice_number``.

Configure ``backend/.env``: ``DMS_BASE_URL``, ``DMS_MODE``, login, ``DMS_SIEBEL_*``.

**Create Invoice auto-click** runs only when ``ENVIRONMENT=prod`` or ``ENVIRONMENT=production``
(see ``HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE`` in ``app.config``). Otherwise the wrapper
still runs Apply Campaign and logs that Create Invoice must be clicked manually.

Environment (optional):
  CREATE_INVOICE_TEST_MOBILE                 default: 9351244099
  CREATE_INVOICE_TEST_FIRST_NAME             default: SHRUTI
  CREATE_INVOICE_TEST_ORDER_NUMBER           default: (empty) — set to restrict grid to one Order#
  CREATE_INVOICE_TEST_FULL_CHASSIS           required when My Orders outcome is **pending**
                                             (full VIN attach before Apply Campaign)
  CREATE_INVOICE_TEST_LINE_ITEM_DISCOUNT     optional line discount
  CREATE_INVOICE_TEST_FINANCIER_NAME         optional finance fields on order form
  CREATE_INVOICE_TEST_FORCE_ALLOCATED_PATH   default: 0 — set 1 to skip VIN attach and use
                                             ``start_at_order_link_before_apply=True`` even
                                             when grid classifies as pending/unknown_rows
  CREATE_INVOICE_TEST_SKIP_PRINT_REPORTS     default: 0 — set 1 to skip ``print_hero_dms_forms``
  CREATE_INVOICE_TEST_POST_LOGIN_WAIT_SEC    default: 2
  CREATE_INVOICE_TEST_INVOICE_POLL_SEC       default: 90 — poll header Invoice# after attach
  CREATE_INVOICE_TEST_PAUSE_BEFORE_EXIT      default: 1

Run:
  python test_dms_create_invoice.py
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
logger = logging.getLogger("test_dms_create_invoice")

DEFAULT_MOBILE = "9351244099"
DEFAULT_FIRST_NAME = "SHRUTI"
DEFAULT_ORDER_NUMBER = ""


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


def _poll_invoice_number_header(
    page,
    *,
    frame_sel: str | None,
    poll_sec: float,
    note,
) -> str:
    """Best-effort Invoice# from order header fields (same JS as ``_create_order`` inner scrape)."""
    from app.services.hero_dms_shared_utilities import _safe_page_wait

    deadline = time.monotonic() + max(10.0, poll_sec)
    last = ""
    while time.monotonic() < deadline:
        for frame in [page, *list(page.frames)]:
            try:
                v = frame.evaluate(
                    """() => {
                        const vis = (el) => {
                            if (!el) return false;
                            const st = window.getComputedStyle(el);
                            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                            const r = el.getBoundingClientRect();
                            return r.width > 2 && r.height > 2;
                        };
                        const orderOnly = (s) => {
                            const t = String(s || '').toLowerCase();
                            return t.includes('order') && !t.includes('invoice');
                        };
                        const tryInputs = Array.from(document.querySelectorAll(
                            "input[aria-label*='Invoice' i], input[title*='Invoice' i], "
                            + "input[name*='Invoice' i], input[id*='Invoice' i]"
                        ));
                        for (const el of tryInputs) {
                            if (!vis(el)) continue;
                            const al = el.getAttribute('aria-label') || '';
                            const tt = el.getAttribute('title') || '';
                            if (orderOnly(al) || orderOnly(tt)) continue;
                            const val = (el.value || '').trim();
                            if (val && val.length >= 3 && !/^(pending|—|-)$/i.test(val)) return val;
                        }
                        const tryLinks = Array.from(document.querySelectorAll(
                            "a[name='Invoice Number'], a[name='Invoice #'], "
                            + "a[aria-label*='Invoice' i], a[title*='Invoice' i]"
                        ));
                        for (const a of tryLinks) {
                            if (!vis(a)) continue;
                            const txt = (a.textContent || '').trim();
                            if (txt && /[A-Za-z0-9-]{4,}/.test(txt)) return txt;
                        }
                        return '';
                    }"""
                )
                if (v or "").strip():
                    last = str(v).strip()
                    note(f"Test: scraped Invoice# header={last!r}.")
                    return last
            except Exception:
                continue
        _safe_page_wait(page, 1000, log_label="test_create_invoice_poll")
    return last


def _run_print_hero_dms_forms(
    page,
    *,
    mobile: str,
    order_number: str,
    invoice_number: str,
    contact_first_name: str,
    frame_sel: str | None,
    action_timeout_ms: int,
    note,
) -> tuple[bool, str | None, list[str], list[dict]]:
    from app.config import DEALER_ID, get_uploaded_scans_sale_folder
    from app.services.hero_dms_playwright_invoice import (
        DEFAULT_HERO_DMS_RUN_REPORT_NAMES,
        print_hero_dms_forms,
    )

    dl_dir = get_uploaded_scans_sale_folder(int(DEALER_ID), mobile).resolve()
    logger.info("--- print_hero_dms_forms (Run Report PDFs) ---")
    logger.info("Downloads dir: %s", dl_dir)
    logger.info("Reports: %s", list(DEFAULT_HERO_DMS_RUN_REPORT_NAMES))
    ok, err, paths, details = print_hero_dms_forms(
        page,
        mobile=mobile,
        order_number=order_number,
        invoice_number=invoice_number,
        contact_first_name=contact_first_name,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=frame_sel,
        downloads_dir=dl_dir,
        report_names=DEFAULT_HERO_DMS_RUN_REPORT_NAMES,
        note=note,
    )
    _ok_reports: list[str] = []
    _fail_reports: list[str] = []
    for row in details:
        name = row.get("report", "?")
        if row.get("ok"):
            _ok_reports.append(name)
            logger.info("  OK   %s  ->  %s", name, row.get("path") or "(no path)")
        else:
            _fail_reports.append(name)
            logger.error("  FAIL %s  :  %s", name, row.get("error") or "unknown error")
    logger.info(
        "Run Report batch: ok=%s succeeded=%d failed=%d paths=%d",
        ok,
        len(_ok_reports),
        len(_fail_reports),
        len(paths),
    )
    if err:
        logger.error("print_hero_dms_forms error: %s", err)
    return ok, err, paths, details


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        ENVIRONMENT,
        HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE,
        dms_automation_is_real_siebel,
    )
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_invoice import (
        _attach_vehicle_to_bkg,
        _click_my_orders_jqgrid_order_for_mobile_or_order,
        _my_orders_invoice_meaningful,
        _run_vehicle_sales_my_orders_mobile_search,
        _scrape_ex_showroom_into_scraped_after_invoice_number,
        _scrape_invoice_list_amount_after_create_invoice,
    )
    from app.services.hero_dms_shared_utilities import _poll_and_handle_siebel_error_popup, _safe_page_wait

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set — add it to backend/.env.")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real / siebel / live / production / hero for this wrapper.")
        return 1

    mobile = (os.getenv("CREATE_INVOICE_TEST_MOBILE") or DEFAULT_MOBILE).strip()
    first_name = (os.getenv("CREATE_INVOICE_TEST_FIRST_NAME") or DEFAULT_FIRST_NAME).strip()
    order_number = (os.getenv("CREATE_INVOICE_TEST_ORDER_NUMBER") or DEFAULT_ORDER_NUMBER).strip()
    full_chassis = (os.getenv("CREATE_INVOICE_TEST_FULL_CHASSIS") or "").strip()
    line_discount = (os.getenv("CREATE_INVOICE_TEST_LINE_ITEM_DISCOUNT") or "").strip()
    financier = (os.getenv("CREATE_INVOICE_TEST_FINANCIER_NAME") or "").strip()
    force_allocated = (os.getenv("CREATE_INVOICE_TEST_FORCE_ALLOCATED_PATH") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )
    skip_print = (os.getenv("CREATE_INVOICE_TEST_SKIP_PRINT_REPORTS") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )

    digits = "".join(c for c in mobile if c.isdigit())
    if not digits:
        logger.error("CREATE_INVOICE_TEST_MOBILE has no digits.")
        return 1

    try:
        post_login_wait = max(0.0, float(os.getenv("CREATE_INVOICE_TEST_POST_LOGIN_WAIT_SEC") or "2"))
    except ValueError:
        post_login_wait = 2.0
    try:
        invoice_poll_sec = max(15.0, float(os.getenv("CREATE_INVOICE_TEST_INVOICE_POLL_SEC") or "90"))
    except ValueError:
        invoice_poll_sec = 90.0

    _pause_before_exit = (os.getenv("CREATE_INVOICE_TEST_PAUSE_BEFORE_EXIT") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    _tmo = int(DMS_SIEBEL_ACTION_TIMEOUT_MS or 8000)
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None

    def note(msg: str) -> None:
        logger.info("%s", msg)

    exit_code = 1
    print_ok = True
    try:
        logger.info(
            "ENVIRONMENT=%r  HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE=%s  first_name=%r  skip_print=%s",
            ENVIRONMENT,
            HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE,
            first_name,
            skip_print,
        )
        if not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
            logger.warning(
                "Create Invoice will NOT be auto-clicked — set ENVIRONMENT=prod in backend/.env "
                "to test the full prod path. Apply Campaign will still run."
            )

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
                "CREATE_INVOICE_TEST_POST_LOGIN_WAIT_SEC to change).",
                post_login_wait,
            )
            time.sleep(post_login_wait)

        _goto_vehicle_sales_my_orders(page, action_timeout_ms=_tmo, note=note)

        if order_number:
            logger.info("--- My Orders mobile search (restrict to Order# %r) ---", order_number)
        else:
            logger.info("--- My Orders mobile search (all rows for mobile) ---")
        mos = _run_vehicle_sales_my_orders_mobile_search(
            page,
            mobile=digits,
            action_timeout_ms=_tmo,
            content_frame_selector=frame_sel,
            note=note,
            restrict_to_order_number=order_number,
            expected_contact_first_name=first_name,
        )
        oc = (mos.outcome or "").strip()
        inv_hint = (mos.primary_invoice or "").strip()
        ord_hint = (mos.primary_order or "").strip() or order_number
        readback_mob = (mos.primary_mobile or "").strip()
        readback_fn = (mos.primary_contact_first_name or "").strip()
        readback_idt = (mos.primary_invoice_date or "").strip()
        logger.info(
            "My Orders outcome=%r order=%r invoice=%r readback_mobile=%r "
            "readback_first_name=%r readback_invoice_date=%r mismatch=%s rows=%d error=%r",
            oc,
            ord_hint,
            inv_hint,
            readback_mob,
            readback_fn,
            readback_idt,
            mos.mobile_readback_mismatch,
            len(mos.rows or []),
            mos.error,
        )

        if oc == "error" and (mos.error or "").strip() == "my_orders_no_matching_order":
            logger.error("No grid row for mobile %s + Order# %r.", digits, order_number)
            return 1
        if oc in ("error", "no_rows"):
            logger.error("Mobile search did not produce a usable grid.")
            return 1

        inv_no = inv_hint

        if oc == "invoiced" and _my_orders_invoice_meaningful(inv_hint):
            print("")
            print("========== Create Invoice test — already invoiced ==========")
            print(f"  Mobile searched:      {digits}")
            print(f"  Expected first name:  {first_name!r}")
            print(f"  Grid readback mobile: {readback_mob!r}")
            print(f"  Contact first name:   {readback_fn!r}")
            print(f"  Invoice date:         {readback_idt!r}")
            print(f"  Order#:               {ord_hint!r}")
            print(f"  Invoice# (grid):      {inv_hint!r}")
            print("  No Apply Campaign / Create Invoice run needed.")
            print("==========================================================")
            print("")

            if not skip_print:
                print_ok, print_err, print_paths, _ = _run_print_hero_dms_forms(
                    page,
                    mobile=digits,
                    order_number=ord_hint,
                    invoice_number=inv_hint,
                    contact_first_name=first_name,
                    frame_sel=frame_sel,
                    action_timeout_ms=_tmo,
                    note=note,
                )
                if not print_ok:
                    logger.error("print_hero_dms_forms failed: %s", (print_err or "unknown")[:400])
                    return 1
                logger.info("Saved %d report PDF(s).", len(print_paths))
            return 0

        if not ord_hint:
            logger.error(
                "No Order# from grid and CREATE_INVOICE_TEST_ORDER_NUMBER not set — "
                "cannot drill. Set CREATE_INVOICE_TEST_ORDER_NUMBER or use an invoiced row."
            )
            return 1

        _safe_page_wait(page, 800, log_label="test_before_order_drill")
        _poll_and_handle_siebel_error_popup(
            page, frame_sel, note, context="test before Order# drill", total_ms=800, step_ms=200
        )

        logger.info("--- Drill Order# in jqGrid ---")
        if not _click_my_orders_jqgrid_order_for_mobile_or_order(
            page,
            mobile=digits,
            order_number=ord_hint,
            content_frame_selector=frame_sel,
            note=note,
            action_timeout_ms=_tmo,
        ):
            logger.error("Could not open Order# %r from My Orders grid.", ord_hint)
            return 1

        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        _safe_page_wait(page, 1800, log_label="test_after_order_drill")
        err_pop = _poll_and_handle_siebel_error_popup(
            page,
            frame_sel,
            note,
            context="test after Order# drilldown",
            total_ms=1600,
            step_ms=320,
        )
        if err_pop:
            logger.error("Siebel error after Order# drill: %s", err_pop[:300])
            return 1

        use_allocated_shortcut = force_allocated or oc in ("allocated", "unknown_rows")
        if oc == "pending" and not use_allocated_shortcut and not full_chassis:
            logger.error(
                "My Orders outcome is **pending** — set CREATE_INVOICE_TEST_FULL_CHASSIS "
                "(17-char VIN) for full attach, or CREATE_INVOICE_TEST_FORCE_ALLOCATED_PATH=1 "
                "if the order is already allocated on the form."
            )
            return 1

        logger.info(
            "--- attach_vehicle_to_bkg (Apply Campaign + Create Invoice) "
            "start_at_order_link_before_apply=%s ---",
            use_allocated_shortcut,
        )
        att_ok, att_err, att_scraped = _attach_vehicle_to_bkg(
            page,
            full_chassis=full_chassis or "00000000000000000",
            order_number=ord_hint,
            action_timeout_ms=_tmo,
            content_frame_selector=frame_sel,
            note=note,
            start_at_order_link_before_apply=use_allocated_shortcut,
            line_item_discount=line_discount,
            financier_name=financier,
            skip_line_item_fill=use_allocated_shortcut,
        )
        if not att_ok:
            logger.error("attach_vehicle_to_bkg failed: %s", (att_err or "unknown")[:400])
            return 1

        _safe_page_wait(page, 1200, log_label="test_after_attach")
        inv_no = _poll_invoice_number_header(
            page, frame_sel=frame_sel, poll_sec=invoice_poll_sec, note=note
        )
        if not inv_no and inv_hint and _my_orders_invoice_meaningful(inv_hint):
            inv_no = inv_hint

        scraped: dict = dict(att_scraped or {})
        scraped["order_number"] = ord_hint
        scraped["invoice_number"] = inv_no or ""
        if inv_no:
            _scrape_ex_showroom_into_scraped_after_invoice_number(
                page,
                content_frame_selector=frame_sel,
                scraped=scraped,
                invoice_number=inv_no,
                note=note,
            )
        ex_amount = _scrape_invoice_list_amount_after_create_invoice(
            page, content_frame_selector=frame_sel
        )

        print("")
        print("========== Create Invoice test result ==========")
        print(f"  Mobile searched:           {digits}")
        print(f"  Expected first name:       {first_name!r}")
        print(f"  Grid readback mobile:      {readback_mob!r}")
        print(f"  Contact first name:        {readback_fn!r}")
        print(f"  Invoice date:              {readback_idt!r}")
        print(f"  Mobile readback mismatch:  {mos.mobile_readback_mismatch}")
        print(f"  Order#:                    {ord_hint!r}")
        print(f"  My Orders outcome:         {oc!r}")
        print(f"  Allocated shortcut:        {use_allocated_shortcut}")
        print(f"  Auto-click Create Invoice: {HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE}")
        print(f"  Invoice# (after attach):   {inv_no!r}")
        print(f"  Invoice list amount:       {ex_amount!r}")
        print(f"  scraped extras:            {scraped}")
        if not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
            print("  NOTE: Click Create Invoice manually in Siebel if the button is still visible.")
        print("================================================")
        print("")

        if HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
            exit_code = 0 if (inv_no and _my_orders_invoice_meaningful(inv_no)) else 1
            if exit_code != 0:
                logger.error(
                    "Create Invoice auto-click path finished but Invoice# not scraped within poll window."
                )
        else:
            exit_code = 0
            logger.info("Non-prod: attach path OK; verify Create Invoice manually in the browser.")

        if exit_code == 0 and not skip_print and inv_no and _my_orders_invoice_meaningful(inv_no):
            print_ok, print_err, print_paths, _ = _run_print_hero_dms_forms(
                page,
                mobile=digits,
                order_number=ord_hint,
                invoice_number=inv_no,
                contact_first_name=first_name,
                frame_sel=frame_sel,
                action_timeout_ms=_tmo,
                note=note,
            )
            if not print_ok:
                logger.error("print_hero_dms_forms failed: %s", (print_err or "unknown")[:400])
                exit_code = 1
            else:
                logger.info("Saved %d report PDF(s).", len(print_paths))
        elif exit_code == 0 and not skip_print and not (inv_no and _my_orders_invoice_meaningful(inv_no)):
            logger.warning(
                "Skipping print_hero_dms_forms — no meaningful Invoice# yet "
                "(set ENVIRONMENT=prod or scrape Invoice# manually first)."
            )

    except Exception as exc:
        logger.exception("Test failed: %s", exc)
        exit_code = 1
    finally:
        if _pause_before_exit:
            try:
                input("Press Enter to close the script (browser may stay open for inspection)... ")
            except EOFError:
                pass
        retain_automation_browser_for_operator_manual_close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
