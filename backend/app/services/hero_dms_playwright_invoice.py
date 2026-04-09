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
from typing import Any
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
    get_uploaded_scans_sale_folder,
)
from app.services.hero_dms_shared_utilities import (
    SiebelDmsUrls,
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
_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE = False  # temporarily: skip auto-click (set True to re-enable)


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


_ATTACH_LINE_ITEMS_MAX = 100


def _normalize_attach_line_items(
    *,
    full_chassis: str,
    line_item_discount: str,
    attach_line_items: list[dict] | None,
) -> tuple[list[dict[str, str]], str | None]:
    """
    Build a list of ``{full_chassis, line_item_discount}`` for order line attach.

    If ``attach_line_items`` is non-empty, each dict may use ``full_chassis``, ``vin``, or ``frame_num``
    and optional ``line_item_discount`` / ``discount``. Otherwise a single line is built from
    ``full_chassis`` + ``line_item_discount``.
    """
    if attach_line_items is not None and len(attach_line_items) > 0:
        if len(attach_line_items) > _ATTACH_LINE_ITEMS_MAX:
            return [], f"attach_line_items exceeds maximum ({_ATTACH_LINE_ITEMS_MAX})."
        out: list[dict[str, str]] = []
        for raw in attach_line_items:
            if not isinstance(raw, dict):
                continue
            ch = str(
                raw.get("full_chassis")
                or raw.get("vin")
                or raw.get("frame_num")
                or ""
            ).strip()
            disc = str(raw.get("line_item_discount") or raw.get("discount") or "").strip()
            if ch:
                out.append({"full_chassis": ch, "line_item_discount": disc})
        if not out:
            return [], "attach_line_items was empty or had no chassis values."
        return out, None
    ch = (full_chassis or "").strip()
    if not ch:
        return [], "attach_vehicle_to_bkg: full_chassis is empty (line-item VIN)."
    return (
        [{"full_chassis": ch, "line_item_discount": (line_item_discount or "").strip()}],
        None,
    )


def _read_vin_from_order_line_row(
    page: Page,
    *,
    row_n: int,
    content_frame_selector: str | None,
) -> str:
    """Best-effort read of the VIN input for Siebel order line row ``row_n`` (1-based)."""
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
    _rid = f"{int(row_n)}_s_1_l_VIN"
    for root in roots:
        for css in (f"#{_rid}", f"[id='{_rid}']"):
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    v = (loc.input_value(timeout=900) or "").strip()
                    if v:
                        return v
            except Exception:
                continue
    return ""


def _scrape_ex_showroom_for_order_line_row(
    page: Page,
    *,
    row_n: int,
    content_frame_selector: str | None,
) -> str:
    """
    Best-effort **Total (Ex-showroom)** for a single order line row (1-based index) after **Price All** + **Allocate All**.
    """
    n = int(row_n)
    js = """(rowNum) => {
      const n = Number(rowNum) || 1;
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const tryId = (id) => {
        const el = document.getElementById(id);
        if (!el || !vis(el)) return '';
        return String(el.value || '').trim();
      };
      const suffixTry = ['Total_Ex_Showroom', 'Ex_Showroom', 'Total_Ex_Show_Room', 'ExShowroom'];
      for (const suf of suffixTry) {
        const v = tryId(`${n}_s_1_l_${suf}`);
        if (v) return v;
      }
      const inputs = Array.from(document.querySelectorAll('input'));
      const prefix = `${n}_s_1_l_`;
      for (const el of inputs) {
        const id = String(el.getAttribute('id') || '');
        if (!id.startsWith(prefix)) continue;
        const al = String(el.getAttribute('aria-label') || '').toLowerCase();
        const tt = String(el.getAttribute('title') || '').toLowerCase();
        const nm = String(el.getAttribute('name') || '').toLowerCase();
        const blob = (al + ' ' + tt + ' ' + nm);
        if (!blob.includes('ex-showroom') && !blob.includes('ex showroom') && !blob.includes('ex_showroom')) continue;
        const val = String(el.value || '').trim();
        if (val) return val;
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
            v = root.evaluate(js, n)
            if (v or "").strip():
                return str(v).strip()
        except Exception:
            continue
    return ""


def _scrape_create_order_financier_display_value(
    page: Page,
    *,
    content_frame_selector: str | None,
) -> str:
    """
    After Financer fill, read the main Financer/Financier text field — Siebel may replace typed text with
    the canonical account name. Used to overwrite staging-sourced ``financier_name`` for DB/staging merge.
    """

    def _try_read_from_frame(frame) -> str:
        for _lbl in ("Financer", "Financier", "Financer Name", "Financier Name"):
            esc = _lbl.replace("'", "\\'")
            for css in (
                f"input.siebui-ctrl-input[aria-label*='{esc}' i]",
                f"input[type='text'][aria-label*='{esc}' i]",
                f"input[role='combobox'][aria-label*='{esc}' i]",
                f"input[aria-label*='{esc}' i]",
            ):
                try:
                    loc = frame.locator(css).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=400):
                        continue
                    v = (loc.input_value(timeout=700) or "").strip()
                    if v:
                        return v
                except Exception:
                    continue
            pats = (
                re.compile(rf"^\s*{re.escape(_lbl)}\s*$", re.I),
                re.compile(re.escape(_lbl), re.I),
            )
            for pat in pats:
                try:
                    loc = frame.get_by_label(pat).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=400):
                        continue
                    tag = loc.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "input":
                        v = (loc.input_value(timeout=700) or "").strip()
                        if v:
                            return v
                    inner = loc.locator(
                        "input.siebui-ctrl-input, input[type='text'], "
                        "input[role='combobox'], input:not([type='hidden'])"
                    ).first
                    if inner.count() > 0 and inner.is_visible(timeout=400):
                        v = (inner.input_value(timeout=700) or "").strip()
                        if v:
                            return v
                except Exception:
                    continue
        return ""

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
            v = _try_read_from_frame(root)
            if v:
                return v
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
    /** Prefer Vehicle Sales list grid: ``#gview_s_1_l`` / ``#s_1_ld`` (``data-vis-mode="Grid"``). */
    const collectTables = () => {
        const list = [];
        const seen = new Set();
        const push = (t) => {
            if (!t || !vis(t) || seen.has(t)) return;
            seen.add(t);
            list.push(t);
        };
        const gview = document.querySelector('#gview_s_1_l');
        if (gview) {
            gview.querySelectorAll('table.ui-jqgrid-btable').forEach((t) => push(t));
        }
        const s1ld = document.querySelector('#s_1_ld[data-vis-mode="Grid"], #s_1_ld');
        if (s1ld) {
            s1ld.querySelectorAll('table.ui-jqgrid-btable').forEach((t) => push(t));
        }
        if (list.length === 0) {
            document.querySelectorAll('table.ui-jqgrid-btable').forEach((t) => push(t));
        }
        return list;
    };
    const tables = collectTables();
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
        const looksLikeDateTime = (s) => {
            const t = String(s || '').trim();
            if (!t) return false;
            return /\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(t) && /\\d{1,2}:\\d{2}/.test(t);
        };
        for (const tr of dataRows) {
            if (tr.classList.contains('jqgfirstrow')) continue;
            if (!vis(tr)) continue;
            const row = { status: '', invoice: '', order: '', raw: (tr.innerText || '').trim() };
            /** 1) Canonical Invoice# — id-based selectors; skip vis() because wide
             *  grids scroll columns offscreen giving zero-width bounding rects.
             *  Prefer ``_l_Invoice__`` (double-underscore, the # column) to avoid
             *  ``_l_Invoice_Date`` which shares the ``_l_Invoice`` substring. */
            const invTd = tr.querySelector('td[id$="_l_Invoice__"]')
                || tr.querySelector('td[id*="_l_Invoice__"]')
                || (() => {
                    const cands = tr.querySelectorAll('td[id*="_l_Invoice"]');
                    for (const c of cands) {
                        const cid = (c.getAttribute('id') || '');
                        if (cid.includes('_Date') || cid.includes('_Dt')) continue;
                        return c;
                    }
                    return null;
                })();
            if (invTd) {
                const tit = (invTd.getAttribute('title') || '').trim();
                const tx = (invTd.textContent || '').trim();
                if (tit && !looksLikeDateTime(tit)) {
                    row.invoice = tit;
                } else if (tx && !looksLikeDateTime(tx)) {
                    row.invoice = tx;
                }
            }
            /** 2) Order# — input first, then ``td[id*=_l_Order_Number]`` id fallback. */
            const ordInp = tr.querySelector(
                'input[name="Order_Number"], input[name="Order Number"], '
                + 'input[id*="Order_Number"], input[id$="_Order_Number"], '
                + 'input[aria-labelledby*="Order_Number"], input[aria-labelledby*="Order Number"], '
                + 'input.siebui-list-ctrl[id*="Order_Number"]'
            );
            if (ordInp) {
                const ov = String(ordInp.value || '').trim();
                if (ov) {
                    row.order = ov;
                }
            }
            if (!row.order) {
                const ordTd = tr.querySelector('td[id*="_l_Order_Number"]');
                if (ordTd) {
                    const ot = (ordTd.getAttribute('title') || '').trim() || (ordTd.textContent || '').trim();
                    if (ot) row.order = ot;
                }
            }
            /** 3) Status — ``td[id*=_l_Status]`` id-based pick. */
            if (!row.status) {
                const stTd = tr.querySelector('td[id*="_l_Status"]');
                if (stTd) {
                    const st = (stTd.getAttribute('title') || '').trim() || (stTd.textContent || '').trim();
                    if (st) row.status = st;
                }
            }
            const tds = tr.querySelectorAll('td[role="gridcell"], td');
            tds.forEach((td, i) => {
                const txt = (td.textContent || '').trim();
                const adb = (td.getAttribute('aria-describedby') || '').toLowerCase();
                const cn = (colNames[i] || '').toLowerCase();
                const key = (cn + ' ' + adb).toLowerCase();
                if (!row.status && (key.includes('status') || adb.includes('status'))) row.status = txt;
                else if (
                    !row.invoice
                    && (key.includes('invoice') || adb.includes('invoice'))
                    && !key.includes('invoice_date')
                    && !adb.includes('invoice_date')
                    && !key.includes('inv_dt')
                    && !adb.includes('inv_dt')
                ) {
                    if (!looksLikeDateTime(txt)) row.invoice = txt;
                } else if (
                    !row.order
                    && key.includes('order')
                    && (key.includes('order_number') || key.includes('order#') || key.includes('order_no') || adb.includes('order_number') || adb.includes('order#'))
                ) {
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
    """Whether jqGrid **invoice** cell text looks like a real Invoice# (not dash/placeholder / date-time)."""
    t = (s or "").strip()
    if len(t) < 2:
        return False
    if re.match(r"^(—|-+|–|pending|n/?a)$", t, re.I):
        return False
    # Siebel sometimes puts "Invoice Date" in a column whose id contains ``invoice`` — reject bare datetimes.
    if re.search(r"\d{1,2}/\d{1,2}/\d{4}", t) and re.search(r"\d{1,2}:\d{2}", t):
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
    const normKey = (s) => String(s || '').replace(/\\s+/g, '').toUpperCase();
    const od = String(orderNeedle || '').replace(/\\D/g, '');
    const needleNorm = normKey(orderNeedle);
    const md = String(mobileDigits || '').replace(/\\D/g, '');
    const rowSel =
        '#gview_s_1_l table.ui-jqgrid-btable tbody tr.jqgrow, #gview_s_1_l table.ui-jqgrid-btable tbody tr[role="row"], '
        + '#s_1_ld table.ui-jqgrid-btable tbody tr.jqgrow, #s_1_ld table.ui-jqgrid-btable tbody tr[role="row"], '
        + 'table.ui-jqgrid-btable tbody tr.jqgrow, table.ui-jqgrid-btable tbody tr[role="row"]';
    const rows = document.querySelectorAll(rowSel);
    for (const tr of rows) {
        if (tr.classList.contains('jqgfirstrow')) continue;
        if (!vis(tr)) continue;
        const rowText = tr.innerText || '';
        if (md && !rowText.replace(/\\D/g, '').includes(md)) continue;
        const inp = tr.querySelector(
            'input[name="Order_Number"], input[name="Order Number"], '
            + 'input[id*="Order_Number"], input[id$="_Order_Number"], '
            + 'input[aria-labelledby*="Order_Number"], input[aria-labelledby*="Order Number"]'
        );
        if (inp && vis(inp)) {
            const ov = String(inp.value || '').trim();
            if (!ov) continue;
            const otd = ov.replace(/\\D/g, '');
            let match = true;
            if (od && otd) {
                match = otd === od || otd.includes(od) || od.includes(otd);
            }
            if (match && needleNorm && normKey(ov) !== needleNorm) {
                const vn = normKey(ov);
                if (!vn.includes(needleNorm) && !needleNorm.includes(vn)) match = false;
            }
            if (!match) continue;
            try { inp.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
            try { inp.click(); } catch (e) {}
            return 'ok:' + ov;
        }
        const a = tr.querySelector("a[name='Order Number'], a[name='Order #']") || tr.querySelector('td a');
        if (!a || !vis(a)) continue;
        const ot = (a.textContent || '').trim();
        const otd = ot.replace(/\\D/g, '');
        if (od && otd && otd !== od && !otd.includes(od) && !od.includes(otd)) continue;
        if (needleNorm && normKey(ot) !== needleNorm) {
            const tn = normKey(ot);
            if (!tn.includes(needleNorm) && !needleNorm.includes(tn)) continue;
        }
        try { a.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
        try { a.click(); } catch (e) {}
        return 'ok:' + ot;
    }
    return '';
}"""


_JS_CLICK_MY_ORDERS_INVOICE_LINK = """({ invoiceNeedle, mobileDigits }) => {
    const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    /** Match Siebel My Orders invoice cell: ``td#N_s_1_l_Invoice__`` with ``title`` = Invoice# (alphanumeric). */
    const normInv = (s) => String(s || '').replace(/\\s+/g, '').toUpperCase();
    const needle = normInv(invoiceNeedle);
    const md = String(mobileDigits || '').replace(/\\D/g, '');
    if (!needle) return '';
    const rowSel =
        '#gview_s_1_l table.ui-jqgrid-btable tbody tr.jqgrow, #gview_s_1_l table.ui-jqgrid-btable tbody tr[role="row"], '
        + '#s_1_ld table.ui-jqgrid-btable tbody tr.jqgrow, #s_1_ld table.ui-jqgrid-btable tbody tr[role="row"], '
        + 'table.ui-jqgrid-btable tbody tr.jqgrow, table.ui-jqgrid-btable tbody tr[role="row"]';
    const rows = document.querySelectorAll(rowSel);
    for (const tr of rows) {
        if (tr.classList.contains('jqgfirstrow')) continue;
        if (!vis(tr)) continue;
        const rowText = tr.innerText || '';
        if (md && !rowText.replace(/\\D/g, '').includes(md)) continue;
        let td = tr.querySelector('td[role="gridcell"][id*="_l_Invoice"]');
        if (!td || !vis(td)) {
            td = tr.querySelector('td[role="gridcell"][aria-labelledby="s_1_l_altLink"][id*="Invoice"]');
        }
        if (!td || !vis(td)) {
            td = tr.querySelector('td[role="gridcell"][aria-labelledby="s_1_l_altLink"]');
        }
        let target = null;
        if (td && vis(td)) {
            target = td;
        } else {
            const a = tr.querySelector("a[name='Invoice Number'], a[name='Invoice #']");
            if (a && vis(a)) target = a;
        }
        if (!target || !vis(target)) {
            const tds = tr.querySelectorAll('td[role="gridcell"], td');
            for (const c of tds) {
                const adb = (c.getAttribute('aria-describedby') || '').toLowerCase();
                if (!adb.includes('invoice')) continue;
                const inner = c.querySelector('a');
                target = (inner && vis(inner)) ? inner : c;
                break;
            }
        }
        if (!target || !vis(target)) continue;
        const fromTitle = (target.getAttribute && target.getAttribute('title')) ? target.getAttribute('title').trim() : '';
        const txt = fromTitle || (target.textContent || '').trim();
        const tnorm = normInv(txt);
        if (!tnorm) continue;
        if (tnorm !== needle && !tnorm.includes(needle) && !needle.includes(tnorm)) continue;
        try { target.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
        try { target.click(); } catch (e) {}
        return 'ok:' + txt;
    }
    return '';
}"""


def _click_my_orders_jqgrid_invoice_for_mobile_or_invoice(
    page: Page,
    *,
    mobile: str,
    invoice_number: str,
    content_frame_selector: str | None,
    note,
    action_timeout_ms: int,
) -> bool:
    """Open the sales order from My Orders jqGrid by clicking the **Invoice#** cell/link (Run Report prep)."""
    nd = re.sub(r"\D", "", (mobile or "").strip())
    inv = (invoice_number or "").strip()
    if not inv or not _my_orders_invoice_meaningful(inv):
        return False
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
            hit = root.evaluate(
                _JS_CLICK_MY_ORDERS_INVOICE_LINK,
                {"invoiceNeedle": inv, "mobileDigits": nd},
            )
            if hit:
                note(f"Create Order: opened order from My Orders grid via Invoice# ({hit!r}).")
                _safe_page_wait(page, 1500, log_label="after_my_orders_jqgrid_invoice_click")
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


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


def _fill_order_finance_after_vin_attach(
    page: Page,
    *,
    financier_name: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note: Callable[..., None],
) -> tuple[bool, str | None]:
    """
    On the **Sales Order** form during attach-VIN flow: when ``financier_name`` is non-empty, set
    **Finance Required** = Y, fill **Financer** (same path as create booking), **Hypothecation** = Y.

    Call after **Price All**, **Allocate All**, and ex-showroom / order-line scraping (attach path), or before
    the **Order:** link on the ``start_at_order_link_before_apply`` shortcut.

    Tries explicit Siebel controls (``s_2_1_118_0``, ``s_2_1_117_0``, ``s_2_1_119_0``) then label-based fallbacks.
    """
    _fn = (financier_name or "").strip()
    if not _fn:
        return True, None
    _tmo = min(int(action_timeout_ms or 3000), 4000)
    _fin_req = "Y"
    _hyp_val = "Y"

    def _roots() -> list:
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

    # 1) Finance Required = Y
    _fr_ok = False
    for root in _roots():
        for css in (
            'input[name="s_2_1_118_0"]',
            'input[aria-labelledby="HHML_Finance_Flag_Label_2"]',
            'input[aria-label="Finance Required" i]',
            'input[aria-label*="Finance Required" i]',
        ):
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=650):
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    try:
                        loc.fill("", timeout=_tmo)
                        loc.fill(_fin_req, timeout=_tmo)
                    except Exception:
                        pass
                    try:
                        loc.press("Tab", timeout=1200)
                    except Exception:
                        try:
                            page.keyboard.press("Tab")
                        except Exception:
                            pass
                    _fr_ok = True
                    note(f"attach_vehicle_to_bkg: Finance Required={_fin_req!r} (selector {css!r}).")
                    break
            except Exception:
                continue
        if _fr_ok:
            break
    if not _fr_ok:
        for root in _roots():
            for _lbl in ("Finance Required", "FinanceRequired"):
                if _fill_by_label_on_frame(root, _lbl, _fin_req, action_timeout_ms=action_timeout_ms):
                    _fr_ok = True
                    note(f"attach_vehicle_to_bkg: Finance Required={_fin_req!r} (label {_lbl!r}).")
                    break
                if _select_dropdown_by_label_on_frame(
                    root,
                    label=_lbl,
                    value=_fin_req,
                    action_timeout_ms=min(action_timeout_ms, 8000),
                ):
                    _fr_ok = True
                    note(f"attach_vehicle_to_bkg: Finance Required={_fin_req!r} (dropdown {_lbl!r}).")
                    break
            if _fr_ok:
                break
    if not _fr_ok:
        return False, "Could not set Finance Required = Y (attach order finance)."

    _safe_page_wait(page, 500, log_label="after_attach_finance_required")

    # 2) Financer — same methodology as create booking
    _fin_name_ok = False
    _fin_hard_err: str | None = None
    for root in _roots():
        try:
            ok_fin, fin_msg = _fill_create_order_financier_field_on_frame(
                page,
                root,
                _fn,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
            )
            if ok_fin:
                _fin_name_ok = True
                break
            if fin_msg:
                _fin_hard_err = fin_msg
                break
            for _lbl in ("Financer", "Financier", "Financer Name", "Financier Name"):
                if _select_dropdown_by_label_on_frame(
                    root,
                    label=_lbl,
                    value=_fn.upper(),
                    action_timeout_ms=min(action_timeout_ms, 8000),
                ):
                    _fin_name_ok = True
                    break
            if _fin_name_ok:
                break
        except Exception:
            continue
    if _fin_hard_err:
        _pop_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _pop_err:
            return False, f"Siebel error while setting Financer (attach): {_pop_err[:200]}"
        if _fin_hard_err in (
            "Financer name not matched",
            "Financer name could not be matched",
        ):
            return False, _fin_hard_err
        return False, _fin_hard_err
    if not _fin_name_ok:
        for root in _roots():
            for css in (
                'input[name="s_2_1_117_0"]',
                'input[aria-labelledby="HHML_Finance_Consultant_Name_Label_2"]',
                'input[aria-label="Financer" i]',
                'input[aria-label*="Financer" i]',
            ):
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=650):
                        try:
                            loc.click(timeout=_tmo)
                        except Exception:
                            loc.click(timeout=_tmo, force=True)
                        try:
                            loc.fill("", timeout=_tmo)
                        except Exception:
                            pass
                        try:
                            page.keyboard.type(_fn.upper(), delay=35)
                        except Exception:
                            loc.fill(_fn.upper(), timeout=_tmo)
                        try:
                            loc.press("Tab", timeout=1200)
                        except Exception:
                            pass
                        _fin_name_ok = True
                        note(f"attach_vehicle_to_bkg: Financer filled via {css!r} (fallback).")
                        break
                except Exception:
                    continue
            if _fin_name_ok:
                break
    if not _fin_name_ok:
        _pop_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _pop_err:
            return False, f"Siebel error while setting Financer (attach): {_pop_err[:200]}"
        return False, f"Could not set Financer from {_fn!r} (attach path)."

    _fin_post = _detect_siebel_error_popup(page, content_frame_selector)
    if _fin_post:
        return False, f"Siebel error after Financer (attach): {_fin_post[:200]}"
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _safe_page_wait(page, 400, log_label="after_attach_financer_tab_out")

    # 3) Hypothecation = Y
    _hyp_ok = False
    for root in _roots():
        for css in (
            'input[name="s_2_1_119_0"]',
            'input[aria-labelledby="HHML_Hypothecation_Flag_Label_2"]',
            'input[aria-label="Hypothecation" i]',
            'input[aria-label*="Hypothecation" i]',
        ):
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=650):
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    try:
                        loc.fill("", timeout=_tmo)
                        loc.fill(_hyp_val, timeout=_tmo)
                    except Exception:
                        pass
                    try:
                        loc.press("Tab", timeout=1200)
                    except Exception:
                        try:
                            page.keyboard.press("Tab")
                        except Exception:
                            pass
                    _hyp_ok = True
                    note(f"attach_vehicle_to_bkg: Hypothecation={_hyp_val!r} (selector {css!r}).")
                    break
            except Exception:
                continue
        if _hyp_ok:
            break
    if not _hyp_ok:
        for root in _roots():
            for _lbl in ("Hypothecation", "Hpothecation"):
                if _fill_by_label_on_frame(root, _lbl, _hyp_val, action_timeout_ms=action_timeout_ms):
                    _hyp_ok = True
                    note(f"attach_vehicle_to_bkg: Hypothecation={_hyp_val!r} (label {_lbl!r}).")
                    break
                if _select_dropdown_by_label_on_frame(
                    root,
                    label=_lbl,
                    value=_hyp_val,
                    action_timeout_ms=min(action_timeout_ms, 8000),
                ):
                    _hyp_ok = True
                    note(f"attach_vehicle_to_bkg: Hypothecation={_hyp_val!r} (dropdown {_lbl!r}).")
                    break
            if _hyp_ok:
                break
    if not _hyp_ok:
        return False, "Could not set Hypothecation = Y (attach order finance)."

    _safe_page_wait(page, 350, log_label="after_attach_hypothecation")
    return True, None


def _attach_vehicle_to_bkg(
    page: Page,
    *,
    full_chassis: str,
    order_number: str = "",
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    start_at_order_link_before_apply: bool = False,
    line_item_discount: str = "",
    attach_line_items: list[dict] | None = None,
    financier_name: str = "",
) -> tuple[bool, str | None, dict]:
    """
    After a new sales order is saved:
    1. Click Order Number / Order # header link to open order detail — **skipped** when line-item **New** or **VIN**
       is already visible (e.g. My Orders Order# drill-down).
    2. For each line: **New** → fill VIN (row ``n``) → optional **Discount** → repeat; then **Price All** → **Allocate All** once.
    3. *(Currently disabled via `if False`.)* Single-click **VIN** drilldown → **Serial Number** →
       ``_siebel_run_vehicle_serial_detail_precheck_pdi`` (Pre-check + PDI through submit).
    4. Click ``Order:<order#>`` link → **Apply Campaign** → **Create Invoice** when
       ``_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE`` is True (default: enabled).

    After **Allocate All**, best-effort scrape of **Total (Ex-showroom)** into ``extra_dict`` as
    ``vehicle_price`` / ``vehicle_ex_showroom_cost`` (for ``vehicle_master.vehicle_ex_showroom_price``).
    Multiple lines: also ``order_line_ex_showroom`` (list of per-row chassis + price). Ex-showroom is read
    only after **Price All** + **Allocate All** (not during the add-line loop).

    ``attach_line_items`` (optional): list of dicts with ``full_chassis`` / ``vin`` / ``frame_num`` and optional
    ``line_item_discount`` / ``discount``. If omitted, a single line is built from ``full_chassis`` +
    ``line_item_discount``.

    When ``start_at_order_link_before_apply`` is True, skips the Order header through **Allocate All**
    and starts at the top **Order:<order#>** link (Step 10) — used when My Orders already shows
    **Allocated** stock.
    Returns ``(success, error_detail, extra_dict)``; ``extra_dict`` may be empty when scrape fails.

    When ``financier_name`` is non-empty (from client / ``dms_values``), fills **Finance Required** = Y,
    **Financer**, and **Hypothecation** = Y on the order form (same methodology as create booking), after
    **Price All** + **Allocate All** and after ex-showroom / line-item scraping in that section; on the
    ``start_at_order_link_before_apply`` shortcut (no Price/Allocate), fills those fields before the **Order:** link.
    """
    _tmo = min(int(action_timeout_ms or 3000), 4000)
    _fin = (financier_name or "").strip()
    _finance_attach_ok = False

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

        _line_items, _li_err = _normalize_attach_line_items(
            full_chassis=full_chassis,
            line_item_discount=line_item_discount,
            attach_line_items=attach_line_items,
        )
        if _li_err:
            return False, _li_err, {}

        def _click_new_line_item() -> bool:
            _nw = _click_by_id("s_1_1_35_0_Ctrl", "New button", wait_ms=1200)
            if not _nw:
                _nw = _click_by_id("s_1_1_35_0", "New button (legacy id)", wait_ms=1200)
            if not _nw:
                for root in _all_roots():
                    if _click_first_visible(root, "[id$='_35_0_Ctrl']", "New button (id suffix _35_0_Ctrl)", wait_ms=1200):
                        _nw = True
                        break
            return bool(_nw)

        def _fill_vin_for_row(row_n: int, ch: str) -> bool:
            _ch = (ch or "").strip()
            if not _ch:
                return False
            _ns = str(int(row_n))
            _vin_locator_css: tuple[str, ...] = (
                f"#{_ns}_s_1_l_VIN",
                f"[id='{_ns}_s_1_l_VIN']",
            )
            if row_n == 1:
                _vin_locator_css = _vin_locator_css + (
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

            def _tab_out_vin_like_institution(vin_loc) -> None:
                """Same as Account/Institution Name after typing (`_fill_challan_account_institution_name_verify_pin`): Tab out."""
                try:
                    vin_loc.press("Tab", timeout=1200)
                except Exception:
                    try:
                        page.keyboard.press("Tab")
                    except Exception:
                        pass

            def _try_fill_vin_locator(vin_loc) -> bool:
                # Restored flow: full click enters jqGrid edit mode; then page.keyboard.type (same as before applet issues).
                try:
                    vin_loc.scroll_into_view_if_needed(timeout=_tmo)
                except Exception:
                    pass
                try:
                    vin_loc.click(timeout=_tmo)
                except Exception:
                    try:
                        vin_loc.click(timeout=_tmo, force=True)
                    except Exception:
                        return False
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
                try:
                    page.keyboard.type(_ch, delay=28)
                except Exception:
                    pass
                if not _vin_readback_ok(vin_loc):
                    try:
                        vin_loc.type(_ch, delay=28, timeout=min(8000, int(action_timeout_ms or 3000)))
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
                _tab_out_vin_like_institution(vin_loc)
                return True

            # After **New**, the jqGrid row / VIN input can appear slightly later than the New click wait.
            for _wait_i in range(24):
                _row_ready = False
                for root in _all_roots():
                    for css in _vin_locator_css:
                        try:
                            _wl = root.locator(css).first
                            if _wl.count() > 0 and _wl.is_visible(timeout=450):
                                _row_ready = True
                                break
                        except Exception:
                            continue
                    if _row_ready:
                        break
                if _row_ready:
                    break
                _safe_page_wait(page, 200, log_label=f"attach_vin_row_ready_poll_{row_n}_{_wait_i}")

            _vin_filled = False
            for root in _all_roots():
                for css in _vin_locator_css:
                    try:
                        vin_loc = root.locator(css).first
                        if vin_loc.count() <= 0 or not vin_loc.is_visible(timeout=700):
                            continue
                        if _try_fill_vin_locator(vin_loc):
                            _vin_filled = True
                            note(f"attach_vehicle_to_bkg: VIN filled via {css!r}, row={row_n}, chassis={_ch!r}.")
                            break
                    except Exception:
                        continue
                if _vin_filled:
                    break

            _js_vin_pick = """(payload) => {
          const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width >= 2 && r.height >= 2;
          };
          const c = String((payload && payload.chassis) || '');
          const n = Number(payload && payload.n) || 1;
          let el = document.getElementById(n + '_s_1_l_VIN');
          if (!el || !vis(el)) {
            if (n !== 1) return false;
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
                _pay = {"chassis": _ch, "n": int(row_n)}
                for root in _all_roots():
                    try:
                        if bool(root.evaluate(_js_vin_pick, _pay)):
                            _vin_filled = True
                            note(f"attach_vehicle_to_bkg: JS set VIN field (broad query), row={row_n}, chassis={_ch!r}.")
                            _safe_page_wait(page, 200, log_label="after_vin_js_fill")
                            _id = f"#{int(row_n)}_s_1_l_VIN"
                            try:
                                _vl = root.locator(_id).first
                                if _vl.count() > 0 and _vl.is_visible(timeout=600):
                                    _tab_out_vin_like_institution(_vl)
                                else:
                                    try:
                                        page.keyboard.press("Tab")
                                    except Exception:
                                        pass
                            except Exception:
                                try:
                                    page.keyboard.press("Tab")
                                except Exception:
                                    pass
                            break
                    except Exception:
                        continue

            return bool(_vin_filled)

        def _fill_discount_for_row(row_n: int, disc_raw: str) -> bool:
            _disc_raw = (disc_raw or "").strip()
            if not _disc_raw:
                return True
            _ns = str(int(row_n))
            _disc_locator_css: tuple[str, ...] = (
                f"#{_ns}_s_1_l_Discount",
                f"[id='{_ns}_s_1_l_Discount']",
            )
            if row_n == 1:
                _disc_locator_css = _disc_locator_css + (
                    "input[id$='_l_Discount']",
                    "input[id*='_l_Discount' i]",
                    "input[name='Discount']",
                    "input[aria-label='Discount']",
                    "input[title='Discount']",
                    "input[title*='Discount' i]",
                )

            def _norm_disc_txt(s: str) -> str:
                return re.sub(r"[\s,]", "", (s or "").strip())

            def _disc_readback_ok(dloc) -> bool:
                try:
                    got = (dloc.input_value(timeout=900) or "").strip()
                except Exception:
                    got = ""
                if not got:
                    return False
                return _norm_disc_txt(_disc_raw) == _norm_disc_txt(got) or _disc_raw in got or got in _disc_raw

            def _js_set_discount_on_element(dloc) -> None:
                try:
                    _v = json.dumps(_disc_raw)
                    dloc.evaluate(
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

            def _tab_out_discount(dloc) -> None:
                try:
                    dloc.press("Tab", timeout=1500)
                except Exception:
                    pass
                try:
                    page.keyboard.press("Tab")
                except Exception:
                    pass

            def _try_fill_discount_locator(dloc) -> bool:
                try:
                    dloc.scroll_into_view_if_needed(timeout=_tmo)
                except Exception:
                    pass
                dloc.click(timeout=_tmo)
                _safe_page_wait(page, 220, log_label="after_discount_click")
                try:
                    dloc.focus(timeout=1200)
                except Exception:
                    pass
                try:
                    dloc.press("Control+a", timeout=800)
                except Exception:
                    pass
                try:
                    dloc.fill("", timeout=1000)
                except Exception:
                    pass
                try:
                    page.keyboard.type(_disc_raw, delay=22)
                except Exception:
                    pass
                if not _disc_readback_ok(dloc):
                    try:
                        dloc.type(_disc_raw, delay=22, timeout=min(8000, int(action_timeout_ms or 3000)))
                    except Exception:
                        pass
                if not _disc_readback_ok(dloc):
                    try:
                        dloc.fill(_disc_raw, timeout=2000)
                    except Exception:
                        pass
                if not _disc_readback_ok(dloc):
                    _js_set_discount_on_element(dloc)
                if not _disc_readback_ok(dloc):
                    return False
                _tab_out_discount(dloc)
                return True

            _disc_filled = False
            for root in _all_roots():
                for css in _disc_locator_css:
                    try:
                        dloc = root.locator(css).first
                        if dloc.count() <= 0 or not dloc.is_visible(timeout=700):
                            continue
                        if _try_fill_discount_locator(dloc):
                            _disc_filled = True
                            note(f"attach_vehicle_to_bkg: Discount filled via {css!r}, row={row_n}, value={_disc_raw!r}.")
                            break
                    except Exception:
                        continue
                if _disc_filled:
                    break

            _js_disc_pick = """(payload) => {
              const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width >= 2 && r.height >= 2;
              };
              const c = String((payload && payload.val) || '');
              const n = Number(payload && payload.n) || 1;
              let el = document.getElementById(n + '_s_1_l_Discount');
              if (!el || !vis(el)) {
                if (n !== 1) return false;
                const cands = Array.from(document.querySelectorAll(
                  "input[id$='_l_Discount'], input[name='Discount'], input[aria-label='Discount'], input[title='Discount']"
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

            if not _disc_filled:
                _dp = {"val": _disc_raw, "n": int(row_n)}
                for root in _all_roots():
                    try:
                        if bool(root.evaluate(_js_disc_pick, _dp)):
                            _disc_filled = True
                            note(f"attach_vehicle_to_bkg: JS set Discount field (broad query), row={row_n}, value={_disc_raw!r}.")
                            _safe_page_wait(page, 200, log_label="after_discount_js_fill")
                            try:
                                page.keyboard.press("Tab")
                            except Exception:
                                pass
                            break
                    except Exception:
                        continue

            return bool(_disc_filled)

        # ── Step 2–3: For each line — New → VIN → optional Discount (row ``n`` = 1..N)
        _n_tot = len(_line_items)
        for _ix, _it in enumerate(_line_items):
            n = _ix + 1
            _ch = (_it.get("full_chassis") or "").strip()
            _disc_raw = (_it.get("line_item_discount") or "").strip()
            if not _click_new_line_item():
                return False, "Could not click New button (id=s_1_1_35_0_Ctrl or id suffix _35_0_Ctrl) on order line items.", {}
            _safe_page_wait(page, 900, log_label="after_new_before_vin_field")
            if not _fill_vin_for_row(n, _ch):
                return False, f"Could not fill line-item VIN for row {n} (selectors id/_l_VIN/name=VIN) with {_ch!r}.", {}
            _is_last = _ix == _n_tot - 1
            if _is_last:
                _safe_page_wait(page, 2800, log_label="after_vin_tab_settle")
            else:
                _safe_page_wait(page, 800, log_label=f"after_vin_tab_settle_row_{n}")

            if _disc_raw:
                if not _fill_discount_for_row(n, _disc_raw):
                    return (
                        False,
                        f"Could not fill line-item Discount for row {n} (id/_l_Discount/name=Discount) with {_disc_raw!r}.",
                        {},
                    )
                _safe_page_wait(page, 500, log_label="after_discount_tab_settle")

        _safe_page_wait(page, 400, log_label="after_all_line_items_before_price_all")

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
        _n_lines = len(_line_items)
        if _n_lines > 1:
            _rows_out: list[dict[str, str]] = []
            for _rn in range(1, _n_lines + 1):
                _vin_rb = _read_vin_from_order_line_row(
                    page, row_n=_rn, content_frame_selector=content_frame_selector
                )
                _ex_r = _scrape_ex_showroom_for_order_line_row(
                    page, row_n=_rn, content_frame_selector=content_frame_selector
                )
                _exp_ch = (_line_items[_rn - 1].get("full_chassis") or "").strip()
                _rows_out.append(
                    {
                        "full_chassis": (_vin_rb.strip() or _exp_ch),
                        "vehicle_ex_showroom_cost": (_ex_r or "").strip(),
                    }
                )
                if _ex_r and _looks_like_ex_showroom_price(_ex_r):
                    note(
                        f"attach_vehicle_to_bkg: row {_rn} Ex-showroom={_ex_r!r}, VIN readback={_vin_rb!r}."
                    )
                else:
                    note(
                        f"attach_vehicle_to_bkg: row {_rn} Ex-showroom missing or not numeric ({_ex_r!r}); "
                        f"VIN readback={_vin_rb!r}."
                    )
            _extra["order_line_ex_showroom"] = _rows_out
            _first_price = ""
            for _r in _rows_out:
                _p = (_r.get("vehicle_ex_showroom_cost") or "").strip()
                if _p and _looks_like_ex_showroom_price(_p):
                    _first_price = _p
                    break
            if _first_price:
                _extra["vehicle_ex_showroom_cost"] = _first_price
                _extra["vehicle_price"] = _first_price
                note(
                    f"attach_vehicle_to_bkg: primary Total (Ex-showroom) from first valid line={_first_price!r}."
                )
            else:
                note(
                    "attach_vehicle_to_bkg: no per-row Ex-showroom passed validation after multi-line scrape "
                    "(best-effort)."
                )
        else:
            _ex_raw = _scrape_total_ex_showroom_after_price_allocate(
                page, content_frame_selector=content_frame_selector
            )
            if _ex_raw and _looks_like_ex_showroom_price(_ex_raw):
                _extra["vehicle_ex_showroom_cost"] = _ex_raw
                _extra["vehicle_price"] = _ex_raw
                _extra["order_line_ex_showroom"] = [
                    {
                        "full_chassis": (_line_items[0].get("full_chassis") or "").strip(),
                        "vehicle_ex_showroom_cost": _ex_raw,
                    }
                ]
                note(f"attach_vehicle_to_bkg: scraped Total (Ex-showroom)={_ex_raw!r} after Price All + Allocate All.")
            else:
                note(
                    "attach_vehicle_to_bkg: Total (Ex-showroom) not scraped or not numeric after Allocate All "
                    "(best-effort)."
                )

        if _fin:
            _fo_post, _fe_post = _fill_order_finance_after_vin_attach(
                page,
                financier_name=_fin,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
            )
            if not _fo_post:
                return (
                    False,
                    (_fe_post or "attach: order finance failed after Allocate All and ex-showroom scrape."),
                    _extra,
                )
            _finance_attach_ok = True

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

    if _fin and not _finance_attach_ok:
        _fo3, _fe3 = _fill_order_finance_after_vin_attach(
            page,
            financier_name=_fin,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
        )
        if not _fo3:
            return False, (_fe3 or "attach: order finance failed before Order link."), {}
        _finance_attach_ok = True

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
            "(set _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE=True in hero_dms_playwright_invoice.py to enable)."
        )

    note(
        "attach_vehicle_to_bkg: all steps completed "
        "(Order → VIN → Pre-check → PDI → Apply Campaign"
        + (" → Create Invoice" if _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE else "")
        + ")."
    )
    # When Create Invoice is auto-clicked, a follow-up scrape of refreshed Order#/Invoice# can be added here.
    return True, None, _extra


def _challan_read_pin_code_field(
    roots: list,
    *,
    action_timeout_ms: int,
) -> str:
    """Best-effort Pin Code / Pincode on booking form (same family as contact applet readback)."""
    _tmo = min(int(action_timeout_ms or 3000), 2500)
    for root in roots:
        for _pin_sel in (
            "input[aria-label*='Pin Code' i]",
            "input[aria-label*='Pincode' i]",
            "input[title*='Pin Code' i]",
            "input[title*='Pincode' i]",
            "input[name*='Pin' i]",
        ):
            try:
                pl = root.locator(_pin_sel).first
                if pl.count() > 0 and pl.is_visible(timeout=500):
                    v = (pl.input_value(timeout=_tmo) or "").strip()
                    if v:
                        return v
            except Exception:
                continue
    return ""


def _fill_challan_account_institution_name_verify_pin(
    page: Page,
    *,
    institution_name: str,
    expected_pin: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note: Callable[..., None],
) -> tuple[bool, str]:
    """
    Subdealer challan: dismiss any open MVG applet (Escape), focus the **input** without using the
    pick icon (``focus()`` first; far-left click only as last resort), type the name, then Tab out.
    Success when Pin Code shows a value (Siebel autopopulate after institution match).
    """
    nm = (institution_name or "").strip()
    if not nm:
        return False, "Account/Institution Name value is empty."

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

    _tmo = min(int(action_timeout_ms or 3000), 4000)

    inst_loc = None
    for root in roots:
        for css in (
            "input[aria-label='Account/Institution Name']",
            "input[aria-label*='Account/Institution Name' i]",
            "input[aria-label*='Account Institution Name' i]",
            "input[aria-label*='Institution Name' i]",
        ):
            try:
                loc = root.locator(css).first
                if loc.count() <= 0 or not loc.is_visible(timeout=600):
                    continue
                try:
                    ro = loc.evaluate("el => el.readOnly === true || el.disabled === true")
                except Exception:
                    ro = False
                if ro:
                    continue
                inst_loc = loc
                break
            except Exception:
                continue
        if inst_loc is not None:
            break

    if inst_loc is None:
        return False, "Could not find Account/Institution Name input (challan)."

    # If a prior step opened the MVG / pick applet, close it — we only type in the field and Tab out.
    for _esc_i in range(3):
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        _safe_page_wait(page, 120, log_label=f"challan_institution_dismiss_applet_esc{_esc_i}")

    try:
        inst_loc.scroll_into_view_if_needed(timeout=_tmo)
    except Exception:
        pass

    # Prefer focus without clicking: any click risks the pick/MVG icon on the right opening an applet.
    _focus_ok = False
    try:
        inst_loc.focus(timeout=_tmo)
        _focus_ok = True
    except Exception:
        pass
    if not _focus_ok:
        try:
            inst_loc.evaluate("el => { try { el.focus(); } catch (e) {} }")
            _focus_ok = True
        except Exception:
            pass
    if not _focus_ok:
        # Last resort: click only the far-left strip of the input (never center/right).
        try:
            box = inst_loc.bounding_box()
            if box and float(box.get("width") or 0) > 12:
                w = float(box["width"])
                h = float(box.get("height") or 20)
                # ~6–14px from left edge, or ~7% of width — whichever is smaller (stays out of icon zone).
                x_left = min(14.0, max(6.0, w * 0.07))
                inst_loc.click(position={"x": x_left, "y": h / 2.0}, timeout=_tmo)
            else:
                inst_loc.click(position={"x": 6, "y": 12}, timeout=_tmo)
        except Exception as e:
            return False, f"Account/Institution Name: could not focus input ({e!s})."

    _safe_page_wait(page, 120, log_label="challan_institution_after_focus")

    try:
        inst_loc.press("Control+a", timeout=1200)
    except Exception:
        pass
    try:
        inst_loc.fill("", timeout=_tmo)
    except Exception:
        pass
    try:
        inst_loc.type(nm, delay=35, timeout=min(_tmo * 2, 12000))
    except Exception:
        try:
            inst_loc.fill(nm, timeout=_tmo)
        except Exception as e:
            return False, f"Account/Institution Name: could not type ({e!s})."

    try:
        inst_loc.press("Tab", timeout=1200)
    except Exception:
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass

    note("Create Order: Account/Institution Name typed + Tab (challan); waiting for Pin Code autopopulate.")

    exp_digits = re.sub(r"\D", "", (expected_pin or "").strip())
    for _poll in range(24):
        _safe_page_wait(page, 250, log_label="challan_institution_pin_poll")
        roots2: list = []
        try:
            roots2.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
        except Exception:
            pass
        try:
            roots2.extend(list(_ordered_frames(page)))
        except Exception:
            pass
        roots2.append(page)
        pin_now = _challan_read_pin_code_field(roots2, action_timeout_ms=_tmo)
        got = re.sub(r"\D", "", pin_now or "")
        if len(got) >= 4:
            if exp_digits and got != exp_digits:
                note(
                    f"Create Order: Pin Code autopopulated → {pin_now!r} "
                    f"(expected {expected_pin!r} — continuing)."
                )
            else:
                note(f"Create Order: Pin Code autopopulated after Institution → {pin_now!r}.")
            return True, ""

    return (
        False,
        "Account/Institution Name: Pin Code did not autopopulate after Tab (challan).",
    )


def _create_order(
    page: Page,
    *,
    mobile: str,
    first_name: str,
    full_chassis: str,
    financier_name: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    contact_id: str = "",
    battery_partial: str = "",
    line_item_discount: str = "",
    attach_line_items: list[dict] | None = None,
    form_trace=None,
    hero_dms_flow: str = "add_sales",
    challan_comments_text: str = "",
    network_dealer_name: str = "",
    challan_network_pin: str = "",
) -> tuple[bool, str | None, dict]:
    """
    Vehicle Sales → Sales Orders flow (same frame as the ``+`` New control):

    - When ``hero_dms_flow=add_subdealer_challan``, skip **My Orders** mobile search and go straight to
      **Sales Orders List:New (+)** after loading the Vehicle Sales URL (avoids dummy-mobile grid loops).

    - Otherwise, after opening **My Orders**, run ``_run_vehicle_sales_my_orders_mobile_search`` (Find ``s_1_1_1_0`` →
      Mobile Phone# → mobile → Enter → ``ui-jqgrid-btable``):

      - **invoiced** (meaningful Invoice# on a row): return success with Order#/Invoice# scrape and
        ``ready_for_client_create_invoice=True`` (skip ``+`` booking and attach).
      - **pending**: drill Order# on the matching row, then ``_attach_vehicle_to_bkg`` (full path).
      - **allocated**: drill Order#, then ``_attach_vehicle_to_bkg(..., start_at_order_link_before_apply=True)``.
      - **unknown_rows** with Order# row(s) and no Invoice# on any row: coerce to **allocated** attach (same drill/skip) instead of **+**; otherwise **no_rows** / **unknown_rows** / **error** fall back to the full **+** new-booking path below.

    - **+** path: Sales Orders New:List, Booking Order Type = Normal Booking, optional Comments
      (``Battery no. is …`` when ``battery_partial`` is set),
      finance fields, Contact Last Name F2 applet, Ctrl+S, then ``_attach_vehicle_to_bkg`` from the new order
      (optional ``line_item_discount`` on the same line-item row as VIN, or ``attach_line_items`` for multiple lines).

    ``attach_line_items``: optional list of dicts (``order_line_vehicles`` / ``attach_vehicles`` from DMS); each row
    ``full_chassis`` / ``vin`` / ``frame_num`` and optional per-line discount. When set, overrides single-line
    ``full_chassis`` + ``line_item_discount`` for attach.
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
    _challan_skip_my_orders = (hero_dms_flow or "add_sales").strip() == "add_subdealer_challan"
    if _challan_skip_my_orders:
        note(
            "Create Order: add_subdealer_challan — skipping My Orders mobile search; "
            "opening new booking via Sales Orders List:New (+)."
        )
    else:

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
                line_item_discount=line_item_discount,
                attach_line_items=attach_line_items,
                financier_name=financier_name,
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

        _flow = (hero_dms_flow or "add_sales").strip()
        if _flow == "add_subdealer_challan":
            _ndn = (network_dealer_name or "").strip()
            for root in _roots():
                try:
                    if _fill_by_label_on_frame(root, "Customer Type", "Network", action_timeout_ms=action_timeout_ms):
                        note("Create Order: set Customer Type = Network (challan).")
                        break
                    if _select_dropdown_by_label_on_frame(
                        root,
                        label="Customer Type",
                        value="Network",
                        action_timeout_ms=min(action_timeout_ms, 8000),
                    ):
                        note("Create Order: set Customer Type = Network via dropdown (challan).")
                        break
                except Exception:
                    continue
            if _ndn:
                _inst_ok, _inst_err = _fill_challan_account_institution_name_verify_pin(
                    page,
                    institution_name=_ndn,
                    expected_pin=(challan_network_pin or "").strip(),
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                )
                if not _inst_ok:
                    return False, _inst_err, scraped
            _safe_page_wait(page, 400, log_label="after_challan_network_fields")

        _bp = (battery_partial or "").strip()
        _challan_ct = (challan_comments_text or "").strip()
        if _flow == "add_subdealer_challan" and _challan_ct:
            _comments_text = _challan_ct
            _filled_comments = False
            _tmo_c = min(int(action_timeout_ms or 3000), 4000)
            _comment_css = (
                'textarea[name="s_2_1_202_0"]',
                'textarea[aria-label="Comments"]',
                'textarea[aria-label*="Comments" i]',
            )
            for root in _all_ui_roots():
                if _filled_comments:
                    break
                for css in _comment_css:
                    try:
                        loc = root.locator(css).first
                        if loc.count() <= 0 or not loc.is_visible(timeout=700):
                            continue
                        try:
                            loc.click(timeout=_tmo_c)
                        except Exception:
                            loc.click(timeout=_tmo_c, force=True)
                        loc.fill("", timeout=_tmo_c)
                        loc.fill(_comments_text, timeout=_tmo_c)
                        try:
                            loc.press("Tab", timeout=1200)
                        except Exception:
                            pass
                        _filled_comments = True
                        break
                    except Exception:
                        continue
            if not _filled_comments:
                for root in _all_ui_roots():
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
                note(f"Create Order: filled Comments (challan) → {_comments_text!r}.")
                if callable(form_trace):
                    form_trace(
                        "v4_create_order_comments",
                        "Sales order / create booking",
                        "fill_Comments_challan_helmet",
                        comments=_comments_text,
                    )
                _safe_page_wait(page, 350, log_label="after_booking_comments_challan")
        elif _bp:
            _comments_text = f"Battery no. is {_bp}"
            _filled_comments = False
            _tmo_c = min(int(action_timeout_ms or 3000), 4000)
            _comment_css = (
                'textarea[name="s_2_1_202_0"]',
                'textarea[aria-label="Comments"]',
                'textarea[aria-label*="Comments" i]',
            )
            for root in _all_ui_roots():
                if _filled_comments:
                    break
                for css in _comment_css:
                    try:
                        loc = root.locator(css).first
                        if loc.count() <= 0 or not loc.is_visible(timeout=700):
                            continue
                        try:
                            loc.click(timeout=_tmo_c)
                        except Exception:
                            loc.click(timeout=_tmo_c, force=True)
                        loc.fill("", timeout=_tmo_c)
                        loc.fill(_comments_text, timeout=_tmo_c)
                        try:
                            loc.press("Tab", timeout=1200)
                        except Exception:
                            pass
                        _filled_comments = True
                        break
                    except Exception:
                        continue
            if not _filled_comments:
                for root in _all_ui_roots():
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

#         # 3b-3d) Finance Required, Financier, Hypothecation
#         _fin_name = (financier_name or "").strip()
#         _is_financed = bool(_fin_name)
#         _fin_val = "Y" if _is_financed else "N"

#         _fin_ok = False
#         for root in _roots():
#             try:
#                 for _lbl in ("Finance Required", "FinanceRequired"):
#                     if _fill_by_label_on_frame(root, _lbl, _fin_val, action_timeout_ms=action_timeout_ms):
#                         _fin_ok = True
#                         _locked_root = root
#                         break
#                     if _select_dropdown_by_label_on_frame(
#                         root,
#                         label=_lbl,
#                         value=_fin_val,
#                         action_timeout_ms=min(action_timeout_ms, 8000),
#                     ):
#                         _fin_ok = True
#                         _locked_root = root
#                         break
#                 if _fin_ok:
#                     break
#             except Exception:
#                 continue
#         if not _fin_ok:
#             return False, f"Could not set Finance Required = {_fin_val}.", scraped
#         _safe_page_wait(page, 400, log_label="after_finance_required")
#         note(f"Create Order: set Finance Required = {_fin_val}.")

#         if _is_financed:
#             _fin_caps = _fin_name.upper()
#             _fin_name_ok = False
#             _fin_hard_err: str | None = None
#             for root in _roots():
#                 try:
#                     ok_fin, fin_msg = _fill_create_order_financier_field_on_frame(
#                         page,
#                         root,
#                         _fin_name,
#                         action_timeout_ms=action_timeout_ms,
#                         content_frame_selector=content_frame_selector,
#                         note=note,
#                     )
#                     if ok_fin:
#                         _fin_name_ok = True
#                         _locked_root = root
#                         break
#                     if fin_msg:
#                         _fin_hard_err = fin_msg
#                         break
#                     for _lbl in ("Financer", "Financier", "Financer Name", "Financier Name"):
#                         if _select_dropdown_by_label_on_frame(
#                             root,
#                             label=_lbl,
#                             value=_fin_caps,
#                             action_timeout_ms=min(action_timeout_ms, 8000),
#                         ):
#                             _fin_name_ok = True
#                             _locked_root = root
#                             break
#                     if _fin_name_ok:
#                         break
#                 except Exception:
#                     continue
#             if _fin_hard_err:
#                 _fin_err = _detect_siebel_error_popup(page, content_frame_selector)
#                 if _fin_err:
#                     return False, f"Siebel error while setting Financier/Financer: {_fin_err[:200]}", scraped
#                 if _fin_hard_err in (
#                     "Financer name not matched",
#                     "Financer name could not be matched",
#                 ):
#                     return False, _fin_hard_err, scraped
#                 return False, _fin_hard_err, scraped
#             if not _fin_name_ok:
#                 _fin_err = _detect_siebel_error_popup(page, content_frame_selector)
#                 if _fin_err:
#                     return False, f"Siebel error while setting Financier/Financer: {_fin_err[:200]}", scraped
#                 return (
#                     False,
#                     f"Could not set Financier/Financer (ALL CAPS + Tab path) from {_fin_name!r} (typed {_fin_caps!r}).",
#                     scraped,
#                 )
#             _safe_page_wait(page, 500, log_label="after_financier_fill")
#             _fin_resolved = _scrape_create_order_financier_display_value(
#                 page, content_frame_selector=content_frame_selector
#             ).strip()
#             if _fin_resolved:
#                 scraped["financier_name"] = _fin_resolved
#             note(
#                 "Create Order: Financier/Financer main field + tablet field2 (ALL CAPS + Enter; no pick icon / "
#                 f"no MVG). staging={_fin_name!r} typed={_fin_caps!r}"
#                 + (
#                     f" siebel_display={scraped.get('financier_name')!r}."
#                     if scraped.get("financier_name")
#                     else "."
#                 )
#             )
#             _fin_post_err = _detect_siebel_error_popup(page, content_frame_selector)
#             if _fin_post_err:
#                 return False, f"Siebel error after Financier/Financer input: {_fin_post_err[:200]}", scraped
#             # Applet closed — focus is often still on Financer; Tab out before Hypothecation / remaining fields.
#             try:
#                 page.keyboard.press("Tab")
#             except Exception:
#                 pass
#             _safe_page_wait(page, 400, log_label="after_financier_tab_out_before_hypothecation")
#             note("Create Order: Tab out from Financer field; proceeding to Hypothecation and next fields.")

#         if _is_financed:
#             _hyp_val = "Y"
#             _hyp_ok = False
#             for root in _roots():
#                 try:
#                     for _lbl in ("Hypothecation", "Hpothecation"):
#                         if _fill_by_label_on_frame(root, _lbl, _hyp_val, action_timeout_ms=action_timeout_ms):
#                             _hyp_ok = True
#                             _locked_root = root
#                             break
#                         if _select_dropdown_by_label_on_frame(
#                             root,
#                             label=_lbl,
#                             value=_hyp_val,
#                             action_timeout_ms=min(action_timeout_ms, 8000),
#                         ):
#                             _hyp_ok = True
#                             _locked_root = root
#                             break
#                     if _hyp_ok:
#                         break
#                 except Exception:
#                     continue
#             if not _hyp_ok:
#                 return False, f"Could not set Hypothecation = {_hyp_val}.", scraped
#             _safe_page_wait(page, 400, log_label="after_hypothecation")
#             note(f"Create Order: set Hypothecation = {_hyp_val}.")
#         else:
#             note("Create Order: financier empty — skipped Financier and Hypothecation fields.")
        if _locked_root is not None:
            try:
                note(f"Create Order: locked booking form context for Contact Last Name/F2 (url={(getattr(_locked_root, 'url', '') or '')[:120]!r}).")
            except Exception:
                note("Create Order: locked booking form context for Contact Last Name/F2.")

        # 4) Contact Last Name → F2 pick applet flow (operator-provided deterministic path)
        if _flow == "add_subdealer_challan":
            _applet_done = True
            _contact_pin_rb = (challan_network_pin or "").strip() or "000000"
            _applet_err = ""
            note("Create Order: subdealer challan — skipped Contact Last Name F2 applet.")
        else:
            _mob_digits = re.sub(r"\D", "", (mobile or "").strip())
            _first_need = (first_name or "").strip().lower()
            _applet_done = False
            _applet_err = ""
            _contact_pin_rb = ""
            _contact_roots = [_locked_root] if _locked_root is not None else list(_roots())


            for root in _contact_roots:
                try:
                    # Use CSS selector with exact aria-label (not get_by_label which can match via label associations)
                    fld = root.locator("input[aria-label*='Contact Last Name' i]").first
                    if fld.count() <= 0 or not fld.is_visible(timeout=700):
                        fld = root.locator("input[aria-label='Contact Last Name']").first
                    if fld.count() <= 0 or not fld.is_visible(timeout=700):
                        continue
    
    
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
                    if not _contact_pin_rb:
                        _applet_err = "Contact applet completed but Pincode stayed empty after selection."
                        continue
                    _applet_done = True
                    break
                except Exception:
                    continue
            if not _applet_done:
                return False, f"Could not complete Contact Last Name F2 applet flow. {_applet_err}".strip(), scraped

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
            line_item_discount=line_item_discount,
            attach_line_items=attach_line_items,
            financier_name=financier_name,
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


def _select_run_report_name(
    page: Page,
    *,
    report_name: str,
    content_frame_selector: str | None,
    action_timeout_ms: int,
    note: Callable[..., None],
) -> bool:
    """
    Set **Report Name** on the Run Report applet. Siebel often hides the real ``<select>`` and shows a
    combobox — ``select_option`` alone may not open the list; we use JS (selectedIndex + events), then
    ``force=True``, then visible input / dropdown icon + option pick.
    """
    want = (report_name or "").strip()
    if not want:
        return False
    _tmo = min(int(action_timeout_ms or 3000), 8000)
    val_pat = re.compile(rf"^\s*{re.escape(want)}\s*$", re.I)

    js_verify_selected = """(want) => {
      const w = String(want || '').trim();
      const norm = (s) => String(s || '').replace(/\\s+/g, '').toLowerCase();
      const wn = norm(w);
      const s = document.querySelector('select[name="s_reportNameField"]');
      if (!s || s.selectedIndex < 0) return false;
      const o = s.options[s.selectedIndex];
      const t = (o.text || '').trim();
      const v = (o.value || '').trim();
      return v === w || t === w || norm(v) === wn || norm(t) === wn ||
        t.indexOf(w) >= 0 || v.indexOf(w) >= 0 || norm(t).indexOf(wn) >= 0;
    }"""

    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            pane = root.locator("#s_ReportPane")
            if pane.count() > 0:
                pane.first.wait_for(state="attached", timeout=15_000)
                try:
                    pane.first.wait_for(state="visible", timeout=10_000)
                except Exception:
                    pass
                note("print_hero_dms_forms: Run Report pane (#s_ReportPane) ready.")
                break
        except Exception:
            continue
    _safe_page_wait(page, 200, log_label="after_report_pane_visible_before_name_field")

    # Options sometimes populate after the pane paints — brief poll.
    for _wait_i in range(24):
        max_opts = 0
        for root in _siebel_all_search_roots(page, content_frame_selector):
            try:
                n = root.evaluate(
                    """() => {
                        const s = document.querySelector('select[name="s_reportNameField"]');
                        return s ? s.options.length : 0;
                    }"""
                )
                max_opts = max(max_opts, int(n or 0))
            except Exception:
                continue
        if max_opts > 0:
            break
        _safe_page_wait(page, 400, log_label="run_report_options_poll")

    js_pick = """(want) => {
      const w = String(want || '').trim();
      const norm = (s) => String(s || '').replace(/\\s+/g, '').toLowerCase();
      const wn = norm(w);
      const nodes = document.querySelectorAll('select[name="s_reportNameField"]');
      for (const sel of nodes) {
        for (let i = 0; i < sel.options.length; i++) {
          const opt = sel.options[i];
          const v = (opt.value || '').trim();
          const t = (opt.text || '').trim();
          if (
            v === w || t === w ||
            norm(v) === wn || norm(t) === wn ||
            t.indexOf(w) >= 0 || v.indexOf(w) >= 0 ||
            norm(t).indexOf(wn) >= 0
          ) {
            try { sel.focus(); } catch (e) {}
            sel.selectedIndex = i;
            ['input','change','blur'].forEach(function(ev) {
              try { sel.dispatchEvent(new Event(ev, { bubbles: true })); } catch(e) {}
            });
            if (window.$ && window.$(sel).trigger) {
              try { window.$(sel).trigger('change'); } catch(e) {}
            }
            return 'js:' + v;
          }
        }
      }
      // Fuzzy: "Form22" / "Form 22" / labels with extra suffix (Siebel LOV text)
      const wn2 = norm(w);
      if (wn2.length >= 4 && wn2.indexOf('form') >= 0 && wn2.indexOf('22') >= 0) {
        for (const sel of nodes) {
          for (let i = 0; i < sel.options.length; i++) {
            const opt = sel.options[i];
            const t = norm(opt.text || '');
            const v = norm(opt.value || '');
            if ((t.indexOf('form') >= 0 && t.indexOf('22') >= 0) ||
                (v.indexOf('form') >= 0 && v.indexOf('22') >= 0) ||
                t.indexOf('form22') >= 0 || v.indexOf('form22') >= 0) {
              try { sel.focus(); } catch (e) {}
              sel.selectedIndex = i;
              ['input','change','blur'].forEach(function(ev) {
                try { sel.dispatchEvent(new Event(ev, { bubbles: true })); } catch(e) {}
              });
              if (window.$ && window.$(sel).trigger) {
                try { window.$(sel).trigger('change'); } catch(e) {}
              }
              return 'js-fuzzy:' + (opt.value || '').trim();
            }
          }
        }
      }
      return '';
    }"""

    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            hit = root.evaluate(js_pick, want)
            if (hit or "").strip():
                note(f"print_hero_dms_forms: Report Name set via DOM ({hit!r}).")
                _safe_page_wait(page, 450, log_label="after_report_name_js")
                return True
        except Exception:
            continue

    for root in _siebel_all_search_roots(page, content_frame_selector):
        sel = root.locator('select[name="s_reportNameField"]').first
        try:
            if sel.count() <= 0:
                continue
            sel.scroll_into_view_if_needed(timeout=_tmo)
            for kwargs in (
                {"value": want, "timeout": _tmo, "force": True},
                {"label": want, "timeout": _tmo, "force": True},
            ):
                try:
                    sel.select_option(**kwargs)
                    note("print_hero_dms_forms: Report Name select_option(force).")
                    _safe_page_wait(page, 350, log_label="after_report_name_select_option")
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    # Siebel combobox: click visible input (or open-list control), type / pick from popup.
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            inp = root.locator(
                '#s_ReportPane input[aria-labelledby*="Report_Name" i], '
                '#s_ReportPane input[id*="Report_Name" i], '
                'input[aria-labelledby="Report_Name_Label_1"]'
            ).first
            if inp.count() <= 0 or not inp.is_visible(timeout=1200):
                continue
            try:
                inp.click(timeout=_tmo)
            except Exception:
                inp.click(timeout=_tmo, force=True)
            _safe_page_wait(page, 300, log_label="after_report_name_input_click")
            try:
                page.keyboard.press("Control+a")
            except Exception:
                pass
            try:
                page.keyboard.type(want, delay=40)
            except Exception:
                pass
            _safe_page_wait(page, 350, log_label="after_report_name_type")
            for opener in (
                ("Alt+ArrowDown", None),
                ("F4", None),
                ("ArrowDown", None),
            ):
                try:
                    if opener[0] == "Alt+ArrowDown":
                        page.keyboard.down("Alt")
                        page.keyboard.press("ArrowDown")
                        page.keyboard.up("Alt")
                    else:
                        page.keyboard.press(opener[0])
                    _safe_page_wait(page, 200, log_label=f"report_name_key_{opener[0]}")
                except Exception:
                    pass
            for opt_root in (page, root):
                try:
                    o = opt_root.get_by_role("option", name=val_pat).first
                    if o.count() > 0 and o.is_visible(timeout=700):
                        o.click(timeout=_tmo)
                        note("print_hero_dms_forms: Report Name picked (role=option).")
                        return True
                except Exception:
                    pass
                try:
                    o = opt_root.locator(
                        "li.ui-menu-item, .ui-menu-item, div[role='option'], li[role='option']"
                    ).filter(has_text=re.compile(re.escape(want), re.I)).first
                    if o.count() > 0 and o.is_visible(timeout=700):
                        o.click(timeout=_tmo)
                        note("print_hero_dms_forms: Report Name picked (menu item).")
                        return True
                except Exception:
                    pass
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            _safe_page_wait(page, 400, log_label="after_report_name_enter")
            for vr in _siebel_all_search_roots(page, content_frame_selector):
                try:
                    if vr.evaluate(js_verify_selected, want):
                        note("print_hero_dms_forms: Report Name set via combobox (verified).")
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            pane = root.locator("#s_ReportPane")
            if pane.count() <= 0:
                continue
            for btn_sel in (
                'a.siebui-btn-icon[title*="Report Name" i]',
                "#s_ReportPane .siebui-icon-dropdown",
                "#s_ReportPane span.siebui-icon-dropdown",
                "#s_ReportPane a.siebui-icon-dropdown",
            ):
                btn = pane.locator(btn_sel).first
                if btn.count() <= 0 or not btn.is_visible(timeout=500):
                    continue
                try:
                    btn.click(timeout=_tmo)
                except Exception:
                    btn.click(timeout=_tmo, force=True)
                _safe_page_wait(page, 450, log_label="after_report_name_dropdown_icon")
                for opt_root in (page, root):
                    try:
                        o = opt_root.get_by_role("option", name=val_pat).first
                        if o.count() > 0 and o.is_visible(timeout=900):
                            o.click(timeout=_tmo)
                            note("print_hero_dms_forms: Report Name via icon + option.")
                            return True
                    except Exception:
                        pass
                    try:
                        o = opt_root.locator("li, div").filter(has_text=val_pat).first
                        if o.count() > 0 and o.is_visible(timeout=600):
                            o.click(timeout=_tmo)
                            note("print_hero_dms_forms: Report Name via icon + list row.")
                            return True
                    except Exception:
                        pass
        except Exception:
            continue

    return False


def _looks_like_uuid_filename(name: str) -> bool:
    """True when the file stem looks like a bare GUID (Siebel temp / stray download)."""
    stem = Path(name).stem
    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            stem,
            re.I,
        )
    )


def _sanitize_report_basename(report_name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (report_name or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    return s or "report"


def _mobile_digits_10(mobile: str) -> str:
    dig = re.sub(r"\D", "", (mobile or "").strip())
    if len(dig) >= 10:
        return dig[-10:]
    if dig:
        return dig.zfill(10)[:10]
    return "0000000000"


def _default_run_report_downloads_dir(mobile: str, *, dealer_id: int | None = None) -> Path:
    """``Uploaded scans/{dealer_id}/{mobile10}_{ddmmyy}/`` — same leaf as Add Sales / pre-OCR."""
    did = int(dealer_id) if dealer_id is not None else int(DEALER_ID)
    return get_uploaded_scans_sale_folder(did, mobile)


def _append_run_hero_dms_reports_to_playwright_log(
    log_path: Path | str | None,
    *,
    downloads_dir: Path | None,
    mobile: str,
    report_names: list[str],
    report_details: list[dict[str, Any]],
    saved_paths: list[str],
    summary: str | None,
    all_ok: bool,
    abort_message: str | None = None,
) -> None:
    """Append a ``--- run_hero_dms_reports ---`` block to the Playwright DMS execution log file."""
    if not log_path:
        return
    try:
        p = Path(str(log_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        dd = ""
        try:
            dd = str(downloads_dir.resolve()) if downloads_dir is not None else ""
        except Exception:
            dd = str(downloads_dir or "")
        lines = [
            "",
            f"--- run_hero_dms_reports ({_ts_ist_iso()}) ---",
            f"downloads_dir={dd!r}",
            f"mobile={_mobile_digits_10(mobile)!r}",
            f"reports_requested={', '.join(report_names)}",
        ]
        if abort_message:
            lines.append(f"status=aborted error={abort_message!r}")
        for r in report_details:
            lines.append(
                "  "
                f"report={r.get('report')!r} ok={r.get('ok')!r} "
                f"path={r.get('path')!r} err={r.get('error')!r}"
            )
        lines.append(f"saved_pdf_paths={saved_paths!r}")
        lines.append(f"all_reports_ok={all_ok!r}")
        if summary:
            lines.append(f"summary={summary!r}")
        lines.append("--- end run_hero_dms_reports ---")
        with open(p, "a", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
    except OSError as ex:
        logger.warning("run_hero_dms_reports: could not append Playwright DMS log %s: %s", log_path, ex)


def _mobile_report_pdf_filename(mobile: str, report_name: str) -> str:
    """``{Mobile}_{Report Name}.pdf`` with a filesystem-safe report stem (spaces → underscores)."""
    mob = _mobile_digits_10(mobile)
    stem = _sanitize_report_basename(report_name).replace(" ", "_")
    return f"{mob}_{stem}.pdf"


def _score_run_report_download(dl, report_name: str) -> int:
    """
    Prefer real ``*.pdf`` names and titles that match the report; deprioritize UUID / no-extension
    names (Siebel sometimes fires a spurious download before the PDF).
    """
    fn = (getattr(dl, "suggested_filename", None) or "").strip()
    if not fn:
        return 0
    lower = fn.lower()
    score = 0
    if lower.endswith(".pdf"):
        score += 100
    if _looks_like_uuid_filename(fn):
        score -= 85
    if not Path(fn).suffix:
        score -= 45
    rn = (report_name or "").lower().replace(" ", "")
    stem = Path(fn).stem.lower().replace(" ", "")
    if rn and rn in stem:
        score += 35
    return score


def _pick_best_run_report_download(downloads: list, report_name: str):
    if not downloads:
        return None
    best_i = max(
        range(len(downloads)),
        key=lambda i: (_score_run_report_download(downloads[i], report_name), i),
    )
    return downloads[best_i]


# Default Run Report batch after staging commit: order matters (GST Retail Invoice first).
DEFAULT_HERO_DMS_RUN_REPORT_NAMES: tuple[str, ...] = (
    "GST Retail Invoice",
    "GST Booking Receipt",
    "Form22",
    "Sale Certificate",
    "Form 20",
)


def print_hero_dms_forms(
    page: Page,
    *,
    mobile: str,
    order_number: str = "",
    invoice_number: str = "",
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note: Callable[..., None] | None = None,
    downloads_dir: Path | None = None,
    downloads_dealer_id: int | None = None,
    report_names: str | list[str] | tuple[str, ...] = DEFAULT_HERO_DMS_RUN_REPORT_NAMES,
    between_reports_wait_ms: int = 500,
    continue_on_report_error: bool = True,
    execution_log_path: Path | str | None = None,
) -> tuple[bool, str | None, list[str], list[dict[str, Any]]]:
    """
    Vehicle Sales → **My Orders** (mobile search), then drill-down on **Invoice#** when available
    (argument or grid ``primary_invoice``), otherwise **Order#** (same as ``_create_order`` pending path),
    then **Report(s)** → Run Report applet. For each name in ``report_names``: select **Report Name**,
    **Submit**, save download.

    Default batch is ``DEFAULT_HERO_DMS_RUN_REPORT_NAMES`` (**GST Retail Invoice** first). Override
    ``report_names`` to run a different set or order.

    When ``downloads_dir`` is omitted, files go under
    ``Uploaded scans/{dealer_id}/{mobile}_{ddmmyy}/`` (see ``get_uploaded_scans_sale_folder`` and ``downloads_dealer_id``).
    Each file is saved as ``{mobile}_{Report Name}.pdf`` (spaces in the report title become underscores).

    When ``execution_log_path`` is set (Playwright DMS ``Playwright_DMS_*.txt``), appends a
    ``run_hero_dms_reports`` section listing downloads directory and per-report outcomes.

    By default ``continue_on_report_error`` is True: failures for one report do not stop the rest; the fourth
    return value lists each report with ``ok``, ``error``, and ``path``. Set False to stop on first failure.

    Stray Siebel downloads (e.g. UUID filenames) are **cancelled** when a better PDF candidate exists; the
    browser tray may still briefly list them depending on timing.
    """
    _note: Callable[..., None] = note if callable(note) else (lambda m: logger.info("%s", m))
    _tmo = min(int(action_timeout_ms or 3000), 8000)

    def _unique_dest(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suf = path.stem, path.suffix
        for i in range(1, 1000):
            alt = path.with_name(f"{stem}_{i}{suf}")
            if not alt.exists():
                return alt
        return path

    if isinstance(report_names, str):
        _names = [report_names.strip()] if (report_names or "").strip() else []
    else:
        _names = [str(x).strip() for x in (report_names or []) if str(x).strip()]
    if not _names:
        _append_run_hero_dms_reports_to_playwright_log(
            execution_log_path,
            downloads_dir=None,
            mobile=mobile or "",
            report_names=_names,
            report_details=[],
            saved_paths=[],
            summary=None,
            all_ok=False,
            abort_message="print_hero_dms_forms: report_names is empty.",
        )
        return False, "print_hero_dms_forms: report_names is empty.", [], []

    digits = re.sub(r"\D", "", (mobile or "").strip())
    if not digits:
        _append_run_hero_dms_reports_to_playwright_log(
            execution_log_path,
            downloads_dir=None,
            mobile=mobile or "",
            report_names=_names,
            report_details=[],
            saved_paths=[],
            summary=None,
            all_ok=False,
            abort_message="print_hero_dms_forms: mobile_phone is empty.",
        )
        return False, "print_hero_dms_forms: mobile_phone is empty.", [], []

    # ── Navigate to Vehicle Sales / My Orders (same URL + post-goto wait as ``_create_order``).
    try:
        from urllib.parse import urlparse as _up

        _purl = _up(page.url)
        _base_url = f"{_purl.scheme}://{_purl.netloc}{_purl.path}"
    except Exception:
        _base_url = "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    _vs_url = f"{_base_url}?SWECmd=GotoView&SWEView=Order+Entry+-+My+Orders+View+(Sales)&SWERF=1&SWEHo=&SWEBU=1"
    _note(f"Create Order: navigating to Vehicle Sales URL. base={_base_url[:60]}")
    try:
        page.goto(_vs_url, timeout=min(_tmo * 3, 45000), wait_until="load")
    except Exception:
        try:
            page.goto(_vs_url, timeout=min(_tmo * 3, 45000), wait_until="domcontentloaded")
        except Exception as _e:
            _note(f"Create Order: goto Vehicle Sales URL raised {_e!r} — continuing.")
    _siebel_after_goto_wait(page, floor_ms=4500)
    _note(f"Create Order: arrived at Vehicle Sales (post-goto wait). URL={page.url[:120]}")

    _mos = _run_vehicle_sales_my_orders_mobile_search(
        page,
        mobile=mobile,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=_note,
    )
    _mo_po = (_mos.primary_order or "").strip()
    _mo_pi = (_mos.primary_invoice or "").strip()
    _on = (order_number or "").strip() or _mo_po
    _inv_eff = (invoice_number or "").strip() or _mo_pi
    _opened = False
    _drill_via = ""
    if _inv_eff and _my_orders_invoice_meaningful(_inv_eff):
        _opened = _click_my_orders_jqgrid_invoice_for_mobile_or_invoice(
            page,
            mobile=mobile,
            invoice_number=_inv_eff,
            content_frame_selector=content_frame_selector,
            note=_note,
            action_timeout_ms=action_timeout_ms,
        )
        if _opened:
            _drill_via = "invoice"
    if not _opened:
        if not _on:
            _append_run_hero_dms_reports_to_playwright_log(
                execution_log_path,
                downloads_dir=None,
                mobile=mobile,
                report_names=_names,
                report_details=[],
                saved_paths=[],
                summary=None,
                all_ok=False,
                abort_message=(
                    "print_hero_dms_forms: need Order# or Invoice# (from argument or My Orders grid)."
                ),
            )
            return (
                False,
                "print_hero_dms_forms: need Order# or Invoice# (from argument or My Orders grid).",
                [],
                [],
            )
        _opened = _click_my_orders_jqgrid_order_for_mobile_or_order(
            page,
            mobile=mobile,
            order_number=_on,
            content_frame_selector=content_frame_selector,
            note=_note,
            action_timeout_ms=action_timeout_ms,
        )
        if _opened:
            _drill_via = "order"
    if not _opened:
        _append_run_hero_dms_reports_to_playwright_log(
            execution_log_path,
            downloads_dir=None,
            mobile=mobile,
            report_names=_names,
            report_details=[],
            saved_paths=[],
            summary=None,
            all_ok=False,
            abort_message=(
                "Create Order: Pending My Orders row but could not open Invoice# or Order# drill-down."
            ),
        )
        return (
            False,
            "Create Order: Pending My Orders row but could not open Invoice# or Order# drill-down.",
            [],
            [],
        )
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    _ctx = (
        "Create Order after My Orders Invoice# click (pending)"
        if _drill_via == "invoice"
        else "Create Order after My Orders Order# click (pending)"
    )
    _err_mo = _poll_and_handle_siebel_error_popup(
        page,
        content_frame_selector,
        _note,
        context=_ctx,
        total_ms=1400,
        step_ms=300,
    )
    if _err_mo:
        _lbl = "Invoice#" if _drill_via == "invoice" else "Order#"
        _em = f"Create Order: Siebel error after My Orders {_lbl} click (pending): {_err_mo[:220]}"
        _append_run_hero_dms_reports_to_playwright_log(
            execution_log_path,
            downloads_dir=None,
            mobile=mobile,
            report_names=_names,
            report_details=[],
            saved_paths=[],
            summary=None,
            all_ok=False,
            abort_message=_em,
        )
        return (
            False,
            _em,
            [],
            [],
        )
    _safe_page_wait(page, 1800, log_label="after_my_orders_pending_drilldown")
    _safe_page_wait(page, 200, log_label="before_reports_tb_14_click")

    # ── Report(s) toolbar → Run Report pane → Form22 → Submit (download).
    _clicked_reports = False
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            rep = root.locator('div#_sweviewbar li#tb_14[role="menuitem"]').first
            if rep.count() <= 0 or not rep.is_visible(timeout=600):
                rep = root.locator("div#_sweviewbar li#tb_14").first
            if rep.count() > 0 and rep.is_visible(timeout=600):
                try:
                    rep.click(timeout=_tmo)
                except Exception:
                    rep.click(timeout=_tmo, force=True)
                _clicked_reports = True
                _note("print_hero_dms_forms: clicked Report(s) (tb_14).")
                _safe_page_wait(page, 1200, log_label="after_reports_toolbar_click")
                break
        except Exception:
            continue
    if not _clicked_reports:
        _append_run_hero_dms_reports_to_playwright_log(
            execution_log_path,
            downloads_dir=None,
            mobile=mobile,
            report_names=_names,
            report_details=[],
            saved_paths=[],
            summary=None,
            all_ok=False,
            abort_message=(
                "print_hero_dms_forms: Report(s) toolbar control (tb_14) not found or not clickable."
            ),
        )
        return (
            False,
            "print_hero_dms_forms: Report(s) toolbar control (tb_14) not found or not clickable.",
            [],
            [],
        )

    if downloads_dir is not None:
        dest_root = Path(downloads_dir).expanduser().resolve()
    else:
        dest_root = _default_run_report_downloads_dir(mobile, dealer_id=downloads_dealer_id).resolve()
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _download_one_run_report(current_report_name: str) -> tuple[bool, str | None, Path | None]:
        """Select ``current_report_name`` in Run Report applet, Submit, save file to ``dest_root``."""
        if not _select_run_report_name(
            page,
            report_name=current_report_name,
            content_frame_selector=content_frame_selector,
            action_timeout_ms=action_timeout_ms,
            note=_note,
        ):
            return (
                False,
                f"could not set Report Name to {current_report_name!r} (DOM / combobox).",
                None,
            )
        sub_btn = None
        for root in _siebel_all_search_roots(page, content_frame_selector):
            try:
                b = root.locator('button[title="Run Report:Submit"].appletButton').first
                if b.count() <= 0 or not b.is_visible(timeout=400):
                    b = root.locator('button[title="Run Report:Submit"]').first
                if b.count() > 0 and b.is_visible(timeout=800):
                    sub_btn = b
                    break
            except Exception:
                continue
        if sub_btn is None:
            return False, "Run Report Submit button not found.", None
        # Siebel may emit two download events: a stray UUID/no-extension blob, then the real PDF.
        # ``expect_download`` only sees the first — collect all and pick the best candidate.
        _collected: list = []

        def _on_download(_d) -> None:
            _collected.append(_d)

        page.on("download", _on_download)
        try:
            try:
                sub_btn.click(timeout=_tmo)
            except Exception:
                sub_btn.click(timeout=_tmo, force=True)
            _deadline = time.time() + min(180.0, max(45.0, _tmo / 1000.0 * 6))
            while time.time() < _deadline:
                page.wait_for_timeout(120)
                if len(_collected) >= 1:
                    page.wait_for_timeout(850)
                    break
            else:
                if not _collected:
                    page.wait_for_timeout(2500)
        finally:
            try:
                page.remove_listener("download", _on_download)
            except Exception:
                pass

        if not _collected:
            return False, "no download event after Submit (timed out).", None
        if len(_collected) > 1:
            _note(
                f"print_hero_dms_forms: {len(_collected)} download event(s) for "
                f"{current_report_name!r} — using best PDF candidate."
            )

        try:
            best = _pick_best_run_report_download(_collected, current_report_name)
            if best is None:
                best = _collected[-1]
            for d in _collected:
                if d is best:
                    continue
                try:
                    d.cancel()
                except Exception:
                    pass
            out_fn = _mobile_report_pdf_filename(mobile, current_report_name)
            final_path = _unique_dest(dest_root / out_fn)
            best.save_as(str(final_path))
            _note(
                f"print_hero_dms_forms: report {current_report_name!r} saved to {final_path!r}."
            )
            return True, None, final_path
        except PlaywrightTimeout:
            return False, "timed out while saving report download.", None
        except Exception as _ex:
            return False, f"download/save failed: {_ex!s}", None

    saved_paths: list[str] = []
    report_details: list[dict[str, Any]] = []
    for _i, _rn in enumerate(_names):
        if _i > 0:
            _safe_page_wait(
                page,
                max(0, int(between_reports_wait_ms)),
                log_label=f"between_run_reports_{_i}",
            )
        ok_one, err_one, path_one = _download_one_run_report(_rn)
        report_details.append(
            {
                "report": _rn,
                "ok": ok_one,
                "error": err_one,
                "path": str(path_one) if path_one is not None else None,
            }
        )
        if ok_one and path_one is not None:
            saved_paths.append(str(path_one))
        if not ok_one:
            if not continue_on_report_error:
                _summary_partial = err_one or "download failed"
                _append_run_hero_dms_reports_to_playwright_log(
                    execution_log_path,
                    downloads_dir=dest_root,
                    mobile=mobile,
                    report_names=_names,
                    report_details=report_details,
                    saved_paths=saved_paths,
                    summary=_summary_partial,
                    all_ok=False,
                    abort_message=f"stopped on first failure (report: {_rn!r})",
                )
                return (
                    False,
                    f"print_hero_dms_forms: {err_one or 'download failed'} (report: {_rn!r})",
                    saved_paths,
                    report_details,
                )
    _all_ok = all(r.get("ok") for r in report_details)
    _summary: str | None = None
    if not _all_ok:
        _parts = [
            f"{r['report']}: {r.get('error') or 'failed'}"
            for r in report_details
            if not r.get("ok")
        ]
        _summary = "; ".join(_parts) if _parts else "one or more reports failed."
    _append_run_hero_dms_reports_to_playwright_log(
        execution_log_path,
        downloads_dir=dest_root,
        mobile=mobile,
        report_names=_names,
        report_details=report_details,
        saved_paths=saved_paths,
        summary=_summary,
        all_ok=_all_ok,
        abort_message=None,
    )
    return _all_ok, _summary, saved_paths, report_details


def prepare_order(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    out: dict,
    *,
    mobile: str,
    video_first_name: str,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    note: Callable[..., None],
    step: Callable[..., None],
    form_trace: Callable[..., None] | None,
    ms_done: Callable[[str], None] | None,
    log_vehicle_snapshot: Callable[[str], None],
) -> dict:
    """
    Generate Booking + ``_create_order`` + merge scrape into ``out[\"vehicle\"]``.
    Returns ``order_scraped`` from ``_create_order`` (may be empty). On failure sets ``out[\"error\"]``.
    """
    full_chassis = (
        str((out.get("vehicle") or {}).get("full_chassis") or "").strip()
        or str(dms_values.get("full_chassis") or "").strip()
        or str(dms_values.get("frame_num") or "").strip()
    )
    _enq_u = (urls.enquiry or "").strip() or (urls.contact or "").strip()
    if _enq_u:
        _goto(page, _enq_u, "enquiry_for_booking_video", nav_timeout_ms=nav_timeout_ms)
        _siebel_after_goto_wait(page, floor_ms=900)
    _safe_page_wait(page, 500, log_label="before_generate_booking_video")
    if _try_click_generate_booking(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("Video path: clicked Generate Booking before create_order.")
        if callable(ms_done):
            ms_done("Booking generated")
    else:
        step("Stopped: Generate Booking was not found before create_order (video path).")
        out["error"] = (
            "Siebel: Generate Booking control was not found before create_order. "
            "Booking is mandatory when no existing order is present."
        )
        return {}

    if callable(form_trace):
        form_trace(
            "v4_create_order",
            "Vehicle Sales / Sales Orders",
            "vehicle_sales_new_order_then_pick_contact_then_vin_search_price_allocate",
            mobile_phone=mobile,
            first_name=video_first_name,
            full_chassis=full_chassis,
        )
    _line_disc = (
        str(dms_values.get("line_item_discount") or dms_values.get("discount") or "").strip()
    )
    _raw_olv = dms_values.get("order_line_vehicles") or dms_values.get("attach_vehicles")
    _attach_li = _raw_olv if isinstance(_raw_olv, list) and len(_raw_olv) > 0 else None
    _flow_pv = str(dms_values.get("hero_dms_flow") or "add_sales").strip()
    ok_order, order_err, order_scraped = _create_order(
        page,
        mobile=mobile,
        first_name=video_first_name,
        full_chassis=full_chassis,
        financier_name=(dms_values.get("financier_name") or "").strip(),
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        contact_id=out.get("contact_id", ""),
        battery_partial=(dms_values.get("battery_partial") or "").strip(),
        line_item_discount=_line_disc,
        attach_line_items=_attach_li,
        form_trace=form_trace,
        hero_dms_flow=_flow_pv,
        challan_comments_text=str(dms_values.get("challan_comments_text") or "").strip(),
        network_dealer_name=str(
            dms_values.get("network_dealer_name") or dms_values.get("to_dealer_name") or ""
        ).strip(),
        challan_network_pin=str(
            dms_values.get("network_pin_code") or dms_values.get("pin_code") or ""
        ).strip(),
    )
    if not ok_order:
        step("Stopped: create_order flow failed.")
        out["error"] = f"Siebel: create_order failed. {order_err or ''}".strip()
        return {}

    if order_scraped.get("ready_for_client_create_invoice"):
        out["ready_for_client_create_invoice"] = True

    if order_scraped:
        veh = dict(out.get("vehicle") or {})
        if order_scraped.get("inventory_location"):
            veh["inventory_location"] = order_scraped.get("inventory_location")
        if order_scraped.get("vehicle_price"):
            veh["vehicle_price"] = order_scraped.get("vehicle_price")
        if order_scraped.get("order_number"):
            veh["order_number"] = order_scraped.get("order_number")
        if order_scraped.get("invoice_number"):
            veh["invoice_number"] = order_scraped.get("invoice_number")
        if order_scraped.get("vehicle_ex_showroom_cost"):
            veh["vehicle_ex_showroom_cost"] = order_scraped.get("vehicle_ex_showroom_cost")
        if order_scraped.get("order_line_ex_showroom"):
            veh["order_line_ex_showroom"] = order_scraped.get("order_line_ex_showroom")
        if order_scraped.get("cubic_capacity"):
            veh["cubic_capacity"] = order_scraped.get("cubic_capacity")
        if order_scraped.get("vehicle_type"):
            veh["vehicle_type"] = order_scraped.get("vehicle_type")
        _fn_sc = (order_scraped.get("financier_name") or "").strip()
        if _fn_sc:
            veh["financier_name"] = _fn_sc
            dms_values["financier_name"] = _fn_sc
            _cm_up = out.get("dms_customer_master_collated")
            if isinstance(_cm_up, dict):
                _cf_up = _cm_up.get("fields")
                if isinstance(_cf_up, dict):
                    _cf_up["financier"] = _fn_sc
        out["vehicle"] = veh
        log_vehicle_snapshot("video_create_order_scrape_merge")

    return dict(order_scraped or {})
