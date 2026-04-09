"""
Hero Connect / Oracle Siebel Open UI — customer / contact automation (Playwright).

Find-Contact search, mobile fill, contact match/sweep, enquiry tab detection,
Relation's Name, Address/branch2, Payments tab, Add Customer Payment,
and the ``_add_enquiry_opportunity`` pipeline (including financier MVG helpers).
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
from typing import Any
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
    _MOBILE_DOM_EVAL_JS,
    _agent_debug_log,
    _click_find_go_query,
    _detect_siebel_error_popup,
    _fill_by_label_on_frame,
    _frame_url_matches_payment_hint,
    _frames_for_enquiry_subgrid_eval,
    _goto,
    _hero_default_mobile_search_hit_root_hint,
    _hero_default_payment_lines_root_hint,
    _is_browser_disconnected_error,
    _iter_frame_locator_roots,
    _iter_mobile_search_hit_roots,
    _iter_siebel_root_search_order,
    _load_contact_enquiry_subgrid_hint_dict_from_config,
    _load_mobile_search_hit_hint_dict_from_config,
    _load_payment_lines_hint_dict_from_config,
    _locator_for_duplicate_fields,
    _normalize_cubic_cc_digits,
    _ordered_frames,
    _poll_and_handle_siebel_error_popup,
    _safe_page_wait,
    _siebel_after_goto_wait,
    _siebel_all_search_roots,
    _siebel_click_by_id_anywhere,
    _siebel_inter_action_pause,
    _siebel_ist_today,
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
    _add_enquiry_vehicle_scrape_has_model_year_color,
    _apply_year_of_mfg_yyyy,
    _merge_add_enquiry_vehicle_scrape,
    _normalize_manufacturing_year_yyyy,
    _siebel_fill_key_battery_from_dms_values,
    scrape_siebel_vehicle_row,
)

logger = logging.getLogger(__name__)


def _try_fill_mobile_dom_scan(page: Page, value: str) -> bool:
    """Last resort: pick the highest-scoring visible text input in each frame by label/title/name."""
    if not (value or "").strip():
        return False
    v = value.strip()
    for frame in _ordered_frames(page):
        try:
            ok = frame.evaluate(_MOBILE_DOM_EVAL_JS, v)
            if ok:
                logger.info(
                    "siebel_dms: filled mobile via DOM scan in frame %s",
                    (frame.url or "")[:100],
                )
                return True
        except Exception as e:
            logger.debug("siebel_dms: dom scan failed: %s", e)
            continue
    return False


def _try_fill_mobile_and_find_in_contact_applet(
    page: Page,
    *,
    mobile: str,
    timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    first_name: str | None = None,
) -> bool:
    """
    Keep interaction inside the opened global Find->Contact applet (right fly-in): fill Mobile Phone,
    then click the local Find icon/button in the same applet.
    """
    if not (mobile or "").strip():
        return False

    # #region agent log - contact find same-frame diagnostics
    def _dbg(hypothesis_id: str, message: str, data: dict) -> None:
        try:
            import json as _j_dbg, time as _t_dbg
            from pathlib import Path as _P_dbg
            _log_path = _P_dbg(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_log_path, "a", encoding="utf-8") as _lf_dbg:
                _lf_dbg.write(
                    _j_dbg.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "pre-fix",
                            "hypothesisId": hypothesis_id,
                            "location": "hero_dms_playwright_customer.py:_try_fill_mobile_and_find_in_contact_applet",
                            "message": message,
                            "data": data,
                            "timestamp": _ts_ist_iso(),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
    # #endregion
    _dbg(
        "H1",
        "contact_find_entry",
        {
            "has_mobile": bool((mobile or "").strip()),
            "has_first_name": bool((first_name or "").strip()),
            "first_name_len": len((first_name or "").strip()),
        },
    )
    mobile_selectors = _mobile_selectors(mobile_aria_hints)
    mobile_selectors = [
        'input[title="Mobile Phone"]',
        'input[title*="Mobile Phone" i]',
        'input[title*="Mobile Phone #" i]',
        'input[aria-label*="Mobile Phone" i]',
        *mobile_selectors,
    ]
    find_css = (
        'input[type="submit"][value*="Find" i]',
        'input[type="button"][value*="Find" i]',
        'button[title="Find" i]',
        'button[title*="Find" i]',
        'a[title="Find" i]',
        'a[title*="Find" i]',
        '[role="button"][title="Find" i]',
        '[role="button"][title*="Find" i]',
        'button[aria-label="Find" i]',
        'button[aria-label*="Find" i]',
        '[role="button"][aria-label="Find" i]',
        '[role="button"][aria-label*="Find" i]',
    )

    _FILL_FIRST_IN_RIGHT_FIND_PANEL_JS = """(mobileValue) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
        const isTxt = (el) => {
        if (!el || el.tagName !== 'INPUT') return false;
        const t = String(el.type || 'text').toLowerCase();
        return !['hidden','submit','button','checkbox','radio','file','image'].includes(t);
      };
      const panelCandidates = Array.from(document.querySelectorAll('div'))
        .filter(vis)
        .filter((d) => {
          const r = d.getBoundingClientRect();
          if (r.width < 220 || r.width > 480 || r.height < 180 || r.height > 620) return false;
          if (r.left < window.innerWidth * 0.52) return false; // right-side fly-in
          const txt = (d.innerText || '').toLowerCase();
          return txt.includes('contact') || txt.includes('mobile') || txt.includes('rse') || txt.includes('tehsil');
        });
      if (!panelCandidates.length) return false;
      panelCandidates.sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        return ra.top - rb.top;
      });
      for (const panel of panelCandidates) {
        const inputs = Array.from(panel.querySelectorAll('input')).filter((i) => isTxt(i) && vis(i));
        if (!inputs.length) continue;
        const first = inputs.find((i) => {
          const t = String(i.getAttribute('title') || '').toLowerCase();
          const a = String(i.getAttribute('aria-label') || '').toLowerCase();
          return t.includes('mobile phone') || a.includes('mobile phone');
        }) || inputs[0];
        try {
          first.focus();
          first.value = '';
          first.value = String(mobileValue || '').trim();
          first.dispatchEvent(new Event('input', { bubbles: true }));
          first.dispatchEvent(new Event('change', { bubbles: true }));
          first.dispatchEvent(new Event('blur', { bubbles: true }));
        } catch (e) {
          continue;
        }
        const findBtn = panel.querySelector(
          'button[title*="find" i],a[title*="find" i],[role="button"][title*="find" i],' +
          'button[aria-label*="find" i],[role="button"][aria-label*="find" i],' +
          'input[type="submit"][value*="find" i],input[type="button"][value*="find" i]'
        );
        if (findBtn && vis(findBtn)) {
          try { findBtn.click(); return true; } catch (e) {}
        }
      }
      return false;
    }"""

    def try_root(root) -> bool:
        # #region agent log - findfieldsbox probe on this root
        try:
            _ff_probe = root.evaluate(
                """() => {
                  const box = document.getElementById('findfieldsbox');
                  if (!box) return { has_box: false, editable_inputs: 0, mobile_title_inputs: 0, first_id_present: false };
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 2 && r.height >= 2;
                  };
                  const inputs = Array.from(box.querySelectorAll('input')).filter(vis);
                  const editable = inputs.filter((i) => {
                    const t = String(i.type || 'text').toLowerCase();
                    if (['hidden','submit','button','checkbox','radio','file','image'].includes(t)) return false;
                    return !i.readOnly && !i.disabled;
                  });
                  const mobileByTitle = editable.filter((i) => String(i.getAttribute('title') || '') === 'Mobile Phone').length;
                  const firstId = !!box.querySelector('input#field_textbox_1');
                  return {
                    has_box: true,
                    editable_inputs: editable.length,
                    mobile_title_inputs: mobileByTitle,
                    first_id_present: firstId,
                  };
                }"""
            )
            _dbg("H6", "findfieldsbox_probe", _ff_probe or {})
        except Exception:
            _dbg("H6", "findfieldsbox_probe_eval_failed", {})
        # #endregion

        # Strict path: fill inside same-frame #findfieldsbox using required selectors.
        try:
            fn_raw = (first_name or "").strip()
            fn_find = _first_name_for_contact_find_query_field(fn_raw)
            ff_out = root.evaluate(
                """(args) => {
                  const mobileVal = String(args.mobile || '').trim();
                  const firstVal = String(args.first || '').trim();
                  const box = document.getElementById('findfieldsbox');
                  if (!box) return { ok: false, reason: 'no_findfieldsbox' };
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 2 && r.height >= 2;
                  };
                  const editable = (el) => !!(el && vis(el) && !el.readOnly && !el.disabled);

                  const m = box.querySelector('input[title="Mobile Phone"]');
                  const f = box.querySelector('input#field_textbox_1');
                  if (!editable(m)) return { ok: false, reason: 'mobile_not_editable_or_missing' };
                  if (firstVal && !editable(f)) return { ok: false, reason: 'first_not_editable_or_missing' };

                  m.focus();
                  m.value = '';
                  m.value = mobileVal;
                  m.dispatchEvent(new Event('input', { bubbles: true }));
                  m.dispatchEvent(new Event('change', { bubbles: true }));

                  if (firstVal) {
                    f.focus();
                    f.value = '';
                    f.value = firstVal;
                    f.dispatchEvent(new Event('input', { bubbles: true }));
                    f.dispatchEvent(new Event('change', { bubbles: true }));
                  }

                  const findSel = [
                    'input[type="submit"][value*="Find" i]',
                    'input[type="button"][value*="Find" i]',
                    'button[title="Find" i]',
                    'button[aria-label="Find" i]',
                    '[role="button"][title="Find" i]',
                    '[role="button"][aria-label="Find" i]'
                  ];
                  for (const s of findSel) {
                    const b = box.querySelector(s);
                    if (editable(b) || vis(b)) {
                      try { b.click(); return { ok: true, mode: 'find_button_in_box' }; } catch (e) {}
                    }
                  }
                  try {
                    if (firstVal) f.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
                    else m.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
                    return { ok: true, mode: 'enter_fallback_in_box' };
                  } catch (e) {
                    return { ok: false, reason: 'find_click_and_enter_failed' };
                  }
                }""",
                {"mobile": mobile, "first": fn_find},
            )
            _dbg("H8", "findfieldsbox_strict_fill_attempt", ff_out or {})
            if ff_out and ff_out.get("ok"):
                return True
        except Exception:
            _dbg("H8", "findfieldsbox_strict_fill_eval_failed", {})

        applets: list = []
        # Prefer applet that looks like Find->Contact (contains Mobile Phone + First/Last name labels).
        try:
            cand = root.locator(".siebui-applet").filter(has_text=re.compile(r"Mobile\s*Phone", re.I))
            n = cand.count()
            for i in range(min(n, 12)):
                a = cand.nth(i)
                try:
                    if not a.is_visible(timeout=500):
                        continue
                    txt = (a.inner_text(timeout=900) or "").lower()
                    if ("first name" in txt or "last name" in txt or "contact type" in txt):
                        applets.append(a)
                except Exception:
                    continue
        except Exception:
            pass
        # Fallback to any visible applet containing Mobile Phone.
        if not applets:
            try:
                cand = root.locator(".siebui-applet").filter(has_text=re.compile(r"Mobile\s*Phone", re.I))
                n = cand.count()
                for i in range(min(n, 12)):
                    a = cand.nth(i)
                    try:
                        if a.is_visible(timeout=450):
                            applets.append(a)
                    except Exception:
                        continue
            except Exception:
                pass

        for applet in applets:
            # #region agent log - applet-level candidate quality
            try:
                _applet_diag = applet.evaluate(
                    """(el) => {
                      const vis = (n) => {
                        if (!n) return false;
                        const st = window.getComputedStyle(n);
                        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                        const r = n.getBoundingClientRect();
                        return r.width >= 2 && r.height >= 2;
                      };
                      const txt = String(el.innerText || '').slice(0, 300);
                      const mobile = el.querySelector('input[title="Mobile Phone"]');
                      const first = el.querySelector('input#field_textbox_1');
                      return {
                        has_mobile_title_exact: !!mobile,
                        mobile_editable: !!(mobile && vis(mobile) && !mobile.readOnly && !mobile.disabled),
                        has_first_id: !!first,
                        first_editable: !!(first && vis(first) && !first.readOnly && !first.disabled),
                        text_sample: txt,
                      };
                    }"""
                )
                _dbg("H7", "applet_candidate_probe", _applet_diag or {})
            except Exception:
                _dbg("H7", "applet_candidate_probe_eval_failed", {})
            # #endregion
            # Fill mobile only within this applet.
            filled = False
            for css in mobile_selectors:
                try:
                    loc = applet.locator(css).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=700):
                        continue
                    try:
                        loc.click(timeout=min(3000, timeout_ms))
                    except Exception:
                        loc.click(timeout=min(3000, timeout_ms), force=True)
                    loc.fill("", timeout=min(3000, timeout_ms))
                    loc.fill(mobile.strip(), timeout=timeout_ms)
                    filled = True
                    break
                except Exception:
                    continue
            if not filled:
                try:
                    loc = applet.get_by_label(re.compile(r"mobile\s*(phone|number|no|#)?", re.I)).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        loc.fill("", timeout=min(3000, timeout_ms))
                        loc.fill(mobile.strip(), timeout=timeout_ms)
                        filled = True
                except Exception:
                    pass
            if not filled:
                continue

            fn_raw = (first_name or "").strip()
            fn_find = _first_name_for_contact_find_query_field(fn_raw)
            if fn_find:
                # #region agent log - same applet selector visibility
                _strict_id_count = 0
                _strict_id_visible = False
                _fallback_selector_hits = 0
                try:
                    _strict = applet.locator('input#field_textbox_1, input[id="field_textbox_1"]')
                    _strict_id_count = _strict.count()
                    if _strict_id_count > 0:
                        try:
                            _strict_id_visible = _strict.first.is_visible(timeout=300)
                        except Exception:
                            _strict_id_visible = False
                except Exception:
                    pass
                for _s in _SIEBEL_FIND_FIRST_NAME_SELECTORS:
                    try:
                        _l = applet.locator(_s)
                        if _l.count() > 0:
                            _fallback_selector_hits += 1
                    except Exception:
                        continue
                _dbg(
                    "H2",
                    "first_name_selector_probe_same_applet",
                    {
                        "strict_id_count": _strict_id_count,
                        "strict_id_visible": _strict_id_visible,
                        "fallback_selector_hits": _fallback_selector_hits,
                    },
                )
                # #endregion
                fn_filled = False
                for css in _SIEBEL_FIND_FIRST_NAME_SELECTORS:
                    try:
                        fl = applet.locator(css).first
                        if fl.count() > 0 and fl.is_visible(timeout=700):
                            fl.fill("", timeout=min(3000, timeout_ms))
                            fl.fill(fn_find, timeout=timeout_ms)
                            fn_filled = True
                            break
                    except Exception:
                        continue
                if not fn_filled:
                    try:
                        fl = applet.get_by_label(re.compile(r"^\s*First\s*Name\s*$", re.I)).first
                        if fl.count() > 0 and fl.is_visible(timeout=700):
                            fl.fill("", timeout=min(3000, timeout_ms))
                            fl.fill(fn_find, timeout=timeout_ms)
                            fn_filled = True
                    except Exception:
                        pass
                if not fn_filled:
                    _dbg(
                        "H3",
                        "first_name_not_filled_in_same_applet",
                        {
                            "reason": "selectors_not_visible_or_fill_failed",
                        },
                    )
                    continue

            _safe_page_wait(page, 150, log_label="contact_applet_mobile_filled")
            # Click Find icon/button inside same applet.
            for css in find_css:
                try:
                    btn = applet.locator(css).first
                    if btn.count() > 0 and btn.is_visible(timeout=700):
                        try:
                            btn.click(timeout=timeout_ms)
                        except Exception:
                            btn.click(timeout=timeout_ms, force=True)
                        _dbg(
                            "H4",
                            "find_clicked_same_applet",
                            {"used_selector": css, "had_first_name": bool(fn_raw)},
                        )
                        return True
                except Exception:
                    continue
            # Fallback by title
            try:
                btn = applet.get_by_title(re.compile(r"^\s*Find\s*$", re.I)).first
                if btn.count() > 0 and btn.is_visible(timeout=700):
                    try:
                        btn.click(timeout=timeout_ms)
                    except Exception:
                        btn.click(timeout=timeout_ms, force=True)
                    _dbg(
                        "H4",
                        "find_clicked_same_applet_title_fallback",
                        {"had_first_name": bool(fn_raw)},
                    )
                    return True
            except Exception:
                pass
        return False

    # Strong fallback for custom Find popup: fill first visible field and click Find inside that popup.
    if not (first_name or "").strip():
        for frame in _ordered_frames(page):
            try:
                if bool(frame.evaluate(_FILL_FIRST_IN_RIGHT_FIND_PANEL_JS, mobile.strip())):
                    logger.info(
                        "siebel_dms: filled first visible input + clicked Find in right Contact popup (DOM fallback)"
                    )
                    _dbg("H5", "dom_fallback_used_for_mobile", {"first_name_required": False})
                    return True
            except Exception:
                continue
    else:
        _dbg("H5", "dom_fallback_skipped_due_to_first_name_requirement", {"first_name_required": True})

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        try:
            if try_root(fl):
                return True
        except Exception:
            continue
    for frame in _ordered_frames(page):
        try:
            if try_root(frame):
                return True
        except Exception:
            continue
    _dbg("H1", "contact_find_same_frame_failed_all_roots", {"first_name_required": bool((first_name or "").strip())})
    return False


def _siebel_blur_and_settle(page: Page, *, ms: int = 400) -> None:
    """Siebel often keeps focus in the Find/mobile field; blur so the main enquiry applet receives clicks."""
    try:
        page.evaluate(
            """() => {
            const a = document.activeElement;
            if (a && typeof a.blur === 'function') a.blur();
        }"""
        )
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    _safe_page_wait(page, ms, log_label="blur_settle")

def _try_prepare_find_contact_applet(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    """
    Hero Connect navigation: **Find** → object type **Contact** (header dropdown or right applet),
    so the Mobile field is the Contact search field (not Job Card / Vehicle, etc.).
    """
    contact_label = re.compile(r"^\s*Contact\s*$", re.I)
    find_label = re.compile(r"^\s*Find\s*$", re.I)

    def _force_open_contact_find_via_dom() -> bool:
        """
        Last-resort for custom/non-ARIA Siebel header controls:
        - pick a visible <select> that has both Find and Contact options
        - set Contact and fire input/change/keyboard events
        - click nearby Find-titled trigger in same header cluster
        """
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
            const contact = opts.find(o => norm(o.textContent) === 'contact');
            if (!hasFind || !contact) continue;
            sel.focus();
            sel.value = contact.value;
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
                logger.info("siebel_dms: forced global Contact selection via DOM fallback")
                _safe_page_wait(page, 700, log_label="force_open_contact_find_dom")
            return ok
        except Exception:
            return False

    def _select_global_find_contact(root) -> bool:
        """
        Top nav global finder often has a select/combobox currently showing ``Find`` with options:
        Contact, Job Card, Customer Account, etc. Choose **Contact** there first.
        """
        # Click-path first: opening the dropdown and clicking Contact reliably triggers applet open.
        for scope in (root, page):
            try:
                # Header/global finder control
                cb = scope.get_by_role("combobox", name=find_label).first
                if cb.count() <= 0 or not cb.is_visible(timeout=500):
                    continue
                cb.click(timeout=timeout_ms)
                _safe_page_wait(page, 250, log_label="global_find_open_click")
                clicked_contact = False
                for role in ("option", "menuitem", "link"):
                    try:
                        item = page.get_by_role(role, name=contact_label).first
                        if item.count() > 0 and item.is_visible(timeout=600):
                            item.click(timeout=timeout_ms)
                            clicked_contact = True
                            logger.info("siebel_dms: global finder clicked Contact (%s)", role)
                            break
                    except Exception:
                        continue
                if clicked_contact:
                    _safe_page_wait(page, 500, log_label="global_find_contact_clicked")
                    return True
            except Exception:
                continue

        # Native <select> path (fallback)
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
                has_contact = any(x == "contact" for x in opts)
                if not (has_find and has_contact):
                    continue
                # Mirror operator flow exactly: Find -> Contact.
                try:
                    sel.select_option(label=find_label, timeout=timeout_ms)
                    _safe_page_wait(page, 180, log_label="global_find_select_find")
                except Exception:
                    pass
                sel.select_option(label=contact_label, timeout=timeout_ms)
                logger.info("siebel_dms: global top finder selected Contact (native select)")
                _safe_page_wait(page, 350, log_label="global_find_contact_select")
                return True
            except Exception:
                continue

        # ARIA combobox/menu path
        for scope in (root, page):
            try:
                cb = scope.get_by_role("combobox", name=re.compile(r"^\s*find\s*$", re.I)).first
                if cb.count() > 0 and cb.is_visible(timeout=500):
                    cb.click(timeout=timeout_ms)
                    _safe_page_wait(page, 250, log_label="global_find_open")
                    for role in ("option", "menuitem", "link"):
                        try:
                            item = page.get_by_role(role, name=contact_label).first
                            if item.count() > 0 and item.is_visible(timeout=500):
                                item.click(timeout=timeout_ms)
                                logger.info("siebel_dms: global top finder chose Contact (%s)", role)
                                _safe_page_wait(page, 350, log_label="global_find_contact_menu")
                                return True
                        except Exception:
                            continue
            except Exception:
                continue
        if _force_open_contact_find_via_dom():
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

    def select_contact_on_native_selects(root) -> bool:
        for sel in _visible_selects(root):
            try:
                texts = sel.evaluate(
                    """el => [...el.options].map(o => (o.textContent || '').trim())"""
                )
                if not texts or not any((t or "").strip().lower() == "contact" for t in texts):
                    continue
                # Skip lists where "Contact" is only part of a longer option (e.g. "Contacts_Enquiry")
                exact_contact = [t for t in texts if (t or "").strip().lower() == "contact"]
                if not exact_contact:
                    continue
                sel.select_option(label=contact_label, timeout=timeout_ms)
                logger.info("siebel_dms: Find object type → Contact (native select)")
                return True
            except Exception:
                continue
        return False

    def click_contact_menu(page_: Page, root) -> bool:
        """Menus often render at page level after opening a frame-local dropdown."""
        for scope in (root, page_):
            for role in ("menuitem", "option", "link"):
                try:
                    loc = scope.get_by_role(role, name=contact_label).first
                    if loc.count() > 0 and loc.is_visible(timeout=800):
                        loc.click(timeout=timeout_ms)
                        logger.info("siebel_dms: chose Contact from Find menu (%s)", role)
                        return True
                except Exception:
                    continue
        return False

    def open_find_dropdown_then_contact(page_: Page, root) -> bool:
        try:
            cb = root.get_by_role("combobox", name=re.compile(r"find", re.I)).first
            if cb.count() > 0 and cb.is_visible(timeout=700):
                cb.click(timeout=timeout_ms)
                _safe_page_wait(page_, 400, log_label="find_combobox")
                if click_contact_menu(page_, root):
                    return True
        except Exception:
            pass
        return False

    def try_on_root(page_: Page, root) -> bool:
        changed_global = _select_global_find_contact(root)
        if select_contact_on_native_selects(root):
            return True
        if open_find_dropdown_then_contact(page_, root):
            return True
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


def _mobile_selectors(extra_hints: list[str]) -> list[str]:
    base = [
        'input[aria-label*="Cellular" i]',
        'input[aria-label*="Cell Phone" i]',
        'input[aria-label*="Mobile Phone #" i]',
        'input[aria-label*="Mobile Phone" i]',
        'input[aria-label*="Mobile Number" i]',
        'input[aria-label*="Mobile No" i]',
        'input[title*="Mobile Phone #" i]',
        'input[title*="Mobile Number" i]',
        'input[title*="Main Phone" i]',
        'input[aria-label*="Main Phone" i]',
        'input[aria-label*="Phone #" i]',
        'input[title*="Cellular" i]',
        'input[title*="Cell Phone" i]',
        'input[title*="Mobile" i]',
        'input[title*="Mobile No" i]',
        'input[name*="Cellular" i]',
        'input[name*="CellPhone" i]',
        'input[name*="Mobile" i]',
        'input[name*="MobileNo" i]',
    ]
    for h in extra_hints:
        if len(h) >= 2:
            base.insert(0, f'input[aria-label*="{h}" i]')
            base.insert(0, f'input[title*="{h}" i]')
    return base


def _try_fill_mobile_semantic(
    page: Page,
    value: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
    extra_hints: list[str],
    prefer_second_match: bool = False,
    label_visible_ms: int = 800,
) -> bool:
    """
    Hero Connect Find applet (dark right panel, Contact → Mobile Phone): label may not
    duplicate into aria-label; match by accessible name via Playwright.
    """
    if not (value or "").strip():
        return False
    patterns: list[re.Pattern[str]] = []
    for h in extra_hints:
        t = (h or "").strip()
        if len(t) >= 2:
            patterns.append(re.compile(re.escape(t), re.I))
    patterns.extend(
        [
            re.compile(r"mobile\s*phone\s*#\s*", re.I),
            re.compile(r"mobile\s*phone\s*#?", re.I),
            re.compile(r"mobile\s*number", re.I),
            re.compile(r"mobile\s*phone", re.I),
            re.compile(r"cellular", re.I),
        ]
    )

    def try_on_root(root) -> bool:
        for pat in patterns:
            for get_loc in (
                lambda p=pat: root.get_by_label(p),
                lambda p=pat: root.get_by_role("textbox", name=p),
                lambda p=pat: root.get_by_role("searchbox", name=p),
                lambda p=pat: root.get_by_role("combobox", name=p),
            ):
                try:
                    base = get_loc()
                    loc = _locator_for_duplicate_fields(
                        base, prefer_second_if_duplicate=prefer_second_match
                    )
                    if loc is None:
                        continue
                    if not loc.is_visible(timeout=label_visible_ms):
                        continue
                    try:
                        loc.click(timeout=min(3000, timeout_ms))
                    except Exception:
                        loc.click(timeout=min(3000, timeout_ms), force=True)
                    loc.fill("", timeout=min(3000, timeout_ms))
                    loc.fill(value.strip(), timeout=timeout_ms)
                    logger.info("siebel_dms: filled mobile via semantic locator")
                    return True
                except Exception as e:
                    logger.debug("siebel_dms: semantic mobile try failed: %s", e)
                    continue
        return False

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        if try_on_root(fl):
            return True
    for frame in _ordered_frames(page):
        if try_on_root(frame):
            return True
    return False


def _contact_view_find_by_mobile(
    page: Page,
    *,
    contact_url: str,
    mobile: str,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    note,
    step,
    stage_msg: str,
    wait_after_go_ms: int = 2000,
    first_name: str | None = None,
) -> bool:
    """
    Open Contact Find view, set object type to Contact when possible, fill **mobile**, optional
    **First Name**, then Go. When ``first_name`` is set, both fields are filled before Find; the
    First Name field receives the **exact** string (no ``*`` wildcard).
    When ``first_name`` is omitted, behavior matches legacy **mobile-only** find (re-find after basic
    enquiry, etc.).

    When ``wait_after_go_ms`` is **2000** (default), the post–Find/Go pause uses
    :func:`_contact_find_after_go_wait_bounded` (400/800/800 ms slices, early exit when the mobile
    hit is visible) instead of a single 2000 ms sleep. Other values keep a fixed sleep.
    """
    cu = (contact_url or "").strip()
    if not cu:
        note("Contact URL missing — cannot run Find by mobile.")
        return False
    _goto(page, cu, "contact_find", nav_timeout_ms=nav_timeout_ms)
    _siebel_after_goto_wait(page, floor_ms=1200)
    step(stage_msg)

    if _try_expand_find_flyin(
        page,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Find pane expand control clicked (if it was collapsed).")

    if _try_prepare_find_contact_applet(
        page,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Find → Contact: object type selected so the mobile field is the Contact search field.")
    _safe_page_wait(page, 600, log_label="after_find_contact_prep")

    _mobile_vis = 2400
    # Prefer strict applet-scoped flow so focus stays in the Find->Contact fly-in.
    scoped_applet_find_clicked = _try_fill_mobile_and_find_in_contact_applet(
        page,
        mobile=mobile,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        mobile_aria_hints=mobile_aria_hints,
        first_name=first_name,
    )
    if scoped_applet_find_clicked:
        note(
            "Filled Mobile (title='Mobile Phone') and First Name (id='field_textbox_1' when present) "
            "as exact first name (no wildcard) in the same Contact Find frame and clicked Find."
        )
        if wait_after_go_ms == 2000:
            _contact_find_after_go_wait_bounded(
                page, mobile, content_frame_selector=content_frame_selector, note=note
            )
        else:
            _safe_page_wait(page, wait_after_go_ms, log_label="after_contact_find_go_scoped")
        return True

    fn_req = (first_name or "").strip()
    if fn_req:
        note(
            "Find failed in strict same-frame mode (Mobile title='Mobile Phone' + First Name id='field_textbox_1')."
        )
        return False

    def _attempt_fill_mobile() -> bool:
        ok = _try_fill_field(
            page,
            _mobile_selectors(mobile_aria_hints),
            mobile,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            visible_timeout_ms=_mobile_vis,
        )
        if not ok:
            ok = _try_fill_mobile_semantic(
                page,
                mobile,
                timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                extra_hints=mobile_aria_hints,
                label_visible_ms=_mobile_vis,
            )
        if not ok:
            ok = _try_fill_mobile_dom_scan(page, mobile)
        return ok

    filled_mobile = _attempt_fill_mobile()
    if not filled_mobile:
        # Some tenants need an explicit second pass: open the top Find applet again, re-select Contact,
        # then retry Mobile Phone fill.
        note("Find mobile field not visible on first pass — retrying with forced Find→Contact applet open.")
        _try_expand_find_flyin(
            page,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _safe_page_wait(page, 350, log_label="retry_expand_find_flyin")
        _try_prepare_find_contact_applet(
            page,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _safe_page_wait(page, 700, log_label="retry_find_contact_prep")
        filled_mobile = _attempt_fill_mobile()
    if not filled_mobile:
        return False

    if fn_req:
        if not _fill_first_name_in_find_roots(
            page,
            fn_req,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        ):
            note("Find: could not fill First Name in Contact Find pane after mobile fill.")
            return False
        _fn_q = _first_name_for_contact_find_query_field(fn_req)
        note(f"Filled First Name in Find pane → {_fn_q!r} (exact match query).")

    _siebel_blur_and_settle(page, ms=350)

    if _click_find_go_query(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
        note("Clicked Find/Go on contact view (mobile query).")
    else:
        note("No Find/Go control clicked on contact view after mobile fill.")

    if wait_after_go_ms == 2000:
        _contact_find_after_go_wait_bounded(
            page, mobile, content_frame_selector=content_frame_selector, note=note
        )
    else:
        _safe_page_wait(page, wait_after_go_ms, log_label="after_contact_find_go")
    return True


def _contact_view_find_by_mobile_strategy_two(
    page: Page,
    *,
    contact_url: str,
    mobile: str,
    first_name: str | None,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    note,
    step,
    stage_msg_mobile_only: str,
    stage_msg_mobile_and_first: str,
    wait_after_go_ms: int = 2000,
) -> bool:
    """
    Video / stage-1 Contact Find: **one** navigation + Find/Go.

    - When ``first_name`` is non-empty after strip: fill **mobile + exact first name**, then Go
      (narrower Search Results at the server/UI).
    - When ``first_name`` is empty: **mobile-only** Find (legacy).

    Left-pane drilldown still uses mobile-scoped row/plan logic only; this helper does not change that.
    Kept name ``*_strategy_two`` for stable call sites; the old two-step mobile-then-first Find path
    was removed.
    """
    fn = (first_name or "").strip()
    stage = stage_msg_mobile_and_first if fn else stage_msg_mobile_only
    return _contact_view_find_by_mobile(
        page,
        contact_url=contact_url,
        mobile=mobile,
        nav_timeout_ms=nav_timeout_ms,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        mobile_aria_hints=mobile_aria_hints,
        note=note,
        step=step,
        stage_msg=stage,
        first_name=fn if fn else None,
        wait_after_go_ms=wait_after_go_ms,
    )

def _derive_relation_and_name(
    *,
    relation_prefix: str,
    care_of: str,
    gender: str,
) -> tuple[str, str]:
    """
    Use DB ``care_of`` (Father/Husband line): first marker (S/O, W/O, D/O) picks relation;
    remaining text becomes Relation's Name.
    """
    rel = (relation_prefix or "").strip().upper().replace(".", "")
    g = (gender or "").strip().lower()
    default_prefix = "S/o" if g.startswith("m") else "D/o"
    co = (care_of or "").strip()
    if not co:
        return rel, ""

    m = re.match(r"^\s*(S\s*/?\s*O|W\s*/?\s*O|D\s*/?\s*O)\s*[:\-]?\s*(.*)\s*$", co, re.I)
    if not m:
        nm = co
        if nm and not re.match(r"^\s*[SWD]\s*/?\s*O\b", nm, re.I):
            nm = f"{default_prefix} {nm}".strip()
        return rel, nm
    marker = re.sub(r"\s+", "", (m.group(1) or "").upper()).replace("/", "")
    rest = (m.group(2) or "").strip()
    if marker == "SO":
        rel = "S/O"
    elif marker == "WO":
        rel = "W/O"
    elif marker == "DO":
        rel = "D/O"
    if rest:
        name = f"{default_prefix} {rest}".strip()[:255]
    elif co and not re.match(r"^\s*[SWD]\s*/?\s*O\b", co, re.I):
        name = f"{default_prefix} {co}".strip()[:255]
    else:
        name = co[:255] if co else ""
    return rel, name


def _relation_type_from_care_of(care_of: str) -> str:
    """S/O, W/O, or D/O from leading marker in ``care_of`` (case-insensitive)."""
    co = (care_of or "").strip()
    if not co:
        return ""
    m = re.match(r"^\s*(S\s*/?\s*O|W\s*/?\s*O|D\s*/?\s*O)\b", co, re.I)
    if not m:
        return ""
    marker = re.sub(r"\s+", "", (m.group(1) or "").upper()).replace("/", "")
    if marker == "SO":
        return "S/O"
    if marker == "WO":
        return "W/O"
    if marker == "DO":
        return "D/O"
    return ""


def _relation_display_name_from_care_of(care_of: str) -> str:
    """
    Relation's Name only: text after S/O, W/O, or D/O (strip marker/punctuation);
    if there is no marker, use full ``care_of`` (trimmed).
    """
    co = (care_of or "").strip()
    if not co:
        return ""
    m = re.match(r"^\s*(S\s*/?\s*O|W\s*/?\s*O|D\s*/?\s*O)\s*[:\-]?\s*(.*)\s*$", co, re.I)
    if m:
        rest = (m.group(2) or "").strip()
        return rest[:255] if rest else ""
    return co[:255]


def _occupation_siebel_label_from_staging_profession(profession: str | None) -> str:
    """Hero contact Occupation LOV: farmer-related staging → Farmer/ Farm Related, else Private Sector."""
    p = (profession or "").strip().lower()
    if not p:
        return "Private Sector"
    if "farmer" in p or "farming" in p or re.search(r"\bfarm\b", p):
        return "Farmer/ Farm Related"
    return "Private Sector"


def _pick_occupation_siebel_lov(
    page: Page,
    *,
    occupation_label: str,
    timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Open Occupation (``name=s_4_1_120_0``, ``aria-label=Occupation``) and pick a dropdown option.
    ``occupation_label`` is the visible list text, e.g. ``Farmer/ Farm Related`` or ``Private Sector``.
    """
    target = (occupation_label or "").strip()
    if not target:
        return False
    occ_selectors = [
        'input[name="s_4_1_120_0"]',
        'input[aria-label="Occupation"]',
        'input[aria-label*="Occupation" i]',
    ]
    option_patterns = [re.compile(r"^\s*" + re.escape(target) + r"\s*$", re.I)]
    if "farmer" in target.lower():
        option_patterns.extend(
            [
                re.compile(r"Farmer\s*/\s*Farm\s*Related", re.I),
                re.compile(r"Farmer.*Farm\s*Related", re.I),
            ]
        )
    if "private" in target.lower():
        option_patterns.append(re.compile(r"Private\s+Sector", re.I))

    def try_root(root) -> bool:
        opened = False
        control = None
        for css in occ_selectors:
            try:
                c = root.locator(css).first
                if c.count() > 0 and c.is_visible(timeout=700):
                    try:
                        c.click(timeout=timeout_ms)
                    except Exception:
                        c.click(timeout=timeout_ms, force=True)
                    opened = True
                    control = c
                    break
            except Exception:
                continue
        if not opened:
            return False
        _safe_page_wait(page, 220, log_label="after_occupation_click")

        if control is not None:
            try:
                tag = (control.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
                if tag == "select":
                    for pat in option_patterns:
                        try:
                            control.select_option(label=pat, timeout=timeout_ms)
                            return True
                        except Exception:
                            continue
            except Exception:
                pass

        for pat in option_patterns:
            for role in ("option", "menuitem", "listitem", "link"):
                try:
                    loc = root.get_by_role(role, name=pat)
                    n = loc.count()
                    for i in range(min(n, 12)):
                        o = loc.nth(i)
                        if o.is_visible(timeout=500):
                            try:
                                o.click(timeout=timeout_ms)
                            except Exception:
                                o.click(timeout=timeout_ms, force=True)
                            return True
                except Exception:
                    continue
            for css in ("li", "a", "div", "span", "td"):
                try:
                    opts = root.locator(css).filter(has_text=pat)
                    n = opts.count()
                    for i in range(min(n, 20)):
                        o = opts.nth(i)
                        if o.is_visible(timeout=500):
                            try:
                                o.click(timeout=timeout_ms)
                            except Exception:
                                o.click(timeout=timeout_ms, force=True)
                            return True
                except Exception:
                    continue
        return False

    for r in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if try_root(r):
                note(f"Occupation LOV: selected {target!r}.")
                return True
        except Exception:
            continue
    note(f"Occupation LOV: could not select {target!r}.")
    return False


def _pick_relation_type_from_dropdown(
    page: Page,
    *,
    relation: str,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """
    Click relation type field titled like ``S/O\\W/O\\D/O:`` and pick option from opened dropdown.
    """
    rel = (relation or "").strip().upper().replace(".", "")
    if rel in ("SO", "S/O"):
        target = "S/O"
    elif rel in ("WO", "W/O"):
        target = "W/O"
    elif rel in ("DO", "D/O"):
        target = "D/O"
    else:
        target = relation.strip()
    if not target:
        return False

    type_selectors = [
        'input[name="s_4_1_86_0"]',
        'input[aria-label="S/O\\W/O\\D/O" i]',
        'select[title*="S/O\\W/O\\D/O" i]',
        'select[aria-label*="S/O\\W/O\\D/O" i]',
        'input[title*="S/O\\W/O\\D/O" i]',
        'input[aria-label*="S/O\\W/O\\D/O" i]',
        'select[title*="S/O" i]',
        'select[aria-label*="S/O" i]',
    ]

    def try_root(root) -> bool:
        # Open relation dropdown control
        opened = False
        control = None
        for css in type_selectors:
            try:
                c = root.locator(css).first
                if c.count() > 0 and c.is_visible(timeout=700):
                    try:
                        c.click(timeout=timeout_ms)
                    except Exception:
                        c.click(timeout=timeout_ms, force=True)
                    opened = True
                    control = c
                    break
            except Exception:
                continue
        if not opened:
            return False
        _safe_page_wait(page, 220, log_label="after_relation_type_click")

        # Native select path
        if control is not None:
            try:
                tag = (control.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
                if tag == "select":
                    control.select_option(label=re.compile(rf"^\s*{re.escape(target)}\s*$", re.I), timeout=timeout_ms)
                    return True
            except Exception:
                pass

        # Open-UI dropdown list path
        option_patterns = (
            re.compile(rf"^\s*{re.escape(target)}\s*$", re.I),
            re.compile(rf"\b{re.escape(target)}\b", re.I),
        )
        for pat in option_patterns:
            for role in ("option", "menuitem", "listitem", "link"):
                try:
                    loc = root.get_by_role(role, name=pat)
                    n = loc.count()
                    for i in range(min(n, 12)):
                        o = loc.nth(i)
                        if o.is_visible(timeout=500):
                            try:
                                o.click(timeout=timeout_ms)
                            except Exception:
                                o.click(timeout=timeout_ms, force=True)
                            return True
                except Exception:
                    continue
            for css in ("li", "a", "div", "span", "td"):
                try:
                    opts = root.locator(css).filter(has_text=pat)
                    n = opts.count()
                    for i in range(min(n, 20)):
                        o = opts.nth(i)
                        if o.is_visible(timeout=500):
                            try:
                                o.click(timeout=timeout_ms)
                            except Exception:
                                o.click(timeout=timeout_ms, force=True)
                            return True
                except Exception:
                    continue
        return False

    for r in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if try_root(r):
                return True
        except Exception:
            continue

    # Geometry fallback: click control directly above "Relation's Name", then pick target option.
    js_geo_pick = """(target) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 4 && r.height >= 4;
      };
      const n = (s) => String(s || '').trim().toLowerCase();
      const relNameInput = Array.from(document.querySelectorAll('input'))
        .find(i => vis(i) && (n(i.getAttribute('title')).includes('relation') || n(i.getAttribute('aria-label')).includes('relation')));
      if (!relNameInput) return false;
      const rr = relNameInput.getBoundingClientRect();
      const candidates = Array.from(document.querySelectorAll('input,select,div[role="combobox"],span[role="combobox"],a[role="button"],button'))
        .filter(vis)
        .filter(el => {
          const r = el.getBoundingClientRect();
          const dy = rr.top - r.top;
          const dx = Math.abs(r.left - rr.left);
          if (dy < 12 || dy > 80) return false;  // above relation name field
          if (dx > 70) return false;
          const t = n(el.getAttribute('title')) + ' ' + n(el.getAttribute('aria-label'));
          return t.includes('s/o') || t.includes('w/o') || t.includes('d/o') || t.includes('relation') || r.width < 220;
        })
        .sort((a,b) => Math.abs((rr.top - a.getBoundingClientRect().top) - 35) - Math.abs((rr.top - b.getBoundingClientRect().top) - 35));
      if (!candidates.length) return false;
      const ctrl = candidates[0];
      try { ctrl.click(); } catch (e) {}

      const targ = String(target || '').trim().toUpperCase();
      const opts = Array.from(document.querySelectorAll('[role="option"],li,div,span,a,td'))
        .filter(vis)
        .filter(el => {
          const tx = n(el.innerText || el.textContent || '').toUpperCase();
          return tx === targ || tx.includes(' ' + targ) || tx.startsWith(targ) || tx.endsWith(targ);
        });
      for (const o of opts) {
        try { o.click(); return true; } catch (e) {}
      }
      // Native select fallback if control is select
      if (ctrl.tagName === 'SELECT') {
        const sel = ctrl;
        const options = Array.from(sel.options || []);
        const hit = options.find(o => String(o.textContent || '').toUpperCase().includes(targ));
        if (hit) {
          try {
            sel.value = hit.value;
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          } catch (e) {}
        }
      }
      return false;
    }"""
    for frame in _ordered_frames(page):
        try:
            if bool(frame.evaluate(js_geo_pick, target)):
                return True
        except Exception:
            continue
    return False


def _fill_relation_fields_verified(
    page: Page,
    *,
    relation: str,
    relation_name: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
) -> tuple[bool, bool]:
    """
    Fill relation type + Relation's Name and verify values stuck in the UI.
    Returns ``(relation_type_filled, relation_name_filled)``.
    """
    rel = (relation or "").strip()
    nm = (relation_name or "").strip()

    # User-requested mode: fill only Relation's Name on the opened customer record and skip relation type.
    def _fill_relation_name_on_opened_customer_form() -> bool:
        """
        Strictly target the exact label text ``Relation's Name`` on the opened customer record.
        Avoids writing into short-caps/other relation fields.
        """
        set_on_form_js = """(nmValue) => {
          const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width >= 2 && r.height >= 2;
          };
          const txt = (s) => String(s || '').trim().toLowerCase();
          const labels = Array.from(document.querySelectorAll('label,span,div,td,th')).filter(vis);
          const relLbl = labels.find(el => {
            const t = txt(el.innerText || '');
            return t === "relation's name" || t === "relation's name:";
          });
          if (!relLbl) return false;
          // Ensure this is the customer details form (has first-name context nearby).
          const hasFirstNameContext = labels.some(el => txt(el.innerText || '').includes('first name'));
          if (!hasFirstNameContext) return false;

          const lr = relLbl.getBoundingClientRect();
          const candidates = Array.from(document.querySelectorAll('input[type="text"],input,textarea')).filter(vis);
          let best = null;
          let bestScore = 1e9;
          for (const c of candidates) {
            const r = c.getBoundingClientRect();
            const dy = Math.abs((r.top + r.height / 2) - (lr.top + lr.height / 2));
            const dx = r.left - lr.right;
            if (dy > 24) continue;
            if (dx < -10 || dx > 420) continue;
            const score = Math.max(dx, 0) + dy * 7;
            if (score < bestScore) { bestScore = score; best = c; }
          }
          if (!best) return false;
          try {
            best.focus();
            best.value = '';
            best.value = String(nmValue || '').trim();
            best.dispatchEvent(new Event('input', { bubbles: true }));
            best.dispatchEvent(new Event('change', { bubbles: true }));
            best.dispatchEvent(new Event('blur', { bubbles: true }));
            return true;
          } catch (e) {
            return false;
          }
        }"""
        for frame in _ordered_frames(page):
            try:
                if bool(frame.evaluate(set_on_form_js, nm)):
                    return True
            except Exception:
                continue
        return False

    name_fill_attempted = False
    if nm:
        name_fill_attempted = _fill_relation_name_on_opened_customer_form()
    if nm and not name_fill_attempted:
        # Exact selector fallback only (no Father/Husband short-caps fields).
        name_fill_attempted = _try_fill_field(
            page,
            [
                "input[title*=\"Relation's Name\" i]",
                "input[aria-label*=\"Relation's Name\" i]",
                "input[title*=\"Relation Name\" i]",
                "input[aria-label*=\"Relation Name\" i]",
            ],
            nm[:255],
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            prefer_second_if_duplicate=True,
        )
    _safe_page_wait(page, 180, log_label="after_relation_name_fill")
    _safe_page_wait(page, 220, log_label="after_relation_name_only_attempt")

    rel_key = re.sub(r"[^A-Z]", "", rel.upper())  # S/O -> SO
    nm_key = re.sub(r"\s+", " ", nm).strip().lower()

    verify_js = """(relKey, nmKey) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const norm = (s) => String(s || '').replace(/[^a-z]/gi, '').toUpperCase();
      let relOk = !relKey;
      let nameOk = !nmKey;

      const relSelects = Array.from(document.querySelectorAll(
        'select[title*="S/O" i],select[aria-label*="S/O" i],select[title*="W/O" i],select[aria-label*="W/O" i],select[aria-label*="Relation" i],select[title*="Relation" i],select'
      )).filter(vis);
      for (const s of relSelects) {
        try {
          const idx = s.selectedIndex;
          const tx = idx >= 0 ? ((s.options[idx] || {}).textContent || '') : '';
          if (relKey && norm(tx).includes(relKey)) { relOk = true; break; }
        } catch (e) {}
      }

      const nameInputs = Array.from(document.querySelectorAll(
        'input[title*="Relation\\'s Name" i],input[aria-label*="Relation\\'s Name" i],input[title*="Relation Name" i],input[aria-label*="Relation Name" i],input[aria-label*="Father" i],input[aria-label*="Husband" i],input[type="text"]'
      )).filter(vis);
      for (const i of nameInputs) {
        try {
          const v = String(i.value || '').trim().toLowerCase();
          if (nmKey && v && (v.includes(nmKey) || nmKey.includes(v))) { nameOk = true; break; }
        } catch (e) {}
      }
      return { relOk, nameOk };
    }"""

    rel_ok = True  # intentionally skipped by request
    name_ok = (not nm) or bool(name_fill_attempted)
    for frame in _ordered_frames(page):
        try:
            got = frame.evaluate(verify_js, rel_key, nm_key)
            rel_ok = rel_ok or bool((got or {}).get("relOk"))
            name_ok = name_ok or bool((got or {}).get("nameOk"))
            if rel_ok and name_ok:
                return True, True
        except Exception:
            continue

    # DOM force-set fallback for Relation's Name only (label proximity).
    set_js = """(nmValue) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const txt = (s) => String(s || '').trim().toLowerCase();
      let nameSet = false;

      const allTextNodes = Array.from(document.querySelectorAll('label,span,div,td,th')).filter(vis);
      const relNameLabel = allTextNodes.find(el => {
        const t = txt(el.innerText || '');
        return t === "relation's name" || t === "relation's name:";
      });

      const nearestControlRight = (labelEl, selector) => {
        if (!labelEl) return null;
        const lr = labelEl.getBoundingClientRect();
        const candidates = Array.from(document.querySelectorAll(selector)).filter(vis);
        let best = null;
        let bestScore = 1e9;
        for (const c of candidates) {
          const r = c.getBoundingClientRect();
          const dy = Math.abs((r.top + r.height / 2) - (lr.top + lr.height / 2));
          const dx = r.left - lr.right;
          if (dx < -18 || dx > 380) continue;
          if (dy > 60) continue;
          const score = Math.max(dx, 0) + dy * 2;
          if (score < bestScore) { bestScore = score; best = c; }
        }
        return best;
      };
      const nearestInSameRow = (labelEl, selector) => {
        if (!labelEl) return null;
        const lr = labelEl.getBoundingClientRect();
        const candidates = Array.from(document.querySelectorAll(selector)).filter(vis);
        let best = null;
        let bestScore = 1e9;
        for (const c of candidates) {
          const r = c.getBoundingClientRect();
          const dy = Math.abs((r.top + r.height / 2) - (lr.top + lr.height / 2));
          const dx = r.left - lr.right;
          if (dy > 22) continue;
          if (dx < -12 || dx > 420) continue;
          const score = Math.max(dx, 0) + dy * 6;
          if (score < bestScore) { bestScore = score; best = c; }
        }
        return best;
      };

      if (nmValue) {
        let nameInput = nearestInSameRow(
          relNameLabel,
          'input[type="text"],input,textarea'
        );
        if (!nameInput) {
          nameInput = nearestControlRight(relNameLabel, 'input[type="text"],input,textarea');
        }
        if (nameInput) {
          try {
            nameInput.focus();
            nameInput.value = '';
            nameInput.value = String(nmValue).trim();
            nameInput.dispatchEvent(new Event('input', { bubbles: true }));
            nameInput.dispatchEvent(new Event('change', { bubbles: true }));
            nameInput.dispatchEvent(new Event('blur', { bubbles: true }));
            nameSet = true;
          } catch (e) {}
        }
      } else {
        nameSet = true;
      }

      return { nameSet };
    }"""
    for frame in _ordered_frames(page):
        try:
            frame.evaluate(set_js, nm)
        except Exception:
            continue
    _safe_page_wait(page, 300, log_label="after_relation_name_dom_fallback")

    # Verify once more
    for frame in _ordered_frames(page):
        try:
            got = frame.evaluate(verify_js, "", nm_key)
            rel_ok = rel_ok or bool((got or {}).get("relOk"))
            name_ok = name_ok or bool((got or {}).get("nameOk"))
            if rel_ok and name_ok:
                break
        except Exception:
            continue
    return rel_ok, name_ok


def _fill_relations_name_exact(
    page: Page,
    *,
    relation_name: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """
    Exact behavior: fill only the field labeled/titled ``Relation's Name`` and verify that same field.
    """
    v = (relation_name or "").strip()
    if not v:
        return False

    # Single deterministic JS: label(text) -> nearest right input/textarea -> set -> verify same control value.
    set_and_verify_js = """(value) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const norm = (s) => String(s || '').trim().toLowerCase();
      const labelNorm = (txt) => norm(txt).replace(/\\s+/g, ' ').replace(/\\s*:\\s*$/g, '');

      const labels = Array.from(document.querySelectorAll('td,th,label,span,div')).filter(vis);
      const relLabel = labels.find(el => {
        const t = labelNorm(el.innerText || el.textContent || '');
        return t === \"relation's name\" || t.includes(\"relation's name\");
      });
      if (!relLabel) return { ok: false, reason: 'label_not_found' };

      const row =
        relLabel.closest('tr') ||
        relLabel.closest('[role="row"]') ||
        null;
      const lr = relLabel.getBoundingClientRect();
      const candidatesSource = row ? row : document;
      const candidates = Array.from(candidatesSource.querySelectorAll('input,textarea')).filter(vis);

      let best = null;
      let bestScore = 1e18;
      for (const el of candidates) {
        // Skip button-like/submit-ish inputs.
        try {
          const t = (el.getAttribute('type') || '').toLowerCase();
          if (t && ['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'image'].includes(t)) continue;
        } catch (e) {}

        const r = el.getBoundingClientRect();
        const dy = Math.abs((r.top + r.height / 2) - (lr.top + lr.height / 2));
        const dx = r.left - lr.right;
        // Same visual row: allow some tolerance.
        if (dy > 40) continue;
        // Must be to the right of the label.
        if (dx < -12) continue;
        // Prefer closest-right and vertically aligned.
        const score = dx + dy * 6;
        if (score < bestScore) {
          bestScore = score;
          best = el;
        }
      }
      if (!best) return { ok: false, reason: 'target_input_not_found' };

      try {
        best.focus();
        // Clear then set; some Siebel controls need blur to commit.
        if ('value' in best) {
          best.value = '';
          best.value = String(value || '').trim();
        }
        best.dispatchEvent(new Event('input', { bubbles: true }));
        best.dispatchEvent(new Event('change', { bubbles: true }));
        best.dispatchEvent(new Event('blur', { bubbles: true }));
      } catch (e) {
        return { ok: false, reason: 'set_failed' };
      }

      try {
        const after = norm(best.value || '');
        const want = norm(value || '');
        if (!after) return { ok: false, reason: 'value_empty_after' };
        const ok = after.includes(want) || want.includes(after);
        return { ok, after, want };
      } catch (e) {
        return { ok: false, reason: 'verify_failed' };
      }
    }"""

    for frame in _ordered_frames(page):
        try:
            res = frame.evaluate(set_and_verify_js, v)
            if isinstance(res, dict) and res.get("ok"):
                _safe_page_wait(page, 150, log_label="after_relation_name_exact_js_ok")
                return True
        except Exception:
            continue
    return False


# First Name inputs in Contact Find fly-in / applet (mobile + first name search).
_SIEBEL_FIND_FIRST_NAME_SELECTORS: tuple[str, ...] = (
    'input#field_textbox_1',
    'input[id="field_textbox_1"]',
    'input[aria-label*="First Name" i]',
    'input[title*="First Name" i]',
    'input[name*="FirstName" i]',
)

# Values rejected for Contact Find — must be a real first name (video SOP / §6.1a gate).
_SIEBEL_FIRST_NAME_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "",
        "na",
        "n/a",
        "n.a.",
        "null",
        "none",
        "-",
        "--",
        ".",
        "..",
        "...",
        "tbd",
        "pending",
    }
)


def _validate_contact_find_first_name(raw: str) -> tuple[bool, str]:
    """
    Contact Find requires **mobile + first name**. Rejects empty/whitespace and common placeholders.
    Returns ``(ok, error_message)`` — ``error_message`` empty when ``ok``.
    """
    s = (raw or "").strip()
    if not s:
        return False, (
            "Siebel: Contact First Name is required for Find (mobile + first name) but is empty or whitespace."
        )
    low = s.lower()
    if low in _SIEBEL_FIRST_NAME_PLACEHOLDERS:
        return False, f"Siebel: Contact First Name is a placeholder ({s!r}); cannot run Find."
    if all(c == "." for c in s):
        return False, f"Siebel: Contact First Name is invalid ({s!r}); cannot run Find."
    return True, ""


def _first_name_for_contact_find_query_field(raw: str) -> str:
    """
    Value typed into Siebel Contact Find **First Name**: **exact** string (no ``*`` wildcard).

    Trailing dots from dotted duplicate keys are stripped so the typed value matches the dotted
    re-find path; the grid matcher uses the same normalization (case-insensitive exact equality).
    """
    s = (raw or "").strip()
    if not s:
        return ""
    while s.endswith("."):
        s = s[:-1].strip()
    return s


def _mobile_needle_for_contact_grid_match(mobile: str) -> str:
    """
    Prefer full **10-digit** tail for contact **list/grid** matching (fewer false positives
    than an 8-digit substring). Falls back to 8+ digits when the number is shorter.
    """
    d = re.sub(r"\D", "", (mobile or "").strip())
    if len(d) >= 10:
        return d[-10:]
    return d if len(d) >= 8 else ""


def _siebel_ui_suggests_contact_match(page: Page, mobile: str) -> bool:
    """
    After Find/Go on Contact, detect a **search hit** only when the mobile appears in a
    **table result row** (≥3 ``td``), not in the Find query field (which still holds the number).

    If no table hit → treat as new contact / full enquiry form. Div-based grids with no
    ``table`` may false-negative here; tune iframe/DOM or extend heuristics if needed.
    """
    needle = _mobile_needle_for_contact_grid_match(mobile)
    if not needle:
        return False
    script = """(needle) => {
      if (!needle || needle.length < 8) return false;
      const compact = (s) => String(s).replace(/\\s+/g, '');
      const has = (s) => compact(s).includes(needle);
      for (const tr of document.querySelectorAll('table tbody tr')) {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 3) continue;
        const text = (tr.innerText || '').replace(/\\s+/g, '');
        if (has(text)) return true;
      }
      return false;
    }"""
    for frame in _ordered_frames(page):
        try:
            if frame.evaluate(script, needle):
                return True
        except Exception:
            continue
    return False


def _siebel_ui_suggests_contact_match_mobile_first(page: Page, mobile: str, first_name: str) -> bool:
    """
    After Find/Go with **mobile + first name**, true when some data row ``tr`` has the mobile in the
    row's **textContent** (compact) and either the first name is detectable on the row **or** the
    **Title column** ``td`` (the cell under the Title heading containing ``a[name="Title"]``) contains
    the mobile digits — Siebel often renders the number there while omitting the first name from the DOM.

    Matching is **case-insensitive** on first name; mobile stays digit-based.
    """
    needle = _mobile_needle_for_contact_grid_match(mobile)
    target = (first_name or "").strip()
    if not needle or not target:
        return False
    script = """([needle, target]) => {
      if (!needle || needle.length < 8 || !target) return false;
      const compact = (s) => String(s || '').replace(/\\s+/g, '');
      const norm = (s) => String(s || '').replace(/\\u00a0/g, ' ').trim();
      const firstNameKeyFromFind = (raw) => {
        let s = String(raw || '').replace(/\\u00a0/g, ' ').trim().toLowerCase();
        while (s.endsWith('.')) s = s.slice(0, -1).trim();
        return s;
      };
      const textMatchesFindFirstName = (text, keyBase) => {
        if (!keyBase || text == null) return false;
        const c = String(text).replace(/\\u00a0/g, ' ').trim().toLowerCase();
        if (!c) return false;
        if (c === keyBase) return true;
        if (c.startsWith(keyBase + ' ')) return true;
        const keyHead = keyBase.split(/\\s+/).filter(Boolean)[0] || '';
        if (keyHead && c === keyHead) return true;
        const first = c.split(/\\s+/).filter(Boolean)[0] || '';
        let fs = first;
        while (fs.endsWith('.')) fs = fs.slice(0, -1).trim();
        if (fs === keyBase) return true;
        if (keyHead && fs === keyHead) return true;
        return false;
      };
      const rowContainsFindFirstKey = (tr, keyBase) => {
        if (!keyBase) return false;
        const keyHead = keyBase.split(/\\s+/).filter(Boolean)[0] || '';
        const raw = norm(tr.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        if (!raw || (!raw.includes(keyBase) && !(keyHead && raw.includes(keyHead)))) return false;
        if (raw.startsWith(keyBase + ' ')) return true;
        if (keyHead && raw.startsWith(keyHead + ' ')) return true;
        const parts = raw.split(/[\\s,;|\\/\\u2013\\u2014-]+/).filter(Boolean);
        for (const p of parts) {
          let q = p;
          while (q.endsWith('.')) q = q.slice(0, -1).trim();
          if (q === keyBase || (keyHead && q === keyHead)) return true;
          if (p.startsWith(keyBase + ' ')) return true;
          if (keyHead && p.startsWith(keyHead + ' ')) return true;
        }
        return false;
      };
      const keyBase = firstNameKeyFromFind(target);
      if (!keyBase) return false;
      const hasM = (s) => compact(s).includes(needle);
      const rowHasFirst = (tr) => {
        const tds = tr.querySelectorAll('td');
        for (const td of tds) {
          if (textMatchesFindFirstName(td.textContent, keyBase)) return true;
          if (textMatchesFindFirstName(td.getAttribute('title') || '', keyBase)) return true;
          if (textMatchesFindFirstName(td.getAttribute('aria-label') || '', keyBase)) return true;
          for (const inp of td.querySelectorAll('input, textarea')) {
            if (textMatchesFindFirstName(inp.value, keyBase)) return true;
          }
        }
        return rowContainsFindFirstKey(tr, keyBase);
      };
      for (const tr of document.querySelectorAll('table tr')) {
        if (tr.closest('thead')) continue;
        const tds = tr.querySelectorAll('td');
        if (tds.length < 3) continue;
        const rowBody = tr.textContent || '';
        if (!hasM(rowBody)) continue;
        if (rowHasFirst(tr)) return true;
      }
      return false;
    }"""
    # #region agent log - mobile+first matcher diagnostics
    def _dbg_mf(hypothesis_id: str, message: str, data: dict) -> None:
        try:
            import json as _j_mf, time as _t_mf
            from pathlib import Path as _p_mf
            _log_path = _p_mf(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_log_path, "a", encoding="utf-8") as _lf_mf:
                _lf_mf.write(
                    _j_mf.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "post-fix",
                            "hypothesisId": hypothesis_id,
                            "location": "hero_dms_playwright_customer.py:_siebel_ui_suggests_contact_match_mobile_first",
                            "message": message,
                            "data": data,
                            "timestamp": _ts_ist_iso(),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
    _dbg_mf(
        "M1",
        "match_entry",
        {"needle": needle, "target_first_name": target},
    )
    diag_js = """([needle, target]) => {
      const compact = (s) => String(s || '').replace(/\\s+/g, '');
      const norm = (s) => String(s || '').replace(/\\u00a0/g, ' ').trim();
      const firstNameKeyFromFind = (raw) => {
        let s = String(raw || '').replace(/\\u00a0/g, ' ').trim().toLowerCase();
        while (s.endsWith('.')) s = s.slice(0, -1).trim();
        return s;
      };
      const textMatchesFindFirstName = (text, keyBase) => {
        if (!keyBase || text == null) return false;
        const c = String(text).replace(/\\u00a0/g, ' ').trim().toLowerCase();
        if (!c) return false;
        if (c === keyBase) return true;
        if (c.startsWith(keyBase + ' ')) return true;
        const keyHead = keyBase.split(/\\s+/).filter(Boolean)[0] || '';
        if (keyHead && c === keyHead) return true;
        const first = c.split(/\\s+/).filter(Boolean)[0] || '';
        let fs = first;
        while (fs.endsWith('.')) fs = fs.slice(0, -1).trim();
        if (fs === keyBase) return true;
        if (keyHead && fs === keyHead) return true;
        return false;
      };
      const rowContainsFindFirstKey = (tr, keyBase) => {
        if (!keyBase) return false;
        const keyHead = keyBase.split(/\\s+/).filter(Boolean)[0] || '';
        const raw = norm(tr.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        if (!raw || (!raw.includes(keyBase) && !(keyHead && raw.includes(keyHead)))) return false;
        if (raw.startsWith(keyBase + ' ')) return true;
        if (keyHead && raw.startsWith(keyHead + ' ')) return true;
        const parts = raw.split(/[\\s,;|\\/\\u2013\\u2014-]+/).filter(Boolean);
        for (const p of parts) {
          let q = p;
          while (q.endsWith('.')) q = q.slice(0, -1).trim();
          if (q === keyBase || (keyHead && q === keyHead)) return true;
          if (p.startsWith(keyBase + ' ')) return true;
          if (keyHead && p.startsWith(keyHead + ' ')) return true;
        }
        return false;
      };
      const keyBase = firstNameKeyFromFind(target);
      const rowHasFirst = (tr) => {
        if (!keyBase) return false;
        const tds = tr.querySelectorAll('td');
        for (const td of tds) {
          if (textMatchesFindFirstName(td.textContent, keyBase)) return true;
          if (textMatchesFindFirstName(td.getAttribute('title') || '', keyBase)) return true;
          if (textMatchesFindFirstName(td.getAttribute('aria-label') || '', keyBase)) return true;
          for (const inp of td.querySelectorAll('input, textarea')) {
            if (textMatchesFindFirstName(inp.value, keyBase)) return true;
          }
        }
        return rowContainsFindFirstKey(tr, keyBase);
      };
      const out = { table_rows_seen: 0, mobile_rows_seen: 0, first_resolved_rows_seen: 0, sample_rows: [] };
      const rows = Array.from(document.querySelectorAll('table tr'));
      for (const tr of rows) {
        if (tr.closest('thead')) continue;
        const tds = tr.querySelectorAll('td');
        if (tds.length < 2) continue;
        out.table_rows_seen += 1;
        const rowFull = String(tr.textContent || '');
        const rowInner = String(tr.innerText || '');
        const hasMobile = needle && compact(rowFull).includes(needle);
        const firstOk = target ? rowHasFirst(tr) : false;
        if (hasMobile) out.mobile_rows_seen += 1;
        if (firstOk) out.first_resolved_rows_seen += 1;
        if (out.sample_rows.length < 3) {
          out.sample_rows.push({
            text_inner: rowInner.slice(0, 180),
            text_content: rowFull.slice(0, 220),
            has_mobile: hasMobile,
            first_resolved: firstOk,
            td_count: tds.length
          });
        }
      }
      return out;
    }"""
    # #endregion
    mobile_only_js = """(needle) => {
      if (!needle || needle.length < 8) return false;
      const compact = (s) => String(s || '').replace(/\\s+/g, '');
      for (const tr of document.querySelectorAll('table tr')) {
        if (tr.closest('thead')) continue;
        const tds = tr.querySelectorAll('td');
        if (tds.length < 3) continue;
        if (compact(tr.textContent || '').includes(needle)) return true;
      }
      return false;
    }"""
    for frame in _ordered_frames(page):
        try:
            try:
                _dbg_mf("M2", "frame_diag", frame.evaluate(diag_js, [needle, target]) or {})
            except Exception:
                _dbg_mf("M2", "frame_diag_eval_failed", {})
            if frame.evaluate(script, [needle, target]):
                _dbg_mf("M3", "match_true_frame", {"matched": True})
                return True
            # Runtime-proven fallback: some Siebel grids expose only mobile under Title/Show Details
            # in row text while first-name cells are not present in DOM.
            if bool(frame.evaluate(mobile_only_js, needle)):
                _dbg_mf("M4", "match_mobile_only_fallback_true", {"matched": True})
                return True
        except Exception:
            continue
    _dbg_mf("M3", "match_false_all_frames", {"matched": False})
    return False


def _contact_list_row_text_hints_enquiry(text: str) -> bool:
    """Same enquiry-hint heuristics as the Contact Find list JS (not a substitute for subgrid read)."""
    if not (text or "").strip():
        return False
    if re.search(r"senq", text, re.I):
        return True
    if re.search(r"enquiry", text, re.I) and re.search(r"\d", text):
        return True
    if re.search(r"\b[0-9]{2,5}-[0-9]{1,3}-[A-Z]{2,6}-[A-Z0-9-]{4,}\b", text, re.I):
        return True
    return False


def _find_contact_mobile_first_grid_counts(
    page: Page,
    mobile: str,
    first_name: str,
    *,
    content_frame_selector: str | None = None,
    cached_plans: list[tuple[object, int, str, int]] | None = None,
) -> tuple[int, int]:
    """
    After Find/Go on Contact: count **drillable** rows for ``mobile`` — same rules as
    ``_contact_find_mobile_drilldown_occurrence_count(..., first_name_exact=None)`` / title sweep ordinals.

    ``first_name`` is ignored for these counts (first name is often absent from list row text).

    Second count: among those rows, how many list texts **hint** at an enquiry (``SENQ``, ``Enquiry`` + digits,
    Siebel-style id). Not a substitute for ``_contact_enquiry_tab_has_rows`` after drilldown.

    Pass **cached_plans** from a single ``_contact_mobile_drilldown_plans(..., first_name_exact=None)`` call
    to avoid rebuilding plans (video path).
    """
    needle = _mobile_needle_for_contact_grid_match(mobile)
    if not needle:
        return 0, 0
    if cached_plans is not None:
        plans = cached_plans
    else:
        plans = _contact_mobile_drilldown_plans(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=None,
        )
    n_match = len(plans)
    n_hint = 0
    for _dr_root, _row_i, _, _ in plans:
        try:
            _row = _dr_root.locator("table tr").nth(_row_i)
            row_text = (_row.inner_text(timeout=800) or "").strip()
        except Exception:
            row_text = ""
        if _contact_list_row_text_hints_enquiry(row_text):
            n_hint += 1
    return n_match, n_hint


def _fill_first_name_in_find_roots(
    page: Page,
    first_name: str,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """Fill First Name in Contact Find pane (any search root / frame) with exact text (no ``*``)."""
    fn = _first_name_for_contact_find_query_field((first_name or "").strip())
    if not fn:
        return False
    for root in list(_siebel_locator_search_roots(page, content_frame_selector)) + list(
        _ordered_frames(page)
    ) + [page]:
        for css in _SIEBEL_FIND_FIRST_NAME_SELECTORS:
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.fill("", timeout=min(3000, action_timeout_ms))
                    loc.fill(fn, timeout=action_timeout_ms)
                    return True
            except Exception:
                continue
        try:
            loc = root.get_by_label(re.compile(r"^\s*First\s*Name\s*$", re.I)).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                loc.fill("", timeout=min(3000, action_timeout_ms))
                loc.fill(fn, timeout=action_timeout_ms)
                return True
        except Exception:
            continue
    return False


def _mobile_text_patterns_for_grid(mobile: str) -> list[re.Pattern[str]]:
    """Match how Hero may show the number in grids (plain, spaced, dashed)."""
    needle = _mobile_needle_for_contact_grid_match(mobile)
    raw = re.sub(r"\D", "", (mobile or "").strip())
    pats: list[re.Pattern[str]] = []
    for chunk in (needle, raw):
        if not chunk or len(chunk) < 8:
            continue
        pats.append(re.compile(re.escape(chunk)))
        if len(chunk) == 10:
            a, b = chunk[:5], chunk[5:]
            pats.append(re.compile(rf"{re.escape(a)}[\s\-]{{0,3}}{re.escape(b)}"))
    # Dedup by pattern string
    seen: set[str] = set()
    out: list[re.Pattern[str]] = []
    for p in pats:
        k = p.pattern
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


_SIEBEL_THIRD_LEVEL_VIEW_BAR_SELECTORS = (
    "select#j_s_vctrl_div_tabScreen",
    '[id="j_s_vctrl_div_tabScreen"]',
    "#s_vctrl_div select#j_s_vctrl_div_tabScreen",
    'select[aria-label="Third Level View Bar"]',
)

# True when this document's Third Level View Bar <select> lists Payments (contact shell), vs a nested
# duplicate ``select#j_s_vctrl_div_tabScreen`` that only exposes Profile/Address subsets.
_SIEBEL_JS_THIRD_LEVEL_BAR_INCLUDES_PAYMENTS_OPTION = """() => {
  const pick = (s) => {
    if (!s || String(s.tagName).toLowerCase() !== 'select') return false;
    for (let i = 0; i < s.options.length; i++) {
      const t = String(s.options[i].textContent || s.options[i].label || '').trim();
      if (/^payments?$/i.test(t)) return true;
      if (/customer\\s+payments?/i.test(t)) return true;
      if (/^payment\\s*details$/i.test(t)) return true;
    }
    return false;
  };
  const a = document.querySelector('#s_vctrl_div select#j_s_vctrl_div_tabScreen');
  if (pick(a)) return true;
  const b = document.querySelector('select#j_s_vctrl_div_tabScreen');
  if (pick(b)) return true;
  const c = document.querySelector('select[aria-label="Third Level View Bar"]');
  return pick(c);
}"""


def _siebel_root_third_level_bar_includes_payments_option(root) -> bool:
    """Prefer the shell document where **Payments** exists in the Third Level combo (not a sub-applet only)."""
    try:
        return bool(_siebel_root_evaluate(root, _SIEBEL_JS_THIRD_LEVEL_BAR_INCLUDES_PAYMENTS_OPTION))
    except Exception:
        return False


def _siebel_ctx_third_level_bar_includes_payments_option(ctx) -> bool:
    """Same as :func:`_siebel_root_third_level_bar_includes_payments_option` for **Page** / **Frame** only."""
    try:
        return bool(ctx.evaluate(_SIEBEL_JS_THIRD_LEVEL_BAR_INCLUDES_PAYMENTS_OPTION))
    except Exception:
        return False


def _siebel_frames_branch2_shell_for_third_level_bar(page: Page) -> list[Frame]:
    """
    **Branch (2)** / Address lineage: from any document that contains ``iframe#S_A1`` or ``[id="S_A1"]``
    (where City / Postal jqGrid is filled), walk :py:attr:`Frame.parent_frame` until a document exposes
    the Third Level View Bar (``#s_vctrl_div select#j_s_vctrl_div_tabScreen`` or bare ``select#j_s_vctrl_div_tabScreen``).

    The tab strip lives **above** the S_A1 grid; this matches the same **frame shift** as postal entry
    (scoped under S_A1) vs chrome (parent shell). Those shells are tried **first** for **Payments** activation.
    """
    _has_sa1 = """() => !!(document.querySelector('iframe#S_A1') || document.querySelector('[id="S_A1"]'))"""
    _has_bar = """() => !!(document.querySelector('#s_vctrl_div select#j_s_vctrl_div_tabScreen')
      || document.querySelector('select#j_s_vctrl_div_tabScreen')
      || document.querySelector('select[aria-label="Third Level View Bar"]'))"""
    out: list[Frame] = []
    seen: set[int] = set()
    main = page.main_frame
    to_scan: list[Frame] = [main]
    try:
        for f in _ordered_frames(page):
            if f != main:
                to_scan.append(f)
    except Exception:
        pass
    for frame in to_scan:
        try:
            if not bool(frame.evaluate(_has_sa1)):
                continue
        except Exception:
            continue
        f: Frame | None = frame
        for _ in range(14):
            if f is None:
                break
            try:
                if bool(f.evaluate(_has_bar)):
                    k = id(f)
                    if k not in seen:
                        seen.add(k)
                        out.append(f)
                    break
            except Exception:
                pass
            try:
                f = f.parent_frame
            except Exception:
                break
    return out


def _siebel_search_roots_payments_third_level_first(
    page: Page, content_frame_selector: str | None
) -> list:
    """
    **Prepend** :func:`_siebel_frames_branch2_shell_for_third_level_bar` (**Frame**\\ s aligned with
    **S_A1** / Address postal scope), then :func:`_siebel_all_search_roots`, deduped by object id.

    Within the merged list, roots whose Third Level bar includes **Payments** sort first (stable).
    """
    b2_shell = _siebel_frames_branch2_shell_for_third_level_bar(page)
    base = list(_siebel_all_search_roots(page, content_frame_selector))
    merged: list = []
    seen: set[int] = set()
    for r in b2_shell:
        k = id(r)
        if k in seen:
            continue
        seen.add(k)
        merged.append(r)
    for r in base:
        k = id(r)
        if k in seen:
            continue
        seen.add(k)
        merged.append(r)
    scored = [
        (0 if _siebel_root_third_level_bar_includes_payments_option(r) else 1, i, r)
        for i, r in enumerate(merged)
    ]
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored]


def _siebel_try_select_payments_third_level_view_bar(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Same control as branch **(2)** Address: ``<select id="j_s_vctrl_div_tabScreen">`` (Third Level View Bar
    combo behind the chevron). On Hero Connect the **Payments** third-level view uses the ``<option>`` whose
    visible label is exactly **Payments** — that string is tried first via ``select_option(label=…)``.

    **Per-label timeout is capped** (``_opt_t``): uncapped waits on many roots × labels could stall for minutes
    when an option is missing. Callers should run :func:`_siebel_js_select_third_level_option_matching` first.
    """
    _cap = min(int(action_timeout_ms), 6000)
    _opt_t = min(1200, max(400, _cap // 3))
    _labels = (
        "Payments",  # canonical operator-visible label
        "Payment",
        "Customer payments",
        "Customer Payments",
        "Payment details",
        "Payment Details",
        "Payment information",
    )
    for root in _siebel_search_roots_payments_third_level_first(page, content_frame_selector):
        for css in _SIEBEL_THIRD_LEVEL_VIEW_BAR_SELECTORS:
            try:
                loc = root.locator(css).first
                if loc.count() == 0:
                    continue
                if not loc.is_visible(timeout=450):
                    continue
                for lbl in _labels:
                    try:
                        loc.select_option(label=lbl, timeout=_opt_t)
                        note(
                            f"Payments: Third Level View Bar — selected {lbl!r} via {css!r} "
                            "(same control as Address tabScreen6 path)."
                        )
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


def _siebel_js_select_third_level_option_matching(
    page: Page,
    *,
    label_regex: str,
    content_frame_selector: str | None,
) -> bool:
    """
    ``evaluate`` in **Frame**\\ s: pick first ``<option>`` whose text matches ``label_regex``.

    Tries :func:`_siebel_frames_branch2_shell_for_third_level_bar` first (same shell as **S_A1** / Address),
    then **page** + :func:`_ordered_frames` (deduped).
    """
    js = """(needle) => {
      let re;
      try { re = new RegExp(needle, 'i'); } catch (e) { return false; }
      const sels = ['select#j_s_vctrl_div_tabScreen', '[id="j_s_vctrl_div_tabScreen"]'];
      for (const sel of sels) {
        const s = document.querySelector(sel);
        if (!s || String(s.tagName).toLowerCase() !== 'select') continue;
        for (let i = 0; i < s.options.length; i++) {
          const o = s.options[i];
          const t = String(o.textContent || o.label || o.value || '').trim();
          if (re.test(t)) {
            s.selectedIndex = i;
            s.dispatchEvent(new Event('input', { bubbles: true }));
            s.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          }
        }
      }
      return false;
    }"""
    raw: list = []
    _seen_raw: set[int] = set()
    for _f in _siebel_frames_branch2_shell_for_third_level_bar(page):
        _k = id(_f)
        if _k not in _seen_raw:
            _seen_raw.add(_k)
            raw.append(_f)
    if id(page) not in _seen_raw:
        _seen_raw.add(id(page))
        raw.append(page)
    try:
        for _fr in _ordered_frames(page):
            _k = id(_fr)
            if _k not in _seen_raw:
                _seen_raw.add(_k)
                raw.append(_fr)
    except Exception:
        pass
    order = sorted(
        range(len(raw)),
        key=lambda i: (0 if _siebel_ctx_third_level_bar_includes_payments_option(raw[i]) else 1, i),
    )
    for i in order:
        ctx = raw[i]
        try:
            if hasattr(ctx, "evaluate") and ctx.evaluate(js, label_regex):
                return True
        except Exception:
            continue
    return False


_SIEBEL_JS_PAYMENTS_SUBVIEW_ALREADY_ACTIVE = """() => {
  try {
    let href = String(location.href || '');
    try { href = decodeURIComponent(href); } catch (e1) {}
    if (/Contact(?:\\+|%2[bB])Site(?:\\+|%2[bB])Payments(?:\\+|%2[bB])View/i.test(href)) {
      return true;
    }
  } catch (e) {}
  const box = document.querySelector('#s_vctrl_div');
  if (!box) return false;
  const st = window.getComputedStyle(box);
  if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
  const r = box.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  if (box.querySelector('li[aria-label*="Payments Selected" i]')) return true;
  const act = box.querySelector('li.ui-tabs-active, li.siebui-active-navtab');
  if (act) {
    const t = (act.innerText || act.textContent || '').replace(/\\s+/g, ' ').trim();
    if (/^Payments?$/i.test(t)) return true;
  }
  const anchors = box.querySelectorAll('a.ui-tabs-anchor[href*="tabScreen_noop"]');
  for (let i = 0; i < anchors.length; i++) {
    const a = anchors[i];
    if (String(a.getAttribute('aria-selected') || '').toLowerCase() !== 'true') continue;
    const t = (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim();
    if (/^Payments?$/i.test(t) || /^Customer\\s+payments?$/i.test(t)) return true;
  }
  return false;
}"""


def _siebel_payments_click_fast_frame_order(page: Page) -> list[Frame]:
    """Main frame first, then a few Siebel-priority iframes (see :func:`_ordered_frames`)."""
    seen: set[int] = set()
    out: list[Frame] = []
    main = page.main_frame
    seen.add(id(main))
    out.append(main)
    try:
        for f in _ordered_frames(page):
            if id(f) in seen:
                continue
            seen.add(id(f))
            out.append(f)
            if len(out) >= 6:
                break
    except Exception:
        pass
    return out


def _siebel_payments_subview_already_active(
    page: Page, *, content_frame_selector: str | None
) -> bool:
    """
    True when ``location`` already shows **Contact Site Payments** and/or **Payments** is selected under
    visible ``#s_vctrl_div``. Tries main frame then the same small iframe list as
    :func:`_siebel_payments_click_fast_frame_order`. ``content_frame_selector`` is accepted for parity
    with callers; explicit chained roots are still handled by the full Payments click / select sweep.
    """
    _ = content_frame_selector
    for frame in _siebel_payments_click_fast_frame_order(page):
        try:
            if bool(frame.evaluate(_SIEBEL_JS_PAYMENTS_SUBVIEW_ALREADY_ACTIVE)):
                return True
        except Exception:
            continue
    return False


_SIEBEL_S_VCTRL_SUBVIEW_NAV_SELECTORS = (
    # Operator-confirmed: Payments lives under subview nav (same strip as Third Level tabs).
    "div#s_vctrl_div.siebui-nav-tab.siebui-subview-navs",
    "div#s_vctrl_div.siebui-subview-navs",
    "#s_vctrl_div.siebui-subview-navs",
    "#s_vctrl_div.siebui-nav-tab",
    "#s_vctrl_div",
)


def _siebel_try_click_payments_tab_under_s_vctrl(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Click **Payments** under the Siebel view-control strip: prefer
    ``div#s_vctrl_div.siebui-nav-tab.siebui-subview-navs`` (and variants), not only bare ``#s_vctrl_div`` —
    multiple ``#s_vctrl_div`` nodes may exist; ``.first`` can target a hidden clone.
    """
    t = min(int(action_timeout_ms), 6000)
    pay_pat = re.compile(r"^\s*Payments?\s*$", re.I)
    pay_loose = re.compile(r"^\s*Payments?\s*$|Customer\s+payments?|Payment\s+details", re.I)

    def _try_in_vctrl_box(vbox, *, log_tag: str) -> bool:
        if vbox.count() == 0:
            return False
        vc = vbox.first
        try:
            if not vc.is_visible(timeout=400):
                return False
        except Exception:
            return False
        for css in (
            'a.ui-tabs-anchor[href*="s_vctrl_div_tabScreen_noop"]:has-text("Payments")',
            'a.ui-tabs-anchor[href*="tabScreen_noop"]:has-text("Payments")',
            'a.ui-tabs-anchor:has-text("Payments")',
            'a.ui-tabs-anchor:has-text("Payment")',
            'a[href*="tabScreen_noop"]:has-text("Payments")',
            'a[href*="tabScreen"]:has-text("Payments")',
            'a:has-text("Payments")',
            'a:has-text("Payment")',
        ):
            try:
                loc = vc.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=550):
                    try:
                        loc.click(timeout=t)
                    except Exception:
                        loc.click(timeout=t, force=True)
                    note(f"Payments: clicked Payments tab ({log_tag}, css={css[:56]!r}).")
                    return True
            except Exception:
                continue
        for role in ("tab", "link", "button"):
            try:
                loc = vc.get_by_role(role, name=pay_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=550):
                    try:
                        loc.click(timeout=t)
                    except Exception:
                        loc.click(timeout=t, force=True)
                    note(f"Payments: clicked Payments tab ({log_tag}, role={role}).")
                    return True
            except Exception:
                continue
        try:
            loc = vc.locator("a.ui-tabs-anchor").filter(has_text=pay_pat).first
            if loc.count() > 0 and loc.is_visible(timeout=550):
                try:
                    loc.click(timeout=t)
                except Exception:
                    loc.click(timeout=t, force=True)
                note(f"Payments: clicked Payments tab ({log_tag}, ui-tabs-anchor filter).")
                return True
        except Exception:
            pass
        try:
            loc = vc.get_by_text(re.compile(r"^\s*Payments\s*$", re.I)).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                try:
                    loc.click(timeout=t)
                except Exception:
                    loc.click(timeout=t, force=True)
                note(f"Payments: clicked Payments tab ({log_tag}, get_by_text).")
                return True
        except Exception:
            pass
        try:
            loc = vc.locator("a, span, li, [role='tab']").filter(has_text=pay_loose).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                try:
                    loc.click(timeout=t)
                except Exception:
                    loc.click(timeout=t, force=True)
                note(f"Payments: clicked Payments tab ({log_tag}, broad text filter).")
                return True
        except Exception:
            pass
        return False

    _js_click_payments_in_subview = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const boxes = [];
      for (const sel of [
        'div#s_vctrl_div.siebui-nav-tab.siebui-subview-navs',
        'div#s_vctrl_div.siebui-subview-navs',
        '#s_vctrl_div.siebui-subview-navs',
        '#s_vctrl_div'
      ]) {
        document.querySelectorAll(sel).forEach((n) => boxes.push(n));
      }
      const seen = new Set();
      const payRe = /^\\s*Payments?\\s*$/i;
      const custRe = /^\\s*Customer\\s+payments?\\s*$/i;
      for (const box of boxes) {
        if (seen.has(box)) continue;
        seen.add(box);
        if (!vis(box)) continue;
        const cand = box.querySelectorAll('a, [role="tab"], button, .siebui-btn-text, span');
        for (const el of cand) {
          if (!vis(el)) continue;
          const tx = (el.innerText || el.textContent || '').trim();
          if (payRe.test(tx) || custRe.test(tx) || /^Payment\\s+details$/i.test(tx)) {
            try { el.click(); return true; } catch (e) {}
          }
        }
      }
      return false;
    }"""

    def _scan_root_for_payments_vctrl(root, *, sweep_tag: str) -> bool:
        for vsel in _SIEBEL_S_VCTRL_SUBVIEW_NAV_SELECTORS:
            try:
                vwrap = root.locator(vsel)
                n = vwrap.count()
                if n == 0:
                    continue
                _lim = min(n, 8)
                for j in range(_lim):
                    if _try_in_vctrl_box(vwrap.nth(j), log_tag=f"{vsel}@[{j}]"):
                        return True
            except Exception:
                continue
        try:
            if bool(_siebel_root_evaluate(root, _js_click_payments_in_subview)):
                note(
                    "Payments: clicked Payments tab (JS fallback in subview #s_vctrl_div"
                    f", {sweep_tag})."
                )
                return True
        except Exception:
            pass
        return False

    _fast_frames = _siebel_payments_click_fast_frame_order(page)
    _fast_frame_ids = {id(f) for f in _fast_frames}
    for root in _fast_frames:
        if _scan_root_for_payments_vctrl(root, sweep_tag="fast-main-then-iframes"):
            return True

    for root in _siebel_search_roots_payments_third_level_first(page, content_frame_selector):
        if isinstance(root, Frame) and id(root) in _fast_frame_ids:
            continue
        if _scan_root_for_payments_vctrl(root, sweep_tag="full-sweep"):
            return True
    return False


def _siebel_try_activate_payments_tab(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Open the **Payments** view before Payment Lines automation.

    Order (latency-tuned): **JS** ``select`` option match in page/frames first (cheap ``evaluate``), then
    Playwright ``select_option`` on the Third Level bar (capped per-label timeout), then **#s_vctrl_div**
    **Payments** anchors, then frame-wide tab/link name patterns.

    When the **Contact Site Payments** view is already active (URL and/or selected **Payments** under
    ``#s_vctrl_div``), skips **select** churn and redundant tab clicks.
    """
    if _siebel_payments_subview_already_active(
        page, content_frame_selector=content_frame_selector
    ):
        try:
            note(
                "Payments: subview already active (URL or #s_vctrl_div) — skipping tab activation."
            )
        except Exception:
            pass
        return True
    try:
        note("Payments: activating tab — trying JS Third Level View Bar match (fast), then select_option.")
    except Exception:
        pass
    if _siebel_js_select_third_level_option_matching(
        page,
        label_regex=r"^Payments$|^Payment$|Customer\s+payments?|Payment\s+details",
        content_frame_selector=content_frame_selector,
    ):
        try:
            note(
                "Payments: Third Level View Bar — selected Payments option via JS option-text match."
            )
        except Exception:
            pass
        _safe_page_wait(page, 500, log_label="after_payments_third_level_js")
        return True
    if _siebel_try_select_payments_third_level_view_bar(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        _safe_page_wait(page, 500, log_label="after_payments_third_level_select")
        return True
    if _siebel_try_click_payments_tab_under_s_vctrl(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        _safe_page_wait(page, 500, log_label="after_payments_s_vctrl_anchor")
        return True
    _patterns: tuple[re.Pattern[str], ...] = (
        re.compile(r"^Payments?$", re.I),
        re.compile(r"^Payment details$", re.I),
        re.compile(r"^Customer payments?$", re.I),
        re.compile(r"^Payment information$", re.I),
        re.compile(r"^Payment$", re.I),
        re.compile(r"\bPayment\b", re.I),
    )
    for pat in _patterns:
        if _siebel_try_click_named_in_frames(
            page,
            pat,
            roles=("tab", "link"),
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        ):
            try:
                note(f"Payments tab click matched pattern={pat.pattern!r}.")
            except Exception:
                pass
            return True
    return False


def _siebel_try_click_named_in_frames(
    page: Page,
    pattern: re.Pattern[str],
    *,
    roles: tuple[str, ...],
    timeout_ms: int,
    content_frame_selector: str | None,
    max_candidates: int = 14,
) -> bool:
    """Click first visible control in any Siebel frame whose accessible name matches ``pattern``."""

    def try_root(root) -> bool:
        for role in roles:
            try:
                loc = root.get_by_role(role, name=pattern)
                n = loc.count()
                for i in range(min(n, max_candidates)):
                    try:
                        c = loc.nth(i)
                        if c.is_visible(timeout=500):
                            c.click(timeout=timeout_ms)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        if try_root(fl):
            return True
    for frame in _ordered_frames(page):
        if try_root(frame):
            return True
    return False


def _eval_mobile_search_hit_ready(
    page: Page,
    mobile: str,
    *,
    content_frame_selector: str | None,
) -> bool:
    """
    Single-pass check: left pane / grid shows a visible drilldown candidate for ``mobile``.
    Shared by :func:`_wait_for_mobile_search_hit_ready` and bounded post–Find/Go waits.
    Uses :func:`_iter_mobile_search_hit_roots` so **DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_*** can prioritize
    the iframe that holds Search Results.
    """
    needle = _mobile_needle_for_contact_grid_match(mobile)
    raw_digits = re.sub(r"\D", "", (mobile or "").strip())
    if not needle and not raw_digits:
        return False
    _js = """(args) => {
      const needle = String(args.needle || '');
      const raw = String(args.raw || '');
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      };
      const d = (s) => String(s || '').replace(/\\D/g, '');
      for (const a of document.querySelectorAll("a")) {
        if (!vis(a)) continue;
        const txt = (a.innerText || a.textContent || '').trim();
        if (!txt) continue;
        const dd = d(txt);
        if ((needle && dd.includes(needle)) || (raw.length >= 8 && dd.includes(raw))) return true;
      }
      for (const tr of document.querySelectorAll("table tbody tr, tr[role='row']")) {
        if (!vis(tr)) continue;
        const dd = d(tr.textContent || '');
        if ((needle && dd.includes(needle)) || (raw.length >= 8 && dd.includes(raw))) return true;
      }
      return false;
    }"""
    for root in _iter_mobile_search_hit_roots(page, content_frame_selector):
        try:
            if not bool(root.evaluate(_js, {"needle": needle, "raw": raw_digits})):
                continue
            return True
        except Exception:
            continue
    return False


def _wait_for_mobile_search_hit_ready(
    page: Page,
    mobile: str,
    *,
    content_frame_selector: str | None,
    wait_ms: int,
) -> bool:
    """
    Wait until left Search Results has a visible drilldown candidate for ``mobile``.
    Pure wait-strategy helper; does not click.
    """
    start_t = time.monotonic()
    deadline = start_t + max(0.2, wait_ms / 1000.0)
    while time.monotonic() < deadline:
        if _eval_mobile_search_hit_ready(page, mobile, content_frame_selector=content_frame_selector):
            return True
        _safe_page_wait(page, 120, log_label="wait_mobile_hit_ready")
    return False


def _contact_find_after_go_wait_bounded(
    page: Page,
    mobile: str,
    *,
    content_frame_selector: str | None,
    note,
) -> None:
    """
    Strategy 1: after Contact Find/Go, wait up to **2000 ms** in slices **400 + 800 + 800 ms**,
    exiting early when :func:`_eval_mobile_search_hit_ready` is true (replaces a single 2000 ms sleep).
    """
    for i, ms in enumerate((400, 800, 800)):
        _safe_page_wait(page, ms, log_label=f"after_contact_find_go_slice_{i + 1}_of_3")
        hit = _eval_mobile_search_hit_ready(page, mobile, content_frame_selector=content_frame_selector)
        if hit:
            note(
                "Contact Find: Search Results mobile hit visible — "
                "ending post–Find/Go bounded wait early (strategy 1)."
            )
            return
    note("Contact Find: post–Find/Go bounded wait completed (2000 ms max, strategy 1).")


def _after_left_customer_click_wait_bounded(
    page: Page,
    *,
    content_frame_selector: str | None,
    note,
    first_name: str | None = None,
) -> None:
    """
    Strategy 1: after left Search Results drill-in click, wait up to **1000 ms** in slices
    **200 + 400 + 400 ms**, exiting early when contact detail fields are ready
    (replaces a single 1000 ms sleep).
    """
    for i, ms in enumerate((200, 400, 400)):
        _safe_page_wait(page, ms, log_label=f"after_left_customer_click_slice_{i + 1}_of_3")
        ready = _wait_for_contact_detail_ready(
            page,
            content_frame_selector=content_frame_selector,
            wait_ms=200,
            first_name=first_name,
        )
        if ready:
            note(
                "Opened customer: contact detail fields visible — "
                "ending post–click bounded wait early (strategy 1)."
            )
            return
    note("Opened customer: post–click bounded wait completed (1000 ms max, strategy 1).")


def _contacts_applet_first_name_drill_target_visible(root, first_name: str) -> bool:
    """
    Read-only probe aligned with :func:`_siebel_open_found_customer_record` ``try_root``:
    **Contacts** applet shows the first-name **link** or **cell** the drill will click.
    """
    fn = (first_name or "").strip()
    if not fn:
        return False
    fn_pat = re.compile(rf"^\s*{re.escape(fn)}\s*$", re.I)
    try:
        apps = root.locator(".siebui-applet").filter(has_text=re.compile(r"Contacts", re.I))
        n_apps = apps.count()
        for aidx in range(min(n_apps, 6)):
            app = apps.nth(aidx)
            if not (app.count() > 0 and app.is_visible(timeout=500)):
                continue
            try:
                lnk = app.get_by_role("link", name=fn_pat).first
                if lnk.count() > 0 and lnk.is_visible(timeout=400):
                    return True
            except Exception:
                pass
            for css in ("table tbody tr td", "table tr td", '[role="gridcell"]', "td"):
                try:
                    cands = app.locator(css).filter(has_text=fn_pat)
                    if cands.count() > 0 and cands.first.is_visible(timeout=400):
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    try:
        lnk = root.get_by_role("link", name=fn_pat).first
        if lnk.count() > 0 and lnk.is_visible(timeout=400):
            return True
    except Exception:
        pass
    return False


def _wait_for_contact_detail_ready(
    page: Page,
    *,
    content_frame_selector: str | None,
    wait_ms: int,
    first_name: str | None = None,
) -> bool:
    """Wait until contact detail is ready: detail **inputs** and/or Contacts first-name drill targets."""
    sels = (
        'input[aria-label="First Name"]',
        'textarea[aria-label="First Name"]',
        "input[name*='First_Name' i]",
        'input[aria-label="Relation\'s Name"]',
        'textarea[aria-label="Relation\'s Name"]',
    )
    start_t = time.monotonic()
    deadline = start_t + max(0.2, wait_ms / 1000.0)
    while time.monotonic() < deadline:
        for root in _siebel_locator_search_roots(page, content_frame_selector):
            for css in sels:
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=250):
                        return True
                except Exception:
                    continue
            if first_name and _contacts_applet_first_name_drill_target_visible(root, first_name):
                return True
        _safe_page_wait(page, 120, log_label="wait_contact_detail_ready")
    return False


def _siebel_try_click_mobile_search_hit_link(
    page: Page,
    mobile: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """
    After Find/Go, open the contact from the left **Search Results** / **Siebel Find** pane (Title
    column). Hero often uses ``<a href="javascript:void(0);">`` for the blue mobile drill-in — scoped
    to ``.siebui-applet`` when it contains **Search Results**. Tries: accessible-name link, javascript
    anchors + force/double-click, generic ``<a>`` by phone text, table / ``role=row`` scan, row click.
    Uses :func:`_iter_mobile_search_hit_roots` for iframe order.
    """
    needle = _mobile_needle_for_contact_grid_match(mobile)
    raw_compact = re.sub(r"\s+", "", (mobile or "").strip())
    raw_digits = re.sub(r"\D", "", (mobile or "").strip())
    name_patterns: list[re.Pattern[str]] = []
    if raw_compact:
        name_patterns.append(re.compile(re.escape(raw_compact)))
    if needle and needle != raw_compact:
        name_patterns.append(re.compile(re.escape(needle)))
    for pat in name_patterns:
        if _siebel_try_click_named_in_frames(
            page,
            pat,
            roles=("link",),
            timeout_ms=timeout_ms,
            content_frame_selector=content_frame_selector,
        ):
            return True

    text_patterns = _mobile_text_patterns_for_grid(mobile or "")
    row_selectors = (
        "table tbody tr",
        "table tr",
        '[role="row"]',
        "tr[role='row']",
    )

    def row_contains_needle(row_digits: str) -> bool:
        if needle and needle in row_digits:
            return True
        if raw_digits and len(raw_digits) >= 8 and raw_digits in row_digits:
            return True
        return False

    def _try_click_siebel_drilldown(loc) -> bool:
        """Siebel list drill-ins often use ``javascript:void(0)``; overlay may block a normal click."""
        try:
            if not loc.is_visible(timeout=600):
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

    def try_click_in_root(root) -> bool:
        # Left **Search Results** / **Siebel Find** pane: hit is usually
        # ``<a href="javascript:void(0)">8306827880</a>`` — status bar shows ``javascript:void(0);``.
        scopes: list = []
        try:
            panel = root.locator(".siebui-applet").filter(has_text=re.compile(r"Search\s+Results", re.I)).first
            if panel.count() > 0:
                try:
                    if panel.is_visible(timeout=450):
                        scopes.append(panel)
                except Exception:
                    pass
        except Exception:
            pass
        scopes.append(root)

        for scope in scopes:
            for tpat in text_patterns:
                for css in (
                    'a[href^="javascript"]',
                    'a[href*="void(0)"]',
                    "a[href*='javascript']",
                    "a.siebui-ctrl-drilldown",
                    "a",
                ):
                    try:
                        hits = scope.locator(css).filter(has_text=tpat)
                        hn = hits.count()
                        for i in range(min(hn, 30)):
                            if _try_click_siebel_drilldown(hits.nth(i)):
                                return True
                    except Exception:
                        continue
        # Scan list rows: prefer first <a> in a row that contains the mobile anywhere
        if not needle and not (raw_digits and len(raw_digits) >= 8):
            return False
        for rsel in row_selectors:
            try:
                rows = root.locator(rsel)
                n = rows.count()
            except Exception:
                continue
            for i in range(min(n, 120)):
                row = rows.nth(i)
                try:
                    if not row.is_visible(timeout=250):
                        continue
                except Exception:
                    continue
                try:
                    row_digits = re.sub(r"\D", "", row.inner_text(timeout=800) or "")
                except Exception:
                    continue
                if not row_contains_needle(row_digits):
                    continue
                for inner in (
                    row.locator("a[href]"),
                    row.locator("a"),
                    row.get_by_role("link"),
                ):
                    try:
                        if inner.count() <= 0:
                            continue
                        link = inner.first
                        if link.is_visible(timeout=500):
                            link.click(timeout=timeout_ms)
                            return True
                    except Exception:
                        continue
                # Row itself is the hit target (Open UI list row)
                try:
                    row.click(timeout=timeout_ms)
                    return True
                except Exception:
                    continue
        return False

    for root in _iter_mobile_search_hit_roots(page, content_frame_selector):
        try:
            if try_click_in_root(root):
                return True
        except Exception:
            continue
    return False


def _siebel_try_activate_find_contact_context(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> None:
    """
    After **Contact_Enquiry** / enquiry subgrid work, Siebel often leaves a sub-tab active so the
    upper **Contacts** list (First Name drilldown) is not in focus. Best-effort: activate **Find Contact**
    (tab/link) or the **Contacts** list sub-tab (not **Contact_Enquiry**).
    """
    t = min(int(action_timeout_ms), 4500)
    for pat, label in (
        (re.compile(r"Find\s*Contact", re.I), "Find Contact"),
        (re.compile(r"Find\s+Contact", re.I), "Find Contact"),
    ):
        if _siebel_try_click_named_in_frames(
            page,
            pat,
            roles=("tab", "link", "button"),
            timeout_ms=t,
            content_frame_selector=content_frame_selector,
        ):
            note(f"Activated {label!r} tab/link — switching context for Contacts / First Name.")
            _safe_page_wait(page, 900, log_label="after_find_contact_context_tab")
            return
    # Sub-view "Contacts" (list) vs "Contact_Enquiry" — exact name reduces accidental top-nav hits.
    if _siebel_try_click_named_in_frames(
        page,
        re.compile(r"^\s*Contacts\s*$", re.I),
        roles=("tab", "link"),
        timeout_ms=t,
        content_frame_selector=content_frame_selector,
        max_candidates=8,
    ):
        note("Activated Contacts sub-tab (list) — leaving Contact_Enquiry for First Name column.")
        _safe_page_wait(page, 900, log_label="after_contacts_list_subtab")
        return
    note("Find Contact / Contacts list tab not found — proceeding to First Name click anyway.")


def _branch2_fill_scoped_in_root(
    root,
    *,
    scopes: tuple[str, ...],
    selectors: tuple[str, ...],
    value: str,
    action_timeout_ms: int,
    note,
    log_label: str,
    allow_fill_fallback: bool = False,
) -> bool:
    """Fill the first visible control matching *selectors* under *scopes* (empty scope = root).

    When *allow_fill_fallback* is True, a failed ``fill()`` (jqGrid **td**, Siebel LOV / popup input)
    triggers inner ``input``/``textarea`` or ``press_sequentially``.
    """
    v = (value or "").strip()
    if not v:
        return False
    t = min(int(action_timeout_ms), 6000)

    def _fill_locator(loc, sc: str, css: str) -> bool:
        try:
            if loc.count() == 0:
                return False
            if not loc.is_visible(timeout=500):
                return False
            loc.click(timeout=min(2000, t))
            try:
                loc.fill(v, timeout=t)
            except Exception:
                if not allow_fill_fallback:
                    raise
                try:
                    inner = loc.locator("input, textarea").first
                    if inner.count() > 0 and inner.is_visible(timeout=650):
                        inner.fill(v, timeout=t)
                    else:
                        loc.press_sequentially(v, delay=12)
                except Exception:
                    try:
                        loc.press_sequentially(v, delay=12)
                    except Exception:
                        return False
            note(f"Branch (2): filled {log_label} in {sc} via {css!r} → {v[:48]!r}.")
            return True
        except Exception:
            return False

    for scope in scopes:
        for css in selectors:
            try:
                if scope:
                    container = root.locator(scope).first
                    if container.count() == 0:
                        continue
                    if not container.is_visible(timeout=220):
                        continue
                    loc = container.locator(css).first
                else:
                    loc = root.locator(css).first
                sc = scope or "(root)"
                if _fill_locator(loc, sc, css):
                    return True
            except Exception:
                continue
    return False


def _iter_branch2_s_a1_frame_roots(page: Page, content_frame_selector: str | None):
    """
    Yield search roots for Siebel **S_A1**: **iframe** ``iframe#S_A1`` first, then any element
    ``[id="S_A1"]`` (some builds use a **div**/section instead of ``<iframe>``).

    Hero often hosts the Address **jqGrid** (e.g. ``gview_s_1_l``) and **Postal_Code** inputs here —
    try **before** unscoped grid scans.
    """
    for sel in ("iframe#S_A1", 'iframe[id="S_A1"]'):
        try:
            yield page.frame_locator(sel)
        except Exception:
            continue
    try:
        for parent in _iter_frame_locator_roots(page, content_frame_selector):
            for sel in ("iframe#S_A1", 'iframe[id="S_A1"]'):
                try:
                    yield parent.frame_locator(sel)
                except Exception:
                    continue
    except Exception:
        pass
    try:
        for frame in _ordered_frames(page):
            for sel in ("iframe#S_A1", 'iframe[id="S_A1"]'):
                try:
                    yield frame.frame_locator(sel)
                except Exception:
                    continue
    except Exception:
        pass
    # Non-iframe container (same id on a block element)
    try:
        yield page.locator('[id="S_A1"]').first
    except Exception:
        pass
    try:
        for parent in _iter_frame_locator_roots(page, content_frame_selector):
            try:
                yield parent.locator('[id="S_A1"]').first
            except Exception:
                continue
    except Exception:
        pass
    try:
        for frame in _ordered_frames(page):
            try:
                yield frame.locator('[id="S_A1"]').first
            except Exception:
                continue
    except Exception:
        pass


def _branch2_try_fill_contact_input(
    page: Page,
    *,
    selectors: tuple[str, ...],
    value: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    log_label: str,
) -> bool:
    """Fill one contact applet input (Home Phone # / Email) by trying selectors across Siebel roots."""
    v = (value or "").strip()
    if not v:
        return False
    t = min(int(action_timeout_ms), 6000)
    for root in _siebel_all_search_roots(page, content_frame_selector):
        for css in selectors:
            try:
                loc = root.locator(css).first
                if loc.count() == 0:
                    continue
                if not loc.is_visible(timeout=450):
                    continue
                loc.click(timeout=min(2000, t))
                loc.fill(v, timeout=t)
                note(f"Branch (2): filled {log_label} → {v[:48]!r}.")
                return True
            except Exception:
                continue
    return False


def _branch2_select_address_via_third_level_view_bar(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Hero contact detail exposes **Third Level View Bar** as ``<select id="j_s_vctrl_div_tabScreen">``
    (``aria-label="Third Level View Bar"``). **Address** is ``value="tabScreen6"``. This must run before
    relying on **Address** ``ui-tabs-anchor`` clicks (tabs may not be exposed as links until selected).
    """
    t = min(int(action_timeout_ms), 6000)
    for root in _siebel_all_search_roots(page, content_frame_selector):
        for css in _SIEBEL_THIRD_LEVEL_VIEW_BAR_SELECTORS:
            try:
                loc = root.locator(css).first
                if loc.count() == 0:
                    continue
                if not loc.is_visible(timeout=450):
                    continue
                try:
                    loc.select_option(value="tabScreen6", timeout=t)
                    note(
                        "Branch (2): Third Level View Bar — selected Address (tabScreen6) via "
                        f"{css!r}."
                    )
                    return True
                except Exception:
                    try:
                        loc.select_option(label="Address", timeout=t)
                        note(
                            "Branch (2): Third Level View Bar — selected Address by label via "
                            f"{css!r}."
                        )
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


def _branch2_click_address_tab_under_s_vctrl(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Open the **Address** third-level view: prefer **Third Level View Bar** ``select#j_s_vctrl_div_tabScreen``
    (``tabScreen6``), then ``#s_vctrl_div`` **Address** ``ui-tabs-anchor`` / tab role fallbacks.
    """
    if _branch2_select_address_via_third_level_view_bar(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        return True
    t = min(int(action_timeout_ms), 6000)
    addr_pat = re.compile(r"^\s*Address\s*$", re.I)
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            vwrap = root.locator("#s_vctrl_div")
            if vwrap.count() == 0:
                continue
            vctrl = vwrap.first
            if not vctrl.is_visible(timeout=300):
                continue
            for css in (
                'a[data-tabindex="tabScreen6"].ui-tabs-anchor',
                'a.ui-tabs-anchor[href*="tabScreen_noop"]:has-text("Address")',
                'a.ui-tabs-anchor:has-text("Address")',
            ):
                try:
                    loc = vctrl.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=550):
                        loc.click(timeout=t)
                        note("Branch (2): clicked Address tab under #s_vctrl_div (CSS).")
                        return True
                except Exception:
                    continue
            try:
                loc = vctrl.get_by_role("tab", name=addr_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=550):
                    loc.click(timeout=t)
                    note("Branch (2): clicked Address tab under #s_vctrl_div (role=tab).")
                    return True
            except Exception:
                pass
            try:
                loc = vctrl.locator("a.ui-tabs-anchor").filter(has_text=addr_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=550):
                    loc.click(timeout=t)
                    note("Branch (2): clicked Address tab under #s_vctrl_div (anchor filter).")
                    return True
            except Exception:
                pass
        except Exception:
            continue
    return False


def _siebel_video_branch2_address_postal_and_save(
    page: Page,
    *,
    pin_code: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    home_phone: str | None = None,
    contact_email: str | None = None,
    city: str | None = None,
) -> bool:
    """
    Video branch **(2)** (no Open enquiry): after Relation's Name path, fill **Home Phone #** and **Email**,
    open **Address**, set **City** (``name=City``, ``id=1_City``, Sieb LOV classes) then **Postal Code**
    (jqGrid ``1_s_1_l_Postal_Code`` / ``name=Postal_Code`` / ``1_Postal_Code``),
    preferring **iframe#S_A1** then **#SWEApplet1** / **form SWE_Form1_0** / **div#S_A1**, then **Ctrl+S** (Save toolbar fallback).

    ``home_phone`` defaults from DMS landline / alternate phone at the caller; ``contact_email`` defaults
    to ``NA`` when the caller passes None. ``city`` from DMS (e.g. city or district).
    """
    pin = (pin_code or "").strip()
    if not pin:
        note("Branch (2) Address: pin_code empty — skipping Address tab / Postal Code fill.")
        return False
    t = min(int(action_timeout_ms), 6000)
    hp = (home_phone or "").strip()
    em = (contact_email if contact_email is not None else "NA").strip()
    if not em:
        em = "NA"

    _branch2_try_fill_contact_input(
        page,
        selectors=(
            'input[name="s_4_1_159_0"]',
            '[aria-label="Home Phone #"]',
            'input[aria-label*="Home Phone" i]',
        ),
        value=hp,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        log_label="Home Phone #",
    )
    _safe_page_wait(page, 280, log_label="after_branch2_home_phone")
    _branch2_try_fill_contact_input(
        page,
        selectors=(
            'input[name="s_4_1_225_0"][aria-labelledby="EmailAddress_Label_4"]',
            'input[name="s_4_1_225_0"]',
            'input[aria-label="Email" i]',
            '[aria-label="Email"]',
        ),
        value=em,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        log_label="Email",
    )
    _safe_page_wait(page, 280, log_label="after_branch2_email")

    if _branch2_click_address_tab_under_s_vctrl(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        _safe_page_wait(page, 700, log_label="after_address_tab_branch2")
    elif _siebel_try_click_named_in_frames(
        page,
        re.compile(r"^\s*Address\s*$", re.I),
        roles=("tab", "link", "button"),
        timeout_ms=t,
        content_frame_selector=content_frame_selector,
    ):
        note("Branch (2): clicked Address tab (frame scan fallback).")
        _safe_page_wait(page, 700, log_label="after_address_tab_branch2")
    else:
        note("Branch (2): Address tab not found — trying Postal Code field anyway.")

    city_val = (city or "").strip()
    # jqGrid **gview** may be class ``gview_1_l`` or ``gview_s_1_l``; cell id ``1_s_1_l_Postal_Code`` (td).
    _scopes = (
        "#SWEApplet1 #gview_s_1_l",
        "#SWEApplet1 .gview_1_l",
        "#SWEApplet1 .gview_s_1_l",
        'form[name="SWE_Form1_0"]',
        "#SWEApplet1",
        "div#S_A1",
        "#S_A1",
        "#gview_s_1_l",
        ".gview_1_l",
        "",
    )
    _city_sels = (
        'input#1_City',
        '[id="1_City"]',
        'input[id="1_City"]',
        'input[name="City"]',
        'input.siebui-input-popup[id="1_City"]',
        'input[role="textbox"][name="City"]',
        'input[aria-labelledby*="s_1_l_City" i]',
    )
    _postal_sels = (
        "td#1_s_1_l_Postal_Code",
        '[id="1_s_1_l_Postal_Code"]',
        "td#1_s_1_l_Postal_Code input",
        '[id="1_s_1_l_Postal_Code"] input',
        'input[name="Postal_Code"]',
        '[id="1_Postal_Code"]',
        'input[id="1_Postal_Code"]',
        'input[name*="Postal" i]',
    )

    def _fill_city_and_postal_in_root(root) -> bool:
        if city_val:
            _branch2_fill_scoped_in_root(
                root,
                scopes=_scopes,
                selectors=_city_sels,
                value=city_val,
                action_timeout_ms=action_timeout_ms,
                note=note,
                log_label="City (1_City)",
                allow_fill_fallback=True,
            )
        return _branch2_fill_scoped_in_root(
            root,
            scopes=_scopes,
            selectors=_postal_sels,
            value=pin,
            action_timeout_ms=action_timeout_ms,
            note=note,
            log_label="Postal Code",
            allow_fill_fallback=True,
        )

    _filled = False
    for s_a1_root in _iter_branch2_s_a1_frame_roots(page, content_frame_selector):
        if _fill_city_and_postal_in_root(s_a1_root):
            note("Branch (2): City / Postal Code filled inside S_A1 scope (iframe or id=S_A1).")
            _filled = True
            break
    if not _filled:
        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            if _fill_city_and_postal_in_root(fl):
                _filled = True
                break
    if not _filled:
        for frame in _ordered_frames(page):
            if _fill_city_and_postal_in_root(frame):
                _filled = True
                break
    if not _filled and _fill_city_and_postal_in_root(page):
        _filled = True
    if not _filled:
        for root in _siebel_all_search_roots(page, content_frame_selector):
            if _fill_city_and_postal_in_root(root):
                _filled = True
                break
    if not _filled:
        note(
            "Branch (2): could not locate or fill Postal Code "
            "(tried iframe#S_A1 → SWEApplet1 / form SWE_Form1_0 / div#S_A1 / "
            "td#1_s_1_l_Postal_Code, inputs name=Postal_Code / id 1_Postal_Code)."
        )
        return False

    _safe_page_wait(page, 350, log_label="after_city_postal_fill_branch2")
    try:
        page.keyboard.press("Control+S", delay=50)
        note("Branch (2): pressed Ctrl+S after City / Postal Code.")
        return True
    except Exception:
        note("Branch (2): Ctrl+S failed — trying Save control.")
    if _try_click_siebel_save(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("Branch (2): Save clicked after City / Postal Code.")
        return True
    try:
        page.keyboard.press("Control+S", delay=50)
        note("Branch (2): pressed Ctrl+S (retry) after City / Postal Code.")
        return True
    except Exception:
        note("Branch (2): Save toolbar and Ctrl+S both failed after City / Postal Code.")
        return False


def _siebel_video_path_after_find_go_to_all_enquiries(
    page: Page,
    *,
    mobile: str,
    first_name: str,
    care_of: str,
    address_line_1: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    skip_search_hit_click: bool = False,
    customer_profession: str | None = None,
) -> bool:
    """
    Steps after **Find + Go** from operator recording *Find Contact Enquiry*:
    optional **Siebel Find** tab → click the **Search Results** mobile drill-in → **Contacts** →
    **Contact_Enquiry** (Contacts + Enquiries tables, Enquiry# link) → **Enquiry** → **All Enquiries**.
    If *skip_search_hit_click* is True, the left-pane drilldown click is skipped (already done by caller).
    """
    if not skip_search_hit_click:
        if not _wait_for_mobile_search_hit_ready(
            page, mobile, content_frame_selector=content_frame_selector, wait_ms=2200
        ):
            _safe_page_wait(page, 180, log_label="after_find_go_before_drill_fallback")
        if _siebel_try_click_named_in_frames(
            page,
            re.compile(r"Siebel\s*Find", re.I),
            roles=("tab", "link"),
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        ):
            note("Activated Siebel Find tab in search results (video SOP).")
            _wait_for_mobile_search_hit_ready(
                page, mobile, content_frame_selector=content_frame_selector, wait_ms=700
            )

        if not _siebel_try_click_mobile_search_hit_link(
            page,
            mobile,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        ):
            note("Could not click a search-result link for the mobile — check left Search Results grid.")
            return False
        note("Opened contact from search hit hyperlink (video SOP).")
        if not _wait_for_contact_detail_ready(
            page,
            content_frame_selector=content_frame_selector,
            wait_ms=1200,
            first_name=first_name,
        ):
            _safe_page_wait(page, 180, log_label="after_contact_drill_link_fallback")
    else:
        note("Skipped search-hit drilldown click (already opened by caller).")
        _siebel_try_activate_find_contact_context(
            page,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
        )

    care_val = (care_of or "").strip()

    # Deterministic navigation: open full contact via First Name in Contacts grid (even if care_of empty).
    opened_customer = _siebel_open_found_customer_record(
        page,
        mobile=mobile,
        first_name=first_name,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        skip_left_pane_click=True,
    )
    if not opened_customer:
        note("Could not click First Name in Contacts pane (video SOP).")
        return False

    if not _wait_for_contact_detail_ready(
        page,
        content_frame_selector=content_frame_selector,
        wait_ms=700,
        first_name=first_name,
    ):
        _safe_page_wait(page, 120, log_label="after_first_name_click_before_relation_fill_fallback")

    if not care_val:
        note("No care_of from DB — skipping Relation's Name / Address Line 1 fill after First Name drilldown.")
        return True

    rel_type = _relation_type_from_care_of(care_val)
    rel_name = _relation_display_name_from_care_of(care_val)
    occ_label = _occupation_siebel_label_from_staging_profession(customer_profession)

    _branch2_try_fill_contact_input(
        page,
        selectors=(
            'input[name="s_4_1_225_0"][aria-labelledby="EmailAddress_Label_4"]',
            'input[name="s_4_1_225_0"]',
        ),
        value="NA",
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        log_label="Email (NA, relation path)",
    )
    _safe_page_wait(page, 200, log_label="after_relation_path_email_na")
    _pick_occupation_siebel_lov(
        page,
        occupation_label=occ_label,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    )
    _safe_page_wait(page, 200, log_label="after_relation_path_occupation")
    if rel_type:
        _pick_relation_type_from_dropdown(
            page,
            relation=rel_type,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        _safe_page_wait(page, 220, log_label="after_relation_path_relation_type")

    if not (rel_name or "").strip():
        note(
            "Relation's Name empty after stripping S/O · W/O · D/O prefix from care_of — "
            "skipping Relation's Name field; continuing Address Line 1 path."
        )
        return _after_relation_fill_nav()

    # DB rule: Address Line 1 should be the substring between first and second comma.
    addr_raw = (address_line_1 or "").strip()
    addr_line1_value = ""
    if addr_raw and "," in addr_raw:
        parts = [p.strip() for p in addr_raw.split(",")]
        if len(parts) >= 3:
            addr_line1_value = parts[1]
    if not addr_line1_value:
        addr_line1_value = ""

    def _fill_with_retry(loc, value: str, *, attempts: int = 3, visible_ms: int = 900, _lbl: str = "") -> bool:
        """
        Stabilize flaky Siebel input commit:
        wait visible -> fill -> blur(Tab) -> readback verify, with short backoff retries.
        """
        for i in range(max(1, attempts)):
            try:
                cnt = loc.count()
                if cnt <= 0:
                    return False
                vis = loc.is_visible(timeout=visible_ms)
                if not vis:
                    continue
                try:
                    loc.click(timeout=min(2000, action_timeout_ms))
                except Exception:
                    loc.click(timeout=min(2000, action_timeout_ms), force=True)
                loc.fill(value, timeout=action_timeout_ms)
                try:
                    loc.press("Tab", timeout=min(1200, action_timeout_ms))
                except Exception:
                    pass
                _safe_page_wait(page, 120 + (i * 200), log_label=f"retry_settle_{i+1}")
                got = (loc.input_value(timeout=min(2500, action_timeout_ms)) or "").strip()
                _ok = bool(got and (value.lower() in got.lower() or got.lower() in value.lower()))
                if _ok:
                    return True
            except Exception:
                pass
            _safe_page_wait(page, 220 + (i * 250), log_label=f"retry_backoff_{i+1}")
        return False

    def _fill_address_line_1_if_available() -> bool:
        if not addr_line1_value:
            return True
        addr_selectors = (
            'input[aria-label="Address Line 1"]',
            'textarea[aria-label="Address Line 1"]',
        )
        # Try frame-locator roots first, then frames.
        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            for css in addr_selectors:
                try:
                    loc = fl.locator(css).first
                    if _fill_with_retry(loc, addr_line1_value, attempts=3, visible_ms=900):
                        note(f"Address Line 1 filled from DB substring: {addr_line1_value!r}")
                        return True
                except Exception:
                    continue
        for frame in _ordered_frames(page):
            for css in addr_selectors:
                try:
                    loc = frame.locator(css).first
                    if _fill_with_retry(loc, addr_line1_value, attempts=3, visible_ms=900):
                        note(f"Address Line 1 filled from DB substring: {addr_line1_value!r}")
                        return True
                except Exception:
                    continue
        note(f"Could not fill Address Line 1 from DB substring: {addr_line1_value!r}")
        return False

    def _after_relation_fill_nav() -> bool:
        addr_ok = _fill_address_line_1_if_available()
        if not addr_ok:
            note("Stopping before Add customer payment: Address Line 1 was not filled.")
            return False
        note(
            "Relation's Name filled; optional Address Line 1 substring applied when available. "
            "Continuing video SOP (branch (2) contact fields / Payments next)."
        )
        return True

    def _read_first_name_probe() -> None:
        """
        Rendering stabilizer: read First Name before Relation's Name attempts.
        This intentionally adds a small settle point for late Siebel field rendering.
        """
        first_name_selectors = (
            'input[aria-label="First Name"]',
            'textarea[aria-label="First Name"]',
            "input[name*='First_Name' i]",
            "input[id*='First_Name' i]",
            "input[title*='First Name' i]",
        )
        # Frame-locator roots first.
        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            for css in first_name_selectors:
                try:
                    loc = fl.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=600):
                        try:
                            _ = (loc.input_value(timeout=1200) or "").strip()
                        except Exception:
                            pass
                        _safe_page_wait(page, 220, log_label="after_first_name_read_probe")
                        return
                except Exception:
                    continue
            try:
                by_label = fl.get_by_label("First Name", exact=True).first
                if by_label.count() > 0 and by_label.is_visible(timeout=500):
                    try:
                        _ = (by_label.input_value(timeout=1200) or "").strip()
                    except Exception:
                        pass
                    _safe_page_wait(page, 220, log_label="after_first_name_read_probe")
                    return
            except Exception:
                pass
        # Then ordered frames.
        for frame in _ordered_frames(page):
            for css in first_name_selectors:
                try:
                    loc = frame.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=600):
                        try:
                            _ = (loc.input_value(timeout=1200) or "").strip()
                        except Exception:
                            pass
                        _safe_page_wait(page, 220, log_label="after_first_name_read_probe")
                        return
                except Exception:
                    continue
            try:
                by_label = frame.get_by_label("First Name", exact=True).first
                if by_label.count() > 0 and by_label.is_visible(timeout=500):
                    try:
                        _ = (by_label.input_value(timeout=1200) or "").strip()
                    except Exception:
                        pass
                    _safe_page_wait(page, 220, log_label="after_first_name_read_probe")
                    return
            except Exception:
                pass

    exact_selectors = (
        "input[aria-label=\"Relation's Name\"]",
        "textarea[aria-label=\"Relation's Name\"]",
        "input[name='s_4_1_89_0'][aria-label=\"Relation's Name\"]",
        "input[name='s_4_1_89_0']",
        "input[aria-labelledby=\"Relation's_Name_Label_4\"]",
        "input.s_4_1_89_0",
    )

    # Outer retry: when Siebel is slow the detail form may not be rendered yet.
    for _outer in range(4):
        _read_first_name_probe()

        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            for css in exact_selectors:
                try:
                    loc = fl.locator(css).first
                    if _fill_with_retry(loc, rel_name, attempts=3, visible_ms=900, _lbl=f"fl:{css[:40]}"):
                        return _after_relation_fill_nav()
                except Exception:
                    continue
        for frame in _ordered_frames(page):
            for css in exact_selectors:
                try:
                    loc = frame.locator(css).first
                    if _fill_with_retry(loc, rel_name, attempts=3, visible_ms=900, _lbl=f"fr:{css[:40]}"):
                        return _after_relation_fill_nav()
                except Exception:
                    continue

        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            try:
                loc = fl.get_by_label("Relation's Name", exact=True).first
                if _fill_with_retry(loc, rel_name, attempts=3, visible_ms=700, _lbl="lbl_fl"):
                    return _after_relation_fill_nav()
            except Exception:
                continue
        for frame in _ordered_frames(page):
            try:
                loc = frame.get_by_label("Relation's Name", exact=True).first
                if _fill_with_retry(loc, rel_name, attempts=3, visible_ms=700, _lbl="lbl_fr"):
                    return _after_relation_fill_nav()
            except Exception:
                continue

        if _outer < 3:
            _safe_page_wait(page, 1200 + _outer * 800, log_label=f"relation_name_outer_retry_{_outer}")

    note("Could not fill Relation's Name on opened customer record (video SOP).")
    return False


def _contact_mobile_drilldown_plans(
    page: Page,
    mobile: str,
    *,
    content_frame_selector: str | None,
    first_name_exact: str | None = None,
) -> list[tuple[object, int, str, int]]:
    """
    Build ordered drilldown plans: each row that contains the mobile (10-digit / raw digit rules)
    and has a visible row link. **Duplicate-mobile detection:** we scan each search root separately
    and keep the **single** root's plan list with the **most** hits.

    **Fast path:** ``DMS_SIEBEL_CONTENT_FRAME_SELECTOR`` FrameLocators + :func:`_resolve_builtin_contact_find_grid_frame`
    (builtin Hero **SWEView** / **Opportunity+List** URL). If any root yields plans, return immediately
    without scanning every iframe. **Fallback:** full sweep (mirrored grids, hint drift after Siebel upgrade).
    ``len(returned)`` is the number of table rows for sweep ordinals ``0 .. len-1``.
    ``first_name_exact`` is accepted for call-site parity with sweep/video caching; row selection is mobile-scoped.
    """
    _ = first_name_exact
    drill_needle = _mobile_needle_for_contact_grid_match(mobile)
    drill_raw = re.sub(r"\D", "", (mobile or "").strip())
    row_has_mobile_js = """(el, args) => {
      const needle = String(args.needle || '');
      const raw = String(args.raw || '');
      const digits = (s) => String(s || '').replace(/\\D/g, '');
      const tr = el.closest('tr');
      if (!tr) return false;
      const blob = (tr && tr.textContent) ? tr.textContent : '';
      const d = digits(blob);
      if (needle && d.includes(needle)) return true;
      if (raw.length >= 8 && d.includes(raw)) return true;
      return false;
    }"""

    args = {"needle": drill_needle, "raw": drill_raw}

    def _collect_plans_for_root(_dr_root: object) -> list[tuple[object, int, str, int]]:
        plans_here: list[tuple[object, int, str, int]] = []
        try:
            _rows = _dr_root.locator("table tr")
            _rn = _rows.count()
        except Exception:
            return plans_here
        for _ri in range(min(_rn, 80)):
            _row = _rows.nth(_ri)
            try:
                _tds = _row.locator("td")
                if _tds.count() < 3:
                    continue
                if not _row.is_visible(timeout=500):
                    continue
            except Exception:
                continue
            try:
                _mobile_ok = bool(_row.evaluate(row_has_mobile_js, args))
            except Exception:
                _mobile_ok = False
            if not _mobile_ok:
                continue
            _row_link_sel: str | None = None
            _row_link_idx: int | None = None
            for _link_sel in ('a[name="Title"]', 'a[name="title"]', "a[href]", '[role="link"]'):
                try:
                    _links = _row.locator(_link_sel)
                    _ln = _links.count()
                except Exception:
                    continue
                for _li in range(min(_ln, 8)):
                    _lnk = _links.nth(_li)
                    try:
                        if not _lnk.is_visible(timeout=300):
                            continue
                    except Exception:
                        continue
                    _row_link_idx = _li
                    _row_link_sel = _link_sel
                    break
                if _row_link_idx is not None:
                    break
            if _row_link_sel is not None and _row_link_idx is not None:
                plans_here.append((_dr_root, _ri, _row_link_sel, _row_link_idx))
        return plans_here

    best_plans: list[tuple[object, int, str, int]] = []
    # Fast path: explicit FrameLocators + single **Frame** from builtin URL (usually main) — no full scan.
    _fast_roots: list[object] = []
    for _fl in _iter_frame_locator_roots(page, content_frame_selector):
        _fast_roots.append(_fl)
    _direct_fr = _resolve_builtin_contact_find_grid_frame(page)
    if _direct_fr is not None:
        _fast_roots.append(_direct_fr)
    for _dr_root in _fast_roots:
        _ph = _collect_plans_for_root(_dr_root)
        if len(_ph) > len(best_plans):
            best_plans = _ph
    if best_plans:
        return best_plans

    # Fallback: full multi-root sweep (mirrored grids, odd iframes, or hint URL drift).
    for _dr_root in (
        list(
            _iter_siebel_root_search_order(
                page,
                content_frame_selector,
                _load_mobile_search_hit_hint_dict_from_config(),
            )
        )
        + list(_ordered_frames(page))
        + [page]
    ):
        _ph = _collect_plans_for_root(_dr_root)
        if len(_ph) > len(best_plans):
            best_plans = _ph
    return best_plans


def _contact_find_mobile_drilldown_occurrence_count(
    page: Page,
    mobile: str,
    *,
    content_frame_selector: str | None = None,
    first_name_exact: str | None = None,
    cached_plans: list[tuple[object, int, str, int]] | None = None,
) -> int:
    """Return how many result rows contain ``mobile`` and are drillable (same rules as sweep ordinals)."""
    if cached_plans is not None:
        return len(cached_plans)
    return len(
        _contact_mobile_drilldown_plans(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=first_name_exact,
        )
    )


def _click_nth_mobile_title_drilldown(
    page: Page,
    mobile: str,
    ordinal: int,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    first_name_exact: str | None = None,
    cached_plans: list[tuple[object, int, str, int]] | None = None,
) -> bool:
    """
    After Contact Find/Go, click the ``ordinal``-th (0-based) drilldown row that matches ``mobile``.
    We do **not** depend on the Title anchor text; we anchor to the row that contains the mobile digits
    and click a visible link inside that row.

    **cached_plans**: optional list from ``_contact_mobile_drilldown_plans`` built with the **same**
    ``first_name_exact`` as this call — avoids a duplicate full grid scan (video path).
    """
    if ordinal < 0:
        return False
    if cached_plans is not None:
        plans = cached_plans
    else:
        plans = _contact_mobile_drilldown_plans(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=first_name_exact,
        )
    if ordinal >= len(plans):
        return False
    _dr_root, _row_i, _link_sel, _link_i = plans[ordinal]
    _dr_el = _dr_root.locator("table tr").nth(_row_i).locator(_link_sel).nth(_link_i)
    try:
        _dr_el.click(timeout=action_timeout_ms)
        return True
    except Exception:
        try:
            _dr_el.click(timeout=action_timeout_ms, force=True)
            return True
        except Exception:
            return False


def _contact_find_title_sweep_for_enquiry(
    page: Page,
    *,
    mobile: str,
    first_name: str | None,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    note,
    step,
    max_title_ordinals: int = 12,
    cached_plans_ord0: list[tuple[object, int, str, int]] | None = None,
    cached_plans_dup: list[tuple[object, int, str, int]] | None = None,
) -> tuple[bool, str, int, str | None]:
    """
    Duplicate-mobile sweep: when the Find list shows **several rows with the same mobile**
    (typically **Search Results** staying visible in a **left pane** while Contact / Enquiries load on
    the right), click each drillable row **in-place** (**ordinal** 0, 1, …) — **no Contact Find re-run**
    between rows. After each click, switch to **Contact_Enquiry** and detect an open enquiry via
    ``#jqgh_s_1_l_Enquiry_`` and ``input``/``textarea`` ``name=\"Enquiry_\"``
    (``_contact_enquiry_tab_has_rows``).

    ``first_name`` (when set) restricts **ordinal 0** drill targets (row must match Find key). For
    **ordinal ≥ 1**, drills use **mobile-only** row matching (same mobile in the list).

    **cached_plans_ord0**: plans from ``_contact_mobile_drilldown_plans(..., first_name_exact=first_name)``.
    **cached_plans_dup**: plans with ``first_name_exact=None`` (duplicate rows). When both set, skips
    rebuilding plans on each click (video path).

    Returns ``(has_existing_enquiry, enquiry_number, row_count, error_message)``.
    ``error_message`` set → caller must stop. If no error and ``has_existing_enquiry`` is False, every
    matching Title was opened and all had zero enquiry rows — caller may create a new enquiry.
    """
    used_fallback_link = False
    ordinal = 0
    fn = (first_name or "").strip()
    if cached_plans_ord0 is not None:
        _n_mobile_rows = len(cached_plans_ord0)
    else:
        _n_mobile_rows = _contact_find_mobile_drilldown_occurrence_count(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=fn if fn else None,
        )
    _ord_max = _n_mobile_rows - 1 if _n_mobile_rows else None
    note(
        f"Contact Find grid: {_n_mobile_rows} row(s) contain mobile {mobile} with a drilldown link"
        + (
            f" (sweep uses ordinal 0..{_ord_max})."
            if _ord_max is not None and _ord_max >= 0
            else "."
        )
    )

    while ordinal < max_title_ordinals:
        drilled = False
        if ordinal > 0:
            note(
                f"Duplicate mobile: click row {ordinal + 1} **in-place** (Search Results stay open; "
                f"no re-find between rows)."
            )
            drilled = _click_nth_mobile_title_drilldown(
                page,
                mobile,
                ordinal,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                first_name_exact=None,
                cached_plans=cached_plans_dup,
            )
            if not drilled:
                note(
                    f"In-place drill for duplicate row {ordinal + 1} failed — stopping sweep "
                    f"(no Contact Find re-run; list should remain visible in split view)."
                )
                break

        if not drilled:
            drilled = _click_nth_mobile_title_drilldown(
                page,
                mobile,
                ordinal,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                first_name_exact=(fn if fn else None) if ordinal == 0 else None,
                cached_plans=cached_plans_ord0
                if ordinal == 0
                else cached_plans_dup,
            )
        if not drilled and ordinal == 0 and not used_fallback_link:
            drilled = _siebel_try_click_mobile_search_hit_link(
                page,
                mobile,
                timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
            if drilled:
                used_fallback_link = True
                note("Opened contact from search-hit link (fallback) — duplicate sweep index 0.")
        if not drilled:
            if ordinal == 0:
                return False, "", 0, (
                    "Siebel: contact matched in search results, but could not click a Title drilldown "
                    "or search-hit link to open the contact detail."
                )
            break

        note(
            f"Drilldown {ordinal + 1}/{max(_n_mobile_rows, 1)} for mobile {mobile} (match index {ordinal}) "
            f"— opening contact, then Contact_Enquiry for #jqgh_s_1_l_Enquiry_ / name=Enquiry_."
        )
        _safe_page_wait(page, 2000, log_label="after_title_drilldown_sweep")
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        _enq_checked = False
        _enq_rows = 0
        _enq_number = ""
        for _enq_attempt in range(3):
            # After the first activation, reprobe the subgrid without re-clicking the tab first —
            # Siebel sometimes paints the jqGrid after our first pass; a redundant click can also
            # confuse focus/order on duplicate-mobile drilldowns.
            if _enq_attempt > 0:
                _enq_checked, _enq_rows, _enq_number = _contact_enquiry_tab_has_rows(
                    page,
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                    activate_tab=False,
                )
                if _enq_checked:
                    break
            _enq_checked, _enq_rows, _enq_number = _contact_enquiry_tab_has_rows(
                page,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                activate_tab=True,
            )
            if _enq_checked:
                break
            note(
                f"Contact_Enquiry tab: attempt {_enq_attempt + 1}/3 could not verify "
                f"(Title index {ordinal}) — retrying."
            )
            _safe_page_wait(page, 1200, log_label=f"contact_enquiry_tab_retry_sweep_{ordinal}_{_enq_attempt}")

        note(
            f"Contact_Enquiry check (Title index {ordinal}): "
            f"checked={_enq_checked!r}, rows={_enq_rows}, enquiry#={_enq_number!r}."
        )
        if not _enq_checked:
            return False, "", 0, (
                "Siebel: Contact_Enquiry tab could not be opened or verified. "
                "Cannot determine whether an enquiry exists for this contact."
            )

        if (_enq_number or "").strip() or _enq_rows > 0:
            if (_enq_number or "").strip():
                note(f"Enquiry exists: Enquiry#={_enq_number!r}. Proceeding (Title index {ordinal}).")
            else:
                note(
                    f"Contact_Enquiry has rows={_enq_rows} but Enquiry# was not scraped — "
                    f"proceeding without Add Enquiry (Title index {ordinal})."
                )
            return True, (_enq_number or "").strip(), _enq_rows, None

        note(
            f"No enquiry on contact Title index {ordinal} (rows=0) — trying next duplicate row for the "
            f"same mobile if present."
        )
        ordinal += 1

    return False, "", 0, None


def _contact_enquiry_tab_has_rows(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    activate_tab: bool = True,
) -> tuple[bool, int, str]:
    """
    Open Contact_Enquiry tab and check whether Enquiry grid has data rows **on the opened contact**.

    When ``activate_tab`` is False, only waits and re-evaluates frames (tab already activated).

    Detection: header ``#jqgh_s_1_l_Enquiry_`` (or **Enquiry#** / **Enquiries** / **Enquiry list** text),
    ``aria-describedby`` / ``th`` hints, then non-empty values on ``input`` / ``textarea``
    ``name=\"Enquiry_\"``; table cell scrape; Hero **Enquiry#** as
    visible ``<a>`` (e.g. ``11870-01-SENQ-0623-305``) inside **.siebui-applet** when the applet text
    references enquiries.     When **Enquiry Status** fields exist (``id=1_HHML_Enquiry_Status`` or any
    element whose ``id`` ends with ``HHML_Enquiry_Status``), only rows with status **Open**
    (case-insensitive) count as an **open** enquiry for sweep skip. **Closed** enquiries do not
    count — caller takes branch **(2)** (Address / postal). Frames: **main first**, then Siebel iframes.
    """
    if activate_tab:
        _clicked = _siebel_try_click_named_in_frames(
            page,
            re.compile(r"Contact[_\s]*Enquiry", re.I),
            roles=("tab", "link", "button"),
            timeout_ms=min(action_timeout_ms, 3500),
            content_frame_selector=content_frame_selector,
        )
        if not _clicked:
            note("Contact_Enquiry tab not clickable (could not verify enquiry rows).")
            return False, 0, ""

        _safe_page_wait(page, 1400, log_label="after_contact_enquiry_tab")
    else:
        _safe_page_wait(page, 550, log_label="contact_enquiry_subgrid_reprobe_no_tab_click")

    _js = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
      const isEmptyEnq = (v) => {
        const s = String(v || '').trim();
        return !s || s === '-' || s === '—';
      };
      const nameLooksEnquiryCol = (nm) => {
         const n = norm(nm || '').replace(/_/g, '');
         if (!n) return false;
         if (!n.includes('enquiry')) return false;
         if (n.includes('enquirysource')) return false;
         if (n.includes('enquirytype')) return false;
         return true;
      };

      // Hero Connect: Enquiry# is often a blue <a> like 11870-01-SENQ-0623-305 (hyphens + SENQ).
      const textLooksLikeHeroEnquiryKey = (t) => {
        const s = String(t || '').replace(/^\\s+|\\s+$/g, '').trim();
        if (s.length < 8 || s.length > 120) return false;
        if (/SENQ/i.test(s)) return true;
        if (/SCON/i.test(s) && /\\d+-\\d+-/.test(s)) return false;
        if (/^\\d[\\dA-Z]*(?:-[\\dA-Z]+){2,}$/i.test(s) && s.includes('-')) return true;
        return false;
      };

      const heroLinksInApplet = () => {
        let best = '';
        let cnt = 0;
        let scope = 'none';
        const tryAp = (root) => {
          for (const a of root.querySelectorAll('a')) {
            if (!vis(a)) continue;
            const t = (a.textContent || '').trim();
            if (!textLooksLikeHeroEnquiryKey(t)) continue;
            cnt += 1;
            if (!best) best = t;
          }
        };
        const applets = Array.from(document.querySelectorAll('.siebui-applet')).filter(vis);
        for (const ap of applets) {
          const snip = norm(ap.innerText || '').slice(0, 1200);
          if (!snip.includes('enquiries') && !snip.includes('enquiry#') && !snip.includes('enquiry #')
              && !snip.includes('enquiry list') && !snip.includes('open enquiries')) continue;
          tryAp(ap);
          if (cnt > 0) scope = 'applet';
        }
        if (cnt === 0) {
          tryAp(document.body || document.documentElement);
          if (cnt > 0) scope = 'global';
        }
        return { cnt, best, scope };
      };

      // When Enquiry Status controls exist (Hero: id 1_HHML_Enquiry_Status or *HHML_Enquiry_Status),
      // count only rows with status value "Open" (case-insensitive).
      const enquiryStatusFieldSelector = '[id="1_HHML_Enquiry_Status"], [id$="HHML_Enquiry_Status"]';
      const hasEnquiryStatusInDom = () => !!document.querySelector(enquiryStatusFieldSelector);
      const statusValueIsOpen = (el) => {
        if (!el) return false;
        let raw = '';
        const tag = String(el.tagName || '').toLowerCase();
        if (tag === 'select') {
          const o = el.selectedOptions && el.selectedOptions[0];
          raw = o ? String(o.textContent || o.value || '') : String(el.value || '');
        } else {
          raw = el.value != null && String(el.value).trim() !== '' ? String(el.value) : String(el.textContent || '');
        }
        return norm(raw) === 'open';
      };
      const applyOpenEnquiryFilter = (rowCount, enquiryNumber) => {
        if (!hasEnquiryStatusInDom()) {
          return { rowCount, enquiryNumber };
        }
        let rc = 0;
        let en = '';
        for (const inp of document.querySelectorAll('input[name="Enquiry_"], textarea[name="Enquiry_"]')) {
          const v = (inp.value != null ? String(inp.value) : '').trim();
          if (isEmptyEnq(v)) continue;
          const tr = inp.closest('tr');
          const st = tr && tr.querySelector(enquiryStatusFieldSelector);
          if (!st || !statusValueIsOpen(st)) continue;
          rc += 1;
          if (!en) en = v;
        }
        if (rc === 0) {
          for (const tr of document.querySelectorAll('table tbody tr, tr[role="row"], tr.jqgrow')) {
            if (tr.closest('thead')) continue;
            const st = tr.querySelector(enquiryStatusFieldSelector);
            if (!st || !statusValueIsOpen(st)) continue;
            let hit = false;
            let ev = '';
            for (const inp of tr.querySelectorAll('input[name="Enquiry_"], textarea[name="Enquiry_"]')) {
              const iv = (inp.value || '').trim();
              if (!isEmptyEnq(iv)) { hit = true; ev = iv; break; }
            }
            if (!hit) {
              for (const a of tr.querySelectorAll('a')) {
                const t = (a.textContent || '').trim();
                if (textLooksLikeHeroEnquiryKey(t)) { hit = true; ev = t; break; }
              }
            }
            if (hit) { rc += 1; if (!en) en = ev; }
          }
        }
        return { rowCount: rc, enquiryNumber: rc > 0 ? en : '' };
      };
      const withOpenEnquiryCounts = (payload) => {
        const f = applyOpenEnquiryFilter(payload.rowCount, payload.enquiryNumber);
        const baseDiag = payload.diag || {};
        return {
          ...payload,
          rowCount: f.rowCount,
          enquiryNumber: f.enquiryNumber,
          diag: {
            ...baseDiag,
            openEnquiryStatusFiltered: hasEnquiryStatusInDom(),
          },
        };
      };

      let headerFound = !!document.querySelector(
        '#jqgh_s_1_l_Enquiry_, [aria-describedby*="Enquiry_"], th[id*="Enquiry_"], th[name="Enquiry_"]'
      );
      let jqghIdHit = '';
      if (headerFound) {
        const _jid = document.querySelector('#jqgh_s_1_l_Enquiry_');
        if (_jid) jqghIdHit = String(_jid.id || '').slice(0, 80);
      }
      if (!headerFound) {
        const jqhAll = Array.from(document.querySelectorAll('[id^="jqgh_"]'));
        for (const el of jqhAll) {
          const id = String(el.id || '');
          if (/enquiry/i.test(id) && /jqgh/i.test(id)) {
            headerFound = true;
            jqghIdHit = id.slice(0, 80);
            break;
          }
        }
      }
      if (!headerFound) {
        const hdrNodes = Array.from(document.querySelectorAll('th, td, div, span, a'));
        for (const n of hdrNodes) {
          const t = norm(n.textContent || '');
          if (t === 'enquiry#' || t === 'enquiry #' || t === 'enquiry no' || t === 'enquiry no.'
              || t === 'enquiries' || t === 'enquiry list'
              || (t.includes('enquiry') && (t.includes('#') || t.includes('no')))) {
            headerFound = true;
            break;
          }
        }
      }
      const allInputs = Array.from(document.querySelectorAll('input, textarea'));
      const fuzzyNamed = allInputs.filter((el) => nameLooksEnquiryCol(el.getAttribute('name')));
      const fuzzyNonEmpty = fuzzyNamed.filter((el) => !isEmptyEnq(el.value));
      const sampleEnquiryNames = fuzzyNamed.slice(0, 12).map((el) => String(el.getAttribute('name') || '').slice(0, 72));

      if (!headerFound) {
        const _hl = heroLinksInApplet();
        if (_hl.cnt > 0 && _hl.best) {
          return withOpenEnquiryCounts({
            checked: true, rowCount: _hl.cnt, enquiryNumber: _hl.best,
            diag: {
              headerFound: false, jqghIdHit,
              exactEnquiryUnderscore: document.querySelectorAll('input[name="Enquiry_"], textarea[name="Enquiry_"]').length,
              fuzzyEnquiryNameFields: fuzzyNamed.length,
              fuzzyNonEmptyValues: fuzzyNonEmpty.length,
              sampleEnquiryNames,
              usedHeroLinkScan: true,
              heroLinkScope: _hl.scope,
            },
          });
        }
        return withOpenEnquiryCounts({
          checked: false, rowCount: 0, enquiryNumber: '',
          diag: {
            headerFound: false, jqghIdHit,
            exactEnquiryUnderscore: document.querySelectorAll('input[name="Enquiry_"], textarea[name="Enquiry_"]').length,
            fuzzyEnquiryNameFields: fuzzyNamed.length,
            fuzzyNonEmptyValues: fuzzyNonEmpty.length,
            sampleEnquiryNames,
            heroLinkScope: _hl.scope,
          },
        });
      }

      let rowCount = 0;
      let enquiryNumber = '';
      // Hero / Siebel Open UI: Enquiry# is often in list column inputs, not td innerText.
      const enqFields = Array.from(
        document.querySelectorAll('input[name="Enquiry_"], textarea[name="Enquiry_"]')
      );
      for (const inp of enqFields) {
        const v = (inp.value != null ? String(inp.value) : '').trim();
        if (isEmptyEnq(v)) continue;
        rowCount += 1;
        if (!enquiryNumber) enquiryNumber = v;
      }
      if (rowCount > 0) {
        return withOpenEnquiryCounts({
          checked: true, rowCount, enquiryNumber,
          diag: {
            headerFound: true, jqghIdHit, usedFuzzyFallback: false,
            exactEnquiryUnderscore: enqFields.length,
            fuzzyEnquiryNameFields: fuzzyNamed.length,
            fuzzyNonEmptyValues: fuzzyNonEmpty.length,
            sampleEnquiryNames,
          },
        });
      }

      const valueLooksLikeEnquiryNo = (v) => {
        const s = String(v || '').trim();
        if (s.length < 3 || s.length > 120) return false;
        if (/SENQ/i.test(s)) return true;
        if (!/^[A-Z0-9./\\s_-]+$/i.test(s)) return false;
        return true;
      };
      if (rowCount === 0 && fuzzyNonEmpty.length > 0) {
        const numLike = fuzzyNonEmpty.filter((el) => valueLooksLikeEnquiryNo(el.value));
        if (numLike.length > 0) {
          rowCount = numLike.length;
          enquiryNumber = String(numLike[0].value != null ? numLike[0].value : '').trim();
          return withOpenEnquiryCounts({
            checked: true, rowCount, enquiryNumber,
            diag: {
              headerFound: true, jqghIdHit, usedFuzzyFallback: true,
              exactEnquiryUnderscore: enqFields.length,
              fuzzyEnquiryNameFields: fuzzyNamed.length,
              fuzzyNonEmptyValues: fuzzyNonEmpty.length,
              sampleEnquiryNames: fuzzyNamed.slice(0, 12).map((el) => String(el.getAttribute('name') || '').slice(0, 72)),
            },
          });
        }
      }

      const tables = [];
      let jqh = document.querySelector('#jqgh_s_1_l_Enquiry_');
      if (!jqh) {
        for (const el of document.querySelectorAll('[id^="jqgh_"]')) {
          if (/enquiry/i.test(el.id || '')) {
            jqh = el;
            break;
          }
        }
      }
      if (jqh) {
        const anc = jqh.closest('table');
        if (anc) tables.push(anc);
      }
      for (const tbl of document.querySelectorAll('table')) {
        if (!tables.includes(tbl)) tables.push(tbl);
      }
      for (const tbl of tables) {
        const ttxt = norm(tbl.innerText || '');
        if (!ttxt.includes('enquiry#') && !ttxt.includes('enquiry #')
            && !ttxt.includes('enquiries') && !ttxt.includes('enquiry list')
            && !ttxt.includes('enquiry no') && !(ttxt.includes('enquiry') && ttxt.includes('no'))) continue;

        let enqColIdx = -1;
        const hdrRow = tbl.querySelector('thead tr, tr');
        if (hdrRow) {
          const hCells = hdrRow.querySelectorAll('th, td');
          for (let ci = 0; ci < hCells.length; ci++) {
            const ht = norm(hCells[ci].textContent || '');
            if (ht === 'enquiry#' || ht === 'enquiry #' || ht === 'enquiry no' || ht === 'enquiry no.'
                || (ht.includes('enquiry') && (ht.includes('#') || ht.includes('no')))) {
              enqColIdx = ci;
              break;
            }
          }
        }

        const rows = Array.from(tbl.querySelectorAll('tbody tr, tr')).filter((tr) => {
          if (!vis(tr)) return false;
          const cls = tr.className || '';
          const tdCount = tr.querySelectorAll('td').length;
          if (tdCount <= 0) return false;
          if (/jqgfirstrow/i.test(cls)) return false;
          const txt = norm(tr.textContent || '');
          if (!txt) return false;
          if (txt.includes('enquiry#') && tdCount < 2) return false;
          return true;
        });
        if (rows.length > rowCount) rowCount = rows.length;
        if (rows.length > 0 && !enquiryNumber) {
          const firstRow = rows[0];
          const cells = firstRow.querySelectorAll('td');
          if (enqColIdx >= 0 && enqColIdx < cells.length) {
            const cell = cells[enqColIdx];
            const inp = cell.querySelector('input[name="Enquiry_"], input, textarea');
            enquiryNumber = (inp && inp.value != null && String(inp.value).trim())
              ? String(inp.value).trim()
              : (cell.textContent || '').trim();
          }
          if (!enquiryNumber) {
            for (const cell of cells) {
              const inp2 = cell.querySelector('input[name="Enquiry_"], input');
              if (inp2 && String(inp2.value || '').trim()) {
                enquiryNumber = String(inp2.value).trim();
                break;
              }
              const a = cell.querySelector('a');
              const t = ((a ? a.textContent : cell.textContent) || '').trim();
              if (t && textLooksLikeHeroEnquiryKey(t)) {
                enquiryNumber = t;
                break;
              }
              if (t && /^[A-Z0-9][A-Z0-9._-]{2,}$/i.test(t) && !norm(t).includes('enquiry')) {
                enquiryNumber = t;
                break;
              }
            }
          }
        }
      }
      let usedHeroAtEnd = false;
      let heroScopeEnd = '';
      if (!enquiryNumber || rowCount === 0) {
        const _hle = heroLinksInApplet();
        if (_hle.cnt > 0 && _hle.best) {
          if (!enquiryNumber) enquiryNumber = _hle.best;
          if (rowCount === 0) rowCount = _hle.cnt;
          usedHeroAtEnd = true;
          heroScopeEnd = _hle.scope;
        }
      }
      return withOpenEnquiryCounts({
        checked: true, rowCount, enquiryNumber,
        diag: {
          headerFound: true, jqghIdHit, usedFuzzyFallback: false,
          exactEnquiryUnderscore: enqFields.length,
          fuzzyEnquiryNameFields: fuzzyNamed.length,
          fuzzyNonEmptyValues: fuzzyNonEmpty.length,
          sampleEnquiryNames,
          usedHeroLinkScan: usedHeroAtEnd,
          heroLinkScope: heroScopeEnd || undefined,
        },
      });
    }"""

    _best_cnt = 0
    _best_no = ""
    _any_checked = False
    _main = page.main_frame
    for _r in _frames_for_enquiry_subgrid_eval(page):
        try:
            _res = _r.evaluate(_js)
            if not _res:
                continue
            if _res.get("checked"):
                _any_checked = True
                _cnt = int(_res.get("rowCount") or 0)
                _enq_no = str(_res.get("enquiryNumber") or "").strip()
                if _cnt > 0 and _r == _main:
                    return True, _cnt, _enq_no
                if _cnt > _best_cnt:
                    _best_cnt = _cnt
                    _best_no = _enq_no
                elif _cnt == _best_cnt and _best_cnt > 0 and _enq_no and not _best_no:
                    _best_no = _enq_no
        except Exception:
            continue
    if _best_cnt > 0:
        return True, _best_cnt, _best_no
    if _any_checked:
        return True, 0, ""
    return False, 0, ""


def _siebel_root_evaluate(root, js: str):
    """Run ``js`` in a ``Frame`` / ``Page``, or via ``body`` for a ``FrameLocator``."""
    try:
        return root.evaluate(js)
    except Exception:
        pass
    loc_m = getattr(root, "locator", None)
    if loc_m is not None:
        for sel in ("body", "html"):
            try:
                return loc_m(sel).evaluate(js)
            except Exception:
                continue
    return None

def _frame_iframe_title_matches_payment_lines(frame: Frame) -> bool:
    """True when this frame is an ``iframe`` whose ``title`` indicates the Payment Lines applet."""
    try:
        fe = frame.frame_element()
        t = (fe.get_attribute("title") or "").strip().lower()
        return "payment line" in t
    except Exception:
        return False


def _siebel_frame_has_payment_lines_hhml_grid(frame: Frame) -> bool:
    """True when the document lists Payment Lines grid markers (e.g. **HHML_Transaction_No** column)."""
    js = """() => {
      return !!document.querySelector(
        '[aria-describedby*="HHML_Transaction_No"], th[id*="HHML_Transaction_No"], [id$="_l_HHML_Transaction_No"], table[name*="ui-jqgri-ftable" i], table[name*="ui-jqgrid-ftable" i], table.ui-jqgri-ftable, table.ui-jqgrid-ftable'
      );
    }"""
    try:
        return bool(frame.evaluate(js))
    except Exception:
        return False


def _siebel_root_has_payment_lines_toolbar(root) -> bool:
    """True when this root's document shows Payment Lines **List:New** / Save toolbar (``+`` context)."""
    js = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const sels = [
        "[aria-label='Payment Lines List:New']",
        "[title='Payment Lines List:New']",
        "[aria-label='Payment Lines List: Save']",
        "[aria-label='Payment Lines List:Save']",
        "[title='Payment Lines List: Save']",
        "[title='Payment Lines List:Save']",
        "a.siebui-icon-new",
        "button.siebui-icon-new",
        "a.siebui-icon-save",
        "button.siebui-icon-save",
      ];
      for (const s of sels) {
        if (vis(document.querySelector(s))) return true;
      }
      return false;
    }"""
    return bool(_siebel_root_evaluate(root, js))


def _payment_lines_list_has_populated_transaction_number(root) -> bool:
    """
    In the Payment Lines document (often iframe **title=\"Payment Lines\"**), true when a grid cell
    bound to **HHML_Transaction_No** (``aria-describedby`` e.g. ``s_2_l_HHML_Transaction_No``) has a
    committed value.
    """
    js = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const normCell = (s) => String(s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const isPlaceholder = (v) => {
        const t = v.toLowerCase();
        if (!v || t === '-' || t === '—' || t === 'n/a' || t === 'na') return true;
        if (t.includes('click') && t.includes('add')) return true;
        return false;
      };
      const looksLikeTxnId = (v) => {
        if (isPlaceholder(v)) return false;
        const core = v.replace(/\\s/g, '');
        if (core.length < 2) return false;
        if (/^[A-Z0-9][A-Z0-9\\-_/]{0,48}$/i.test(core)) return true;
        if (/\\d{4,}/.test(v) && /[A-Za-z]/.test(v)) return true;
        if (/^\\d{4,}$/.test(v)) return true;
        return false;
      };

      // Hero HHML: jqGrid cells use aria-describedby *HHML_Transaction_No* (id prefix may be s_2_l_, etc.)
      for (const td of document.querySelectorAll('tbody td[aria-describedby]')) {
        const adb = (td.getAttribute('aria-describedby') || '').toLowerCase();
        if (!adb.includes('hhml_transaction_no')) continue;
        if (!vis(td)) continue;
        const v = normCell(td.innerText || td.textContent || '');
        if (looksLikeTxnId(v)) return true;
      }

      const tables = document.querySelectorAll(
        'table.ui-jqgrid-btable, div.ui-jqgrid-bdiv table, table.siebui-list, table.siebui-list table, table[name*="ui-jqgri-ftable" i], table[name*="ui-jqgrid-ftable" i], table.ui-jqgri-ftable, table.ui-jqgrid-ftable'
      );
      for (const table of tables) {
        if (!vis(table)) continue;
        let txnCol = -1;
        for (const htr of table.querySelectorAll('thead tr')) {
          const cells = htr.querySelectorAll('th, td');
          cells.forEach((cell, i) => {
            const t = normCell(cell.innerText || cell.textContent || '').toLowerCase();
            if (!t) return;
            if ((t.includes('transaction') || /^txn\\b/.test(t) || /^trans\\.?\\s*#/.test(t)) &&
                (t.includes('#') || t.includes('number') || t.includes(' num') || t.includes('no.')))
              txnCol = i;
          });
        }
        if (txnCol < 0) {
          const firstData = table.querySelector('tbody tr.jqgrow, tbody tr[role="row"]');
          if (firstData) {
            firstData.querySelectorAll('td').forEach((td, i) => {
              const adb = (td.getAttribute('aria-describedby') || '').toLowerCase();
              if (adb.includes('hhml_transaction_no')) txnCol = i;
              else if (adb.includes('transaction') && (adb.includes('num') || adb.includes('seq') || adb.includes('txn') || adb.includes('transaction_no')))
                txnCol = i;
            });
          }
        }
        if (txnCol >= 0) {
          for (const tr of table.querySelectorAll('tbody tr.jqgrow, tbody tr[role="row"]')) {
            if (!vis(tr)) continue;
            const tds = tr.querySelectorAll('td');
            if (tds.length <= txnCol) continue;
            const v = normCell(tds[txnCol].innerText || tds[txnCol].textContent || '');
            if (looksLikeTxnId(v)) return true;
          }
        }
      }
      for (const tr of document.querySelectorAll('tbody tr.jqgrow, tbody tr[role="row"]')) {
        if (!vis(tr)) continue;
        for (const td of tr.querySelectorAll('td')) {
          const adb = (td.getAttribute('aria-describedby') || '').toLowerCase();
          if (adb.includes('hhml_transaction_no')) {
            const v = normCell(td.innerText || td.textContent || '');
            if (looksLikeTxnId(v)) return true;
            continue;
          }
          if (!adb.includes('transaction')) continue;
          if (!adb.includes('num') && !adb.includes('seq') && !adb.includes('txn') && !adb.includes('transaction_no')) continue;
          const v = normCell(td.innerText || td.textContent || '');
          if (looksLikeTxnId(v)) return true;
        }
      }
      // Tenant variant: Payment Lines table uses marker name/class `ui-jqgri-ftable` and may not
      // expose HHML_Transaction_No aria-describedby in row cells. Treat any populated data row as existing.
      for (const table of document.querySelectorAll(
        'table[name*="ui-jqgri-ftable" i], table[name*="ui-jqgrid-ftable" i], table.ui-jqgri-ftable, table.ui-jqgrid-ftable'
      )) {
        if (!vis(table)) continue;
        for (const tr of table.querySelectorAll('tbody tr, tr')) {
          if (!vis(tr)) continue;
          const cls = String(tr.className || '').toLowerCase();
          if (cls.includes('jqgfirstrow') || cls.includes('header')) continue;
          const tds = tr.querySelectorAll('td');
          if (!tds || tds.length < 2) continue;
          let hasData = false;
          for (const td of tds) {
            const v = normCell(td.innerText || td.textContent || td.getAttribute('title') || '');
            if (v && !isPlaceholder(v)) {
              hasData = true;
              break;
            }
          }
          if (hasData) return true;
        }
      }
      return false;
    }"""
    return bool(_siebel_root_evaluate(root, js))


def _payment_lines_detection_reason(root) -> str:
    """
    Best-effort reason string for Payment Lines row detection.

    Returns one of:
    - ``hhml_transaction_no``
    - ``ui_jqgri_ftable_row``
    - ``none``
    """
    js = """() => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      };
      const normCell = (s) => String(s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const isPlaceholder = (v) => {
        const t = v.toLowerCase();
        if (!v || t === '-' || t === '—' || t === 'n/a' || t === 'na') return true;
        if (t.includes('click') && t.includes('add')) return true;
        return false;
      };
      for (const td of document.querySelectorAll('tbody td[aria-describedby]')) {
        const adb = (td.getAttribute('aria-describedby') || '').toLowerCase();
        if (!adb.includes('hhml_transaction_no')) continue;
        if (!vis(td)) continue;
        const v = normCell(td.innerText || td.textContent || '');
        if (v && !isPlaceholder(v)) return 'hhml_transaction_no';
      }
      for (const table of document.querySelectorAll(
        'table[name*="ui-jqgri-ftable" i], table[name*="ui-jqgrid-ftable" i], table.ui-jqgri-ftable, table.ui-jqgrid-ftable'
      )) {
        if (!vis(table)) continue;
        for (const tr of table.querySelectorAll('tbody tr, tr')) {
          if (!vis(tr)) continue;
          const cls = String(tr.className || '').toLowerCase();
          if (cls.includes('jqgfirstrow') || cls.includes('header')) continue;
          const tds = tr.querySelectorAll('td');
          if (!tds || tds.length < 2) continue;
          for (const td of tds) {
            const v = normCell(td.innerText || td.textContent || td.getAttribute('title') || '');
            if (v && !isPlaceholder(v)) return 'ui_jqgri_ftable_row';
          }
        }
      }
      return 'none';
    }"""
    try:
        return str(_siebel_root_evaluate(root, js) or "none").strip()
    except Exception:
        return "none"


def _payment_line_toolbar_roots_priority(root) -> tuple:
    """Prefer **Frame** payment contexts: toolbar+grid together, then titled **Payment Lines** iframe."""
    if not isinstance(root, Frame):
        return (3, 99)
    tb = _siebel_root_has_payment_lines_toolbar(root)
    gr = _siebel_frame_has_payment_lines_hhml_grid(root)
    ti = _frame_iframe_title_matches_payment_lines(root)
    if tb and gr:
        return (0, 0)
    if ti and gr:
        return (0, 1)
    if tb and ti:
        return (0, 2)
    if tb:
        return (1, 0)
    if ti or gr:
        return (1, 2)
    return (2, 0)


def _resolve_builtin_contact_find_grid_frame(page: Page) -> Frame | None:
    """
    Single **Frame** for the Contact Find **Search Results** / title grid when the loaded **frame.url**
    matches the builtin (or env) mobile-search hint — avoids walking every iframe when the grid is here.

    Order: **main_frame** first (common Hero layout), then other ``page.frames`` from ``_ordered_frames``.
    """
    hint = _load_mobile_search_hit_hint_dict_from_config()
    roots_sorted = hint.get("roots_sorted") if isinstance(hint, dict) else None
    page_top = str(hint.get("page_url_top") or "") if isinstance(hint, dict) else ""
    if not isinstance(roots_sorted, list) or not roots_sorted:
        return None
    main = page.main_frame
    for entry in roots_sorted:
        if not isinstance(entry, dict):
            continue
        try:
            fu_m = (main.url or "").strip()
        except Exception:
            fu_m = ""
        if _frame_url_matches_payment_hint(fu_m, entry, page_top):
            return main
    for entry in roots_sorted:
        if not isinstance(entry, dict):
            continue
        for frame in _ordered_frames(page):
            if frame == main:
                continue
            try:
                fu = (frame.url or "").strip()
            except Exception:
                fu = ""
            if _frame_url_matches_payment_hint(fu, entry, page_top):
                return frame
    return None

def _try_payment_line_roots_from_hint(page: Page, hint: dict[str, object]) -> list | None:
    """
    Return ``[Frame]`` when a single ``page`` frame matches the hint entry and passes Payment Lines
    toolbar/title/grid verification; else ``None`` (caller runs full gather).
    """
    roots_sorted = hint.get("roots_sorted")
    if not isinstance(roots_sorted, list) or not roots_sorted:
        return None
    idx = int(hint.get("payment_lines_root_index_primary") or 0)
    if idx < 0 or idx >= len(roots_sorted):
        idx = 0
    entry = roots_sorted[idx]
    if not isinstance(entry, dict):
        return None
    page_top = str(hint.get("page_url_top") or "")
    tit_needle = str(entry.get("iframe_element_title") or "").strip()

    for frame in _ordered_frames(page):
        try:
            fu = frame.url or ""
            if not _frame_url_matches_payment_hint(fu, entry, page_top):
                continue
            if tit_needle:
                try:
                    fe = frame.frame_element()
                    t = (fe.get_attribute("title") or "").strip()
                    if not t:
                        continue
                    tl, tn = tit_needle.lower(), t.lower()
                    if tn not in tl and tl not in tn and tl != tn:
                        continue
                except Exception:
                    continue
            if (
                _siebel_root_has_payment_lines_toolbar(frame)
                or _frame_iframe_title_matches_payment_lines(frame)
                or _siebel_frame_has_payment_lines_hhml_grid(frame)
            ):
                return [frame]
        except Exception:
            continue
    return None


def _iter_frame_locator_roots_only(page: Page, content_frame_selector: str | None):
    """Chained ``FrameLocator`` roots only (no ``Frame`` walk) — used after fast frame scan."""
    yield from _iter_frame_locator_roots(page, content_frame_selector)


def _gather_payment_line_toolbar_roots(page: Page, content_frame_selector: str | None) -> list:
    """Frames / locators where **Payment Lines** lives: **List:New** toolbar, HHML grid, or iframe title.

    Scan real ``Frame`` objects first (``frame.evaluate`` is cheap). Only if none match do we probe
    ``FrameLocator`` chains from ``DMS_SIEBEL_CONTENT_FRAME_SELECTOR`` / auto iframe selectors — those
    can block on ``body`` resolution when run first.
    """
    out: list = []
    seen: set[int] = set()

    def _add(r) -> None:
        k = id(r)
        if k in seen:
            return
        seen.add(k)
        out.append(r)

    for frame in _ordered_frames(page):
        try:
            if (
                _siebel_root_has_payment_lines_toolbar(frame)
                or _frame_iframe_title_matches_payment_lines(frame)
                or _siebel_frame_has_payment_lines_hhml_grid(frame)
            ):
                _add(frame)
        except Exception:
            continue

    if not out:
        for root in _iter_frame_locator_roots_only(page, content_frame_selector):
            try:
                if _siebel_root_has_payment_lines_toolbar(root):
                    _add(root)
            except Exception:
                continue

    return out


_PAYMENT_LINES_SAVE_ICON_SELECTORS = (
    "a[aria-label='Payment Lines List:Save']",
    "button[aria-label='Payment Lines List:Save']",
    "a[title='Payment Lines List:Save']",
    "button[title='Payment Lines List:Save']",
    "a[aria-label='Payment Lines List: Save']",
    "button[aria-label='Payment Lines List: Save']",
    "a[title='Payment Lines List: Save']",
    "button[title='Payment Lines List: Save']",
    "a[title*='Save' i]",
    "button[title*='Save' i]",
    "a[aria-label*='Save' i]",
    "button[aria-label*='Save' i]",
    "a.siebui-icon-save",
    "button.siebui-icon-save",
)


def _merge_payment_lines_toolbar_roots_for_save(
    page: Page,
    content_frame_selector: str | None,
    initial_roots: list,
) -> list:
    """Initial ``+`` roots first, then a fresh gather so the Save control's frame is included after edits."""
    out: list = []
    seen: set[int] = set()
    for r in list(initial_roots) + list(_gather_payment_line_toolbar_roots(page, content_frame_selector) or []):
        k = id(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out if out else list(initial_roots)


def _try_click_payment_lines_save_icon(
    roots: list,
    *,
    action_timeout_ms: int,
) -> tuple[bool, str | None]:
    """Returns (clicked, selector_that_worked_or_none)."""
    for sroot in roots:
        for css in _PAYMENT_LINES_SAVE_ICON_SELECTORS:
            try:
                btn = sroot.locator(css).first
                if btn.count() > 0 and btn.is_visible(timeout=500):
                    try:
                        btn.click(timeout=action_timeout_ms)
                    except Exception:
                        btn.click(timeout=action_timeout_ms, force=True)
                    return True, css
            except Exception:
                continue
    return False, None


def _poll_payment_lines_transaction_verified(
    page: Page,
    content_frame_selector: str | None,
    *,
    note: Callable[..., object],
    total_ms: int = 9000,
    step_ms: int = 450,
) -> bool:
    """Poll grid roots until Transaction# (or tenant row heuristic) appears or timeout."""
    t0 = time.monotonic()
    attempt = 0
    while True:
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if elapsed_ms >= total_ms:
            break
        attempt += 1
        for _vr in _gather_payment_line_toolbar_roots(page, content_frame_selector):
            try:
                if _payment_lines_list_has_populated_transaction_number(_vr):
                    _det_via = _payment_lines_detection_reason(_vr)
                    note(
                        "Payments: post-save row detection matched "
                        f"(detected_via={_det_via}, poll_attempt={attempt})."
                    )
                    return True
            except Exception:
                continue
        remaining_ms = total_ms - (time.monotonic() - t0) * 1000.0
        if remaining_ms <= 0:
            break
        _safe_page_wait(
            page,
            int(min(step_ms, max(80, remaining_ms))),
            log_label=f"payment_txn_poll_{attempt}",
        )
    return False


def _add_customer_payment(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    vehicle_context: dict | None = None,
) -> tuple[bool, str]:
    """
    Open **Payments**, locate the frame(s) where **Payment Lines List:New** (``+``) lives, then **in that
    document** check the grid for a row with a populated **Transaction #**. If present, skip add.
    Otherwise click ``+``, fill Type / Mode / Amount and Save.

    Amount rule:
    - if vehicle_type starts with ``motorcycle`` (tolerates ``motorcyle`` typo) and cubic_capacity < 130:
      amount = ``90000``
    - else amount = ``120000``

    Returns ``(True, "")`` on success. On failure, ``(False, code)`` where ``code`` is one of:
    ``no_payment_lines_root``, ``payment_lines_frame``, ``payment_plus``, ``payment_save``,
    ``payment_verify``, ``payment_exception`` — used for operator ``step`` / ``out["error"]`` text.

    Save: **Payment Lines** Save icon first (merged toolbar roots), **Ctrl+S** if no icon; post-save
    **Transaction#** detection is polled; on miss, the **alternate** save (icon vs Ctrl+S) is tried once.
    """
    _vt_raw = str((vehicle_context or {}).get("vehicle_type") or "").strip()
    _cc_raw = str((vehicle_context or {}).get("cubic_capacity") or "").strip()
    _cc_num = _normalize_cubic_cc_digits(_cc_raw)
    try:
        _cc_val = float(_cc_num) if _cc_num else 0.0
    except Exception:
        _cc_val = 0.0
    _vt_norm = re.sub(r"[^a-z]", "", _vt_raw.lower())
    _is_motorcycle = _vt_norm.startswith("motorcycle") or _vt_norm.startswith("motorcyle")
    _txn_amount = "90000" if (_is_motorcycle and _cc_val > 0 and _cc_val < 130) else "120000"
    note(
        "Payment amount rule: "
        f"vehicle_type={_vt_raw!r}, cubic_capacity={_cc_raw!r}, "
        f"cc_num={_cc_num!r}, transaction_amount={_txn_amount!r}."
    )
    note("Payments: starting Add customer payment (tab activation, then Payment Lines roots / '+').")

    _safe_page_wait(page, 250, log_label="before_payments_plus_click")

    # Primary path: short Payments-tab activation first, then root discovery.
    _tab_tmo = int(min(4000, max(1200, action_timeout_ms // 4)))
    if _siebel_try_activate_payments_tab(
        page,
        action_timeout_ms=_tab_tmo,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        _safe_page_wait(page, 800, log_label="after_payments_tab_activate_primary")
    payment_toolbar_roots: list = []
    _hint_cfg = _load_payment_lines_hint_dict_from_config()
    _hinted = _try_payment_line_roots_from_hint(page, _hint_cfg)
    if _hinted:
        payment_toolbar_roots = _hinted
        _is_builtin = (_hint_cfg.get("hint_source") == "builtin") or (
            str(_hint_cfg.get("trial_run_id") or "").strip() == "hero_builtin_default"
        )
        if _is_builtin:
            note(
                "Payments: matched Payment Lines root from built-in Hero hint "
                "(fast path; full frame scan skipped)."
            )
        else:
            note(
                "Payments: matched Payment Lines root from DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE / "
                "_ROOT_HINT_JSON (fast path; full frame scan skipped)."
            )
    else:
        note(
            "Payments: payment-lines hint (built-in or env) did not match a verified frame — "
            "falling back to full gather."
        )
    if not payment_toolbar_roots:
        payment_toolbar_roots = _gather_payment_line_toolbar_roots(page, content_frame_selector)
    if not payment_toolbar_roots:
        note(
            "Payments: no Payment Lines root after primary tab activation; "
            "retrying root search without extra tab wait."
        )
        payment_toolbar_roots = _gather_payment_line_toolbar_roots(page, content_frame_selector)

    payment_toolbar_roots.sort(key=_payment_line_toolbar_roots_priority)
    if payment_toolbar_roots:
        try:
            note(f"Payments: {len(payment_toolbar_roots)} Payment Lines root(s).")
        except Exception:
            pass

    if not payment_toolbar_roots:
        note(
            "Payment debug: Payment Lines toolbar (List:New / Save) not found — "
            "cannot locate '+' frame; ensure the Payments view shows Payment Lines."
        )
        return False, "no_payment_lines_root"

    def _try_receipts_query_in_root(root, idx: int) -> bool:
        """
        User-directed probe on the same Payment Lines root as ``+``:
        click ``name='s_2_1_1_0'`` -> Tab -> type ``Receipts`` -> Tab -> Enter.
        """
        try:
            fld = root.locator("input[name='s_2_1_1_0'], textarea[name='s_2_1_1_0']").first
            if fld.count() == 0 or not fld.is_visible(timeout=700):
                return False
            try:
                fld.click(timeout=min(2500, action_timeout_ms))
            except Exception:
                fld.click(timeout=min(2500, action_timeout_ms), force=True)
            _safe_page_wait(page, 120, log_label=f"payment_receipts_probe_click_{idx}")
            page.keyboard.press("Tab")
            _safe_page_wait(page, 100, log_label=f"payment_receipts_probe_tab1_{idx}")
            page.keyboard.type("Receipts")
            _safe_page_wait(page, 120, log_label=f"payment_receipts_probe_type_{idx}")
            page.keyboard.press("Tab")
            _safe_page_wait(page, 120, log_label=f"payment_receipts_probe_tab2_{idx}")
            page.keyboard.press("Enter")
            _safe_page_wait(page, 1200, log_label=f"payment_receipts_probe_enter_{idx}")
            return True
        except Exception:
            return False

    for idx, pr in enumerate(payment_toolbar_roots):
        try:
            _try_receipts_query_in_root(pr, idx)
            if _payment_lines_list_has_populated_transaction_number(pr):
                _det_via = _payment_lines_detection_reason(pr)
                note(
                    "Payments: Payment Lines list already has a row with populated Transaction# — "
                    f"skipping '+' and new-line entry (detected_via={_det_via})."
                )
                return True, ""
        except Exception:
            continue

    plus_selectors = (
        "a[aria-label='Payment Lines List:New']",
        "button[aria-label='Payment Lines List:New']",
        "a[title='Payment Lines List:New']",
        "button[title='Payment Lines List:New']",
        "button[title='+']",
        "a[title='+']",
        "[role='button'][aria-label='+']",
        "button[aria-label*='new' i]",
        "a[aria-label*='new' i]",
        "button[title*='new' i]",
        "a[title*='new' i]",
        "button[title*='add' i]",
        "a[title*='add' i]",
        "button.siebui-icon-new",
        "a.siebui-icon-new",
    )

    def _click_plus_in_root(root) -> bool:
        # Avoid ``page`` default timeouts (often 60s+) on buried iframes; cap interaction waits.
        _plus_to = int(min(12_000, max(2_500, action_timeout_ms // 5)))
        _vis_ms = int(min(2_000, max(400, _plus_to // 4)))
        for css in plus_selectors:
            try:
                c = root.locator(css).first
                if c.count() > 0 and c.is_visible(timeout=_vis_ms):
                    try:
                        c.click(timeout=_plus_to)
                    except Exception:
                        c.click(timeout=_plus_to, force=True)
                    return True
            except Exception:
                continue
        return False

    root_candidates = payment_toolbar_roots

    for root in root_candidates:
        try:
            if _click_plus_in_root(root):
                note("Clicked '+' icon on Payments tab.")
                _safe_page_wait(page, 500, log_label="after_payments_plus_click")
                note("Payment sequence: '+' -> Transaction Amount -> Transaction Type -> Payment Lines Save icon.")

                # Lock to the frame containing Payment Lines editable row fields.
                payment_frames: list[Frame] = []
                for frame in _ordered_frames(page):
                    try:
                        has_payment_lines_marker = bool(
                            frame.evaluate(
                                """() => {
                                  const vis = (el) => {
                                    if (!el) return false;
                                    const st = window.getComputedStyle(el);
                                    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                                    const r = el.getBoundingClientRect();
                                    return r.width >= 2 && r.height >= 2;
                                  };
                                  const sels = [
                                    "[id='1_s_2_1_Transaction_Amount']",
                                    "[name='1_s_2_1_Transaction_Amount']",
                                    "[title_id='1_s_2_1_Transaction_Amount']",
                                    "[title-id='1_s_2_1_Transaction_Amount']",
                                    "[title='1_s_2_1_Transaction_Amount']",
                                    "[name='Transaction_Type']",
                                    "[name='Transaction_Type_New']",
                                    "[id='Transaction_Type']",
                                    "[title_id='Transaction_Type']",
                                    "[title-id='Transaction_Type']",
                                    "input[name*='Transaction_Type' i]",
                                    "input[id*='Transaction_Type' i]",
                                    "[name='Transaction_Amount']",
                                  ];
                                  for (const s of sels) {
                                    const el = document.querySelector(s);
                                    if (vis(el)) return true;
                                  }
                                  return false;
                                }"""
                            )
                        )
                        if has_payment_lines_marker:
                            payment_frames.append(frame)
                    except Exception:
                        continue

                if payment_frames:
                    try:
                        note(f"Payment lines scoped frame locked: url={(payment_frames[0].url or '')[:180]!r}, name={payment_frames[0].name!r}")
                    except Exception:
                        pass
                else:
                    note("Payment lines scoped frame not detected by strict markers; trying relaxed visible-input scan.")
                    for frame in _ordered_frames(page):
                        try:
                            maybe_data_frame = bool(
                                frame.evaluate(
                                    """() => {
                                      const vis = (el) => {
                                        if (!el) return false;
                                        const st = window.getComputedStyle(el);
                                        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                                        const r = el.getBoundingClientRect();
                                        return r.width >= 2 && r.height >= 2;
                                      };
                                      const inputs = Array.from(document.querySelectorAll("input, textarea"));
                                      let visibleEdits = 0;
                                      for (const el of inputs) {
                                        if (!vis(el)) continue;
                                        const t = (el.getAttribute("type") || "").toLowerCase();
                                        if (t === "hidden") continue;
                                        visibleEdits += 1;
                                        if (visibleEdits >= 4) return true;
                                      }
                                      return false;
                                    }"""
                                )
                            )
                            if maybe_data_frame:
                                payment_frames.append(frame)
                                break
                        except Exception:
                            continue
                    if payment_frames:
                        try:
                            note(f"Payment lines relaxed frame lock: url={(payment_frames[0].url or '')[:180]!r}, name={payment_frames[0].name!r}")
                        except Exception:
                            pass
                    else:
                        note("Payment lines scoped frame not detected; stopping to avoid focus drift.")
                        return False, "payment_lines_frame"
                # Prefer the exact frame that contains Transaction_Type fields.
                tx_frame = None
                for frame in _ordered_frames(page):
                    try:
                        has_txn_type = bool(
                            frame.evaluate(
                                """() => {
                                  const isVisible = (el) => {
                                    if (!el) return false;
                                    const st = window.getComputedStyle(el);
                                    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0) return false;
                                    const r = el.getBoundingClientRect();
                                    return r.width >= 2 && r.height >= 2;
                                  };
                                  const sels = [
                                    "input[name='Transaction_Type']",
                                    "input[name='Transaction_Type_New']",
                                    "select[name='Transaction_Type']",
                                    "select[name='Transaction_Type_New']",
                                    "input[id='Transaction_Type']",
                                    "input[title_id='Transaction_Type']",
                                    "input[title-id='Transaction_Type']",
                                    "input[name*='Transaction_Type' i]",
                                    "input[id*='Transaction_Type' i]",
                                  ];
                                  for (const s of sels) {
                                    const el = document.querySelector(s);
                                    if (el && isVisible(el)) return true;
                                  }
                                  return false;
                                }"""
                            )
                        )
                        if has_txn_type:
                            tx_frame = frame
                            break
                    except Exception:
                        continue
                if tx_frame is not None:
                    scoped_roots = [tx_frame]
                    try:
                        note(f"Transaction field frame locked: url={(tx_frame.url or '')[:180]!r}, name={tx_frame.name!r}")
                    except Exception:
                        pass
                else:
                    # Keep focus locked to the first detected Payment Lines frame.
                    scoped_roots = [payment_frames[0]]

                # Transaction Amount may appear in a sibling frame; lock it independently.
                amount_roots = scoped_roots
                amt_frame = None
                for frame in _ordered_frames(page):
                    try:
                        has_txn_amt = bool(
                            frame.evaluate(
                                """() => {
                                  const isVisible = (el) => {
                                    if (!el) return false;
                                    const st = window.getComputedStyle(el);
                                    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0) return false;
                                    const r = el.getBoundingClientRect();
                                    return r.width >= 2 && r.height >= 2;
                                  };
                                  const sels = [
                                    "input[name='Transaction_Amount']",
                                    "input[id='Transaction_Amount']",
                                    "input[id='1_s_2_1_Transaction_Amount']",
                                    "input[name='1_s_2_1_Transaction_Amount']",
                                    "input[title_id='1_s_2_1_Transaction_Amount']",
                                    "input[title-id='1_s_2_1_Transaction_Amount']",
                                    "input[title='1_s_2_1_Transaction_Amount']",
                                    "input[aria-label*='Transaction Amount' i]",
                                    "input[title*='Transaction Amount' i]",
                                  ];
                                  for (const s of sels) {
                                    const el = document.querySelector(s);
                                    if (el && isVisible(el)) return true;
                                  }
                                  return false;
                                }"""
                            )
                        )
                        if has_txn_amt:
                            amt_frame = frame
                            break
                    except Exception:
                        continue
                if amt_frame is not None:
                    amount_roots = [amt_frame]
                    try:
                        note(f"Transaction amount frame locked: url={(amt_frame.url or '')[:180]!r}, name={amt_frame.name!r}")
                    except Exception:
                        pass

                _TXN_TYPE_SELS = (
                    "input[name='Transaction_Type']",
                    "input[name='Transaction_Type_New']",
                    "select[name='Transaction_Type']",
                    "select[name='Transaction_Type_New']",
                    "input[id='Transaction_Type']",
                    "input[title_id='Transaction_Type']",
                    "input[title-id='Transaction_Type']",
                    "input[name*='Transaction_Type' i]",
                    "input[id*='Transaction_Type' i]",
                    "select[name*='Transaction_Type' i]",
                )
                _TXN_AMT_SELS = (
                    "input[name='Transaction_Amount']",
                    "input[id='Transaction_Amount']",
                    "input[id='1_s_2_1_Transaction_Amount']",
                    "input[name='1_s_2_1_Transaction_Amount']",
                    "input[title_id='1_s_2_1_Transaction_Amount']",
                    "input[title-id='1_s_2_1_Transaction_Amount']",
                    "input[title='1_s_2_1_Transaction_Amount']",
                    "input[aria-label*='Transaction Amount' i]",
                    "input[title*='Transaction Amount' i]",
                    "input[name*='Transaction_Amount' i]",
                )
                _PAY_MODE_SELS = (
                    "input[name='Payment_Method_New']",
                    "input[id='1_Payment_Method_New']",
                    "select[name='Payment_Method_New']",
                    "input[name*='Payment_Method' i]",
                    "select[name*='Payment_Method' i]",
                    "input[name*='Payment_Mode' i]",
                    "select[name*='Payment_Mode' i]",
                    "input[name*='Receipt_Type' i]",
                    "select[name*='Receipt_Type' i]",
                    "input[id*='Payment_Mode' i]",
                    "select[id*='Payment_Mode' i]",
                )

                def _direct_fill(roots, selectors, value, *, label):
                    """Locate field by selector in *roots*, click it, type value, Tab to commit.

                    Frame is used only as a locator scope — no window.focus() or
                    el.focus() calls, so focus is never forcibly trapped.
                    """
                    for r in roots:
                        for css in selectors:
                            try:
                                loc = r.locator(css).first
                                if loc.count() == 0 or not loc.is_visible(timeout=500):
                                    continue
                                tag = (loc.evaluate("el => el.tagName") or "").upper()
                                if tag == "SELECT":
                                    try:
                                        loc.select_option(
                                            label=re.compile(rf"^\s*{re.escape(value)}\s*$", re.I),
                                            timeout=action_timeout_ms,
                                        )
                                        note(f"Payment direct: {label} set via <select> → {value!r}.")
                                        return True
                                    except Exception:
                                        pass
                                _is_ro = bool(loc.evaluate("el => el.readOnly"))
                                if not _is_ro:
                                    try:
                                        loc.click(timeout=action_timeout_ms)
                                    except Exception:
                                        loc.click(timeout=action_timeout_ms, force=True)
                                    _safe_page_wait(page, 250, log_label=f"direct_after_click_{label}")
                                    try:
                                        loc.fill(value, timeout=action_timeout_ms)
                                    except Exception:
                                        loc.press("Control+a", timeout=1200)
                                        page.keyboard.type(value)
                                else:
                                    note(f"Payment direct: {label} cell readOnly, activation failed.")
                                    return False
                                _safe_page_wait(page, 120, log_label=f"direct_before_tab_{label}")
                                page.keyboard.press("Tab")
                                note(f"Payment direct: {label} filled → {value!r}.")
                                return True
                            except Exception:
                                continue
                    return False

                # Wait for the new row to fully render before addressing fields.
                _safe_page_wait(page, 1500, log_label="wait_for_new_row_render")

                # Direct field addressing: each field is located by selector,
                # clicked, filled, and Tab-committed independently.
                # No Tab-chain navigation ⇒ immune to Siebel's focus-steal timer.
                type_ok = False
                amount_ok = False
                mode_ok = False

                # 1. Transaction Type — fill first so Siebel's required-field
                #    validator is satisfied and stops stealing focus.
                type_ok = _direct_fill(
                    scoped_roots, _TXN_TYPE_SELS, "Receipt", label="Transaction_Type",
                )
                if type_ok:
                    # Wait for Siebel to process the value server-side.
                    # On some runs this triggers a ~30s server round-trip;
                    # on others it's instant.  We poll for focus stability:
                    # if focus stays away from Transaction_Type for 500ms,
                    # the value is committed and the focus-steal timer won't fire.
                    _safe_page_wait(page, 600, log_label="direct_after_txn_type_commit")
                    _focus_stable = False
                    for _fc in range(5):
                        _steal_check = None
                        for _sr in scoped_roots:
                            try:
                                _steal_check = _sr.evaluate("""() => {
                                    const ae = document.activeElement;
                                    if (!ae) return null;
                                    return { name: ae.name || '', ariaLabel: ae.getAttribute('aria-label') || '' };
                                }""")
                                if _steal_check:
                                    break
                            except Exception:
                                continue
                        _on_txn_type = False
                        if _steal_check:
                            _sc_name = (_steal_check.get("name") or "").lower()
                            _sc_label = (_steal_check.get("ariaLabel") or "").lower()
                            _on_txn_type = "transaction_type" in _sc_name or "transaction type" in _sc_label
                        if not _on_txn_type:
                            _focus_stable = True
                            break
                        # Focus was stolen back — Siebel hasn't committed yet. Wait and retry.
                        note(f"Payment direct: focus-steal detected on Transaction_Type (check {_fc}), waiting...")
                        _safe_page_wait(page, 500, log_label=f"focus_steal_wait_{_fc}")
                        # Re-fill if needed — the steal may have cleared the value.
                        try:
                            page.keyboard.type("Receipt")
                            page.keyboard.press("Tab")
                        except Exception:
                            pass
                # 2. Payment Mode — try direct selector; fall back to typing at
                #    current focus position (Tab from Transaction_Type lands here).
                mode_ok = _direct_fill(
                    scoped_roots, _PAY_MODE_SELS, "Cash", label="Payment_Mode",
                )
                if not mode_ok and type_ok:
                    try:
                        page.keyboard.type("Cash")
                        page.keyboard.press("Tab")
                        mode_ok = True
                        note("Payment direct: Payment_Mode filled via keyboard fallback.")
                    except Exception:
                        note("Payment direct: Payment_Mode keyboard fallback failed.")
                if mode_ok:
                    _safe_page_wait(page, 300, log_label="direct_after_payment_mode_commit")

                # 3. Transaction Amount — must NOT click the cell directly.
                # Siebel list cells are readOnly in display mode; clicking them
                # breaks the row's active edit context.  The only reliable way
                # to reach the cell is Tab navigation within the active row.
                # From Payment_Mode + Tab, ~3 more Tabs reach Transaction Amount.
                amount_ok = False
                note("Payment direct: Transaction_Amount — using Tab navigation (preserving edit context).")
                _tab_filled = False
                for _ti in range(8):
                    try:
                        page.keyboard.press("Tab")
                        _safe_page_wait(page, 200, log_label=f"tab_nav_amount_{_ti}")
                        _ae = None
                        for _tr in (list(amount_roots) + list(_ordered_frames(page))):
                            try:
                                _ae = _tr.evaluate("""() => {
                                    const ae = document.activeElement;
                                    if (!ae || ae === document.body) return null;
                                    return {
                                        tag: ae.tagName, name: ae.name || '',
                                        ariaLabel: ae.getAttribute('aria-label') || '',
                                        readOnly: ae.readOnly, id: ae.id || '',
                                        val: ae.value || ''
                                    };
                                }""")
                                if _ae and _ae.get("tag") in ("INPUT", "TEXTAREA", "SELECT"):
                                    break
                            except Exception:
                                continue
                        if not _ae:
                            continue
                        # In Siebel edit mode, ariaLabel is often empty — match
                        # against name and id which are reliable in edit mode.
                        _ae_name = (_ae.get("name") or "").lower()
                        _ae_id = (_ae.get("id") or "").lower()
                        _ae_label = (_ae.get("ariaLabel") or "").lower()
                        _is_txn_amount = (
                            "transaction_amount" in _ae_name
                            or "transaction_amount" in _ae_id
                            or "transaction amount" in _ae_label
                        )
                        if _is_txn_amount:
                            if not _ae.get("readOnly"):
                                try:
                                    page.keyboard.press("Control+a")
                                except Exception:
                                    pass
                                _safe_page_wait(page, 80, log_label="tab_nav_amount_clear")
                                page.keyboard.type(_txn_amount)
                                _safe_page_wait(page, 120, log_label="tab_nav_amount_fill")
                                page.keyboard.press("Tab")
                                _tab_filled = True
                                note(
                                    f"Payment direct: Transaction_Amount filled with {_txn_amount} via Tab navigation "
                                    f"(tab {_ti}, name={_ae.get('name')!r})."
                                )
                            else:
                                note(f"Payment direct: Transaction_Amount reached via Tab but still readOnly (tab {_ti}).")
                            break
                        # If focus left the Payment Lines applet, stop.
                        _ae_tag = _ae.get("tag", "")
                        if _ae_tag not in ("INPUT", "TEXTAREA", "SELECT"):
                            note(f"Payment direct: Tab navigation left Payment Lines (tag={_ae_tag}, tab {_ti}).")
                            break
                    except Exception:
                        continue
                if not _tab_filled:
                    note("Payment direct: Transaction_Amount could not be filled via Tab navigation.")
                amount_ok = _tab_filled
                if amount_ok:
                    _safe_page_wait(page, 300, log_label="direct_after_txn_amount_commit")

                note(
                    "Filled payment fields (direct): "
                    f"Type=Receipt(ok={type_ok!r}), Mode=Cash(ok={mode_ok!r}), "
                    f"Amount={_txn_amount}(ok={amount_ok!r})."
                )
                _safe_page_wait(page, 400, log_label="after_amount_before_save")

                save_action_roots = _merge_payment_lines_toolbar_roots_for_save(
                    page, content_frame_selector, payment_toolbar_roots
                )
                note(
                    f"Payment debug: save action roots merged initial + fresh gather (count={len(save_action_roots)}). "
                    "Primary save: Payment Lines Save icon; fallback: Ctrl+S."
                )

                def _payment_save_error_popup_text() -> str | None:
                    _err_msg = None
                    for _chk_root in list(_siebel_locator_search_roots(page, content_frame_selector)) + list(
                        _ordered_frames(page)
                    ):
                        try:
                            _err_msg = _chk_root.evaluate(
                                """() => {
                                  const vis = (el) => {
                                    if (!el) return false;
                                    const st = window.getComputedStyle(el);
                                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                                    const r = el.getBoundingClientRect();
                                    return r.width > 5 && r.height > 5;
                                  };
                                  for (const s of [
                                    "[role='alertdialog']", "[role='alert']",
                                    ".siebui-popup-error", ".siebui-alert",
                                    ".error-dialog", ".ui-dialog.ui-widget",
                                    "[id*='ErrorPopup']", "[class*='error' i][class*='popup' i]",
                                    "[class*='modal' i][class*='error' i]"
                                  ]) {
                                    const el = document.querySelector(s);
                                    if (el && vis(el)) {
                                      return (el.innerText || el.textContent || '').trim().substring(0, 500);
                                    }
                                  }
                                  return null;
                                }"""
                            )
                            if _err_msg:
                                break
                        except Exception:
                            continue
                    return _err_msg

                def _run_one_save_attempt(method: str) -> bool:
                    if method == "icon":
                        _ok, _css = _try_click_payment_lines_save_icon(
                            save_action_roots,
                            action_timeout_ms=action_timeout_ms,
                        )
                        if _ok:
                            note(f"Payment debug: clicked Payment Lines Save icon (primary) — {_css!r}.")
                            return True
                        return False
                    try:
                        page.keyboard.press("Control+s")
                        note("Payment debug: used Ctrl+S (Save icon not clicked this attempt).")
                        return True
                    except Exception as _save_key_ex:
                        note(f"Payment debug: Ctrl+S failed: {_save_key_ex}")
                        return False

                save_clicked = _run_one_save_attempt("icon")
                save_method_first = "icon" if save_clicked else None
                if not save_clicked:
                    save_clicked = _run_one_save_attempt("ctrl_s")
                    save_method_first = "ctrl_s" if save_clicked else None

                if save_clicked:
                    _safe_page_wait(page, 1800, log_label="after_payment_save_processing")
                    _err_msg = _payment_save_error_popup_text()
                    if _err_msg:
                        note(f"Payment save: Siebel error popup detected → {_err_msg!r:.300}")
                    else:
                        note("Payment save submitted — no error popup detected.")
                    _verify_txn = _poll_payment_lines_transaction_verified(
                        page,
                        content_frame_selector,
                        note=note,
                        total_ms=9000,
                        step_ms=450,
                    )
                    if _verify_txn:
                        note("Payments: verified Payment Lines row with populated Transaction# after save.")
                        return True, ""
                    note(
                        "Payments: post-save poll did not find Transaction# yet — "
                        "retrying alternate save (Save icon vs Ctrl+S)."
                    )
                    if save_method_first == "icon":
                        if _run_one_save_attempt("ctrl_s"):
                            _safe_page_wait(page, 1800, log_label="after_payment_alt_ctrl_s")
                            _err2 = _payment_save_error_popup_text()
                            if _err2:
                                note(f"Payment save (alt Ctrl+S): Siebel error popup → {_err2!r:.300}")
                            _verify_txn = _poll_payment_lines_transaction_verified(
                                page,
                                content_frame_selector,
                                note=note,
                                total_ms=7000,
                                step_ms=400,
                            )
                            if _verify_txn:
                                note("Payments: verified Payment Lines row after alternate Ctrl+S.")
                                return True, ""
                    elif save_method_first == "ctrl_s":
                        if _run_one_save_attempt("icon"):
                            _safe_page_wait(page, 1800, log_label="after_payment_alt_save_icon")
                            _err2 = _payment_save_error_popup_text()
                            if _err2:
                                note(f"Payment save (alt icon): Siebel error popup → {_err2!r:.300}")
                            _verify_txn = _poll_payment_lines_transaction_verified(
                                page,
                                content_frame_selector,
                                note=note,
                                total_ms=7000,
                                step_ms=400,
                            )
                            if _verify_txn:
                                note("Payments: verified Payment Lines row after alternate Save icon.")
                                return True, ""
                    note(
                        "Payments: save was attempted but no Payment Lines row with Transaction# "
                        "was detected after save and retries (see earlier Payment debug lines)."
                    )
                    return False, "payment_verify"
                note("Could not submit payment save (Save icon and Ctrl+S both failed for this attempt).")
                return False, "payment_save"
        except Exception as e:
            note(f"Add customer payment flow failed after '+' click attempt: {e}")
            return False, "payment_exception"
    note("Could not click '+' icon on Payments tab (Payment Lines List:New not visible).")
    return False, "payment_plus"


def _siebel_open_found_customer_record(
    page: Page,
    *,
    mobile: str,
    first_name: str,
    timeout_ms: int,
    content_frame_selector: str | None,
    note,
    skip_left_pane_click: bool = False,
) -> bool:
    """
    Existing-customer flow:
    1) left Search Results pane click on mobile/customer hit (optional)
    2) right Contacts applet click customer first-name link (e.g., Akash) to open full record.
    """
    if not skip_left_pane_click:
        left_ok = _siebel_try_click_mobile_search_hit_link(
            page,
            mobile,
            timeout_ms=timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        if not left_ok:
            return False
        _after_left_customer_click_wait_bounded(
            page,
            content_frame_selector=content_frame_selector,
            note=note,
            first_name=first_name,
        )
    else:
        _after_left_customer_click_wait_bounded(
            page,
            content_frame_selector=content_frame_selector,
            note=note,
            first_name=first_name,
        )

    fn = (first_name or "").strip()
    fn_pat = re.compile(rf"^\s*{re.escape(fn)}\s*$", re.I) if fn else None
    fn_contains_pat = re.compile(re.escape(fn), re.I) if fn else None

    def _clickish(loc) -> bool:
        try:
            if not loc.is_visible(timeout=700):
                return False
        except Exception:
            return False
        for act in (
            lambda: loc.click(timeout=timeout_ms),
            lambda: loc.click(timeout=timeout_ms, force=True),
            lambda: loc.dblclick(timeout=timeout_ms),
        ):
            try:
                act()
                return True
            except Exception:
                continue
        return False

    def try_root(root) -> bool:
        # Prefer links inside Contacts applet/grid.
        try:
            apps = root.locator(".siebui-applet").filter(has_text=re.compile(r"Contacts", re.I))
            n_apps = apps.count()
            for aidx in range(min(n_apps, 6)):
                app = apps.nth(aidx)
                if not (app.count() > 0 and app.is_visible(timeout=600)):
                    continue
                # 1) Exact first-name link in Contacts applet
                if fn_pat is not None:
                    try:
                        l = app.get_by_role("link", name=fn_pat).first
                        if l.count() > 0 and _clickish(l):
                            return True
                    except Exception:
                        pass
                    # 2) Any element (not only role=link) that renders first name in the row
                    for css in (
                        "table tbody tr td",
                        "table tr td",
                        '[role="gridcell"]',
                        "td",
                    ):
                        try:
                            cands = app.locator(css).filter(has_text=fn_pat)
                            n = cands.count()
                            for i in range(min(n, 20)):
                                c = cands.nth(i)
                                if _clickish(c):
                                    return True
                        except Exception:
                            continue
                    # 3) If exact fails due to hidden chars, try contains pattern
                    if fn_contains_pat is not None:
                        try:
                            cands = app.locator("table tbody tr td, table tr td, [role='gridcell']").filter(
                                has_text=fn_contains_pat
                            )
                            n = cands.count()
                            for i in range(min(n, 20)):
                                c = cands.nth(i)
                                if _clickish(c):
                                    return True
                        except Exception:
                            pass
                for css in (
                    "table tbody tr td a",
                    "table tr td a",
                    "a.siebui-ctrl-drilldown",
                    "a[href*='javascript']",
                ):
                    try:
                        l = app.locator(css).first
                        if l.count() > 0 and _clickish(l):
                            return True
                    except Exception:
                        continue
        except Exception:
            pass

        # Wider fallback: first-name link anywhere visible in the same root.
        if fn_pat is not None:
            try:
                l = root.get_by_role("link", name=fn_pat).first
                if l.count() > 0 and _clickish(l):
                    return True
            except Exception:
                pass
            # Final fallback: grid cell click anywhere in root
            try:
                cands = root.locator("table tbody tr td, table tr td, [role='gridcell']").filter(has_text=fn_pat)
                n = cands.count()
                for i in range(min(n, 24)):
                    if _clickish(cands.nth(i)):
                        return True
            except Exception:
                pass
        return False

    for root in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if try_root(root):
                return True
        except Exception:
            continue

    # Deterministic fallback: click row-1 cell under "First Name" column in Contacts grid.
    js_click_first_name_col = """(targetName) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 4 && r.height >= 4;
      };
      const norm = (s) => String(s || '').trim().toLowerCase();
      const hasName = (tx) => {
        if (!targetName) return true;
        return norm(tx).includes(norm(targetName));
      };
      const applets = Array.from(document.querySelectorAll('.siebui-applet'));
      for (const app of applets) {
        if (!vis(app)) continue;
        const txt = (app.innerText || '').toLowerCase();
        if (!txt.includes('contacts')) continue;
        const table = app.querySelector('table');
        if (!table) continue;
        const heads = Array.from(table.querySelectorAll('thead th, tr th'));
        let idx = -1;
        heads.forEach((h, i) => { if (idx < 0 && norm(h.innerText) === 'first name') idx = i; });
        if (idx < 0) {
          heads.forEach((h, i) => { if (idx < 0 && norm(h.innerText).includes('first name')) idx = i; });
        }
        if (idx < 0) continue;
        const rows = Array.from(table.querySelectorAll('tbody tr, tr')).filter(vis);
        for (const tr of rows) {
          const cells = tr.querySelectorAll('td');
          if (!cells || cells.length <= idx) continue;
          const td = cells[idx];
          if (!vis(td)) continue;
          if (!hasName(td.innerText || '')) continue;
          try {
            td.click();
            td.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
            return true;
          } catch (e) {}
        }
      }
      return false;
    }"""
    for frame in _ordered_frames(page):
        try:
            if bool(frame.evaluate(js_click_first_name_col, fn)):
                return True
        except Exception:
            continue

    # DOM fallback for div-based grids: find visible element with exact first-name text inside Contacts applet.
    js_click_first_name_div = """(targetName) => {
      const vis = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width >= 3 && r.height >= 3;
      };
      const norm = (s) => String(s || '').trim().toLowerCase().replace(/\\s+/g,' ');
      const tn = norm(targetName);
      const applets = Array.from(document.querySelectorAll('.siebui-applet'));
      for (const app of applets) {
        if (!vis(app)) continue;
        const txt = (app.innerText || '').toLowerCase();
        if (!txt.includes('contacts')) continue;
        const exact = Array.from(app.querySelectorAll('a,span,td,div,[role=\"gridcell\"],[role=\"link\"]'))
          .filter(vis)
          .filter(el => norm(el.innerText || el.textContent || '') === tn);
        for (const el of exact) {
          try { el.click(); return true; } catch(e) {}
        }
        const contain = Array.from(app.querySelectorAll('a,span,td,div,[role=\"gridcell\"],[role=\"link\"]'))
          .filter(vis)
          .filter(el => norm(el.innerText || el.textContent || '').includes(tn));
        for (const el of contain) {
          try { el.click(); return true; } catch(e) {}
        }
      }
      return false;
    }"""
    for frame in _ordered_frames(page):
        try:
            if bool(frame.evaluate(js_click_first_name_div, fn)):
                return True
        except Exception:
            continue
    return False
def _try_click_enquiry_top_tab(
    page: Page, *, action_timeout_ms: int, content_frame_selector: str | None
) -> bool:
    """
    Main module **Enquiry** tab (third-level / view bar). Hero Connect often marks the control with
    ``aria-label="Enquiry Selected"`` (e.g. from **Vehicles**); try that before generic **Enquiry**
    role/name matches.
    """

    def _click_first_visible(locator) -> bool:
        try:
            n = locator.count()
        except Exception:
            return False
        for i in range(min(n, 12)):
            el = locator.nth(i)
            try:
                if not el.is_visible(timeout=700):
                    continue
                try:
                    el.click(timeout=action_timeout_ms)
                except Exception:
                    el.click(timeout=action_timeout_ms, force=True)
                return True
            except Exception:
                continue
        return False

    enquiry_selected_css = (
        '[aria-label="Enquiry Selected"]',
        '[aria-label="Enquiry Selected" i]',
    )

    search_roots: list = [page]
    for r in _siebel_locator_search_roots(page, content_frame_selector):
        if r is not page:
            search_roots.append(r)

    for root in search_roots:
        for css in enquiry_selected_css:
            try:
                if _click_first_visible(root.locator(css)):
                    logger.info("siebel_dms: Enquiry tab via %s", css[:50])
                    return True
            except Exception:
                continue
        for role in ("tab", "link", "button"):
            try:
                loc = root.get_by_role(role, name=re.compile(r"^\s*Enquiry\s+Selected\s*$", re.I))
                if _click_first_visible(loc):
                    logger.info("siebel_dms: Enquiry tab via role=%s name=Enquiry Selected", role)
                    return True
            except Exception:
                continue

    name_res = (
        re.compile(r"^\s*Enquiry\s*$", re.I),
        re.compile(r"\bEnquiry\b", re.I),
    )
    for root in search_roots:
        for nr in name_res:
            for role in ("tab", "link", "button"):
                try:
                    loc = root.get_by_role(role, name=nr)
                    if _click_first_visible(loc):
                        return True
                except Exception:
                    continue
    return False


def _try_click_opportunities_list_new(
    page: Page, *, action_timeout_ms: int, content_frame_selector: str | None
) -> Frame | None:
    """
    Click new opportunity using only ``Opportunity Form:New`` and return the frame where it was clicked.
    After this point, callers should keep interaction scoped to this frame to avoid focus drift.
    """
    def _click_first_visible(locator) -> bool:
        try:
            n = locator.count()
        except Exception:
            return False
        for i in range(min(n, 10)):
            el = locator.nth(i)
            try:
                if not el.is_visible(timeout=800):
                    continue
                try:
                    el.click(timeout=action_timeout_ms)
                except Exception:
                    el.click(timeout=action_timeout_ms, force=True)
                return True
            except Exception:
                continue
        return False

    focused_selectors = (
        "a[aria-label='Opportunity Form:New']",
        "button[aria-label='Opportunity Form:New']",
        "a[title='Opportunity Form:New']",
        "button[title='Opportunity Form:New']",
        "a[aria-label='Opportunity Form: New']",
        "button[aria-label='Opportunity Form: New']",
        "a[title='Opportunity Form: New']",
        "button[title='Opportunity Form: New']",
        "[aria-label*='Opportunity Form' i][aria-label*='New' i]",
        "[title*='Opportunity Form' i][title*='New' i]",
    )

    def _try_activate_opportunity_form_scope(root) -> bool:
        """
        Some tenants keep Enquiry on an opportunity list pane first. Click any visible
        Opportunity Form tab/link/button to shift focus into the form pane before +New.
        """
        for role in ("tab", "link", "button"):
            try:
                loc = root.get_by_role(role, name=re.compile(r"^\s*Opportunity\s*Form\s*$", re.I))
                if _click_first_visible(loc):
                    return True
            except Exception:
                continue
        for css in (
            "[aria-label='Opportunity Form' i]",
            "[title='Opportunity Form' i]",
            "[aria-label*='Opportunity Form' i]:not([aria-label*='New' i])",
            "[title*='Opportunity Form' i]:not([title*='New' i])",
        ):
            try:
                if _click_first_visible(root.locator(css)):
                    return True
            except Exception:
                continue
        return False

    # Retry a few times: activate Opportunity Form scope, then click Opportunity Form:New in-frame.
    for attempt in range(3):
        for frame in _ordered_frames(page):
            try:
                _try_activate_opportunity_form_scope(frame)
            except Exception:
                pass

            try:
                for css in focused_selectors:
                    loc = frame.locator(css)
                    # Try scrolling into view before visibility check
                    try:
                        if loc.count() > 0:
                            loc.first.scroll_into_view_if_needed(timeout=1500)
                            _safe_page_wait(page, 300, log_label="scroll_opp_form_new")
                    except Exception:
                        pass
                    if _click_first_visible(loc):
                        logger.info(
                            "siebel_dms: clicked Opportunity Form:New in focused frame (attempt=%s)",
                            attempt + 1,
                        )
                        return frame
            except Exception:
                continue
        # Main page fallback (rare tenant rendering outside iframes)
        try:
            _try_activate_opportunity_form_scope(page)
            for css in focused_selectors:
                loc = page.locator(css)
                try:
                    if loc.count() > 0:
                        loc.first.scroll_into_view_if_needed(timeout=1500)
                        _safe_page_wait(page, 300, log_label="scroll_opp_form_new_page")
                except Exception:
                    pass
                if _click_first_visible(loc):
                    logger.info(
                        "siebel_dms: clicked Opportunity Form:New on page root (attempt=%s)",
                        attempt + 1,
                    )
                    return None
        except Exception:
            pass
        _safe_page_wait(page, 550, log_label=f"retry_opportunity_form_new_{attempt+1}")
    return None


def _frame_containing_enquiry_type(page: Page, preferred: Frame | None = None) -> Frame | None:
    if preferred is not None:
        try:
            if preferred.locator('[aria-label="Enquiry Type"]').count() > 0:
                return preferred
        except Exception:
            pass
    for frame in _ordered_frames(page):
        try:
            if frame.locator('[aria-label="Enquiry Type"]').count() > 0:
                return frame
        except Exception:
            continue
    return None


def _financier_mvg_wait_popup_indicator(
    page: Page, content_frame_selector: str | None, *, max_wait_ms: int
) -> bool:
    """True when the financier MVG applet exposes Account Name criteria or the Financial Consultant grid."""
    deadline = max(0, int(max_wait_ms))
    step = 220
    elapsed = 0
    while elapsed <= deadline:
        for root in _siebel_all_search_roots(page, content_frame_selector):
            try:
                hit = root.evaluate(
                    """() => {
                      const vis = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 2 && r.height > 2;
                      };
                      for (const sel of document.querySelectorAll('select')) {
                        if (!vis(sel)) continue;
                        const opts = Array.from(sel.options || []);
                        if (opts.some(o => /account\\s*name/i.test((o.textContent || '').trim()))) return true;
                      }
                      const pop = document.querySelector('.siebui-popup, .ui-dialog, [role="dialog"], [class*="siebui-applet" i]');
                      if (pop && vis(pop)) {
                        const tr0 = pop.querySelector('table tbody tr');
                        if (tr0) {
                          const tds = tr0.querySelectorAll('td');
                          if (tds.length >= 2) {
                            const c0 = tds[0];
                            const ctl = c0 && (c0.querySelector('select') || c0.querySelector('input:not([type="hidden"]):not([type="button"])'));
                            if (ctl && vis(ctl)) return true;
                          }
                        }
                      }
                      const tds = document.querySelectorAll(
                        'td[role="gridcell"][title="Financial Consultant"], td[id*="HHML_Type"], td[id$="_l_HHML_Type"]'
                      );
                      for (const td of tds) { if (vis(td)) return true; }
                      const dlg = Array.from(document.querySelectorAll(
                        '.siebui-popup, .ui-dialog, .ui-dialog-content, [role="dialog"]'
                      )).find(d => vis(d) && /pick\\s*financers/i.test(d.textContent || ''));
                      if (dlg) {
                        const s = dlg.querySelector('select');
                        if (s && vis(s)) return true;
                      }
                      return false;
                    }"""
                )
                if hit:
                    return True
            except Exception:
                continue
        if elapsed < deadline:
            _safe_page_wait(page, step, log_label="financier_mvg_popup_poll")
        elapsed += step
    return False


# Pick Financers MVG: Siebel field-type combobox (must never receive the financier search string).
_FINANCER_PICK_ACCOUNT_ID_COMBO_NAME = "s_5_1_312_0"


def _financier_mvg_find_pick_financers_search_value_input(
    page: Page, content_frame_selector: str | None,
) -> object | None:
    """
    The **search value** textbox to the right of the Account ID / Account Name criterion —
    **not** ``s_5_1_312_0`` (that is only for the criterion combobox).
    """
    _dlg_title = re.compile(r"Pick\s*Financer", re.I)
    _combo_css = f'input[type="text"][name="{_FINANCER_PICK_ACCOUNT_ID_COMBO_NAME}"]'
    _try_names = (
        "s_5_1_312_1",
        "s_5_1_313_0",
        "s_5_1_312_2",
    )
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            dlg = (
                root.locator(
                    ".siebui-popup, .ui-dialog, .ui-dialog-content, [role=\"dialog\"]"
                )
                .filter(has_text=_dlg_title)
                .first
            )
            if dlg.count() <= 0 or not dlg.is_visible(timeout=700):
                continue
            for nm in _try_names:
                loc = dlg.locator(f'input[type="text"][name="{nm}"]').first
                if loc.count() > 0 and loc.is_visible(timeout=600):
                    return loc
            try:
                xpv = (
                    f"xpath=.//input[@name='{_FINANCER_PICK_ACCOUNT_ID_COMBO_NAME}']"
                    "/ancestor::td[1]/following-sibling::td[1]//input"
                )
                loc = dlg.locator(xpv).first
                if loc.count() > 0 and loc.is_visible(timeout=600):
                    return loc
            except Exception:
                pass
            try:
                combo = dlg.locator(_combo_css).first
                if combo.count() > 0 and combo.is_visible(timeout=400):
                    loc = combo.locator(
                        "xpath=ancestor::td[1]/following-sibling::td[1]//input"
                    ).first
                    if loc.count() > 0 and loc.is_visible(timeout=600):
                        return loc
            except Exception:
                pass
            try:
                n = dlg.locator("input").count()
                for ii in range(min(n, 32)):
                    cand = dlg.locator("input").nth(ii)
                    if cand.count() <= 0 or not cand.is_visible(timeout=250):
                        continue
                    try:
                        in_grid = cand.evaluate(
                            """el => !!el.closest(
                              'table.ui-jqgrid-btable, table.ui-jqgrid-btable, .ui-jqgrid-btable, .ui-jqgrid'
                            )"""
                        )
                    except Exception:
                        in_grid = False
                    if in_grid:
                        continue
                    try:
                        nm = cand.evaluate("el => String(el.getAttribute('name')||'')").strip()
                    except Exception:
                        nm = ""
                    if nm == _FINANCER_PICK_ACCOUNT_ID_COMBO_NAME:
                        continue
                    typ = (
                        cand.evaluate("el => String(el.type || '').toLowerCase()") or ""
                    ).strip()
                    if typ in ("hidden", "button", "submit", "image", "checkbox", "radio"):
                        continue
                    if typ in ("text", "search", ""):
                        return cand
            except Exception:
                pass
        except Exception:
            continue
    return None


def _financier_mvg_find_pick_financers_dialog_toolbar(
    page: Page, content_frame_selector: str | None,
) -> tuple[object | None, object | None]:
    """
    **Pick Financers** MVG (Hero): toolbar row is **[search icon] | field-type ``<select>`` (Account ID) |
    value ``<input>`` | Go** — not always a 2-column criteria table. Return ``(select, value_input)``.
    """
    _dlg_title = re.compile(r"Pick\s*Financers", re.I)
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            dlg = (
                root.locator(
                    ".siebui-popup, .ui-dialog, .ui-dialog-content, [role=\"dialog\"]"
                )
                .filter(has_text=_dlg_title)
                .first
            )
            if dlg.count() <= 0 or not dlg.is_visible(timeout=800):
                continue
            dd = dlg.locator("select").first
            if dd.count() <= 0 or not dd.is_visible(timeout=700):
                continue
            val = None
            try:
                row = dlg.locator("tr:has(select)").first
                if row.count() > 0 and row.is_visible(timeout=400):
                    vt = row.locator('input[type="text"]').first
                    if vt.count() > 0 and vt.is_visible(timeout=500):
                        val = vt
            except Exception:
                pass
            if val is None:
                try:
                    n = dlg.locator("input").count()
                    for ii in range(min(n, 24)):
                        cand = dlg.locator("input").nth(ii)
                        if cand.count() <= 0 or not cand.is_visible(timeout=300):
                            continue
                        try:
                            in_grid = cand.evaluate(
                                """el => !!el.closest(
                                  'table.ui-jqgrid-btable, table.ui-jqgrid-btable, .ui-jqgrid-btable, .ui-jqgrid'
                                )"""
                            )
                        except Exception:
                            in_grid = False
                        if in_grid:
                            continue
                        typ = (
                            cand.evaluate("el => String(el.type || '').toLowerCase()") or ""
                        ).strip()
                        if typ in ("hidden", "button", "submit", "image", "checkbox", "radio"):
                            continue
                        if typ in ("text", "search", ""):
                            val = cand
                            break
                except Exception:
                    pass
            if val is None:
                try:
                    nx = dd.locator(
                        "xpath=ancestor::td[1]/following-sibling::td[1]//input"
                    ).first
                    if nx.count() > 0 and nx.is_visible(timeout=500):
                        val = nx
                except Exception:
                    pass
            try:
                if val is not None and val.count() > 0:
                    nm = (
                        val.evaluate("el => String(el.getAttribute('name') || '')").strip()
                    )
                    if nm == _FINANCER_PICK_ACCOUNT_ID_COMBO_NAME:
                        val = None
            except Exception:
                pass
            return dd, val
        except Exception:
            continue
    return None, None


def _financier_mvg_find_criteria_type_locator(page: Page, content_frame_selector: str | None):
    """
    MVG search applet: first column is often a **Siebel combo** (looks like a text field) for the
    criterion type (pick **Account Name**), second column is the value. Prefer popup-scoped first-row
    cells; fall back to any native ``select`` that lists Account Name.
    """
    c1, _c2 = _financier_mvg_find_search_row_pair(page, content_frame_selector)
    if c1 is not None:
        return c1
    acc_re = re.compile(r"account\s*name", re.I)
    _popup_first_cell = (
        ".siebui-popup table tbody tr:first-child td:nth-child(1) select",
        ".siebui-popup table tbody tr:first-child td:nth-child(1) input",
        ".ui-dialog table tbody tr:first-child td:nth-child(1) select",
        ".ui-dialog table tbody tr:first-child td:nth-child(1) input",
        ".ui-dialog-content table tbody tr:first-child td:nth-child(1) select",
        ".ui-dialog-content table tbody tr:first-child td:nth-child(1) input",
        "[role=\"dialog\"] table tbody tr:first-child td:nth-child(1) select",
        "[role=\"dialog\"] table tbody tr:first-child td:nth-child(1) input",
        ".siebui-applet table tbody tr:first-child td:nth-child(1) select",
        ".siebui-applet table tbody tr:first-child td:nth-child(1) input",
    )
    for root in _siebel_all_search_roots(page, content_frame_selector):
        for css in _popup_first_cell:
            try:
                loc = root.locator(css).first
                if loc.count() <= 0 or not loc.is_visible(timeout=500):
                    continue
                try:
                    row2 = root.locator(
                        ".siebui-popup table tbody tr:first-child td:nth-child(2) input, "
                        ".ui-dialog table tbody tr:first-child td:nth-child(2) input, "
                        "[role=\"dialog\"] table tbody tr:first-child td:nth-child(2) input, "
                        ".siebui-applet table tbody tr:first-child td:nth-child(2) input"
                    ).first
                    if row2.count() > 0 and row2.is_visible(timeout=350):
                        return loc
                except Exception:
                    pass
                return loc
            except Exception:
                continue
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            n = root.locator("select").count()
            for i in range(min(n, 48)):
                sel = root.locator("select").nth(i)
                try:
                    if sel.count() <= 0 or not sel.is_visible(timeout=450):
                        continue
                except Exception:
                    continue
                try:
                    oc = sel.locator("option").count()
                    for j in range(min(oc, 96)):
                        txt = (sel.locator("option").nth(j).inner_text(timeout=250) or "").strip()
                        if acc_re.search(txt):
                            return sel
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _financier_mvg_find_search_row_pair(
    page: Page, content_frame_selector: str | None,
) -> tuple[object | None, object | None]:
    """
    First MVG search row: **criterion type** control (col 1) and **value** field (col 2).
    Locators are from the same ``tr`` so Tab order is not relied on for typing the financier name.
    """
    scopes = (
        ".siebui-popup",
        ".ui-dialog",
        ".ui-dialog-content",
        "[role=\"dialog\"]",
        ".siebui-applet",
    )
    cell_sel = "input:not([type='hidden']):not([type='button']):not([type='submit']), select, textarea"
    for root in _siebel_all_search_roots(page, content_frame_selector):
        for sc in scopes:
            for ri in (0, 1):
                try:
                    row = root.locator(f"{sc} table tbody tr").nth(ri)
                    if row.count() <= 0 or not row.is_visible(timeout=450):
                        continue
                    ntd = row.locator("td").count()
                    if ntd < 2:
                        continue
                    c1 = row.locator("td").nth(0).locator(cell_sel).first
                    c2 = row.locator("td").nth(1).locator(cell_sel).first
                    if c1.count() <= 0 or not c1.is_visible(timeout=550):
                        continue
                    if c2.count() <= 0 or not c2.is_visible(timeout=550):
                        continue
                    return c1, c2
                except Exception:
                    continue
    return None, None


def _financier_mvg_pick_account_name_on_criteria_control(
    _loc,
    page: Page,
    content_frame_selector: str | None,
    *,
    action_timeout_ms: int,
) -> bool:
    """
    **Pick Financers** applet (Hero): after the applet opens, drive the Account ID combobox
    (Siebel Open UI: ``input`` ``name="s_5_1_312_0"``, ``role="combobox"``, …): **click**, then
    **ArrowDown** twice. Returns ``True`` when the combo was found and keys were sent; ``False`` if
    the control was not found.
    """
    _tmo = min(int(action_timeout_ms), 8000)
    _acct_id_combo = f'input[type="text"][name="{_FINANCER_PICK_ACCOUNT_ID_COMBO_NAME}"]'
    acct: object | None = None
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            cand = root.locator(_acct_id_combo).first
            if cand.count() > 0 and cand.is_visible(timeout=900):
                acct = cand
                break
        except Exception:
            continue
    if acct is None:
        try:
            cand = page.locator(_acct_id_combo).first
            if cand.count() > 0 and cand.is_visible(timeout=700):
                acct = cand
        except Exception:
            pass
    if acct is None:
        return False
    try:
        acct.scroll_into_view_if_needed(timeout=_tmo)
    except Exception:
        pass
    try:
        acct.click(timeout=_tmo)
    except Exception:
        try:
            acct.click(timeout=_tmo, force=True)
        except Exception:
            return False
    _safe_page_wait(page, 200, log_label="financier_mvg_account_id_s_5_1_312_0_click")
    for i in range(2):
        try:
            acct.press("ArrowDown", timeout=900)
        except Exception:
            try:
                page.keyboard.press("ArrowDown")
            except Exception:
                pass
        _safe_page_wait(
            page,
            280 if i == 0 else 300,
            log_label=f"financier_mvg_account_id_arrow_down_{i + 1}",
        )
    return True


def _financier_mvg_financial_consultant_data_row_count(
    page: Page, content_frame_selector: str | None,
) -> int:
    """
    Count jqGrid **data** rows under the Financial Consultant / ``HHML_Type`` column.
    Returns **-1** if that grid is not present in any root.
    """
    for root in _siebel_all_search_roots(page, content_frame_selector):
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
                  const cells = Array.from(document.querySelectorAll(
                    'td[role="gridcell"][title="Financial Consultant"], td[id*="HHML_Type"], td[id$="_l_HHML_Type"]'
                  ));
                  const marker = cells.find(vis);
                  if (!marker) return -1;
                  const tbl = marker.closest('table');
                  if (!tbl) return -1;
                  const rows = Array.from(tbl.querySelectorAll('tbody tr.jqgrow')).filter(vis);
                  let n = 0;
                  for (const tr of rows) {
                    const t = (tr.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (t.length < 2) continue;
                    if (/^(no records|no rows|no data|0\\s*-?\\s*0)/i.test(t)) continue;
                    n++;
                  }
                  return n;
                }"""
            )
            if isinstance(v, int) and v >= 0:
                return v
        except Exception:
            continue
    return -1


def _financier_mvg_click_first_result_row(page: Page, content_frame_selector: str | None) -> bool:
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            clicked = root.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 2 && r.height > 2;
                  };
                  const cells = Array.from(document.querySelectorAll(
                    'td[role="gridcell"][title="Financial Consultant"], td[id*="HHML_Type"], td[id$="_l_HHML_Type"]'
                  ));
                  const marker = cells.find(vis);
                  if (!marker) return false;
                  const tbl = marker.closest('table');
                  if (!tbl) return false;
                  const rows = Array.from(tbl.querySelectorAll('tbody tr.jqgrow')).filter(vis);
                  for (const tr of rows) {
                    const t = (tr.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (t.length < 2) continue;
                    if (/^(no records|no rows|no data|0\\s*-?\\s*0)/i.test(t)) continue;
                    const td = tr.querySelector('td[role="gridcell"]') || tr.querySelector('a') || tr;
                    try { td.click(); return true; } catch (e) {}
                    try { tr.click(); return true; } catch (e) {}
                  }
                  return false;
                }"""
            )
            if clicked:
                return True
        except Exception:
            continue
    return False


def _financier_mvg_account_name_search_and_pick(
    page: Page,
    content_frame_selector: str | None,
    caps: str,
    *,
    action_timeout_ms: int,
) -> str | None:
    """
    In the open financier MVG applet: **Account Name** → Tab → ALL CAPS name → Tab → Enter;
    require at least one Financial Consultant grid row, then first row + Enter.

    Returns ``None`` on success, or a short error token / user-facing message.
    """
    _tmo = min(int(action_timeout_ms), 8000)
    criteria_loc, value_loc = None, None
    _pf_dd, _pf_val = _financier_mvg_find_pick_financers_dialog_toolbar(page, content_frame_selector)
    if _pf_dd is not None:
        criteria_loc, value_loc = _pf_dd, _pf_val
    if criteria_loc is None:
        criteria_loc, value_loc = _financier_mvg_find_search_row_pair(page, content_frame_selector)
    if criteria_loc is None:
        criteria_loc = _financier_mvg_find_criteria_type_locator(page, content_frame_selector)
    if criteria_loc is None:
        return "Financer MVG: Account Name criteria not found in popup."
    if not _financier_mvg_pick_account_name_on_criteria_control(
        criteria_loc,
        page,
        content_frame_selector,
        action_timeout_ms=action_timeout_ms,
    ):
        return "Financer MVG: could not focus Account ID criterion field (Pick Financers)."
    _safe_page_wait(page, 220, log_label="financier_mvg_after_account_name")
    _val_resolved = _financier_mvg_find_pick_financers_search_value_input(
        page, content_frame_selector
    )
    if _val_resolved is not None:
        value_loc = _val_resolved
    if value_loc is not None:
        try:
            _vn = value_loc.evaluate("el => String(el.getAttribute('name')||'')").strip()
            if _vn == _FINANCER_PICK_ACCOUNT_ID_COMBO_NAME:
                value_loc = _financier_mvg_find_pick_financers_search_value_input(
                    page, content_frame_selector
                )
        except Exception:
            pass
    if value_loc is None:
        return "Financer MVG: could not locate financier search value field (not the Account ID combo)."
    try:
        value_loc.scroll_into_view_if_needed(timeout=_tmo)
    except Exception:
        pass
    try:
        value_loc.click(timeout=_tmo)
    except Exception:
        try:
            value_loc.click(timeout=_tmo, force=True)
        except Exception:
            return "Financer MVG: could not focus financier value field."
    _safe_page_wait(page, 180, log_label="financier_mvg_value_field_focus")
    try:
        value_loc.fill("", timeout=_tmo)
    except Exception:
        pass
    try:
        value_loc.press("Control+a", timeout=700)
    except Exception:
        pass
    try:
        value_loc.fill(caps, timeout=_tmo)
    except Exception:
        try:
            value_loc.type(caps, delay=22, timeout=_tmo)
        except Exception:
            return "Financer MVG: could not type financier name."
    _safe_page_wait(page, 200, log_label="financier_mvg_after_caps_in_value_cell")
    try:
        value_loc.press("Tab", timeout=1200)
    except Exception:
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass
    _safe_page_wait(page, 120, log_label="financier_mvg_before_search_enter")
    try:
        page.keyboard.press("Enter")
    except Exception:
        return "Financer MVG: Enter after search fields failed."
    _safe_page_wait(page, 700, log_label="financier_mvg_after_search_enter")
    n = _financier_mvg_financial_consultant_data_row_count(page, content_frame_selector)
    if n < 0:
        _safe_page_wait(page, 1000, log_label="financier_mvg_grid_retry_wait")
        n = _financier_mvg_financial_consultant_data_row_count(page, content_frame_selector)
    if n < 0:
        return "Financer MVG: Financial Consultant result grid did not appear."
    if n == 0:
        return "Financer name not matched"
    if not _financier_mvg_click_first_result_row(page, content_frame_selector):
        return "Financer MVG: could not select first search result row."
    _safe_page_wait(page, 350, log_label="financier_mvg_after_first_row_click")
    try:
        page.keyboard.press("Enter")
    except Exception:
        pass
    _safe_page_wait(page, 400, log_label="financier_mvg_after_result_enter")
    return None


def _fill_create_order_financier_field_on_frame(
    page: Page,
    frame,
    financier_display: str,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note=None,
) -> tuple[bool, str | None]:
    """
    **Vehicle Sales — Financer** on create order (main form only):

    Click the **Financer text input** (not the pick/magnifier control), type the name in **ALL CAPS**,
    **Tab** out. That **Tab** opens a small tablet/dialog with focus in the first field.

    Then: **Tab** to the second field, clear it, type the same **ALL CAPS** financier name, **Tab**, **Enter**
    (not Enter immediately after typing). Siebel may resolve the account and replace the main Financer text
    (e.g. canonical company name) without a detectable **Financial Consultant** jqGrid row count — so this
    path does **not** validate or select rows in that grid; it waits for the tablet to settle / close.

    Does **not** use the MVG pick-icon / Pick Financers popup flow.

    Returns ``(True, None)`` on success; ``(False, None)`` if the main field could not be resolved or filled.
    """
    _caps = (financier_display or "").strip().upper()
    if not _caps:
        return False, None
    _tmo = min(int(action_timeout_ms), 8000)

    def _input_ok(loc) -> bool:
        try:
            if loc.count() <= 0 or not loc.is_visible(timeout=650):
                return False
            tag = loc.evaluate("el => el.tagName.toLowerCase()")
            if tag != "input":
                return False
            typ = (
                loc.evaluate("el => (el.getAttribute('type')||'text').toLowerCase()") or "text"
            )
            if typ in ("hidden", "button", "submit", "checkbox", "radio"):
                return False
            try:
                ro = loc.evaluate("el => el.readOnly === true || el.disabled === true")
            except Exception:
                ro = False
            if ro:
                return False
            try:
                iid = str(loc.evaluate("el => el.id || ''") or "")
                if re.search(r"_icon$|pick|mvg|lookup", iid, re.I):
                    return False
            except Exception:
                pass
            try:
                inside_pick = loc.evaluate(
                    """el => {
                      const a = el.closest('a');
                      if (!a) return false;
                      const t = (a.getAttribute('title')||'') + (a.getAttribute('class')||'');
                      return /pick|mvg|lookup|search/i.test(t);
                    }"""
                )
            except Exception:
                inside_pick = False
            if inside_pick:
                return False
            return True
        except Exception:
            return False

    def _find_fin_text_input():
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
                    if _input_ok(loc):
                        return loc
                except Exception:
                    continue
            pats = (
                re.compile(rf"^\s*{re.escape(_lbl)}\s*$", re.I),
                re.compile(re.escape(_lbl), re.I),
            )
            for pat in pats:
                try:
                    loc = frame.get_by_label(pat).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=650):
                        continue
                    tag = loc.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "input" and _input_ok(loc):
                        return loc
                    inner = loc.locator(
                        "input.siebui-ctrl-input, input[type='text'], "
                        "input[role='combobox'], input:not([type='hidden'])"
                    ).first
                    if _input_ok(inner):
                        return inner
                except Exception:
                    continue
        return None

    inp = _find_fin_text_input()
    if inp is None:
        return False, None

    try:
        inp.scroll_into_view_if_needed(timeout=_tmo)
    except Exception:
        pass
    try:
        inp.click(timeout=_tmo, position={"x": 12, "y": 10})
    except Exception:
        try:
            inp.focus(timeout=_tmo)
        except Exception:
            try:
                inp.click(timeout=_tmo, force=True, position={"x": 12, "y": 10})
            except Exception:
                return False, None
    _safe_page_wait(page, 200, log_label="financier_main_text_click")
    try:
        inp.fill("", timeout=_tmo)
    except Exception:
        pass
    try:
        inp.press("Control+a", timeout=800)
    except Exception:
        pass
    try:
        inp.fill(_caps, timeout=_tmo)
    except Exception:
        try:
            inp.type(_caps, delay=22, timeout=_tmo)
        except Exception:
            return False, None
    _safe_page_wait(page, 220, log_label="financier_main_after_type")
    try:
        inp.press("Tab", timeout=1200)
    except Exception:
        try:
            page.keyboard.press("Tab")
        except Exception:
            pass
    _safe_page_wait(page, 600, log_label="financier_main_after_tab_tablet_open")
    # Tablet opens with focus in field 1 — Tab to field 2, clear, fill financier ALL CAPS, Enter.
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _safe_page_wait(page, 280, log_label="financier_tablet_tab_to_field2")

    def _frames_fin_tablet_scan_order():
        seen: set[int] = set()
        out = []
        for f in (frame, page.main_frame, *_ordered_frames(page)):
            try:
                fid = id(f)
                if fid in seen:
                    continue
                seen.add(fid)
                out.append(f)
            except Exception:
                continue
        return out

    def _focused_fillable_input_element():
        for fr in _frames_fin_tablet_scan_order():
            try:
                h = fr.evaluate_handle(
                    """() => {
                        const e = document.activeElement;
                        if (!e) return null;
                        const t = e.tagName.toLowerCase();
                        if (t !== 'input' && t !== 'textarea') return null;
                        if (e.disabled || e.readOnly) return null;
                        const ty = (e.getAttribute('type') || 'text').toLowerCase();
                        if (ty === 'hidden' || ty === 'button' || ty === 'submit') return null;
                        return e;
                    }"""
                )
                el = h.as_element()
                if el is not None:
                    return el
            except Exception:
                continue
        return None

    _f2 = _focused_fillable_input_element()
    _typed = False
    if _f2 is not None:
        try:
            try:
                _f2.fill("", timeout=_tmo)
            except Exception:
                pass
            try:
                _f2.press("Control+a", timeout=800)
            except Exception:
                pass
            try:
                _f2.fill(_caps, timeout=_tmo)
                _typed = True
            except Exception:
                try:
                    _f2.type(_caps, delay=20, timeout=_tmo)
                    _typed = True
                except Exception:
                    _typed = False
        except Exception:
            _typed = False
    if not _typed:
        try:
            page.keyboard.press("Control+a")
        except Exception:
            pass
        try:
            page.keyboard.type(_caps, delay=20)
            _typed = True
        except Exception:
            return False, None
    # After value in field 2: Tab (not Enter), then Enter — runs search and loads the grid below.
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    _safe_page_wait(page, 220, log_label="financier_tablet_field2_after_tab")
    try:
        page.keyboard.press("Enter")
    except Exception:
        pass
    _safe_page_wait(page, 700, log_label="financier_tablet_after_tab_enter_search")
    _safe_page_wait(page, 600, log_label="financier_tablet_settle_no_grid_check")
    if callable(note):
        try:
            note(
                "Create Order: Financer tablet field2 ALL CAPS + Tab + Enter; "
                "no Financial Consultant grid row check (Siebel may resolve canonical name on main field). "
                f"source={financier_display!r} typed={_caps!r}."
            )
        except Exception:
            pass
    return True, None


def _select_dropdown_by_label_on_frame(
    frame: Frame,
    *,
    label: str,
    value: str,
    action_timeout_ms: int,
) -> bool:
    """
    Strict frame-scoped dropdown selection for Opportunity Form.
    Avoids page/global roots that can pull focus to a different applet/frame.
    """
    sv = (value or "").strip()
    if not sv:
        return False
    pats = (
        re.compile(rf"^\s*{re.escape(label)}\s*$", re.I),
        re.compile(re.escape(label), re.I),
    )
    val_pat = re.compile(rf"^\s*{re.escape(sv)}\s*$", re.I)

    for pat in pats:
        try:
            fld = frame.get_by_label(pat).first
            if fld.count() <= 0 or not fld.is_visible(timeout=700):
                continue
            try:
                fld.click(timeout=action_timeout_ms)
            except Exception:
                fld.click(timeout=action_timeout_ms, force=True)
            try:
                fld.select_option(label=sv, timeout=action_timeout_ms)
                return True
            except Exception:
                pass
            for role in ("option", "menuitem", "link"):
                try:
                    opt = frame.get_by_role(role, name=val_pat).first
                    if opt.count() > 0 and opt.is_visible(timeout=650):
                        try:
                            opt.click(timeout=action_timeout_ms)
                        except Exception:
                            opt.click(timeout=action_timeout_ms, force=True)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _add_enquiry_opportunity(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace,
    vehicle_merge: dict | None = None,
) -> tuple[bool, str | None, str]:
    """
    **Find→Vehicles** + VIN drill is **commented out** in this branch: assume the UI is already on a view
    where **Enquiry → Opportunity Form:New** applies, and take model / YYYY ``year_of_mfg`` / color from
    ``vehicle_merge`` (typically ``prepare_vehicle``).

    Then **Opportunity Form:New**, fill opportunity fields from DB + vehicle model/color (**Financier**
    fields are skipped), then **Ctrl+S**.

    **Contact First Name** comes from ``dms_values["first_name"]`` (caller passes base or dotted name).

    After **Ctrl+S**, Enquiry# must **differ** from the pre-save scrape at **0.5s, 2.5s, and 3.5s**
    post-save; otherwise **hard fail**.

    Returns ``(success, error_detail, enquiry_number)`` — ``error_detail`` is a short operator-facing
    reason when ``success`` is False; ``enquiry_number`` is the scraped Enquiry# on success (empty on
    failure).
    """
    fr_db = (dms_values.get("finance_required") or "").strip().upper()
    if fr_db in ("Y", "N"):
        finance_required = fr_db
    else:
        finance_required = "Y" if (dms_values.get("financier_name") or "").strip() else "N"
    aadhar = (dms_values.get("aadhar_id") or "").strip()
    frame_p = (dms_values.get("frame_partial") or "").strip()
    engine_p = (dms_values.get("engine_partial") or "").strip()

    if not aadhar:
        note("Add Enquiry: aadhar_id from DB is empty — cannot fill UIN No.")
        return False, "Missing customer Aadhaar last 4 for UIN No.", ""

    if callable(form_trace):
        form_trace(
            "add_enquiry_branch",
            "No contact table match → Enquiry + Opportunities (Find→Vehicles commented out)",
            "chassis_engine_Enter_then_Enquiry_tab_Opportunities_New_fields_Ctrl_S",
            frame_partial=frame_p,
            engine_partial=engine_p,
            finance_required=finance_required,
            financier_name=(dms_values.get("financier_name") or "(empty)")[:120],
            aadhar_id=aadhar,
        )

    # --- Find→Vehicles + VIN drill (commented out: proceed on current Siebel view to Enquiry tab) ---
    # _reuse_vm = vehicle_merge if _add_enquiry_reuse_vehicle_dict_ready(vehicle_merge) else None
    # _vq_ok, scraped_v = _siebel_vehicle_find_chassis_engine_enter(
    #     page,
    #     (urls.vehicle or "").strip(),
    #     frame_p,
    #     engine_p,
    #     nav_timeout_ms=nav_timeout_ms,
    #     action_timeout_ms=action_timeout_ms,
    #     content_frame_selector=content_frame_selector,
    #     note=note,
    #     reuse_vehicle_dict=_reuse_vm,
    # )
    # if not _vq_ok:
    #     return False, "Vehicle find failed (chassis/engine query or VIN fly-in).", ""
    # _apply_year_of_mfg_yyyy(scraped_v)
    # if not _add_enquiry_vehicle_scrape_has_model_year_color(scraped_v):
    #     note(
    #         "Add Enquiry: vehicle data missing model, year of manufacture, and color — "
    #         "not opening Enquiry / new opportunity."
    #     )
    #     return (
    #         False,
    #         "Add Enquiry: vehicle data did not yield model, YYYY year of manufacture, and color.",
    #         "",
    #     )
    # if _reuse_vm is None:
    #     note(
    #         "Add Enquiry: scraped from vehicle list — "
    #         f"model={scraped_v.get('model')!r}, year_of_mfg={scraped_v.get('year_of_mfg')!r}, "
    #         f"color={scraped_v.get('color')!r}."
    #     )
    # if vehicle_merge is not None:
    #     _merge_add_enquiry_vehicle_scrape(vehicle_merge, scraped_v)

    scraped_v = dict(vehicle_merge or {})
    _apply_year_of_mfg_yyyy(scraped_v)
    if not _add_enquiry_vehicle_scrape_has_model_year_color(scraped_v):
        note(
            "Add Enquiry: vehicle data missing model, year of manufacture, and color — "
            "with Find→Vehicles disabled, ensure prepare_vehicle populated vehicle_merge."
        )
        return (
            False,
            "Add Enquiry: vehicle_merge did not yield model, YYYY year of manufacture, and color.",
            "",
        )
    note(
        "Add Enquiry: using vehicle_merge for model/year/color (Find→Vehicles disabled) — "
        f"model={scraped_v.get('model')!r}, year_of_mfg={scraped_v.get('year_of_mfg')!r}, "
        f"color={scraped_v.get('color')!r}."
    )
    if vehicle_merge is not None:
        _merge_add_enquiry_vehicle_scrape(vehicle_merge, scraped_v)

    if callable(form_trace):
        form_trace(
            "add_enquiry_vehicle_scrape",
            "prepare_vehicle / vehicle_merge (Find→Vehicles disabled)",
            "read_model_year_color_before_Enquiry_Opportunity",
            model=str(scraped_v.get("model") or ""),
            year_of_mfg=str(scraped_v.get("year_of_mfg") or ""),
            color=str(scraped_v.get("color") or ""),
            full_chassis=str(scraped_v.get("full_chassis") or ""),
            full_engine=str(scraped_v.get("full_engine") or ""),
        )

    # #region agent log
    try:
        import json as _j_ae

        with open(Path(__file__).resolve().parents[3] / "debug-0875fe.log", "a", encoding="utf-8") as _lf:
            _lf.write(
                _j_ae.dumps(
                    {
                        "sessionId": "0875fe",
                        "hypothesisId": "KB-B",
                        "location": "hero_dms_playwright_customer.py:_add_enquiry_opportunity",
                        "message": "add_enquiry_before_key_battery_fill",
                        "data": {"after_vehicle_grid_scrape": True},
                        "timestamp": _ts_ist_iso(),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion

    _siebel_fill_key_battery_from_dms_values(
        page,
        dms_values,
        action_timeout_ms=action_timeout_ms,
        note=note,
        log_prefix="Add Enquiry",
    )

    if not _try_click_enquiry_top_tab(
        page, action_timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("Add Enquiry: Enquiry tab not found (tried aria-label Enquiry Selected and Enquiry).")
        return False, "Enquiry main tab not found.", ""
    note("Add Enquiry: Enquiry tab clicked.")
    _safe_page_wait(page, 1800, log_label="after_enquiry_tab")

    opp_frame = _try_click_opportunities_list_new(
        page, action_timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    )
    if opp_frame is None:
        note('Add Enquiry: Opportunity Form:New not found in current Enquiry pane/frame.')
        return False, 'Opportunity Form:New not found on Enquiry view.'
    note("Add Enquiry: clicked Opportunity Form:New.")
    _safe_page_wait(page, 1200, log_label="after_opportunity_new")

    enq_frame = _frame_containing_enquiry_type(page, preferred=opp_frame)
    if enq_frame is None:
        note('Add Enquiry: no frame contains aria-label="Enquiry Type".')
        return False, "New opportunity form not found (no Enquiry Type field).", ""

    def _scrape_enquiry_number_from_frame(frame: Frame) -> str:
        """
        Best-effort read of saved Enquiry# from Opportunity form after Ctrl+S.
        """
        # 1) Label-based input extraction
        for pat in (
            re.compile(r"^\s*Enquiry\s*#\s*$", re.I),
            re.compile(r"^\s*Enquiry\s*No\.?\s*$", re.I),
            re.compile(r"\bEnquiry\s*#\b", re.I),
            re.compile(r"\bEnquiry\s*No\b", re.I),
        ):
            try:
                loc = frame.get_by_label(pat).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    try:
                        v = (loc.input_value(timeout=1200) or "").strip()
                    except Exception:
                        v = (loc.get_attribute("value") or "").strip()
                    if v:
                        return v
            except Exception:
                continue
        # 2) Attribute-based controls
        for css in (
            'input[aria-label*="Enquiry#" i]',
            'input[aria-label*="Enquiry No" i]',
            'input[title*="Enquiry#" i]',
            'input[title*="Enquiry No" i]',
            'textarea[aria-label*="Enquiry#" i]',
            'textarea[title*="Enquiry#" i]',
        ):
            try:
                loc = frame.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    try:
                        v = (loc.input_value(timeout=1200) or "").strip()
                    except Exception:
                        v = (loc.get_attribute("value") or "").strip()
                    if v:
                        return v
            except Exception:
                continue
        # 3) Read-only drilldown/link text on saved row
        for role in ("link", "gridcell", "cell"):
            try:
                loc = frame.get_by_role(role, name=re.compile(r"\bENQ[-/\s]?\d+\b", re.I)).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    t = (loc.inner_text(timeout=1200) or "").strip()
                    if t:
                        return t
            except Exception:
                continue
        # 4) DOM fallback
        try:
            js_val = frame.evaluate(
                """() => {
                  const norm = (s) => String(s || '').trim();
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 2 && r.height >= 2;
                  };
                  const controls = Array.from(document.querySelectorAll('input,textarea,a,span,div')).filter(vis);
                  for (const el of controls) {
                    const al = norm(el.getAttribute('aria-label') || '');
                    const tt = norm(el.getAttribute('title') || '');
                    const tx = norm(el.value || el.textContent || '');
                    const k = (al + ' ' + tt).toLowerCase();
                    if (!(k.includes('enquiry') && (k.includes('#') || k.includes('no')))) continue;
                    if (!tx) continue;
                    return tx;
                  }
                  return '';
                }"""
            )
            s = str(js_val or "").strip()
            if s:
                return s
        except Exception:
            pass
        return ""

    def _derive_age_from_dob_text(dob_raw: str) -> str:
        s = (dob_raw or "").strip()
        if not s:
            return ""
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
            try:
                dob_dt = datetime.strptime(s, fmt).date()
                t = _siebel_ist_today()
                return str(max(0, t.year - dob_dt.year - ((t.month, t.day) < (dob_dt.month, dob_dt.day))))
            except Exception:
                continue
        m = re.search(r"\b(19\d{2}|20\d{2})\b", s)
        if m:
            try:
                return str(max(0, _siebel_ist_today().year - int(m.group(1))))
            except Exception:
                return ""
        return ""

    def _normalize_gender_for_form(raw: str) -> str:
        s = (raw or "").strip().lower()
        if s in ("m", "male"):
            return "M"
        if s in ("f", "female"):
            return "F"
        if s in ("o", "other"):
            return "O"
        return (raw or "").strip()

    def _address_line1_between_first_second_comma(raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            return ""
        parts = [p.strip() for p in s.split(",")]
        if len(parts) >= 3:
            return parts[1]
        if len(parts) == 2:
            return parts[1]
        return s

    def _city_pick_any_then_ok(frame: Frame) -> bool:
        search_btn = None
        for css in (
            '[aria-label*="City/Town/Village" i][aria-label*="Search" i]',
            '[title*="City/Town/Village" i][title*="Search" i]',
            '[aria-label*="City/Town/Village" i][aria-label*="Pick" i]',
            '[title*="City/Town/Village" i][title*="Pick" i]',
            'button[aria-label*="City" i][aria-label*="Search" i]',
            'a[aria-label*="City" i][aria-label*="Search" i]',
            'button[title*="City" i][title*="Search" i]',
            'a[title*="City" i][title*="Search" i]',
        ):
            try:
                loc = frame.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    search_btn = loc
                    break
            except Exception:
                continue
        if search_btn is None:
            return True
        try:
            search_btn.click(timeout=action_timeout_ms)
        except Exception:
            try:
                search_btn.click(timeout=action_timeout_ms, force=True)
            except Exception:
                return False
        _safe_page_wait(page, 700, log_label="city_pick_open")
        picked = False
        for root in (frame, page):
            for css in ("table tbody tr td a", "table tbody tr td", '[role="option"]', '[role="row"] [role="gridcell"]'):
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=650):
                        try:
                            loc.click(timeout=action_timeout_ms)
                        except Exception:
                            loc.click(timeout=action_timeout_ms, force=True)
                        picked = True
                        break
                except Exception:
                    continue
            if picked:
                break
        if not picked:
            return False
        for root in (frame, page):
            for css in (
                'button[aria-label="OK" i]',
                'a[aria-label="OK" i]',
                'button[title="OK" i]',
                'a[title="OK" i]',
                'input[type="button"][value="OK" i]',
                'input[type="submit"][value="OK" i]',
            ):
                try:
                    ok = root.locator(css).first
                    if ok.count() > 0 and ok.is_visible(timeout=700):
                        try:
                            ok.click(timeout=action_timeout_ms)
                        except Exception:
                            ok.click(timeout=action_timeout_ms, force=True)
                        return True
                except Exception:
                    continue
        return True

    def _select_variant_first_value(frame: Frame, variant_hint: str = "") -> bool:
        vh = (variant_hint or "").strip()
        for pat in (re.compile(r"^\s*Variant\s*$", re.I), re.compile(r"\bVariant\b", re.I)):
            try:
                fld = frame.get_by_label(pat).first
                if fld.count() <= 0 or not fld.is_visible(timeout=700):
                    continue
                try:
                    fld.click(timeout=action_timeout_ms)
                except Exception:
                    fld.click(timeout=action_timeout_ms, force=True)
                if vh:
                    try:
                        fld.fill("", timeout=action_timeout_ms)
                        fld.fill(vh, timeout=action_timeout_ms)
                        fld.press("Tab", timeout=1200)
                        _safe_page_wait(page, 300, log_label="variant_hint_tab")
                        readback = ""
                        try:
                            readback = (fld.input_value(timeout=800) or "").strip()
                        except Exception:
                            pass
                        if readback:
                            return True
                    except Exception:
                        pass
                # Siebel bounded text input: click pick icon to open pick applet
                for pick_css in (
                    '[aria-label*="Variant" i][aria-label*="Pick" i]',
                    '[title*="Variant" i][title*="Pick" i]',
                    '[aria-label*="Variant" i][aria-label*="Search" i]',
                ):
                    for root in (frame, page):
                        try:
                            pick = root.locator(pick_css).first
                            if pick.count() > 0 and pick.is_visible(timeout=500):
                                pick.click(timeout=action_timeout_ms)
                                _safe_page_wait(page, 800, log_label="variant_pick_open")
                                for row_root in (frame, page):
                                    for row_css in ("table tbody tr td a", "table tbody tr td", '[role="option"]', '[role="row"] [role="gridcell"]'):
                                        try:
                                            row = row_root.locator(row_css).first
                                            if row.count() > 0 and row.is_visible(timeout=500):
                                                row.click(timeout=action_timeout_ms)
                                                _safe_page_wait(page, 300, log_label="variant_pick_row")
                                                for ok_css in ('button[aria-label="OK" i]', 'a[aria-label="OK" i]', 'button[title="OK" i]', 'input[type="button"][value="OK" i]'):
                                                    try:
                                                        ok = row_root.locator(ok_css).first
                                                        if ok.count() > 0 and ok.is_visible(timeout=500):
                                                            ok.click(timeout=action_timeout_ms)
                                                            break
                                                    except Exception:
                                                        continue
                                                return True
                                        except Exception:
                                            continue
                        except Exception:
                            continue
                # Fallback: ArrowDown + Enter for simple dropdown behavior
                try:
                    fld.press("ArrowDown", timeout=1200)
                    fld.press("Enter", timeout=1200)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        # CSS fallback for aria-label mismatch
        for css in ('input[aria-label*="Variant" i]',):
            try:
                fld = frame.locator(css).first
                if fld.count() <= 0 or not fld.is_visible(timeout=500):
                    continue
                fld.click(timeout=action_timeout_ms)
                if vh:
                    fld.fill("", timeout=action_timeout_ms)
                    fld.fill(vh, timeout=action_timeout_ms)
                    fld.press("Tab", timeout=1200)
                    return True
                fld.press("ArrowDown", timeout=1200)
                fld.press("Enter", timeout=1200)
                return True
            except Exception:
                continue
        return False

    pre_save_enquiry_no = _scrape_enquiry_number_from_frame(enq_frame)

    def try_field(labels: tuple[str, ...], value: str, *, required: bool) -> bool:
        sv = (value or "").strip()
        if not required and not sv:
            return True
        for lb in labels:
            if _fill_by_label_on_frame(enq_frame, lb, sv, action_timeout_ms=action_timeout_ms):
                _safe_page_wait(page, 200, log_label="add_enq_after_field")
                return True
            if _select_dropdown_by_label_on_frame(
                enq_frame,
                label=lb,
                value=sv,
                action_timeout_ms=min(action_timeout_ms, 8000),
            ):
                _safe_page_wait(page, 200, log_label="add_enq_after_dd_frame_scoped")
                return True
        if required:
            note(f"Add Enquiry: could not set {labels} to {sv!r}.")
            return False
        return True

    def try_field_any(labels: tuple[str, ...], candidates: tuple[str, ...]) -> bool:
        """Best-effort: return True if any candidate was applied."""
        for cand in candidates:
            c = (cand or "").strip()
            if not c:
                continue
            if try_field(labels, c, required=False):
                return True
        return False

    first = (dms_values.get("first_name") or "").strip()
    last = (dms_values.get("last_name") or "").strip() or "."
    mobile = (dms_values.get("mobile_phone") or "").strip()
    landline = (dms_values.get("landline") or dms_values.get("alt_phone_num") or "").strip()
    state = (dms_values.get("state") or "").strip()
    district = (dms_values.get("district") or "").strip()
    tehsil = (dms_values.get("tehsil") or "").strip()
    city = (dms_values.get("city") or "").strip()
    addr = _address_line1_between_first_second_comma((dms_values.get("address_line_1") or "").strip())
    pin = (dms_values.get("pin_code") or "").strip()
    age = (dms_values.get("age") or "").strip()
    if not age:
        age = _derive_age_from_dob_text((dms_values.get("date_of_birth") or "").strip())
    gender = _normalize_gender_for_form((dms_values.get("gender") or "").strip())
    model_i = (scraped_v.get("model") or "").strip()
    color_i = (scraped_v.get("color") or "").strip()
    today_str = _siebel_ist_today().strftime("%d/%m/%Y")

    if not try_field(("Contact First Name", "First Name"), first, required=True):
        return False, "Could not set Contact First Name.", ""
    if not try_field(("Contact Last Name", "Last Name"), last, required=True):
        return False, "Could not set Contact Last Name.", ""
    if not try_field(("Mobile Phone", "Mobile Phone #", "Cellular Phone"), mobile, required=True):
        return False, "Could not set Mobile Phone.", ""
    landline_use = landline or mobile

    if not try_field(("Landline #", "Landline", "Home Phone #", "Home Phone", "Land Line", "Alternate Phone", "Alternate Number"), landline_use, required=True):
        return False, "Could not set Landline.", ""
    if not try_field(("Email", "Email Address", "E-mail"), "NA", required=True):
        return False, "Could not set Email.", ""

    if not try_field(("UIN Type",), "Aadhaar Card", required=True):
        if not try_field_any(("UIN Type",), ("Aadhaar",)):
            return False, "Could not set UIN Type (Aadhaar).", ""
    if not try_field(("UIN No.", "UIN Number", "UIN No"), aadhar, required=True):
        return False, "Could not set UIN No.", ""

    if not try_field(("State",), state, required=True):
        return False, "Could not set State.", ""
    dist_use = district or city
    tehsil_use = tehsil or city
    if not try_field(("District",), dist_use, required=True):
        return False, "Could not set District.", ""
    if not try_field(("Tehsil", "Tehsil/Taluka", "Taluka"), tehsil_use, required=True):
        return False, "Could not set Tehsil/Taluka.", ""
    if not try_field(("City", "City/Town/Village"), city, required=True):
        return False, "Could not set City/Town/Village.", ""
    if not _city_pick_any_then_ok(enq_frame):
        return False, "Could not pick City/Town/Village from search sub form.", ""
    if not try_field(("Address Line 1", "Address Line1", "Address"), addr, required=True):
        return False, "Could not set Address Line 1.", ""
    if not try_field(("Pin Code", "Pin code", "PIN Code", "Postal Code"), pin, required=True):
        return False, "Could not set Pin Code.", ""

    if not try_field(
        ("Model Interested In", "Model Interested in", "Interested Model", "Model"),
        model_i,
        required=True,
    ):
        return False, "Could not set Model Interested In (from vehicle scrape).", ""
    if not try_field(("Color", "Colour"), color_i, required=True):
        return False, "Could not set Color (from vehicle scrape).", ""

    if not try_field(("Finance Required",), finance_required, required=True):
        return False, "Could not set Finance Required.", ""
    if not try_field(("Booking Order Type",), "Normal Booking", required=True):
        return False, "Could not set Booking Order Type.", ""
    sku_hint = (scraped_v.get("sku") or "").strip()
    if not _select_variant_first_value(enq_frame, variant_hint=sku_hint):
        note("Add Enquiry: Variant auto-select with SKU/pick failed — will try Tab selection after Model.")
        if not _fill_by_label_on_frame(enq_frame, "Variant", " ", action_timeout_ms=action_timeout_ms):
            note("Add Enquiry: Variant field could not be activated for Tab-pick.")
    else:
        note("Add Enquiry: Variant selected successfully.")

    # Age & Gender filled AFTER Model/Variant — Siebel form resets these fields
    # on Model/Variant server round-trip, so they must go last.
    if not try_field(("Age(Years)", "Age"), age, required=True):
        return False, "Could not set Age.", ""
    if not try_field(("Gender",), gender, required=True):
        return False, "Could not set Gender.", ""
    try_field_any(("Enquiry Source",), ("Walk-In", "Walk In", "Walkin"))
    if not try_field(("Point of Contact",), "Customer Walk-in", required=True):
        if not try_field_any(
            ("Point of Contact",),
            ("Customer Walk-In", "Walk-In", "Customer Walk In"),
        ):
            return False, "Could not set Point of Contact.", ""

    try_field_any(
        ("Actual Enquiry Date", "Enquiry Date", "Actual Enquiry Dt"),
        (today_str,),
    )

    note("Add Enquiry: Financier fields skipped by design (tenant control).")

    # --- Finalize: always Ctrl+S ---
    try:
        page.keyboard.press("Control+s")
    except Exception:
        try:
            page.keyboard.press("Meta+s")
        except Exception:
            note("Add Enquiry: Ctrl+S failed.")
            return False, "Ctrl+S save failed on new opportunity form.", ""
    note("Add Enquiry: pressed Ctrl+S to save enquiry.")
    _save_t0 = time.monotonic()

    _safe_page_wait(page, 350, log_label="add_enquiry_after_save_immediate")
    _save_err_immediate = _detect_siebel_error_popup(page, content_frame_selector)
    if _save_err_immediate:
        note(f"Add Enquiry: immediate Siebel error after Ctrl+S → {_save_err_immediate!r:.300}")
        return False, f"Siebel error after Ctrl+S: {_save_err_immediate[:200]}", ""

    note(f"Add Enquiry: pre_save Enquiry# gate baseline → {pre_save_enquiry_no!r}.")
    pre_norm = (pre_save_enquiry_no or "").strip()
    poll_readings: list[tuple[float, str]] = []
    enquiry_no = ""
    for target_sec in (0.5, 2.5, 3.5):
        elapsed = time.monotonic() - _save_t0
        need_s = target_sec - elapsed
        if need_s > 0:
            _safe_page_wait(
                page,
                int(need_s * 1000) + 1,
                log_label=f"add_enquiry_enquiry_gate_{target_sec}s",
            )
        _save_error = _detect_siebel_error_popup(page, content_frame_selector)
        if _save_error:
            note(f"Add Enquiry: Siebel error after save → {_save_error!r:.300}")
            return False, f"Siebel error after Ctrl+S: {_save_error[:200]}", ""
        cur = _scrape_enquiry_number_from_frame(enq_frame)
        cur_norm = (cur or "").strip()
        poll_readings.append((target_sec, cur_norm))
        note(
            f"Add Enquiry: enquiry# poll at {target_sec}s post-Ctrl+S → {cur_norm!r} "
            f"(compare pre_save={pre_norm!r})."
        )
        if cur_norm and cur_norm != pre_norm:
            enquiry_no = cur_norm
            break

    if not enquiry_no or pre_norm == (enquiry_no or "").strip():
        note(
            "Add Enquiry: HARD FAIL — Enquiry# unchanged vs pre-save after timed polls "
            f"(0.5s / 2.5s / 3.5s). pre={pre_norm!r} readings={poll_readings!r}."
        )
        return (
            False,
            "Enquiry# did not change from pre-save after Ctrl+S (polled at 0.5s, 2.5s, 3.5s). "
            "See the Playwright DMS execution log [NOTE] lines for poll values.",
            "",
        )

    note(f"Add Enquiry: saved Enquiry#={enquiry_no!r} (gate passed).")
    if callable(form_trace):
        form_trace(
            "add_enquiry_saved",
            "Opportunity Form",
            "post_save_scrape_enquiry_number",
            enquiry_number=enquiry_no,
            pre_save_enquiry=pre_norm,
            poll_readings_repr=str(poll_readings),
        )
    return True, None, enquiry_no


def prepare_customer(*args: Any, **kwargs: Any) -> bool:
    """Siebel Find Contact → payment + collate (delegates to ``hero_dms_prepare_customer.prepare_customer``)."""
    from app.services.hero_dms_prepare_customer import prepare_customer as _prepare_customer_impl

    return _prepare_customer_impl(*args, **kwargs)
