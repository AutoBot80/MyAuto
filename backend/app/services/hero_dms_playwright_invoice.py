"""
Hero Connect / Oracle Siebel Open UI — invoice / order automation (Playwright).

My Orders jqGrid search, attach vehicle to booking (VIN + Price All + Allocate +
Apply Campaign + Create Invoice), and the ``_create_order`` pipeline.
"""
from __future__ import annotations

import logging
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeout

from app.config import (
    DEALER_ID,
    DMS_SIEBEL_AUTO_IFRAME_SELECTORS,
    DMS_SIEBEL_INTER_ACTION_DELAY_MS,
    DMS_SIEBEL_POST_GOTO_WAIT_MS,
)
from app.services.hero_dms_shared_utilities import (
    SiebelDmsUrls,
    _agent_debug_log,
    _click_find_go_query,
    _detect_siebel_error_popup,
    _fill_by_label_on_frame,
    _goto,
    _is_browser_disconnected_error,
    _iter_frame_locator_roots,
    _locator_for_duplicate_fields,
    _normalize_cubic_cc_digits,
    _ordered_frames,
    _poll_and_handle_siebel_error_popup,
    _safe_page_wait,
    _siebel_after_goto_wait,
    _siebel_all_search_roots,
    _siebel_click_by_id_anywhere,
    _siebel_inter_action_pause,
    _siebel_locator_search_roots,
    _siebel_note_frame_focus_snapshot,
    _siebel_scrape_text_by_id_anywhere,
    _try_click_siebel_save,
    _try_click_toolbar_by_name,
    _try_dismiss_siebel_error_dialog,
    _try_expand_find_flyin,
    _try_fill_field,
    _ts_ist_iso,
    _fill_in_frame,
    _fill_with_frame_locator,
    select_siebel_dropdown_value,
    _try_select_option,
    _try_click_generate_booking,
)
from app.services.hero_dms_playwright_vehicle import (
    _looks_like_ex_showroom_price,
    _siebel_run_vehicle_serial_detail_precheck_pdi,
    scrape_siebel_vehicle_row,
)
from app.services.hero_dms_playwright_customer import (
    _fill_create_order_financier_field_on_frame,
    _select_dropdown_by_label_on_frame,
)

logger = logging.getLogger(__name__)


# Siebel **Create Invoice** after order attach: off by default — enable only when product wants automation
# to submit the invoice (operator may complete this step manually).
_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE = True


def _scrape_total_ex_showroom_after_price_allocate(
    page: Page,
    *,
    content_frame_selector: str | None,
) -> str:
    """
    Best-effort **Total (Ex-showroom)** from order line items after **Price All** + **Allocate All**.
    Same field family as the legacy ``_create_order`` line-item path.
    """
    js = """() => {
      const q = [
        "input[aria-label*='Total (Ex-showroom)' i]",
        "input[title*='Total (Ex-showroom)' i]",
        "input[aria-label*='Ex-showroom' i]",
        "input[title*='Ex-showroom' i]",
        "input[aria-label*='Ex Showroom' i]",
        "input[id*='Ex_Showroom' i]",
        "input[name*='Ex_Showroom' i]"
      ];
      for (const s of q) {
        for (const el of document.querySelectorAll(s)) {
          const val = (el.value || '').trim();
          if (val) return val;
        }
      }
      return '';
    }"""
    roots: list = []
    try:
        roots.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
    except Exception:
        pass
    try:
        roots.extend(list(_ordered_frames(page)))
    except Exception:
        pass
    roots.append(page)
    for root in roots:
        try:
            v = root.evaluate(js)
            if (v or "").strip():
                return str(v).strip()
        except Exception:
            continue
    return ""


@dataclass
class _MyOrdersGridSearchResult:
    """Outcome of Vehicle Sales My Orders jqGrid search by mobile (``_create_order`` branching)."""

    outcome: str  # invoiced | pending | allocated | no_rows | unknown_rows | error
    primary_order: str = ""
    primary_invoice: str = ""
    rows: list[dict] | None = None
    error: str | None = None


_JS_MY_ORDERS_JQGRID_ROWS = """() => {
    const out = [];
    const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 1 && r.height > 1;
    };
    const tables = document.querySelectorAll('table.ui-jqgrid-btable');
    for (const table of tables) {
        if (!vis(table)) continue;
        const headerRow = table.querySelector('thead tr.ui-jqgrid-labels') || table.querySelector('thead tr');
        const colNames = [];
        if (headerRow) {
            headerRow.querySelectorAll('th').forEach((th) => {
                const id = (th.getAttribute('id') || '');
                const tail = id.split('_').pop() || '';
                const txt = (th.textContent || '').trim().toLowerCase();
                colNames.push((tail || txt || '').toLowerCase());
            });
        }
        const dataRows = table.querySelectorAll('tbody tr.jqgrow, tbody tr[role="row"]');
        for (const tr of dataRows) {
            if (tr.classList.contains('jqgfirstrow')) continue;
            if (!vis(tr)) continue;
            const row = { status: '', invoice: '', order: '', raw: (tr.innerText || '').trim() };
            const tds = tr.querySelectorAll('td[role="gridcell"], td');
            tds.forEach((td, i) => {
                const txt = (td.textContent || '').trim();
                const adb = (td.getAttribute('aria-describedby') || '').toLowerCase();
                const cn = (colNames[i] || '').toLowerCase();
                const key = (cn + ' ' + adb).toLowerCase();
                if (key.includes('status') || adb.includes('status')) row.status = txt;
                else if (key.includes('invoice') || adb.includes('invoice')) row.invoice = txt;
                else if (key.includes('order') && (key.includes('order_number') || key.includes('order#') || key.includes('order_no') || adb.includes('order_number') || adb.includes('order#'))) {
                    const a = td.querySelector('a[name="Order Number"], a[name="Order #"], a');
                    row.order = ((a && a.textContent) ? a.textContent : txt).trim();
                }
            });
            if (!row.order && !row.status && !row.invoice) {
                const a = tr.querySelector("a[name='Order Number'], a[name='Order #']");
                if (a && vis(a)) row.order = (a.textContent || '').trim();
            }
            out.push(row);
        }
    }
    return out;
}"""


def _my_orders_invoice_meaningful(s: str) -> bool:
    """Whether jqGrid **invoice** cell text looks like a real Invoice# (not dash/placeholder)."""
    t = (s or "").strip()
    if len(t) < 2:
        return False
    if re.match(r"^(—|-+|–|pending|n/?a)$", t, re.I):
        return False
    return bool(re.search(r"[A-Za-z0-9]", t))


def _my_orders_row_text_blob(r: dict) -> str:
    """
    Combined visible text for a jqGrid row. Siebel often omits ``status`` in ``row.status`` when
    column ids (``colNames[i]`` / ``aria-describedby``) do not contain ``status`` — the UI still
    shows *Allocated* / *Pending* in ``raw`` (row ``innerText``).
    """
    parts = [
        (r.get("status") or "").strip(),
        (r.get("raw") or "").strip(),
        (r.get("invoice") or "").strip(),
    ]
    return " ".join(p for p in parts if p).lower()


def _my_orders_blob_looks_allocated(blob: str) -> bool:
    if not blob:
        return False
    if "not allocated" in blob or "deallocated" in blob:
        return False
    if "allocated" in blob or "allotted" in blob or "allotment" in blob:
        return True
    # Short status like "Alloc" (word) without matching Pending Allocation heuristics below
    if re.search(r"\balloc\b", blob):
        return True
    return False


def _my_orders_blob_looks_pending(blob: str) -> bool:
    if not blob or "pending" not in blob:
        return False
    # e.g. "Pending Allocation" → treat as allocated path, not pending-only
    if _my_orders_blob_looks_allocated(blob):
        return False
    return True


def _classify_my_orders_grid_rows(rows: list[dict]) -> tuple[str, str, str]:
    """
    From jqGrid row dicts, pick branching outcome and primary order/invoice.
    Returns ``(outcome, primary_order, primary_invoice)``.

    **Precedence:** **allocated** is checked before **pending** so a grid with both (e.g. older Pending
    rows plus one **Allocated** row) drills the Allocated **Order#** — matching operator expectation.
    """
    if not rows:
        return "no_rows", "", ""

    for r in rows:
        inv = (r.get("invoice") or "").strip()
        if _my_orders_invoice_meaningful(inv):
            return "invoiced", (r.get("order") or "").strip(), inv
    for r in rows:
        blob = _my_orders_row_text_blob(r)
        if _my_orders_blob_looks_allocated(blob):
            return "allocated", (r.get("order") or "").strip(), ""
    for r in rows:
        blob = _my_orders_row_text_blob(r)
        if _my_orders_blob_looks_pending(blob):
            return "pending", (r.get("order") or "").strip(), ""
    return "unknown_rows", (rows[0].get("order") or "").strip(), ""


def _find_vehicle_sales_my_orders_search_root(page: Page, content_frame_selector: str | None):
    """Frame (or page) that contains Find field ``name=s_1_1_1_0`` and Sales Orders New (+)."""
    roots: list = []
    try:
        roots.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
    except Exception:
        pass
    try:
        roots.extend(list(_ordered_frames(page)))
    except Exception:
        pass
    roots.append(page)
    for root in roots:
        try:
            dd = root.locator('[name="s_1_1_1_0"]').first
            if dd.count() <= 0 or not dd.is_visible(timeout=500):
                continue
            plus = root.locator(
                "a[aria-label='Sales Orders List:New'], button[aria-label='Sales Orders List:New'], "
                "a[aria-label*='Sales Orders List' i][aria-label*='New' i], "
                "button[aria-label*='Sales Orders List' i][aria-label*='New' i]"
            ).first
            if plus.count() > 0 and plus.is_visible(timeout=400):
                return root
        except Exception:
            continue
    for root in roots:
        try:
            dd = root.locator('[name="s_1_1_1_0"]').first
            if dd.count() > 0 and dd.is_visible(timeout=400):
                return root
        except Exception:
            continue
    return None


def _read_my_orders_jqgrid_rows_anywhere(page: Page, content_frame_selector: str | None) -> list[dict]:
    roots: list = []
    try:
        roots.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
    except Exception:
        pass
    try:
        roots.extend(list(_ordered_frames(page)))
    except Exception:
        pass
    roots.append(page)
    for root in roots:
        try:
            raw = root.evaluate(_JS_MY_ORDERS_JQGRID_ROWS)
            if isinstance(raw, list) and raw:
                return list(raw)
        except Exception:
            continue
    return []


_JS_CLICK_MY_ORDERS_ORDER_LINK = """({ orderNeedle, mobileDigits }) => {
    const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const od = String(orderNeedle || '').replace(/\\D/g, '');
    const md = String(mobileDigits || '').replace(/\\D/g, '');
    const rows = document.querySelectorAll(
        'table.ui-jqgrid-btable tbody tr.jqgrow, table.ui-jqgrid-btable tbody tr[role="row"]'
    );
    for (const tr of rows) {
        if (tr.classList.contains('jqgfirstrow')) continue;
        if (!vis(tr)) continue;
        const rowText = tr.innerText || '';
        if (md && !rowText.replace(/\\D/g, '').includes(md)) continue;
        const a = tr.querySelector("a[name='Order Number'], a[name='Order #']") || tr.querySelector('td a');
        if (!a || !vis(a)) continue;
        const ot = (a.textContent || '').trim();
        const otd = ot.replace(/\\D/g, '');
        if (od && otd && otd !== od && !otd.includes(od) && !od.includes(otd)) continue;
        try { a.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
        try { a.click(); } catch (e) {}
        return 'ok:' + ot;
    }
    return '';
}"""


def _click_my_orders_jqgrid_order_for_mobile_or_order(
    page: Page,
    *,
    mobile: str,
    order_number: str,
    content_frame_selector: str | None,
    note,
    action_timeout_ms: int,
) -> bool:
    """Open the sales order from My Orders jqGrid after mobile search (Pending / Allocated paths)."""
    nd = re.sub(r"\D", "", (mobile or "").strip())
    on = (order_number or "").strip()
    roots: list = []
    try:
        roots.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
    except Exception:
        pass
    try:
        roots.extend(list(_ordered_frames(page)))
    except Exception:
        pass
    roots.append(page)
    for root in roots:
        try:
            hit = root.evaluate(_JS_CLICK_MY_ORDERS_ORDER_LINK, {"orderNeedle": on, "mobileDigits": nd})
            if hit:
                note(f"Create Order: opened order from My Orders grid ({hit!r}).")
                _safe_page_wait(page, 1500, log_label="after_my_orders_jqgrid_order_click")
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


def _run_vehicle_sales_my_orders_mobile_search(
    page: Page,
    *,
    mobile: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> _MyOrdersGridSearchResult:
    """
    My Orders view: Find dropdown ``s_1_1_1_0`` → Mobile Phone# → value field → Enter → read ``ui-jqgrid-btable``.
    """
    _tmo = min(int(action_timeout_ms or 3000), 8000)
    root = _find_vehicle_sales_my_orders_search_root(page, content_frame_selector)
    if root is None:
        note("Create Order: My Orders Find (s_1_1_1_0) not found — treating as unknown_rows.")
        return _MyOrdersGridSearchResult(outcome="unknown_rows", error="find_root_missing")
    digits = re.sub(r"\D", "", (mobile or "").strip())
    if not digits:
        return _MyOrdersGridSearchResult(outcome="error", error="mobile_empty")
    try:
        dd = root.locator('[name="s_1_1_1_0"]').first
        dd.scroll_into_view_if_needed(timeout=_tmo)
        try:
            dd.click(timeout=_tmo)
        except Exception:
            dd.click(timeout=_tmo, force=True)
        _safe_page_wait(page, 200, log_label="after_my_orders_find_click")
        _picked = False
        try:
            tag = (dd.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
            if tag == "select":
                try:
                    dd.select_option(label=re.compile(r"mobile\s*phone", re.I))
                    _picked = True
                except Exception:
                    try:
                        dd.select_option(index=0)
                    except Exception:
                        pass
            else:
                try:
                    dd.press("Alt+ArrowDown", timeout=1200)
                except Exception:
                    pass
                _safe_page_wait(page, 250, log_label="my_orders_find_lov_open")
                for _ in range(24):
                    try:
                        dd.press("ArrowDown", timeout=400)
                    except Exception:
                        break
                    try:
                        tx = (dd.input_value(timeout=200) or dd.evaluate("el => el.value || el.textContent || ''") or "").lower()
                    except Exception:
                        tx = ""
                    if "mobile" in tx and "phone" in tx:
                        _picked = True
                        break
                if not _picked:
                    try:
                        dd.type("Mobile Phone", delay=40, timeout=2000)
                        _picked = True
                    except Exception:
                        pass
        except Exception as _e:
            note(f"Create Order: My Orders Find dropdown Mobile Phone# selection raised {_e!r} — continuing.")
        try:
            dd.press("Tab", timeout=1200)
        except Exception:
            page.keyboard.press("Tab")
        _safe_page_wait(page, 200, log_label="after_my_orders_find_tab_to_value")
        _filled = False
        for name in ("s_1_1_1_1", "s_1_1_1_2"):
            try:
                loc = root.locator(f'input[name="{name}"], [name="{name}"]').first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.click(timeout=800)
                    loc.fill("", timeout=500)
                    loc.type(digits, delay=25, timeout=3000)
                    _filled = True
                    note(f"Create Order: filled My Orders Find value field name={name!r}.")
                    break
            except Exception:
                continue
        if not _filled:
            try:
                page.keyboard.type(digits, delay=25)
                _filled = True
            except Exception:
                pass
        if not _filled:
            return _MyOrdersGridSearchResult(outcome="error", error="could_not_fill_find_value")
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass
        _safe_page_wait(page, 150, log_label="after_my_orders_value_tab")
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        _safe_page_wait(page, min(2500, _tmo), log_label="after_my_orders_find_enter")
        rows = _read_my_orders_jqgrid_rows_anywhere(page, content_frame_selector)
        oc, po, pi = _classify_my_orders_grid_rows(rows)
        note(
            f"Create Order: My Orders grid search outcome={oc!r} rows={len(rows)} "
            f"primary_order={po!r} primary_invoice={pi!r}."
        )
        return _MyOrdersGridSearchResult(outcome=oc, primary_order=po, primary_invoice=pi, rows=rows or [])
    except Exception as _ex:
        note(f"Create Order: My Orders mobile search failed: {_ex!r}")
        return _MyOrdersGridSearchResult(outcome="error", error=str(_ex))


def _attach_vehicle_to_bkg(
    page: Page,
    *,
    full_chassis: str,
    order_number: str = "",
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    start_at_order_link_before_apply: bool = False,
) -> tuple[bool, str | None, dict]:
    """
    After a new sales order is saved:
    1. Click Order Number / Order # header link to open order detail — **skipped** when line-item **New** or **VIN**
       is already visible (e.g. My Orders Order# drill-down).
    2. Click **New** → fill VIN → Price All → Allocate All.
    3. *(Currently disabled via `if False`.)* Single-click **VIN** drilldown → **Serial Number** →
       ``_siebel_run_vehicle_serial_detail_precheck_pdi`` (Pre-check + PDI through submit).
    4. Click ``Order:<order#>`` link → **Apply Campaign** → **Create Invoice** when
       ``_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE`` is True (default: enabled).

    After **Allocate All**, best-effort scrape of **Total (Ex-showroom)** into ``extra_dict`` as
    ``vehicle_price`` / ``vehicle_ex_showroom_cost`` (for ``vehicle_master.vehicle_ex_showroom_price``).
    When ``start_at_order_link_before_apply`` is True, skips the Order header through **Allocate All**
    and starts at the top **Order:<order#>** link (Step 10) — used when My Orders already shows
    **Allocated** stock.
    Returns ``(success, error_detail, extra_dict)``; ``extra_dict`` may be empty when scrape fails.
    """
    _tmo = min(int(action_timeout_ms or 3000), 4000)

    def _all_roots() -> list:
        r: list = []
        try:
            r.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
        except Exception:
            pass
        try:
            r.extend(list(_ordered_frames(page)))
        except Exception:
            pass
        r.append(page)
        return r

    def _click_by_id(element_id: str, label: str, wait_ms: int = 1000) -> bool:
        for root in _all_roots():
            try:
                loc = root.locator(f"#{element_id}").first
                if loc.count() > 0 and loc.is_visible(timeout=700):
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    note(f"attach_vehicle_to_bkg: clicked {label} (id={element_id!r}).")
                    _safe_page_wait(page, wait_ms, log_label=f"after_{label.replace(' ', '_').lower()}")
                    return True
            except Exception:
                continue
        for root in _all_roots():
            try:
                hit = root.evaluate(f"""() => {{
                    const el = document.getElementById("{element_id}");
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    el.scrollIntoView({{ block: 'center' }});
                    el.click();
                    return true;
                }}""")
                if hit:
                    note(f"attach_vehicle_to_bkg: JS clicked {label} (id={element_id!r}).")
                    _safe_page_wait(page, wait_ms, log_label=f"after_{label.replace(' ', '_').lower()}_js")
                    return True
            except Exception:
                continue
        return False

    def _click_by_name(name_val: str, label: str, wait_ms: int = 1000) -> bool:
        for root in _all_roots():
            for css in (f"[name='{name_val}']", f"a[name='{name_val}']", f"button[name='{name_val}']", f"input[name='{name_val}']"):
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        try:
                            loc.click(timeout=_tmo)
                        except Exception:
                            loc.click(timeout=_tmo, force=True)
                        note(f"attach_vehicle_to_bkg: clicked {label} (name={name_val!r}).")
                        _safe_page_wait(page, wait_ms, log_label=f"after_{label.replace(' ', '_').lower()}")
                        return True
                except Exception:
                    continue
        return False

    def _order_detail_already_open_for_line_items() -> bool:
        """My Orders Order# drill-down often lands on line items without a separate Order Number header link."""
        for sel in (
            "#s_1_1_35_0_Ctrl",
            "#s_1_1_35_0",
            "[id$='_35_0_Ctrl']",
            "input[id$='_l_VIN']",
            "input[name='VIN']",
        ):
            for root in _all_roots():
                try:
                    loc = root.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=450):
                        return True
                except Exception:
                    continue
        return False

    def _click_first_visible(root, selector: str, label: str, wait_ms: int) -> bool:
        try:
            loc = root.locator(selector).first
            if loc.count() > 0 and loc.is_visible(timeout=700):
                try:
                    loc.click(timeout=_tmo)
                except Exception:
                    loc.click(timeout=_tmo, force=True)
                note(f"attach_vehicle_to_bkg: clicked {label} (selector={selector!r}).")
                _safe_page_wait(page, wait_ms, log_label=f"after_{label.replace(' ', '_').lower()}")
                return True
        except Exception:
            pass
        return False

    if start_at_order_link_before_apply:
        note(
            "attach_vehicle_to_bkg: start_at_order_link_before_apply=True — "
            "skipping Order header through Allocate All; continuing at Order:<n> link before Apply Campaign."
        )
        _extra = {}
    else:
        # ── Step 1: Click Order Number header link (skip if line items already visible — e.g. My Orders drill-down)
        _order_clicked = False
        _order_selectors = (
            "a[name='Order Number'][tabindex='-1']",
            "a[name='Order Number']",
            "a[name='Order #'][tabindex='-1']",
            "a[name='Order #']",
        )
        for root in _all_roots():
            for css in _order_selectors:
                try:
                    loc = root.locator(css).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=900):
                        continue
                    try:
                        loc.scroll_into_view_if_needed(timeout=_tmo)
                    except Exception:
                        pass
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    note(f"attach_vehicle_to_bkg: clicked Order header drill-down via {css!r}.")
                    _safe_page_wait(page, 1500, log_label="after_attach_vehicle_to_bkg_click")
                    _order_clicked = True
                    break
                except Exception:
                    continue
            if _order_clicked:
                break
        if not _order_clicked:
            _js_order = """() => {
              const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              };
              let el = document.querySelector("a[name='Order Number'][tabindex='-1']");
              if (!el || !vis(el)) el = document.querySelector("a[name='Order Number']");
              if (!el || !vis(el)) el = document.querySelector("a[name='Order #'][tabindex='-1']");
              if (!el || !vis(el)) el = document.querySelector("a[name='Order #']");
              if (!el || !vis(el)) return '';
              try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
              el.click();
              return 'ok';
            }"""
            for frame in _ordered_frames(page):
                try:
                    if frame.evaluate(_js_order):
                        _order_clicked = True
                        note("attach_vehicle_to_bkg: JS clicked Order header drill-down in frame.")
                        _safe_page_wait(page, 1500, log_label="after_attach_vehicle_to_bkg_js_frame")
                        break
                except Exception:
                    continue
            if not _order_clicked:
                try:
                    if page.evaluate(_js_order):
                        _order_clicked = True
                        note("attach_vehicle_to_bkg: JS clicked Order header drill-down on main page.")
                        _safe_page_wait(page, 1500, log_label="after_attach_vehicle_to_bkg_js_page")
                except Exception:
                    pass
        if not _order_clicked and _order_detail_already_open_for_line_items():
            _order_clicked = True
            note(
                "attach_vehicle_to_bkg: skipped Order header drill-down — line items New/VIN already visible "
                "(e.g. after My Orders Order# open)."
            )
        if not _order_clicked:
            return False, "Could not click Order Number / Order # header link and order line items are not visible yet.", {}

        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        # ── Step 2: Click New button on order line / allocate (Hero: control id ends with _Ctrl) ──
        _new_clicked = _click_by_id("s_1_1_35_0_Ctrl", "New button", wait_ms=1200)
        if not _new_clicked:
            _new_clicked = _click_by_id("s_1_1_35_0", "New button (legacy id)", wait_ms=1200)
        if not _new_clicked:
            for root in _all_roots():
                if _click_first_visible(root, "[id$='_35_0_Ctrl']", "New button (id suffix _35_0_Ctrl)", wait_ms=1200):
                    _new_clicked = True
                    break
        if not _new_clicked:
            return False, "Could not click New button (id=s_1_1_35_0_Ctrl or id suffix _35_0_Ctrl) on order line items.", {}
    
        # ── Step 3: Line-item VIN — same selector family as Sales Orders ``name=VIN`` path; row id may be
        # ``1_s_1_l_VIN``, ``2_s_1_l_VIN``, etc. Use **locator.type** (not ``page.keyboard``) so iframe focus works.
        _ch = (full_chassis or "").strip()
        if not _ch:
            return False, "attach_vehicle_to_bkg: full_chassis is empty (line-item VIN).", {}
    
        _safe_page_wait(page, 500, log_label="after_new_before_vin_field")
        _vin_locator_css: tuple[str, ...] = (
            "#1_s_1_l_VIN",
            "[id='1_s_1_l_VIN']",
            "input[id$='_l_VIN']",
            "input[id*='_l_VIN' i]",
            "input[name='VIN']",
            "input[aria-label='VIN']",
            "input[title='VIN']",
            "input[title*='VIN' i]",
        )
    
        def _vin_readback_ok(vin_loc) -> bool:
            try:
                got = (vin_loc.input_value(timeout=900) or "").strip()
            except Exception:
                got = ""
            if not got:
                return False
            _digits = lambda s: re.sub(r"\D", "", s)
            return _ch in got or _digits(_ch) in _digits(got) or len(_digits(got)) >= 8
    
        def _js_set_vin_value_on_element(vin_loc) -> None:
            """Siebel line inputs often ignore Playwright fill/type; set value + InputEvent on the node."""
            try:
                import json as _json
    
                _v = _json.dumps(_ch)
                vin_loc.evaluate(
                    f"""(el) => {{
                      const v = {_v};
                      try {{ el.focus(); }} catch (e) {{}}
                      el.value = '';
                      el.value = v;
                      try {{
                        el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertFromPaste', data: v }}));
                      }} catch (e) {{
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                      }}
                      el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}"""
                )
            except Exception:
                pass
    
        def _tab_out_vin(vin_loc) -> None:
            try:
                vin_loc.press("Tab", timeout=1500)
            except Exception:
                pass
            try:
                page.keyboard.press("Tab")
            except Exception:
                pass
    
        def _try_fill_vin_locator(vin_loc) -> bool:
            try:
                vin_loc.scroll_into_view_if_needed(timeout=_tmo)
            except Exception:
                pass
            vin_loc.click(timeout=_tmo)
            _safe_page_wait(page, 220, log_label="after_vin_click")
            try:
                vin_loc.focus(timeout=1200)
            except Exception:
                pass
            try:
                vin_loc.press("Control+a", timeout=800)
            except Exception:
                pass
            try:
                vin_loc.fill("", timeout=1000)
            except Exception:
                pass
            _typed = False
            try:
                page.keyboard.type(_ch, delay=28)
                _typed = True
            except Exception:
                pass
            if not _vin_readback_ok(vin_loc):
                try:
                    vin_loc.type(_ch, delay=28, timeout=min(8000, int(action_timeout_ms or 3000)))
                    _typed = True
                except Exception:
                    pass
            if not _vin_readback_ok(vin_loc):
                try:
                    vin_loc.fill(_ch, timeout=2000)
                except Exception:
                    pass
            if not _vin_readback_ok(vin_loc):
                _js_set_vin_value_on_element(vin_loc)
            if not _vin_readback_ok(vin_loc):
                return False
            _tab_out_vin(vin_loc)
            return True
    
        _vin_filled = False
        for root in _all_roots():
            for css in _vin_locator_css:
                try:
                    vin_loc = root.locator(css).first
                    if vin_loc.count() <= 0 or not vin_loc.is_visible(timeout=700):
                        continue
                    if _try_fill_vin_locator(vin_loc):
                        _vin_filled = True
                        note(f"attach_vehicle_to_bkg: VIN filled via {css!r}, chassis={_ch!r}.")
                        break
                except Exception:
                    continue
            if _vin_filled:
                break
    
        _js_vin_pick = """(chassis) => {
          const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width >= 2 && r.height >= 2;
          };
          const c = String(chassis || '');
          let el = document.getElementById('1_s_1_l_VIN');
          if (!el || !vis(el)) {
            const cands = Array.from(document.querySelectorAll(
              "input[id$='_l_VIN'], input[name='VIN'], input[aria-label='VIN'], input[title='VIN']"
            ));
            el = cands.find((e) => vis(e)) || null;
          }
          if (!el) return false;
          try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
          try { el.focus(); } catch (e) {}
          el.value = '';
          el.value = c;
          try {
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: c }));
          } catch (e) {
            el.dispatchEvent(new Event('input', { bubbles: true }));
          }
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        }"""
    
        if not _vin_filled:
            for root in _all_roots():
                try:
                    if bool(root.evaluate(_js_vin_pick, _ch)):
                        _vin_filled = True
                        note(f"attach_vehicle_to_bkg: JS set VIN field (broad query), chassis={_ch!r}.")
                        _safe_page_wait(page, 200, log_label="after_vin_js_fill")
                        try:
                            page.keyboard.press("Tab")
                        except Exception:
                            pass
                        try:
                            page.keyboard.press("Tab")
                        except Exception:
                            pass
                        break
                except Exception:
                    continue
    
        if not _vin_filled:
            return False, f"Could not fill line-item VIN (selectors id/_l_VIN/name=VIN) with {_ch!r}.", {}
        _safe_page_wait(page, 2800, log_label="after_vin_tab_settle")
    
        # ── Step 4: Click Price All (name="s_1_1_7_0") ──
        if not _click_by_name("s_1_1_7_0", "Price All", wait_ms=2000):
            return False, "Could not click Price All (name=s_1_1_7_0).", {}
        _pa_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _pa_err:
            note(f"attach_vehicle_to_bkg: Siebel error after Price All → {_pa_err!r:.300}")
            return False, f"Siebel error after Price All: {_pa_err[:200]}", {}
    
        # ── Step 5: Click Allocate All (id="s_1_1_9_0_Ctrl") ──
        if not _click_by_id("s_1_1_9_0_Ctrl", "Allocate All", wait_ms=2000):
            return False, "Could not click Allocate All (id=s_1_1_9_0_Ctrl).", {}
        _aa_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _aa_err:
            note(f"attach_vehicle_to_bkg: Siebel error after Allocate All → {_aa_err!r:.300}")
            return False, f"Siebel error after Allocate All: {_aa_err[:200]}", {}
    
        _extra: dict = {}
        _safe_page_wait(page, 1200, log_label="after_allocate_all_before_ex_showroom_scrape")
        _ex_raw = _scrape_total_ex_showroom_after_price_allocate(
            page, content_frame_selector=content_frame_selector
        )
        if _ex_raw and _looks_like_ex_showroom_price(_ex_raw):
            _extra["vehicle_ex_showroom_cost"] = _ex_raw
            _extra["vehicle_price"] = _ex_raw
            note(f"attach_vehicle_to_bkg: scraped Total (Ex-showroom)={_ex_raw!r} after Price All + Allocate All.")
        else:
            note(
                "attach_vehicle_to_bkg: Total (Ex-showroom) not scraped or not numeric after Allocate All "
                "(best-effort)."
            )
    
        note(
            "attach_vehicle_to_bkg: skipped VIN drilldown, Serial Number, and Pre-check/PDI through PDI submit (disabled)."
        )
        if False:  # restore: re-enable post-Allocate vehicle tab + serial Pre-check/PDI
            # ── Step 6: Click VIN drilldown (name="VIN") → opens Vehicles tab ──
            if not _click_by_name("VIN", "VIN drilldown", wait_ms=2000):
                return False, "Could not click VIN drilldown (name=VIN) to open Vehicles tab.", {}
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
        
            # ── Step 7: Click Serial Number (name="Serial Number") ──
            if not _click_by_name("Serial Number", "Serial Number", wait_ms=2000):
                return False, "Could not click Serial Number (name='Serial Number') on Vehicles tab.", {}
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
        
            # ── Steps 8–9: Pre-check + PDI only (no DOM field scrapes; shared with ``prepare_vehicle`` semantics).
            _pc_ok, _pc_err = _siebel_run_vehicle_serial_detail_precheck_pdi(
                page,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                form_trace=None,
                log_prefix="attach_vehicle_to_bkg",
                scraped=None,
            )
            if not _pc_ok:
                return False, _pc_err or "Pre-check / PDI failed after Serial Number drilldown.", {}
    

    # ── Step 10: Click "Order:<order#>" link at top of page ──
    _order_link_clicked = False
    _order_num = (order_number or "").strip()
    if _order_num:
        _order_link_pattern = f"Order:{_order_num}"
        for root in _all_roots():
            try:
                _ol_result = root.evaluate(f"""(pat) => {{
                    const vis = (el) => {{
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (st.display === 'none' || st.visibility === 'hidden') return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }};
                    const links = Array.from(document.querySelectorAll('a'));
                    for (const a of links) {{
                        const ih = (a.innerHTML || '').trim();
                        if (ih.includes(pat) && vis(a)) {{
                            a.scrollIntoView({{ block: 'center' }});
                            a.click();
                            return ih;
                        }}
                    }}
                    return '';
                }}""", _order_link_pattern)
                if _ol_result:
                    _order_link_clicked = True
                    note(f"attach_vehicle_to_bkg: clicked Order link ({_ol_result!r}).")
                    break
            except Exception:
                continue
    if not _order_link_clicked:
        # Fallback: try any anchor whose text contains "Order:" or the order number
        for root in _all_roots():
            try:
                if _order_num:
                    loc = root.locator(f"a:has-text('Order:{_order_num}')").first
                else:
                    loc = root.locator("a:has-text('Order:')").first
                if loc.count() > 0 and loc.is_visible(timeout=700):
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    _order_link_clicked = True
                    note("attach_vehicle_to_bkg: clicked Order link via Playwright locator.")
                    break
            except Exception:
                continue
    if not _order_link_clicked:
        return False, f"Could not click 'Order:{_order_num}' link at top of page.", {}
    _safe_page_wait(page, 2000, log_label="after_order_link_click")
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    _err_after_order = _poll_and_handle_siebel_error_popup(
        page,
        content_frame_selector,
        note,
        context="attach_vehicle_to_bkg after Order# header link",
        total_ms=1400,
        step_ms=300,
    )
    if _err_after_order:
        return False, f"Siebel error after Order# link: {_err_after_order[:200]}", {}

    # ── Step 11: Click "Apply Campaign" button ──
    _ac_clicked = False
    _ac_selectors = [
        "button:has-text('Apply Campaign')", "a:has-text('Apply Campaign')",
        "input[type='button'][value='Apply Campaign' i]",
        "[aria-label*='Apply Campaign' i]",
        "[title*='Apply Campaign' i]",
    ]
    for root in _all_roots():
        if _ac_clicked:
            break
        for css in _ac_selectors:
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=700):
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    _ac_clicked = True
                    break
            except Exception:
                continue
    if not _ac_clicked:
        # JS fallback
        for root in _all_roots():
            try:
                hit = root.evaluate("""() => {
                    const vis = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (st.display === 'none' || st.visibility === 'hidden') return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    };
                    const all = Array.from(document.querySelectorAll('button, a, input[type="button"]'));
                    for (const el of all) {
                        const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                        if (/apply\\s*campaign/i.test(t) && vis(el)) { el.click(); return true; }
                    }
                    return false;
                }""")
                if hit:
                    _ac_clicked = True
                    break
            except Exception:
                continue
    if not _ac_clicked:
        return False, "Could not click 'Apply Campaign' button.", {}
    note("attach_vehicle_to_bkg: clicked Apply Campaign.")
    _safe_page_wait(page, 1500, log_label="after_apply_campaign")

    _ac_err = _detect_siebel_error_popup(page, content_frame_selector)
    if _ac_err:
        note(f"attach_vehicle_to_bkg: Siebel error after Apply Campaign → {_ac_err!r:.300}")
        return False, f"Siebel error after Apply Campaign: {_ac_err[:200]}", {}

    # ── Step 12: Create Invoice (optional; **off by default** — leave auto-click disabled until product asks) ──
    # When enabled, add a short settle wait after click, then optionally re-run
    # ``_scrape_order_number_current`` / ``_scrape_invoice_number_current`` if the UI refreshes
    # Order#/Invoice# for ``sales_master``. The video ``_create_order`` path already scrapes those
    # after ``_attach_vehicle_to_bkg`` when this flag is False.
    if _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE:
        _ci_clicked = False
        _ci_selectors = [
            "button:has-text('Create Invoice')", "a:has-text('Create Invoice')",
            "input[type='button'][value='Create Invoice' i]",
            "[aria-label*='Create Invoice' i]",
            "[title*='Create Invoice' i]",
        ]
        for root in _all_roots():
            if _ci_clicked:
                break
            for css in _ci_selectors:
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        try:
                            loc.click(timeout=_tmo)
                        except Exception:
                            loc.click(timeout=_tmo, force=True)
                        _ci_clicked = True
                        break
                except Exception:
                    continue
        if not _ci_clicked:
            for root in _all_roots():
                try:
                    hit = root.evaluate("""() => {
                        const vis = (el) => {
                            if (!el) return false;
                            const st = window.getComputedStyle(el);
                            if (st.display === 'none' || st.visibility === 'hidden') return false;
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        };
                        const all = Array.from(document.querySelectorAll('button, a, input[type="button"]'));
                        for (const el of all) {
                            const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                            if (/create\\s*invoice/i.test(t) && vis(el)) { el.click(); return true; }
                        }
                        return false;
                    }""")
                    if hit:
                        _ci_clicked = True
                        break
                except Exception:
                    continue
        if not _ci_clicked:
            return False, "Could not click 'Create Invoice' button.", {}
        note("attach_vehicle_to_bkg: clicked Create Invoice.")
        _ci_err = ""
        for _ci_poll in range(4):
            _safe_page_wait(page, 800, log_label=f"create_invoice_error_poll_{_ci_poll}")
            _ci_err = _detect_siebel_error_popup(page, content_frame_selector) or ""
            if _ci_err:
                break
        if _ci_err:
            note(f"attach_vehicle_to_bkg: Siebel error after Create Invoice → {_ci_err!r:.300}")
            return False, f"Siebel error after Create Invoice: {_ci_err[:200]}", {}
    else:
        note(
            "attach_vehicle_to_bkg: Create Invoice not auto-clicked "
            "(set _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE=False in hero_dms_playwright_invoice.py to disable)."
        )

    note(
        "attach_vehicle_to_bkg: all steps completed "
        "(Order → VIN → Pre-check → PDI → Apply Campaign"
        + (" → Create Invoice" if _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE else "")
        + ")."
    )
    # When Create Invoice is auto-clicked, a follow-up scrape of refreshed Order#/Invoice# can be added here.
    return True, None, _extra


def _create_order(
    page: Page,
    *,
    mobile: str,
    first_name: str,
    full_chassis: str,
    financier_name: str,
    contact_id: str = "",
    battery_partial: str = "",
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
) -> tuple[bool, str | None, dict]:
    """
    Vehicle Sales → Sales Orders flow (same frame as the ``+`` New control):

    - After opening **My Orders**, run ``_run_vehicle_sales_my_orders_mobile_search`` (Find ``s_1_1_1_0`` →
      Mobile Phone# → mobile → Enter → ``ui-jqgrid-btable``):

      - **invoiced** (meaningful Invoice# on a row): return success with Order#/Invoice# scrape and
        ``ready_for_client_create_invoice=True`` (skip ``+`` booking and attach).
      - **pending**: drill Order# on the matching row, then ``_attach_vehicle_to_bkg`` (full path).
      - **allocated**: drill Order#, then ``_attach_vehicle_to_bkg(..., start_at_order_link_before_apply=True)``.
      - **unknown_rows** with Order# row(s) and no Invoice# on any row: coerce to **allocated** attach (same drill/skip) instead of **+**; otherwise **no_rows** / **unknown_rows** / **error** fall back to the full **+** new-booking path below.

    - **+** path: Sales Orders New:List, Booking Order Type = Normal Booking, optional Comments (battery),
      finance fields, Contact Last Name F2 applet, Ctrl+S, then ``_attach_vehicle_to_bkg`` from the new order.
    """
    scraped: dict = {"inventory_location": "", "vehicle_price": "", "order_number": "", "invoice_number": ""}

    def _roots():
        return _siebel_locator_search_roots(page, content_frame_selector)

    def _all_ui_roots():
        roots = []
        try:
            roots.extend(list(_roots()))
        except Exception:
            pass
        try:
            roots.extend(list(_ordered_frames(page)))
        except Exception:
            pass
        roots.append(page)
        return roots

    def _click_any(selectors: tuple[str, ...], *, timeout: int = 1200) -> bool:
        for root in _roots():
            for css in selectors:
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        try:
                            loc.click(timeout=timeout)
                        except Exception:
                            loc.click(timeout=timeout, force=True)
                        return True
                except Exception:
                    continue
        return False

    def _fill_any(selectors: tuple[str, ...], value: str, *, timeout: int = 2000) -> bool:
        v = (value or "").strip()
        if not v:
            return False
        for root in _roots():
            for css in selectors:
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        try:
                            loc.click(timeout=timeout)
                        except Exception:
                            loc.click(timeout=timeout, force=True)
                        try:
                            loc.fill(v, timeout=timeout)
                        except Exception:
                            try:
                                loc.press("Control+a", timeout=timeout)
                            except Exception:
                                pass
                            loc.type(v, delay=20, timeout=timeout)
                        return True
                except Exception:
                    continue
        return False

    def _scrape_order_number_current() -> str:
        """Best-effort scrape of Order# from current Vehicle Sales context."""
        for root in _roots():
            try:
                v = root.evaluate(
                    """() => {
                        const vis = (el) => {
                            if (!el) return false;
                            const st = window.getComputedStyle(el);
                            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                            const r = el.getBoundingClientRect();
                            return r.width > 2 && r.height > 2;
                        };
                        const tryInputs = Array.from(document.querySelectorAll(
                            "input[aria-label*='Order' i], input[title*='Order' i], input[name*='Order' i], input[id*='Order' i]"
                        ));
                        for (const el of tryInputs) {
                            if (!vis(el)) continue;
                            const val = (el.value || '').trim();
                            if (val && /[A-Za-z0-9-]{4,}/.test(val)) return val;
                        }
                        const tryLinks = Array.from(document.querySelectorAll(
                            "a[name='Order Number'], a[name='Order #'], a[aria-label*='Order' i], a[title*='Order' i], td[aria-describedby*='Order' i] a"
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
                    return str(v).strip()
            except Exception:
                continue
        return ""

    def _scrape_invoice_number_current() -> str:
        """Best-effort Invoice# from order/invoice header fields (exclude bare Order-only labels)."""
        for root in _roots():
            try:
                v = root.evaluate(
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
                            "input[aria-label*='Invoice' i], input[title*='Invoice' i], input[name*='Invoice' i], input[id*='Invoice' i]"
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
                            "a[name='Invoice Number'], a[name='Invoice #'], a[aria-label*='Invoice' i], a[title*='Invoice' i]"
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
                    return str(v).strip()
            except Exception:
                continue
        return ""

    if callable(form_trace):
        form_trace(
            "v4_create_order",
            "Vehicle Sales / Sales Orders",
            "open_vehicle_sales_then_create_order",
            mobile_phone=mobile,
            full_chassis=full_chassis,
        )

    # 1) Navigate to Vehicle Sales view directly via URL (same pattern as Contact URL navigation in v1).
    #    The Find combobox in Siebel is a jQuery UI autocomplete — get_by_role("combobox") finds the wrong
    #    element (the Contact page search input). Direct URL navigation is the only reliable approach.
    import os as _os
    try:
        from urllib.parse import urlparse as _up
        _purl = _up(page.url)
        _base_url = f"{_purl.scheme}://{_purl.netloc}{_purl.path}"
    except Exception:
        _base_url = "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    _vs_url = f"{_base_url}?SWECmd=GotoView&SWEView=Order+Entry+-+My+Orders+View+(Sales)&SWERF=1&SWEHo=&SWEBU=1"
    note(f"Create Order: navigating to Vehicle Sales URL. base={_base_url[:60]}")
    try:
        page.goto(_vs_url, timeout=min(action_timeout_ms * 3, 45000), wait_until="load")
    except Exception:
        try:
            page.goto(_vs_url, timeout=min(action_timeout_ms * 3, 45000), wait_until="domcontentloaded")
        except Exception as _e:
            note(f"Create Order: goto Vehicle Sales URL raised {_e!r} — continuing.")
    _siebel_after_goto_wait(page, floor_ms=4500)
    note(f"Create Order: arrived at Vehicle Sales (post-goto wait). URL={page.url[:120]}")

    _mos = _run_vehicle_sales_my_orders_mobile_search(
        page,
        mobile=mobile,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    )
    _mo_oc = (_mos.outcome or "").strip()
    _mo_po = (_mos.primary_order or "").strip()
    _mo_pi = (_mos.primary_invoice or "").strip()

    # Grid returned rows but status/invoice columns did not classify (e.g. nonstandard header ids). If we
    # have Order# links and no meaningful Invoice# on any row, prefer allocated-style attach (Order# drill
    # then skip to pre–Apply Campaign) instead of creating a duplicate booking via '+'.
    if _mo_oc == "unknown_rows" and _mos.rows:
        _rows = _mos.rows
        _has_order = any((r.get("order") or "").strip() for r in _rows)
        _any_inv = any(_my_orders_invoice_meaningful((r.get("invoice") or "").strip()) for r in _rows)
        if _has_order and not _any_inv:
            _picked = ""
            for r in _rows:
                if _my_orders_blob_looks_allocated(_my_orders_row_text_blob(r)):
                    _picked = (r.get("order") or "").strip()
                    if _picked:
                        break
            if not _picked:
                for r in _rows:
                    _picked = (r.get("order") or "").strip()
                    if _picked:
                        break
            if _picked:
                note(
                    "Create Order: My Orders grid unknown_rows with Order# row(s) and no Invoice# — "
                    "using allocated attach path (Order# drill, skip to pre–Apply Campaign)."
                )
                _mo_po = _picked
                _mo_oc = "allocated"

    def _finalize_my_orders_attach(branch: str) -> tuple[bool, str | None, dict]:
        """After ``_attach_vehicle_to_bkg`` from a My Orders grid drill-down."""
        _att_ok, _att_err, _att_scraped = _attach_vehicle_to_bkg(
            page,
            full_chassis=full_chassis,
            order_number=_mo_po or "",
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            start_at_order_link_before_apply=(branch == "allocated"),
        )
        scraped["order_drilldown_opened"] = bool(_att_ok)
        scraped["my_orders_branch"] = branch
        if _att_scraped:
            scraped.update(_att_scraped)
        if not _att_ok:
            return False, (_att_err or "attach_vehicle_to_bkg failed.").strip(), scraped
        _safe_page_wait(page, 900, log_label=f"after_my_orders_{branch}_attach")
        order_ref = _scrape_order_number_current()
        if order_ref:
            scraped["order_number"] = order_ref
        inv_no = _scrape_invoice_number_current()
        scraped["invoice_number"] = (inv_no or scraped.get("invoice_number") or "")
        if callable(form_trace):
            form_trace(
                "v4_create_order",
                "Vehicle Sales — My Orders branch",
                f"attach_vehicle_to_bkg_my_orders_{branch}",
                order_number=str(scraped.get("order_number") or ""),
                invoice_number=str(scraped.get("invoice_number") or ""),
            )
        return True, None, scraped

    if _mo_oc == "invoiced":
        scraped["order_number"] = _mo_po
        scraped["invoice_number"] = _mo_pi
        scraped["my_orders_branch"] = "invoiced"
        scraped["ready_for_client_create_invoice"] = True
        note(
            "Create Order: My Orders grid shows Invoice# — skipping '+' booking/attach; "
            "operator may use Create Invoice on the client app."
        )
        return True, None, scraped

    if _mo_oc == "pending":
        if not _click_my_orders_jqgrid_order_for_mobile_or_order(
            page,
            mobile=mobile,
            order_number=_mo_po,
            content_frame_selector=content_frame_selector,
            note=note,
            action_timeout_ms=action_timeout_ms,
        ):
            return False, "Create Order: Pending My Orders row but could not open Order# drill-down.", scraped
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        _err_mo_p = _poll_and_handle_siebel_error_popup(
            page,
            content_frame_selector,
            note,
            context="Create Order after My Orders Order# click (pending)",
            total_ms=1400,
            step_ms=300,
        )
        if _err_mo_p:
            return (
                False,
                f"Create Order: Siebel error after My Orders Order# click (pending): {_err_mo_p[:220]}",
                scraped,
            )
        _safe_page_wait(page, 1800, log_label="after_my_orders_pending_drilldown")
        scraped["order_number"] = _mo_po or scraped.get("order_number") or ""
        return _finalize_my_orders_attach("pending")

    if _mo_oc == "allocated":
        if not _click_my_orders_jqgrid_order_for_mobile_or_order(
            page,
            mobile=mobile,
            order_number=_mo_po,
            content_frame_selector=content_frame_selector,
            note=note,
            action_timeout_ms=action_timeout_ms,
        ):
            return False, "Create Order: Allocated My Orders row but could not open Order# drill-down.", scraped
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        _err_mo_a = _poll_and_handle_siebel_error_popup(
            page,
            content_frame_selector,
            note,
            context="Create Order after My Orders Order# click (allocated)",
            total_ms=1400,
            step_ms=300,
        )
        if _err_mo_a:
            return (
                False,
                f"Create Order: Siebel error after My Orders Order# click (allocated): {_err_mo_a[:220]}",
                scraped,
            )
        _safe_page_wait(page, 1800, log_label="after_my_orders_allocated_drilldown")
        scraped["order_number"] = _mo_po or scraped.get("order_number") or ""
        return _finalize_my_orders_attach("allocated")

    if _mo_oc == "error":
        note(f"Create Order: My Orders mobile search error={_mos.error!r} — falling back to '+' new booking.")
    elif _mo_oc == "unknown_rows":
        note("Create Order: My Orders grid unknown_rows — falling back to '+' new booking.")
    elif _mo_oc == "no_rows":
        note("Create Order: My Orders grid empty for this mobile — full '+' new booking path.")
    else:
        note(f"Create Order: My Orders outcome={_mo_oc!r} — full '+' new booking path.")

    # JS snippet run inside each frame to find and click the first Order Number drill-down link.
    # Siebel renders the link as <a name="Order Number">ORDER-VALUE</a> inside a grid td.
    _JS_CLICK_ORDER_LINK = """() => {
        const selectors = [
            "a[name='Order Number']",
            "a[name='Order #']",
            "td[aria-describedby*='Order_Number'] a",
            "td[aria-describedby*='Order#'] a",
            "td[headers*='Order_Number'] a",
            "a[aria-label*='Order Number']",
            "a[aria-label*='Order #']",
            "a[title*='Order Number']",
            "a[title*='Order #']"
        ];
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && vis(el)) {
                el.click();
                return 'clicked:' + sel;
            }
        }
        // Fallback: first <a> inside any cell whose column header contains 'order'
        const headers = Array.from(document.querySelectorAll('th, thead td'));
        let orderColIdx = -1;
        for (const h of headers) {
            if ((h.innerText || '').toLowerCase().includes('order')) {
                // find its column index
                const tr = h.closest('tr');
                if (tr) {
                    const ths = Array.from(tr.querySelectorAll('th, td'));
                    orderColIdx = ths.indexOf(h);
                    break;
                }
            }
        }
        if (orderColIdx >= 0) {
            const tbody = document.querySelector('table tbody') || document.querySelector('table');
            if (tbody) {
                const rows = tbody.querySelectorAll('tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length > orderColIdx) {
                        const a = cells[orderColIdx].querySelector('a');
                        if (a && vis(a)) { a.click(); return 'clicked:col-hdr-idx=' + orderColIdx; }
                    }
                }
            }
        }
        return '';
    }"""

    _JS_CLICK_ORDER_LINK_MATCH_MOBILE = """(needle) => {
        const n = String(needle || '').replace(/\\D/g, '');
        if (!n) return '';
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        const rowSels = [
            'table.ui-jqgrid-btable tbody tr',
            'div.ui-jqgrid-bdiv table tbody tr',
            'table.siebui-list tbody tr',
            'table tbody tr',
            '[role="row"]'
        ];
        for (const rs of rowSels) {
            const rows = document.querySelectorAll(rs);
            for (const tr of rows) {
                if (tr.closest('thead')) continue;
                if (!vis(tr)) continue;
                const digits = (tr.innerText || '').replace(/\\D/g, '');
                if (!digits.includes(n)) continue;
                const a = tr.querySelector(
                    "a[name='Order Number'], a[name='Order #'], td[aria-describedby*='Order_Number'] a, td[aria-describedby*='Order#'] a"
                ) || tr.querySelector('td a');
                if (a && vis(a)) {
                    try { a.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                    a.click();
                    return 'mobile-match:' + rs;
                }
            }
        }
        return '';
    }"""

    def _click_order_link_via_js() -> bool:
        """Try JS evaluate on every frame to click the Order Number drill-down link."""
        for frame in _ordered_frames(page):
            try:
                result = frame.evaluate(_JS_CLICK_ORDER_LINK)
                if result:
                    note(f"Create Order: JS clicked Order link in frame. result={result!r}")
                    return True
            except Exception:
                continue
        return False

    def _click_order_link_js_row_matching_mobile() -> bool:
        """Prefer Order# link on the grid row that contains the customer's mobile digits."""
        nd = re.sub(r"\D", "", (mobile or "").strip())
        if not nd:
            return False
        for frame in _ordered_frames(page):
            try:
                result = frame.evaluate(_JS_CLICK_ORDER_LINK_MATCH_MOBILE, nd)
                if result:
                    note(f"Create Order: JS clicked Order# on mobile-matched row. result={result!r}")
                    return True
            except Exception:
                continue
        return False

    def _click_order_link_via_playwright() -> bool:
        """Try Playwright locators across all roots to click the Order Number link."""
        for root in _roots():
            for css in (
                "a[name='Order Number']",
                "a[name='Order #']",
                "td[aria-describedby*='Order_Number' i] a",
                "td[aria-describedby*='Order#' i] a",
                "a[aria-label*='Order Number' i]",
                "a[aria-label*='Order #' i]",
                "a[title*='Order Number' i]",
                "a[title*='Order #' i]",
            ):
                try:
                    loc = root.locator(css).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=600):
                        continue
                    try:
                        loc.click(timeout=min(action_timeout_ms, 3000))
                    except Exception:
                        loc.click(timeout=min(action_timeout_ms, 3000), force=True)
                    note(f"Create Order: Playwright clicked Order link css={css!r}")
                    return True
                except Exception:
                    continue
        return False

    def _open_order_link(attempt_label: str) -> bool:
        """Mobile-matched row first, then first visible Order# (JS / Playwright)."""
        note(f"Create Order: attempting Order# click ({attempt_label}).")
        if _click_order_link_js_row_matching_mobile():
            return True
        if _click_order_link_via_js():
            return True
        if _click_order_link_via_playwright():
            return True
        return False

    if True:  # Full '+' new booking; My Orders grid branches above return early when applicable.
        # 2) Click + (Sales Orders New:List)
        if not _click_any(
            (
                "a[aria-label='Sales Orders List:New']",
                "button[aria-label='Sales Orders List:New']",
                "a[title='Sales Orders List:New']",
                "button[title='Sales Orders List:New']",
                "a[aria-label*='Sales Orders List' i][aria-label*='New' i]",
                "button[aria-label*='Sales Orders List' i][aria-label*='New' i]",
            ),
            timeout=min(action_timeout_ms, 3000),
        ):
            return False, "Could not click Sales Orders New:List (+).", scraped
        _safe_page_wait(page, 1000, log_label="after_sales_orders_new")
        note("Create Order: clicked Sales Orders New:List (+).")

        # 3) Booking Order Type = Normal Booking
        def _set_booking_order_type_normal() -> bool:
            # Preferred: explicit value set via fill/dropdown helper.
            for root in _roots():
                try:
                    if _fill_by_label_on_frame(root, "Booking Order Type", "Normal Booking", action_timeout_ms=action_timeout_ms):
                        return True
                    if _select_dropdown_by_label_on_frame(
                        root,
                        label="Booking Order Type",
                        value="Normal Booking",
                        action_timeout_ms=min(action_timeout_ms, 8000),
                    ):
                        return True
                except Exception:
                    continue

            # Fallback: treat as bounded dropdown and pick first value via keyboard.
            for root in _roots():
                try:
                    loc = root.get_by_label(re.compile(r"booking\s*order\s*type", re.I)).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=700):
                        continue
                    try:
                        loc.click(timeout=min(action_timeout_ms, 2500))
                    except Exception:
                        loc.click(timeout=min(action_timeout_ms, 2500), force=True)
                    # Open LOV/dropdown and choose first entry (operator-confirmed Normal Booking).
                    try:
                        loc.press("Alt+ArrowDown", timeout=1200)
                    except Exception:
                        pass
                    _safe_page_wait(page, 200, log_label="booking_order_type_open")
                    try:
                        loc.press("ArrowDown", timeout=1200)
                    except Exception:
                        pass
                    loc.press("Enter", timeout=1200)
                    _safe_page_wait(page, 250, log_label="booking_order_type_pick_first")
                    val = ""
                    try:
                        val = (loc.input_value(timeout=700) or "").strip()
                    except Exception:
                        pass
                    if not val:
                        continue
                    # Accept exact or partial normalized result.
                    if "normal" in val.lower() and "booking" in val.lower():
                        return True
                    # Some Siebel LOVs show compact code values after selection.
                    return True
                except Exception:
                    continue
            return False

        _set_ok = False
        _set_ok = _set_booking_order_type_normal()
        if not _set_ok:
            return False, "Could not set Booking Order Type = Normal Booking.", scraped
        _safe_page_wait(page, 600, log_label="after_booking_order_type")
        note("Create Order: set Booking Order Type = Normal Booking.")

        _bp = (battery_partial or "").strip()
        if _bp:
            _comments_text = f"Battery is {_bp}"
            _filled_comments = False
            for root in _roots():
                try:
                    if _fill_by_label_on_frame(
                        root, "Comments", _comments_text, action_timeout_ms=action_timeout_ms
                    ):
                        _filled_comments = True
                        break
                    if _fill_by_label_on_frame(
                        root, "Comment", _comments_text, action_timeout_ms=action_timeout_ms
                    ):
                        _filled_comments = True
                        break
                except Exception:
                    continue
            if _filled_comments:
                note(f"Create Order: filled Comments → {_comments_text!r}.")
                if callable(form_trace):
                    form_trace(
                        "v4_create_order_comments",
                        "Sales order / create booking",
                        "fill_Comments_battery_from_detail_sheet",
                        comments=_comments_text,
                    )
                _safe_page_wait(page, 350, log_label="after_booking_comments")
            else:
                note(
                    "Create Order: could not fill Comments with battery line (best-effort); "
                    f"intended text was {_comments_text!r}."
                )

        _locked_root = None

        # 3b-3d) Finance Required, Financier, Hypothecation
        _fin_name = (financier_name or "").strip()
        _is_financed = bool(_fin_name)
        _fin_val = "Y" if _is_financed else "N"
        # #region agent log — finance branch input
        try:
            with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                import json as _j_fin, time as _t_fin
                _fin_token = _fin_name.lower()
                _lf.write(_j_fin.dumps({
                    "sessionId": "08e634",
                    "runId": "pre-fix",
                    "hypothesisId": "H1_H2",
                    "location": "hero_dms_playwright_invoice.py:_create_order_finance_input",
                    "message": "Finance branch decision inputs",
                    "data": {
                        "financier_present": bool(_fin_name),
                        "financier_len": len(_fin_name),
                        "financier_token": _fin_token if _fin_token in ("", "na", "n/a", "null", "none", "-") else "other",
                        "finance_required_target": _fin_val,
                    },
                    "timestamp": _ts_ist_iso(),
                }) + "\n")
        except Exception:
            pass
        # #endregion

        _fin_ok = False
        for root in _roots():
            try:
                for _lbl in ("Finance Required", "FinanceRequired"):
                    if _fill_by_label_on_frame(root, _lbl, _fin_val, action_timeout_ms=action_timeout_ms):
                        _fin_ok = True
                        _locked_root = root
                        break
                    if _select_dropdown_by_label_on_frame(
                        root,
                        label=_lbl,
                        value=_fin_val,
                        action_timeout_ms=min(action_timeout_ms, 8000),
                    ):
                        _fin_ok = True
                        _locked_root = root
                        break
                if _fin_ok:
                    break
            except Exception:
                continue
        if not _fin_ok:
            return False, f"Could not set Finance Required = {_fin_val}.", scraped
        _safe_page_wait(page, 400, log_label="after_finance_required")
        note(f"Create Order: set Finance Required = {_fin_val}.")
        # #region agent log — finance required set outcome
        try:
            with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                _lf.write(_j_fin.dumps({
                    "sessionId": "08e634",
                    "runId": "pre-fix",
                    "hypothesisId": "H3",
                    "location": "hero_dms_playwright_invoice.py:_create_order_finance_required_outcome",
                    "message": "Finance Required set result",
                    "data": {
                        "finance_required_target": _fin_val,
                        "finance_required_set": bool(_fin_ok),
                    },
                    "timestamp": _ts_ist_iso(),
                }) + "\n")
        except Exception:
            pass
        # #endregion

        if _is_financed:
            _fin_caps = _fin_name.upper()
            _fin_name_ok = False
            _fin_hard_err: str | None = None
            for root in _roots():
                try:
                    ok_fin, fin_msg = _fill_create_order_financier_field_on_frame(
                        page,
                        root,
                        _fin_name,
                        action_timeout_ms=action_timeout_ms,
                        content_frame_selector=content_frame_selector,
                        note=note,
                    )
                    if ok_fin:
                        _fin_name_ok = True
                        _locked_root = root
                        break
                    if fin_msg:
                        _fin_hard_err = fin_msg
                        break
                    for _lbl in ("Financer", "Financier", "Financer Name", "Financier Name"):
                        if _select_dropdown_by_label_on_frame(
                            root,
                            label=_lbl,
                            value=_fin_caps,
                            action_timeout_ms=min(action_timeout_ms, 8000),
                        ):
                            _fin_name_ok = True
                            _locked_root = root
                            break
                    if _fin_name_ok:
                        break
                except Exception:
                    continue
            if _fin_hard_err:
                _fin_err = _detect_siebel_error_popup(page, content_frame_selector)
                if _fin_err:
                    return False, f"Siebel error while setting Financier/Financer: {_fin_err[:200]}", scraped
                if _fin_hard_err in (
                    "Financer name not matched",
                    "Financer name could not be matched",
                ):
                    return False, _fin_hard_err, scraped
                return False, _fin_hard_err, scraped
            if not _fin_name_ok:
                _fin_err = _detect_siebel_error_popup(page, content_frame_selector)
                if _fin_err:
                    return False, f"Siebel error while setting Financier/Financer: {_fin_err[:200]}", scraped
                return (
                    False,
                    f"Could not set Financier/Financer (ALL CAPS + Tab path) from {_fin_name!r} (typed {_fin_caps!r}).",
                    scraped,
                )
            _safe_page_wait(page, 500, log_label="after_financier_fill")
            note(
                "Create Order: Financier/Financer main field + tablet field2 (ALL CAPS + Enter; no pick icon / "
                f"no MVG). source={_fin_name!r} typed={_fin_caps!r}."
            )
            _fin_post_err = _detect_siebel_error_popup(page, content_frame_selector)
            if _fin_post_err:
                return False, f"Siebel error after Financier/Financer input: {_fin_post_err[:200]}", scraped
            # Applet closed — focus is often still on Financer; Tab out before Hypothecation / remaining fields.
            try:
                page.keyboard.press("Tab")
            except Exception:
                pass
            _safe_page_wait(page, 400, log_label="after_financier_tab_out_before_hypothecation")
            note("Create Order: Tab out from Financer field; proceeding to Hypothecation and next fields.")

        if _is_financed:
            _hyp_val = "Y"
            _hyp_ok = False
            for root in _roots():
                try:
                    for _lbl in ("Hypothecation", "Hpothecation"):
                        if _fill_by_label_on_frame(root, _lbl, _hyp_val, action_timeout_ms=action_timeout_ms):
                            _hyp_ok = True
                            _locked_root = root
                            break
                        if _select_dropdown_by_label_on_frame(
                            root,
                            label=_lbl,
                            value=_hyp_val,
                            action_timeout_ms=min(action_timeout_ms, 8000),
                        ):
                            _hyp_ok = True
                            _locked_root = root
                            break
                    if _hyp_ok:
                        break
                except Exception:
                    continue
            if not _hyp_ok:
                return False, f"Could not set Hypothecation = {_hyp_val}.", scraped
            _safe_page_wait(page, 400, log_label="after_hypothecation")
            note(f"Create Order: set Hypothecation = {_hyp_val}.")
            # #region agent log — hypothecation set outcome
            try:
                with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                    _lf.write(_j_fin.dumps({
                        "sessionId": "08e634",
                        "runId": "pre-fix",
                        "hypothesisId": "H3",
                        "location": "hero_dms_playwright_invoice.py:_create_order_hypothecation_outcome",
                        "message": "Hypothecation set result",
                        "data": {
                            "hypothecation_target": _hyp_val,
                            "hypothecation_set": bool(_hyp_ok),
                        },
                        "timestamp": _ts_ist_iso(),
                    }) + "\n")
            except Exception:
                pass
            # #endregion
        else:
            note("Create Order: financier empty — skipped Financier and Hypothecation fields.")
        if _locked_root is not None:
            try:
                note(f"Create Order: locked booking form context for Contact Last Name/F2 (url={(getattr(_locked_root, 'url', '') or '')[:120]!r}).")
            except Exception:
                note("Create Order: locked booking form context for Contact Last Name/F2.")

        # 4) Contact Last Name → F2 pick applet flow (operator-provided deterministic path)
        _mob_digits = re.sub(r"\D", "", (mobile or "").strip())
        _first_need = (first_name or "").strip().lower()
        _applet_done = False
        _applet_err = ""
        _contact_pin_rb = ""
        _contact_roots = [_locked_root] if _locked_root is not None else list(_roots())

        # #region agent log — F2 applet context
        try:
            _lr_url = getattr(_locked_root, 'url', None) if _locked_root else None
            _lr_type = type(_locked_root).__name__ if _locked_root else "None"
            _cr_count = len(_contact_roots)
            with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                import json as _j_f2, time as _t_f2
                _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H1_H4","location":"hero_dms_playwright_invoice.py:create_order_f2_start","message":"F2 applet context","data":{"locked_root_type":_lr_type,"locked_root_url":(_lr_url or "")[:150],"contact_roots_count":_cr_count},"timestamp":_ts_ist_iso()}) + "\n")
        except Exception:
            pass
        # #endregion

        for root in _contact_roots:
            try:
                # Use CSS selector with exact aria-label (not get_by_label which can match via label associations)
                fld = root.locator("input[aria-label*='Contact Last Name' i]").first
                if fld.count() <= 0 or not fld.is_visible(timeout=700):
                    fld = root.locator("input[aria-label='Contact Last Name']").first
                if fld.count() <= 0 or not fld.is_visible(timeout=700):
                    # #region agent log — CLS not found in root
                    try:
                        with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                            _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H10","location":"hero_dms_playwright_invoice.py:create_order_cls_miss","message":"CLS field not found via aria-label CSS selector","data":{"root_url":getattr(root,'url','?')[:120]},"timestamp":_ts_ist_iso()}) + "\n")
                    except Exception:
                        pass
                    # #endregion
                    continue

                # #region agent log — CLS found, about to click + F2
                try:
                    _cls_aria = fld.evaluate("el => el.getAttribute('aria-label') || ''")
                    _cls_name = fld.evaluate("el => el.getAttribute('name') || ''")
                    _cls_id = fld.evaluate("el => el.getAttribute('id') || ''")
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H10","location":"hero_dms_playwright_invoice.py:create_order_cls_found","message":"CLS field found via aria-label CSS","data":{"aria":_cls_aria[:80],"name":_cls_name,"id":_cls_id[:40],"root_url":getattr(root,'url','?')[:120]},"timestamp":_ts_ist_iso()}) + "\n")
                except Exception:
                    pass
                # #endregion

                try:
                    fld.click(timeout=min(action_timeout_ms, 2500))
                except Exception:
                    fld.click(timeout=min(action_timeout_ms, 2500), force=True)
                _safe_page_wait(page, 300, log_label="after_cls_click_before_f2")
                try:
                    fld.press("F2", timeout=1200)
                except Exception:
                    pass
                _safe_page_wait(page, 1200, log_label="after_contact_lastname_f2")

                _all_roots_for_applet = list(_ordered_frames(page)) + [page]

                # Check if F2 keyboard press opened the applet (field names are dynamic, use suffix pattern)
                _applet_opened_by_f2 = False
                for _chk in _all_roots_for_applet:
                    try:
                        if _chk.locator("input[name$='_312_0']").first.is_visible(timeout=400):
                            _applet_opened_by_f2 = True
                            break
                    except Exception:
                        continue

                if not _applet_opened_by_f2:
                    note("Create Order: F2 key did not open applet — trying icon click near CLS field.")
                    try:
                        fld.click(timeout=1500)
                    except Exception:
                        pass
                    _safe_page_wait(page, 300, log_label="refocus_cls_before_icon")
                    _icon_clicked = False
                    try:
                        _icon_handle = fld.evaluate_handle("""(el) => {
                            const sel = "[aria-label='Press F2 for Selection Field']";
                            let p = el.parentElement;
                            for (let depth = 0; p && depth < 8; depth++, p = p.parentElement) {
                                const icon = p.querySelector(sel);
                                if (icon) {
                                    const st = window.getComputedStyle(icon);
                                    if (st.display !== 'none' && st.visibility !== 'hidden') return icon;
                                }
                                if (['TABLE', 'FORM', 'BODY'].includes(p.tagName)) break;
                            }
                            let sib = el.nextElementSibling;
                            for (let i = 0; sib && i < 5; i++, sib = sib.nextElementSibling) {
                                if (sib.matches && sib.matches(sel)) return sib;
                                const inner = sib.querySelector && sib.querySelector(sel);
                                if (inner) return inner;
                            }
                            return null;
                        }""")
                        _icon_el = _icon_handle.as_element()
                        if _icon_el:
                            _icon_el.click(timeout=2000)
                            _icon_clicked = True
                    except Exception:
                        _icon_clicked = False

                    # #region agent log — icon click result
                    try:
                        with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                            _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H11","location":"hero_dms_playwright_invoice.py:create_order_icon_handle","message":"F2 icon click via evaluate_handle","data":{"icon_clicked": _icon_clicked},"timestamp":_ts_ist_iso()}) + "\n")
                    except Exception:
                        pass
                    # #endregion

                    if _icon_clicked:
                        _safe_page_wait(page, 1500, log_label="after_f2_icon_click")
                        note("Create Order: clicked F2 icon near Contact Last Name field.")
                    else:
                        note("Create Order: F2 icon not found near Contact Last Name field.")

                # Applet should now be open with focus on the first field.
                # Verify by checking the focused element reads "Contact Id" (or similar).
                _safe_page_wait(page, 500, log_label="applet_settle")
                _focused_val = ""
                try:
                    _focused_val = page.evaluate("""() => {
                        const el = document.activeElement;
                        if (!el) return '';
                        return (el.value || el.textContent || '').trim();
                    }""") or ""
                except Exception:
                    pass

                # #region agent log — focused element after applet open
                try:
                    _focus_info = page.evaluate("""() => {
                        const el = document.activeElement;
                        if (!el) return {tag: 'none'};
                        return {tag: el.tagName, name: el.getAttribute('name') || '', aria: (el.getAttribute('aria-label') || '').substring(0,60), val: (el.value || '').substring(0,60)};
                    }""") or {}
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H14","location":"hero_dms_playwright_invoice.py:create_order_applet_focus","message":"Focused element after applet open","data":_focus_info,"timestamp":_ts_ist_iso()}) + "\n")
                except Exception:
                    pass
                # #endregion

                # Determine search value: use Contact ID if available (keeps default "Contact Id" dropdown),
                # otherwise fall back to mobile with dropdown change attempt.
                _search_val = ""
                _search_type = ""
                if contact_id:
                    _search_val = contact_id
                    _search_type = "Contact Id"
                    note(f"Create Order: using scraped Contact ID={contact_id!r} for applet search.")
                else:
                    _search_val = _mob_digits or mobile
                    _search_type = "Mobile Phone"
                    note("Create Order: no Contact ID — will try Mobile Phone search.")

                if _search_type == "Contact Id" and "contact id" in _focused_val.lower():
                    # Dropdown already shows "Contact Id" — just Tab to the value field
                    page.keyboard.press("Tab")
                    _safe_page_wait(page, 400, log_label="cls_tab_to_value")
                else:
                    # Need to change the Find dropdown to "Mobile Phone"
                    _find_changed = False
                    try:
                        page.keyboard.press("Alt+ArrowDown")
                        _safe_page_wait(page, 400, log_label="find_dropdown_open")
                        for _nav in range(12):
                            _cur = page.evaluate("() => (document.activeElement || {}).value || ''") or ""
                            if "mobile" in _cur.lower():
                                _find_changed = True
                                break
                            page.keyboard.press("ArrowDown")
                            _safe_page_wait(page, 150, log_label="find_dropdown_nav")
                        if _find_changed:
                            page.keyboard.press("Enter")
                            _safe_page_wait(page, 200, log_label="find_dropdown_select")
                    except Exception:
                        pass
                    note(f"Create Order: Find dropdown change to 'Mobile Phone' = {_find_changed}.")
                    page.keyboard.press("Tab")
                    _safe_page_wait(page, 400, log_label="cls_tab_to_value")

                # Verify focus landed on the value field after Tab
                _val_focus = {}
                try:
                    _val_focus = page.evaluate("""() => {
                        const el = document.activeElement;
                        if (!el) return {tag: 'none'};
                        return {tag: el.tagName, name: el.getAttribute('name') || '', aria: (el.getAttribute('aria-label') || '').substring(0,60), val: (el.value || '').substring(0,60), type: el.getAttribute('type') || ''};
                    }""") or {}
                except Exception:
                    pass

                # #region agent log — focus after Tab to value field
                try:
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H18","location":"hero_dms_playwright_invoice.py:create_order_val_focus","message":"Focus after Tab to value field","data":{**_val_focus, "search_type": _search_type, "search_val": _search_val},"timestamp":_ts_ist_iso()}) + "\n")
                except Exception:
                    pass
                # #endregion

                # Value field: fill via Playwright locator (page.keyboard.type doesn't work on this Siebel field).
                _val_filled = False
                _val_readback = ""
                _fill_strategy = ""

                # Strategy A: fill the focused element directly (fastest — no frame scanning)
                try:
                    _active = page.evaluate_handle("() => document.activeElement")
                    _el = _active.as_element()
                    if _el:
                        _el.fill(_search_val)
                        _val_readback = (_el.input_value() or "").strip()
                        if _val_readback:
                            _val_filled = True
                            _fill_strategy = "focused_element"
                except Exception:
                    pass

                # Strategy B: find by aria-label — try page first (applet renders in main page), then frames
                if not _val_filled:
                    _all_fill_roots = [page] + list(_ordered_frames(page))
                    for _fr in _all_fill_roots:
                        try:
                            _vf = _fr.locator("input[aria-label='Starting with' i]").first
                            if _vf.count() > 0 and _vf.is_visible(timeout=800):
                                _vf.click(timeout=1500)
                                _vf.fill(_search_val, timeout=2000)
                                _val_readback = (_vf.input_value(timeout=1000) or "").strip()
                                if _val_readback:
                                    _val_filled = True
                                    _fill_strategy = "aria_label"
                                    break
                        except Exception:
                            continue

                # Strategy C: find by dynamic name suffix _313_0
                if not _val_filled:
                    for _fr in _all_fill_roots:
                        try:
                            _vf = _fr.locator("input[name$='_313_0']").first
                            if _vf.count() > 0 and _vf.is_visible(timeout=800):
                                _vf.click(timeout=1500)
                                _vf.fill(_search_val, timeout=2000)
                                _val_readback = (_vf.input_value(timeout=1000) or "").strip()
                                if _val_readback:
                                    _val_filled = True
                                    _fill_strategy = "name_suffix"
                                    break
                        except Exception:
                            continue

                # #region agent log — value field fill result
                try:
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H20","location":"hero_dms_playwright_invoice.py:create_order_val_fill","message":"Value field fill result","data":{"typed": _search_val, "readback": _val_readback, "filled": _val_filled, "strategy": _fill_strategy},"timestamp":_ts_ist_iso()}) + "\n")
                except Exception:
                    pass
                # #endregion

                note(f"Create Order: applet value field fill={_val_filled}, readback='{_val_readback}'.")

                # Trigger query — try Go/Query button across all fresh roots, fallback Enter
                _safe_page_wait(page, 300, log_label="before_query_btn")
                _fresh_roots = list(_ordered_frames(page)) + [page]
                _qry_clicked = False
                _qry_selectors = [
                    "button[aria-label='Pick Contact List:Go']",
                    "button[name$='_314_0'], a[name$='_314_0'], input[name$='_314_0']",
                    "button[aria-label*='Go' i]",
                    "button[aria-label*='Query' i]",
                    "a[aria-label*='Go' i]",
                ]
                for _qsel in _qry_selectors:
                    if _qry_clicked:
                        break
                    for r2 in _fresh_roots:
                        try:
                            t = r2.locator(_qsel).first
                            if t.count() > 0 and t.is_visible(timeout=400):
                                t.click(timeout=1500)
                                _qry_clicked = True
                                break
                        except Exception:
                            continue
                if not _qry_clicked:
                    page.keyboard.press("Enter")
                _safe_page_wait(page, 1200, log_label="after_contact_pick_query")
                note(f"Create Order: applet query triggered (button={_qry_clicked}).")

                # #region agent log — post-query applet state
                try:
                    _pq_data = {}
                    for _sr in _fresh_roots:
                        try:
                            _pq_data = _sr.evaluate("""() => {
                                const rows = Array.from(document.querySelectorAll("tr")).slice(0, 20);
                                const cells = [];
                                for (const tr of rows) {
                                    for (const c of tr.querySelectorAll("td, th, a, span")) {
                                        const t = (c.textContent || '').trim();
                                        if (t && t.length <= 50 && t.length > 1) cells.push(t);
                                        if (cells.length >= 20) break;
                                    }
                                    if (cells.length >= 20) break;
                                }
                                const ok = document.querySelector("button[name$='_315_0'], a[name$='_315_0']");
                                return {row_sample: cells.join(' | ').substring(0, 300), ok_visible: ok ? window.getComputedStyle(ok).display !== 'none' : null};
                            }""")
                            if _pq_data.get("row_sample"):
                                _pq_data["frame_url"] = getattr(_sr, 'url', '?')[:120]
                                break
                        except Exception:
                            continue
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H13","location":"hero_dms_playwright_invoice.py:create_order_post_query","message":"Post-query applet state","data":_pq_data,"timestamp":_ts_ist_iso()}) + "\n")
                except Exception:
                    pass
                # #endregion

                # Match row by first name: find "Contact First Name" column index from header, then match data rows.
                _row_ok = False
                _row_diag = ""
                _fresh_roots2 = list(_ordered_frames(page)) + [page]
                for _rr in _fresh_roots2:
                    try:
                        _result = _rr.evaluate(
                            """(firstNeed) => {
                                const norm = (s) => String(s || '').trim().toLowerCase();
                                const fn = norm(firstNeed);
                                if (!fn) return {clicked: false, err: 'empty first name'};
                                const tables = document.querySelectorAll("table");
                                for (const tbl of tables) {
                                    const allRows = Array.from(tbl.querySelectorAll("tr"));
                                    if (allRows.length < 2) continue;
                                    // Find header row and "Contact First Name" column index
                                    let fnColIdx = -1;
                                    let headerRowIdx = -1;
                                    for (let ri = 0; ri < Math.min(allRows.length, 5); ri++) {
                                        const hCells = allRows[ri].querySelectorAll("th, td");
                                        for (let ci = 0; ci < hCells.length; ci++) {
                                            const ht = norm(hCells[ci].textContent || '');
                                            if (ht === 'contact first name' || ht === 'first name') {
                                                fnColIdx = ci;
                                                headerRowIdx = ri;
                                                break;
                                            }
                                        }
                                        if (fnColIdx >= 0) break;
                                    }
                                    // Scan data rows
                                    const dataStart = headerRowIdx >= 0 ? headerRowIdx + 1 : 1;
                                    const seenNames = [];
                                    for (let ri = dataStart; ri < allRows.length; ri++) {
                                        const cells = allRows[ri].querySelectorAll("td");
                                        if (cells.length < 2) continue;
                                        const cellTexts = Array.from(cells).map(c => (c.textContent || '').trim());
                                        // Skip header-like rows
                                        const joined = cellTexts.join('|').toLowerCase();
                                        if (joined.includes('contact first name') || joined.includes('search results')) continue;
                                        // Check name match
                                        let nameInRow = '';
                                        if (fnColIdx >= 0 && fnColIdx < cells.length) {
                                            nameInRow = norm(cells[fnColIdx].textContent || '');
                                        }
                                        if (!nameInRow) {
                                            // Fallback: check all cells for the name
                                            nameInRow = cellTexts.find(t => norm(t) === fn) ? fn : '';
                                        }
                                        if (nameInRow) seenNames.push(nameInRow);
                                        if (nameInRow === fn) {
                                            const clickable = allRows[ri].querySelector("a, input[type='radio'], input[type='checkbox'], td");
                                            if (clickable) { clickable.click(); } else { allRows[ri].click(); }
                                            return {clicked: true, preview: cellTexts.slice(0, 8).join(' | ').substring(0, 200), fnColIdx: fnColIdx};
                                        }
                                    }
                                    if (seenNames.length > 0) {
                                        return {clicked: false, err: 'name not matched', seen: seenNames.slice(0, 10).join(', '), fnColIdx: fnColIdx};
                                    }
                                }
                                return {clicked: false, err: 'no data rows found'};
                            }""",
                            _first_need,
                        )

                        # #region agent log — row match result
                        try:
                            with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                                _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H15","location":"hero_dms_playwright_invoice.py:create_order_row_match","message":"Row match result","data":_result or {},"timestamp":_ts_ist_iso()}) + "\n")
                        except Exception:
                            pass
                        # #endregion

                        if _result and _result.get("clicked"):
                            _row_ok = True
                            note(f"Create Order: matched row by first name. Preview: {(_result.get('preview',''))[:80]}")
                            break
                        elif _result:
                            _row_diag = _result.get("err", "") + "; seen=" + _result.get("seen", "")
                    except Exception:
                        continue
                if not _row_ok:
                    _applet_err = f"no first-name match for {first_name!r} in applet. {_row_diag[:180]}"
                    if _search_type == "Contact Id" and _row_diag and "no data rows found" in _row_diag:
                        note(
                            f"Create Order: applet query by Contact ID={_search_val!r} returned zero rows — "
                            "no enquiry exists for this contact. Stopping applet flow."
                        )
                        _applet_err = (
                            f"Applet query by Contact ID ({_search_val}) returned no results. "
                            "No enquiry/contact record found in Siebel for this ID."
                        )
                        break
                    continue
                _safe_page_wait(page, 400, log_label="after_row_select")
                note("Create Order: matched contact row in applet.")

                # OK button — search fresh roots, fallback Enter
                _ok_clicked = False
                # #region agent log — applet ok precheck
                try:
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({
                            "sessionId": "08e634",
                            "runId": "pre-fix",
                            "hypothesisId": "H4_H5",
                            "location": "hero_dms_playwright_invoice.py:create_order_applet_ok_precheck",
                            "message": "Preparing to click applet OK",
                            "data": {
                                "fresh_roots_count": len(_fresh_roots2),
                                "row_matched": bool(_row_ok),
                            },
                            "timestamp": _ts_ist_iso(),
                        }) + "\n")
                except Exception:
                    pass
                # #endregion
                for r2 in _fresh_roots2:
                    try:
                        for _ok_sel in (
                            "button[aria-label='Pick Contact List:OK']",
                            "a[aria-label='Pick Contact List:OK']",
                            "input[aria-label='Pick Contact List:OK']",
                            "button[name$='_315_0'], a[name$='_315_0'], input[name$='_315_0']",
                            "button[aria-label*='OK' i]",
                            "a[aria-label*='OK' i]",
                            "input[aria-label*='OK' i]",
                            "button:has-text('OK')",
                            "a:has-text('OK')",
                        ):
                            t = r2.locator(_ok_sel).first
                            if t.count() > 0 and t.is_visible(timeout=500):
                                try:
                                    t.click(timeout=2000)
                                except Exception:
                                    t.click(timeout=2000, force=True)
                                _ok_clicked = True
                                break
                        if _ok_clicked:
                            break
                    except Exception:
                        continue
                if not _ok_clicked:
                    for r2 in _fresh_roots2:
                        try:
                            _js_ok = r2.evaluate(
                                """() => {
                                    const vis = (el) => {
                                      if (!el) return false;
                                      const st = window.getComputedStyle(el);
                                      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') === 0) return false;
                                      const rc = el.getBoundingClientRect();
                                      return rc.width > 0 && rc.height > 0;
                                    };
                                    const sels = [
                                      "button[aria-label='Pick Contact List:OK']",
                                      "a[aria-label='Pick Contact List:OK']",
                                      "input[aria-label='Pick Contact List:OK']",
                                      "button[name$='_315_0']",
                                      "a[name$='_315_0']",
                                      "input[name$='_315_0']",
                                    ];
                                    for (const s of sels) {
                                      const el = document.querySelector(s);
                                      if (vis(el)) {
                                        try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                        el.click();
                                        return s;
                                      }
                                    }
                                    return "";
                                }"""
                            )
                            if _js_ok:
                                _ok_clicked = True
                                break
                        except Exception:
                            continue
                if not _ok_clicked:
                    page.keyboard.press("Enter")
                # #region agent log — applet ok click outcome
                try:
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({
                            "sessionId": "08e634",
                            "runId": "pre-fix",
                            "hypothesisId": "H4",
                            "location": "hero_dms_playwright_invoice.py:create_order_applet_ok_outcome",
                            "message": "Applet OK click outcome",
                            "data": {
                                "ok_button_clicked": bool(_ok_clicked),
                                "enter_fallback_used": not bool(_ok_clicked),
                            },
                            "timestamp": _ts_ist_iso(),
                        }) + "\n")
                except Exception:
                    pass
                # #endregion

                # Poll for Siebel error popup after OK — applet closes, focus
                # shifts to main form, and error may render with a delay.
                _ok_had_error = False
                for _ok_poll in range(4):
                    _safe_page_wait(page, 800, log_label=f"after_contact_pick_ok_poll_{_ok_poll}")
                    _ok_poll_err = _detect_siebel_error_popup(page, content_frame_selector)
                    if _ok_poll_err:
                        _applet_err = f"contact pick applet OK Siebel error: {_ok_poll_err[:220]}"
                        _ok_had_error = True
                        break
                if _ok_had_error:
                    continue
                note(f"Create Order: confirmed OK on applet (button={_ok_clicked}).")

                # Best-effort readback of Pincode after contact selection
                _pin_rb = ""
                _fresh_roots3 = list(_ordered_frames(page)) + [page] + _contact_roots
                for r3 in _fresh_roots3:
                    try:
                        for _pin_sel in (
                            "input[aria-label*='Pin Code' i]",
                            "input[aria-label*='Pincode' i]",
                            "input[title*='Pin Code' i]",
                            "input[name*='Pin' i]",
                        ):
                            _pl = r3.locator(_pin_sel).first
                            if _pl.count() > 0 and _pl.is_visible(timeout=500):
                                _pin_rb = (_pl.input_value(timeout=700) or "").strip()
                                if _pin_rb:
                                    break
                        if _pin_rb:
                            break
                    except Exception:
                        continue
                note(f"Create Order: post-contact applet readback — Pincode={_pin_rb!r}.")
                _contact_pin_rb = (_pin_rb or "").strip()
                # #region agent log — pincode readback outcome
                try:
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({
                            "sessionId": "08e634",
                            "runId": "pre-fix",
                            "hypothesisId": "H6_H7",
                            "location": "hero_dms_playwright_invoice.py:create_order_pincode_readback",
                            "message": "Pincode readback after contact applet",
                            "data": {
                                "pincode_non_empty": bool((_pin_rb or "").strip()),
                                "pincode_len": len((_pin_rb or "").strip()),
                            },
                            "timestamp": _ts_ist_iso(),
                        }) + "\n")
                except Exception:
                    pass
                # #endregion
                if not _contact_pin_rb:
                    _applet_err = "Contact applet completed but Pincode stayed empty after selection."
                    continue
                _applet_done = True
                # #region agent log — applet completion flag
                try:
                    with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                        _lf.write(_j_f2.dumps({
                            "sessionId": "08e634",
                            "runId": "pre-fix",
                            "hypothesisId": "H7",
                            "location": "hero_dms_playwright_invoice.py:create_order_applet_done_flag",
                            "message": "Applet flow completion flag set",
                            "data": {
                                "applet_done": True,
                                "pincode_non_empty": bool((_pin_rb or "").strip()),
                            },
                            "timestamp": _ts_ist_iso(),
                        }) + "\n")
                except Exception:
                    pass
                # #endregion
                break
            except Exception:
                continue
        if not _applet_done:
            return False, f"Could not complete Contact Last Name F2 applet flow. {_applet_err}".strip(), scraped

        # #region agent log — pre-save pincode guard
        try:
            with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                _lf.write(_j_f2.dumps({
                    "sessionId": "08e634",
                    "runId": "pre-fix",
                    "hypothesisId": "H8",
                    "location": "hero_dms_playwright_invoice.py:create_order_pre_save_pin_guard",
                    "message": "Pre-save pincode guard check",
                    "data": {
                        "pincode_non_empty": bool(_contact_pin_rb),
                        "pincode_len": len(_contact_pin_rb),
                    },
                    "timestamp": _ts_ist_iso(),
                }) + "\n")
        except Exception:
            pass
        # #endregion
        if not _contact_pin_rb:
            return False, "Pincode is empty after contact selection; skipping save (Ctrl+S).", scraped

        # 8) Ctrl+S save
        try:
            page.keyboard.press("Control+s")
        except Exception:
            try:
                page.keyboard.press("Meta+s")
            except Exception:
                return False, "Could not press Ctrl+S on Sales Order form.", scraped
        _safe_page_wait(page, 1500, log_label="after_create_order_save")
        note("Create Order: pressed Ctrl+S on Sales Order form.")
        _err_co_save = _poll_and_handle_siebel_error_popup(
            page,
            content_frame_selector,
            note,
            context="Create Order after Ctrl+S save (Sales Order form)",
            total_ms=1600,
            step_ms=320,
        )
        if _err_co_save:
            return (
                False,
                f"Create Order: Siebel error after Ctrl+S save: {_err_co_save[:220]}",
                scraped,
            )
        order_no = _scrape_order_number_current()
        scraped["order_number"] = order_no
        if order_no:
            note(f"Create Order: scraped Order#={order_no!r} after save.")
        else:
            note("Create Order: Order# not readable after save (best-effort).")
        _on_norm = (order_no or "").strip()
        if _on_norm and _on_norm.upper().startswith("TXN"):
            note(
                "Create Order: Order# still looks like an unsaved transaction id (TXN…) after Ctrl+S — "
                f"treating save as failed ({_on_norm!r})."
            )
            return (
                False,
                "Create Order: Sales order did not persist after Ctrl+S — Order# is still a TXN transaction "
                f"placeholder ({_on_norm[:80]}). Fix validation errors (e.g. required fields) and retry.",
                scraped,
            )
        _att_ok, _att_err, _att_scraped = _attach_vehicle_to_bkg(
            page,
            full_chassis=full_chassis,
            order_number=order_no or "",
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
        )
        scraped["order_drilldown_opened"] = bool(_att_ok)
        if _att_scraped:
            scraped.update(_att_scraped)
        if not _att_ok:
            return False, (_att_err or "attach_vehicle_to_bkg failed.").strip(), scraped
        _safe_page_wait(page, 900, log_label="after_attach_order_invoice_scrape")
        order_ref = _scrape_order_number_current()
        if order_ref:
            scraped["order_number"] = order_ref
            if order_ref != order_no:
                note(f"Create Order: refreshed Order#={order_ref!r} after header drill-down.")
        inv_no = _scrape_invoice_number_current()
        scraped["invoice_number"] = inv_no or ""
        if inv_no:
            note(f"Create Order: scraped Invoice#={inv_no!r} after drill-down.")
        else:
            note("Create Order: Invoice# not on screen or not readable yet (best-effort).")
        if callable(form_trace):
            form_trace(
                "v4_create_order",
                "Vehicle Sales — order header",
                "attach_vehicle_to_bkg_click_order_number_header",
                order_number=str(scraped.get("order_number") or ""),
                invoice_number=str(scraped.get("invoice_number") or ""),
            )
        return True, None, scraped
