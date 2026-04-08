"""
Hero Connect / Oracle Siebel Open UI — vehicle automation (Playwright).

Find-Vehicle search, VIN/engine fill, vehicle list scrape, vehicle detail/features,
serial number drilldown, Pre-check/PDI, inventory location gate, and the
``prepare_vehicle`` pipeline. Also contains add-enquiry vehicle helpers called
from the customer module.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeout

from app.config import (
    DEALER_ID,
    DMS_SIEBEL_AUTO_IFRAME_SELECTORS,
    DMS_SIEBEL_INTER_ACTION_DELAY_MS,
    DMS_SIEBEL_POST_GOTO_WAIT_MS,
)
from app.services.hero_dms_shared_utilities import (
    SiebelDmsUrls,
    _detect_siebel_error_popup,
    _goto,
    _is_browser_disconnected_error,
    _iter_frame_locator_roots,
    _normalize_cubic_cc_digits,
    _ordered_frames,
    _safe_page_wait,
    _siebel_after_goto_wait,
    _siebel_all_search_roots,
    _siebel_click_by_id_anywhere,
    _siebel_ist_now,
    _siebel_ist_today,
    _siebel_locator_search_roots,
    _siebel_naive_datetime_as_ist,
    _siebel_note_frame_focus_snapshot,
    _siebel_scrape_text_by_id_anywhere,
    _try_click_toolbar_by_name,
    _try_expand_find_flyin,
    _fill_by_label_on_frame,
    _try_fill_field,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Branch-coverage tracker — logs which Siebel UI fallback strategy actually
# succeeds during a run.  Accumulated in-memory; dumped by ``prepare_vehicle``
# and ``_siebel_vehicle_find_chassis_engine_enter`` at the end of their flows.
# ---------------------------------------------------------------------------
_BRANCH_HITS: dict[str, int] = {}


def _branch_hit(func: str, branch: str) -> None:
    key = f"{func}::{branch}"
    _BRANCH_HITS[key] = _BRANCH_HITS.get(key, 0) + 1


def _dump_branch_hits(note) -> None:
    if not _BRANCH_HITS:
        return
    note(f"BRANCH_HITS ({len(_BRANCH_HITS)} entries): {dict(_BRANCH_HITS)}")
    logger.info("branch_hits: %s", _BRANCH_HITS)


# Left **Search Results** jqGrid roots: HMCL builds differ — Title anchors may use ``s_100_1_l`` while
# another skin keeps ``gview_s_1001_l``; cover both plus a generic fallback (skipping main list ``gview_s_1_l``).
_SIEBEL_VEHICLE_LEFT_SEARCH_GVIEW_IDS: tuple[str, ...] = (
    "gview_s_1001_l",
    "gview_s_100_1_l",
)


def _siebel_left_search_gview_table_id(gview_id: str) -> str:
    """``gview_s_1001_l`` → ``s_1001_l`` (Siebel table id parallel to the gview wrapper)."""
    s = (gview_id or "").strip()
    if s.startswith("gview_"):
        return s[len("gview_") :]
    return s


def _siebel_vehicle_find_wildcard_value(raw: str) -> str:
    """Hero Connect vehicle Find uses ``*`` prefix on VIN/Engine for partial match (see operator screenshots)."""
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("*"):
        return s
    return f"*{s}"


def _try_prepare_find_vehicles_applet(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    """
    Header **Find** → object type **Vehicles** (same pattern as Find → Contact), so the right fly-in
    exposes **VIN** / **Engine#** query fields — not Job Card / Contact / Vehicle Sales.
    """
    vehicles_label = re.compile(r"^\s*Vehicles\s*$", re.I)
    find_label = re.compile(r"^\s*Find\s*$", re.I)

    def _force_open_vehicles_find_via_dom() -> bool:
        js = """() => {
          const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width >= 10 && r.height >= 10;
          };
          const norm = (s) => String(s || '').trim().toLowerCase();
          const sels = Array.from(document.querySelectorAll('select')).filter(vis);
          for (const sel of sels) {
            const opts = Array.from(sel.options || []);
            const hasFind = opts.some(o => norm(o.textContent) === 'find');
            const veh = opts.find(o => norm(o.textContent) === 'vehicles');
            if (!hasFind || !veh) continue;
            sel.focus();
            sel.value = veh.value;
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            sel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            sel.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
            sel.blur();
            const host = sel.closest('div,td,th,form,header') || sel.parentElement || document.body;
            const trig = host.querySelector(
              '[title*="find" i], [aria-label*="find" i], button[title*="find" i], a[title*="find" i]'
            );
            if (trig && vis(trig)) {
              try { trig.click(); } catch (e) {}
            }
            return true;
          }
          return false;
        }"""
        try:
            ok = bool(page.evaluate(js))
            if ok:
                logger.info("siebel_dms: forced global Vehicles selection via DOM fallback")
                _safe_page_wait(page, 700, log_label="force_open_vehicles_find_dom")
            return ok
        except Exception:
            return False

    def _select_global_find_vehicles(root) -> bool:
        for scope in (root, page):
            try:
                cb = scope.get_by_role("combobox", name=find_label).first
                if cb.count() <= 0 or not cb.is_visible(timeout=500):
                    continue
                cb.click(timeout=timeout_ms)
                _safe_page_wait(page, 250, log_label="global_find_open_vehicles_click")
                for role in ("option", "menuitem", "link"):
                    try:
                        item = page.get_by_role(role, name=vehicles_label).first
                        if item.count() > 0 and item.is_visible(timeout=600):
                            item.click(timeout=timeout_ms)
                            logger.info("siebel_dms: global finder clicked Vehicles (%s)", role)
                            _safe_page_wait(page, 500, log_label="global_find_vehicles_clicked")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        try:
            sels = root.locator("select")
            n = sels.count()
        except Exception:
            n = 0
        for i in range(min(n, 20)):
            try:
                sel = sels.nth(i)
                if not sel.is_visible(timeout=500):
                    continue
                opts = sel.evaluate(
                    """el => [...el.options].map(o => (o.textContent || '').trim().toLowerCase())"""
                )
                if not opts:
                    continue
                has_find = any(x == "find" for x in opts)
                has_vehicles = any(x == "vehicles" for x in opts)
                if not (has_find and has_vehicles):
                    continue
                try:
                    sel.select_option(label=find_label, timeout=timeout_ms)
                    _safe_page_wait(page, 180, log_label="global_find_select_find_vehicles")
                except Exception:
                    pass
                sel.select_option(label=vehicles_label, timeout=timeout_ms)
                logger.info("siebel_dms: global top finder selected Vehicles (native select)")
                _safe_page_wait(page, 350, log_label="global_find_vehicles_select")
                return True
            except Exception:
                continue

        for scope in (root, page):
            try:
                cb = scope.get_by_role("combobox", name=re.compile(r"^\s*find\s*$", re.I)).first
                if cb.count() > 0 and cb.is_visible(timeout=500):
                    cb.click(timeout=timeout_ms)
                    _safe_page_wait(page, 250, log_label="global_find_open_vehicles")
                    for role in ("option", "menuitem", "link"):
                        try:
                            item = page.get_by_role(role, name=vehicles_label).first
                            if item.count() > 0 and item.is_visible(timeout=500):
                                item.click(timeout=timeout_ms)
                                logger.info("siebel_dms: global top finder chose Vehicles (%s)", role)
                                _safe_page_wait(page, 350, log_label="global_find_vehicles_menu")
                                return True
                        except Exception:
                            continue
            except Exception:
                continue
        if _force_open_vehicles_find_via_dom():
            _branch_hit("_select_global_find_vehicles", "dom_force")
            return True
        return False

    def _visible_selects(root):
        try:
            loc = root.locator("select")
            n = loc.count()
        except Exception:
            return []
        out = []
        for i in range(min(n, 50)):
            try:
                s = loc.nth(i)
                if s.is_visible(timeout=400):
                    out.append(s)
            except Exception:
                continue
        return out

    def select_vehicles_on_native_selects(root) -> bool:
        for sel in _visible_selects(root):
            try:
                texts = sel.evaluate(
                    """el => [...el.options].map(o => (o.textContent || '').trim())"""
                )
                if not texts or not any((t or "").strip().lower() == "vehicles" for t in texts):
                    continue
                exact = [t for t in texts if (t or "").strip().lower() == "vehicles"]
                if not exact:
                    continue
                sel.select_option(label=vehicles_label, timeout=timeout_ms)
                logger.info("siebel_dms: Find object type → Vehicles (native select)")
                return True
            except Exception:
                continue
        return False

    def click_vehicles_menu(page_: Page, root) -> bool:
        for scope in (root, page_):
            for role in ("menuitem", "option", "link"):
                try:
                    loc = scope.get_by_role(role, name=vehicles_label).first
                    if loc.count() > 0 and loc.is_visible(timeout=800):
                        loc.click(timeout=timeout_ms)
                        logger.info("siebel_dms: chose Vehicles from Find menu (%s)", role)
                        return True
                except Exception:
                    continue
        return False

    def open_find_dropdown_then_vehicles(page_: Page, root) -> bool:
        try:
            cb = root.get_by_role("combobox", name=re.compile(r"find", re.I)).first
            if cb.count() > 0 and cb.is_visible(timeout=700):
                cb.click(timeout=timeout_ms)
                _safe_page_wait(page_, 400, log_label="find_combobox_vehicles")
                if click_vehicles_menu(page_, root):
                    return True
        except Exception:
            pass
        return False

    def open_find_combobox_aria_then_vehicles(page_: Page, root) -> bool:
        """Hero **Find ComboBox** (``aria-label``) → **Vehicles** (same as operator Find pane)."""
        for css in ('[aria-label="Find ComboBox" i]', '[aria-label="Find combobox" i]'):
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=700):
                    loc.click(timeout=timeout_ms)
                    _safe_page_wait(page_, 450, log_label="find_combobox_aria_labeled")
                    if click_vehicles_menu(page_, root):
                        logger.info("siebel_dms: aria-label Find ComboBox → Vehicles")
                        return True
            except Exception:
                continue
        for name_re in (re.compile(r"^\s*Find\s+ComboBox\s*$", re.I), re.compile(r"Find\s+ComboBox", re.I)):
            try:
                cb = root.get_by_role("combobox", name=name_re).first
                if cb.count() > 0 and cb.is_visible(timeout=700):
                    cb.click(timeout=timeout_ms)
                    _safe_page_wait(page_, 450, log_label="find_combobox_role_find_combobox")
                    if click_vehicles_menu(page_, root):
                        logger.info("siebel_dms: role combobox Find ComboBox → Vehicles")
                        return True
            except Exception:
                continue
        return False

    def try_on_root(page_: Page, root) -> bool:
        if open_find_combobox_aria_then_vehicles(page_, root):
            _branch_hit("_try_prepare_find_vehicles_applet", "aria_combobox")
            return True
        changed_global = _select_global_find_vehicles(root)
        if select_vehicles_on_native_selects(root):
            _branch_hit("_try_prepare_find_vehicles_applet", "native_select")
            return True
        if open_find_dropdown_then_vehicles(page_, root):
            _branch_hit("_try_prepare_find_vehicles_applet", "dropdown_regex")
            return True
        if changed_global:
            _branch_hit("_try_prepare_find_vehicles_applet", "global_find")
        return changed_global

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        try:
            if try_on_root(page, fl):
                return True
        except Exception:
            pass
    for frame in _ordered_frames(page):
        try:
            if try_on_root(page, frame):
                return True
        except Exception:
            continue
    return False


def _try_fill_vin_engine_in_vehicles_find_applet(
    page: Page,
    *,
    chassis_wildcard: str,
    engine_wildcard: str,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """
    Fill **VIN** and **Engine#** inside the Find→Vehicles right fly-in, then submit.

    Production-observed winning path: ``#findfieldsbox`` JS strict (``input#field_textbox_0``
    / ``input#field_textbox_2``) with Find button or Enter key.
    """
    cw = (chassis_wildcard or "").strip()
    ew = (engine_wildcard or "").strip()
    if not cw or not ew:
        return False

    _FINDFIELDSBOX_JS = """(args) => {
      const vinVal = String(args.vin || '').trim();
      const engVal = String(args.eng || '').trim();
      const box = document.getElementById('findfieldsbox') || document.getElementById('findfieldbox');
      if (!box) return { ok: false, reason: 'no_findfieldsbox' };
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const editable = (el) => !!(el && vis(el) && !el.readOnly && !el.disabled);
      const vin = box.querySelector('input#field_textbox_0');
      const eng = box.querySelector('input#field_textbox_2');
      if (!editable(vin)) return { ok: false, reason: 'vin_not_editable_or_missing' };
      if (!editable(eng)) return { ok: false, reason: 'engine_not_editable_or_missing' };
      vin.focus(); vin.value = ''; vin.value = vinVal;
      vin.dispatchEvent(new Event('input', { bubbles: true }));
      vin.dispatchEvent(new Event('change', { bubbles: true }));
      eng.focus(); eng.value = ''; eng.value = engVal;
      eng.dispatchEvent(new Event('input', { bubbles: true }));
      eng.dispatchEvent(new Event('change', { bubbles: true }));
      for (const s of [
        'input[type="submit"][value*="Find" i]', 'input[type="button"][value*="Find" i]',
        'button[title="Find" i]', 'button[aria-label="Find" i]',
        '[role="button"][title="Find" i]', '[role="button"][aria-label="Find" i]'
      ]) {
        const b = box.querySelector(s);
        if (b && vis(b)) { try { b.click(); return { ok: true, mode: 'find_button' }; } catch (e) {} }
      }
      try {
        eng.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
        eng.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
        return { ok: true, mode: 'enter_key' };
      } catch (e) { return { ok: false, reason: 'submit_failed' }; }
    }"""

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        try:
            out = fl.evaluate(_FINDFIELDSBOX_JS, {"vin": cw, "eng": ew})
            if out and out.get("ok"):
                _branch_hit("_try_fill_vin_engine", "findfieldsbox_js_strict")
                return True
        except Exception:
            continue
    for frame in _ordered_frames(page):
        try:
            out = frame.evaluate(_FINDFIELDSBOX_JS, {"vin": cw, "eng": ew})
            if out and out.get("ok"):
                _branch_hit("_try_fill_vin_engine", "findfieldsbox_js_strict")
                return True
        except Exception:
            continue
    logger.debug("vehicle_find: findfieldsbox not found or not editable in any root")
    return False


def _merge_scrape_vehicle_detail_applet(page: Page, scraped: dict, *, content_frame_selector: str | None) -> dict:
    """
    After the left-pane **Search Results** VIN drill-down (or when detail is visible), read **Vehicle
    Information** from table rows and from Siebel **input** ``title`` / ``aria-label`` (Model, Color,
    Dispatch Year → ``year_of_mfg``, SKU).
    """
    _ = content_frame_selector
    detail_js = """() => {
      const out = {};
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
      const lblTxt = (inp) => {
        const t = norm(inp.getAttribute('title') || '');
        const a = norm(inp.getAttribute('aria-label') || '');
        return (t + ' ' + a).toLowerCase();
      };
      const setInp = (inp, key) => {
        if (!inp || !vis(inp)) return;
        const v = norm(inp.value || inp.getAttribute('value') || '');
        if (!v || v === '*') return;
        out[key] = v;
      };
      const applets = Array.from(document.querySelectorAll('.siebui-applet')).filter(vis);
      for (const ap of applets) {
        const block = norm(ap.innerText || '');
        if (!block.includes('Model') && !block.includes('Vehicle') && !block.includes('Vehicle Information')) continue;
        const rows = ap.querySelectorAll('tr');
        for (const tr of rows) {
          if (!vis(tr)) continue;
          const cells = tr.querySelectorAll('td, th');
          if (cells.length < 2) continue;
          const lab = norm(cells[0].innerText || '');
          const val = norm(cells[1].innerText || '');
          if (!lab || !val || val === '*') continue;
          if (/^Model$/i.test(lab)) out.model = val;
          if (/^Dispatch\\s*Year$/i.test(lab)) out.dispatch_year = val;
          if (/^Year\\s+of\\s+Manufacture$/i.test(lab) || /^Mfg\\.?\\s*Year$/i.test(lab)) out.year_of_mfg = val;
          if (/^Color$/i.test(lab) || /^Body\\s*Color$/i.test(lab) || /^Colour$/i.test(lab)) out.color = val;
          if (/^SKU$/i.test(lab)) out.sku = val;
        }
        const inputs = Array.from(ap.querySelectorAll('input')).filter((i) => vis(i));
        for (const inp of inputs) {
          const lt = lblTxt(inp);
          if (lt.includes('model') && !lt.includes('model year')) setInp(inp, 'model');
          if (lt.includes('dispatch') && lt.includes('year')) setInp(inp, 'dispatch_year');
          if ((lt.includes('color') || lt.includes('colour')) && !lt.includes('discount')) setInp(inp, 'color');
          if (/^sku\\b/.test(lt) || lt === 'sku' || (lt.includes('sku') && !lt.includes('risk'))) setInp(inp, 'sku');
        }
      }
      return out;
    }"""
    extra: dict = {}
    for frame in _ordered_frames(page):
        try:
            got = frame.evaluate(detail_js)
            if isinstance(got, dict) and got:
                for k, v in got.items():
                    if v and str(v).strip():
                        extra[k] = str(v).strip()
        except Exception:
            continue
    if not extra:
        return scraped
    merged = dict(scraped) if scraped else {}
    if extra.get("model") and not (merged.get("model") or "").strip():
        merged["model"] = extra["model"]
    y = (merged.get("year_of_mfg") or "").strip()
    if not y:
        if (extra.get("year_of_mfg") or "").strip():
            merged["year_of_mfg"] = extra["year_of_mfg"].strip()
        elif (extra.get("dispatch_year") or "").strip():
            merged["year_of_mfg"] = extra["dispatch_year"].strip()
    if extra.get("color") and not (merged.get("color") or "").strip():
        merged["color"] = extra["color"]
    if extra.get("sku") and not (merged.get("sku") or "").strip():
        merged["sku"] = extra["sku"]
    _apply_year_of_mfg_yyyy(merged)
    return merged


_VIN_ARIA_SCOPE_SCRAPE_JS = """() => {
  const out = {};
  const vis = (el) => {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2;
  };
  const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
  const badPh = /case sensitive|^[*]$/i;

  const isVinField = (inp) => {
    if (!inp || inp.tagName !== 'INPUT') return false;
    const al = norm(inp.getAttribute('aria-label') || '');
    const tt = norm(inp.getAttribute('title') || '');
    return /^vin$/i.test(al) || /^vin$/i.test(tt);
  };

  const vinInp = Array.from(document.querySelectorAll('input')).find((i) => isVinField(i) && vis(i));
  if (!vinInp) return out;

  let root = vinInp.closest('.siebui-applet');
  if (!root || !vis(root)) root = document.body;

  const harvestValue = (el) => {
    if (!el) return '';
    if (el.tagName === 'SELECT') {
      const so = el.selectedOptions && el.selectedOptions[0];
      return norm(so ? so.textContent : el.value);
    }
    return norm(el.value || el.getAttribute('value') || '');
  };

  const takeIfGood = (v) => v && !badPh.test(v) && v !== '*' && v.length > 0;

  const fc = harvestValue(vinInp);
  if (takeIfGood(fc)) out.full_chassis = fc;

  const inputs = Array.from(root.querySelectorAll('input, select')).filter(vis);
  for (const el of inputs) {
    if (el === vinInp) continue;
    const al = norm(el.getAttribute('aria-label') || '');
    const tt = norm(el.getAttribute('title') || '');
    const lk = (al + ' ' + tt).toLowerCase();
    const v = harvestValue(el);
    if (!takeIfGood(v)) continue;
    if (/\\bmodel\\b/.test(lk) && !/year/.test(lk) && !/code/.test(lk) && !/number/.test(lk)) out.model = v;
    if (/dispatch\\s*year/.test(lk) || /manufacturing\\s*year/.test(lk) || /^mfg\\.?\\s*year$/i.test(lk)) {
      out.year_of_mfg = v;
      out.dispatch_year = v;
    }
    if ((/^color$/i.test(al) || /^color$/i.test(tt) || /body\\s*color/.test(lk) || /^colour$/i.test(al)) &&
        !/discount/.test(lk)) {
      out.color = v;
    }
    if (/engine\\s*#/.test(lk) || /^engine#$/i.test(al.replace(/\\s/g, '')) ||
        (lk.includes('engine') && lk.includes('#'))) {
      out.full_engine = v;
    }
    if (/^engine\\s*number$/i.test(al) || /^engine\\s*number$/i.test(tt)) out.full_engine = v;
  }

  const tables = root.querySelectorAll('table');
  for (const t of tables) {
    const rows = t.querySelectorAll('tr');
    if (rows.length < 2) continue;
    const hdrCells = rows[0].querySelectorAll('th, td');
    let engCol = -1;
    for (let c = 0; c < hdrCells.length; c++) {
      const hx = norm(hdrCells[c].innerText || '');
      if (/engine\\s*#|engine#/i.test(hx)) {
        engCol = c;
        break;
      }
    }
    if (engCol < 0) continue;
    for (let r = 1; r < Math.min(rows.length, 8); r++) {
      const cells = rows[r].querySelectorAll('td, th');
      if (cells.length <= engCol) continue;
      const ev = norm(cells[engCol].innerText || '');
      if (takeIfGood(ev)) {
        out.full_engine = ev;
        break;
      }
    }
  }
  return out;
}"""


def _score_vehicle_detail_dict(d: dict) -> int:
    keys = ("full_chassis", "full_engine", "model", "year_of_mfg", "color")
    return sum(1 for k in keys if (str(d.get(k) or "").strip()))


def _merge_scrape_vehicle_record_from_vin_aria(
    page: Page, scraped: dict, *, content_frame_selector: str | None
) -> dict:
    """
    After left-pane VIN drill-down, read **Vehicle Information** scoped from an input with
    ``aria-label``/title **VIN** (operator anchor). Produces ``full_chassis``, ``full_engine``,
    and fills ``model`` / ``year_of_mfg`` / ``color`` when inputs are present.
    """
    _ = content_frame_selector
    merged = dict(scraped) if scraped else {}
    best: dict = {}
    for frame in _ordered_frames(page):
        try:
            got = frame.evaluate(_VIN_ARIA_SCOPE_SCRAPE_JS)
            if not isinstance(got, dict) or not got:
                continue
            if _score_vehicle_detail_dict(got) > _score_vehicle_detail_dict(best):
                best = got
        except Exception:
            continue
    if not best:
        return merged
    for k in ("full_chassis", "full_engine", "dispatch_year"):
        v = best.get(k)
        if v and str(v).strip():
            merged[k] = str(v).strip()
    detail_anchor = (best.get("full_chassis") or "").strip()
    if detail_anchor:
        for k in ("model", "year_of_mfg", "color"):
            bv = (best.get(k) or "").strip()
            if bv:
                merged[k] = bv
    else:
        if best.get("model") and not (merged.get("model") or "").strip():
            merged["model"] = str(best["model"]).strip()
        yb = (best.get("year_of_mfg") or "").strip()
        if yb and not (merged.get("year_of_mfg") or "").strip():
            merged["year_of_mfg"] = yb
        if best.get("color") and not (merged.get("color") or "").strip():
            merged["color"] = str(best["color"]).strip()
    _apply_year_of_mfg_yyyy(merged)
    return merged


def _vin_match_key(chassis: str) -> str:
    """Alphanumeric compact form for matching left-pane VIN links (strip wildcards / spaces)."""
    s = (chassis or "").strip().lstrip("*").rstrip("*").strip()
    return re.sub(r"[^A-Za-z0-9]", "", s)


def _normalize_manufacturing_year_yyyy(raw: str) -> str:
    """
    Siebel **Dispatch Year** / date fields may return ``2009``, ``2,009`` (grouped digits), ``24/03/2009``,
    or ISO strings. Store **year_of_mfg** as four-digit ``YYYY`` (1900–2099).
    """
    s = (raw or "").strip()
    if not s:
        return ""

    def _ok_year(y: str) -> bool:
        try:
            n = int(y, 10)
            return 1900 <= n <= 2099
        except ValueError:
            return False

    # Strip common grouping so "2,009" / "2 009" / thin-space grouped years become "2009"
    de_grouped = re.sub(r"[\s,\u00a0\u202f'ʼ`]", "", s)
    for m in re.finditer(r"(19\d{2}|20\d{2})", de_grouped):
        y = m.group(1)
        if _ok_year(y):
            return y

    m = re.search(r"\b(19\d{2}|20\d{2})\b", s)
    if m and _ok_year(m.group(1)):
        return m.group(1)
    return ""


def _apply_year_of_mfg_yyyy(d: dict) -> None:
    """Normalize ``year_of_mfg`` in place when present."""
    y = _normalize_manufacturing_year_yyyy(str(d.get("year_of_mfg") or ""))
    if y:
        d["year_of_mfg"] = y


def _siebel_try_click_vin_search_hit_link(
    page: Page,
    chassis: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """
    After vehicle Find/Enter, open the hit from the left **Search Results** pane (blue VIN hyperlink).
    Primary: jqGrid ``div#gview_s_1001_l`` or ``#gview_s_100_1_l`` (HMCL id variants) → ``a[name="Title"]``
    / ``a[id*="_l_Title"]``; Playwright click with JS fallback across frames. Skips the main list grid
    ``gview_s_1_l`` when probing unknown jqGrid roots.

    When **DMS only has a partial**, match on visible text plus **title** / **aria-label**; fall back to the
    **only** VIN-like Title link (11–19 alnum) when a single hit row is shown.
    """
    vin_key = _vin_match_key(chassis)
    use_key = bool(vin_key) and len(vin_key) >= 4

    # Left Search Results jqGrid often paints after the main list — give the Title row time to appear.
    _safe_page_wait(page, 1500, log_label="vin_left_search_pre_drill_settle")

    def _try_click_siebel_drilldown(loc) -> bool:
        try:
            if not loc.is_visible(timeout=1400):
                return False
        except Exception:
            return False
        for click_try in (
            lambda: loc.click(timeout=timeout_ms),
            lambda: loc.click(timeout=timeout_ms, force=True),
            lambda: loc.dblclick(timeout=timeout_ms),
        ):
            try:
                click_try()
                return True
            except Exception:
                continue
        return False

    def _title_link_vin_compact(link) -> str:
        parts: list[str] = []
        try:
            parts.append(link.inner_text(timeout=800) or "")
        except Exception:
            pass
        for attr in ("title", "aria-label"):
            try:
                v = link.get_attribute(attr)
                if v:
                    parts.append(v)
            except Exception:
                pass
        return re.sub(r"[^A-Za-z0-9]", "", " ".join(parts))

    def row_contains_vin(row_compact: str) -> bool:
        if not use_key or not row_compact:
            return False
        rk = row_compact.upper()
        vk = vin_key.upper()
        if vk in rk:
            return True
        if len(rk) >= 4 and rk in vk:
            return True
        if rk.endswith(vk) or rk.startswith(vk):
            return True
        if len(rk) >= 4 and len(vk) >= 4 and (vk.startswith(rk) or rk.startswith(vk)):
            return True
        return False

    def _gview_title_chains_for(gview_id: str) -> tuple[str, ...]:
        tid = _siebel_left_search_gview_table_id(gview_id)
        return (
            f"#{gview_id}.ui-jqgrid-view table#{tid} a[name='Title']",
            f"#{gview_id} table.ui-jqgrid-btable a[name='Title']",
            f"#{gview_id} table#{tid} a[name='Title']",
            f'#{gview_id} table[id="{tid}"] a[name="Title"]',
            f"div#{gview_id}.ui-jqgrid-view a[name='Title']",
            f'#{gview_id} a[name="Title"][id*="_l_Title"]',
            f'#{gview_id} a[id*="_l_Title"]',
            f".ui-jqgrid-view#{gview_id} a[name='Title']",
        )

    def _try_gview_unique_single_title(scope, gview_id: str) -> bool:
        """
        Exactly one visible **Title** link whose text looks like a full VIN (11–19 alnum).
        """
        try:
            g = scope.locator(f"#{gview_id}").first
            if g.count() == 0 or not g.is_visible(timeout=1200):
                return False
            titles = g.locator('a[name="Title"], a[id*="_l_Title"]')
            n = titles.count()
            if n != 1:
                return False
            link = titles.first
            if not link.is_visible(timeout=1500):
                return False
            t = _title_link_vin_compact(link)
            if len(t) < 11 or len(t) > 19:
                return False
            try:
                link.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            return _try_click_siebel_drilldown(link)
        except Exception:
            return False

    def _try_gview_title_links_for_id(scope, gview_id: str) -> bool:
        """Click **Title** drilldown under one left-search jqGrid root."""
        if not use_key:
            return _try_gview_unique_single_title(scope, gview_id)
        for title_chain in _gview_title_chains_for(gview_id):
            try:
                titles = scope.locator(title_chain)
                tn = titles.count()
                for ti in range(min(tn, 40)):
                    link = titles.nth(ti)
                    try:
                        if not link.is_visible(timeout=1600):
                            continue
                        compact = _title_link_vin_compact(link)
                        if not row_contains_vin(compact):
                            continue
                        try:
                            link.scroll_into_view_if_needed(timeout=1500)
                        except Exception:
                            pass
                        if _try_click_siebel_drilldown(link):
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        if _try_gview_unique_single_title(scope, gview_id):
            return True
        return False

    def _try_left_search_gviews_in_root(scope) -> bool:
        if not use_key:
            for gid in _SIEBEL_VEHICLE_LEFT_SEARCH_GVIEW_IDS:
                if _try_gview_unique_single_title(scope, gid):
                    return True
            return False
        for gid in _SIEBEL_VEHICLE_LEFT_SEARCH_GVIEW_IDS:
            if _try_gview_title_links_for_id(scope, gid):
                return True
        return False

    def _try_js_click_left_search_titles(frame) -> bool:
        """DOM click in known + discovered left jqGrids when Playwright hit-testing fails."""
        try:
            hit = frame.evaluate(
                """({ vk, useKey, knownIds }) => {
                  const norm = (s) => String(s || '').replace(/[^A-Za-z0-9]/g, '');
                  const key = norm(vk);
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 1 && r.height >= 1;
                  };
                  const linkCompact = (a) => {
                    const t = norm(a.innerText || a.textContent || '');
                    const title = norm(a.getAttribute('title') || '');
                    const al = norm(a.getAttribute('aria-label') || '');
                    return t + title + al;
                  };
                  const clickMatching = (nodes) => {
                    if (useKey && key && key.length >= 4) {
                      for (const a of nodes) {
                        const raw = linkCompact(a);
                        if (!raw) continue;
                        const T = raw.toUpperCase();
                        const K = key.toUpperCase();
                        if (T.includes(K) || K.includes(T) || T.endsWith(K) || T.startsWith(K)) {
                          try { a.scrollIntoView({ block: 'center' }); } catch (e) {}
                          try { a.click(); return true; } catch (e) {}
                          continue;
                        }
                        if (K.length >= 4 && T.length >= 4 && (K.startsWith(T) || T.startsWith(K))) {
                          try { a.scrollIntoView({ block: 'center' }); } catch (e) {}
                          try { a.click(); return true; } catch (e) {}
                        }
                      }
                      return false;
                    }
                    const vinLike = (a) => {
                      const n = linkCompact(a);
                      return n.length >= 11 && n.length <= 19;
                    };
                    const cand = nodes.filter(vinLike);
                    if (cand.length !== 1) return false;
                    try { cand[0].scrollIntoView({ block: 'center' }); } catch (e) {}
                    try { cand[0].click(); return true; } catch (e) { return false; }
                  };
                  const clickFirstMatchInGrid = (g) => {
                    if (!g) return false;
                    try { g.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
                    const nodes = Array.from(
                      g.querySelectorAll('a[name="Title"], a[id*="_l_Title"]')
                    ).filter(vis);
                    return clickMatching(nodes);
                  };
                  for (const id of knownIds) {
                    const g = document.getElementById(id);
                    if (g && clickFirstMatchInGrid(g)) return true;
                  }
                  const skip = new Set(['gview_s_1_l']);
                  const seen = new Set(knownIds);
                  const extras = [];
                  for (const g of document.querySelectorAll('div.ui-jqgrid-view[id^="gview_s_"]')) {
                    const id = g.id || '';
                    if (!id || skip.has(id) || seen.has(id)) continue;
                    const nodes = Array.from(
                      g.querySelectorAll('a[name="Title"], a[id*="_l_Title"]')
                    ).filter(vis);
                    if (!nodes.length) continue;
                    extras.push({ g, n: nodes.length });
                  }
                  extras.sort((a, b) => a.n - b.n);
                  for (const { g } of extras) {
                    if (clickFirstMatchInGrid(g)) return true;
                  }
                  return false;
                }""",
                {
                    "vk": vin_key,
                    "useKey": use_key,
                    "knownIds": list(_SIEBEL_VEHICLE_LEFT_SEARCH_GVIEW_IDS),
                },
            )
            return bool(hit)
        except Exception:
            return False

    def _one_attempt() -> bool:
        def try_click_in_root(root) -> bool:
            if _try_left_search_gviews_in_root(root):
                _branch_hit("_siebel_try_click_vin", "pw_gview_title")
                return True
            return False

        for root in _siebel_locator_search_roots(page, content_frame_selector):
            try:
                if try_click_in_root(root):
                    return True
            except Exception:
                continue
        for fr in list(_ordered_frames(page)) + [page.main_frame]:
            try:
                if _try_js_click_left_search_titles(fr):
                    _branch_hit("_siebel_try_click_vin", "js_left_search_title")
                    return True
            except Exception:
                continue
        return False

    _vin_drill_retry_ms = (800, 1100, 1400, 1700)
    for attempt in range(1 + len(_vin_drill_retry_ms)):
        if attempt:
            _safe_page_wait(
                page,
                _vin_drill_retry_ms[attempt - 1],
                log_label=f"vin_left_search_drill_retry_{attempt}",
            )
        if _one_attempt():
            return True
    return False


def _siebel_parse_grid_date_cell_to_date(text: str) -> date | None:
    """Best-effort parse for Siebel list/grid date cells (often DD/MM/YYYY). Returns None if unknown."""
    t = (text or "").strip()
    if not t:
        return None
    for sep in (" ", "T"):
        if sep in t:
            t = t.split(sep)[0].strip()
            break
    t = t[:10].strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def _siebel_parse_pdi_expiry_cell_to_datetime(text: str) -> datetime | None:
    """
    Parse PDI Expiry cells (date + optional time). Returns timezone-naive ``datetime`` in **IST**
    wall-clock (same convention as Siebel display) or None.
    """
    t = (text or "").strip()
    if not t:
        return None
    t = re.sub(r"[\u00a0\u202f]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for fmt in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(t[:96].strip(), fmt)
        except ValueError:
            continue
    return None


def _siebel_pdi_expiry_still_valid(
    *,
    expiry_dates: list[date],
    expiry_datetimes: list[datetime],
    buffer: timedelta,
) -> tuple[bool, date | None, datetime | None]:
    """
    PDI is still valid if any expiry **datetime** (interpreted as **IST**) is after
    ``now_ist - buffer`` (grace window for clock skew / scrape delay), or any **date** is on or after
    **today** in IST (calendar-day expiry).
    """
    _today = _siebel_ist_today()
    _now = _siebel_ist_now()
    _best_d: date | None = max(expiry_dates) if expiry_dates else None
    _best_dt: datetime | None = max(expiry_datetimes) if expiry_datetimes else None
    _best_dt_ist: datetime | None = (
        _siebel_naive_datetime_as_ist(_best_dt) if _best_dt is not None else None
    )
    if _best_dt_ist is not None and _best_dt_ist > _now - buffer:
        return True, _best_dt_ist.date(), _best_dt_ist
    if _best_d is not None and _best_d >= _today:
        return True, _best_d, _best_dt_ist
    return False, _best_d, _best_dt_ist


def _siebel_lov_pick_first_row_ok_pdi_style(
    page: Page,
    *,
    roots: Callable[[], list],
    action_timeout_ms: int,
    note,
    log_prefix: str,
    stage_label: str,
) -> tuple[bool, bool]:
    """
    PDI **Service Request** pick-applet pattern (shared with Pre-check **Technician** LOV).

    Sequence matches the inline PDI block: **800ms** settle → click first plausible table data row
    (``table tbody tr`` / ``table tr``) → **600ms** → click **OK** (five selectors, **500ms** visibility) →
    **1000ms** → **400ms** after-dialog settle.

    Returns ``(row_clicked, ok_clicked)``. Caller may require both before **Submit** / **Ctrl+S**.
    """
    _tmo = min(int(action_timeout_ms or 3000), 4000)
    _safe_page_wait(page, 800, log_label=f"after_{stage_label}_lov_pdi_style_settle")
    _row_hit = False
    for root in roots():
        try:
            _row_result = root.evaluate("""() => {
                const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                const rows = Array.from(document.querySelectorAll('table tbody tr, table tr'));
                for (const tr of rows) {
                    if (!vis(tr)) continue;
                    const tds = tr.querySelectorAll('td');
                    if (tds.length < 2) continue;
                    const cls = (tr.className || '').toLowerCase();
                    if (cls.includes('jqgfirstrow') || cls.includes('header')) continue;
                    const txt = (tr.textContent || '').trim();
                    if (!txt || txt.length < 3) continue;
                    const clickable = tr.querySelector('a, input[type="radio"], input[type="checkbox"], td');
                    if (clickable) { clickable.click(); } else { tr.click(); }
                    return 'row_clicked';
                }
                return '';
            }""")
            if _row_result:
                _row_hit = True
                note(f"{log_prefix}: picked first row in pick applet ({stage_label}).")
                _safe_page_wait(page, 600, log_label=f"after_{stage_label}_row_pick")
                break
        except Exception:
            continue

    _ok_done = False
    for root in roots():
        for ok_css in (
            "button[aria-label*='OK' i]",
            "a[aria-label*='OK' i]",
            "input[type='button'][value='OK' i]",
            "button:has-text('OK')",
            "a:has-text('OK')",
        ):
            try:
                ok_loc = root.locator(ok_css).first
                if ok_loc.count() > 0 and ok_loc.is_visible(timeout=500):
                    try:
                        ok_loc.click(timeout=_tmo)
                    except Exception:
                        ok_loc.click(timeout=_tmo, force=True)
                    _ok_done = True
                    note(f"{log_prefix}: clicked OK on pick applet ({stage_label}).")
                    _safe_page_wait(page, 1000, log_label=f"after_{stage_label}_ok")
                    break
            except Exception:
                continue
        if _ok_done:
            break
    if not _ok_done:
        note(f"{log_prefix}: OK button not found on pick applet ({stage_label}) (best-effort).")

    _safe_page_wait(page, 400, log_label=f"after_{stage_label}_lov_close_settle")
    return _row_hit, _ok_done


def _siebel_click_service_request_list_new_record(
    page: Page,
    *,
    roots: Callable[[], list],
    action_timeout_ms: int,
    note,
    log_prefix: str,
    context: str,
) -> bool:
    """
    Click **Service Request List:New** (toolbar ``+`` / ``siebui-icon-newrecord``).

    Shared by **Pre-check** and **PDI** tabs: JS tries known ids (``s_3_1_12_0_Ctrl``, several ``s_2_*``), then
    scans visible ``siebui-icon-newrecord`` for **Service Request** / **PDI List** / **Precheck** ``List:New``
    labels; ``get_by_role`` for those names; CSS (skip **Menu**). PDI often uses **PDI List:New** or ``s_2_*``
    ids, not only **Service Request List:New**. See **LLD** **6.240**, **6.243**.
    """
    _tmo = min(int(action_timeout_ms or 3000), 4000)

    def _label_is_list_new_not_menu(el) -> bool:
        try:
            al = (el.get_attribute("aria-label") or "").strip()
            tt = (el.get_attribute("title") or "").strip()
        except Exception:
            return False
        lab = f"{al} {tt}".lower()
        if "menu" in lab and "new" not in lab:
            return False
        if ":new" in lab or "list:new" in lab.replace(" ", ""):
            return True
        if al.endswith(":New") or tt.endswith(":New"):
            return True
        return False

    _sr_new_selectors = (
        "button#s_3_1_12_0_Ctrl.siebui-icon-newrecord",
        "button#s_2_1_12_0_Ctrl.siebui-icon-newrecord",
        "button#s_2_2_31_0_Ctrl.siebui-icon-newrecord",
        "button.siebui-icon-newrecord[aria-label='Service Request List:New']",
        "button.siebui-icon-newrecord[title='Service Request List:New']",
        "button[data-display='New'][aria-label='Service Request List:New']",
        "[aria-label='Service Request List:New']",
        "a[aria-label='Service Request List:New']",
        "button[aria-label='Service Request List:New']",
        "[aria-label*='Service Request List' i][aria-label*='New' i]",
        "[title='Service Request List:New']",
        "a[title='Service Request List:New']",
        "img[title='Service Request List:New']",
        "[title*='Service Request List:New' i]",
        "[aria-label='PDI List:New']",
        "[aria-label='Pdi List:New']",
        "button[aria-label='PDI List:New']",
        "a[aria-label='PDI List:New']",
        "[aria-label*='PDI' i][aria-label*='List' i][aria-label*='New' i]",
        "[title='PDI List:New']",
        "[title*='PDI List:New' i]",
        "[aria-label='Precheck List:New']",
        "[aria-label='Pre-check List:New']",
        "a[aria-label='Precheck List:New']",
        "a[aria-label='Pre-check List:New']",
        "button[aria-label='Precheck List:New']",
        "button[aria-label='Pre-check List:New']",
        "[aria-label*='Precheck' i][aria-label*='New' i]",
        "[aria-label*='Pre-check' i][aria-label*='New' i]",
        "[title='Precheck List:New']",
        "[title='Pre-check List:New']",
        "a[title='Precheck List:New']",
        "img[title='Precheck List:New']",
        "[title*='Precheck List:New' i]",
        "[title*='Pre-check List:New' i]",
    )

    # Pre-check tab usually exposes ``s_3_1_12_0_Ctrl``; PDI tab often uses ``s_2_*`` applets — try several
    # ids, then scan all visible ``siebui-icon-newrecord`` buttons (same idea as Pre-check when labels match).
    _js_plus = """() => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width >= 2 && r.height >= 2;
        };
        const isListNewNotMenu = (el) => {
            const al = (el.getAttribute('aria-label') || '').toLowerCase();
            const tt = (el.getAttribute('title') || '').toLowerCase();
            const dd = (el.getAttribute('data-display') || '').trim();
            const lab = al + ' ' + tt;
            if (lab.includes('service request list: menu')) return false;
            if (lab.includes('menu') && !lab.includes('list:new') && !lab.includes('list: new')) return false;
            if (al.includes('service request list:new') || tt.includes('service request list:new')) return true;
            if (al.includes('pdi list:new') || tt.includes('pdi list:new')) return true;
            if (al.includes('precheck list:new') || al.includes('pre-check list:new')) return true;
            if (tt.includes('precheck list:new') || tt.includes('pre-check list:new')) return true;
            if (dd === 'New' && (lab.includes('list') || lab.includes('applet'))) return true;
            if (lab.includes('list:new') || lab.includes('list : new')) return true;
            return false;
        };
        const tryClickId = (hid) => {
            const el = document.getElementById(hid);
            if (!el || String(el.tagName).toLowerCase() !== 'button') return false;
            if (!el.classList.contains('siebui-icon-newrecord')) return false;
            const dd = (el.getAttribute('data-display') || '').trim();
            const idOk =
                isListNewNotMenu(el)
                || (dd === 'New' && (hid.indexOf('s_2_') === 0 || hid.indexOf('s_3_1_12') === 0));
            if (!idOk) return false;
            if (!vis(el)) return false;
            try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
            el.click();
            return true;
        };
        const ids = [
            's_3_1_12_0_Ctrl', 's_2_2_32_0', 's_2_1_12_0_Ctrl', 's_2_2_31_0_Ctrl', 's_2_2_33_0_Ctrl',
            's_2_1_11_0_Ctrl', 's_2_2_30_0_Ctrl',
        ];
        for (const hid of ids) {
            if (tryClickId(hid)) return true;
        }
        const btns = Array.from(document.querySelectorAll('button.siebui-icon-newrecord'));
        for (const el of btns) {
            if (!vis(el) || !isListNewNotMenu(el)) continue;
            try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
            el.click();
            return true;
        }
        return false;
    }"""

    for _root in roots():
        try:
            if bool(_root.evaluate(_js_plus)):
                _branch_hit("_click_sr_list_new", f"js_newrecord_{context}")
                note(
                    f"{log_prefix}: clicked {context} + (JS: siebui-icon-newrecord — "
                    "ids s_3_1_12_0_Ctrl / s_2_* or scan matching Service Request / PDI / Precheck List:New)."
                )
                return True
        except Exception:
            continue

    for _sr_role_name in (
        "Service Request List:New",
        "PDI List:New",
        "Precheck List:New",
        "Pre-check List:New",
    ):
        for _root in roots():
            for _role in ("link", "button"):
                try:
                    _rl = _root.get_by_role(_role, name=_sr_role_name, exact=True)
                    if _rl.count() > 0 and _rl.first.is_visible(timeout=600):
                        try:
                            _rl.first.click(timeout=_tmo)
                        except Exception:
                            _rl.first.click(timeout=_tmo, force=True)
                        _branch_hit("_click_sr_list_new", f"role_{_role}_{context}")
                        note(
                            f"{log_prefix}: clicked {context} + via role={_role!r} "
                            f"name={_sr_role_name!r}."
                        )
                        return True
                except Exception:
                    continue

    for _root in roots():
        for _css in _sr_new_selectors:
            try:
                _grp = _root.locator(_css)
                _n = _grp.count()
                for _ii in range(min(_n, 12)):
                    _loc = _grp.nth(_ii)
                    if not _loc.is_visible(timeout=500):
                        continue
                    if not _label_is_list_new_not_menu(_loc):
                        continue
                    try:
                        _loc.click(timeout=_tmo)
                    except Exception:
                        _loc.click(timeout=_tmo, force=True)
                    _branch_hit("_click_sr_list_new", f"css_{_css[:30]}_{context}")
                    note(f"{log_prefix}: clicked {context} + ({_css!r} nth={_ii}).")
                    return True
            except Exception:
                continue

    return False


def _click_third_level_view_bar_tab(
    page: Page,
    tab_text: str,
    *,
    wait_ms: int,
    content_frame_selector: str | None,
    note,
    log_prefix: str,
) -> bool:
    """
    Click a tab in Siebel's Third Level View Bar (``#s_vctrl_div``).

    Used for **Pre-check** and **PDI** tabs. Production-observed: ``s_vctrl_div`` is the
    consistent container; hyphen-insensitive match (e.g. "Pre-check" ↔ "PreCheck").
    """
    tab_norm = (tab_text or "").strip().lower()
    if not tab_norm:
        return False

    _TAB_JS = """(tabNeedle) => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        const norm = (s) => String(s || '').trim().toLowerCase();
        const compact = (s) => s.replace(/[-\\s]+/g, '');
        const matches = (txt, needle) => {
            if (txt === needle || txt.includes(needle)) return true;
            const a = compact(txt); const b = compact(needle);
            return a === b || a.includes(b) || b.includes(a);
        };
        const bar = document.getElementById('s_vctrl_div');
        if (!bar || !vis(bar)) return { ok: false };
        const tabs = Array.from(bar.querySelectorAll("a, button, [role='tab']"));
        for (const t of tabs) {
            if (!vis(t)) continue;
            const raw = (t.innerText || t.textContent || t.getAttribute('aria-label') || t.getAttribute('title') || '');
            const txt = norm(raw);
            if (!matches(txt, tabNeedle)) continue;
            let target = t;
            if ((t.tagName || '').toUpperCase() === 'LI') {
                const inner = t.querySelector("a, button, [role='tab']");
                if (inner && inner !== t) { target = inner; }
                else { continue; }
            }
            try { target.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
            try { target.focus(); } catch (e) {}
            try { target.click(); } catch (e) {}
            try {
                const opts = { bubbles: true, cancelable: true, view: window };
                target.dispatchEvent(new MouseEvent('mousedown', opts));
                target.dispatchEvent(new MouseEvent('mouseup', opts));
                target.dispatchEvent(new MouseEvent('click', opts));
            } catch (e2) {}
            return { ok: true };
        }
        return { ok: false };
    }"""

    _ = content_frame_selector
    for root in list(_ordered_frames(page)) + [page.main_frame]:
        try:
            _res = root.evaluate(_TAB_JS, tab_norm)
            if isinstance(_res, dict) and _res.get("ok"):
                _branch_hit("_click_third_level_tab", f"{tab_text}_s_vctrl_div")
                note(f"{log_prefix}: clicked {tab_text} from #s_vctrl_div tab strip.")
                _safe_page_wait(page, wait_ms, log_label=f"after_third_level_{tab_norm}_tab")
                return True
        except Exception:
            continue
    logger.debug("third_level_tab: s_vctrl_div not found for tab=%r", tab_text)
    return False


def _siebel_run_vehicle_serial_detail_precheck_pdi(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
    log_prefix: str = "vehicle_serial_detail",
    scraped: dict | None = None,
    do_feature_id_scrape: bool = True,
) -> tuple[bool, str | None]:
    """
    Pre-check + PDI applets on the **vehicle serial** detail view (after ``Serial Number`` drilldown).

    Shared by ``prepare_vehicle`` and ``_attach_vehicle_to_bkg``. Third Level View Bar tabs are
    clicked by label (with hyphen-insensitive match for **Pre-check** vs **PreCheck**). Tab ``ui-id-*``
    values are dynamic across runs/tenants, so fixed tab ids are not treated as primary selectors.
    **Pre-check list ``+``:** **Service Request** applet header — ``button#s_3_1_12_0_Ctrl`` with class
    ``siebui-icon-newrecord`` (``title``/``aria-label`` = **Service Request List:New**); also **Service Request List:New**
    / ``Precheck List:New`` by role or CSS. Skip **Service Request List: Menu**. Then **Technician** pick must target the **second** pick icon when
    generic ``siebui-icon-picklist`` CSS is used (``.first`` would re-click **Open**).
    **Open** / **Technician**: click first
    **jqgrow** row, then **Tab**-retry loops for **Open** pick only. **Submit** / **Ctrl+S** runs **right after** the **Open** LOV
    closes when save succeeds; **Technician** LOV runs only when Submit did not persist or validation remains. PDI: **Service Request List:New**
    then optional legacy ``s_2_2_32_0_icon`` / ``s_2_2_32_0``. After the **Open** pick icon, ``_pick_first_row_and_ok``; after the
    **Technician** pick icon, ``_siebel_lov_pick_first_row_ok_pdi_style`` (same settle / first row /
    **OK** / settle as the PDI pick applet). LOV pick icons use ``*_icon`` ids — **never** ``s_3_1_12_0_Ctrl`` (that id is the
    header **+** / ``siebui-icon-newrecord``, not a picklist). **Technician** tries ``s_3_2_26_0_icon`` / ``s_3_2_24_0_icon``
    / … before ``s_3_2_25_0_icon`` (often **Open**).

    **Pre-check existing rows:** Before creating a row, the flow probes for an existing Pre-check list row.
    The probe counts ``table.ui-jqgrid-btable`` grids scoped by **Precheck** / **Pre-check** text,
    **Service Request List** / **applet** labels, or **gview_s_3** / **s_3_*_l** jqGrid ids, excluding
    **gview_s_2** / **s_2_l** (PDI list). Unrelated large grids are still avoided (not every ``<table>``).
    """
    _tmo = min(int(action_timeout_ms or 3000), 4000)

    def _roots():
        return _siebel_all_search_roots(page, content_frame_selector)

    if do_feature_id_scrape and scraped is not None:
        cc = _siebel_scrape_text_by_id_anywhere(
            page, "4_s_1_l_HHML_Feature_Value", content_frame_selector=content_frame_selector
        ) or _siebel_scrape_text_by_id_anywhere(
            page, "4_s_1_l_HHML_Fetaure_Value", content_frame_selector=content_frame_selector
        )
        vt = _siebel_scrape_text_by_id_anywhere(
            page, "5_s_1_l_HHML_Feature_Value", content_frame_selector=content_frame_selector
        ) or _siebel_scrape_text_by_id_anywhere(
            page, "5_s_1_l_HHML_Fetaure_Value", content_frame_selector=content_frame_selector
        )
        _cc_log = ""
        if cc:
            _cc_norm = _normalize_cubic_cc_digits(cc) or str(cc).strip()
            scraped["cubic_capacity"] = _cc_norm
            _cc_log = _cc_norm
        if vt:
            scraped["vehicle_type"] = vt
        note(
            f"{log_prefix}: feature-id scrape cubic_capacity={_cc_log!r}, vehicle_type={vt!r}."
        )

    _siebel_note_frame_focus_snapshot(
        page,
        note,
        "precheck_pdi_entry_before_precheck_tab",
        log_prefix=log_prefix,
        content_frame_selector=content_frame_selector,
    )
    if callable(form_trace):
        form_trace(
            "vehicle_serial_precheck_pdi",
            "Vehicle serial detail",
            "precheck_tab_open",
            log_prefix=log_prefix,
        )

    _precheck_tab_ok = _click_third_level_view_bar_tab(
        page, "Pre-check", wait_ms=1500,
        content_frame_selector=content_frame_selector, note=note, log_prefix=log_prefix,
    )
    if not _precheck_tab_ok:
        _precheck_tab_ok = _siebel_click_by_id_anywhere(
            page,
            "ui-id-1115",
            timeout_ms=_tmo,
            content_frame_selector=content_frame_selector,
            note=note,
            label="Pre-check tab (legacy ui-id-1115)",
            log_prefix=log_prefix,
            wait_ms=1500,
        )
    if not _precheck_tab_ok:
        return False, "Could not open Pre-check tab (Third Level View Bar text match failed)."

    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    _siebel_note_frame_focus_snapshot(
        page,
        note,
        "precheck_pdi_after_precheck_tab_networkidle",
        log_prefix=log_prefix,
        content_frame_selector=content_frame_selector,
    )

    _precheck_existing_rows = 0
    _precheck_existing_signal = ""
    for _ri, _root in enumerate(_roots()):
        try:
            _probe = _root.evaluate("""() => {
                const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                // Only jqGrid list tables under a Pre-check / Precheck List applet. Counting every
                // visible <table> on the page (or max across iframes) picked unrelated grids (e.g. 89 rows)
                // and skipped Pre-check entry when the Pre-check list was actually empty.
                const isPrecheckScoped = (el) => {
                    const isPdiGrid = (node) => {
                        let x = node;
                        for (let d = 0; d < 22 && x; d++) {
                            const pid = String(x.id || '').toLowerCase();
                            if (pid.includes('gview_s_2') || pid.includes('s_2_l')) return true;
                            x = x.parentElement;
                        }
                        return false;
                    };
                    if (isPdiGrid(el)) return false;
                    let n = el;
                    for (let d = 0; d < 28 && n; d++) {
                        const id = String(n.id || '');
                        const nm = String(n.getAttribute('name') || '');
                        const tit = String(n.getAttribute('title') || '');
                        const hay = (id + ' ' + nm + ' ' + tit).toLowerCase();
                        if (hay.includes('precheck') || hay.includes('pre-check') || hay.includes('pre_check')) {
                            return true;
                        }
                        if ((hay.includes('service request') && (hay.includes('list') || hay.includes('applet'))) ||
                            hay.includes('service request list')) {
                            return true;
                        }
                        n = n.parentElement;
                    }
                    n = el;
                    for (let d = 0; d < 12 && n; d++) {
                        const pid = String(n.id || '').toLowerCase();
                        if (pid.includes('gview_s_3') || (pid.includes('s_3_') && pid.includes('_l') && !pid.includes('s_2'))) {
                            return true;
                        }
                        n = n.parentElement;
                    }
                    return false;
                };
                let maxRows = 0;
                const tables = Array.from(document.querySelectorAll('table.ui-jqgrid-btable')).filter(
                    (tb) => vis(tb) && isPrecheckScoped(tb)
                );
                for (const tb of tables) {
                    const rows = Array.from(
                        tb.querySelectorAll('tbody tr.jqgrow, tbody tr[role="row"]')
                    ).filter((tr) => {
                        if (!vis(tr)) return false;
                        const cls = String(tr.className || '').toLowerCase();
                        if (cls.includes('jqgfirstrow') || cls.includes('ui-jqgrid-labels') || cls.includes('jqg-empty')) {
                            return false;
                        }
                        const tds = tr.querySelectorAll('td');
                        if (tds.length < 2) return false;
                        const txt = (tr.textContent || '').trim();
                        return txt.length >= 2;
                    });
                    if (rows.length > maxRows) maxRows = rows.length;
                }
                const href = String(window.location.href || '');
                const hasPrecheckRowId = href.includes('HMCL+PDI+Precheck+List+Applet') && href.includes('SWERowId1=');
                return { maxRows, hasPrecheckRowId };
            }""")
            if isinstance(_probe, dict):
                _rows = int(_probe.get("maxRows") or 0)
                if _rows > _precheck_existing_rows:
                    _precheck_existing_rows = _rows
                    _precheck_existing_signal = f"root[{_ri}]:maxRows={_rows}"
                # Stale ``SWERowId1=`` in the URL is common; **do not** skip Pre-check when scoped jqGrid count is 0 (**LLD** **6.237**).
        except Exception:
            continue
    logger.debug("precheck_existing: rows=%d signal=%s", _precheck_existing_rows, _precheck_existing_signal)

    _precheck_already_present = _precheck_existing_rows > 0
    if _precheck_already_present:
        note(
            f"{log_prefix}: Pre-check already has row(s) "
            f"(rows={_precheck_existing_rows}, signal={_precheck_existing_signal or 'n/a'}) — "
            "skipping Pre-check entry and continuing to PDI."
        )

    def _click_precheck_pick_icon(stage_label: str) -> tuple[bool, str]:
        _used = ""
        _ok = False
        # ``s_3_1_12_0_Ctrl`` is the **Service Request List:New** header button (``siebui-icon-newrecord``), not a LOV pick.
        if "technician" in (stage_label or "").lower():
            _pick_ids = [
                "s_3_2_26_0_icon",
                "s_3_2_24_0_icon",
                "s_3_3_25_0_icon",
                "s_3_3_26_0_icon",
                "s_3_2_25_0_icon",
            ]
        else:
            _pick_ids = [
                "s_3_2_25_0_icon",
                "s_3_2_24_0_icon",
                "s_3_2_26_0_icon",
                "s_3_3_25_0_icon",
                "s_3_3_26_0_icon",
            ]
        for _pc_pick_id in _pick_ids:
            if _siebel_click_by_id_anywhere(
                page,
                _pc_pick_id,
                timeout_ms=_tmo,
                content_frame_selector=content_frame_selector,
                note=note,
                label=f"Pre-check pick icon ({_pc_pick_id}) [{stage_label}]",
                log_prefix=log_prefix,
                wait_ms=1200,
            ):
                _ok = True
                _used = _pc_pick_id
                _branch_hit("_click_precheck_pick_icon", f"id_{_pc_pick_id}_{stage_label}")
                break
        if not _ok:
            _css_fb = (
                "a.siebui-icon-picklist",
                "img.siebui-icon-picklist",
                "[class*='siebui-icon-picklist' i]",
                "a[title*='Pick' i]",
                "img[title*='Pick' i]",
            )
            _is_tech = "technician" in (stage_label or "").lower()
            # Technician column: .first matches the Open column's pick again — prefer 2nd/3rd match.
            _nth_try = (1, 0, 2, 3) if _is_tech else (0,)
            for _css in _css_fb:
                for _root in _roots():
                    try:
                        _grp = _root.locator(_css)
                        _nmax = _grp.count()
                        if _nmax < 1:
                            continue
                        for _ni in _nth_try:
                            if _ni >= _nmax:
                                continue
                            _loc = _grp.nth(_ni)
                            if not _loc.is_visible(timeout=700):
                                continue
                            try:
                                _loc.scroll_into_view_if_needed(timeout=800)
                            except Exception:
                                pass
                            try:
                                _loc.click(timeout=_tmo)
                            except Exception:
                                _loc.click(timeout=_tmo, force=True)
                            _ok, _used = True, f"{_css}@nth={_ni}"
                            _branch_hit("_click_precheck_pick_icon", f"css_{_css[:30]}_{stage_label}")
                            note(
                                f"{log_prefix}: Pre-check pick via CSS {_css!r} nth={_ni} [{stage_label}] "
                                "(fallback after id misses)."
                            )
                            break
                        if _ok:
                            break
                    except Exception:
                        continue
                if _ok:
                    break
        return _ok, _used

    def _pick_first_row_and_ok(stage_label: str) -> bool:
        _safe_page_wait(page, 800, log_label=f"after_{stage_label}_icon_settle")
        _pick_ok = False
        for root in _roots():
            try:
                _pick_result = root.evaluate("""() => {
                    const vis = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (st.display === 'none' || st.visibility === 'hidden') return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    };
                    const inputs = Array.from(document.querySelectorAll('input'));
                    let searchBtn = null;
                    for (const inp of inputs) {
                        const al = (inp.getAttribute('aria-label') || '').toLowerCase();
                        const tt = (inp.getAttribute('title') || '').toLowerCase();
                        if ((al.includes('search') || al.includes('go') || tt.includes('search') || tt.includes('go'))
                            && vis(inp) && inp.type !== 'text') {
                            searchBtn = inp;
                            break;
                        }
                    }
                    if (searchBtn) {
                        searchBtn.click();
                        return 'search_clicked';
                    }
                    return '';
                }""")
                if _pick_result:
                    note(f"{log_prefix}: clicked search in pick applet ({stage_label}, {_pick_result!r}).")
                    _safe_page_wait(page, 1200, log_label=f"after_{stage_label}_search_click")
                    _pick_ok = True
                    break
            except Exception:
                continue

        if not _pick_ok:
            note(f"{log_prefix}: search icon not found in pick applet ({stage_label}; trying Enter fallback).")
            try:
                page.keyboard.press("Enter")
                _safe_page_wait(page, 1200, log_label=f"after_{stage_label}_enter_fallback")
                _pick_ok = True
            except Exception:
                pass

        _safe_page_wait(page, 600, log_label=f"before_{stage_label}_pick_row")
        _row_clicked = False
        for root in _roots():
            try:
                _row_result = root.evaluate("""() => {
                    const vis = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (st.display === 'none' || st.visibility === 'hidden') return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    };
                    const selectors = [
                        'table.ui-jqgrid-btable tbody tr.jqgrow',
                        'table tbody tr.jqgrow',
                        'table tbody tr[role="row"]',
                        'table tbody tr',
                        'table tr',
                    ];
                    let rows = [];
                    for (const sel of selectors) {
                        rows = Array.from(document.querySelectorAll(sel)).filter(vis);
                        if (rows.length) break;
                    }
                    for (const tr of rows) {
                        if (!vis(tr)) continue;
                        const tds = tr.querySelectorAll('td');
                        if (tds.length < 2) continue;
                        const cls = (tr.className || '').toLowerCase();
                        if (cls.includes('jqgfirstrow') || cls.includes('ui-jqgrid-labels') || cls.includes('jqg-empty')) {
                            continue;
                        }
                        if (cls.includes('header')) continue;
                        const txt = (tr.textContent || '').trim();
                        if (!txt || txt.length < 3) continue;
                        const clickable = tr.querySelector('a, input[type="radio"], input[type="checkbox"], td');
                        if (clickable) { clickable.click(); } else { tr.click(); }
                        return 'row_clicked';
                    }
                    return '';
                }""")
                if _row_result:
                    _row_clicked = True
                    note(f"{log_prefix}: picked first row in pick applet ({stage_label}).")
                    _safe_page_wait(page, 600, log_label=f"after_{stage_label}_row_pick")
                    break
            except Exception:
                continue

        if _row_clicked:
            try:
                page.keyboard.press("Enter")
                _safe_page_wait(page, 450, log_label=f"after_{stage_label}_row_enter_commit")
            except Exception:
                pass

        _ok_selectors = (
            "button[aria-label*='OK' i]",
            "a[aria-label*='OK' i]",
            "input[type='button'][value='OK' i]",
            "input[type='submit'][value*='OK' i]",
            "input[type='button'][value*='OK' i]",
            "button:has-text('OK')",
            "a:has-text('OK')",
            "[role='button'][aria-label*='OK' i]",
            "button.siebui-btn-primary:has-text('OK')",
            "input[type='submit'][value='OK' i]",
        )

        def _scan_ok() -> bool:
            for root in _roots():
                for ok_css in _ok_selectors:
                    try:
                        ok_loc = root.locator(ok_css).first
                        if ok_loc.count() > 0 and ok_loc.is_visible(timeout=600):
                            try:
                                ok_loc.click(timeout=_tmo)
                            except Exception:
                                ok_loc.click(timeout=_tmo, force=True)
                            _branch_hit("_pick_first_row_and_ok", f"ok_{ok_css[:30]}_{stage_label}")
                            note(f"{log_prefix}: clicked OK on pick applet ({stage_label}).")
                            _safe_page_wait(page, 1000, log_label=f"after_{stage_label}_ok")
                            return True
                    except Exception:
                        continue
            return False

        _ok_done_local = _scan_ok()
        if not _ok_done_local and _row_clicked:
            try:
                page.keyboard.press("Enter")
                _safe_page_wait(page, 500, log_label=f"after_{stage_label}_second_enter_for_ok")
            except Exception:
                pass
            _ok_done_local = _scan_ok()

        if not _ok_done_local:
            note(f"{log_prefix}: OK button not found on pick applet ({stage_label}; best-effort).")
        if _ok_done_local:
            return True
        if _row_clicked:
            note(
                f"{log_prefix}: pick applet ({stage_label}): row selected; OK control not found — "
                "treating step as complete (Siebel often commits on row/Enter)."
            )
            return True
        return False

    def _precheck_focus_first_precheck_jqgrow() -> None:
        """Click first **jqgrow** row in a Precheck-scoped jqGrid so LOV pick icons are bound to the line item."""
        _jq_js = """() => {
            const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            };
            const isPrecheckScoped = (el) => {
                const isPdiGrid = (node) => {
                    let x = node;
                    for (let d = 0; d < 22 && x; d++) {
                        const pid = String(x.id || '').toLowerCase();
                        if (pid.includes('gview_s_2') || pid.includes('s_2_l')) return true;
                        x = x.parentElement;
                    }
                    return false;
                };
                if (isPdiGrid(el)) return false;
                let n = el;
                for (let d = 0; d < 28 && n; d++) {
                    const id = String(n.id || '');
                    const nm = String(n.getAttribute('name') || '');
                    const tit = String(n.getAttribute('title') || '');
                    const hay = (id + ' ' + nm + ' ' + tit).toLowerCase();
                    if (hay.includes('precheck') || hay.includes('pre-check') || hay.includes('pre_check')) {
                        return true;
                    }
                    if ((hay.includes('service request') && (hay.includes('list') || hay.includes('applet'))) ||
                        hay.includes('service request list')) {
                        return true;
                    }
                    n = n.parentElement;
                }
                n = el;
                for (let d = 0; d < 12 && n; d++) {
                    const pid = String(n.id || '').toLowerCase();
                    if (pid.includes('gview_s_3') || (pid.includes('s_3_') && pid.includes('_l') && !pid.includes('s_2'))) {
                        return true;
                    }
                    n = n.parentElement;
                }
                return false;
            };
            const tables = Array.from(document.querySelectorAll('table.ui-jqgrid-btable')).filter(
                (tb) => vis(tb) && isPrecheckScoped(tb)
            );
            for (const tb of tables) {
                const tr = tb.querySelector('tbody tr.jqgrow');
                if (!tr || !vis(tr)) continue;
                try { tr.scrollIntoView({ block: 'center' }); } catch (e) {}
                const tds = tr.querySelectorAll('td');
                if (tds.length && vis(tds[0])) {
                    tds[0].click();
                    return true;
                }
                tr.click();
                return true;
            }
            return false;
        }"""
        for _root in _roots():
            try:
                if bool(_root.evaluate(_jq_js)):
                    note(f"{log_prefix}: focused Pre-check list first data row (jqgrow) before Open pick.")
                    _safe_page_wait(page, 450, log_label="after_precheck_jqgrow_focus")
                    return
            except Exception:
                continue

    if not _precheck_already_present:
        if not _siebel_click_service_request_list_new_record(
            page,
            roots=_roots,
            action_timeout_ms=action_timeout_ms,
            note=note,
            log_prefix=log_prefix,
            context="Pre-check",
        ):
            return (
                False,
                "Could not click Pre-check list '+' "
                "(tried button#s_3_1_12_0_Ctrl / s_2_2_32_0 siebui-icon-newrecord, Service Request List:New, "
                "Precheck List:New; skipped Service Request List: Menu).",
            )
        note(f"{log_prefix}: clicked Pre-check list New (+).")
        _safe_page_wait(page, 1200, log_label="after_precheck_list_new")

    _precheck_icon_ok = True
    _precheck_icon_used = ""
    if not _precheck_already_present:
        _precheck_focus_first_precheck_jqgrow()
        _precheck_icon_ok, _precheck_icon_used = False, ""
        for _open_try in range(6):
            if _open_try > 0:
                try:
                    page.keyboard.press("Tab")
                    _safe_page_wait(page, 180, log_label=f"precheck_tab_before_open_pick_try_{_open_try}")
                except Exception:
                    pass
            _precheck_icon_ok, _precheck_icon_used = _click_precheck_pick_icon("precheck_open_status")
            if _precheck_icon_ok:
                if _open_try > 0:
                    note(f"{log_prefix}: Open pick icon succeeded after {_open_try} extra Tab(s) (focus on Open column).")
                break
    if not _precheck_icon_ok:
        return False, (
            "Could not click Pre-check pick icon for Open status "
            "(tried icon ids + CSS; jqgrow focus + up to 6 Tab steps; s_3_1_12_0_Ctrl is header + only, not LOV)."
        )

    if not _precheck_already_present:
        _open_pick_complete = _pick_first_row_and_ok("precheck_open_status")
        if not _open_pick_complete:
            return (
                False,
                "Pre-check: Open status pick applet did not complete (row/OK not confirmed). "
                "Technician step was not run; Pre-check Submit and PDI were skipped.",
            )

        def _precheck_try_submit() -> bool:
            _done = False
            for root in _roots():
                for sub_css in (
                    "button:has-text('Submit')",
                    "a:has-text('Submit')",
                    "input[type='button'][value='Submit' i]",
                    "button[aria-label*='Submit' i]",
                    "a[aria-label*='Submit' i]",
                    "button[title*='Submit' i]",
                    "a[title*='Submit' i]",
                ):
                    try:
                        sub_loc = root.locator(sub_css).first
                        if sub_loc.count() > 0 and sub_loc.is_visible(timeout=700):
                            try:
                                sub_loc.click(timeout=_tmo)
                            except Exception:
                                sub_loc.click(timeout=_tmo, force=True)
                            _done = True
                            note(f"{log_prefix}: clicked Submit (Pre-check).")
                            _safe_page_wait(page, 1500, log_label="after_precheck_submit")
                            break
                    except Exception:
                        continue
                if _done:
                    break
            if not _done:
                try:
                    page.keyboard.press("Control+s")
                    _safe_page_wait(page, 1200, log_label="after_precheck_ctrl_s_save")
                    _done = True
                    note(f"{log_prefix}: Pre-check record save via Ctrl+S (no Submit control matched).")
                except Exception:
                    pass
            return _done

        # Many tenants: pick **Open** (and operator) in one LOV → **Submit** here. Extra **Tab**s before Technician
        # break focus and never reach Submit — try save first, then optional Technician only if needed.
        _submit_done = _precheck_try_submit()
        _err_after_submit = _detect_siebel_error_popup(page, content_frame_selector)
        if _submit_done and not _err_after_submit:
            note(f"{log_prefix}: Pre-check saved after Open LOV (Submit/Ctrl+S; Technician step skipped).")
        else:
            if _submit_done and _err_after_submit:
                note(
                    f"{log_prefix}: Pre-check Submit after Open returned validation/error — "
                    "trying Technician LOV if pick icon is available."
                )
            elif not _submit_done:
                note(
                    f"{log_prefix}: Pre-check Submit not completed after Open — "
                    "trying Technician pick (0 Tab first, then Tab + pick)."
                )

            _tech_icon_ok = False
            _tech_icon_used = ""
            for _ti in range(5):
                if _ti > 0:
                    try:
                        page.keyboard.press("Tab")
                        _safe_page_wait(page, 220, log_label=f"precheck_tab_toward_technician_{_ti}")
                    except Exception:
                        pass
                _tech_icon_ok, _tech_icon_used = _click_precheck_pick_icon("precheck_technician_after_tab")
                if _tech_icon_ok:
                    note(
                        f"{log_prefix}: Technician pick icon clicked after {_ti} extra Tab(s) "
                        f"(id={_tech_icon_used!r})."
                    )
                    break
            if _tech_icon_ok:
                _tech_row_ok, _tech_ok_done = _siebel_lov_pick_first_row_ok_pdi_style(
                    page,
                    roots=_roots,
                    action_timeout_ms=action_timeout_ms,
                    note=note,
                    log_prefix=log_prefix,
                    stage_label="Pre-check Technician",
                )
                if not (_tech_row_ok and _tech_ok_done):
                    note(
                        f"{log_prefix}: Technician LOV did not fully complete (row={_tech_row_ok}, ok={_tech_ok_done}) — "
                        "attempting Submit anyway."
                    )
            else:
                note(
                    f"{log_prefix}: Technician pick icon not found — attempting Submit anyway "
                    "(Open-only / operator-in-Open workflows)."
                )

            if not (_submit_done and not _err_after_submit):
                _submit_done = _precheck_try_submit()
            if not _submit_done:
                return False, "Could not click Submit on Pre-check or save with Ctrl+S."

        _submit_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _submit_err:
            note(f"{log_prefix}: Siebel error after Pre-check Submit → {_submit_err!r:.300}")
            return False, f"Siebel error after Pre-check Submit: {_submit_err[:200]}"
        note(f"{log_prefix}: Pre-check completed.")

    _pdi_tab_clicked = _click_third_level_view_bar_tab(
        page, "PDI", wait_ms=1500,
        content_frame_selector=content_frame_selector, note=note, log_prefix=log_prefix,
    )
    for root in _roots():
        if _pdi_tab_clicked:
            break
        for _pdi_css in (
            "a:has-text('PDI')",
            "li:has-text('PDI') a",
            "span:has-text('PDI')",
            "[role='tab']:has-text('PDI')",
            "button:has-text('PDI')",
        ):
            try:
                loc = root.locator(_pdi_css).first
                if loc.count() > 0 and loc.is_visible(timeout=700):
                    try:
                        loc.click(timeout=_tmo)
                    except Exception:
                        loc.click(timeout=_tmo, force=True)
                    _pdi_tab_clicked = True
                    break
            except Exception:
                continue
        if _pdi_tab_clicked:
            break
    if not _pdi_tab_clicked:
        for root in _roots():
            try:
                hit = root.evaluate("""() => {
                    const vis = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (st.display === 'none' || st.visibility === 'hidden') return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    };
                    const all = Array.from(document.querySelectorAll('a, li, span, button, [role="tab"]'));
                    for (const el of all) {
                        if ((el.innerText || '').trim() === 'PDI' && vis(el)) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if hit:
                    _pdi_tab_clicked = True
                    break
            except Exception:
                continue
    if not _pdi_tab_clicked:
        return False, "Could not click PDI tab."
    note(f"{log_prefix}: clicked PDI tab.")
    _safe_page_wait(page, 200, log_label="after_pdi_tab_click_refresh")
    _safe_page_wait(page, 1500, log_label="after_pdi_tab")
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    _siebel_note_frame_focus_snapshot(
        page,
        note,
        "precheck_pdi_after_pdi_tab_networkidle",
        log_prefix=log_prefix,
        content_frame_selector=content_frame_selector,
    )

    _pdi_expiry_aria_js = """() => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width >= 0 && r.height >= 0;
        };
        const raw = [];
        const seen = new Set();
        const push = (el) => {
            if (!el || !vis(el)) return;
            const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!t || seen.has(t)) return;
            seen.add(t);
            raw.push(t.slice(0, 96));
        };
        const sel = [
            '[aria-labelledby*="HMCL_PDI_Expiry_Date"]',
            '[aria-labelledby*="PDI_Expiry_Date"]',
            '[aria-labelledby*="s_2_l_altDateTime"]',
            '[id*="s_2_l_altDateTime"]',
            '[id*="HMCL_PDI_Expiry"]',
        ].join(', ');
        document.querySelectorAll(sel).forEach((el) => {
            const al = el.getAttribute('aria-labelledby') || '';
            const id = el.getAttribute('id') || '';
            if (
                al.includes('PDI_Expiry') || al.includes('altDateTime') ||
                id.includes('altDateTime') || id.includes('HMCL_PDI_Expiry')
            ) {
                push(el);
            }
        });
        return { expiryRaw: raw, source: 'aria-labelledby' };
    }"""
    _pdi_expiry_raw_aria: list[str] = []
    _pdi_aria_best = -1
    for _proot in _roots():
        try:
            _ar = _proot.evaluate(_pdi_expiry_aria_js)
            if not isinstance(_ar, dict):
                continue
            _er = list(_ar.get("expiryRaw") or [])
            if len(_er) > _pdi_aria_best:
                _pdi_aria_best = len(_er)
                _pdi_expiry_raw_aria = _er
        except Exception:
            continue

    _pdi_js = """() => {
        const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        const headerIsPdiExpiry = (txt) => {
            const c = norm(txt);
            return (c.includes('pdi') && c.includes('expir')) || c === 'pdi expiry date' || c.includes('pdi expiry');
        };
        const isPdiListScoped = (el) => {
            let n = el;
            for (let d = 0; d < 28 && n; d++) {
                const id = String(n.id || '');
                const nm = String(n.getAttribute('name') || '');
                const tit = String(n.getAttribute('title') || '');
                const hay = (id + ' ' + nm + ' ' + tit).toLowerCase();
                if (hay.includes('precheck') || hay.includes('pre-check') || hay.includes('pre_check')) {
                    return false;
                }
                n = n.parentElement;
            }
            n = el;
            for (let d = 0; d < 28 && n; d++) {
                const id = String(n.id || '').toLowerCase();
                const nm = String(n.getAttribute('name') || '').toLowerCase();
                const tit = String(n.getAttribute('title') || '').toLowerCase();
                const hay = id + ' ' + nm + ' ' + tit;
                if (id.includes('s_2_l') || id.includes('gview_s_2') || hay.includes('hmcl+pdi')) {
                    return true;
                }
                if (hay.includes('pdi') && (hay.includes('list') || hay.includes('applet') || hay.includes('service'))) {
                    return true;
                }
                n = n.parentElement;
            }
            return false;
        };
        let tables = Array.from(document.querySelectorAll('table.ui-jqgrid-btable')).filter((tb) => vis(tb) && isPdiListScoped(tb));
        if (tables.length === 0) {
            tables = Array.from(document.querySelectorAll('table')).filter((tb) => vis(tb) && isPdiListScoped(tb));
        }
        let best = { rowCount: 0, headerMatched: false, colIdx: -1, expiryRaw: [] };
        for (const tb of tables) {
            const rows = Array.from(tb.querySelectorAll('tr')).filter(vis);
            if (rows.length < 1) continue;
            let colIdx = -1;
            for (let ri = 0; ri < Math.min(4, rows.length); ri++) {
                const cells = rows[ri].querySelectorAll('th, td');
                for (let ci = 0; ci < cells.length; ci++) {
                    if (headerIsPdiExpiry(cells[ci].textContent || '')) {
                        colIdx = ci;
                        break;
                    }
                }
                if (colIdx >= 0) break;
            }
            const expiryRaw = [];
            let dataRows = 0;
            for (const tr of rows) {
                const cls = String(tr.className || '').toLowerCase();
                if (cls.includes('jqgfirstrow')) continue;
                const tds = tr.querySelectorAll('td');
                if (tds.length < 2) continue;
                const rowTxt = (tr.textContent || '').trim();
                if (rowTxt.length < 2) continue;
                const ths = tr.querySelectorAll('th');
                if (ths.length > 0 && tds.length === 0) continue;
                dataRows++;
                if (colIdx >= 0 && colIdx < tds.length) {
                    const cellVal = (tds[colIdx].innerText || tds[colIdx].textContent || '').trim();
                    if (cellVal) expiryRaw.push(cellVal.slice(0, 48));
                }
            }
            if (dataRows > best.rowCount || (colIdx >= 0 && !best.headerMatched)) {
                best = {
                    rowCount: Math.max(best.rowCount, dataRows),
                    headerMatched: colIdx >= 0,
                    colIdx,
                    expiryRaw: colIdx >= 0 ? expiryRaw.slice(0, 12) : [],
                };
            }
        }
        return best;
    }"""
    _pdi_row_count = 0
    _pdi_header_matched = False
    _pdi_table_expiry_raw: list[str] = []
    _pdi_best_score = -1
    for _pri, _proot in enumerate(_roots()):
        try:
            _pr = _proot.evaluate(_pdi_js)
            if not isinstance(_pr, dict):
                continue
            _rc = int(_pr.get("rowCount") or 0)
            _hm = bool(_pr.get("headerMatched"))
            _er = list(_pr.get("expiryRaw") or [])
            _sc = _rc + (10_000 if _hm else 0)
            if _sc > _pdi_best_score:
                _pdi_best_score = _sc
                _pdi_row_count = _rc
                _pdi_header_matched = _hm
                _pdi_table_expiry_raw = _er
        except Exception:
            continue

    _pdi_expiry_seen: set[str] = set()
    _pdi_expiry_raw: list[str] = []
    for _x in list(_pdi_expiry_raw_aria) + list(_pdi_table_expiry_raw):
        _k = str(_x or "").strip()
        if not _k or _k in _pdi_expiry_seen:
            continue
        _pdi_expiry_seen.add(_k)
        _pdi_expiry_raw.append(_k)
    if _pdi_expiry_raw_aria or _pdi_table_expiry_raw:
        _pdi_header_matched = bool(_pdi_header_matched or _pdi_expiry_raw_aria)

    _pdi_datetimes: list[datetime] = []
    _pdi_dates_only: list[date] = []
    for _raw in _pdi_expiry_raw:
        _dt = _siebel_parse_pdi_expiry_cell_to_datetime(_raw)
        if _dt is not None:
            _pdi_datetimes.append(_dt)
            continue
        _d = _siebel_parse_grid_date_cell_to_date(_raw)
        if _d is not None:
            _pdi_dates_only.append(_d)
    _pdi_expiry_dates_combined: list[date] = list(_pdi_dates_only)
    for _dt in _pdi_datetimes:
        _pdi_expiry_dates_combined.append(_dt.date())
    _today = _siebel_ist_today()
    _pdi_buffer = timedelta(minutes=15)
    _pdi_valid, _pdi_best_d, _pdi_best_dt = _siebel_pdi_expiry_still_valid(
        expiry_dates=_pdi_expiry_dates_combined,
        expiry_datetimes=_pdi_datetimes,
        buffer=_pdi_buffer,
    )
    _pdi_max_expiry: date | None = _pdi_best_d
    _parsed_any = bool(_pdi_datetimes or _pdi_dates_only)

    if _pdi_row_count == 0:
        _pdi_need_new_row = True
    elif not _pdi_expiry_raw:
        _pdi_need_new_row = True
    elif _parsed_any:
        _pdi_need_new_row = not _pdi_valid
    else:
        _pdi_need_new_row = True

    if _pdi_row_count > 0 and _pdi_expiry_raw and not _parsed_any:
        note(
            f"{log_prefix}: PDI list has row(s) (count≈{_pdi_row_count}) but PDI Expiry text did not parse "
            f"(samples={_pdi_expiry_raw[:3]!r}) — will add a new PDI row."
        )

    if _pdi_row_count > 0 and not _pdi_need_new_row:
        _exp_note = ""
        if _pdi_best_dt is not None:
            _exp_note = f"latest PDI Expiry (datetime)={_pdi_best_dt.isoformat(timespec='seconds')}"
        elif _pdi_max_expiry is not None:
            _exp_note = f"latest PDI Expiry (date)={_pdi_max_expiry.isoformat()}"
        note(
            f"{log_prefix}: PDI list has row(s) with valid expiry ({_exp_note}, "
            f"grace={_pdi_buffer.total_seconds() / 60:.0f}m vs now IST, today={_today.isoformat()}) — "
            "skipping Service Request New / pick / Submit."
        )
    elif _pdi_need_new_row and _pdi_row_count > 0 and _parsed_any and not _pdi_valid:
        note(
            f"{log_prefix}: PDI Expiry not valid vs now (grace={_pdi_buffer.total_seconds() / 60:.0f}m) — "
            "adding a new PDI row."
        )
    elif _pdi_row_count == 0:
        note(f"{log_prefix}: PDI list has no data rows — adding new PDI row.")

    logger.debug(
        "pdi_existing: rows=%d header=%s need_new=%s expiry=%s",
        _pdi_row_count, _pdi_header_matched, _pdi_need_new_row,
        _pdi_max_expiry.isoformat() if _pdi_max_expiry else "",
    )

    def _eval_pdi_grid_rowcount() -> int:
        """Same scoring as expiry scan: best table's ``rowCount`` (header match wins ties)."""
        _best_sc = -1
        _rc = 0
        for _proot in _roots():
            try:
                _pr = _proot.evaluate(_pdi_js)
                if not isinstance(_pr, dict):
                    continue
                _c = int(_pr.get("rowCount") or 0)
                _hm = bool(_pr.get("headerMatched"))
                _sc = _c + (10_000 if _hm else 0)
                if _sc > _best_sc:
                    _best_sc = _sc
                    _rc = _c
            except Exception:
                continue
        return _rc

    if _pdi_need_new_row:
        _pdi_rows_before_new = _eval_pdi_grid_rowcount()
        note(
            f"{log_prefix}: PDI new-row flow — Service Request list rowCount≈{_pdi_rows_before_new} "
            "(before New)."
        )
        _safe_page_wait(page, 1200, log_label="before_pdi_service_request_list_new")
        if not _siebel_click_service_request_list_new_record(
            page,
            roots=_roots,
            action_timeout_ms=action_timeout_ms,
            note=note,
            log_prefix=log_prefix,
            context="PDI",
        ):
            return (
                False,
                "Could not click 'Service Request List:New' on PDI tab "
                "(same paths as Pre-check +; see _siebel_click_service_request_list_new_record).",
            )
        note(f"{log_prefix}: clicked Service Request List:New on PDI tab.")
        _safe_page_wait(page, 1200, log_label="after_sr_list_new")

        # The "+" is often only title/aria "Service Request List:New" (clicked above). Some builds add a
        # separate Siebel pick id — try it, but do not fail the flow if absent (tenant has no s_2_2_32_0_icon).
        _pdi_legacy_pick = _siebel_click_by_id_anywhere(
            page,
            "s_2_2_32_0_icon",
            timeout_ms=_tmo,
            content_frame_selector=content_frame_selector,
            note=note,
            label="PDI pick icon (legacy s_2_2_32_0_icon)",
            log_prefix=log_prefix,
            wait_ms=1200,
        )
        if not _pdi_legacy_pick:
            _pdi_legacy_pick = _siebel_click_by_id_anywhere(
                page,
                "s_2_2_32_0",
                timeout_ms=_tmo,
                content_frame_selector=content_frame_selector,
                note=note,
                label="PDI pick button (legacy s_2_2_32_0)",
                log_prefix=log_prefix,
                wait_ms=1200,
            )
        if not _pdi_legacy_pick:
            note(
                f"{log_prefix}: PDI legacy pick ids (s_2_2_32_0_icon / s_2_2_32_0) not found — "
                "continuing after Service Request List:New only."
            )

        _siebel_lov_pick_first_row_ok_pdi_style(
            page,
            roots=_roots,
            action_timeout_ms=action_timeout_ms,
            note=note,
            log_prefix=log_prefix,
            stage_label="PDI",
        )

        _pdi_submit_done = False
        for root in _roots():
            for sub_css in (
                "button:has-text('Submit')",
                "a:has-text('Submit')",
                "input[type='button'][value='Submit' i]",
                "button[aria-label*='Submit' i]",
                "a[aria-label*='Submit' i]",
                "button[title*='Submit' i]",
                "a[title*='Submit' i]",
            ):
                try:
                    sub_loc = root.locator(sub_css).first
                    if sub_loc.count() > 0 and sub_loc.is_visible(timeout=700):
                        try:
                            sub_loc.click(timeout=_tmo)
                        except Exception:
                            sub_loc.click(timeout=_tmo, force=True)
                        _pdi_submit_done = True
                        note(f"{log_prefix}: clicked Submit on PDI form.")
                        _safe_page_wait(page, 1500, log_label="after_pdi_submit")
                        break
                except Exception:
                    continue
            if _pdi_submit_done:
                break
        if not _pdi_submit_done:
            return False, "Could not click Submit button on PDI form."

        _pdi_submit_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _pdi_submit_err:
            note(f"{log_prefix}: Siebel error after PDI Submit → {_pdi_submit_err!r:.300}")
            return False, f"Siebel error after PDI Submit: {_pdi_submit_err[:200]}"

        _safe_page_wait(page, 2000, log_label="pdi_post_submit_row_verify")
        _pdi_submit_err_late = _detect_siebel_error_popup(page, content_frame_selector)
        if _pdi_submit_err_late:
            note(f"{log_prefix}: Siebel error after PDI Submit (delayed) → {_pdi_submit_err_late!r:.300}")
            return False, f"Siebel error after PDI Submit: {_pdi_submit_err_late[:200]}"

        _pdi_rows_after_submit = _eval_pdi_grid_rowcount()
        if _pdi_rows_after_submit <= _pdi_rows_before_new:
            _safe_page_wait(page, 2000, log_label="pdi_rowcount_recheck")
            _pdi_rows_after_submit = _eval_pdi_grid_rowcount()
        if _pdi_rows_after_submit <= _pdi_rows_before_new:
            return (
                False,
                "PDI Submit did not increase the Service Request list row count "
                f"(before={_pdi_rows_before_new}, after={_pdi_rows_after_submit}).",
            )
        note(
            f"{log_prefix}: PDI list row count increased after Submit "
            f"({_pdi_rows_before_new} → {_pdi_rows_after_submit})."
        )

    note(f"{log_prefix}: PDI completed successfully.")
    if callable(form_trace):
        form_trace(
            "vehicle_serial_precheck_pdi",
            "Vehicle serial detail",
            "pdi_submit_done" if _pdi_need_new_row else "pdi_valid_existing_skipped_new_row",
            log_prefix=log_prefix,
        )
    _siebel_note_frame_focus_snapshot(
        page,
        note,
        "precheck_pdi_flow_completed",
        log_prefix=log_prefix,
        content_frame_selector=content_frame_selector,
    )
    return True, None


def _grid_cells_suggest_in_transit(texts: list[str]) -> bool:
    blob = " ".join(texts).lower()
    if re.search(r"\bin[\s-]*transit\b", blob):
        return True
    if "in transit" in blob or "in-transit" in blob:
        return True
    return False


def _looks_like_ex_showroom_price(s: str) -> bool:
    """True when ``s`` looks like a numeric ex-showroom / order value (not a model colour name)."""
    t = (s or "").strip()
    if not t or len(t) > 24:
        return False
    letters = sum(1 for c in t if c.isalpha())
    digits = sum(1 for c in t if c.isdigit())
    if digits == 0:
        return False
    # Mostly alphabetic phrase (e.g. variant / colour description)
    if letters >= 8 and letters > digits * 2:
        return False
    compact = re.sub(r"[\s,₹RsINRinr]", "", t)
    if re.fullmatch(r"\d+\.?\d*", compact):
        return True
    if digits >= 4 and letters <= max(2, digits // 3):
        return True
    return False


def _best_chassis_str(*candidates: str | None) -> str:
    """Prefer full VIN-style token over short DMS partials."""
    seen: list[str] = []
    for c in candidates:
        s = (c or "").strip()
        if s and s not in seen:
            seen.append(s)
    if not seen:
        return ""

    def score(s: str) -> tuple[int, int]:
        al = re.sub(r"[^A-Za-z0-9]", "", s)
        lg = len(al)
        if 11 <= lg <= 17:
            return (4, lg)
        if lg >= 10:
            return (3, lg)
        if lg >= 6:
            return (2, lg)
        return (1, lg)

    return max(seen, key=score)


def _best_engine_str(*candidates: str | None) -> str:
    """Prefer full engine no. (letters + digits, longer) over short numeric partials."""
    seen: list[str] = []
    for c in candidates:
        s = (c or "").strip()
        if s and s not in seen:
            seen.append(s)
    if not seen:
        return ""

    def score(s: str) -> tuple[int, int]:
        al = re.sub(r"[^A-Za-z0-9]", "", s)
        lg = len(al)
        has_a = any(c.isalpha() for c in al)
        has_d = any(c.isdigit() for c in al)
        if has_a and has_d and lg >= 12:
            return (5, lg)
        if has_a and has_d and lg >= 8:
            return (4, lg)
        if lg >= 10:
            return (3, lg)
        if has_a and has_d:
            return (2, lg)
        return (1, lg)

    return max(seen, key=score)


def _strip_invalid_grid_small_int_fields(d: dict, *, seating_key: str, cyl_key: str) -> None:
    """Remove obviously wrong grid values (e.g. colour / registration in wrong column)."""
    for key, mx in ((seating_key, 30), (cyl_key, 16)):
        v = str(d.get(key) or "").strip()
        if not v:
            continue
        if not re.fullmatch(r"\d{1,2}", v):
            d.pop(key, None)
            continue
        n = int(v)
        if n < 0 or n > mx:
            d.pop(key, None)


def _apply_two_wheeler_seating_cylinders_body(out: dict) -> None:
    """Match ``fill_hero_dms_service`` / BRD: motorcycle & scooter → seating 2, cylinders 1, body Open."""
    vt = (out.get("vehicle_type") or "").strip().upper().replace(" ", "")
    if "MOTORCYCLE" not in vt and "SCOOTER" not in vt:
        return
    out["seating_capacity"] = "2"
    out["num_cylinders"] = "1"
    if not (out.get("body_type") or "").strip():
        out["body_type"] = "Open"


def _try_click_process_receipt(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"process\s+receipt", re.I),
            re.compile(r"^\s*receive\s*$", re.I),
            re.compile(r"receive\s+vehicle", re.I),
            re.compile(r"grn", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Process Receipt",
    )


def _try_click_price_all(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"price\s*all", re.I),
            re.compile(r"priceall", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Price All",
    )


def _try_click_allocate_line(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    if _try_click_toolbar_by_name(
        page,
        (re.compile(r"allocate\s+all", re.I),),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Allocate All",
    ):
        return True
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"^\s*allocate\s*$", re.I),
            re.compile(r"allocate\s+line", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Allocate",
    )


def _try_fill_mobile_on_enquiry_form(
    page: Page,
    mobile: str,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
) -> bool:
    """Customer Information mobile (2nd match when Find also has Mobile Phone)."""
    from app.services.hero_dms_playwright_customer import (
        _mobile_selectors,
        _siebel_blur_and_settle,
        _try_fill_mobile_dom_scan,
        _try_fill_mobile_semantic,
    )

    if not (mobile or "").strip():
        return False
    if _try_fill_field(
        page,
        _mobile_selectors(mobile_aria_hints),
        mobile,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        prefer_second_if_duplicate=True,
        visible_timeout_ms=2400,
    ):
        _siebel_blur_and_settle(page, ms=350)
        return True
    if _try_fill_mobile_semantic(
        page,
        mobile,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        extra_hints=mobile_aria_hints,
        prefer_second_match=True,
        label_visible_ms=2400,
    ):
        _siebel_blur_and_settle(page, ms=350)
        return True
    if _try_fill_mobile_dom_scan(page, mobile):
        _siebel_blur_and_settle(page, ms=350)
        return True
    return False


def _js_best_grid_row(frame: Frame) -> dict | None:
    """Return {texts: [...], len: n} for the widest plausible data row in the frame."""
    try:
        return frame.evaluate(
            """() => {
            const tables = Array.from(document.querySelectorAll('table'));
            let best = null;
            for (const t of tables) {
              const tbody = t.querySelector('tbody');
              if (!tbody) continue;
              const rows = tbody.querySelectorAll('tr');
              for (const tr of rows) {
                const cells = tr.querySelectorAll('td');
                if (cells.length < 6) continue;
                let nonempty = 0;
                const texts = [];
                for (const c of cells) {
                  const tx = (c.innerText || '').trim();
                  texts.push(tx);
                  if (tx.length > 0) nonempty++;
                }
                if (nonempty < 4) continue;
                if (!best || texts.length > best.len) best = { texts, len: texts.length };
              }
            }
            return best;
        }"""
        )
    except Exception:
        return None


def scrape_siebel_vehicle_row(page: Page, *, content_frame_selector: str | None) -> dict:
    """
    Best-effort scrape of first wide row from Siebel list / grid in any frame.
    Maps 13+ columns from the Siebel vehicle grid when possible.
    """
    _ = content_frame_selector  # reserved if we scope evaluate to frame_locator later
    best: dict | None = None
    best_len = 0
    for frame in _ordered_frames(page):
        row = _js_best_grid_row(frame)
        if row and row.get("len", 0) > best_len:
            best = row
            best_len = row["len"]

    if not best or not best.get("texts"):
        return {}

    texts: list[str] = best["texts"]
    in_tr = _grid_cells_suggest_in_transit(texts)
    if len(texts) >= 13:
        ex_show = texts[11].strip()
        _cc = _normalize_cubic_cc_digits(texts[5].strip()) or texts[5].strip()
        row = {
            "key_num": texts[0].strip(),
            "frame_num": texts[1].strip(),
            "engine_num": texts[2].strip(),
            "model": texts[3].strip(),
            "color": texts[4].strip(),
            "cubic_capacity": _cc,
            "seating_capacity": texts[6].strip(),
            "body_type": texts[7].strip(),
            "vehicle_type": texts[8].strip(),
            "num_cylinders": texts[9].strip(),
            "year_of_mfg": texts[12].strip(),
            "in_transit": in_tr,
        }
        if _looks_like_ex_showroom_price(ex_show):
            row["vehicle_price"] = ex_show
        _strip_invalid_grid_small_int_fields(
            row, seating_key="seating_capacity", cyl_key="num_cylinders"
        )
        return row
    if len(texts) >= 6:
        ex_short = texts[-2].strip() if len(texts) > 2 else ""
        row = {
            "key_num": texts[0].strip(),
            "frame_num": texts[1].strip() if len(texts) > 1 else "",
            "engine_num": texts[2].strip() if len(texts) > 2 else "",
            "model": texts[3].strip() if len(texts) > 3 else "",
            "color": texts[4].strip() if len(texts) > 4 else "",
            "year_of_mfg": texts[-1].strip() if len(texts) > 1 else "",
            "in_transit": in_tr,
        }
        if _looks_like_ex_showroom_price(ex_short):
            row["vehicle_price"] = ex_short
        return row
    return {"in_transit": in_tr} if in_tr else {}


def _siebel_prepare_vehicle_list_find_vin_engine(
    page: Page,
    *,
    frame_p: str,
    engine_p: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    **Auto Vehicle List** / stock search: same **Find → Vehicles** fly-in as Add Enquiry —
    expand Find, choose **Vehicles**, fill **VIN** / **Engine#** in ``#findfieldsbox`` or ``#findfieldbox``
    with ``*`` wildcards, then **Find** / **Enter**.

    Does **not** click a Search Results VIN (``prepare_vehicle`` does that after grid scrape).
    """
    fp = (frame_p or "").strip()
    ep = (engine_p or "").strip()
    if not fp or not ep:
        return False

    if _try_expand_find_flyin(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("prepare_vehicle: expanded Find fly-in (if collapsed).")

    if _try_prepare_find_vehicles_applet(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("prepare_vehicle: Find → Vehicles for list query.")
    else:
        note(
            "prepare_vehicle: Find → Vehicles not confirmed — still attempting VIN/Engine fill in find field box."
        )
    _safe_page_wait(page, 600, log_label="prepare_vehicle_after_find_vehicles")

    cw = _siebel_vehicle_find_wildcard_value(fp)
    ew = _siebel_vehicle_find_wildcard_value(ep)
    filled = _try_fill_vin_engine_in_vehicles_find_applet(
        page,
        chassis_wildcard=cw,
        engine_wildcard=ew,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    if filled:
        note("prepare_vehicle: VIN + Engine# submitted in Find→Vehicles applet.")
        return True

    note("prepare_vehicle: Find→Vehicles VIN/Engine fill failed — one retry (expand Find, Vehicles).")
    _try_expand_find_flyin(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    )
    _safe_page_wait(page, 350, log_label="prepare_vehicle_find_retry_expand")
    _try_prepare_find_vehicles_applet(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    )
    _safe_page_wait(page, 500, log_label="prepare_vehicle_find_retry_vehicles")
    filled = _try_fill_vin_engine_in_vehicles_find_applet(
        page,
        chassis_wildcard=cw,
        engine_wildcard=ew,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    if filled:
        note("prepare_vehicle: VIN + Engine# submitted on retry.")
    return bool(filled)


def _wait_for_vehicle_find_applet_ready(
    page: Page,
    *,
    content_frame_selector: str | None,
    wait_ms: int = 4500,
) -> bool:
    """Wait until Vehicle List find applet controls are visible in any candidate root."""
    _js = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const sels = [
        "#findfieldsbox",
        "#findfieldbox",
        "[aria-label='Find ComboBox']",
        "[title='Find ComboBox']",
        "input[aria-label*='VIN' i]",
        "input[aria-label*='Engine' i]",
      ];
      for (const s of sels) {
        const el = document.querySelector(s);
        if (vis(el)) return true;
      }
      return false;
    }"""
    start_t = time.monotonic()
    deadline = start_t + max(0.2, wait_ms / 1000.0)
    poll_count = 0
    while time.monotonic() < deadline:
        poll_count += 1
        for root in _siebel_locator_search_roots(page, content_frame_selector):
            try:
                if bool(root.evaluate(_js)):
                    return True
            except Exception:
                continue
        _safe_page_wait(page, 140, log_label="wait_vehicle_find_applet_ready")
    return False


def _siebel_goto_vehicle_list_and_scrape(
    page: Page,
    vehicle_url: str,
    frame_p: str,
    engine_p: str,
    *,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
) -> tuple[dict, str | None]:
    """Navigate to Auto Vehicle List, run **only** Find→Vehicles ``*``VIN + ``*``Engine partial query, scrape row."""
    _goto(page, vehicle_url, "vehicle_list", nav_timeout_ms=nav_timeout_ms)
    _safe_page_wait(page, 1500, log_label="vehicle_list_open")
    first_ready = _wait_for_vehicle_find_applet_ready(
        page,
        content_frame_selector=content_frame_selector,
        wait_ms=4500,
    )

    fp = (frame_p or "").strip()
    ep = (engine_p or "").strip()
    if not fp or not ep:
        return {}, (
            "Siebel: Auto Vehicle List requires non-empty **frame_partial** (VIN/chassis) and "
            "**engine_partial**; Find→Vehicles uses *-prefixed partials only (no key/grid search fallback)."
        )

    query_ok = _siebel_prepare_vehicle_list_find_vin_engine(
        page,
        frame_p=fp,
        engine_p=ep,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    )
    if callable(form_trace):
        form_trace(
            "5_vehicle_list",
            "Auto Vehicle List — search/query row",
            "find_vehicles_vin_engine_applet_only",
            frame_partial=frame_p,
            engine_partial=engine_p,
            find_vehicles_vin_engine_ok=query_ok,
        )
    if not query_ok:
        # One extra settle+retry here for runs where browser/app shell is still rendering.
        if _wait_for_vehicle_find_applet_ready(
            page,
            content_frame_selector=content_frame_selector,
            wait_ms=3200,
        ):
            query_ok = _siebel_prepare_vehicle_list_find_vin_engine(
                page,
                frame_p=fp,
                engine_p=ep,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
            )
        if query_ok:
            note("prepare_vehicle: Find→Vehicles query succeeded after applet-ready retry.")
        else:
            final_ready = _wait_for_vehicle_find_applet_ready(
                page, content_frame_selector=content_frame_selector, wait_ms=900
            )
            _hint = "find applet not ready/visible" if not final_ready else "query submit did not complete"
            return {}, (
                "Siebel: Find→Vehicles VIN/Engine query failed even with frame_partial/engine_partial present; "
                f"likely {_hint}. If applet is in a nested iframe, set DMS_SIEBEL_CONTENT_FRAME_SELECTOR."
            )

    try:
        _safe_page_wait(page, 2500, log_label="vehicle_search_settle")
        page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        note("networkidle wait timed out; continuing scrape.")
    except Exception as e:
        if _is_browser_disconnected_error(e):
            raise RuntimeError(
                "Siebel: browser disconnected while waiting for the vehicle grid. "
                "Keep Hero Connect open; see earlier Fill DMS guidance."
            ) from e
        raise

    scraped = scrape_siebel_vehicle_row(page, content_frame_selector=content_frame_selector)
    if scraped.get("key_num") or scraped.get("frame_num") or scraped.get("engine_num"):
        note("Scraped vehicle row from Siebel grid.")
    else:
        note("Vehicle grid scrape returned no key/chassis/engine; check list applet or selectors.")
    return scraped, None


def _siebel_locator_roots_for_vehicle_prep(
    page: Page, content_frame_selector: str | None
) -> list:
    """Frames + page roots to search for vehicle detail links and aria-labelled fields."""
    roots: list = []
    try:
        roots.extend(list(_siebel_locator_search_roots(page, content_frame_selector)))
    except Exception:
        pass
    for fr in _ordered_frames(page):
        roots.append(fr)
    roots.append(page)
    dedup: list = []
    seen: set[int] = set()
    for r in roots:
        k = id(r)
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def _siebel_read_control_value(loc) -> str:
    """Visible input/textarea/select text or value (best-effort)."""
    try:
        if loc.count() == 0 or not loc.is_visible(timeout=400):
            return ""
        tag = (loc.evaluate("el => el.tagName") or "").upper()
        if tag == "SELECT":
            try:
                return (loc.input_value() or "").strip()
            except Exception:
                pass
        if tag in ("INPUT", "TEXTAREA"):
            try:
                return (loc.input_value() or "").strip()
            except Exception:
                return (loc.inner_text(timeout=500) or "").strip()
        return (loc.inner_text(timeout=500) or "").strip()
    except Exception:
        return ""


def _siebel_scrape_vehicle_detail_by_aria_labels(page: Page) -> dict[str, str]:
    """
    On the **vehicle** applet, read standard UIDAI-style fields by exact ``aria-label``:
    VIN → ``full_chassis``, Model, Manufacturing Year → ``year_of_mfg``, SKU → ``variant``,
    Color, Engine Number → ``full_engine``.
    """
    mapping: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("full_chassis", ("VIN",)),
        ("model", ("Model",)),
        ("year_of_mfg", ("Manufacturing Year",)),
        ("variant", ("SKU",)),
        ("color", ("Color",)),
        ("full_engine", ("Engine Number",)),
    )
    found: dict[str, str] = {}
    for key, labels in mapping:
        if found.get(key):
            continue
        for fr in _ordered_frames(page):
            for lbl in labels:
                if found.get(key):
                    break
                for sel in (
                    f'input[aria-label="{lbl}"]',
                    f'textarea[aria-label="{lbl}"]',
                    f'select[aria-label="{lbl}"]',
                ):
                    try:
                        loc = fr.locator(sel).first
                        val = _siebel_read_control_value(loc)
                        if val:
                            found[key] = val
                            break
                    except Exception:
                        continue
    if not found.get("variant"):
        for fr in _ordered_frames(page):
            try:
                loc = fr.get_by_label(re.compile(r"^\s*SKU\s*$", re.I)).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    val = _siebel_read_control_value(loc)
                    if val:
                        found["variant"] = val
                        break
            except Exception:
                continue
    return found


def _siebel_click_by_name_anywhere(
    page: Page,
    name_val: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
    note,
    log_label: str,
    visible_text_fallback: str | None = None,
) -> bool:
    """Click ``a``/``button``/generic element with HTML ``name`` (e.g. Serial Number drilldown).

    Production-observed: drilldown anchors always live under ``#s_1_l`` (jqGrid table).
    Thin JS fallback covers Playwright hit-test edge cases.
    """
    nv_esc = name_val.replace("'", "\\'")

    def _try_click_loc(loc, *, where: str) -> bool:
        try:
            if loc.count() == 0:
                return False
        except Exception:
            return False
        try:
            if not loc.is_visible(timeout=1200):
                return False
        except Exception:
            return False
        try:
            loc.evaluate("(el) => { try { el.scrollIntoView({ block: 'center' }); } catch (e) {} }")
        except Exception:
            pass
        try:
            loc.click(timeout=timeout_ms)
            note(f"prepare_vehicle: clicked {log_label} (name={name_val!r}, {where}).")
            return True
        except Exception:
            try:
                loc.click(timeout=timeout_ms, force=True)
                note(f"prepare_vehicle: clicked {log_label} (name={name_val!r}, force, {where}).")
                return True
            except Exception:
                return False

    roots = list(_siebel_locator_roots_for_vehicle_prep(page, content_frame_selector))

    # Primary: #s_1_l scoped (always wins in production)
    for root in roots:
        for css in (
            f"#s_1_l a[name='{nv_esc}']",
            f"#s_1_l button[name='{nv_esc}']",
            f"#s_1_l [name='{nv_esc}']",
        ):
            try:
                loc = root.locator(css).first
                if _try_click_loc(loc, where="scoped #s_1_l"):
                    _branch_hit("_click_by_name", "grid_scope_#s_1_l")
                    return True
            except Exception:
                continue

    # Thin JS fallback — tries #s_1_l first, then document-wide
    for fr in list(_ordered_frames(page)) + [page.main_frame]:
        try:
            clicked = fr.evaluate(
                """(name) => {
                  const want = String(name);
                  const tryClick = (root) => {
                    const nodes = root.querySelectorAll('[name]');
                    for (const el of nodes) {
                      if ((el.getAttribute('name') || '') !== want) continue;
                      const st = window.getComputedStyle(el);
                      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) continue;
                      const r = el.getBoundingClientRect();
                      if (r.width < 2 && r.height < 2) continue;
                      try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
                      try { el.click(); return true; } catch (e) {}
                    }
                    return false;
                  };
                  const tbl = document.getElementById('s_1_l');
                  if (tbl && tryClick(tbl)) return true;
                  return tryClick(document);
                }""",
                name_val,
            )
            if clicked:
                _branch_hit("_click_by_name", "js_click_frame")
                note(f"prepare_vehicle: clicked {log_label!r} (name={name_val!r}, JS click in frame).")
                return True
        except Exception:
            continue
    return False


def _siebel_vehicle_features_hhml_applet_visible(page: Page) -> bool:
    """
    True when the **Features** step is already showing: HHML value cells visible (typical row ids
    ``4_s_1_l_*`` / ``5_s_1_l_*``), **or** a visible landmark such as ``aria-label`` containing
    **Features in Vehicles**, **or** a visible **Features** list grid ``table[summary="Features"]``.
    Siebel may land on this view after Serial drilldown without HHML ids; use this to skip redundant
    VIN/grid clicks.
    """
    _js = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const cellTxt = (el) => {
        if (!el) return '';
        return String(
          el.value || el.textContent || el.innerText || el.getAttribute('title') || ''
        ).trim();
      };
      for (const id of [
        '4_s_1_l_HHML_Feature_Value', '5_s_1_l_HHML_Feature_Value',
        '4_s_1_l_HHML_Fetaure_Value', '5_s_1_l_HHML_Fetaure_Value'
      ]) {
        const el = document.getElementById(id);
        if (el && (vis(el) || cellTxt(el))) return true;
      }
      const any = document.querySelector('[id*="HHML_Feature_Value"],[id*="HHML_Fetaure_Value"]');
      if (any && (vis(any) || cellTxt(any))) return true;
      const land = document.querySelector('[aria-label*="Features in Vehicles" i]');
      if (land && vis(land)) return true;
      const featGrid = document.querySelector(
        'table[summary="Features"], table[summary*="Features" i]'
      );
      return !!(featGrid && vis(featGrid));
    }"""
    for fr in list(_ordered_frames(page)) + [page.main_frame]:
        try:
            if bool(fr.evaluate(_js)):
                return True
        except Exception:
            continue
    return False


def _siebel_try_click_features_and_image_tab(
    page: Page, *, action_timeout_ms: int, note
) -> bool:
    """Open **Features and Image** (or closest tab label) on vehicle serial drill-in view."""
    hints = ("Features and Image", "Features & Image", "Features")
    patts = [re.compile(re.escape(h), re.I) for h in hints]
    search_roots = list(_ordered_frames(page))
    search_roots.append(page.main_frame)
    for sub, rx in zip(hints, patts):
        for root in search_roots:
            try:
                tab = root.get_by_role("tab", name=rx)
                if tab.count() > 0:
                    t0 = tab.first
                    if t0.is_visible(timeout=500):
                        t0.click(timeout=action_timeout_ms)
                        note(f"prepare_vehicle: clicked tab matching {sub!r}.")
                        return True
            except Exception:
                pass
            try:
                link = root.locator("a, [role='tab'], button").filter(has_text=rx).first
                if link.count() > 0 and link.is_visible(timeout=450):
                    link.click(timeout=action_timeout_ms)
                    note(f"prepare_vehicle: clicked control matching {sub!r}.")
                    return True
            except Exception:
                continue
    note("prepare_vehicle: Features and Image tab not found (best-effort).")
    return False


def _siebel_scrape_features_cubic_and_vehicle_type(page: Page) -> tuple[str, str]:
    """
    On **Features and Image**, read cubic capacity and vehicle type.

    1. **HHML** ids ``4_s_1_l_HHML_Feature_Value`` / ``5_s_1_l_HHML_Feature_Value`` (and ``Fetaure`` typo)
       — reads ``value``, text, and ``title`` (Siebel ``td.edit-cell`` often mirrors the value in ``title``).
    2. **Features grid**: ``table[summary="Features"]`` with columns Feature / Value / … — row **CC Category**
       → cubic text (e.g. ``125 CC``); **Class of Vehicle** → vehicle type.
    """
    cubic = ""
    vtype = ""
    for fr in _ordered_frames(page):
        try:
            data = fr.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 2 && r.height > 2;
                  };
                  const cellText = (el) => {
                    if (!el) return '';
                    return String(
                      el.value || el.textContent || el.innerText || el.getAttribute('title') || ''
                    ).trim();
                  };
                  const read = (id) => {
                    const el = document.getElementById(id);
                    if (!el) return '';
                    return cellText(el);
                  };
                  let cubic = read('4_s_1_l_HHML_Feature_Value') || read('4_s_1_l_HHML_Fetaure_Value');
                  let vtype = read('5_s_1_l_HHML_Feature_Value') || read('5_s_1_l_HHML_Fetaure_Value');
                  const rowHint = (id) => {
                    const m = /^([0-9]+)_/.exec(id || '');
                    return m ? parseInt(m[1], 10) : -1;
                  };
                  if (!cubic || !vtype) {
                    const cand = Array.from(
                      document.querySelectorAll('[id*="HHML_Feature_Value"],[id*="HHML_Fetaure_Value"]')
                    ).filter((el) => vis(el) || cellText(el));
                    const byRow = { 4: [], 5: [] };
                    for (const el of cand) {
                      const id = el.getAttribute('id') || '';
                      const t = String(
                        el.value || el.textContent || el.innerText || el.getAttribute('title') || ''
                      ).replace(/\\s+/g, ' ').trim();
                      if (!t) continue;
                      const rh = rowHint(id);
                      if (rh === 4) byRow[4].push(t);
                      else if (rh === 5) byRow[5].push(t);
                      else if (id.indexOf('_4_') >= 0) byRow[4].push(t);
                      else if (id.indexOf('_5_') >= 0) byRow[5].push(t);
                    }
                    if (!cubic && byRow[4].length) cubic = byRow[4][0];
                    if (!vtype && byRow[5].length) {
                      const pick = byRow[5].find((x) => /[a-zA-Z]{2,}/.test(x)) || byRow[5][0];
                      vtype = pick;
                    }
                  }
                  if (!vtype || /^\\d{4,5}-[A-Z0-9-]+$/i.test(vtype)) {
                    const cand = Array.from(
                      document.querySelectorAll('[id*="HHML_Feature_Value"],[id*="HHML_Fetaure_Value"]')
                    ).filter((el) => vis(el) || cellText(el));
                    for (const el of cand) {
                      const id = el.getAttribute('id') || '';
                      if (rowHint(id) !== 5 && id.indexOf('_5_') < 0) continue;
                      const t = String(
                        el.value || el.textContent || el.innerText || el.getAttribute('title') || ''
                      ).replace(/\\s+/g, ' ').trim();
                      if (t && /[a-zA-Z]{2,}/.test(t) && t.length > vtype.length) vtype = t;
                    }
                  }
                  if (!cubic || !vtype) {
                    const grids = Array.from(
                      document.querySelectorAll('table[summary="Features"], table[summary*="Features" i]')
                    ).filter(vis);
                    const featKey = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const valFrom = (cell) => {
                      const v = cell.value;
                      if (v !== undefined && v !== null && String(v).trim() !== '') return String(v).trim();
                      return String(
                        cell.textContent || cell.innerText || cell.getAttribute('title') || ''
                      ).replace(/\\s+/g, ' ').trim();
                    };
                    for (const tbl of grids) {
                      const tb = tbl.querySelector('tbody') || tbl;
                      for (const tr of tb.querySelectorAll('tr')) {
                        const tds = tr.querySelectorAll('td');
                        if (tds.length < 2) continue;
                        const fk = featKey(tds[0].textContent || tds[0].innerText);
                        if (fk === 'feature' || fk === 'value' || fk === 'description') continue;
                        const val = valFrom(tds[1]);
                        if (!val) continue;
                        if (!cubic && fk.includes('cc category')) {
                          cubic = val;
                          continue;
                        }
                        if (!cubic && fk.includes('cubic') && fk.includes('capac')) {
                          cubic = val;
                          continue;
                        }
                        if (!vtype && fk.includes('class of vehicle')) {
                          vtype = val;
                        }
                      }
                      if (cubic && vtype) break;
                    }
                  }
                  return { cubic, vehicle_type: vtype };
                }"""
            )
            if isinstance(data, dict):
                cubic = str(data.get("cubic") or "").strip()
                vtype = str(data.get("vehicle_type") or "").strip()
                if cubic or vtype:
                    return cubic, vtype
        except Exception:
            continue
    return cubic, vtype


def _prepare_vehicle_merge_detail_from_aria_labels(
    page: Page,
    scraped: dict,
    *,
    note,
    form_trace=None,
) -> None:
    """Merge **Vehicle Information** fields from aria-labelled inputs into ``scraped``; trace form step."""
    detail = _siebel_scrape_vehicle_detail_by_aria_labels(page)
    for k, v in detail.items():
        if v and str(v).strip():
            scraped[k] = str(v).strip()
    if detail:
        note(f"prepare_vehicle: vehicle detail applet (aria-labels) → {list(detail.keys())!r}.")
    if callable(form_trace):
        form_trace(
            "5_vehicle_detail",
            "Auto Vehicle — detail applet",
            "scrape_VIN_Model_Year_Manu_SKU_Color_Engine_by_aria_label",
            full_chassis=str(scraped.get("full_chassis") or ""),
            model=str(scraped.get("model") or ""),
            year_of_mfg=str(scraped.get("year_of_mfg") or ""),
            variant=str(scraped.get("variant") or ""),
            color=str(scraped.get("color") or ""),
            full_engine=str(scraped.get("full_engine") or ""),
        )


def _prepare_vehicle_open_serial_detail_from_vehicle_grid(
    page: Page,
    scraped: dict,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> str | None:
    """
    On **Auto Vehicle** detail, drill in via top jqGrid (``#gview_s_1_l`` / ``#s_1_l``): **VIN** (best-effort)
    then **Serial Number** (required) so Third Level tabs match Siebel's serial-detail view.
    """
    _fb = (
        _best_chassis_str(
            str(scraped.get("full_chassis") or "").strip(),
            str(scraped.get("frame_num") or "").strip(),
        )
        or ""
    ).strip()
    _tmo = min(int(action_timeout_ms or 3000), 5000)
    if _siebel_click_by_name_anywhere(
        page,
        "VIN",
        timeout_ms=_tmo,
        content_frame_selector=content_frame_selector,
        note=note,
        log_label="VIN drilldown (vehicle grid gview_s_1_l)",
        visible_text_fallback=_fb or None,
    ):
        _safe_page_wait(page, 900, log_label="after_prepare_vehicle_vin_drilldown")
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            note("prepare_vehicle: networkidle after VIN drilldown timed out; continuing.")
        except Exception:
            pass
    else:
        note(
            "prepare_vehicle: VIN drilldown (name='VIN') not found on vehicle grid — "
            "continuing to Serial Number."
        )

    if not _siebel_click_by_name_anywhere(
        page,
        "Serial Number",
        timeout_ms=_tmo,
        content_frame_selector=content_frame_selector,
        note=note,
        log_label="Serial Number drilldown (gview_s_1_l)",
        visible_text_fallback=_fb or None,
    ):
        return (
            "Siebel: could not click Serial Number drilldown (name='Serial Number') on the vehicle grid "
            "(expected under #gview_s_1_l / #s_1_l)."
        )
    note("prepare_vehicle: opened vehicle serial detail (Serial Number drilldown).")
    _safe_page_wait(page, 1200, log_label="after_prepare_vehicle_serial_drilldown")
    try:
        page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        note("prepare_vehicle: networkidle after Serial Number drilldown timed out; continuing.")
    except Exception:
        pass
    _siebel_note_frame_focus_snapshot(
        page,
        note,
        "after_serial_number_drill_settled",
        log_prefix="prepare_vehicle",
        content_frame_selector=content_frame_selector,
    )
    return None


def _prepare_vehicle_scrape_serial_precheck_pdi_and_features(
    page: Page,
    scraped: dict,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
) -> str | None:
    """
    On the **vehicle** detail view (dealer stock): click **Serial Number**, run tab **Pre-check** + **PDI**
    (``_siebel_run_vehicle_serial_detail_precheck_pdi``), then **Features and Image** for cubic/type.

    ``prepare_vehicle`` calls this only when ``in_transit`` is false after the inventory gate — not while the
    unit is treated as in-transit (Siebel rejects Pre-check/PDI there).

    Returns ``None`` on success or when **Serial Number** is missing (best-effort skip); on Pre-check/PDI
    failure returns an error string.
    """
    if not _siebel_click_by_name_anywhere(
        page,
        "Serial Number",
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        log_label="Serial Number drilldown",
    ):
        note(
            "prepare_vehicle: Serial Number drilldown (name='Serial Number') not found — "
            "skipping serial-detail Pre-check/PDI and Features scrape."
        )
        return None

    try:
        _safe_page_wait(page, 1200, log_label="after_serial_number_click")
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        note("prepare_vehicle: networkidle after Serial Number timed out; continuing.")
    except Exception:
        pass

    _serial_pc_ok, _serial_pc_err = _siebel_run_vehicle_serial_detail_precheck_pdi(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        form_trace=form_trace,
        log_prefix="prepare_vehicle",
        scraped=scraped,
        do_feature_id_scrape=True,
    )
    if not _serial_pc_ok:
        return _serial_pc_err or "Pre-check / PDI failed after Serial Number drilldown (prepare_vehicle)."

    if not _siebel_try_click_features_and_image_tab(
        page, action_timeout_ms=action_timeout_ms, note=note
    ):
        return None
    _safe_page_wait(page, 1000, log_label="after_features_tab")
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    cc, vt = _siebel_scrape_features_cubic_and_vehicle_type(page)
    if cc:
        scraped["cubic_capacity"] = _normalize_cubic_cc_digits(cc) or str(cc).strip()
    if vt:
        scraped["vehicle_type"] = vt
    _feat_cc = str(scraped.get("cubic_capacity") or "").strip()
    note(f"prepare_vehicle: Features tab → cubic_capacity={_feat_cc!r}, vehicle_type={vt!r}.")
    if callable(form_trace):
        form_trace(
            "5_vehicle_features",
            "Features and Image",
            "scrape_HHML_Feature_Value_cubic_and_vehicle_type",
            cubic_capacity=_feat_cc,
            vehicle_type=str(vt or ""),
        )
    return None


def _merge_dms_and_grid_for_vehicle_master(dms_values: dict, grid: dict) -> dict:
    """
    Merge **Auto Vehicle List** grid scrape with DMS / staging fields into one dict suitable for
    ``update_vehicle_master_from_dms`` (same key names as that function).

    Picks the **best** chassis and engine tokens (full VIN / full engine no.) across grid and DMS;
    drops ``frame_num`` / ``engine_num`` after merge so only ``full_chassis`` / ``full_engine`` remain.
    """
    out: dict = {k: v for k, v in dict(grid).items() if v is not None}

    def _ds(*keys: str) -> str:
        for k in keys:
            v = dms_values.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return ""

    fc = _best_chassis_str(
        (out.get("full_chassis") or "").strip(),
        (out.get("frame_num") or "").strip(),
        _ds("full_chassis", "chassis"),
        _ds("frame_partial"),
    )
    fe = _best_engine_str(
        (out.get("full_engine") or "").strip(),
        (out.get("engine_num") or "").strip(),
        _ds("full_engine", "engine"),
        _ds("engine_partial"),
    )
    out.pop("frame_num", None)
    out.pop("engine_num", None)
    if fc:
        out["full_chassis"] = fc
    else:
        out.pop("full_chassis", None)
    if fe:
        out["full_engine"] = fe
    else:
        out.pop("full_engine", None)

    if not (out.get("model") or "").strip():
        m = _ds("vehicle_model", "model")
        if m:
            out["model"] = m
    if not (out.get("color") or "").strip():
        c = _ds("vehicle_colour", "color", "colour")
        if c:
            out["color"] = c
    if not (out.get("key_num") or "").strip():
        k = _ds("key_partial")
        if k:
            out["raw_key_num"] = k
    if not (out.get("year_of_mfg") or "").strip():
        y = _ds("year_of_mfg", "dispatch_year")
        if y:
            out["year_of_mfg"] = y
    v = (out.get("variant") or "").strip() or _ds("variant", "vehicle_variant")
    if v:
        out["variant"] = v
    vp = (out.get("vehicle_price") or "").strip()
    if vp and not _looks_like_ex_showroom_price(vp):
        out.pop("vehicle_price", None)
    if not (out.get("vehicle_price") or "").strip():
        for k in ("vehicle_ex_showroom_price", "vehicle_ex_showroom_cost", "total_amount"):
            raw = dms_values.get(k)
            if raw is None:
                continue
            s = str(raw).strip()
            if s and _looks_like_ex_showroom_price(s):
                out["vehicle_price"] = s
                break
    _strip_invalid_grid_small_int_fields(
        out, seating_key="seating_capacity", cyl_key="num_cylinders"
    )
    _apply_year_of_mfg_yyyy(out)
    _apply_two_wheeler_seating_cylinders_body(out)
    return out


def _vehicle_master_prepare_gaps(merged: dict) -> tuple[list[str], list[str]]:
    """
    Returns ``(critical_messages, informational_messages)`` for operators and the Playwright DMS execution log (caller-supplied path, typically ``Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt``).

    **Critical** = still empty after merge for fields that normally must exist for a coherent
    ``vehicle_master`` row. **Informational** = optional fields or values filled later in the SOP
    (e.g. cubic capacity on order-line attach).

    ``place_of_registeration`` and ``oem_name`` are **not** gaps here — they are filled at persist from
    ``sales_master`` / ``dealer_ref`` when ``vehicle_id`` is set.
    """
    critical: list[str] = []
    info: list[str] = []

    def _chassis_eff() -> str:
        return (
            (merged.get("full_chassis") or "").strip()
            or (merged.get("frame_num") or "").strip()
            or (merged.get("chassis") or "").strip()
        )

    def _engine_eff() -> str:
        return (
            (merged.get("full_engine") or "").strip()
            or (merged.get("engine_num") or "").strip()
            or (merged.get("engine") or "").strip()
        )

    if not _chassis_eff():
        critical.append(
            "chassis still empty after merge — need grid frame_num or DMS full_chassis/frame_partial "
            "on Auto Vehicle List or in staging."
        )
    if not _engine_eff():
        critical.append(
            "engine still empty after merge — need grid engine_num or DMS full_engine/engine_partial."
        )
    if not (merged.get("model") or "").strip():
        critical.append("model still empty after merge — need grid row or DMS vehicle_model/model.")
    if not (merged.get("color") or merged.get("colour") or "").strip():
        critical.append("colour still empty after merge — need grid or DMS vehicle_colour/color.")
    if not (merged.get("year_of_mfg") or "").strip():
        critical.append("year_of_mfg still empty — need grid last column or DMS year_of_mfg.")

    if not (merged.get("key_num") or merged.get("raw_key_num") or "").strip():
        info.append("key_num: optional; grid and key_partial both empty.")

    if not (merged.get("variant") or "").strip():
        info.append("variant: not on typical Auto Vehicle List grid; set in staging/DMS if required.")

    if not (merged.get("cubic_capacity") or "").strip():
        info.append(
            "cubic_capacity: often absent when the grid row has fewer than 13 cells; "
            "use Serial/Features in prepare_vehicle or DMS/staging."
        )

    return critical, info


def _dms_optional_str_for_key_battery(v: object) -> str:
    """
    Normalize DMS/staging values for **Key Number** / **Battery No.** form fill.

    Returns ``""`` when the value is null, empty, or a placeholder — those fields are **not** updated
    on the Siebel form. Accepts non-string payloads (e.g. numeric key codes) via ``str()`` without
    calling ``.strip()`` on non-strings in the caller.
    """
    if v is None:
        return ""
    s = v.strip() if isinstance(v, str) else str(v).strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("null", "none", "n/a", "na", "-", "nil"):
        return ""
    return s


def _dms_key_battery_strings_from_values(dms_values: dict) -> tuple[str, str]:
    """
    Resolve key and battery strings for the vehicle detail form.

    ``key_partial`` / ``key_num`` (first non-empty) and ``battery_partial`` / ``battery_num`` /
    ``battery`` (first non-empty). Null or placeholder values are treated as missing.
    """
    key_val = _dms_optional_str_for_key_battery(dms_values.get("key_partial"))
    if not key_val:
        key_val = _dms_optional_str_for_key_battery(dms_values.get("key_num"))
    battery_val = _dms_optional_str_for_key_battery(dms_values.get("battery_partial"))
    if not battery_val:
        battery_val = _dms_optional_str_for_key_battery(dms_values.get("battery_num"))
    if not battery_val:
        battery_val = _dms_optional_str_for_key_battery(dms_values.get("battery"))
    return key_val, battery_val


def _siebel_fill_key_battery_from_dms_values(
    page: Page,
    dms_values: dict,
    *,
    action_timeout_ms: int,
    note,
    log_prefix: str = "Vehicle prep",
) -> None:
    """
    Best-effort **Battery No.** then **Key Number** fill on the current vehicle form, then Ctrl+S.

    Expects the **vehicle detail** applet (e.g. after left **Search Results** VIN drill-in). Used from
    Add Enquiry and from ``prepare_vehicle`` after opening that view. Does nothing when both resolved
    values are empty (including null/placeholder ``key_partial`` / ``key_num`` / ``battery_partial`` /
    ``battery_num`` / ``battery``). Battery is filled before Key to match Siebel tab order / operator SOP
    on the vehicle page.
    """
    key_val, battery_val = _dms_key_battery_strings_from_values(dms_values)
    if not (key_val or battery_val):
        return
    _veh_fill_frame = None
    for _vf in _ordered_frames(page):
        try:
            if _vf.locator('input[aria-label="Battery No."]').count() > 0:
                _veh_fill_frame = _vf
                break
            if _vf.locator('input[aria-label="Key Number"]').count() > 0:
                _veh_fill_frame = _vf
                break
        except Exception:
            continue
    if _veh_fill_frame is None:
        _veh_fill_frame = page.main_frame
    if battery_val:
        if _fill_by_label_on_frame(_veh_fill_frame, "Battery No.", battery_val, action_timeout_ms=action_timeout_ms):
            note(f"{log_prefix}: filled Battery No. = {battery_val!r} on vehicle page.")
        else:
            note(f"{log_prefix}: could not fill Battery No. = {battery_val!r} on vehicle page (best-effort).")
    if key_val:
        if _fill_by_label_on_frame(_veh_fill_frame, "Key Number", key_val, action_timeout_ms=action_timeout_ms):
            note(f"{log_prefix}: filled Key Number = {key_val!r} on vehicle page.")
        else:
            note(f"{log_prefix}: could not fill Key Number = {key_val!r} on vehicle page (best-effort).")
    if key_val or battery_val:
        _safe_page_wait(page, 400, log_label="after_vehicle_key_battery_fill")
        try:
            page.keyboard.press("Control+s")
            _safe_page_wait(page, 1200, log_label="after_vehicle_key_battery_save")
            note(f"{log_prefix}: saved vehicle record after Key/Battery fill.")
        except Exception:
            note(f"{log_prefix}: Ctrl+S after Key/Battery fill raised an exception (best-effort).")


_INVENTORY_LOC_IN_TRANSIT_RE = re.compile(r"in\s*transit", re.I)
_INVENTORY_LOC_DEALER_RE = re.compile(r"dealer", re.I)

_ERROR_INVENTORY_IN_TRANSIT_BEFORE_BOOKING = (
    "Vehicle is in transit. Create Receiving before Booking."
)


def _siebel_read_inventory_location_field(page: Page) -> str:
    """Read **Inventory Location** from ``aria-label="Inventory Location"`` (all frames)."""
    for fr in _ordered_frames(page):
        for sel in (
            'input[aria-label="Inventory Location"]',
            'textarea[aria-label="Inventory Location"]',
            'select[aria-label="Inventory Location"]',
        ):
            try:
                loc = fr.locator(sel).first
                val = _siebel_read_control_value(loc)
                if val:
                    return str(val).strip()
            except Exception:
                continue
    return ""


def _prepare_vehicle_inventory_location_in_transit_gate(
    scraped: dict,
    page: Page,
    *,
    note,
    form_trace=None,
    step=None,
) -> str | None:
    """
    Authoritative **vehicle_in_transit** from Inventory Location when the field is readable.

    - Substring **in transit** (spacing-flexible) → return hard error (caller stops Fill DMS run).
    - Substring **dealer** → ``scraped['in_transit']=False`` (unit with dealer).
    - Any other non-empty value → ``in_transit=False`` (overrides list-grid heuristic).
    - Empty field → leave ``scraped['in_transit']`` from grid scrape unchanged.

    Returns error text for abort, or ``None`` to continue.
    """
    raw = _siebel_read_inventory_location_field(page)
    if not raw:
        note(
            "prepare_vehicle: Inventory Location (aria-label) not readable — "
            "in_transit may still come from list grid heuristic."
        )
        return None
    scraped["inventory_location"] = raw
    if _INVENTORY_LOC_IN_TRANSIT_RE.search(raw):
        note(f"DECISION: Inventory Location implies In Transit — {raw!r}.")
        if callable(form_trace):
            form_trace(
                "5_vehicle_inventory_location",
                "Auto Vehicle",
                "inventory_location_in_transit_abort_before_booking",
                inventory_location=raw[:240],
            )
        if callable(step):
            step("Stopped: vehicle is in transit — create receiving before booking.")
        return _ERROR_INVENTORY_IN_TRANSIT_BEFORE_BOOKING
    if _INVENTORY_LOC_DEALER_RE.search(raw):
        scraped["in_transit"] = False
        note(f"DECISION: vehicle with dealer per Inventory Location — {raw!r}; in_transit=False.")
    else:
        scraped["in_transit"] = False
        note(
            f"DECISION: Inventory Location present, not In Transit — {raw!r}; "
            "in_transit=False (overrides grid heuristic)."
        )
    if callable(form_trace):
        form_trace(
            "5_vehicle_inventory_location",
            "Auto Vehicle",
            "inventory_location_scrape",
            inventory_location=raw[:240],
            in_transit=bool(scraped.get("in_transit")),
        )
    return None


def prepare_vehicle(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
    ms_done=None,
    step=None,
) -> tuple[bool, str | None, dict, bool, list[str], list[str]]:
    """
    Pre-booking **vehicle preparation** (runs before Generate Booking): navigate to **Auto Vehicle List**,
    Find→Vehicles query, scrape the list grid row, **require** left **Search Results** VIN (Title) drill-in,
    then **Key Number** / **Battery No.** (save), merge **Vehicle Information** from aria-labels, evaluate
    **Inventory Location** (fail if **in transit**). For dealer stock: top-grid **VIN** (best-effort) →
    **Serial Number** drilldown → **Features and Image** (``cubic_capacity`` / ``vehicle_type``) →
    **Pre-check** + **PDI** (``_siebel_run_vehicle_serial_detail_precheck_pdi``).

    **Inventory Location** (``aria-label`` on the detail view): substring **in transit** → hard fail
    ``Vehicle is in transit. Create Receiving before Booking.``; **dealer** (or other non-empty) →
    ``in_transit=False``; empty → keep list-grid heuristic for downstream branches.

    **Pre-check and PDI** run **only** when the vehicle is treated as **dealer / not in-transit**
    (``in_transit`` false after the gate). For **in-transit** stock, they are **skipped** (Siebel fails them);
    the in-transit branch only opens the receipt URL and **Process Receipt** when configured — no second
    Pre-check/PDI URL flow.

    Before return, merges grid + detail + DMS/staging into a dict aligned with ``update_vehicle_master_from_dms``.
    Returns ``(ok, error, merged_vehicle_dict, in_transit, critical_gaps, informational_notes)``.
    ``place_of_registeration`` / ``oem_name`` are applied at DB persist from ``dealer_ref`` / ``oem_ref``, not scraped here.
    """
    key_p, _ = _dms_key_battery_strings_from_values(dms_values)
    frame_p = (dms_values.get("frame_partial") or "").strip()
    engine_p = (dms_values.get("engine_partial") or "").strip()
    vehicle_url = (urls.vehicle or "").strip()

    if callable(form_trace):
        form_trace(
            "5_vehicle_list",
            "Auto Vehicle List (DMS_REAL_URL_VEHICLE)",
            "begin_vehicle_flow_navigate_then_search_applet",
            vehicle_url_truncated=vehicle_url[:200] if vehicle_url else "",
            key_partial=key_p,
            frame_partial=frame_p,
            engine_partial=engine_p,
        )
    if not vehicle_url:
        if callable(step):
            step("Stopped: DMS_REAL_URL_VEHICLE is not configured.")
        return (
            False,
            (
                "Siebel: set DMS_REAL_URL_VEHICLE to the Auto Vehicle List (or stock search) "
                "GotoView URL so Find→Vehicles (*VIN/*Engine) search can run."
            ),
            {},
            False,
            [],
            [],
        )

    scraped, veh_err = _siebel_goto_vehicle_list_and_scrape(
        page,
        vehicle_url,
        frame_p,
        engine_p,
        nav_timeout_ms=nav_timeout_ms,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        form_trace=form_trace,
    )
    if veh_err:
        if callable(step):
            step("Stopped during vehicle list search.")
        return False, veh_err, {}, False, [], []

    _chassis_for_left_hit = (
        _best_chassis_str(
            (frame_p or "").strip(),
            str(scraped.get("frame_num") or "").strip(),
        )
        or ""
    ).strip()
    if not _chassis_for_left_hit:
        merged = _merge_dms_and_grid_for_vehicle_master(dms_values, scraped)
        vm_crit, vm_info = _vehicle_master_prepare_gaps(merged)
        return (
            False,
            "Siebel: missing chassis/VIN for left Search Results drill-in "
            "(set frame_partial and/or ensure the list grid returns frame_num after Find→Vehicles).",
            merged,
            bool(scraped.get("in_transit")),
            vm_crit,
            vm_info,
        )
    if not _siebel_try_click_vin_search_hit_link(
        page,
        _chassis_for_left_hit,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        merged = _merge_dms_and_grid_for_vehicle_master(dms_values, scraped)
        vm_crit, vm_info = _vehicle_master_prepare_gaps(merged)
        return (
            False,
            "Siebel: could not open vehicle detail from left Search Results (VIN Title drilldown).",
            merged,
            bool(scraped.get("in_transit")),
            vm_crit,
            vm_info,
        )
    note("prepare_vehicle: opened vehicle from left Search Results (VIN Title drilldown).")

    try:
        _safe_page_wait(page, 1200, log_label="after_vehicle_left_pane_vin_settle")
        page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        note("prepare_vehicle: networkidle after VIN drill-in timed out; continuing.")
    except Exception:
        pass

    _siebel_fill_key_battery_from_dms_values(
        page,
        dms_values,
        action_timeout_ms=action_timeout_ms,
        note=note,
        log_prefix="Vehicle prep",
    )

    _prepare_vehicle_merge_detail_from_aria_labels(
        page, scraped, note=note, form_trace=form_trace
    )

    _inv_gate_err = _prepare_vehicle_inventory_location_in_transit_gate(
        scraped,
        page,
        note=note,
        form_trace=form_trace,
        step=step,
    )
    if _inv_gate_err:
        merged = _merge_dms_and_grid_for_vehicle_master(dms_values, scraped)
        vm_crit, vm_info = _vehicle_master_prepare_gaps(merged)
        return False, _inv_gate_err, merged, True, vm_crit, vm_info

    _detail_pc_err: str | None = None
    if not bool(scraped.get("in_transit")):
        _detail_pc_err = _prepare_vehicle_scrape_serial_precheck_pdi_and_features(
            page,
            scraped,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            form_trace=form_trace,
        )
        if _detail_pc_err:
            merged = _merge_dms_and_grid_for_vehicle_master(dms_values, scraped)
            vm_crit, vm_info = _vehicle_master_prepare_gaps(merged)
            if callable(step):
                step("Stopped: vehicle serial drilldown, Features, Pre-check, or PDI failed.")
            return (
                False,
                _detail_pc_err,
                merged,
                bool(scraped.get("in_transit")),
                vm_crit,
                vm_info,
            )
    else:
        note(
            "prepare_vehicle: vehicle flagged in-transit — skipping Serial / Features / Pre-check / PDI "
            "(Siebel rejects Pre-check and PDI until received at dealer)."
        )

    note(
        "Vehicle grid scrape (prepare_vehicle): "
        f"model={scraped.get('model')!r}, color={scraped.get('color')!r}, "
        f"frame_num={scraped.get('frame_num')!r}, engine_num={scraped.get('engine_num')!r}, "
        f"key_num={scraped.get('key_num')!r}."
    )

    in_transit_state = bool(scraped.get("in_transit"))
    _inv_txt = (scraped.get("inventory_location") or "").strip()
    if _inv_txt:
        note(
            f"DECISION: vehicle_in_transit={in_transit_state!r} "
            f"(Inventory Location={_inv_txt!r})."
        )
    else:
        note(f"DECISION: vehicle_in_transit={in_transit_state!r} (list grid heuristic; no Inventory Location).")
    if callable(form_trace):
        form_trace(
            "5_vehicle_list",
            "Auto Vehicle List — results grid (scraped row)",
            "read_first_matching_row_from_grid",
            key_num=str(scraped.get("key_num") or ""),
            frame_num=str(scraped.get("frame_num") or ""),
            engine_num=str(scraped.get("engine_num") or ""),
            model=str(scraped.get("model") or ""),
            in_transit=in_transit_state,
            inventory_location=str(scraped.get("inventory_location") or "")[:200],
        )

    if in_transit_state:
        note(
            "prepare_vehicle: vehicle in-transit — opening receipt view if configured; "
            "Pre-check/PDI skipped (not run until dealer stock)."
        )
        if callable(step):
            step("Vehicle appears in transit — receipt path only (Pre-check/PDI skipped).")
        recv_u = (urls.vehicles or "").strip()
        if recv_u:
            if callable(form_trace):
                form_trace(
                    "5b_in_transit_receipt",
                    "Vehicles / In Transit — receipt view (DMS_REAL_URL_VEHICLES)",
                    "goto_receipt_URL_then_Process_Receipt_toolbar_if_present",
                    receipt_url_truncated=recv_u[:200],
                )
            _goto(page, recv_u, "vehicles_receipt", nav_timeout_ms=nav_timeout_ms)
            _siebel_after_goto_wait(page, floor_ms=1000)
            if _try_click_process_receipt(
                page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
            ):
                note("Clicked Process Receipt / receive control.")
                if callable(step):
                    step("Vehicle received — Process Receipt was completed in DMS.")
            else:
                note("Process Receipt control not found; operator may complete receipt manually.")
                if callable(step):
                    step(
                        "Receipt / in-transit screen opened; Process Receipt was not found — "
                        "complete receiving manually if required."
                    )
            if callable(ms_done):
                ms_done("Vehicle received")
        else:
            note(
                "DMS_REAL_URL_VEHICLES is not set — cannot navigate to receipt/in-transit view; "
                "set it to HMCL In Transit (or equivalent) GotoView URL."
            )
            if callable(step):
                step("Receipt URL (DMS_REAL_URL_VEHICLES) is not set — skipped receiving in UI.")
    else:
        note("prepare_vehicle: vehicle at dealer (not in-transit) — receipt branch skipped.")
        if callable(step):
            step("Vehicle does not appear in transit.")

    merged = _merge_dms_and_grid_for_vehicle_master(dms_values, scraped)
    vm_crit, vm_info = _vehicle_master_prepare_gaps(merged)
    if vm_crit:
        note(
            "vehicle_master: fields still missing after prepare_vehicle merge — "
            + "; ".join(vm_crit)
        )

    _dump_branch_hits(note)
    return True, None, merged, in_transit_state, vm_crit, vm_info


def _add_enquiry_vehicle_scrape_has_model_year_color(scraped: dict) -> bool:
    """Require model, **YYYY** year of manufacture, and color before creating an opportunity."""
    m = (scraped.get("model") or "").strip()
    y = _normalize_manufacturing_year_yyyy(scraped.get("year_of_mfg") or "")
    c = (scraped.get("color") or "").strip()
    return bool(m and y and c)


def _add_enquiry_reuse_vehicle_dict_ready(vm: dict | None) -> bool:
    """True when ``prepare_vehicle`` (or prior merge) already supplied model / YYYY / color for Add Enquiry."""
    if not vm:
        return False
    t = dict(vm)
    _apply_year_of_mfg_yyyy(t)
    return _add_enquiry_vehicle_scrape_has_model_year_color(t)


def _merge_add_enquiry_vehicle_scrape(vehicle_merge: dict, scraped: dict) -> None:
    """Copy add-enquiry vehicle scrape into ``out['vehicle']`` (full_chassis / full_engine; no frame_num/engine_num)."""
    for k in (
        "model",
        "color",
        "year_of_mfg",
        "dispatch_year",
        "sku",
        "full_chassis",
        "full_engine",
        "key_num",
        "in_transit",
        "cubic_capacity",
        "seating_capacity",
        "body_type",
        "vehicle_type",
    ):
        v = scraped.get(k)
        if v is None:
            continue
        if isinstance(v, bool):
            vehicle_merge[k] = v
            continue
        s = str(v).strip()
        if s:
            if k == "year_of_mfg":
                yn = _normalize_manufacturing_year_yyyy(s)
                if yn:
                    vehicle_merge[k] = yn
            else:
                vehicle_merge[k] = s


def _siebel_vehicle_find_chassis_engine_enter(
    page: Page,
    vehicle_url: str,
    frame_p: str,
    engine_p: str,
    *,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    reuse_vehicle_dict: dict | None = None,
) -> tuple[bool, dict]:
    """
    Vehicles view: **Find -> Vehicles**, right fly-in **VIN** + **Engine#** with ``*`` wildcards, **Enter**,
    optional Find/Go, click matching **VIN** in left **Search Results**, then scrape grid and **Vehicle
    Information** (model / year / color).
    Returns ``(query_ok, scraped)`` — ``scraped`` may be empty if the grid did not render.

    When ``reuse_vehicle_dict`` is set and passes :func:`_add_enquiry_reuse_vehicle_dict_ready` (e.g. after
    ``prepare_vehicle``), **after** a successful VIN drill-down the list/grid/detail scrape is skipped and
    that dict is reused — **Find→Vehicles** and VIN drill remain so the **Enquiry** tab is on the vehicle
    view.
    """
    vu = (vehicle_url or "").strip()
    fp = (frame_p or "").strip()
    ep = (engine_p or "").strip()
    if not vu:
        note("Add Enquiry: DMS_REAL_URL_VEHICLE is not set — cannot open vehicle list.")
        return False, {}
    if not fp or not ep:
        note("Add Enquiry: chassis/VIN and engine from DB are both required.")
        return False, {}
    cw = _siebel_vehicle_find_wildcard_value(fp)
    ew = _siebel_vehicle_find_wildcard_value(ep)

    _goto(page, vu, "vehicle_list_add_enquiry", nav_timeout_ms=nav_timeout_ms)
    _siebel_after_goto_wait(page, floor_ms=1200)

    if _try_expand_find_flyin(
        page,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Add Enquiry: Find fly-in expand clicked (if collapsed).")

    if _try_prepare_find_vehicles_applet(
        page,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Add Enquiry: Find → Vehicles object type selected (header / Find menu).")
    _safe_page_wait(page, 600, log_label="after_find_vehicles_prep")

    filled_flyin = _try_fill_vin_engine_in_vehicles_find_applet(
        page,
        chassis_wildcard=cw,
        engine_wildcard=ew,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    if filled_flyin:
        note("Add Enquiry: filled VIN + Engine# in Vehicles Find fly-in and pressed Enter.")
    if not filled_flyin:
        note("Add Enquiry: Vehicles Find fly-in fill failed on first pass — retrying Find→Vehicles.")
        _try_expand_find_flyin(
            page,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _safe_page_wait(page, 350, log_label="retry_expand_find_flyin_vehicles")
        _try_prepare_find_vehicles_applet(
            page,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _safe_page_wait(page, 500, log_label="retry_find_vehicles_prep")
        filled_flyin = _try_fill_vin_engine_in_vehicles_find_applet(
            page,
            chassis_wildcard=cw,
            engine_wildcard=ew,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )

    chassis_ok = filled_flyin
    engine_ok = filled_flyin
    if not chassis_ok or not engine_ok:
        note(
            "Add Enquiry: strict Vehicles Find fill failed — expected VIN id='field_textbox_0' and "
            "Engine# id='field_textbox_2' in the same Find→Vehicles frame."
        )
        return False, {}

    _safe_page_wait(page, 400, log_label="after_vehicle_find_enter")
    try:
        _safe_page_wait(page, 2500, log_label="vehicle_find_query_settle")
        page.wait_for_load_state("networkidle", timeout=12_000)
    except PlaywrightTimeout:
        note("Add Enquiry: networkidle wait timed out; continuing vehicle grid scrape.")
    except Exception as e:
        if _is_browser_disconnected_error(e):
            raise RuntimeError(
                "Siebel: browser disconnected while waiting for vehicle grid (Add Enquiry path)."
            ) from e
        raise

    from app.services.hero_dms_playwright_customer import _siebel_try_click_named_in_frames

    if _siebel_try_click_named_in_frames(
        page,
        re.compile(r"Siebel\s*Find", re.I),
        roles=("tab", "link"),
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Add Enquiry: activated Siebel Find tab before VIN drill-down (if present).")
        _safe_page_wait(page, 500, log_label="after_siebel_find_tab_vehicle")

    vin_drill_ok = False
    if _siebel_try_click_vin_search_hit_link(
        page,
        fp,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        vin_drill_ok = True
        note("Add Enquiry: clicked VIN in left Search Results to load vehicle detail.")
        _safe_page_wait(page, 1800, log_label="after_vehicle_search_vin_drilldown")
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            note("Add Enquiry: networkidle after VIN drill-down timed out; continuing scrape.")

    if (
        vin_drill_ok
        and reuse_vehicle_dict is not None
        and _add_enquiry_reuse_vehicle_dict_ready(reuse_vehicle_dict)
    ):
        scraped = dict(reuse_vehicle_dict)
        _apply_year_of_mfg_yyyy(scraped)
        note(
            "Add Enquiry: reusing merged vehicle data from prepare_vehicle — "
            "skipping duplicate list/grid/detail scrape (Enquiry tab from vehicle view)."
        )
        if (scraped.get("full_chassis") or "").strip() or (scraped.get("key_num") or "").strip():
            note("Add Enquiry: vehicle hit present from reuse (full_chassis or list key).")
        elif (scraped.get("model") or "").strip():
            note("Add Enquiry: vehicle detail from reuse has model.")
        if (scraped.get("full_chassis") or "").strip():
            note(
                "Add Enquiry: full VIN scope from reuse — "
                f"full_chassis={scraped.get('full_chassis')!r}, full_engine={scraped.get('full_engine')!r}."
            )
        return True, scraped

    scraped = scrape_siebel_vehicle_row(page, content_frame_selector=content_frame_selector)
    scraped = _merge_scrape_vehicle_detail_applet(
        page, scraped, content_frame_selector=content_frame_selector
    )
    scraped = _merge_scrape_vehicle_record_from_vin_aria(
        page, scraped, content_frame_selector=content_frame_selector
    )
    _apply_year_of_mfg_yyyy(scraped)

    if (scraped.get("full_chassis") or "").strip() or (scraped.get("key_num") or "").strip():
        note("Add Enquiry: vehicle hit present (full_chassis or list key).")
    elif (scraped.get("model") or "").strip():
        note("Add Enquiry: vehicle detail applet has model (narrow grid).")
    else:
        note("Add Enquiry: vehicle grid/detail scrape found no row yet.")
    if (scraped.get("full_chassis") or "").strip():
        note(
            "Add Enquiry: scraped full VIN scope — "
            f"full_chassis={scraped.get('full_chassis')!r}, full_engine={scraped.get('full_engine')!r}."
        )
    _dump_branch_hits(note)
    return True, scraped
