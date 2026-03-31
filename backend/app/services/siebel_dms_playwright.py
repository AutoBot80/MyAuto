"""
Hero Connect / Oracle Siebel Open UI — Playwright helpers for real DMS automation.

**Linear SOP** in ``Playwright_Hero_DMS_fill`` (BRD §6.1a aligned) when ``SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES``
is False. When True, only the operator **Find Contact Enquiry** path runs (Find → Contact → mobile → Go →
drill hit → Contacts → Contact_Enquiry → Enquiry → All Enquiries), then returns with the browser left open.

Default staged flow (flag False): **Find → mobile → Go**; optional
**basic enquiry** (name/address/state/PIN) + Save + **mandatory re-find** when created; **always**
care-of + Save; **Auto Vehicle List** (``prepare_vehicle``: Find→Vehicles, list scrape, mandatory left-pane VIN,
key/battery, merge + inventory, Serial drilldown → Features → Pre-check/PDI at dealer; In Transit → receipt
URL only, no Pre-check/PDI);
**Generate Booking**
**after vehicle for all paths**; allotment (line items) when **not** In Transit;
``_attach_vehicle_to_bkg`` runs **Apply Campaign** at the end (**Create Invoice** is operator-only while
``_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE`` is False).

Siebel renders in nested iframes. The **Find** pane and grids use labels like **Mobile Phone**,
**Mobile Phone #**, or **Mobile Number** — often via ``<label>`` / ``aria-labelledby``, not
``aria-label``, so we try CSS selectors first, then ``get_by_label`` / ``get_by_role``.
Tune with:

- ``DMS_SIEBEL_CONTENT_FRAME_SELECTOR`` — CSS for ``page.frame_locator(...)`` when auto-detection
  fails. Chain nested iframes with `` >> `` (outer ``>>`` inner), e.g. ``iframe#shell >> iframe#s_0_1``.
- ``DMS_SIEBEL_AUTO_IFRAME_SELECTORS`` — comma-separated extra iframe CSS roots tried (after the
  explicit content selector) before walking every frame.
- ``DMS_SIEBEL_POST_GOTO_WAIT_MS`` — minimum wait after ``goto`` contact/enquiry so applets render.
- ``DMS_SIEBEL_MOBILE_ARIA_HINTS`` — comma-separated extra substrings for mobile field
  (adds ``input[aria-label*="<hint>" i]`` patterns).
"""
from __future__ import annotations

import logging
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeout

from app.config import (
    DMS_SIEBEL_AUTO_IFRAME_SELECTORS,
    DMS_SIEBEL_INTER_ACTION_DELAY_MS,
    DMS_SIEBEL_POST_GOTO_WAIT_MS,
)

logger = logging.getLogger(__name__)

# Siebel DMS and operator-entered dates/times are **IST** (Asia/Kolkata, UTC+05:30).
_SIEBEL_TZ = ZoneInfo("Asia/Kolkata")


def _siebel_ist_now() -> datetime:
    """Current wall-clock time in Asia/Kolkata (IST)."""
    return datetime.now(_SIEBEL_TZ)


def _siebel_ist_today() -> date:
    """Calendar *today* in Asia/Kolkata (IST)."""
    return _siebel_ist_now().date()


def _siebel_naive_datetime_as_ist(dt: datetime) -> datetime:
    """Treat naive parsed datetimes as IST (Siebel shows local IST; no offset in cells)."""
    if dt.tzinfo is not None:
        return dt.astimezone(_SIEBEL_TZ)
    return dt.replace(tzinfo=_SIEBEL_TZ)


# Operator video: ``Find Contact Enquiry.mp4`` — Find → Contact → mobile → Go; if **no contact table
# rows**, **Add Enquiry** (vehicle chassis/VIN + engine, Enquiry tab, **Opportunity Form:New**, DB fields,
# Ctrl+S) then stop; else drill → Contacts → relation fill → Payments ``+``. Set False to restore the full
# BRD linear SOP inside ``Playwright_Hero_DMS_fill``.
SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES = True


# region agent log
def _agent_debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    """Session-scoped NDJSON debug log for runtime hypothesis validation."""
    try:
        payload = {
            "sessionId": "0875fe",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        log_path = Path(__file__).resolve().parents[3] / "debug-0875fe.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


# endregion


def _normalize_cubic_cc_digits(val: object) -> str:
    """Extract numeric cc from Siebel grid/feature text (e.g. ``125 CC`` → ``125``)."""
    s = str(val or "").strip().replace(",", "")
    if not s:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return m.group(1) if m else ""


def _is_browser_disconnected_error(exc: BaseException) -> bool:
    """True when Playwright lost the browser/WebSocket (tab closed, crash, CDP ended)."""
    msg = str(exc).lower()
    return any(
        n in msg
        for n in (
            "connection closed",
            "target closed",
            "browser has been closed",
            "econnreset",
            "websocket error",
            "socket hang up",
        )
    )


def _safe_page_wait(target, ms: int, *, log_label: str = "") -> None:
    """
    ``page.wait_for_timeout`` / frame wait that maps driver disconnect to a clear operator message.
    """
    if ms <= 0:
        return
    is_closed_fn = getattr(target, "is_closed", None)
    if callable(is_closed_fn):
        try:
            if is_closed_fn():
                raise RuntimeError(
                    "Siebel: the browser page was already closed before automation could continue"
                    + (f" ({log_label})." if log_label else ".")
                    + " Leave the Hero Connect tab open for the full Fill DMS run."
                )
        except RuntimeError:
            raise
        except Exception:
            pass
    try:
        target.wait_for_timeout(ms)
    except Exception as e:
        if _is_browser_disconnected_error(e):
            raise RuntimeError(
                "Siebel: lost connection to the browser"
                + (f" during wait ({log_label})" if log_label else "")
                + ". The window may have been closed, the browser may have crashed, or the debug "
                "(CDP) session ended. Keep Edge/Chrome open on Hero Connect while Fill DMS runs; "
                "avoid closing the browser or restarting it mid-run. If the API process restarted, "
                "open Hero Connect again and retry Fill DMS."
            ) from e
        raise


def _siebel_inter_action_pause(page: Page) -> None:
    """Optional pause after navigation; helps heavy Open UI applets settle (see ``DMS_SIEBEL_INTER_ACTION_DELAY_MS``)."""
    ms = int(DMS_SIEBEL_INTER_ACTION_DELAY_MS)
    if ms <= 0:
        return
    cap = 60_000
    _safe_page_wait(page, min(ms, cap), log_label="inter_action_delay")


def _detect_siebel_error_popup(page: Page, content_frame_selector: str | None) -> str | None:
    """Return error text from a visible Siebel error/alert popup, or None."""

    def _is_real_error(txt: str) -> bool:
        """Filter out Siebel status bar / debug text that is NOT an actual error."""
        t = (txt or "").strip()
        if not t or len(t) < 5:
            return False
        tl = t.lower()
        # Status-bar / footer boilerplate — never an error
        if "appletRN:" in t or "ViewRN:" in t or "ScreenRN:" in t or "COPY STRING:" in t:
            return False
        if tl.startswith("applet") and "viewrn" in tl:
            return False
        # Real errors contain keywords
        _err_kw = ("error", "required", "sbl-", "invalid", "cannot", "failed",
                    "mandatory", "not valid", "missing", "exception", "unable")
        for kw in _err_kw:
            if kw in tl:
                return True
        # If it looks like a Siebel error code pattern (SBL-XXX-NNNNN)
        if re.search(r"SBL-\w{3}-\d{4,}", t, re.I):
            return True
        return True  # Assume real if no status-bar pattern detected

    search_roots: list = []
    try:
        search_roots = list(_siebel_locator_search_roots(page, content_frame_selector))
    except Exception:
        pass
    search_roots += list(_ordered_frames(page))
    for root in search_roots:
        try:
            msg = root.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 5 && r.height > 5;
                  };
                  // Priority 1: alert/error-specific selectors
                  for (const s of [
                    "[role='alertdialog']", "[role='alert']",
                    ".siebui-popup-error", ".siebui-alert",
                    ".error-dialog",
                    "[id*='ErrorPopup']", "[id*='_swe_alert']",
                    "[class*='error' i][class*='popup' i]",
                    "[class*='modal' i][class*='error' i]"
                  ]) {
                    const el = document.querySelector(s);
                    if (el && vis(el)) {
                      const txt = (el.innerText || el.textContent || '').trim();
                      if (txt.length > 3) return txt.substring(0, 500);
                    }
                  }
                  // Priority 2: generic dialog selectors (need content filtering)
                  for (const s of [
                    ".ui-dialog", ".siebui-popup", ".siebui-msg-popup",
                    "[id*='popup' i][class*='ui-dialog' i]",
                    "[role='dialog']"
                  ]) {
                    const el = document.querySelector(s);
                    if (el && vis(el)) {
                      const txt = (el.innerText || el.textContent || '').trim();
                      if (txt.length > 3) return '@@NEEDS_FILTER@@' + txt.substring(0, 500);
                    }
                  }
                  return null;
                }"""
            )
            if msg:
                needs_filter = msg.startswith("@@NEEDS_FILTER@@")
                clean = msg.replace("@@NEEDS_FILTER@@", "").strip()
                if needs_filter:
                    if _is_real_error(clean):
                        return clean
                else:
                    return clean
        except Exception:
            continue
    # Also check for Siebel error dialogs with specific content selectors
    try:
        alert_txt = page.evaluate("""() => {
            const vis = (el) => { if(!el) return false; const st=window.getComputedStyle(el); return st.display!=='none' && st.visibility!=='hidden'; };
            const d = document.querySelector('.ui-dialog-content, .siebui-popup-msg, [id*="errmsg"], [id*="ErrMsg"]');
            if (d && vis(d)) return (d.innerText || d.textContent || '').trim().substring(0, 500);
            return null;
        }""")
        if alert_txt and len(alert_txt) > 3 and _is_real_error(alert_txt):
            return alert_txt
    except Exception:
        pass
    return None


# Tried after explicit DMS_SIEBEL_CONTENT_FRAME_SELECTOR and before walking all frames.
_DEFAULT_AUTO_IFRAME_SELECTORS: tuple[str, ...] = (
    'iframe[src*="start.swe" i]',
    'iframe[src*="StartSWE" i]',
    'iframe[src*="sweapp" i]',
    'iframe[src*="SWECmd" i]',
    'iframe[id^="s_"]',
    'iframe[name^="s_"]',
)

# Same order as fill_dms_service.DMS_MILESTONE_ORDER (avoid import cycle).
_MILESTONE_SORT_ORDER: tuple[str, ...] = (
    "Customer found",
    "All Enquiries opened",
    "Care of filled",
    "Enquiry created",
    "Booking generated",
    "Vehicle received",
    "Pre check completed",
    "Vehicle inspection done",
    "Vehicle allocated",
    "Allotment view opened",
    "Invoice created",
)


def _sort_milestone_labels(labels: list[str]) -> list[str]:
    order = {k: i for i, k in enumerate(_MILESTONE_SORT_ORDER)}
    return sorted(labels, key=lambda x: order.get(x, 99))


@dataclass(frozen=True)
class SiebelDmsUrls:
    contact: str
    vehicles: str
    precheck: str
    pdi: str
    vehicle: str
    enquiry: str
    line_items: str
    reports: str


def _goto(page: Page, url: str, label: str, *, nav_timeout_ms: int) -> None:
    u = (url or "").strip()
    if not u:
        return
    logger.info("siebel_dms: navigate %s -> %s", label, u[:180])
    page.goto(u, wait_until="domcontentloaded", timeout=nav_timeout_ms)
    _siebel_inter_action_pause(page)


def _frame_score(url: str) -> int:
    u = (url or "").lower()
    score = 0
    if "start.swe" in u or "sweapp" in u:
        score += 6
    if "edealer" in u or "siebel" in u or "heromotocorp" in u:
        score += 4
    if len(u) > 40:
        score += 1
    return score


def _ordered_frames(page: Page) -> list[Frame]:
    """Prefer Siebel content iframes; include main frame last as fallback."""
    frames = list(page.frames)
    main = page.main_frame
    rest = [f for f in frames if f != main]
    rest.sort(key=lambda f: -_frame_score(f.url or ""))
    return rest + [main]


def _frames_for_enquiry_subgrid_eval(page: Page) -> list[Frame]:
    """
    Frames for **Contact_Enquiry** jqGrid / ``Enquiry_`` scrape after a contact drilldown.

    **Main document first** — Hero Connect often renders the opened contact, tabs, and
    ``#jqgh_s_1_l_Enquiry_`` / ``input[name=\"Enquiry_\"]`` there. Remaining Siebel iframes follow
    ``_ordered_frames`` order (excluding main) so duplicate-mobile sweeps still find subgrids that
    live only inside an iframe.
    """
    main = page.main_frame
    out: list[Frame] = [main]
    for f in _ordered_frames(page):
        if f != main:
            out.append(f)
    return out


def _chained_frame_locator(page: Page, sel: str):
    """
    Build a nested ``FrameLocator`` from ``DMS_SIEBEL_CONTENT_FRAME_SELECTOR``.
    Use `` >> `` between iframe CSS selectors (outer to inner).
    """
    parts = [p.strip() for p in re.split(r"\s*>>\s*", sel) if p.strip()]
    if not parts:
        return None
    fl = page.frame_locator(parts[0])
    for p in parts[1:]:
        fl = fl.frame_locator(p)
    return fl


def _iter_frame_locator_roots(page: Page, content_frame_selector: str | None):
    """
    Yields ``FrameLocator`` roots: explicit chained selector (if set), then
    ``DMS_SIEBEL_AUTO_IFRAME_SELECTORS`` and built-in Siebel iframe patterns (deduped by string).
    """
    seen: set[str] = set()
    explicit = (content_frame_selector or "").strip()
    if explicit:
        fl = _chained_frame_locator(page, explicit)
        if fl is not None:
            yield fl
        seen.add(explicit.lower())
    for s in (*DMS_SIEBEL_AUTO_IFRAME_SELECTORS, *_DEFAULT_AUTO_IFRAME_SELECTORS):
        t = (s or "").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        yield page.frame_locator(t)


def _siebel_after_goto_wait(page: Page, *, floor_ms: int = 1200) -> None:
    cap = 120_000
    raw = int(DMS_SIEBEL_POST_GOTO_WAIT_MS)
    ms = min(max(raw, floor_ms), cap)
    _safe_page_wait(page, ms, log_label="post_goto")


def _try_expand_find_flyin(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    """If the Find pane is collapsed, click chrome that reveals it (tenant-specific titles)."""
    expand_css = (
        'button[title*="Show Find" i]',
        'a[title*="Show Find" i]',
        'button[title*="Find Pane" i]',
        '[role="button"][aria-label*="Find" i][aria-label*="pane" i]',
        'button[title*="Expand Find" i]',
    )

    def try_root(root) -> bool:
        for css in expand_css:
            try:
                loc = root.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: expanded Find fly-in (%s)", css[:70])
                    _safe_page_wait(page, 800, log_label="find_flyin_expand")
                    return True
            except Exception:
                continue
        return False

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        try:
            if try_root(fl):
                return True
        except Exception:
            pass
    for frame in _ordered_frames(page):
        if try_root(frame):
            return True
    return False


_MOBILE_DOM_EVAL_JS = """(v) => {
  const BAD = /hidden|submit|button|checkbox|radio|file|image/i;
  const inputs = Array.from(document.querySelectorAll("input")).filter(
    (el) => el.type && !BAD.test(el.type)
  );
  const vis = (el) => {
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2;
  };
  const scoreEl = (el) => {
    const al = (el.getAttribute("aria-label") || "").toLowerCase();
    const t = (el.getAttribute("title") || "").toLowerCase();
    const nm = (el.getAttribute("name") || "").toLowerCase();
    const id = (el.id || "").toLowerCase();
    const ph = (el.getAttribute("placeholder") || "").toLowerCase();
    let s = 0;
    if (al.includes("mobile") || t.includes("mobile") || ph.includes("mobile")) s += 12;
    if (al.includes("cellular") || t.includes("cellular")) s += 10;
    if ((al.includes("phone") || t.includes("phone")) && !al.includes("work") && !t.includes("work"))
      s += 6;
    if (nm.includes("mobile") || id.includes("mobile")) s += 8;
    const ml = el.getAttribute("maxlength");
    if (ml === "10") s += 4;
    if (ml === "12") s += 2;
    return s;
  };
  let best = null;
  let bestSc = 0;
  for (const el of inputs) {
    if (!vis(el)) continue;
    const sc = scoreEl(el);
    if (sc > bestSc) {
      bestSc = sc;
      best = el;
    }
  }
  if (!best || bestSc < 8) return false;
  try {
    best.focus();
    best.value = "";
    best.value = String(v || "").trim();
    best.dispatchEvent(new Event("input", { bubbles: true }));
    best.dispatchEvent(new Event("change", { bubbles: true }));
    best.dispatchEvent(new Event("blur", { bubbles: true }));
  } catch (e) {
    return false;
  }
  return true;
}"""


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
                            "location": "siebel_dms_playwright.py:_try_fill_mobile_and_find_in_contact_applet",
                            "message": message,
                            "data": data,
                            "timestamp": int(_t_dbg.time() * 1000),
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


def _locator_for_duplicate_fields(locator, *, prefer_second_if_duplicate: bool):
    """
    When Find + Customer applets both expose the same label (e.g. two First Name inputs),
    ``.first`` targets the Find row; use index 1 for the main enquiry form when two+ exist.
    """
    try:
        n = locator.count()
        if n <= 0:
            return None
        idx = 1 if (prefer_second_if_duplicate and n >= 2) else 0
        return locator.nth(idx)
    except Exception:
        return None


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
            return True
        changed_global = _select_global_find_vehicles(root)
        if select_vehicles_on_native_selects(root):
            return True
        if open_find_dropdown_then_vehicles(page_, root):
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


def _try_fill_vin_engine_in_vehicles_find_applet(
    page: Page,
    *,
    chassis_wildcard: str,
    engine_wildcard: str,
    timeout_ms: int,
    content_frame_selector: str | None,
) -> bool:
    """
    Fill **VIN** and **Engine#** inside the Find→Vehicles right fly-in, then **Enter** on the engine field.
    Values should already include Siebel ``*`` wildcards when required.
    """
    cw = (chassis_wildcard or "").strip()
    ew = (engine_wildcard or "").strip()
    if not cw or not ew:
        return False

    # #region agent log - vehicle find same-frame diagnostics
    def _dbgv(hypothesis_id: str, message: str, data: dict) -> None:
        try:
            import json as _j_dbgv, time as _t_dbgv
            from pathlib import Path as _P_dbgv
            _log_path = _P_dbgv(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_log_path, "a", encoding="utf-8") as _lf_dbgv:
                _lf_dbgv.write(
                    _j_dbgv.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "pre-fix",
                            "hypothesisId": hypothesis_id,
                            "location": "siebel_dms_playwright.py:_try_fill_vin_engine_in_vehicles_find_applet",
                            "message": message,
                            "data": data,
                            "timestamp": int(_t_dbgv.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass

    _dbgv(
        "V1",
        "vehicle_find_entry",
        {"has_vin": bool(cw), "has_engine": bool(ew), "vin_len": len(cw), "engine_len": len(ew)},
    )
    # #endregion

    vin_css = (
        'input#field_textbox_0',
        'input[id="field_textbox_0"]',
        'input[title*="VIN" i]',
        'input[aria-label*="VIN" i]',
        'input[title*="Chassis" i]',
        'input[aria-label*="Chassis" i]',
    )
    eng_css = (
        'input#field_textbox_2',
        'input[id="field_textbox_2"]',
        'input[title*="Engine#" i]',
        'input[title*="Engine #" i]',
        'input[aria-label*="Engine#" i]',
        'input[aria-label*="Engine #" i]',
        'input[title^="Engine" i]',
        'input[aria-label*="Engine" i]',
    )

    def try_root(root) -> bool:
        # #region agent log - vehicle findfieldsbox probe on this root
        try:
            _ff_probe = root.evaluate(
                """() => {
                  const box = document.getElementById('findfieldsbox') || document.getElementById('findfieldbox');
                  if (!box) {
                    return { has_box: false, vin_id_present: false, engine_id_present: false, vin_editable: false, engine_editable: false };
                  }
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 2 && r.height >= 2;
                  };
                  const vin = box.querySelector('input#field_textbox_0');
                  const eng = box.querySelector('input#field_textbox_2');
                  const editable = (el) => !!(el && vis(el) && !el.readOnly && !el.disabled);
                  return {
                    has_box: true,
                    vin_id_present: !!vin,
                    engine_id_present: !!eng,
                    vin_editable: editable(vin),
                    engine_editable: editable(eng),
                  };
                }"""
            )
            _dbgv("V2", "vehicle_findfieldsbox_probe", _ff_probe or {})
        except Exception:
            _dbgv("V2", "vehicle_findfieldsbox_probe_eval_failed", {})

        # Strict path: fill inside same-frame #findfieldsbox using required IDs.
        try:
            _strict_out = root.evaluate(
                """(args) => {
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

                  vin.focus();
                  vin.value = '';
                  vin.value = vinVal;
                  vin.dispatchEvent(new Event('input', { bubbles: true }));
                  vin.dispatchEvent(new Event('change', { bubbles: true }));

                  eng.focus();
                  eng.value = '';
                  eng.value = engVal;
                  eng.dispatchEvent(new Event('input', { bubbles: true }));
                  eng.dispatchEvent(new Event('change', { bubbles: true }));

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
                    if (b && vis(b)) {
                      try { b.click(); return { ok: true, mode: 'find_button_in_box' }; } catch (e) {}
                    }
                  }
                  try {
                    eng.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
                    eng.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
                    return { ok: true, mode: 'enter_fallback_in_box' };
                  } catch (e) {
                    return { ok: false, reason: 'find_click_and_enter_failed' };
                  }
                }""",
                {"vin": cw, "eng": ew},
            )
            _dbgv("V6", "vehicle_findfieldsbox_strict_fill_attempt", _strict_out or {})
            if _strict_out and _strict_out.get("ok"):
                return True
        except Exception:
            _dbgv("V6", "vehicle_findfieldsbox_strict_fill_eval_failed", {})
        # #endregion
        applets: list = []
        try:
            cand = root.locator(".siebui-applet").filter(has_text=re.compile(r"VIN|Engine", re.I))
            n = cand.count()
            for i in range(min(n, 14)):
                a = cand.nth(i)
                try:
                    if not a.is_visible(timeout=500):
                        continue
                    txt = (a.inner_text(timeout=900) or "").lower()
                    if "vin" in txt and "engine" in txt:
                        applets.append(a)
                except Exception:
                    continue
        except Exception:
            pass
        if not applets:
            try:
                cand = root.locator(".siebui-applet").filter(has_text=re.compile(r"Engine", re.I))
                n = cand.count()
                for i in range(min(n, 10)):
                    a = cand.nth(i)
                    try:
                        if a.is_visible(timeout=450):
                            applets.append(a)
                    except Exception:
                        continue
            except Exception:
                pass

        for applet in applets:
            # #region agent log - vehicle applet candidate quality
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
                      const vin = el.querySelector('input#field_textbox_0');
                      const eng = el.querySelector('input#field_textbox_2');
                      return {
                        has_vin_id: !!vin,
                        has_engine_id: !!eng,
                        vin_editable: !!(vin && vis(vin) && !vin.readOnly && !vin.disabled),
                        engine_editable: !!(eng && vis(eng) && !eng.readOnly && !eng.disabled),
                        text_sample: String(el.innerText || '').slice(0, 220),
                      };
                    }"""
                )
                _dbgv("V3", "vehicle_applet_candidate_probe", _applet_diag or {})
            except Exception:
                _dbgv("V3", "vehicle_applet_candidate_probe_eval_failed", {})
            # #endregion
            try:
                vin_loc = applet.locator('input#field_textbox_0, input[id="field_textbox_0"]').first
                eng_loc = applet.locator('input#field_textbox_2, input[id="field_textbox_2"]').first
                if (
                    vin_loc.count() <= 0
                    or eng_loc.count() <= 0
                    or not vin_loc.is_visible(timeout=700)
                    or not eng_loc.is_visible(timeout=700)
                ):
                    continue
            except Exception:
                continue
            try:
                try:
                    vin_loc.click(timeout=min(3000, timeout_ms))
                except Exception:
                    vin_loc.click(timeout=min(3000, timeout_ms), force=True)
                vin_loc.fill("", timeout=min(3000, timeout_ms))
                vin_loc.fill(cw, timeout=timeout_ms)
                try:
                    eng_loc.click(timeout=min(3000, timeout_ms))
                except Exception:
                    eng_loc.click(timeout=min(3000, timeout_ms), force=True)
                eng_loc.fill("", timeout=min(3000, timeout_ms))
                eng_loc.fill(ew, timeout=timeout_ms)
                eng_loc.press("Enter", timeout=min(8000, timeout_ms))
                logger.info("siebel_dms: filled VIN + Engine# in Vehicles Find applet and pressed Enter")
                _dbgv("V4", "vehicle_find_fill_success_same_applet", {"used_ids": True})
                return True
            except Exception:
                continue
        return False

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
    _dbgv("V5", "vehicle_find_failed_all_roots", {"reason": "strict_ids_not_found_or_not_editable"})
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


def _fill_in_frame(
    frame: Frame,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int,
    prefer_second_if_duplicate: bool = False,
    visible_timeout_ms: int = 800,
) -> bool:
    if not (value or "").strip():
        return False
    for css in selectors:
        try:
            base = frame.locator(css)
            loc = _locator_for_duplicate_fields(
                base, prefer_second_if_duplicate=prefer_second_if_duplicate
            )
            if loc is None:
                continue
            try:
                if not loc.is_visible(timeout=visible_timeout_ms):
                    continue
            except Exception:
                continue
            try:
                loc.click(timeout=min(3000, timeout_ms))
            except Exception:
                loc.click(timeout=min(3000, timeout_ms), force=True)
            loc.fill("", timeout=min(3000, timeout_ms))
            loc.fill(value.strip(), timeout=timeout_ms)
            logger.info("siebel_dms: filled via %s", css[:120])
            return True
        except Exception as e:
            logger.debug("siebel_dms: selector %s failed: %s", css[:80], e)
            continue
    return False


def _fill_with_frame_locator(
    fl,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int,
    prefer_second_if_duplicate: bool = False,
    visible_timeout_ms: int = 800,
) -> bool:
    if not (value or "").strip():
        return False
    for css in selectors:
        try:
            base = fl.locator(css)
            loc = _locator_for_duplicate_fields(
                base, prefer_second_if_duplicate=prefer_second_if_duplicate
            )
            if loc is None:
                continue
            try:
                if not loc.is_visible(timeout=visible_timeout_ms):
                    continue
            except Exception:
                continue
            try:
                loc.click(timeout=min(3000, timeout_ms))
            except Exception:
                loc.click(timeout=min(3000, timeout_ms), force=True)
            loc.fill("", timeout=min(3000, timeout_ms))
            loc.fill(value.strip(), timeout=timeout_ms)
            logger.info("siebel_dms: filled (scoped frame) via %s", css[:120])
            return True
        except Exception as e:
            logger.debug("siebel_dms: scoped selector %s failed: %s", css[:80], e)
            continue
    return False


def _try_fill_field(
    page: Page,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
    prefer_second_if_duplicate: bool = False,
    visible_timeout_ms: int = 800,
) -> bool:
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        if _fill_with_frame_locator(
            fl,
            selectors,
            value,
            timeout_ms=timeout_ms,
            prefer_second_if_duplicate=prefer_second_if_duplicate,
            visible_timeout_ms=visible_timeout_ms,
        ):
            return True
    for frame in _ordered_frames(page):
        if _fill_in_frame(
            frame,
            selectors,
            value,
            timeout_ms=timeout_ms,
            prefer_second_if_duplicate=prefer_second_if_duplicate,
            visible_timeout_ms=visible_timeout_ms,
        ):
            return True
    return False


def select_siebel_dropdown_value(
    page,
    *,
    field_label_patterns,
    value,
    timeout_ms=5000,
    content_frame_selector=None,
    note=lambda *a, **k: None,
):
    import re

    value_pat = re.compile(rf"^\s*{re.escape(value)}\s*$", re.I)

    def _get_field(frame):
        # Primary: label-based
        for pat in field_label_patterns:
            try:
                loc = frame.get_by_label(pat).first
                if loc.count() > 0:
                    return loc
            except Exception:
                continue

        # Fallback: label proximity
        try:
            loc = frame.locator("td, label, span").filter(
                has_text=re.compile("transaction|payment|type|method", re.I)
            ).locator("input").first
            if loc.count() > 0:
                return loc
        except Exception:
            pass

        return None

    for attempt in range(3):
        try:
            # --- Find field ---
            field = None

            # Try all Siebel roots (iframe-safe)
            for root in _siebel_locator_search_roots(page, content_frame_selector):
                field = _get_field(root)
                if field:
                    break

            if not field:
                raise Exception("Dropdown field not found")

            field.wait_for(state="visible", timeout=timeout_ms)

            # --- Click field ---
            try:
                field.click(timeout=2000)
            except Exception:
                field.click(timeout=2000, force=True)

            # --- Force dropdown open ---
            try:
                page.keyboard.press("ArrowDown")
            except Exception:
                pass

            page.wait_for_timeout(400)

            # --- Select option globally ---
            option = page.locator("li, div, span, a").filter(has_text=value_pat).first
            option.wait_for(state="visible", timeout=timeout_ms)

            try:
                option.click(timeout=2000)
            except Exception:
                option.click(timeout=2000, force=True)

            # --- Verify selection ---
            try:
                val = field.input_value(timeout=2000)
                if val and value.lower() in val.lower():
                    note(f"Dropdown set: {value}")
                    return True
            except Exception:
                pass

        except Exception as e:
            note(f"[WARN] dropdown attempt {attempt+1} failed: {e}")
            page.wait_for_timeout(800)

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


def _try_select_option(
    page: Page,
    selectors: list[str],
    label: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
    prefer_second_if_duplicate: bool = False,
) -> bool:
    if not (label or "").strip():
        return False
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        for css in selectors:
            try:
                base = fl.locator(css)
                loc = _locator_for_duplicate_fields(
                    base, prefer_second_if_duplicate=prefer_second_if_duplicate
                )
                if loc is None:
                    continue
                loc.select_option(label=label.strip(), timeout=timeout_ms)
                logger.info("siebel_dms: selected %s via %s", label[:40], css[:80])
                return True
            except Exception:
                continue
    for frame in _ordered_frames(page):
        for css in selectors:
            try:
                base = frame.locator(css)
                loc = _locator_for_duplicate_fields(
                    base, prefer_second_if_duplicate=prefer_second_if_duplicate
                )
                if loc is None:
                    continue
                loc.select_option(label=label.strip(), timeout=timeout_ms)
                logger.info("siebel_dms: selected %s via %s", label[:40], css[:80])
                return True
            except Exception:
                continue
    return False


def _click_find_go_query(page: Page, *, timeout_ms: int, content_frame_selector: str | None) -> bool:
    """
    Click **Find** / **Go** / **Query** to run the Siebel search.

    Hero Connect **Find** fly-in often uses a **round teal icon** (right arrow) whose tooltip is **Find**,
    with no visible text — match ``title`` / ``aria-label`` / Siebel classes; prefer controls inside the
    applet that already shows **Mobile Phone**.
    """

    _find_go_css = (
        'input[type="submit"][value*="Find" i]',
        'input[type="button"][value*="Find" i]',
        'input[type="submit"][value*="Go" i]',
        'input[type="button"][value*="Go" i]',
        'input[type="submit"][value*="Query" i]',
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
        'a[aria-label*="Find" i]',
        '[data-tooltip*="Find" i]',
        '[data-display*="Find" i]',
        'button.siebui-ctrl-btn[title*="Find" i]',
        'a.siebui-ctrl-btn[title*="Find" i]',
        'button.siebui-ctrl-btn[aria-label*="Find" i]',
    )

    def _try_css_click_on(root, css: str, *, tag: str) -> bool:
        try:
            loc = root.locator(css).first
            if loc.count() > 0 and loc.is_visible(timeout=900):
                try:
                    loc.click(timeout=timeout_ms)
                except Exception:
                    loc.click(timeout=timeout_ms, force=True)
                logger.info("siebel_dms: clicked Find/Go via %s (%s)", tag, css[:72])
                return True
        except Exception:
            pass
        return False

    def try_on_root(root) -> bool:
        # 1) Inside Find applet (right fly-in with Mobile Phone) — avoids wrong Find on another applet
        try:
            find_applets = root.locator(".siebui-applet").filter(
                has_text=re.compile(r"Mobile\s*Phone", re.I)
            )
            ac = find_applets.count()
            for i in range(min(ac, 10)):
                applet = find_applets.nth(i)
                try:
                    if not applet.is_visible(timeout=400):
                        continue
                except Exception:
                    continue
                for role, name_pat in (
                    ("button", re.compile(r"^\s*Find\s*$", re.I)),
                    ("button", re.compile(r"(Find|Go|Query)", re.I)),
                    ("link", re.compile(r"(Find|Go|Query)", re.I)),
                ):
                    try:
                        loc = applet.get_by_role(role, name=name_pat)
                        n = loc.count()
                        for j in range(min(n, 12)):
                            c = loc.nth(j)
                            if c.is_visible(timeout=700):
                                try:
                                    c.click(timeout=timeout_ms)
                                except Exception:
                                    c.click(timeout=timeout_ms, force=True)
                                logger.info(
                                    "siebel_dms: clicked %s in Mobile-Phone find applet (%s)",
                                    role,
                                    name_pat.pattern,
                                )
                                return True
                    except Exception:
                        continue
                for css in _find_go_css:
                    if _try_css_click_on(applet, css, tag="find_applet"):
                        return True
                # Tooltip-only control (teal circle + arrow): HTML ``title`` is often exactly **Find**
                try:
                    titled = applet.get_by_title(re.compile(r"^\s*Find\s*$", re.I))
                    tn = titled.count()
                    for j in range(min(tn, 8)):
                        el = titled.nth(j)
                        if el.is_visible(timeout=600):
                            try:
                                el.click(timeout=timeout_ms)
                            except Exception:
                                el.click(timeout=timeout_ms, force=True)
                            logger.info("siebel_dms: clicked get_by_title(Find) in find applet")
                            return True
                except Exception:
                    pass
                # Icon-only: circular arrow — tooltip **Find** on parent; may be svg/img with no inner text
                try:
                    for img_sel in (
                        'button:has(svg)',
                        'a[role="button"]:has(svg)',
                        '[role="button"]:has(svg)',
                        "button:has(img)",
                    ):
                        btns = applet.locator(img_sel)
                        bn = btns.count()
                        for j in range(min(bn, 15)):
                            b = btns.nth(j)
                            try:
                                t = (
                                    (b.get_attribute("title") or "")
                                    + " "
                                    + (b.get_attribute("aria-label") or "")
                                )
                                if re.search(r"\b(find|go|query)\b", t, re.I) and b.is_visible(timeout=500):
                                    try:
                                        b.click(timeout=timeout_ms)
                                    except Exception:
                                        b.click(timeout=timeout_ms, force=True)
                                    logger.info("siebel_dms: clicked svg/img Find control (title/aria matched)")
                                    return True
                            except Exception:
                                continue
                except Exception:
                    pass
        except Exception:
            pass

        # 2) Whole root (frames / outer shell)
        for role, name_pat in (
            ("button", re.compile(r"(Find|Go|Query)", re.I)),
            ("link", re.compile(r"(Find|Go|Query)", re.I)),
        ):
            try:
                loc = root.get_by_role(role, name=name_pat)
                n = loc.count()
                for i in range(min(n, 20)):
                    c = loc.nth(i)
                    if c.is_visible(timeout=900):
                        try:
                            c.click(timeout=timeout_ms)
                        except Exception:
                            c.click(timeout=timeout_ms, force=True)
                        logger.info("siebel_dms: clicked %s (%s)", role, name_pat.pattern)
                        return True
            except Exception:
                continue
        for css in _find_go_css:
            if _try_css_click_on(root, css, tag="root"):
                return True
        try:
            titled = root.get_by_title(re.compile(r"^\s*Find\s*$", re.I))
            tn = titled.count()
            for j in range(min(tn, 10)):
                el = titled.nth(j)
                if el.is_visible(timeout=700):
                    try:
                        el.click(timeout=timeout_ms)
                    except Exception:
                        el.click(timeout=timeout_ms, force=True)
                    logger.info("siebel_dms: clicked get_by_title(Find) on root")
                    return True
        except Exception:
            pass
        return False

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        if try_on_root(fl):
            return True

    for frame in _ordered_frames(page):
        if try_on_root(frame):
            return True
    return False


def _try_click_toolbar_by_name(
    page: Page,
    name_patterns: tuple[re.Pattern[str], ...],
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
    log_tag: str,
) -> bool:
    """Click first visible button/link whose accessible name matches one of the patterns."""

    def try_root(root) -> bool:
        for pat in name_patterns:
            for role in ("button", "link"):
                try:
                    loc = root.get_by_role(role, name=pat).first
                    if loc.count() > 0 and loc.is_visible(timeout=900):
                        loc.click(timeout=timeout_ms)
                        logger.info("siebel_dms: clicked %s (%s)", log_tag, pat.pattern)
                        return True
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


def _try_click_siebel_save(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"^save$", re.I),
            re.compile(r"save\s+record", re.I),
            re.compile(r"^commit$", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Save",
    )


def _try_click_generate_booking(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"generate\s+booking", re.I),
            re.compile(r"generate\s+book", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Generate Booking",
    )


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

    _safe_page_wait(page, wait_after_go_ms, log_label="after_contact_find_go")
    return True


def _refind_customer_after_enquiry(
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
    first_name: str | None = None,
) -> bool:
    """SOP: after saving a **basic** enquiry, Find → mobile (+ first name when provided) → Go before care-of."""
    note("Stage 3 (mandatory re-find): searching again after enquiry save (mobile + first name when set).")
    step("Re-finding customer after enquiry creation (mandatory SOP).")
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
        stage_msg="Contact view: re-find by mobile + first name (post-enquiry).",
        wait_after_go_ms=2000,
        first_name=first_name,
    )


def _fill_basic_enquiry_details(
    page: Page,
    *,
    first: str,
    last: str,
    addr: str,
    state: str,
    pin: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
) -> None:
    """
    New enquiry / customer applet: **name, address, state, PIN only** — no care-of
    (father/relation) and no landline (strict SOP separation from stage 4).
    """
    _siebel_blur_and_settle(page, ms=350)
    dup = True
    _try_fill_field(
        page,
        [
            'input[aria-label*="First Name" i]',
            'input[title*="First Name" i]',
            'input[name*="FirstName" i]',
        ],
        first,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        prefer_second_if_duplicate=dup,
    )
    _try_fill_field(
        page,
        [
            'input[aria-label*="Last Name" i]',
            'input[title*="Last Name" i]',
            'input[name*="LastName" i]',
        ],
        last,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        prefer_second_if_duplicate=dup,
    )
    _try_fill_field(
        page,
        [
            'input[aria-label*="Address" i]',
            'textarea[aria-label*="Address" i]',
            'input[aria-label*="Street" i]',
        ],
        addr[:120],
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        prefer_second_if_duplicate=dup,
    )
    _try_select_option(
        page,
        [
            'select[aria-label*="State" i]',
            'select[title*="State" i]',
            'select[name*="State" i]',
        ],
        state,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        prefer_second_if_duplicate=dup,
    )
    _try_fill_field(
        page,
        [
            'input[aria-label*="Postal" i]',
            'input[aria-label*="ZIP" i]',
            'input[aria-label*="Pin" i]',
            'input[aria-label*="PIN Code" i]',
        ],
        pin,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        prefer_second_if_duplicate=dup,
    )


def _fill_siebel_enquiry_customer_applet(
    page: Page,
    *,
    first: str,
    last: str,
    addr: str,
    state: str,
    pin: str,
    landline: str,
    father: str,
    relation: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
) -> None:
    """
    Backward-compatible **single call**: basic details + care-of.

    Prefer the staged flow in ``Playwright_Hero_DMS_fill`` (basic save → re-find → care-of).
    ``landline`` is applied here only for legacy callers (not part of strict staged SOP basic step).
    """
    _fill_basic_enquiry_details(
        page,
        first=first,
        last=last,
        addr=addr,
        state=state,
        pin=pin,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    if landline:
        dup = True
        _try_fill_field(
            page,
            [
                'input[aria-label*="Work Phone" i]',
                'input[aria-label*="Alternate" i]',
                'input[aria-label*="Landline" i]',
            ],
            landline,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            prefer_second_if_duplicate=dup,
        )
    _fill_siebel_care_of_only(
        page,
        father=father,
        relation=relation,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )


def _fill_siebel_care_of_only(
    page: Page,
    *,
    father: str,
    relation: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
) -> None:
    """§6.1a: existing contact — only relation prefix + father/husband from DB (no name/address overwrite)."""
    _siebel_blur_and_settle(page, ms=350)
    dup = True
    if father:
        _try_fill_field(
            page,
            [
                "input[title*=\"Relation's Name\" i]",
                "input[aria-label*=\"Relation's Name\" i]",
                'input[aria-label*="Father" i]',
                'input[aria-label*="Husband" i]',
                'input[aria-label*="Parent" i]',
            ],
            father[:255],
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            prefer_second_if_duplicate=dup,
        )
    if relation:
        _try_select_option(
            page,
            [
                'select[title*="S/O" i]',
                'select[aria-label*="S/O" i]',
                'select[title*="W/O" i]',
                'select[aria-label*="W/O" i]',
                'select[title*="D/O" i]',
                'select[aria-label*="D/O" i]',
                'select[title*="(W/O)" i]',
                'select[aria-label*="(W/O)" i]',
                'select[aria-label*="Relation" i]',
                'select[aria-label*="S/O" i]',
            ],
            relation,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            prefer_second_if_duplicate=dup,
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
                            "location": "siebel_dms_playwright.py:_siebel_ui_suggests_contact_match_mobile_first",
                            "message": message,
                            "data": data,
                            "timestamp": int(_t_mf.time() * 1000),
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
) -> tuple[int, int]:
    """
    After Find/Go on Contact: count **drillable** rows for ``mobile`` — same rules as
    ``_contact_find_mobile_drilldown_occurrence_count(..., first_name_exact=None)`` / title sweep ordinals.

    ``first_name`` is ignored for these counts (first name is often absent from list row text).

    Second count: among those rows, how many list texts **hint** at an enquiry (``SENQ``, ``Enquiry`` + digits,
    Siebel-style id). Not a substitute for ``_contact_enquiry_tab_has_rows`` after drilldown.
    """
    needle = _mobile_needle_for_contact_grid_match(mobile)
    if not needle:
        return 0, 0
    plans = _contact_mobile_drilldown_plans(
        page,
        mobile,
        content_frame_selector=content_frame_selector,
        first_name_exact=None,
        log_first_name_row_debug=False,
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


def _siebel_locator_search_roots(page: Page, content_frame_selector: str | None):
    """Frames and chained ``FrameLocator`` roots (Siebel list is often inside inner iframes)."""
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        yield fl
    for frame in _ordered_frames(page):
        yield frame


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


def _siebel_try_activate_payments_tab(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Open the **Payments** view before Payment Lines automation. Siebel labels vary
    (**Payments**, **Payment**, **Payment Details**, **Customer Payment**, etc.).
    """
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
    start_t = time.monotonic()
    deadline = start_t + max(0.2, wait_ms / 1000.0)
    poll_count = 0
    while time.monotonic() < deadline:
        poll_count += 1
        for root in _siebel_locator_search_roots(page, content_frame_selector):
            try:
                if bool(root.evaluate(_js, {"needle": needle, "raw": raw_digits})):
                    # region agent log
                    _agent_debug_log(
                        "H4",
                        "siebel_dms_playwright.py:_wait_for_mobile_search_hit_ready",
                        "mobile_hit_ready_success",
                        {
                            "wait_ms": int(wait_ms),
                            "elapsed_ms": int((time.monotonic() - start_t) * 1000),
                            "poll_count": int(poll_count),
                            "has_selector": bool(content_frame_selector),
                        },
                    )
                    # endregion
                    return True
            except Exception:
                continue
        _safe_page_wait(page, 120, log_label="wait_mobile_hit_ready")
    # region agent log
    _agent_debug_log(
        "H4",
        "siebel_dms_playwright.py:_wait_for_mobile_search_hit_ready",
        "mobile_hit_ready_timeout",
        {
            "wait_ms": int(wait_ms),
            "elapsed_ms": int((time.monotonic() - start_t) * 1000),
            "poll_count": int(poll_count),
            "has_selector": bool(content_frame_selector),
        },
    )
    # endregion
    return False


def _wait_for_contact_detail_ready(
    page: Page,
    *,
    content_frame_selector: str | None,
    wait_ms: int,
) -> bool:
    """Wait until right contact detail is rendered (First Name / Relation field visible)."""
    sels = (
        'input[aria-label="First Name"]',
        'textarea[aria-label="First Name"]',
        "input[name*='First_Name' i]",
        'input[aria-label="Relation\'s Name"]',
        'textarea[aria-label="Relation\'s Name"]',
    )
    start_t = time.monotonic()
    deadline = start_t + max(0.2, wait_ms / 1000.0)
    poll_count = 0
    while time.monotonic() < deadline:
        poll_count += 1
        for root in _siebel_locator_search_roots(page, content_frame_selector):
            for css in sels:
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=250):
                        # region agent log
                        _agent_debug_log(
                            "H5",
                            "siebel_dms_playwright.py:_wait_for_contact_detail_ready",
                            "contact_detail_ready_success",
                            {
                                "wait_ms": int(wait_ms),
                                "elapsed_ms": int((time.monotonic() - start_t) * 1000),
                                "poll_count": int(poll_count),
                                "matched_css": css,
                                "has_selector": bool(content_frame_selector),
                            },
                        )
                        # endregion
                        return True
                except Exception:
                    continue
        _safe_page_wait(page, 120, log_label="wait_contact_detail_ready")
    # region agent log
    _agent_debug_log(
        "H5",
        "siebel_dms_playwright.py:_wait_for_contact_detail_ready",
        "contact_detail_ready_timeout",
        {
            "wait_ms": int(wait_ms),
            "elapsed_ms": int((time.monotonic() - start_t) * 1000),
            "poll_count": int(poll_count),
            "has_selector": bool(content_frame_selector),
        },
    )
    # endregion
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

    for root in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if try_click_in_root(root):
                return True
        except Exception:
            continue
    return False


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
    Primary: jqGrid view ``div#gview_s_1001_l.ui-jqgrid-view`` → ``table#s_1001_l`` / ``.ui-jqgrid-btable``
    → ``a[name="Title"]`` (e.g. ``id="1_s_100_1_l_Title"``); then JS click inside ``#gview_s_1001_l`` per
    frame; then broader link/row fallbacks.
    Loads **Vehicle Information** / detail so model, color, and year can be scraped from inputs or rows.

    When **DMS only has a partial** (or scrape has not yet produced ``full_chassis``), the left pane may still
    show the **full VIN** on a single **Title** drilldown — substring match can fail. In that case we fall
    back to clicking the **only** visible VIN-like Title link under ``#gview_s_1001_l`` (one hit row).
    """
    vin_key = _vin_match_key(chassis)
    use_key = bool(vin_key) and len(vin_key) >= 4
    sub_pat = (
        re.compile(".*" + re.escape(vin_key) + ".*", re.I) if use_key else None
    )

    def _try_click_siebel_drilldown(loc) -> bool:
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

    def row_contains_vin(row_compact: str) -> bool:
        if not use_key or not row_compact:
            return False
        rk = row_compact.upper()
        vk = vin_key.upper()
        if vk in rk:
            return True
        return rk.endswith(vk) or rk.startswith(vk)

    row_selectors = (
        "table tbody tr",
        "table tr",
        '[role="row"]',
        "tr[role='row']",
    )

    _gview_title_chains = (
        '#gview_s_1001_l.ui-jqgrid-view table#s_1001_l a[name="Title"]',
        '#gview_s_1001_l table.ui-jqgrid-btable a[name="Title"]',
        '#gview_s_1001_l table#s_1001_l a[name="Title"]',
        '#gview_s_1001_l table[id="s_1001_l"] a[name="Title"]',
        'div#gview_s_1001_l.ui-jqgrid-view a[name="Title"]',
        '#gview_s_1001_l a[name="Title"][id*="_l_Title"]',
        '#gview_s_1001_l a[id*="_l_Title"]',
        '.ui-jqgrid-view#gview_s_1001_l a[name="Title"]',
    )

    def _try_gview_unique_single_title(scope) -> bool:
        """
        Exactly one visible **Title** link whose text looks like a full VIN (11–19 alnum) — safe when the
        list has a single search hit and we cannot match on partial ``frame_partial`` yet.
        """
        try:
            g = scope.locator("#gview_s_1001_l").first
            if g.count() == 0 or not g.is_visible(timeout=600):
                return False
            titles = g.locator('a[name="Title"], a[id*="_l_Title"]')
            n = titles.count()
            if n != 1:
                return False
            link = titles.first
            if not link.is_visible(timeout=800):
                return False
            t = re.sub(r"[^A-Za-z0-9]", "", link.inner_text(timeout=500) or "")
            if len(t) < 11 or len(t) > 19:
                return False
            try:
                link.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            return _try_click_siebel_drilldown(link)
        except Exception:
            return False

    def _try_gview_1001_title_links(scope) -> bool:
        """Click **Title** drilldown under jqGrid ``gview_s_1001_l`` (class ``ui-jqgrid-view``)."""
        if not use_key:
            return _try_gview_unique_single_title(scope)
        for title_chain in _gview_title_chains:
            try:
                titles = scope.locator(title_chain)
                tn = titles.count()
                for ti in range(min(tn, 40)):
                    link = titles.nth(ti)
                    try:
                        if not link.is_visible(timeout=900):
                            continue
                        t = link.inner_text(timeout=500) or ""
                        compact = re.sub(r"[^A-Za-z0-9]", "", t)
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
        if _try_gview_unique_single_title(scope):
            return True
        return False

    def _try_js_click_gview_s_1001_title(frame) -> bool:
        """DOM click inside ``#gview_s_1001_l`` (``ui-jqgrid-view``) when Playwright hit-testing fails."""
        try:
            hit = frame.evaluate(
                """({ vk, useKey }) => {
                  const norm = (s) => String(s || '').replace(/[^A-Za-z0-9]/g, '');
                  const key = norm(vk);
                  const g = document.getElementById('gview_s_1001_l');
                  if (!g) return false;
                  try { g.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 1 && r.height >= 1;
                  };
                  const nodes = Array.from(g.querySelectorAll('a[name="Title"], a[id*="_l_Title"]')).filter(vis);
                  if (useKey && key && key.length >= 4) {
                    for (const a of nodes) {
                      const t = norm(a.innerText || a.textContent || '');
                      if (!t) continue;
                      if (t.includes(key) || key.includes(t) || t.endsWith(key) || t.startsWith(key)) {
                        try { a.scrollIntoView({ block: 'center' }); } catch (e) {}
                        try { a.click(); return true; } catch (e) {}
                      }
                    }
                    return false;
                  }
                  const vinLike = (t) => {
                    const n = norm(t);
                    return n.length >= 11 && n.length <= 19;
                  };
                  const cand = nodes.filter((a) => vinLike(a.innerText || a.textContent || ''));
                  if (cand.length !== 1) return false;
                  try { cand[0].scrollIntoView({ block: 'center' }); } catch (e) {}
                  try { cand[0].click(); return true; } catch (e) { return false; }
                }""",
                {"vk": vin_key, "useKey": use_key},
            )
            return bool(hit)
        except Exception:
            return False

    def try_click_in_root(root) -> bool:
        if _try_gview_1001_title_links(root):
            return True

        scopes: list = []
        for title_re in (
            re.compile(r"Search\s+Results", re.I),
            re.compile(r"Siebel\s+Find", re.I),
        ):
            try:
                panel = root.locator(".siebui-applet").filter(has_text=title_re).first
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
            if _try_gview_1001_title_links(scope):
                return True

        for scope in scopes:

            # Title column / list: any visible anchor whose text contains the chassis key (VIN link).
            if use_key and sub_pat is not None:
                try:
                    alinks = scope.locator("a")
                    an = alinks.count()
                    for i in range(min(an, 45)):
                        link = alinks.nth(i)
                        try:
                            if not link.is_visible(timeout=350):
                                continue
                            t = link.inner_text(timeout=500) or ""
                            compact = re.sub(r"[^A-Za-z0-9]", "", t)
                            if (
                                len(compact) >= min(10, len(vin_key))
                                and vin_key.upper() in compact.upper()
                            ):
                                if _try_click_siebel_drilldown(link):
                                    return True
                        except Exception:
                            continue
                except Exception:
                    pass
                try:
                    loc = scope.get_by_role("link", name=sub_pat)
                    ln = loc.count()
                    for i in range(min(ln, 28)):
                        if _try_click_siebel_drilldown(loc.nth(i)):
                            return True
                except Exception:
                    pass
                for css in (
                    'a[href^="javascript"]',
                    'a[href*="void(0)"]',
                    "a[href*='javascript']",
                    "a.siebui-ctrl-drilldown",
                    "a",
                ):
                    try:
                        hits = scope.locator(css).filter(has_text=sub_pat)
                        hn = hits.count()
                        for i in range(min(hn, 35)):
                            if _try_click_siebel_drilldown(hits.nth(i)):
                                return True
                    except Exception:
                        continue

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
                    row_compact = re.sub(r"[^A-Za-z0-9]", "", row.inner_text(timeout=800) or "")
                except Exception:
                    continue
                if not row_contains_vin(row_compact):
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
                            if _try_click_siebel_drilldown(link):
                                return True
                    except Exception:
                        continue
                try:
                    row.click(timeout=timeout_ms)
                    return True
                except Exception:
                    continue
        return False

    for root in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if try_click_in_root(root):
                return True
        except Exception:
            continue
    for fr in list(_ordered_frames(page)) + [page.main_frame]:
        try:
            if _try_js_click_gview_s_1001_title(fr):
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


def _siebel_video_branch2_address_postal_and_save(
    page: Page,
    *,
    pin_code: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Video branch **(2)** (no Open enquiry): after Relation's Name path, open **Address**, set
    **Postal Code** ``#1_Postal_Code`` (scoped under ``#s_vctrl_div`` when present), then Save.
    """
    pin = (pin_code or "").strip()
    if not pin:
        note("Branch (2) Address: pin_code empty — skipping Address tab / Postal Code fill.")
        return False
    t = min(int(action_timeout_ms), 6000)
    if _siebel_try_click_named_in_frames(
        page,
        re.compile(r"^\s*Address\s*$", re.I),
        roles=("tab", "link", "button"),
        timeout_ms=t,
        content_frame_selector=content_frame_selector,
    ):
        note("Branch (2): clicked Address tab.")
        _safe_page_wait(page, 700, log_label="after_address_tab_branch2")
    else:
        note("Branch (2): Address tab not found via frame scan — trying Postal Code field anyway.")

    def _fill_postal_in_root(root) -> bool:
        try:
            vctrl = root.locator("#s_vctrl_div")
            if vctrl.count() > 0:
                loc = vctrl.locator('[id="1_Postal_Code"]').first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.click(timeout=min(2000, action_timeout_ms))
                    loc.fill(pin, timeout=action_timeout_ms)
                    try:
                        loc.press("Tab", timeout=1200)
                    except Exception:
                        pass
                    note(f"Branch (2): filled Postal Code in #s_vctrl_div / #1_Postal_Code → {pin!r}.")
                    return True
        except Exception:
            pass
        try:
            loc = root.locator('[id="1_Postal_Code"]').first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                loc.click(timeout=min(2000, action_timeout_ms))
                loc.fill(pin, timeout=action_timeout_ms)
                try:
                    loc.press("Tab", timeout=1200)
                except Exception:
                    pass
                note(f"Branch (2): filled #1_Postal_Code → {pin!r}.")
                return True
        except Exception:
            pass
        return False

    _filled = False
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        if _fill_postal_in_root(fl):
            _filled = True
            break
    if not _filled:
        for frame in _ordered_frames(page):
            if _fill_postal_in_root(frame):
                _filled = True
                break
    if not _filled and _fill_postal_in_root(page):
        _filled = True
    if not _filled:
        note("Branch (2): could not locate or fill #1_Postal_Code.")
        return False

    _safe_page_wait(page, 350, log_label="after_postal_fill_branch2")
    if _try_click_siebel_save(
        page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("Branch (2): Save clicked after Address / Postal Code.")
        return True
    try:
        page.keyboard.press("Control+S", delay=50)
        note("Branch (2): pressed Ctrl+S after Address / Postal Code.")
        return True
    except Exception:
        note("Branch (2): Save toolbar and Ctrl+S both failed after Postal Code.")
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
            page, content_frame_selector=content_frame_selector, wait_ms=1200
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
        skip_left_pane_click=True,
    )
    if not opened_customer:
        note("Could not click First Name in Contacts pane (video SOP).")
        return False

    if not _wait_for_contact_detail_ready(
        page, content_frame_selector=content_frame_selector, wait_ms=700
    ):
        _safe_page_wait(page, 120, log_label="after_first_name_click_before_relation_fill_fallback")

    if not care_val:
        note("No care_of from DB — skipping Relation's Name / Address Line 1 fill after First Name drilldown.")
        return True

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
        note("Relation's Name filled; stopping on current field as requested.")
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

        # #region agent log — relation name scan attempt
        import json as _j_rn, time as _t_rn
        try:
            with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                _lf.write(_j_rn.dumps({"sessionId":"08e634","hypothesisId":"RN1","location":"siebel_dms_playwright.py:relation_name_scan","message":"Relation Name scan attempt","data":{"attempt": _outer},"timestamp":int(_t_rn.time()*1000)}) + "\n")
        except Exception:
            pass
        # #endregion

        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            for css in exact_selectors:
                try:
                    loc = fl.locator(css).first
                    if _fill_with_retry(loc, care_val, attempts=3, visible_ms=900, _lbl=f"fl:{css[:40]}"):
                        return _after_relation_fill_nav()
                except Exception:
                    continue
        for frame in _ordered_frames(page):
            for css in exact_selectors:
                try:
                    loc = frame.locator(css).first
                    if _fill_with_retry(loc, care_val, attempts=3, visible_ms=900, _lbl=f"fr:{css[:40]}"):
                        return _after_relation_fill_nav()
                except Exception:
                    continue

        for fl in _iter_frame_locator_roots(page, content_frame_selector):
            try:
                loc = fl.get_by_label("Relation's Name", exact=True).first
                if _fill_with_retry(loc, care_val, attempts=3, visible_ms=700, _lbl="lbl_fl"):
                    return _after_relation_fill_nav()
            except Exception:
                continue
        for frame in _ordered_frames(page):
            try:
                loc = frame.get_by_label("Relation's Name", exact=True).first
                if _fill_with_retry(loc, care_val, attempts=3, visible_ms=700, _lbl="lbl_fr"):
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
    log_first_name_row_debug: bool = False,
) -> list[tuple[object, int, str, int]]:
    """
    Build ordered drilldown plans: each row that contains the mobile (10-digit / raw digit rules)
    and has a visible row link. **Duplicate-mobile detection:** we scan each search root (chained
    frame, scored iframes, main page) separately and keep the **single** root's plan list with the
    **most** hits—so the same physical grid mirrored in parent + iframe is not double-counted.

    ``len(returned)`` is therefore **the number of separate table rows** containing the mobile for
    looping / duplicate sweep (ordinal ``0 .. len-1``).
    """
    drill_needle = _mobile_needle_for_contact_grid_match(mobile)
    drill_raw = re.sub(r"\D", "", (mobile or "").strip())
    fn_ex = (first_name_exact or "").strip()
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
    row_match_js = """(el, args) => {
      const needle = String(args.needle || '');
      const raw = String(args.raw || '');
      const target = String(args.target || '').trim();
      const tr = el.closest('tr');
      if (!tr) return false;
      const tds = tr.querySelectorAll('td');
      if (tds.length < 3) return false;
      const compact = (s) => String(s || '').replace(/\\s+/g, '');
      const rowCompact = compact(tr.textContent || '');
      let mobileOk = false;
      if (needle && rowCompact.includes(needle)) mobileOk = true;
      else if (raw.length >= 8 && rowCompact.includes(raw)) mobileOk = true;
      if (!mobileOk) return false;
      if (!target) return true;
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
      const rowContainsFindFirstKey = (trel, keyBase) => {
        if (!keyBase) return false;
        const keyHead = keyBase.split(/\\s+/).filter(Boolean)[0] || '';
        const rraw = norm(trel.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        if (!rraw || (!rraw.includes(keyBase) && !(keyHead && rraw.includes(keyHead)))) return false;
        if (rraw.startsWith(keyBase + ' ')) return true;
        if (keyHead && rraw.startsWith(keyHead + ' ')) return true;
        const parts = rraw.split(/[\\s,;|\\/\\u2013\\u2014-]+/).filter(Boolean);
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
      if (!keyBase) return true;
      for (const td of tds) {
        if (textMatchesFindFirstName(td.textContent, keyBase)) return true;
        if (textMatchesFindFirstName(td.getAttribute('title') || '', keyBase)) return true;
        if (textMatchesFindFirstName(td.getAttribute('aria-label') || '', keyBase)) return true;
        for (const inp of td.querySelectorAll('input, textarea')) {
          if (textMatchesFindFirstName(inp.value, keyBase)) return true;
        }
      }
      return rowContainsFindFirstKey(tr, keyBase);
    }"""

    args = {"needle": drill_needle, "raw": drill_raw, "target": fn_ex}

    def _dbg_dr(message: str, data: dict, hid: str = "D1") -> None:
        if not log_first_name_row_debug and hid == "D3":
            return
        try:
            import json as _j_dr
            import time as _t_dr
            from pathlib import Path as _p_dr

            _log_path = _p_dr(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_log_path, "a", encoding="utf-8") as _lf_dr:
                _lf_dr.write(
                    _j_dr.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "post-fix",
                            "hypothesisId": hid,
                            "location": "siebel_dms_playwright.py:_contact_mobile_drilldown_plans",
                            "message": message,
                            "data": data,
                            "timestamp": int(_t_dr.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass

    best_plans: list[tuple[object, int, str, int]] = []
    for _dr_root in list(_siebel_locator_search_roots(page, content_frame_selector)) + list(
        _ordered_frames(page)
    ) + [page]:
        plans_here: list[tuple[object, int, str, int]] = []
        try:
            _rows = _dr_root.locator("table tr")
            _rn = _rows.count()
        except Exception:
            continue
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
                    if fn_ex:
                        try:
                            if not bool(_lnk.evaluate(row_match_js, args)):
                                _dbg_dr(
                                    "drilldown_first_name_not_visible_on_mobile_row",
                                    {"row_index": _ri, "needle": drill_needle, "first_name_len": len(fn_ex)},
                                    hid="D3",
                                )
                        except Exception:
                            _dbg_dr(
                                "drilldown_first_name_eval_failed_on_mobile_row",
                                {"row_index": _ri, "needle": drill_needle},
                                hid="D3",
                            )
                    _row_link_idx = _li
                    _row_link_sel = _link_sel
                    break
                if _row_link_idx is not None:
                    break
            if _row_link_sel is not None and _row_link_idx is not None:
                plans_here.append((_dr_root, _ri, _row_link_sel, _row_link_idx))
        if len(plans_here) > len(best_plans):
            best_plans = plans_here
    return best_plans


def _contact_find_mobile_drilldown_occurrence_count(
    page: Page,
    mobile: str,
    *,
    content_frame_selector: str | None = None,
    first_name_exact: str | None = None,
) -> int:
    """Return how many result rows contain ``mobile`` and are drillable (same rules as sweep ordinals)."""
    return len(
        _contact_mobile_drilldown_plans(
            page,
            mobile,
            content_frame_selector=content_frame_selector,
            first_name_exact=first_name_exact,
            log_first_name_row_debug=False,
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
) -> bool:
    """
    After Contact Find/Go, click the ``ordinal``-th (0-based) drilldown row that matches ``mobile``.
    We do **not** depend on the Title anchor text; we anchor to the row that contains the mobile digits
    and click a visible link inside that row.
    """
    if ordinal < 0:
        return False
    drill_needle = _mobile_needle_for_contact_grid_match(mobile)
    fn_ex = (first_name_exact or "").strip()
    # #region agent log
    def _dbg_dr_click(message: str, data: dict, hid: str = "D1") -> None:
        try:
            import json as _j_dr
            import time as _t_dr
            from pathlib import Path as _p_dr

            _log_path = _p_dr(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_log_path, "a", encoding="utf-8") as _lf_dr:
                _lf_dr.write(
                    _j_dr.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "post-fix",
                            "hypothesisId": hid,
                            "location": "siebel_dms_playwright.py:_click_nth_mobile_title_drilldown",
                            "message": message,
                            "data": data,
                            "timestamp": int(_t_dr.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass

    # #endregion
    plans = _contact_mobile_drilldown_plans(
        page,
        mobile,
        content_frame_selector=content_frame_selector,
        first_name_exact=first_name_exact,
        log_first_name_row_debug=True,
    )
    if not plans:
        _dbg_dr_click(
            "drilldown_no_plans",
            {"needle": drill_needle, "ordinal": ordinal, "has_first": bool(fn_ex)},
        )
    else:
        _dbg_dr_click(
            "drilldown_plans_built",
            {"plans_len": len(plans), "ordinal": ordinal, "needle": drill_needle},
            hid="D2",
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

    Returns ``(has_existing_enquiry, enquiry_number, row_count, error_message)``.
    ``error_message`` set → caller must stop. If no error and ``has_existing_enquiry`` is False, every
    matching Title was opened and all had zero enquiry rows — caller may create a new enquiry.
    """
    used_fallback_link = False
    ordinal = 0
    fn = (first_name or "").strip()
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
            )
            # #region agent log
            try:
                import json as _j_s3
                import time as _t_s3
                from pathlib import Path as _p_s3

                _lf3 = _p_s3(__file__).resolve().parents[3] / "debug-08e634.log"
                with open(_lf3, "a", encoding="utf-8") as _lf3f:
                    _lf3f.write(
                        _j_s3.dumps(
                            {
                                "sessionId": "08e634",
                                "runId": "post-fix",
                                "hypothesisId": "E3",
                                "location": "siebel_dms_playwright.py:_contact_find_title_sweep_for_enquiry",
                                "message": "duplicate_row_in_place_drill",
                                "data": {"ordinal": ordinal, "drilled": bool(drilled)},
                                "timestamp": int(_t_s3.time() * 1000),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            if not drilled:
                note(
                    f"In-place drill for duplicate row {ordinal + 1} failed — stopping sweep "
                    f"(no Contact Find re-run; list should remain visible in split view)."
                )
                # #region agent log
                try:
                    import json as _j_s3b
                    import time as _t_s3b
                    from pathlib import Path as _p_s3b

                    _lf3b = _p_s3b(__file__).resolve().parents[3] / "debug-08e634.log"
                    with open(_lf3b, "a", encoding="utf-8") as _lf3bf:
                        _lf3bf.write(
                            _j_s3b.dumps(
                                {
                                    "sessionId": "08e634",
                                    "runId": "post-fix",
                                    "hypothesisId": "E3b",
                                    "location": "siebel_dms_playwright.py:_contact_find_title_sweep_for_enquiry",
                                    "message": "duplicate_in_place_failed_break_no_second_drill",
                                    "data": {"ordinal": ordinal},
                                    "timestamp": int(_t_s3b.time() * 1000),
                                }
                            )
                            + "\n"
                        )
                except Exception:
                    pass
                # #endregion
                break

        if not drilled:
            drilled = _click_nth_mobile_title_drilldown(
                page,
                mobile,
                ordinal,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                first_name_exact=(fn if fn else None) if ordinal == 0 else None,
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
            _enq_checked, _enq_rows, _enq_number = _contact_enquiry_tab_has_rows(
                page,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
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
) -> tuple[bool, int, str]:
    """
    Open Contact_Enquiry tab and check whether Enquiry grid has data rows **on the opened contact**.

    Detection: header ``#jqgh_s_1_l_Enquiry_`` (or **Enquiry#** / **Enquiries** text), then non-empty
    values on ``input`` / ``textarea`` ``name=\"Enquiry_\"``; table cell scrape; Hero **Enquiry#** as
    visible ``<a>`` (e.g. ``11870-01-SENQ-0623-305``) inside **.siebui-applet** when the applet text
    references enquiries. When **Enquiry Status** fields exist (``id=1_HHML_Enquiry_Status`` or any
    element whose ``id`` ends with ``HHML_Enquiry_Status``), only rows with status **Open**
    (case-insensitive) count
    as existing enquiries. Frames: **main first**, then Siebel iframes.
    """
    _clicked = _siebel_try_click_named_in_frames(
        page,
        re.compile(r"Contact[_\s]*Enquiry", re.I),
        roles=("tab", "link", "button"),
        timeout_ms=min(action_timeout_ms, 3500),
        content_frame_selector=content_frame_selector,
    )
    if not _clicked:
        note("Contact_Enquiry tab not clickable (could not verify enquiry rows).")
        # #region agent log
        try:
            import json as _j_ec
            import time as _t_ec
            from pathlib import Path as _p_ec

            _lpc = _p_ec(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_lpc, "a", encoding="utf-8") as _lfc:
                _lfc.write(
                    _j_ec.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "enquiry-detect",
                            "hypothesisId": "H1",
                            "location": "siebel_dms_playwright.py:_contact_enquiry_tab_has_rows",
                            "message": "contact_enquiry_tab_click_failed",
                            "data": {},
                            "timestamp": int(_t_ec.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        return False, 0, ""

    _safe_page_wait(page, 900, log_label="after_contact_enquiry_tab")

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
          if (!snip.includes('enquiries') && !snip.includes('enquiry#') && !snip.includes('enquiry #')) continue;
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

      let headerFound = !!document.querySelector('#jqgh_s_1_l_Enquiry_');
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
              || t === 'enquiries'
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
            && !ttxt.includes('enquiries')
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

    # #region agent log
    def _enq_log(message: str, data: dict) -> None:
        try:
            import json as _j_e
            import time as _t_e
            from pathlib import Path as _p_e

            _lp = _p_e(__file__).resolve().parents[3] / "debug-08e634.log"
            with open(_lp, "a", encoding="utf-8") as _lf_e:
                _lf_e.write(
                    _j_e.dumps(
                        {
                            "sessionId": "08e634",
                            "runId": "post-fix",
                            "hypothesisId": "E1",
                            "location": "siebel_dms_playwright.py:_contact_enquiry_tab_has_rows",
                            "message": message,
                            "data": data,
                            "timestamp": int(_t_e.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass

    # #endregion

    _best_cnt = 0
    _best_no = ""
    _any_checked = False
    _main = page.main_frame
    for _r in _frames_for_enquiry_subgrid_eval(page):
        try:
            _res = _r.evaluate(_js)
            if not _res:
                continue
            try:
                _u = str(getattr(_r, "url", "") or "")[:120]
            except Exception:
                _u = ""
            _diag = _res.get("diag") or {}
            _enq_log(
                "enquiry_frame_eval",
                {
                    "frame_url": _u,
                    "is_main": _r == _main,
                    "checked": bool(_res.get("checked")),
                    "rowCount": int(_res.get("rowCount") or 0),
                    "has_number": bool(str(_res.get("enquiryNumber") or "").strip()),
                    "diag": _diag,
                },
            )
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
        '[aria-describedby*="HHML_Transaction_No"], th[id*="HHML_Transaction_No"], [id$="_l_HHML_Transaction_No"]'
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
        'table.ui-jqgrid-btable, div.ui-jqgrid-bdiv table, table.siebui-list, table.siebui-list table'
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
      return false;
    }"""
    return bool(_siebel_root_evaluate(root, js))


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


def _gather_payment_line_toolbar_roots(page: Page, content_frame_selector: str | None) -> list:
    """Frames / locators where **Payment Lines** lives: **List:New** toolbar, HHML grid, or iframe title."""
    out: list = []
    seen: set[int] = set()

    def _add(r) -> None:
        k = id(r)
        if k in seen:
            return
        seen.add(k)
        out.append(r)

    for root in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if _siebel_root_has_payment_lines_toolbar(root):
                _add(root)
        except Exception:
            continue
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
    return out


def _add_customer_payment(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    Open **Payments**, locate the frame(s) where **Payment Lines List:New** (``+``) lives, then **in that
    document** check the grid for a row with a populated **Transaction #**. If present, skip add.
    Otherwise click ``+``, fill Type / Mode / Amount (**120000**), and Save.
    """
    _safe_page_wait(page, 250, log_label="before_payments_plus_click")
    try:
        note(f"Payment debug: ordered frames count={len(_ordered_frames(page))}.")
    except Exception:
        pass

    if _siebel_try_activate_payments_tab(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        _safe_page_wait(page, 1200, log_label="after_payments_tab_activate")
    else:
        note("Payments tab: no matching tab/link found (will still look for Payment Lines toolbar).")

    payment_toolbar_roots = _gather_payment_line_toolbar_roots(page, content_frame_selector)
    if not payment_toolbar_roots and _siebel_try_activate_payments_tab(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    ):
        _safe_page_wait(page, 1200, log_label="after_payments_tab_retry_toolbar")
        payment_toolbar_roots = _gather_payment_line_toolbar_roots(page, content_frame_selector)

    payment_toolbar_roots.sort(key=_payment_line_toolbar_roots_priority)
    # region agent log
    _agent_debug_log(
        "H1",
        "siebel_dms_playwright.py:_add_customer_payment",
        "payment_toolbar_roots_built",
        {
            "roots_count": int(len(payment_toolbar_roots)),
            "has_selector": bool(content_frame_selector),
            "action_timeout_ms": int(action_timeout_ms),
        },
    )
    # endregion

    if not payment_toolbar_roots:
        # region agent log
        _agent_debug_log(
            "H1",
            "siebel_dms_playwright.py:_add_customer_payment",
            "payment_toolbar_roots_missing",
            {"roots_count": 0, "has_selector": bool(content_frame_selector)},
        )
        # endregion
        note(
            "Payment debug: Payment Lines toolbar (List:New / Save) not found — "
            "cannot locate '+' frame; ensure the Payments view shows Payment Lines."
        )
        return False

    for idx, pr in enumerate(payment_toolbar_roots):
        try:
            if _payment_lines_list_has_populated_transaction_number(pr):
                # region agent log
                _agent_debug_log(
                    "H2",
                    "siebel_dms_playwright.py:_add_customer_payment",
                    "payment_skipped_existing_transaction",
                    {"root_index": int(idx)},
                )
                # endregion
                note(
                    "Payments: Payment Lines list already has a row with populated Transaction# — "
                    "skipping '+' and new-line entry."
                )
                return True
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
    note(
        "Payment debug: '+' frame(s) for Payment Lines "
        f"(payment_toolbar_roots={len(root_candidates)})."
    )

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
                    # region agent log
                    _agent_debug_log(
                        "H3",
                        "siebel_dms_playwright.py:_add_customer_payment",
                        "payment_frame_locked_strict",
                        {"frames_count": int(len(payment_frames))},
                    )
                    # endregion
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
                        # region agent log
                        _agent_debug_log(
                            "H3",
                            "siebel_dms_playwright.py:_add_customer_payment",
                            "payment_frame_locked_relaxed",
                            {"frames_count": int(len(payment_frames))},
                        )
                        # endregion
                        try:
                            note(f"Payment lines relaxed frame lock: url={(payment_frames[0].url or '')[:180]!r}, name={payment_frames[0].name!r}")
                        except Exception:
                            pass
                    else:
                        # region agent log
                        _agent_debug_log(
                            "H3",
                            "siebel_dms_playwright.py:_add_customer_payment",
                            "payment_frame_lock_failed",
                            {"clicked_plus": True},
                        )
                        # endregion
                        note("Payment lines scoped frame not detected; stopping to avoid focus drift.")
                        return False
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
                note(f"Payment debug: transaction roots count={len(scoped_roots)}.")

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
                note(f"Payment debug: amount roots count={len(amount_roots)}.")

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
                                page.keyboard.type("120000")
                                _safe_page_wait(page, 120, log_label="tab_nav_amount_fill")
                                page.keyboard.press("Tab")
                                _tab_filled = True
                                note(
                                    f"Payment direct: Transaction_Amount filled with 120000 via Tab navigation "
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
                    f"Type=Receipt(ok={type_ok!r}), Mode=Cash(ok={mode_ok!r}), Amount=120000(ok={amount_ok!r})."
                )
                _safe_page_wait(page, 400, log_label="after_amount_before_save")

                # Re-detect Payment Lines toolbar roots after row creation (same ``+`` frame context).
                save_action_roots = _gather_payment_line_toolbar_roots(page, content_frame_selector)
                note(f"Payment debug: save action roots count={len(save_action_roots)}.")

                # Save icon (down-arrow / save) click.
                save_clicked = False
                for sroot in save_action_roots:
                    for css in (
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
                    ):
                        try:
                            btn = sroot.locator(css).first
                            if btn.count() > 0 and btn.is_visible(timeout=500):
                                try:
                                    btn.click(timeout=action_timeout_ms)
                                except Exception:
                                    btn.click(timeout=action_timeout_ms, force=True)
                                save_clicked = True
                                note(f"Payment debug: Save clicked via selector: {css}")
                                break
                        except Exception:
                            continue
                    if save_clicked:
                        break
                if save_clicked:
                    _safe_page_wait(page, 3000, log_label="after_payment_save_processing")
                    # Check for Siebel error popup / alert dialog after save.
                    _err_msg = None
                    for _chk_root in list(_siebel_locator_search_roots(page, content_frame_selector)) + list(_ordered_frames(page)):
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
                                  // Siebel inline error / modal popup
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
                    if _err_msg:
                        note(f"Payment save: Siebel error popup detected → {_err_msg!r:.300}")
                    else:
                        note("Clicked Save icon after payment entry — no error popup detected.")
                    # Strict gate: after save, a row must show a populated Transaction# in the ``+`` frame.
                    _verify_txn = False
                    for _vr in _gather_payment_line_toolbar_roots(page, content_frame_selector):
                        try:
                            if _payment_lines_list_has_populated_transaction_number(_vr):
                                _verify_txn = True
                                break
                        except Exception:
                            continue
                    if _verify_txn:
                        note("Payments: verified Payment Lines row with populated Transaction# after save.")
                        return True
                    note("Payments: save clicked but no Payment Lines row with Transaction# detected after save.")
                    return False
                note("Could not click Save icon after filling payment fields.")
                return False
        except Exception as e:
            note(f"Add customer payment flow failed after '+' click attempt: {e}")
            return False
    note("Could not click '+' icon on Payments tab (Payment Lines List:New not visible).")
    return False


def _siebel_all_search_roots(page: Page, content_frame_selector: str | None) -> list:
    """Deduplicated list of content roots + all frames + ``page`` for applet chrome and popups."""
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
    seen: set[int] = set()
    out: list = []
    for x in r:
        k = id(x)
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _siebel_scrape_text_by_id_anywhere(
    page: Page, element_id: str, *, content_frame_selector: str | None
) -> str:
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            val = root.evaluate(f"""() => {{
                const el = document.getElementById("{element_id}");
                if (!el) return '';
                return (el.value || el.textContent || el.innerText || '').trim();
            }}""")
            if val:
                return str(val).strip()
        except Exception:
            continue
    return ""


def _siebel_click_by_id_anywhere(
    page: Page,
    element_id: str,
    *,
    timeout_ms: int,
    content_frame_selector: str | None,
    note,
    label: str,
    log_prefix: str,
    wait_ms: int = 1000,
) -> bool:
    tmo = min(int(timeout_ms or 3000), 4000)
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            loc = root.locator(f"#{element_id}").first
            if loc.count() > 0 and loc.is_visible(timeout=700):
                try:
                    loc.click(timeout=tmo)
                except Exception:
                    loc.click(timeout=tmo, force=True)
                note(f"{log_prefix}: clicked {label} (id={element_id!r}).")
                _safe_page_wait(page, wait_ms, log_label=f"after_{label.replace(' ', '_').lower()}")
                return True
        except Exception:
            continue
    for root in _siebel_all_search_roots(page, content_frame_selector):
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
                note(f"{log_prefix}: JS clicked {label} (id={element_id!r}).")
                _safe_page_wait(page, wait_ms, log_label=f"after_{label.replace(' ', '_').lower()}_js")
                return True
        except Exception:
            continue
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


def _siebel_run_vehicle_serial_detail_precheck_pdi(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
    log_prefix: str = "vehicle_serial_detail",
    scraped: dict | None = None,
) -> tuple[bool, str | None]:
    """
    Pre-check + PDI applets on the **vehicle serial** detail view (after ``Serial Number`` drilldown).

    Shared by ``prepare_vehicle`` and ``_attach_vehicle_to_bkg``. Third Level View Bar tabs are
    clicked by label (with hyphen-insensitive match for **Pre-check** vs **PreCheck**). Tab ``ui-id-*``
    values are dynamic across runs/tenants, so fixed tab ids are not treated as primary selectors.
    Applet controls: Pre-check **Technician** pick icon ``s_3_2_25_0_icon`` (tenant-current), legacy
    ``s_3_1_12_0_Ctrl``; PDI pick ``s_2_2_32_0_icon``.
    """
    _tmo = min(int(action_timeout_ms or 3000), 4000)

    def _roots():
        return _siebel_all_search_roots(page, content_frame_selector)

    def _click_third_level_view_bar_tab(tab_text: str, *, wait_ms: int) -> bool:
        """
        Prefer clicking tabs from the explicit "Third Level View Bar" container because
        this tenant sometimes renders duplicate tab labels elsewhere in the DOM.
        """
        # region agent log
        _dbg_log = Path(__file__).resolve().parents[3] / "debug-0875fe.log"

        def _dbg_ndj(*, hyp: str, loc: str, msg: str, data: dict) -> None:
            try:
                import json as _json_dbg

                with open(_dbg_log, "a", encoding="utf-8") as _lf:
                    _lf.write(
                        _json_dbg.dumps(
                            {
                                "sessionId": "0875fe",
                                "runId": "pre-fix",
                                "hypothesisId": hyp,
                                "location": loc,
                                "message": msg,
                                "data": data,
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass

        # endregion agent log

        tab_norm = (tab_text or "").strip().lower()
        if not tab_norm:
            return False

        # region agent log
        _raw_roots = _roots()
        _seen_r: set[int] = set()
        _rv_roots = []
        for _pref in (page, page.main_frame):
            if hasattr(_pref, "evaluate") and id(_pref) not in _seen_r:
                _seen_r.add(id(_pref))
                _rv_roots.append(_pref)
        for _r in _raw_roots:
            if type(_r).__name__ == "FrameLocator" or id(_r) in _seen_r:
                continue
            if hasattr(_r, "evaluate"):
                _seen_r.add(id(_r))
                _rv_roots.append(_r)

        _root_summ = []
        for _i_r, _r in enumerate(_rv_roots[:16]):
            _entry = {"i": _i_r, "type": type(_r).__name__}
            try:
                _u = getattr(_r, "url", None)
                if callable(_u):
                    _u = _u()
                if isinstance(_u, str) and _u:
                    _entry["url_tail"] = _u[-80:]
            except Exception:
                pass
            _root_summ.append(_entry)
        _dbg_ndj(
            hyp="A",
            loc="siebel_dms_playwright.py:_click_third_level_view_bar_tab",
            msg="third_level_tab_roots_order",
            data={
                "tab": tab_text,
                "roots_len": len(_rv_roots),
                "roots_head": _root_summ,
                "frame_locators_skipped": sum(
                    1 for _x in _raw_roots if type(_x).__name__ == "FrameLocator"
                ),
            },
        )
        # endregion agent log

        for _idx, root in enumerate(_rv_roots):
            try:
                _res = root.evaluate(
                    """(tabNeedle) => {
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
                            const a = compact(txt);
                            const b = compact(needle);
                            return a === b || a.includes(b) || b.includes(a);
                        };
                        // PreCheck / PDI live under Siebel **view control** `#s_vctrl_div` (operator-confirmed).
                        // "Third Level View Bar" hover/tooltip often refers to this strip; aria-scoped nodes can miss controls.
                        const containers = [];
                        const seenC = new Set();
                        const addC = (el, src) => {
                            if (!el || !vis(el) || seenC.has(el)) return;
                            seenC.add(el);
                            containers.push({ el: el, src: src });
                        };
                        addC(document.getElementById('s_vctrl_div'), 's_vctrl_div');
                        for (const bar of document.querySelectorAll(
                            "[aria-label*='Third Level View Bar' i], [title*='Third Level View Bar' i], [id*='ThirdLevelViewBar' i]"
                        )) {
                            addC(bar, 'third_level_view_aria');
                        }
                        const allVisibleTabLabels = [];
                        for (const c of containers) {
                            const bar = c.el;
                            const tabs = Array.from(
                                bar.querySelectorAll("a, button, [role='tab']")
                            );
                            for (const t of tabs) {
                                if (!vis(t)) continue;
                                const raw = (t.innerText || t.textContent || t.getAttribute('aria-label') || t.getAttribute('title') || '');
                                const txt = norm(raw);
                                if (allVisibleTabLabels.length < 80) {
                                    allVisibleTabLabels.push(String(raw).trim().slice(0, 48));
                                }
                                if (matches(txt, tabNeedle)) {
                                    let target = t;
                                    const tTag = (t.tagName || '').toUpperCase();
                                    if (tTag === 'LI') {
                                        // Never click LI wrapper directly; resolve to actionable tab control.
                                        const li = t;
                                        const inner = li.querySelector("a, button, [role='tab']");
                                        if (inner && inner !== li) {
                                            target = inner;
                                        } else {
                                            const sib = li.nextElementSibling;
                                            const sibTag = (sib && sib.tagName) ? String(sib.tagName).toUpperCase() : '';
                                            const sibIsAction = !!(sib && (sibTag === 'A' || sibTag === 'BUTTON' || String(sib.getAttribute('role') || '').toLowerCase() === 'tab'));
                                            if (sibIsAction && vis(sib)) {
                                                target = sib;
                                            } else {
                                                const ctrl = li.getAttribute('aria-controls') || '';
                                                const linked = ctrl ? bar.querySelector(`[id="${ctrl}"], a[aria-controls="${ctrl}"], [href="#${ctrl}"]`) : null;
                                                if (linked && linked !== li && vis(linked)) {
                                                    target = linked;
                                                } else {
                                                    // No actionable element found; skip this match candidate.
                                                    continue;
                                                }
                                            }
                                        }
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
                                    return {
                                        ok: true,
                                        containerCount: containers.length,
                                        containerSrc: c.src,
                                        visibleTabLabels: allVisibleTabLabels,
                                        matchEq: txt === tabNeedle,
                                        labelLen: String(raw).length,
                                        matchedHead: String(raw).slice(0, 24),
                                        matchedTag: t.tagName || '',
                                        matchedId: String(t.id || '').slice(0, 48),
                                        clickedTag: target.tagName || '',
                                        clickId: String(target.id || '').slice(0, 48),
                                    };
                                }
                            }
                        }
                        return {
                            ok: false,
                            containerCount: containers.length,
                            visibleTabLabels: allVisibleTabLabels,
                            matchEq: false,
                            labelLen: 0,
                        };
                    }""",
                    tab_norm,
                )
                # region agent log
                _dbg_ndj(
                    hyp="B",
                    loc="siebel_dms_playwright.py:_click_third_level_view_bar_tab",
                    msg="third_level_tab_root_scan",
                    data={
                        "tab": tab_text,
                        "root_index": _idx,
                        "root_type": type(root).__name__,
                        "eval": _res if isinstance(_res, dict) else {"raw": str(_res)[:120]},
                    },
                )
                # endregion agent log
                if isinstance(_res, dict) and _res.get("ok"):
                    # region agent log
                    _dbg_ndj(
                        hyp="B",
                        loc="siebel_dms_playwright.py:_click_third_level_view_bar_tab",
                        msg="third_level_tab_click_success",
                        data={
                            "tab": tab_text,
                            "root_index": _idx,
                            "root_type": type(root).__name__,
                            "eval": _res,
                            "verification_pass": "post-fix",
                        },
                    )
                    # endregion agent log
                    note(
                        f"{log_prefix}: clicked {tab_text} from tab strip "
                        f"(container={(_res.get('containerSrc') or '')!r})."
                    )
                    _safe_page_wait(page, wait_ms, log_label=f"after_third_level_{tab_norm}_tab")
                    return True
            except Exception as _ex_tab:
                # region agent log
                _dbg_ndj(
                    hyp="C",
                    loc="siebel_dms_playwright.py:_click_third_level_view_bar_tab",
                    msg="third_level_tab_root_exception",
                    data={
                        "tab": tab_text,
                        "root_index": _idx,
                        "root_type": type(root).__name__,
                        "err": str(_ex_tab)[:200],
                    },
                )
                # endregion agent log
                continue
        # region agent log
        _dbg_ndj(
            hyp="D",
            loc="siebel_dms_playwright.py:_click_third_level_view_bar_tab",
            msg="third_level_tab_all_roots_failed",
            data={"tab": tab_text, "roots_len": len(_rv_roots)},
        )
        # endregion agent log
        return False

    # region agent log
    try:
        import json as _json_ae

        _ae_main = page.main_frame.evaluate(
            """() => {
                const e = document.activeElement;
                if (!e) return {};
                return {
                    tag: e.tagName,
                    id: (e.id || '').slice(0, 48),
                    name: (e.name || '').slice(0, 32),
                };
            }"""
        )
        with open(
            Path(__file__).resolve().parents[3] / "debug-0875fe.log",
            "a",
            encoding="utf-8",
        ) as _lf_ae:
            _lf_ae.write(
                _json_ae.dumps(
                    {
                        "sessionId": "0875fe",
                        "runId": "pre-fix",
                        "hypothesisId": "E",
                        "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                        "message": "active_element_main_before_third_level_tabs",
                        "data": {
                            "ae_main": _ae_main,
                            "frames_n": len(page.frames),
                            "feature_scrape_ran": False,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # endregion agent log

    # region agent log — discover real ui-id / labels for Pre-check & PDI tabs (hypothesis F)
    try:
        import json as _json_inv

        _dbg_inv_path = Path(__file__).resolve().parents[3] / "debug-0875fe.log"
        _inv_js = """() => {
            const vis = (el) => {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            };
            const bars = Array.from(document.querySelectorAll(
                "[aria-label*='Third Level View Bar' i], [title*='Third Level View Bar' i], [id*='ThirdLevelViewBar' i]"
            )).filter(vis);
            const tabSamples = [];
            for (const bar of bars) {
                const nodes = Array.from(bar.querySelectorAll("a, button, [role='tab'], li"));
                for (const t of nodes) {
                    if (!vis(t)) continue;
                    const raw = (t.innerText || t.textContent || '').trim();
                    const id = (t.id || '').trim();
                    const al = (t.getAttribute('aria-label') || '').trim().slice(0, 80);
                    const ttl = (t.getAttribute('title') || '').trim().slice(0, 80);
                    const textHead = raw.slice(0, 48);
                    if (!id && !textHead && !al && !ttl) continue;
                    tabSamples.push({
                        tag: t.tagName,
                        id: id.slice(0, 80),
                        textHead: textHead,
                        ariaHead: al,
                        titleHead: ttl,
                    });
                    if (tabSamples.length >= 36) {
                        return { barCount: bars.length, tabSamples: tabSamples };
                    }
                }
            }
            return { barCount: bars.length, tabSamples: tabSamples };
        }"""
        _scan_frames = [page.main_frame] + [
            f for f in _ordered_frames(page) if f != page.main_frame
        ][:14]
        _best_inv = None
        _best_score = -1
        _best_idx = -1
        _best_url = ""
        for _fi, _fr in enumerate(_scan_frames):
            try:
                _one = _fr.evaluate(_inv_js)
            except Exception as _e_one:
                with open(_dbg_inv_path, "a", encoding="utf-8") as _lf:
                    _lf.write(
                        _json_inv.dumps(
                            {
                                "sessionId": "0875fe",
                                "runId": "pre-fix",
                                "hypothesisId": "F",
                                "location": "siebel_dms_playwright.py:_third_level_bar_inventory",
                                "message": "inventory_frame_error",
                                "data": {"frame_index": _fi, "err": str(_e_one)[:200]},
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                continue
            if not isinstance(_one, dict):
                continue
            _bc = int(_one.get("barCount") or 0)
            _ts = _one.get("tabSamples") or []
            _sc = _bc * 100 + len(_ts)
            if _sc > _best_score:
                _best_score = _sc
                _best_inv = _one
                _best_idx = _fi
                try:
                    _best_url = ((_fr.url or "")[-90:]) if _fr else ""
                except Exception:
                    _best_url = ""
        _s_vctrl_main = None
        try:
            _s_vctrl_main = bool(
                page.main_frame.evaluate(
                    "() => !!document.getElementById('s_vctrl_div')"
                )
            )
        except Exception:
            pass
        with open(_dbg_inv_path, "a", encoding="utf-8") as _lf:
            _lf.write(
                _json_inv.dumps(
                    {
                        "sessionId": "0875fe",
                        "runId": "pre-fix",
                        "hypothesisId": "F",
                        "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                        "message": "third_level_bar_tab_inventory",
                        "data": {
                            "best_frame_index": _best_idx,
                            "frame_url_tail": _best_url,
                            "s_vctrl_div_in_main_frame": _s_vctrl_main,
                            "fallback_precheck_id_in_code": "ui-id-1115",
                            "inventory": _best_inv,
                            "note": "Tabs: prefer #s_vctrl_div; see LLD 6.56.",
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # endregion agent log

    if callable(form_trace):
        form_trace(
            "vehicle_serial_precheck_pdi",
            "Vehicle serial detail",
            "precheck_tab_open",
            log_prefix=log_prefix,
        )

    _precheck_tab_ok = _click_third_level_view_bar_tab("Pre-check", wait_ms=1500)
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
                let maxRows = 0;
                const tables = Array.from(document.querySelectorAll('table')).filter(vis);
                for (const tb of tables) {
                    const rows = Array.from(tb.querySelectorAll('tbody tr, tr')).filter((tr) => {
                        if (!vis(tr)) return false;
                        const cls = String(tr.className || '').toLowerCase();
                        if (cls.includes('jqgfirstrow') || cls.includes('header')) return false;
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
                if bool(_probe.get("hasPrecheckRowId")) and not _precheck_existing_signal:
                    _precheck_existing_signal = f"root[{_ri}]:url_has_precheck_rowid"
        except Exception:
            continue
    # region agent log
    try:
        import json as _json_pc_existing

        with open(
            Path(__file__).resolve().parents[3] / "debug-0875fe.log",
            "a",
            encoding="utf-8",
        ) as _lf_pc_existing:
            _lf_pc_existing.write(
                _json_pc_existing.dumps(
                    {
                        "sessionId": "0875fe",
                        "runId": "pre-fix",
                        "hypothesisId": "G5",
                        "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                        "message": "precheck_existing_probe",
                        "data": {
                            "existing_rows": _precheck_existing_rows,
                            "signal": _precheck_existing_signal,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # endregion agent log

    _precheck_already_present = _precheck_existing_rows > 0 or "url_has_precheck_rowid" in _precheck_existing_signal
    if _precheck_already_present:
        note(
            f"{log_prefix}: Pre-check already has row(s) "
            f"(rows={_precheck_existing_rows}, signal={_precheck_existing_signal or 'n/a'}) — "
            "skipping Pre-check entry and continuing to PDI."
        )

    def _click_precheck_pick_icon(stage_label: str) -> tuple[bool, str]:
        _used = ""
        _ok = False
        for _pc_pick_id in ("s_3_2_25_0_icon", "s_3_1_12_0_Ctrl"):
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
                break
        # region agent log
        try:
            import json as _json_pc_icon_stage

            with open(
                Path(__file__).resolve().parents[3] / "debug-0875fe.log",
                "a",
                encoding="utf-8",
            ) as _lf_pc_icon_stage:
                _lf_pc_icon_stage.write(
                    _json_pc_icon_stage.dumps(
                        {
                            "sessionId": "0875fe",
                            "runId": "pre-fix",
                            "hypothesisId": "G2",
                            "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                            "message": "precheck_pick_icon_click_by_stage",
                            "data": {
                                "stage": stage_label,
                                "ok": _ok,
                                "used_id": _used,
                                "tried_ids": ["s_3_2_25_0_icon", "s_3_1_12_0_Ctrl"],
                            },
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # endregion agent log
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
                    note(f"{log_prefix}: picked first row in pick applet ({stage_label}).")
                    _safe_page_wait(page, 600, log_label=f"after_{stage_label}_row_pick")
                    break
            except Exception:
                continue

        _ok_done_local = False
        for root in _roots():
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
                        _ok_done_local = True
                        note(f"{log_prefix}: clicked OK on pick applet ({stage_label}).")
                        _safe_page_wait(page, 1000, log_label=f"after_{stage_label}_ok")
                        break
                except Exception:
                    continue
            if _ok_done_local:
                break
        if not _ok_done_local:
            note(f"{log_prefix}: OK button not found on pick applet ({stage_label}; best-effort).")
        # region agent log
        try:
            import json as _json_pick_out

            with open(
                Path(__file__).resolve().parents[3] / "debug-0875fe.log",
                "a",
                encoding="utf-8",
            ) as _lf_pick_out:
                _lf_pick_out.write(
                    _json_pick_out.dumps(
                        {
                            "sessionId": "0875fe",
                            "runId": "pre-fix",
                            "hypothesisId": "G4",
                            "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                            "message": "precheck_pick_applet_completion",
                            "data": {
                                "stage": stage_label,
                                "ok_clicked": _ok_done_local,
                                "had_search_or_enter_fallback": _pick_ok,
                            },
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # endregion agent log
        return _ok_done_local

    _precheck_icon_ok = True
    _precheck_icon_used = ""
    if not _precheck_already_present:
        _precheck_icon_ok, _precheck_icon_used = _click_precheck_pick_icon("precheck_open_status")
    # region agent log
    try:
        import json as _json_pc_icon

        with open(
            Path(__file__).resolve().parents[3] / "debug-0875fe.log",
            "a",
            encoding="utf-8",
        ) as _lf_pc_icon:
            _lf_pc_icon.write(
                _json_pc_icon.dumps(
                    {
                        "sessionId": "0875fe",
                        "runId": "pre-fix",
                        "hypothesisId": "G",
                        "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                        "message": "precheck_technician_icon_click",
                        "data": {
                            "ok": _precheck_icon_ok,
                            "used_id": _precheck_icon_used,
                            "tried_ids": ["s_3_2_25_0_icon", "s_3_1_12_0_Ctrl"],
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # endregion agent log
    if not _precheck_icon_ok:
        return False, (
            "Could not click Pre-check pick icon for Open status "
            "(tried s_3_2_25_0_icon, s_3_1_12_0_Ctrl)."
        )

    if not _precheck_already_present:
        _open_pick_complete = _pick_first_row_and_ok("precheck_open_status")
        if not _open_pick_complete:
            return (
                False,
                "Pre-check: Open status pick applet did not complete (row/OK not confirmed). "
                "Technician step was not run; Pre-check Submit and PDI were skipped.",
            )

        # Move focus to the next editable cell (Technician) and open its pick applet.
        _tech_icon_ok = False
        _tech_icon_used = ""
        try:
            page.keyboard.press("Tab")
            _safe_page_wait(page, 250, log_label="after_precheck_open_tab_to_technician")
            # region agent log
            try:
                import json as _json_pc_tab

                with open(
                    Path(__file__).resolve().parents[3] / "debug-0875fe.log",
                    "a",
                    encoding="utf-8",
                ) as _lf_pc_tab:
                    _lf_pc_tab.write(
                        _json_pc_tab.dumps(
                            {
                                "sessionId": "0875fe",
                                "runId": "pre-fix",
                                "hypothesisId": "G3",
                                "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                                "message": "precheck_tab_to_technician_cell",
                                "data": {"tab_pressed": True},
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # endregion agent log
        except Exception:
            pass

        _tech_icon_ok, _tech_icon_used = _click_precheck_pick_icon("precheck_technician_after_tab")
        if not _tech_icon_ok:
            return (
                False,
                "Pre-check: Technician pick icon not found after moving focus from Open (Tab). "
                "Pick applet for Technician did not open; Pre-check Submit and PDI were skipped.",
            )
        note(f"{log_prefix}: technician pick icon clicked after Tab (id={_tech_icon_used!r}).")
        _tech_pick_complete = _pick_first_row_and_ok("precheck_technician_after_tab")
        if not _tech_pick_complete:
            return (
                False,
                "Pre-check: Technician pick applet did not complete (row/OK not confirmed). "
                "Pre-check Submit and PDI were skipped.",
            )

        _submit_done = False
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
                        _submit_done = True
                        note(f"{log_prefix}: clicked Submit (Pre-check).")
                        _safe_page_wait(page, 1500, log_label="after_precheck_submit")
                        break
                except Exception:
                    continue
            if _submit_done:
                break
        if not _submit_done:
            return False, "Could not click Submit button on Pre-check."

        _submit_err = _detect_siebel_error_popup(page, content_frame_selector)
        if _submit_err:
            note(f"{log_prefix}: Siebel error after Pre-check Submit → {_submit_err!r:.300}")
            return False, f"Siebel error after Pre-check Submit: {_submit_err[:200]}"
        note(f"{log_prefix}: Pre-check completed.")

    _pdi_tab_clicked = _click_third_level_view_bar_tab("PDI", wait_ms=1500)
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
    _safe_page_wait(page, 1500, log_label="after_pdi_tab")
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

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
        const tables = Array.from(document.querySelectorAll('table')).filter(vis);
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

    try:
        import json as _json_pdi_probe

        with open(
            Path(__file__).resolve().parents[3] / "debug-0875fe.log",
            "a",
            encoding="utf-8",
        ) as _lf_pdi_probe:
            _lf_pdi_probe.write(
                _json_pdi_probe.dumps(
                    {
                        "sessionId": "0875fe",
                        "runId": "pre-fix",
                        "hypothesisId": "G6",
                        "location": "siebel_dms_playwright.py:_siebel_run_vehicle_serial_detail_precheck_pdi",
                        "message": "pdi_existing_probe",
                        "data": {
                            "row_count": _pdi_row_count,
                            "header_matched": _pdi_header_matched,
                            "expiry_samples": _pdi_expiry_raw[:8],
                            "parsed_max_expiry": _pdi_max_expiry.isoformat() if _pdi_max_expiry else "",
                            "today": _today.isoformat(),
                            "need_new_row": _pdi_need_new_row,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass

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
        _sr_new_clicked = False
        _sr_selectors = [
            "[aria-label='Service Request List:New']",
            "a[aria-label='Service Request List:New']",
            "button[aria-label='Service Request List:New']",
            "[aria-label*='Service Request List' i][aria-label*='New' i]",
            "[title='Service Request List:New']",
        ]
        for root in _roots():
            if _sr_new_clicked:
                break
            for css in _sr_selectors:
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        try:
                            loc.click(timeout=_tmo)
                        except Exception:
                            loc.click(timeout=_tmo, force=True)
                        _sr_new_clicked = True
                        break
                except Exception:
                    continue
        if not _sr_new_clicked:
            return False, "Could not click 'Service Request List:New' on PDI tab."
        note(f"{log_prefix}: clicked Service Request List:New on PDI tab.")
        _safe_page_wait(page, 1200, log_label="after_sr_list_new")

        if not _siebel_click_by_id_anywhere(
            page,
            "s_2_2_32_0_icon",
            timeout_ms=_tmo,
            content_frame_selector=content_frame_selector,
            note=note,
            label="PDI pick icon",
            log_prefix=log_prefix,
            wait_ms=1200,
        ):
            if not _siebel_click_by_id_anywhere(
                page,
                "s_2_2_32_0",
                timeout_ms=_tmo,
                content_frame_selector=content_frame_selector,
                note=note,
                label="PDI pick button",
                log_prefix=log_prefix,
                wait_ms=1200,
            ):
                return False, "Could not click PDI pick icon (id=s_2_2_32_0_icon)."

        _safe_page_wait(page, 800, log_label="after_pdi_pick_icon_settle")
        for root in _roots():
            try:
                _pdi_row_result = root.evaluate("""() => {
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
                if _pdi_row_result:
                    note(f"{log_prefix}: picked first row in PDI applet.")
                    _safe_page_wait(page, 600, log_label="after_pdi_row_pick")
                    break
            except Exception:
                continue

        _pdi_ok_done = False
        for root in _roots():
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
                        _pdi_ok_done = True
                        note(f"{log_prefix}: clicked OK on PDI pick applet.")
                        _safe_page_wait(page, 1000, log_label="after_pdi_ok")
                        break
                except Exception:
                    continue
            if _pdi_ok_done:
                break
        if not _pdi_ok_done:
            note(f"{log_prefix}: OK button not found on PDI pick applet (best-effort).")

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
    return True, None


# Siebel **Create Invoice** after order attach: off by default — enable only when product wants automation
# to submit the invoice (operator may complete this step manually).
_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE = False


def _attach_vehicle_to_bkg(
    page: Page,
    *,
    full_chassis: str,
    order_number: str = "",
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> tuple[bool, str | None, dict]:
    """
    After a new sales order is saved:
    1. Click Order Number header link to open order detail.
    2. Click **New** → fill VIN → Price All → Allocate All.
    3. Single-click **VIN** drilldown (name=VIN) → **Serial Number** →
       ``_siebel_run_vehicle_serial_detail_precheck_pdi`` (Pre-check + PDI only; no field scrapes).
    4. Click ``Order:<order#>`` link → **Apply Campaign**. **Create Invoice** is skipped unless
       ``_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE`` is set True.

    Does **not** read Totals, feature ids, or Invoice# from the DOM — vehicle and ex-showroom values
    come from ``prepare_vehicle`` / grid merge and ``_create_order`` scrapes where applicable.
    Returns ``(success, error_detail, extra_dict)`` with ``extra_dict`` always ``{}`` for API compatibility.
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

    # ── Step 1: Click Order Number header link ──
    _order_clicked = False
    _order_selectors = (
        "a[name='Order Number'][tabindex='-1']",
        "a[name='Order Number']",
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
                note(f"attach_vehicle_to_bkg: clicked Order Number header link via {css!r}.")
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
          if (!el || !vis(el)) return '';
          try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
          el.click();
          return 'ok';
        }"""
        for frame in _ordered_frames(page):
            try:
                if frame.evaluate(_js_order):
                    _order_clicked = True
                    note("attach_vehicle_to_bkg: JS clicked Order Number in frame.")
                    _safe_page_wait(page, 1500, log_label="after_attach_vehicle_to_bkg_js_frame")
                    break
            except Exception:
                continue
        if not _order_clicked:
            try:
                if page.evaluate(_js_order):
                    _order_clicked = True
                    note("attach_vehicle_to_bkg: JS clicked Order Number on main page.")
                    _safe_page_wait(page, 1500, log_label="after_attach_vehicle_to_bkg_js_page")
            except Exception:
                pass
    if not _order_clicked:
        return False, "Could not click Order Number header link.", {}

    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    # ── Step 2: Click New button on order line / allocate (Hero: control id ends with _Ctrl) ──
    _new_clicked = _click_by_id("s_1_1_35_0_Ctrl", "New button", wait_ms=1200)
    if not _new_clicked:
        _new_clicked = _click_by_id("s_1_1_35_0", "New button (legacy id)", wait_ms=1200)
    if not _new_clicked:
        return False, "Could not click New button (id=s_1_1_35_0_Ctrl) on order line items.", {}

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

    # ── Step 12: Create Invoice (optional; off by default — operator completes in Siebel) ──
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
            "(set _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE=True in siebel_dms_playwright.py to enable)."
        )

    note(
        "attach_vehicle_to_bkg: all steps completed "
        "(Order → VIN → Pre-check → PDI → Apply Campaign"
        + (" → Create Invoice" if _ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE else "")
        + ")."
    )
    return True, None, {}


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
    Vehicle Sales -> Sales Orders New flow:
    - Open Vehicle Sales (first-level view bar)
    - Click Sales Orders New:List (+) when **Invoice Selected** is not opened directly
    - Set Booking Order Type
    - When ``battery_partial`` is set (detail sheet **Battery No** from DMS fill payload), fill **Comments**
      with ``Battery is <number>``
    - Pick contact by mobile from pick applet
    - Save, scrape Order#, then ``_attach_vehicle_to_bkg`` (header ``a[name='Order Number'][tabindex='-1']``):
      line-item VIN, Price All, Allocate All, VIN drill → Serial → Pre-check/PDI (clicks only — **no** DOM scrapes in attach).
    - (Invoice-selected / legacy path only) On order line items: Line Items List:New -> VIN (name=VIN) -> full chassis + Enter;
      optional pick applet (search by Vin#) -> fill chassis, select row, OK; then scrape inventory
    - If inventory not In transit: Price all + Allocate all
    - Legacy path: scrape Total (Ex-showroom). Primary attach path: ex-showroom from ``prepare_vehicle`` grid / DMS merge.
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

    def _go_to_invoice_selected_direct() -> bool:
        """
        Prefer direct context switch to Invoice Selected (requested by operator),
        instead of querying Vehicle Sales list by mobile.
        """
        _sels = (
            "#ui-id-429",
            "a#ui-id-429",
            "li#ui-id-429",
            "[id='ui-id-429']",
            "a[aria-label='Invoice Selected']",
            "button[aria-label='Invoice Selected']",
            "a[aria-label*='Invoice Selected' i]",
            "button[aria-label*='Invoice Selected' i]",
        )
        for root in _roots():
            try:
                for role in ("tab", "link", "button"):
                    try:
                        by_role = root.get_by_role(role, name=re.compile(r"invoice\s*selected", re.I)).first
                        if by_role.count() > 0 and by_role.is_visible(timeout=600):
                            try:
                                by_role.click(timeout=min(action_timeout_ms, 2500))
                            except Exception:
                                by_role.click(timeout=min(action_timeout_ms, 2500), force=True)
                            _safe_page_wait(page, 1200, log_label="after_invoice_selected_role_click")
                            note(f"Create Order: switched to Invoice Selected via get_by_role({role}).")
                            return True
                    except Exception:
                        continue
                for css in _sels:
                    try:
                        loc = root.locator(css).first
                        if loc.count() <= 0 or not loc.is_visible(timeout=600):
                            continue
                        try:
                            loc.click(timeout=min(action_timeout_ms, 2500))
                        except Exception:
                            loc.click(timeout=min(action_timeout_ms, 2500), force=True)
                        _safe_page_wait(page, 1200, log_label="after_invoice_selected_css_click")
                        note(f"Create Order: switched to Invoice Selected via selector {css!r}.")
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    _invoice_selected_ready = _go_to_invoice_selected_direct()
    if _invoice_selected_ready:
        note("Create Order: Invoice Selected context opened directly; proceeding from selected-order view.")
    else:
        note("Create Order: Invoice Selected context not found; proceeding directly with '+' new booking flow on current page.")

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

    _existing_order_opened = bool(_invoice_selected_ready)
    if _existing_order_opened:
        note("Create Order: staying on Invoice Selected page; skipping '+' new booking creation.")
    else:
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
                    "location": "siebel_dms_playwright.py:_create_order_finance_input",
                    "message": "Finance branch decision inputs",
                    "data": {
                        "financier_present": bool(_fin_name),
                        "financier_len": len(_fin_name),
                        "financier_token": _fin_token if _fin_token in ("", "na", "n/a", "null", "none", "-") else "other",
                        "finance_required_target": _fin_val,
                    },
                    "timestamp": int(_t_fin.time() * 1000),
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
                    "location": "siebel_dms_playwright.py:_create_order_finance_required_outcome",
                    "message": "Finance Required set result",
                    "data": {
                        "finance_required_target": _fin_val,
                        "finance_required_set": bool(_fin_ok),
                    },
                    "timestamp": int(_t_fin.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion

        if _is_financed:
            _fin_name_ok = False
            for root in _roots():
                try:
                    for _lbl in ("Financer", "Financier", "Financer Name", "Financier Name"):
                        if _fill_by_label_on_frame(root, _lbl, _fin_name, action_timeout_ms=action_timeout_ms):
                            _fin_name_ok = True
                            _locked_root = root
                            break
                        if _select_dropdown_by_label_on_frame(
                            root,
                            label=_lbl,
                            value=_fin_name,
                            action_timeout_ms=min(action_timeout_ms, 8000),
                        ):
                            _fin_name_ok = True
                            _locked_root = root
                            break
                    if _fin_name_ok:
                        break
                except Exception:
                    continue
            if not _fin_name_ok:
                _fin_err = _detect_siebel_error_popup(page, content_frame_selector)
                if _fin_err:
                    return False, f"Siebel error while setting Financier/Financer: {_fin_err[:200]}", scraped
                return False, f"Could not set Financier/Financer with value {_fin_name!r}.", scraped
            _safe_page_wait(page, 500, log_label="after_financier_fill")
            note(f"Create Order: set Financier/Financer using input {_fin_name!r}.")
            _fin_post_err = _detect_siebel_error_popup(page, content_frame_selector)
            if _fin_post_err:
                return False, f"Siebel error after Financier/Financer input: {_fin_post_err[:200]}", scraped

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
                        "location": "siebel_dms_playwright.py:_create_order_hypothecation_outcome",
                        "message": "Hypothecation set result",
                        "data": {
                            "hypothecation_target": _hyp_val,
                            "hypothecation_set": bool(_hyp_ok),
                        },
                        "timestamp": int(_t_fin.time() * 1000),
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
                _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H1_H4","location":"siebel_dms_playwright.py:create_order_f2_start","message":"F2 applet context","data":{"locked_root_type":_lr_type,"locked_root_url":(_lr_url or "")[:150],"contact_roots_count":_cr_count},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                            _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H10","location":"siebel_dms_playwright.py:create_order_cls_miss","message":"CLS field not found via aria-label CSS selector","data":{"root_url":getattr(root,'url','?')[:120]},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H10","location":"siebel_dms_playwright.py:create_order_cls_found","message":"CLS field found via aria-label CSS","data":{"aria":_cls_aria[:80],"name":_cls_name,"id":_cls_id[:40],"root_url":getattr(root,'url','?')[:120]},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                            _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H11","location":"siebel_dms_playwright.py:create_order_icon_handle","message":"F2 icon click via evaluate_handle","data":{"icon_clicked": _icon_clicked},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H14","location":"siebel_dms_playwright.py:create_order_applet_focus","message":"Focused element after applet open","data":_focus_info,"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H18","location":"siebel_dms_playwright.py:create_order_val_focus","message":"Focus after Tab to value field","data":{**_val_focus, "search_type": _search_type, "search_val": _search_val},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H20","location":"siebel_dms_playwright.py:create_order_val_fill","message":"Value field fill result","data":{"typed": _search_val, "readback": _val_readback, "filled": _val_filled, "strategy": _fill_strategy},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                        _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H13","location":"siebel_dms_playwright.py:create_order_post_query","message":"Post-query applet state","data":_pq_data,"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                                _lf.write(_j_f2.dumps({"sessionId":"08e634","hypothesisId":"H15","location":"siebel_dms_playwright.py:create_order_row_match","message":"Row match result","data":_result or {},"timestamp":int(_t_f2.time()*1000)}) + "\n")
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
                            "location": "siebel_dms_playwright.py:create_order_applet_ok_precheck",
                            "message": "Preparing to click applet OK",
                            "data": {
                                "fresh_roots_count": len(_fresh_roots2),
                                "row_matched": bool(_row_ok),
                            },
                            "timestamp": int(_t_f2.time() * 1000),
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
                            "location": "siebel_dms_playwright.py:create_order_applet_ok_outcome",
                            "message": "Applet OK click outcome",
                            "data": {
                                "ok_button_clicked": bool(_ok_clicked),
                                "enter_fallback_used": not bool(_ok_clicked),
                            },
                            "timestamp": int(_t_f2.time() * 1000),
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
                            "location": "siebel_dms_playwright.py:create_order_pincode_readback",
                            "message": "Pincode readback after contact applet",
                            "data": {
                                "pincode_non_empty": bool((_pin_rb or "").strip()),
                                "pincode_len": len((_pin_rb or "").strip()),
                            },
                            "timestamp": int(_t_f2.time() * 1000),
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
                            "location": "siebel_dms_playwright.py:create_order_applet_done_flag",
                            "message": "Applet flow completion flag set",
                            "data": {
                                "applet_done": True,
                                "pincode_non_empty": bool((_pin_rb or "").strip()),
                            },
                            "timestamp": int(_t_f2.time() * 1000),
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
                    "location": "siebel_dms_playwright.py:create_order_pre_save_pin_guard",
                    "message": "Pre-save pincode guard check",
                    "data": {
                        "pincode_non_empty": bool(_contact_pin_rb),
                        "pincode_len": len(_contact_pin_rb),
                    },
                    "timestamp": int(_t_f2.time() * 1000),
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
        order_no = _scrape_order_number_current()
        scraped["order_number"] = order_no
        if order_no:
            note(f"Create Order: scraped Order#={order_no!r} after save.")
        else:
            note("Create Order: Order# not readable after save (best-effort).")
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
    # 9) Click Order# drill-down link
    if not _existing_order_opened:
        _order_opened = _open_order_link("step-9-attempt-1")
        if not _order_opened:
            _safe_page_wait(page, 1500, log_label="before_order_link_step9_retry")
            _order_opened = _open_order_link("step-9-attempt-2")
        if not _order_opened:
            return False, "Could not click Order# row/link.", scraped
        _safe_page_wait(page, 1200, log_label="after_open_order_link")
        note("Create Order: clicked Order# row.")
    else:
        _safe_page_wait(page, 1200, log_label="after_open_existing_order_link")

    # 10) Line Items List:New -> click VIN (name=VIN) -> full chassis + Enter; optional Vin# pick applet
    _tmo_line = min(action_timeout_ms, 4000)

    _JS_LINE_ITEMS_NEW = """() => {
        const want = 'line items list:new';
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width > 1 && r.height > 1;
        };
        const fire = (el) => {
            try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
            try { el.focus(); } catch (e) {}
            const opts = { bubbles: true, cancelable: true, view: window };
            try {
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.dispatchEvent(new MouseEvent('click', opts));
            } catch (e) {}
            try { if (typeof el.click === 'function') el.click(); } catch (e) {}
            return true;
        };
        const cand = Array.from(
            document.querySelectorAll('a,button,[role="button"],div[role="button"],span[role="button"]')
        ).filter(vis);
        for (const el of cand) {
            const al = (el.getAttribute('aria-label') || '').trim().toLowerCase();
            const ttl = (el.getAttribute('title') || '').trim().toLowerCase();
            if (al === want || ttl === want || (al.includes('line items list') && al.includes('new'))) {
                fire(el);
                return al || ttl || 'line-items-new';
            }
        }
        return '';
    }"""

    def _click_line_items_new_hardened() -> bool:
        """
        Siebel toolbar \"New\" often shows focus without running the applet action.
        Try: per-frame DOM click + mouse events, then Playwright focus + click + keyboard activate.
        """
        for frame in _ordered_frames(page):
            try:
                hit = frame.evaluate(_JS_LINE_ITEMS_NEW)
                if hit:
                    note(f"Create Order: Line Items List:New — frame JS activation ({hit!r}).")
                    return True
            except Exception:
                continue
        _new_selectors = (
            "a[aria-label='Line Items List:New']",
            "button[aria-label='Line Items List:New']",
            "a[aria-label*='Line Items List' i][aria-label*='New' i]",
            "button[aria-label*='Line Items List' i][aria-label*='New' i]",
        )
        def _activate_locator(target, *, label: str) -> bool:
            target.scroll_into_view_if_needed(timeout=_tmo_line)
            try:
                target.focus(timeout=800)
            except Exception:
                pass
            try:
                target.click(timeout=_tmo_line)
            except Exception:
                target.click(timeout=_tmo_line, force=True)
            try:
                target.evaluate(
                    """el => {
                      const o = { bubbles: true, cancelable: true, view: window };
                      el.dispatchEvent(new MouseEvent('mousedown', o));
                      el.dispatchEvent(new MouseEvent('mouseup', o));
                      el.dispatchEvent(new MouseEvent('click', o));
                      if (typeof el.click === 'function') el.click();
                    }"""
                )
            except Exception:
                pass
            for key in ("Enter", " "):
                try:
                    target.press(key, timeout=800)
                    break
                except Exception:
                    continue
            note(f"Create Order: Line Items List:New — {label}.")
            return True

        for root in _roots():
            for role in ("button", "link"):
                try:
                    by_role = root.get_by_role(role, name=re.compile(r"line\s+items\s+list.*\bnew\b", re.I))
                    if by_role.count() > 0:
                        b = by_role.first
                        if b.is_visible(timeout=600):
                            return _activate_locator(b, label=f"get_by_role({role}) + keys")
                except Exception:
                    pass
            for css in _new_selectors:
                try:
                    loc = root.locator(css).first
                    if loc.count() <= 0 or not loc.is_visible(timeout=600):
                        continue
                    return _activate_locator(loc, label=f"locator {css!r} + keys")
                except Exception:
                    continue
        return False

    if not _click_line_items_new_hardened():
        return False, "Could not activate Line Items List:New (click/focus/keyboard).", scraped
    _safe_page_wait(page, 900, log_label="after_line_items_new")
    note("Create Order: Line Items List:New activation attempted.")

    # Focus/open the VIN control (Siebel uses name="VIN" on the line popup field)
    _click_any(
        (
            "input[name='VIN']",
            "[name='VIN']",
        ),
        timeout=min(action_timeout_ms, 2500),
    )
    _safe_page_wait(page, 400, log_label="after_vin_focus_click")

    _vin_selectors = (
        "input[name='VIN']",
        "input[aria-label='VIN']",
        "input[id*='VIN' i]",
        "input[title='VIN']",
        "input[title*='VIN' i]",
    )
    if not _fill_any(_vin_selectors, full_chassis, timeout=_tmo_line):
        return False, "Could not fill VIN with full chassis on line item.", scraped
    try:
        page.keyboard.press("Enter")
    except Exception:
        pass
    _safe_page_wait(page, 1100, log_label="after_line_item_vin_enter")
    note(f"Create Order: filled line VIN and pressed Enter. chassis={full_chassis!r}")

    def _vin_pick_applet_query_field_visible() -> bool:
        """True only when a Vin# *search* field appears (not the line-item name=VIN box)."""
        for root in _roots():
            try:
                loc_lb = root.get_by_label(re.compile(r"vin\s*#", re.I)).first
                if loc_lb.count() > 0 and loc_lb.is_visible(timeout=450):
                    return True
            except Exception:
                pass
            for css in (
                "input[aria-label*='Vin#' i]",
                "input[aria-label*='VIN#' i]",
                "input[title*='Vin#' i]",
                "input[title*='VIN#' i]",
            ):
                try:
                    loc = root.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=450):
                        return True
                except Exception:
                    continue
        return False

    _JS_CLICK_FIRST_GRID_ROW = """() => {
        const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width > 2 && r.height > 2;
        };
        const tryRows = [
            "table.ui-jqgrid-btable tbody tr[role='row']",
            "div.ui-jqgrid-bdiv table tbody tr",
            "table.siebui-list tbody tr",
            "table tbody tr"
        ];
        for (const sel of tryRows) {
            const rows = Array.from(document.querySelectorAll(sel)).filter(vis);
            for (const tr of rows) {
                if (tr.closest('thead')) continue;
                if ((tr.innerText || '').toLowerCase().includes('order')) continue;
                const tds = tr.querySelectorAll('td');
                if (tds.length === 0) continue;
                try { tr.click(); return 'clicked:' + sel; } catch (e) {}
            }
        }
        return '';
    }"""

    def _handle_vin_search_pick_applet() -> bool:
        """If a Vin# search pick applet is open: Query (optional), fill chassis, Enter, pick row, OK."""
        if not _vin_pick_applet_query_field_visible():
            note("Create Order: no Vin# search pick applet detected; continuing.")
            return True
        note("Create Order: Vin# search pick applet detected; running query + row + OK.")
        _click_any(
            (
                "a[aria-label*='Vin' i][aria-label*='Query' i]",
                "button[aria-label*='Vin' i][aria-label*='Query' i]",
                "a[aria-label*='List' i][aria-label*='Query' i]",
                "button[aria-label*='List' i][aria-label*='Query' i]",
                "a[title*='Query' i]",
                "button[title*='Query' i]",
            ),
            timeout=min(action_timeout_ms, 2000),
        )
        _safe_page_wait(page, 500, log_label="after_vin_pick_query_click")
        _pick_fill_selectors = (
            "input[aria-label*='Vin#' i]",
            "input[aria-label*='VIN#' i]",
            "input[title*='Vin#' i]",
            "input[title*='VIN#' i]",
            "input[id*='1_VIN' i]",
            "input[id*='Vin' i]",
            "input[name*='VIN' i]",
            "input[name*='Vin' i]",
        )
        if not _fill_any(_pick_fill_selectors, full_chassis, timeout=_tmo_line):
            note("Create Order: Vin# pick applet visible but could not fill query field.")
            return False
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        _safe_page_wait(page, 1000, log_label="after_vin_pick_enter")
        _row_clicked = False
        for frame in _ordered_frames(page):
            try:
                r = frame.evaluate(_JS_CLICK_FIRST_GRID_ROW)
                if r:
                    note(f"Create Order: selected row in Vin pick applet. {r!r}")
                    _row_clicked = True
                    break
            except Exception:
                continue
        if not _row_clicked:
            for root in _roots():
                try:
                    loc = root.locator("tbody tr").first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        loc.click(timeout=min(action_timeout_ms, 2500), force=True)
                        _row_clicked = True
                        note("Create Order: selected first tbody row in Vin pick applet (Playwright).")
                        break
                except Exception:
                    continue
        if not _row_clicked:
            note("Create Order: warning — no grid row clicked in Vin pick applet; still trying OK.")
        if not _click_any(
            (
                "button:has-text('OK')",
                "a:has-text('OK')",
                "button[aria-label='OK']",
                "a[aria-label='OK']",
            ),
            timeout=min(action_timeout_ms, 3500),
        ):
            return False
        _safe_page_wait(page, 1000, log_label="after_vin_pick_ok")
        note("Create Order: closed Vin# pick applet with OK.")
        return True

    if not _handle_vin_search_pick_applet():
        return False, "Could not complete Vin# search pick applet (fill row OK).", scraped
    _safe_page_wait(page, 600, log_label="after_vin_flow")
    note(f"Create Order: line-item VIN flow complete. chassis={full_chassis!r}.")

    # 11) Scrape Inventory Location
    inv = ""
    for frame in _ordered_frames(page):
        try:
            got = frame.evaluate(
                """() => {
                  const q = [
                    "input[aria-label*='Inventory Location' i]",
                    "input[title*='Inventory Location' i]",
                    "input[name*='Inventory_Location' i]",
                    "input[id*='Inventory_Location' i]"
                  ];
                  for (const s of q) {
                    const el = document.querySelector(s);
                    if (el && (el.value || '').trim()) return (el.value || '').trim();
                  }
                  return '';
                }"""
            )
            if (got or "").strip():
                inv = (got or "").strip()
                break
        except Exception:
            continue
    scraped["inventory_location"] = inv
    note(f"Create Order: inventory location={inv!r}.")

    if inv.strip().lower() != "in transit":
        _click_any(("a:has-text('Price all')", "button:has-text('Price all')"), timeout=min(action_timeout_ms, 3000))
        _safe_page_wait(page, 800, log_label="after_price_all")
        _click_any(("a:has-text('Allocate all')", "button:has-text('Allocate all')"), timeout=min(action_timeout_ms, 3000))
        _safe_page_wait(page, 800, log_label="after_allocate_all")
        note("Create Order: clicked Price all and Allocate all (inventory is not In transit).")
    else:
        note("Create Order: inventory is In transit; skipped Price all / Allocate all.")

    # 12) Scrape Total (Ex-showroom)
    total_ex = ""
    for frame in _ordered_frames(page):
        try:
            got = frame.evaluate(
                """() => {
                  const q = [
                    "input[aria-label*='Total (Ex-showroom)' i]",
                    "input[title*='Total (Ex-showroom)' i]",
                    "input[aria-label*='Ex-showroom' i]",
                    "input[title*='Ex-showroom' i]"
                  ];
                  for (const s of q) {
                    const el = document.querySelector(s);
                    if (el && (el.value || '').trim()) return (el.value || '').trim();
                  }
                  return '';
                }"""
            )
            if (got or "").strip():
                total_ex = (got or "").strip()
                break
        except Exception:
            continue
    scraped["vehicle_price"] = total_ex
    if total_ex:
        note(f"Create Order: scraped Total (Ex-showroom)={total_ex!r}.")
    else:
        note("Create Order: could not scrape Total (Ex-showroom).")

    _ord = _scrape_order_number_current()
    if _ord:
        scraped["order_number"] = _ord
        note(f"Create Order: scraped Order#={_ord!r} (legacy line-item path).")
    _inv = _scrape_invoice_number_current()
    scraped["invoice_number"] = (_inv or scraped.get("invoice_number") or "")
    if _inv:
        note(f"Create Order: scraped Invoice#={_inv!r} (legacy line-item path).")

    return True, None, scraped


def _siebel_open_found_customer_record(
    page: Page,
    *,
    mobile: str,
    first_name: str,
    timeout_ms: int,
    content_frame_selector: str | None,
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
        _safe_page_wait(page, 1000, log_label="after_left_customer_click")
    else:
        _safe_page_wait(page, 1500, log_label="after_left_customer_click_skipped")

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
    # region agent log
    _agent_debug_log(
        "V2",
        "siebel_dms_playwright.py:_siebel_prepare_vehicle_list_find_vin_engine",
        "vehicle_find_submit_enter",
        {
            "frame_partial_len": int(len(fp)),
            "engine_partial_len": int(len(ep)),
            "has_selector": bool(content_frame_selector),
        },
    )
    # endregion
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
        # region agent log
        _agent_debug_log(
            "V3",
            "siebel_dms_playwright.py:_siebel_prepare_vehicle_list_find_vin_engine",
            "vehicle_find_applet_prepared",
            {"prepared": True},
        )
        # endregion
    else:
        note(
            "prepare_vehicle: Find → Vehicles not confirmed — still attempting VIN/Engine fill in find field box."
        )
        # region agent log
        _agent_debug_log(
            "V3",
            "siebel_dms_playwright.py:_siebel_prepare_vehicle_list_find_vin_engine",
            "vehicle_find_applet_prepared",
            {"prepared": False},
        )
        # endregion
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
        # region agent log
        _agent_debug_log(
            "V4",
            "siebel_dms_playwright.py:_siebel_prepare_vehicle_list_find_vin_engine",
            "vehicle_find_submit_result",
            {"filled": True, "attempt": "first"},
        )
        # endregion
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
    # region agent log
    _agent_debug_log(
        "V4",
        "siebel_dms_playwright.py:_siebel_prepare_vehicle_list_find_vin_engine",
        "vehicle_find_submit_result",
        {"filled": bool(filled), "attempt": "retry"},
    )
    # endregion
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
                    # region agent log
                    _agent_debug_log(
                        "V1",
                        "siebel_dms_playwright.py:_wait_for_vehicle_find_applet_ready",
                        "vehicle_find_applet_ready",
                        {
                            "ready": True,
                            "elapsed_ms": int((time.monotonic() - start_t) * 1000),
                            "wait_ms": int(wait_ms),
                            "poll_count": int(poll_count),
                            "has_selector": bool(content_frame_selector),
                        },
                    )
                    # endregion
                    return True
            except Exception:
                continue
        _safe_page_wait(page, 140, log_label="wait_vehicle_find_applet_ready")
    # region agent log
    _agent_debug_log(
        "V1",
        "siebel_dms_playwright.py:_wait_for_vehicle_find_applet_ready",
        "vehicle_find_applet_ready",
        {
            "ready": False,
            "elapsed_ms": int((time.monotonic() - start_t) * 1000),
            "wait_ms": int(wait_ms),
            "poll_count": int(poll_count),
            "has_selector": bool(content_frame_selector),
        },
    )
    # endregion
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
    # region agent log
    _agent_debug_log(
        "V5",
        "siebel_dms_playwright.py:_siebel_goto_vehicle_list_and_scrape",
        "vehicle_find_initial_ready_gate",
        {"ready": bool(first_ready)},
    )
    # endregion

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
            # region agent log
            _agent_debug_log(
                "V5",
                "siebel_dms_playwright.py:_siebel_goto_vehicle_list_and_scrape",
                "vehicle_find_query_after_retry",
                {"query_ok": True},
            )
            # endregion
        else:
            final_ready = _wait_for_vehicle_find_applet_ready(
                page, content_frame_selector=content_frame_selector, wait_ms=900
            )
            # region agent log
            _agent_debug_log(
                "V5",
                "siebel_dms_playwright.py:_siebel_goto_vehicle_list_and_scrape",
                "vehicle_find_query_after_retry",
                {"query_ok": False, "final_ready": bool(final_ready)},
            )
            # endregion
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

    Hero **Auto Vehicle** detail puts VIN / serial drill-ins in the jqGrid **table** ``#s_1_l`` and/or
    the grid chrome ``div#gview_s_1_l`` — try those scopes first, then fall back to document-wide search.

    ``visible_text_fallback`` (e.g. full chassis): Siebel often shows the VIN as ``outerText`` while
    keeping ``name="Serial Number"``; the accessible name may match the chassis rather than the label,
    so we try ``name`` + text filter and ``role=link`` by chassis on every frame root.
    """
    nv_esc = name_val.replace("'", "\\'")

    def _try_click_loc(loc, *, where: str) -> bool:
        try:
            if loc.count() == 0:
                return False
        except Exception:
            return False
        for vis_ms in (900, 1600):
            try:
                if not loc.is_visible(timeout=vis_ms):
                    continue
            except Exception:
                continue
            try:
                loc.evaluate(
                    """(el) => {
                      try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
                    }"""
                )
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
                    continue
        return False

    roots = list(_siebel_locator_roots_for_vehicle_prep(page, content_frame_selector))
    # 1) jqGrid table + view chrome (drilldown anchors live under ``table#s_1_l`` in Open UI).
    grid_scopes = (
        "#s_1_l",
        "table#s_1_l",
        "[id='s_1_l']",
        "#gview_s_1_l",
        "[id='gview_s_1_l']",
        "div#gview_s_1_l",
    )
    for root in roots:
        for gpre in grid_scopes:
            for css in (
                f"{gpre} a[name='{nv_esc}']",
                f"{gpre} button[name='{nv_esc}']",
                f"{gpre} a[title='{nv_esc}']",
                f"{gpre} [name='{nv_esc}']",
            ):
                try:
                    loc = root.locator(css).first
                    if _try_click_loc(loc, where=f"scoped {gpre!r}"):
                        return True
                except Exception:
                    continue
            try:
                box = root.locator(gpre).first
                if box.count() > 0:
                    role_ln = box.get_by_role(
                        "link", name=re.compile(rf"^\s*{re.escape(name_val)}\s*$", re.I)
                    )
                    if role_ln.count() > 0 and _try_click_loc(
                        role_ln.first, where=f"scoped role=link {gpre!r}"
                    ):
                        return True
            except Exception:
                continue
    # 2) Document-wide (legacy)
    for root in roots:
        for css in (
            f"a[name='{nv_esc}']",
            f"button[name='{nv_esc}']",
            f"[name='{nv_esc}']",
        ):
            try:
                loc = root.locator(css).first
                if _try_click_loc(loc, where="global name match"):
                    return True
            except Exception:
                continue
    vtf = (visible_text_fallback or "").strip()
    if vtf:
        chassis_sub = re.compile(re.escape(vtf), re.I)
        chassis_exact = re.compile(rf"^\s*{re.escape(vtf)}\s*$", re.I)
        _in_tbl = ("#s_1_l", "table#s_1_l")
        for root in roots:
            for tbl_scope in (*_in_tbl, None):
                base = root.locator(tbl_scope) if tbl_scope else root
                _scope_note = (
                    " in #s_1_l" if tbl_scope in _in_tbl else ""
                )
                for css in (
                    f'a[name="{name_val}"]',
                    f'button[name="{name_val}"]',
                    f'[name="{name_val}"]',
                ):
                    try:
                        loc = base.locator(css).filter(has_text=chassis_sub).first
                        if _try_click_loc(
                            loc,
                            where=f"name+chassis visible text{_scope_note or ' (global)'}",
                        ):
                            return True
                    except Exception:
                        continue
                try:
                    ln_e = base.get_by_role("link", name=chassis_exact)
                    if ln_e.count() > 0 and _try_click_loc(
                        ln_e.first,
                        where=f"role=link exact chassis (a11y name){_scope_note}",
                    ):
                        return True
                except Exception:
                    pass
                try:
                    ln_s = base.get_by_role("link", name=chassis_sub)
                    if ln_s.count() > 0 and _try_click_loc(
                        ln_s.first,
                        where=f"role=link chassis substring{_scope_note}",
                    ):
                        return True
                except Exception:
                    pass
                try:
                    bt = base.get_by_role("button", name=chassis_sub)
                    if bt.count() > 0 and _try_click_loc(
                        bt.first,
                        where=f"role=button chassis{_scope_note}",
                    ):
                        return True
                except Exception:
                    pass
    # 4) DOM click for Siebel drilldowns that exist but fail visibility / Playwright hit-testing.
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
                note(
                    f"prepare_vehicle: clicked {log_label!r} (name={name_val!r}, JS querySelector+click in frame)."
                )
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
      for (const id of [
        '4_s_1_l_HHML_Feature_Value', '5_s_1_l_HHML_Feature_Value',
        '4_s_1_l_HHML_Fetaure_Value', '5_s_1_l_HHML_Fetaure_Value'
      ]) {
        const el = document.getElementById(id);
        if (el && vis(el)) return true;
      }
      const any = document.querySelector('[id*="HHML_Feature_Value"],[id*="HHML_Fetaure_Value"]');
      if (any && vis(any)) return true;
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


def _siebel_scrape_features_cubic_and_vehicle_type(page: Page) -> tuple[str, str]:
    """
    On **Features and Image**, read cubic capacity and vehicle type.

    1. **HHML** ids ``4_s_1_l_HHML_Feature_Value`` / ``5_s_1_l_HHML_Feature_Value`` (and ``Fetaure`` typo)
       and visible ``*[id*='HHML_Feature_Value']`` / ``Fetaure`` by row prefix.
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
                  const read = (id) => {
                    const el = document.getElementById(id);
                    if (!el || !vis(el)) return '';
                    return String(el.value || el.textContent || el.innerText || '').trim();
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
                    ).filter(vis);
                    const byRow = { 4: [], 5: [] };
                    for (const el of cand) {
                      const id = el.getAttribute('id') || '';
                      const t = String(el.value || el.textContent || el.innerText || '').replace(/\\s+/g, ' ').trim();
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
                    ).filter(vis);
                    for (const el of cand) {
                      const id = el.getAttribute('id') || '';
                      if (rowHint(id) !== 5 && id.indexOf('_5_') < 0) continue;
                      const t = String(el.value || el.textContent || el.innerText || '').replace(/\\s+/g, ' ').trim();
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
                      return String(cell.textContent || cell.innerText || '').replace(/\\s+/g, ' ').trim();
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
    Dealer-stock path after inventory gate: **Serial Number** drilldown lands on **Features and Image**
    — read cubic / vehicle type from HHML, then **Pre-check** + **PDI**
    (``_siebel_run_vehicle_serial_detail_precheck_pdi``). No Features tab click after serial drill.

    ``prepare_vehicle`` calls this only when ``in_transit`` is false after the inventory gate.

    Returns ``None`` on success; on failure returns an error string.
    """
    if _siebel_vehicle_features_hhml_applet_visible(page):
        note(
            "prepare_vehicle: HHML Features applet already visible — skipping top-grid VIN / "
            "Serial Number drilldown (avoid redundant clicks after navigation)."
        )
    else:
        _drill_err = _prepare_vehicle_open_serial_detail_from_vehicle_grid(
            page,
            scraped,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
        )
        if _drill_err:
            return _drill_err

    # Serial drill opens the Features & Image view — scrape HHML directly (no tab activation step).
    note(
        "prepare_vehicle: reading cubic_capacity / vehicle_type on Features view after serial drill "
        "(no Features tab click)."
    )
    _safe_page_wait(page, 500, log_label="before_features_hhml_scrape")
    cc, vt = "", ""
    for _fi in range(10):
        cc, vt = _siebel_scrape_features_cubic_and_vehicle_type(page)
        if (cc or vt) or _fi >= 9:
            break
        _safe_page_wait(page, 400, log_label=f"features_hhml_scrape_retry_{_fi}")

    if cc or vt:
        if cc:
            scraped["cubic_capacity"] = _normalize_cubic_cc_digits(cc) or str(cc).strip()
        if vt:
            scraped["vehicle_type"] = vt
        _feat_cc = scraped.get("cubic_capacity") or cc
        note(f"prepare_vehicle: Features view → cubic_capacity={_feat_cc!r}, vehicle_type={vt!r}.")
        if callable(form_trace):
            form_trace(
                "5_vehicle_features",
                "Features and Image",
                "scrape_HHML_Feature_Value_cubic_and_vehicle_type",
                cubic_capacity=str(scraped.get("cubic_capacity") or cc or ""),
                vehicle_type=str(vt or ""),
            )
    else:
        note(
            "prepare_vehicle: cubic_capacity / vehicle_type not read from HHML after serial drill "
            "(best-effort)."
        )

    note("prepare_vehicle: Pre-check + PDI (serial detail view).")
    _serial_pc_ok, _serial_pc_err = _siebel_run_vehicle_serial_detail_precheck_pdi(
        page,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
        form_trace=form_trace,
        log_prefix="prepare_vehicle",
        scraped=scraped,
    )
    if not _serial_pc_ok:
        if _serial_pc_err:
            return _serial_pc_err
        return "Pre-check / PDI failed (prepare_vehicle)."
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
    Returns ``(critical_messages, informational_messages)`` for operators and ``Playwright_DMS.txt``.

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

    if not (merged.get("vehicle_price") or "").strip():
        info.append(
            "vehicle_ex_showroom_price: not in merge — grid price column or DMS price fields may be empty."
        )

    return critical, info


def _write_playwright_vehicle_master_section(
    log_fp,
    merged: dict,
    critical: list[str],
    informational: list[str],
) -> None:
    """Append merged vehicle-master keys and gap notes to ``Playwright_DMS.txt``."""
    if log_fp is None:
        return
    keys = (
        "full_chassis",
        "full_engine",
        "key_num",
        "raw_key_num",
        "model",
        "color",
        "colour",
        "variant",
        "cubic_capacity",
        "seating_capacity",
        "body_type",
        "vehicle_type",
        "num_cylinders",
        "year_of_mfg",
        "vehicle_price",
        "in_transit",
        "inventory_location",
    )
    try:
        log_fp.write("\n--- vehicle_master (merged for update_vehicle_master_from_dms) ---\n")
        for k in keys:
            v = merged.get(k)
            if v is None or v == "":
                continue
            safe = str(v).replace("\n", " ").replace("\r", " ")
            if len(safe) > 2000:
                safe = safe[:1997] + "..."
            log_fp.write(f"{k}={safe!r}\n")
        log_fp.write(
            "# place_of_registeration / oem_name: applied at DB persist from sales_master→dealer_ref/oem_ref "
            "when vehicle_id is set (not scraped in prepare_vehicle).\n"
        )
        if critical:
            log_fp.write("critical_gaps:\n")
            for g in critical:
                log_fp.write(f"  - {g}\n")
        if informational:
            log_fp.write("notes:\n")
            for g in informational:
                log_fp.write(f"  - {g}\n")
        log_fp.flush()
    except OSError:
        pass


def _write_playwright_contact_scrape_section(
    log_fp,
    out: dict,
    *,
    had_open_enquiry_from_sweep: bool,
) -> None:
    """Append ``contact_id`` and optional enquiry# to ``Playwright_DMS.txt`` (operator-facing)."""
    if log_fp is None:
        return
    try:
        cid = (out.get("contact_id") or "").strip()
        enq = str((out.get("vehicle") or {}).get("enquiry_number") or "").strip()
        log_fp.write("\n--- contact_scrape (after Relation's Name path) ---\n")
        log_fp.write(f"contact_id={cid!r}\n")
        if had_open_enquiry_from_sweep and enq:
            log_fp.write(f"open_enquiry_number={enq!r}\n")
        elif enq:
            log_fp.write(f"enquiry_number={enq!r}\n")
        log_fp.flush()
    except OSError:
        pass


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
    Add Enquiry and from ``prepare_vehicle`` after opening that view. Does nothing when both partials are
    empty. Battery is filled before Key to match Siebel tab order / operator SOP on the vehicle page.
    """
    key_val = (dms_values.get("key_partial") or "").strip()
    battery_val = (dms_values.get("battery_partial") or "").strip()
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
    # #region agent log
    try:
        import json as _j_kb

        _n = getattr(_siebel_fill_key_battery_from_dms_values, "_agent_n", 0) + 1
        setattr(_siebel_fill_key_battery_from_dms_values, "_agent_n", _n)
        _fu = ""
        try:
            _fu = (_veh_fill_frame.url or "")[:180]
        except Exception:
            _fu = ""
        with open(Path(__file__).resolve().parents[3] / "debug-0875fe.log", "a", encoding="utf-8") as _lf:
            _lf.write(
                _j_kb.dumps(
                    {
                        "sessionId": "0875fe",
                        "hypothesisId": "KB-A",
                        "location": "siebel_dms_playwright.py:_siebel_fill_key_battery_from_dms_values",
                        "message": "key_battery_fill_invoke",
                        "data": {
                            "invoke_seq": _n,
                            "log_prefix": log_prefix,
                            "has_key": bool(key_val),
                            "has_battery": bool(battery_val),
                            "fill_frame_url": _fu,
                        },
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
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
    key_p = (dms_values.get("key_partial") or "").strip()
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

    # #region agent log
    try:
        import json as _j_pv

        with open(Path(__file__).resolve().parents[3] / "debug-0875fe.log", "a", encoding="utf-8") as _lf:
            _lf.write(
                _j_pv.dumps(
                    {
                        "sessionId": "0875fe",
                        "hypothesisId": "KB-D",
                        "location": "siebel_dms_playwright.py:prepare_vehicle",
                        "message": "prepare_vehicle_before_grid_scrape",
                        "data": {"key_partial_present": bool(key_p), "frame_partial_len": len(frame_p)},
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion

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

    # #region agent log
    try:
        import json as _j_pv2

        with open(Path(__file__).resolve().parents[3] / "debug-0875fe.log", "a", encoding="utf-8") as _lf:
            _lf.write(
                _j_pv2.dumps(
                    {
                        "sessionId": "0875fe",
                        "hypothesisId": "KB-E",
                        "location": "siebel_dms_playwright.py:prepare_vehicle",
                        "message": "prepare_vehicle_after_left_pane_vin_ok",
                        "data": {"vin_click_ok": True, "has_chassis_for_left_hit": True},
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion

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

    return True, None, merged, in_transit_state, vm_crit, vm_info


def _add_enquiry_vehicle_scrape_has_model_year_color(scraped: dict) -> bool:
    """Require model, **YYYY** year of manufacture, and color before creating an opportunity."""
    m = (scraped.get("model") or "").strip()
    y = _normalize_manufacturing_year_yyyy(scraped.get("year_of_mfg") or "")
    c = (scraped.get("color") or "").strip()
    return bool(m and y and c)


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
) -> tuple[bool, dict]:
    """
    Vehicles view: **Find → Vehicles**, right fly-in **VIN** + **Engine#** with ``*`` wildcards, **Enter**,
    optional Find/Go, click matching **VIN** in left **Search Results**, then scrape grid and **Vehicle
    Information** (model / year / color).
    Returns ``(query_ok, scraped)`` — ``scraped`` may be empty if the grid did not render.
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

    if _siebel_try_click_named_in_frames(
        page,
        re.compile(r"Siebel\s*Find", re.I),
        roles=("tab", "link"),
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Add Enquiry: activated Siebel Find tab before VIN drill-down (if present).")
        _safe_page_wait(page, 500, log_label="after_siebel_find_tab_vehicle")

    if _siebel_try_click_vin_search_hit_link(
        page,
        fp,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Add Enquiry: clicked VIN in left Search Results to load vehicle detail.")
        _safe_page_wait(page, 1800, log_label="after_vehicle_search_vin_drilldown")
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeout:
            note("Add Enquiry: networkidle after VIN drill-down timed out; continuing scrape.")

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
    return True, scraped


def _try_click_enquiry_top_tab(
    page: Page, *, action_timeout_ms: int, content_frame_selector: str | None
) -> bool:
    """
    Main module **Enquiry** tab. Hero Connect often marks the control with ``aria-label="Enquiry Selected"``
    (even when switching from **Vehicles**); try that before generic **Enquiry** role/name matches.
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


def _fill_by_label_on_frame(
    frame: Frame,
    label: str,
    value: str,
    *,
    action_timeout_ms: int,
) -> bool:
    if not (value or "").strip():
        return False

    def _do_fill(loc) -> bool:
        try:
            loc.click(timeout=action_timeout_ms)
        except Exception:
            loc.click(timeout=action_timeout_ms, force=True)
        loc.fill("", timeout=action_timeout_ms)
        loc.fill(value.strip(), timeout=action_timeout_ms)
        try:
            loc.press("Tab", timeout=1200)
        except Exception:
            pass
        return True

    pats = (
        re.compile(rf"^\s*{re.escape(label)}\s*$", re.I),
        re.compile(re.escape(label), re.I),
    )
    for pat in pats:
        try:
            loc = frame.get_by_label(pat).first
            if loc.count() <= 0 or not loc.is_visible(timeout=700):
                continue
            return _do_fill(loc)
        except Exception:
            continue
    # Fallback: match raw aria-label attribute directly (bypasses aria-labelledby override).
    esc = label.replace("'", "\\'")
    for css in (
        f"input[aria-label*='{esc}' i]",
        f"textarea[aria-label*='{esc}' i]",
        f"select[aria-label*='{esc}' i]",
    ):
        try:
            loc = frame.locator(css).first
            if loc.count() <= 0 or not loc.is_visible(timeout=700):
                continue
            ro = False
            try:
                ro = loc.evaluate("el => el.readOnly === true")
            except Exception:
                pass
            if ro:
                continue
            return _do_fill(loc)
        except Exception:
            continue
    return False


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
    Vehicle find + scrape, **Enquiry** tab, **Opportunity Form:New**,
    fill opportunity fields from DB + scraped model/color (**Financier** fields are skipped),
    then **Ctrl+S**.

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
            "No contact table match → vehicle find + Opportunities",
            "chassis_engine_Enter_then_Enquiry_tab_Opportunities_New_fields_Ctrl_S",
            frame_partial=frame_p,
            engine_partial=engine_p,
            finance_required=finance_required,
            financier_name=(dms_values.get("financier_name") or "(empty)")[:120],
            aadhar_id=aadhar,
        )

    vq_ok, scraped_v = _siebel_vehicle_find_chassis_engine_enter(
        page,
        (urls.vehicle or "").strip(),
        frame_p,
        engine_p,
        nav_timeout_ms=nav_timeout_ms,
        action_timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        note=note,
    )
    if not vq_ok:
        return False, "Vehicle find failed (chassis/engine query or VIN fly-in).", ""

    _apply_year_of_mfg_yyyy(scraped_v)

    if not _add_enquiry_vehicle_scrape_has_model_year_color(scraped_v):
        note(
            "Add Enquiry: vehicle search did not yield model, year of manufacture, and color in the grid — "
            "not opening Enquiry / new opportunity (confirm list applet column layout vs scrape)."
        )
        return (
            False,
            "Vehicle scrape did not yield model, YYYY year of manufacture, and color (see NOTES above).",
            "",
        )

    note(
        "Add Enquiry: scraped from vehicle list — "
        f"model={scraped_v.get('model')!r}, year_of_mfg={scraped_v.get('year_of_mfg')!r}, "
        f"color={scraped_v.get('color')!r}."
    )
    if vehicle_merge is not None:
        _merge_add_enquiry_vehicle_scrape(vehicle_merge, scraped_v)

    if callable(form_trace):
        form_trace(
            "add_enquiry_vehicle_scrape",
            "Auto Vehicle List — results grid",
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
                        "location": "siebel_dms_playwright.py:_add_enquiry_opportunity",
                        "message": "add_enquiry_before_key_battery_fill",
                        "data": {"after_vehicle_grid_scrape": True},
                        "timestamp": int(time.time() * 1000),
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
            "See Playwright_DMS.txt [NOTE] lines for poll values.",
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


def _persist_dms_scrape_to_db(
    customer_id: int | None,
    vehicle_id: int | None,
    vehicle_dict: dict | None,
    note: Callable[..., object],
) -> None:
    """
    Merge scraped vehicle / order fields into ``vehicle_master`` and ``sales_master`` immediately
    after a successful scrape step (Add Enquiry vehicle list, stage 5 grid, create_order, etc.).
    Lazy-imports fill service to avoid circular imports. Safe to call repeatedly (``COALESCE`` updates).
    """
    if not vehicle_id or not vehicle_dict:
        return
    vd = dict(vehicle_dict)
    try:
        from app.services.fill_hero_dms_service import (
            update_sales_master_from_dms_scrape,
            update_vehicle_master_from_dms,
        )

        update_vehicle_master_from_dms(vehicle_id, vd)
        if customer_id:
            update_sales_master_from_dms_scrape(customer_id, vehicle_id, vd)
        note(
            "Persisted scraped DMS fields to database (vehicle_master"
            + (" + sales_master" if customer_id else "")
            + ")."
        )
    except Exception as exc:
        logger.warning("siebel_dms: persist scrape to DB failed vehicle_id=%s: %s", vehicle_id, exc)


def Playwright_Hero_DMS_fill(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    skip_contact_find: bool = False,
    execution_log_path: Path | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Hero Connect / Siebel automation. If ``SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`` is True (module constant),
    runs only the **Find Contact Enquiry** video SOP through **All Enquiries**, then returns (browser
    not closed by this function).

    Otherwise **linear SOP** (stages 1–8 inside the main ``try``):

    1. **Find** customer by mobile (Contact view). 2. If not matched (or ``new_enquiry``), **basic
    enquiry** only (name, address, state, PIN — no care-of) + Save. 3. **Mandatory re-find** by
    mobile after a new basic enquiry. 4. **Care-of** (father/relation) + Save — **always** runs.
    5. **Vehicle** — nested ``stage_5_vehicle_flow()`` (list search/scrape; **dealer** stock → tab Pre-check/PDI
    on serial detail; if **In Transit** → receipt URL / Process Receipt only, no Pre-check/PDI). 6. **Generate Booking** — **always** after
    vehicle processing (in-transit or not). 7.
    **Allotment** (line items, Price All, Allocate) — **non–In Transit only**, after booking. 8.
    **Invoice hook** (message only; no automation).

    **skip_find** (``skip_contact_find=True``): only for special callers — enquiry view → basic details +
    Save → mandatory re-find on ``DMS_REAL_URL_CONTACT`` → stage 4 care-of, then stages 5–8. Real
    Siebel fill from ``fill_dms_service`` always passes ``skip_contact_find=False`` (Find runs even if
    ``dms_contact_path`` in DB is ``skip_find``).

    ``_attach_vehicle_to_bkg`` clicks **Apply Campaign**; **Create Invoice** only if
    ``_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE`` is True. Returns ``vehicle``,
    ``error``, ``dms_siebel_forms_filled``, notes, milestones, and ``dms_step_messages``.

    If ``execution_log_path`` is set, overwrites that file with a UTC timestamped trace (values used,
    STEP / NOTE / MILESTONE lines, and a final END line with ``error`` if any).
    """
    out: dict = {
        "vehicle": {},
        "error": None,
        "dms_siebel_forms_filled": False,
        "dms_siebel_notes": [],
        "dms_milestones": [],
        "dms_step_messages": [],
    }

    page.set_default_timeout(action_timeout_ms)

    mobile = (dms_values.get("mobile_phone") or "").strip()
    first = (dms_values.get("first_name") or "").strip()
    last = (dms_values.get("last_name") or "").strip()
    addr = (dms_values.get("address_line_1") or "").strip()
    state = (dms_values.get("state") or "").strip()
    pin = (dms_values.get("pin_code") or "").strip()
    landline = (dms_values.get("landline") or "").strip()
    care_of = (dms_values.get("care_of") or "").strip()
    key_p = (dms_values.get("key_partial") or "").strip()
    battery_p = (dms_values.get("battery_partial") or "").strip()
    frame_p = (dms_values.get("frame_partial") or "").strip()
    engine_p = (dms_values.get("engine_partial") or "").strip()
    aadhar_uin = (dms_values.get("aadhar_id") or "").strip()
    dms_path = (dms_values.get("dms_contact_path") or "found").strip().lower()

    log_fp = None
    if execution_log_path is not None:
        lp = Path(execution_log_path)
        lp.parent.mkdir(parents=True, exist_ok=True)
        log_fp = open(lp, "w", encoding="utf-8")
        log_fp.write("Playwright DMS — execution log (this run only; UTC timestamps)\n\n")
        log_fp.write(f"started_utc={datetime.now(timezone.utc).isoformat()}\n")
        log_fp.write(f"skip_contact_find={skip_contact_find}\n")
        log_fp.write(f"dms_contact_path={dms_path!r}\n")
        log_fp.write(f"mobile_phone={mobile!r}\n")
        log_fp.write(f"first_name={first!r}\n")
        log_fp.write(f"last_name={last!r}\n")
        log_fp.write(f"address_line_1={addr!r}\n")
        log_fp.write(f"state={state!r}\n")
        log_fp.write(f"pin_code={pin!r}\n")
        log_fp.write(f"landline={landline!r}\n")
        log_fp.write(f"care_of={care_of!r}\n")
        log_fp.write(f"key_partial={key_p!r}\n")
        log_fp.write(f"frame_partial={frame_p!r}\n")
        log_fp.write(f"engine_partial={engine_p!r}\n")
        log_fp.write(f"aadhar_id={aadhar_uin!r}\n")
        log_fp.write(
            "# Siebel: after stage 5, a --- vehicle_master --- block lists merged keys for "
            "update_vehicle_master_from_dms (grid + DMS). Add Enquiry path can still add "
            "full_chassis/full_engine from vehicle detail drill before that.\n"
        )
        cu = (urls.contact or "").strip()
        log_fp.write(f"url_contact_truncated={cu[:200]!r}\n")
        log_fp.write(f"url_enquiry_truncated={(urls.enquiry or '')[:200]!r}\n")
        log_fp.write(f"url_vehicle_truncated={(urls.vehicle or '')[:200]!r}\n")
        log_fp.write("\n--- trace ---\n")
        log_fp.write(
            "Legend: [STEP]/[NOTE]/[MILESTONE] = operator narrative; [FORM] = siebel_step + "
            "Siebel form/screen + action + fields/values being applied on that form.\n\n"
        )
        log_fp.flush()

    def _exec_log(prefix: str, msg: str) -> None:
        if not log_fp or not (msg or "").strip():
            return
        try:
            log_fp.write(f"{datetime.now(timezone.utc).isoformat()} [{prefix}] {msg}\n")
            log_fp.flush()
        except OSError:
            pass

    def form_trace(siebel_step: str, form_name: str, action: str, **fields: object) -> None:
        """Write one structured [FORM] line: step, screen/applet name, action, and field updates."""
        segments = [f"siebel_step={siebel_step}", f"form={form_name}", f"action={action}"]
        for key in sorted(fields.keys()):
            val = fields[key]
            if val is None:
                continue
            if isinstance(val, bool):
                segments.append(f"{key}={val}")
                continue
            v = str(val).replace("\n", " ").strip()
            if v == "":
                continue
            if len(v) > 500:
                v = v[:497] + "..."
            segments.append(f"{key}={v!r}")
        _exec_log("FORM", " | ".join(segments))

    def ms_done(label: str) -> None:
        m = out["dms_milestones"]
        if label not in m:
            m.append(label)
            _exec_log("MILESTONE", label)

    def step(msg: str) -> None:
        """Ordered user-facing progress (Add Sales banner)."""
        if msg and (not out["dms_step_messages"] or out["dms_step_messages"][-1] != msg):
            out["dms_step_messages"].append(msg)
        _exec_log("STEP", msg)

    def note(msg: str) -> None:
        out["dms_siebel_notes"].append(msg)
        logger.info("siebel_dms: %s", msg)
        _exec_log("NOTE", msg)

    def log_vehicle_snapshot(stage: str) -> None:
        """
        Write current ``out['vehicle']`` key-values immediately after each scrape/merge update.
        Keeps Playwright_DMS.txt aligned with in-memory state evolution.
        """
        veh = out.get("vehicle") or {}
        if not log_fp or not isinstance(veh, dict):
            return
        try:
            log_fp.write(f"\n--- vehicle_snapshot ({stage}) ---\n")
            for k in sorted(veh.keys()):
                v = veh.get(k)
                if v is None:
                    continue
                s = str(v).replace("\n", " ").replace("\r", " ").strip()
                if not s:
                    continue
                if len(s) > 2000:
                    s = s[:1997] + "..."
                log_fp.write(f"{k}={s!r}\n")
            log_fp.flush()
        except OSError:
            pass

    customer_save_clicked = False

    def save_customer_record(msg_clicked: str, msg_missing: str) -> None:
        nonlocal customer_save_clicked
        if _try_click_siebel_save(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        ):
            customer_save_clicked = True
            note(msg_clicked)
        else:
            note(msg_missing)

    try:
        step("Started Hero Connect / Siebel DMS automation (linear SOP).")
        _fn_gate_ok, _fn_gate_msg = _validate_contact_find_first_name(first)
        if not _fn_gate_ok:
            step("Stopped: invalid or missing Contact First Name for Siebel automation.")
            out["error"] = _fn_gate_msg
            return out

        if dms_path == "skip_find" and not skip_contact_find:
            note(
                "dms_contact_path=skip_find in form data — real Siebel still runs Stage 1 Contact Find "
                "(mobile + Go) so the existing customer is loaded in the correct Siebel context."
            )

        contact_url = (urls.contact or "").strip()
        in_transit_state = False

        if SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES:
            # User-requested order: run vehicle preparation before Contact Find search.
            step("Pre-step: preparing vehicle before contact find (video path).")
            _pv_ok, _pv_err, _pv_scraped, in_transit_state, _pv_crit, _pv_info = prepare_vehicle(
                page,
                dms_values,
                urls,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                form_trace=form_trace,
                ms_done=ms_done,
                step=step,
            )
            if not _pv_ok:
                out["error"] = _pv_err or "prepare_vehicle failed before contact find."
                return out
            out["vehicle"] = _pv_scraped
            _write_playwright_vehicle_master_section(log_fp, _pv_scraped, _pv_crit, _pv_info)
            _persist_dms_scrape_to_db(customer_id, vehicle_id, out.get("vehicle") or {}, note)

            if skip_contact_find:
                note(
                    "SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES is True — skip_contact_find ignored; "
                    "using Find → All Enquiries video path."
                )
            if not mobile:
                step("Stopped: mobile_phone is required for Find Contact video path.")
                out["error"] = "Siebel: mobile_phone is empty — cannot run Find by mobile."
                return out
            if not contact_url:
                step("Stopped: DMS_REAL_URL_CONTACT is not configured.")
                out["error"] = (
                    "Siebel: set DMS_REAL_URL_CONTACT to the Contact / Find (or Visible Contact List for Find) "
                    "GotoView URL so the video SOP can open the Find applet."
                )
                return out
            video_first_name = first.strip()
            step(
                "Video SOP (Find Contact Enquiry): Find → Contact → mobile + first name → Go; "
                "branch A when N=0 (Add Enquiry) else title sweep for Open enquiry; branch (2) Address+pin "
                "when no Open; Relation's Name → Payments → booking path."
            )
            form_trace(
                "v1_find_contact",
                "Global Find → Contact (Mobile + First Name) + Go",
                "goto_contact_find_URL_then_prepare_Find_Contact_fill_mobile_first_FindGo",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
                first_name=video_first_name,
            )
            ok_find = _contact_view_find_by_mobile(
                page,
                contact_url=contact_url,
                mobile=mobile,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                mobile_aria_hints=mobile_aria_hints,
                note=note,
                step=step,
                stage_msg="Video SOP: Find customer by mobile + first name (Contact view).",
                first_name=video_first_name,
            )
            if not ok_find:
                step("Stopped: could not complete Find by mobile + first name on contact view.")
                out["error"] = (
                    "Siebel: video SOP — could not fill mobile/first name or run Find/Go on the contact view. "
                    "Check Find pane, iframe selectors, and DMS_SIEBEL_* tuning."
                )
                return out
            _grid_first_hint = _siebel_ui_suggests_contact_match_mobile_first(
                page, mobile, video_first_name
            )
            note(
                f"DECISION: contact_table_match_mobile_first_after_find={_grid_first_hint!r} "
                "(informational; branch A/B uses drilldown row count)."
            )

            n_drilldown = _contact_find_mobile_drilldown_occurrence_count(
                page,
                mobile,
                content_frame_selector=content_frame_selector,
                first_name_exact=None,
            )
            note(
                f"Video path: Contact Find drilldown row count N={n_drilldown} "
                "(mobile-only basis for branch A/B)."
            )

            if n_drilldown == 0:
                note(
                    "No contact drilldown rows (branch A) — Add Enquiry with base first name "
                    "(vehicle + Opportunities + Ctrl+S)."
                )
                ae_ok, ae_detail, ae_enq_no = _add_enquiry_opportunity(
                    page,
                    dms_values,
                    urls,
                    action_timeout_ms=action_timeout_ms,
                    nav_timeout_ms=nav_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                    form_trace=form_trace,
                    vehicle_merge=out.setdefault("vehicle", {}),
                )
                if not ae_ok:
                    step("Stopped: Add Enquiry branch failed (zero drilldown contacts).")
                    out["error"] = (
                        "Siebel: video SOP — no contact drilldown rows and Add Enquiry did not complete. "
                        f"{ae_detail or 'See Playwright_DMS.txt [NOTE] lines for the failing step.'}"
                    )
                    return out
                if not (ae_enq_no or "").strip():
                    step("Stopped: Add Enquiry did not return Enquiry#.")
                    out["error"] = (
                        "Siebel: Add Enquiry details were filled but no Enquiry# was scraped. "
                        "Treating as failure to avoid silent partial save."
                    )
                    return out
                ms_done("Add enquiry saved")
                note(f"Add Enquiry saved with Enquiry#={ae_enq_no!r}; re-finding by mobile + first name.")
                out.setdefault("vehicle", {})["enquiry_number"] = ae_enq_no
                log_vehicle_snapshot("video_add_enquiry_saved")
                _persist_dms_scrape_to_db(customer_id, vehicle_id, out.get("vehicle") or {}, note)
                form_trace(
                    "v1b_refind_after_add_enquiry",
                    "Global Find → Contact (Mobile + First Name) + Go",
                    "rerun_find_mobile_first_after_add_enquiry",
                    contact_url_truncated=contact_url[:200],
                    mobile_phone=mobile,
                    first_name=video_first_name,
                )
                ok_refind = _contact_view_find_by_mobile(
                    page,
                    contact_url=contact_url,
                    mobile=mobile,
                    nav_timeout_ms=nav_timeout_ms,
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    mobile_aria_hints=mobile_aria_hints,
                    note=note,
                    step=step,
                    stage_msg="Post Add Enquiry: re-find customer by mobile + first name (Contact view).",
                    first_name=video_first_name,
                )
                if not ok_refind:
                    step("Stopped: Add Enquiry saved but post-save re-find failed.")
                    out["error"] = (
                        "Siebel: Add Enquiry was saved, but the follow-up Find→Contact mobile+first query "
                        "did not complete."
                    )
                    return out
                n_drilldown = _contact_find_mobile_drilldown_occurrence_count(
                    page,
                    mobile,
                    content_frame_selector=content_frame_selector,
                    first_name_exact=None,
                )
                note(f"Video path: after Add Enquiry, drilldown row count N={n_drilldown}.")
                if n_drilldown == 0:
                    step("Stopped: Add Enquiry saved but Find still shows no drilldown contact rows.")
                    out["error"] = (
                        "Siebel: Add Enquiry saved but contact search shows no drillable rows after re-find."
                    )
                    return out
                strict_m = _siebel_ui_suggests_contact_match_mobile_first(
                    page, mobile, video_first_name
                )
                note(f"DECISION: contact_table_match_after_add_enquiry_refind={strict_m!r}")
                if not strict_m:
                    note(
                        "Post Add Enquiry: strict mobile+first not visible on grid — continuing with "
                        "drilldown rows only."
                    )

            _video_snap_fn = (video_first_name or "").strip()
            _video_list_snapshot_counts = _find_contact_mobile_first_grid_counts(
                page, mobile, _video_snap_fn, content_frame_selector=content_frame_selector
            )
            _video_strict_first = _contact_find_mobile_drilldown_occurrence_count(
                page,
                mobile,
                content_frame_selector=content_frame_selector,
                first_name_exact=_video_snap_fn or None,
            )
            note(
                "Find-Contact list snapshot (before Title/enquiry sweep): "
                f"{_video_list_snapshot_counts[0]} row(s) with mobile and drilldown "
                f"(same basis as title sweep ordinals); "
                f"{_video_list_snapshot_counts[1]} with enquiry hint in list text; "
                f"optional strict list row match for first name {_video_snap_fn!r}: {_video_strict_first}."
            )

            sweep_has_open, sweep_enq_no, sweep_enq_rows, _sweep_err = _contact_find_title_sweep_for_enquiry(
                page,
                mobile=mobile,
                first_name=video_first_name,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                mobile_aria_hints=mobile_aria_hints,
                note=note,
                step=step,
            )
            contacts_with_open = (
                1
                if (
                    sweep_has_open
                    and ((sweep_enq_no or "").strip() or int(sweep_enq_rows or 0) > 0)
                )
                else 0
            )
            note(
                f"Video path: drilldown_rows_N={n_drilldown}, "
                f"contacts_with_open_enquiry={contacts_with_open} (Siebel rule: 0 or 1)."
            )

            if _sweep_err:
                step(f"Stopped: {_sweep_err}")
                out["error"] = _sweep_err
                return out

            if sweep_has_open and (sweep_enq_no or "").strip():
                out.setdefault("vehicle", {})["enquiry_number"] = (sweep_enq_no or "").strip()
                log_vehicle_snapshot("video_enquiry_found_in_contact_enquiry")

            if not sweep_has_open:
                note(
                    "Video branch (2): no open enquiry — re-find and drill first contact "
                    "before Relation's Name path."
                )
                if not _contact_view_find_by_mobile(
                    page,
                    contact_url=contact_url,
                    mobile=mobile,
                    nav_timeout_ms=nav_timeout_ms,
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    mobile_aria_hints=mobile_aria_hints,
                    note=note,
                    step=step,
                    stage_msg="Branch (2): re-find for first drilldown contact.",
                    first_name=video_first_name,
                ):
                    step("Stopped: branch (2) re-find failed.")
                    out["error"] = "Siebel: video branch (2) could not re-find contact after sweep."
                    return out
                fn0 = (video_first_name or "").strip()
                _dr2 = _click_nth_mobile_title_drilldown(
                    page,
                    mobile,
                    0,
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    first_name_exact=fn0 if fn0 else None,
                )
                if not _dr2:
                    _dr2 = _siebel_try_click_mobile_search_hit_link(
                        page,
                        mobile,
                        timeout_ms=action_timeout_ms,
                        content_frame_selector=content_frame_selector,
                    )
                if not _dr2:
                    step("Stopped: branch (2) could not drill first contact row.")
                    out["error"] = (
                        "Siebel: video branch (2) — no open enquiry; could not open first drilldown contact."
                    )
                    return out
                _safe_page_wait(page, 2000, log_label="after_title_drilldown_branch2")
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass

            form_trace(
                "v2_drill_and_nav",
                "Search Results + Contacts detail",
                "Siebel_Find_tab_optional_then_link_hit_then_click_first_name_then_fill_Relations_Name_only",
                mobile_phone=mobile,
                first_name=video_first_name,
                care_of=care_of,
            )
            if not _siebel_video_path_after_find_go_to_all_enquiries(
                page,
                mobile=mobile,
                first_name=video_first_name,
                care_of=care_of,
                address_line_1=addr,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                skip_search_hit_click=True,
            ):
                step("Stopped: video SOP failed while opening customer record or filling Relation's Name.")
                out["error"] = (
                    "Siebel: video SOP — after Find/Go, could not fill Relation's Name from care_of. "
                    "Confirm right-pane selectors/labels and iframe scope."
                )
                return out

            if not sweep_has_open:
                if not _siebel_video_branch2_address_postal_and_save(
                    page,
                    pin_code=pin,
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                ):
                    step("Stopped: video branch (2) Address / Postal Code / Save failed.")
                    out["error"] = (
                        "Siebel: no open enquiry path — could not fill Address Postal Code or save."
                    )
                    return out

            # Scrape Contact ID: detail inputs + Contacts grid **Contact Id** column (e.g. 11870-01-SCON-…).
            _contact_id = ""
            _cid_js = """() => {
                const vis = (el) => {
                  if (!el) return false;
                  const st = window.getComputedStyle(el);
                  if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                  const r = el.getBoundingClientRect();
                  return r.width > 2 && r.height > 2;
                };
                const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const sels = [
                  "input[aria-label='Contact Id']",
                  "[aria-labelledby='s_1_l_HHML_Contact_Seq_Num']",
                  "input[aria-label*='Contact Id' i]",
                  "input[name*='Contact_Id' i]",
                  "input[name*='HHML_Contact' i]",
                  "input[id*='Contact_Id' i]",
                ];
                for (const sel of sels) {
                  const el = document.querySelector(sel);
                  if (!el || !vis(el)) continue;
                  const v = (el.value != null ? String(el.value) : (el.textContent || '')).trim();
                  if (v && v.length > 2) return v;
                }
                for (const app of document.querySelectorAll('.siebui-applet')) {
                  if (!vis(app)) continue;
                  const blob = (app.innerText || '').toLowerCase();
                  if (!blob.includes('contact')) continue;
                  const table = app.querySelector('table');
                  if (!table) continue;
                  const heads = Array.from(table.querySelectorAll('thead th, thead td, tr th'));
                  let idx = -1;
                  heads.forEach((h, i) => {
                    const ht = norm(h.innerText || '');
                    if (idx < 0 && (ht === 'contact id' || ht.includes('contact id'))) idx = i;
                  });
                  if (idx < 0) continue;
                  const rows = Array.from(table.querySelectorAll('tbody tr, tr')).filter(vis);
                  for (const tr of rows) {
                    if (!vis(tr)) continue;
                    const cells = tr.querySelectorAll('td');
                    if (idx >= cells.length) continue;
                    const cell = cells[idx];
                    if (!vis(cell)) continue;
                    const a = cell.querySelector('a');
                    const raw = ((a && a.textContent) ? a.textContent : (cell.textContent || '')).trim();
                    if (raw && raw.length > 5 && (/scon/i.test(raw) || /^\\d+-\\d+-/i.test(raw))) return raw;
                  }
                }
                return '';
            }"""
            for _cr in _ordered_frames(page):
                try:
                    _cid = _cr.evaluate(_cid_js)
                    if _cid:
                        _contact_id = str(_cid).strip()
                        break
                except Exception:
                    continue
            if _contact_id:
                note(f"Scraped Contact ID={_contact_id!r} from contact detail page.")
                out["contact_id"] = _contact_id
            else:
                note("Contact ID not found on contact detail page (best-effort).")

            _write_playwright_contact_scrape_section(
                log_fp,
                out,
                had_open_enquiry_from_sweep=sweep_has_open,
            )

            form_trace(
                "v3_add_customer_payment",
                "Payments tab (current frame)",
                "click_Payments_tab_then_click_plus_icon",
            )
            if not _add_customer_payment(
                page,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
            ):
                step("Stopped: could not open Payments tab or click '+' icon.")
                out["error"] = (
                    "Siebel: video SOP — could not click Payments tab and '+' icon for Add customer payment."
                )
                return out

            full_chassis = (
                str((out.get("vehicle") or {}).get("full_chassis") or "").strip()
                or str(dms_values.get("full_chassis") or "").strip()
                or str(dms_values.get("frame_num") or "").strip()
            )
            # If there is no open order for this customer, try Generate Booking before Sales Orders.
            _enq_u = (urls.enquiry or "").strip() or (urls.contact or "").strip()
            if _enq_u:
                _goto(page, _enq_u, "enquiry_for_booking_video", nav_timeout_ms=nav_timeout_ms)
                _siebel_after_goto_wait(page, floor_ms=900)
            _safe_page_wait(page, 500, log_label="before_generate_booking_video")
            if _try_click_generate_booking(
                page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
            ):
                note("Video path: clicked Generate Booking before create_order.")
                ms_done("Booking generated")
            else:
                step("Stopped: Generate Booking was not found before create_order (video path).")
                out["error"] = (
                    "Siebel: Generate Booking control was not found before create_order. "
                    "Booking is mandatory when no existing order is present."
                )
                return out

            form_trace(
                "v4_create_order",
                "Vehicle Sales / Sales Orders",
                "vehicle_sales_new_order_then_pick_contact_then_vin_search_price_allocate",
                mobile_phone=mobile,
                first_name=video_first_name,
                full_chassis=full_chassis,
            )
            # #region agent log — create_order call inputs
            try:
                _fin_name_raw = (dms_values.get("financier_name") or "").strip()
                _fin_tok = _fin_name_raw.lower()
                _fr_raw = (dms_values.get("finance_required") or "").strip().upper()
                with open("debug-08e634.log", "a", encoding="utf-8") as _lf:
                    import json as _j_co, time as _t_co
                    _lf.write(_j_co.dumps({
                        "sessionId": "08e634",
                        "runId": "pre-fix",
                        "hypothesisId": "H9",
                        "location": "siebel_dms_playwright.py:Playwright_Hero_DMS_fill_create_order_inputs",
                        "message": "Inputs passed to create_order",
                        "data": {
                            "finance_required_raw": _fr_raw if _fr_raw in ("Y", "N", "") else "OTHER",
                            "financier_present": bool(_fin_name_raw),
                            "financier_len": len(_fin_name_raw),
                            "financier_token": _fin_tok if _fin_tok in ("", "na", "n/a", "null", "none", "-") else "other",
                        },
                        "timestamp": int(_t_co.time() * 1000),
                    }) + "\n")
            except Exception:
                pass
            # #endregion
            ok_order, order_err, order_scraped = _create_order(
                page,
                mobile=mobile,
                first_name=video_first_name,
                full_chassis=full_chassis,
                financier_name=(dms_values.get("financier_name") or "").strip(),
                contact_id=out.get("contact_id", ""),
                battery_partial=(dms_values.get("battery_partial") or "").strip(),
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                form_trace=form_trace,
            )
            if not ok_order:
                step("Stopped: create_order flow failed.")
                out["error"] = f"Siebel: create_order failed. {order_err or ''}".strip()
                return out

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
                if order_scraped.get("cubic_capacity"):
                    veh["cubic_capacity"] = order_scraped.get("cubic_capacity")
                if order_scraped.get("vehicle_type"):
                    veh["vehicle_type"] = order_scraped.get("vehicle_type")
                out["vehicle"] = veh
                log_vehicle_snapshot("video_create_order_scrape_merge")
                _persist_dms_scrape_to_db(customer_id, vehicle_id, out.get("vehicle") or {}, note)

            step(
                "Video SOP complete: customer record opened, payment added, and create_order flow completed. "
                "Automation stops here (SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES); browser left open."
            )
            note("Relation's Name/Address/Pincode, payment entry, and create_order flow completed; automation stops now.")
            return out

        # --- Full linear SOP (stages 1–8): runs only when SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES is False. ---

        def fill_relation_name_from_care_of(customer_was_found: bool = False) -> None:
            if customer_was_found:
                form_trace(
                    "1_find_contact",
                    "Search Results (left) + Contacts applet (right)",
                    "click_customer_in_left_pane_then_click_first_name_to_open_record",
                    mobile_phone=mobile,
                    first_name=first,
                )
                opened = _siebel_open_found_customer_record(
                    page,
                    mobile=mobile,
                    first_name=first,
                    timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                )
                if opened:
                    note("Opened existing customer record: left hit clicked, then first-name link clicked.")
                else:
                    note(
                        "Customer match found but could not open record by left-hit/first-name click; "
                        "continuing with matched flow."
                    )
            note("Stage 4: fill Relation's Name from DB care_of only (no relation type).")
            step("Adding care-of only (stage 4 — mandatory after find / re-find).")
            form_trace(
                "4_care_of",
                "Contact / Enquiry applet (Father–Husband + Relation line)",
                "fill_relation_name_from_care_of_only_simple",
                care_of_source=care_of,
            )
            care_val = (care_of or "").strip()
            filled_rel_name = False
            if care_val:
                fill_js = """(value) => {
                  const vis = (el) => {
                    if (!el) return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 2 && r.height >= 2;
                  };
                  const norm = (s) => String(s || '').replace(/\\s+/g,' ').trim().toLowerCase();
                  const lblNorm = (s) => norm(s).replace(/\\s*:\\s*$/, '');

                  const labels = Array.from(document.querySelectorAll('td,th,label,span,div'))
                    .filter(vis)
                    .map(el => ({ el, t: lblNorm(el.innerText || el.textContent || '') }));

                  const targetLbl = labels.find(x => x.t === \"relation's name\");
                  if (!targetLbl) return { ok: false, reason: 'label_not_found' };

                  const row = targetLbl.el.closest('tr') || targetLbl.el.closest('[role=\"row\"]') || null;
                  const scope = row || document;

                  const controls = Array.from(scope.querySelectorAll('input,textarea'))
                    .filter(vis)
                    .filter(el => {
                      const t = (el.getAttribute('type') || '').toLowerCase();
                      if (t && ['hidden','submit','button','checkbox','radio','file','image'].includes(t)) return false;
                      return true;
                    });
                  if (!controls.length) return { ok: false, reason: 'control_not_found' };

                  const lr = targetLbl.el.getBoundingClientRect();
                  let best = null;
                  let bestScore = 1e18;
                  for (const c of controls) {
                    const r = c.getBoundingClientRect();
                    const dy = Math.abs((r.top + r.height/2) - (lr.top + lr.height/2));
                    const dx = r.left - lr.right;
                    if (dy > 28) continue;
                    if (dx < -10) continue;
                    const score = dx + dy * 5;
                    if (score < bestScore) { bestScore = score; best = c; }
                  }
                  if (!best) return { ok: false, reason: 'no_candidate' };

                  try {
                    best.focus();
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
                    const ok = after && (after.includes(want) || want.includes(after));
                    return { ok, after, want };
                  } catch (e) {
                    return { ok: false, reason: 'verify_failed' };
                  }
                }"""
                for frame in _ordered_frames(page):
                    try:
                        res = frame.evaluate(fill_js, care_val)
                        if isinstance(res, dict) and res.get("ok") is True:
                            filled_rel_name = True
                            _safe_page_wait(page, 120, log_label="after_relation_name_care_of_inline_fill")
                            break
                    except Exception:
                        continue
            if filled_rel_name:
                ms_done("Care of filled")
            form_trace("4_care_of", "same applet", "click_Save_or_Commit_toolbar_after_care_of")
            save_customer_record(
                "Stage 4: Save after care-of update.",
                "Stage 4: Save not detected after care-of update.",
            )
            step("Care-of step completed (stage 4).")

        def find_customer() -> tuple[bool, bool]:
            if not contact_url:
                step("Stopped: DMS_REAL_URL_CONTACT is not configured.")
                out["error"] = (
                    "Siebel: set DMS_REAL_URL_CONTACT to the Contact / Find view GotoView URL "
                    "so mobile search can run (stage 1)."
                )
                return False, False
            form_trace(
                "1_find_contact",
                "Contact view — Find pane (mobile + first name search)",
                "goto_DMS_REAL_URL_CONTACT_expand_Find_fill_Mobile_FirstName_click_FindGo",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
                first_name=first.strip(),
            )
            ok = _contact_view_find_by_mobile(
                page,
                contact_url=contact_url,
                mobile=mobile,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                mobile_aria_hints=mobile_aria_hints,
                note=note,
                step=step,
                stage_msg="Stage 1: Find customer by mobile + first name (Contact view).",
                first_name=first.strip(),
            )
            if not ok:
                step("Stopped: mobile field not found on contact view — check Find pane and iframe selectors.")
                out["error"] = (
                    "Siebel: could not find a mobile/cellular phone input on the contact view. "
                    "Open the Find pane (right side), set object type to Contact if needed. "
                    "Tune env: DMS_SIEBEL_CONTENT_FRAME_SELECTOR (chain iframes with >>, outer to inner), "
                    "DMS_SIEBEL_AUTO_IFRAME_SELECTORS (comma-separated iframe CSS), "
                    "DMS_SIEBEL_POST_GOTO_WAIT_MS (longer wait after goto), "
                    "or DMS_SIEBEL_MOBILE_ARIA_HINTS (substrings matching the visible field label)."
                )
                return False, False
            note("Stage 1: Find/Go completed for mobile search.")
            step("Stage 1 complete: customer search ran on the mobile number.")
            if dms_path == "new_enquiry":
                note("DECISION: dms_contact_path=new_enquiry — treating as not matched; stage 2 will run.")
                return True, False
            matched = _siebel_ui_suggests_contact_match_mobile_first(page, mobile, first.strip())
            note(
                f"DECISION: customer_found_from_contact_grid={matched!r} "
                f"(mobile + exact first name in table row with ≥3 cells)."
            )
            if matched:
                ms_done("Customer found")
                note("Stage 1: table/grid suggests an existing contact match.")
            else:
                note("Stage 1: no table/grid match — will create basic enquiry (stage 2).")
            return True, matched

        def stage_2_create_enquiry_if_needed(matched: bool) -> bool:
            if matched:
                note("Stage 2: skipped — existing contact found (no new basic enquiry).")
                step("Stage 2 skipped: contact already exists from first search.")
                return False
            note("Stage 2: basic enquiry only (name, address, state, PIN — no care-of on this step).")
            step("Creating new enquiry with basic details only (stage 2).")
            form_trace(
                "2_basic_enquiry",
                "New enquiry / Contact main form (basic customer fields only)",
                "fill_FirstName_LastName_Address_State_PIN",
                first_name=first,
                last_name=last,
                address_line_1=(addr[:220] + "…") if len(addr) > 220 else addr,
                state=state,
                pin_code=pin,
            )
            _fill_basic_enquiry_details(
                page,
                first=first,
                last=last,
                addr=addr,
                state=state,
                pin=pin,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
            form_trace("2_basic_enquiry", "same form", "click_Save_or_Commit_toolbar_after_basic_fields")
            save_customer_record(
                "Stage 2: Save after basic enquiry details.",
                "Stage 2: Save not detected after basic enquiry details.",
            )
            ms_done("Enquiry created")
            step("Stage 2 complete: basic enquiry saved.")
            return True

        def stage_3_refind_customer(enquiry_was_created: bool) -> bool:
            if not enquiry_was_created:
                note("Stage 3: skipped — no new enquiry (re-find mandatory only after new basic enquiry).")
                step("Stage 3 skipped: re-find not required when contact already existed.")
                return True
            form_trace(
                "3_refind_after_new_enquiry",
                "Contact view — Find pane",
                "goto_contact_fill_mobile_again_FindGo_to_open_saved_record",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
            )
            ok = _refind_customer_after_enquiry(
                page,
                contact_url=contact_url,
                mobile=mobile,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                mobile_aria_hints=mobile_aria_hints,
                note=note,
                step=step,
                first_name=first.strip(),
            )
            if not ok:
                out["error"] = (
                    "Siebel: mandatory re-find (stage 3) failed — could not fill mobile on Contact view "
                    "after saving the basic enquiry. Check Find pane and iframe selectors."
                )
                return False
            note("Stage 3: mandatory re-find by mobile completed.")
            step("Stage 3 complete: re-found customer after enquiry save.")
            return True

        def stage_5_vehicle_flow() -> bool:
            """
            Vehicle list search/scrape; **dealer** path runs tab Pre-check/PDI on the vehicle form; if grid
            suggests In Transit → receipt / Process Receipt only (Pre-check/PDI skipped — Siebel rejects until
            dealer stock). Delegates to ``prepare_vehicle``. Sets ``in_transit_state`` and ``out["vehicle"]``.
            Returns False on configuration or vehicle-search failure (``out["error"]`` set).
            """
            nonlocal in_transit_state
            note("Stage 5: vehicle list search, scrape, and In-Transit handling.")
            step("Vehicle flow: key / chassis / engine search (stage 5).")
            ok, err, scraped, in_transit_state, vm_crit, vm_info = prepare_vehicle(
                page,
                dms_values,
                urls,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                form_trace=form_trace,
                ms_done=ms_done,
                step=step,
            )
            if not ok:
                out["error"] = err or "prepare_vehicle failed."
                return False
            out["vehicle"] = scraped
            _write_playwright_vehicle_master_section(log_fp, scraped, vm_crit, vm_info)
            out["dms_siebel_forms_filled"] = bool(customer_save_clicked)
            if not customer_save_clicked:
                note(
                    "Siebel Save was not detected on the customer/enquiry step — vehicle search still ran; "
                    "verify the contact record in Hero Connect. dms_siebel_forms_filled=false for API consumers."
                )
            step("Stage 5: vehicle list query completed; result row read when present.")
            return True

        def stage_6_generate_booking() -> bool:
            note("Stage 6: Generate Booking (always after vehicle processing per SOP).")
            step("Generate Booking (stage 6 — always, regardless of In Transit).")
            enq_u = (urls.enquiry or "").strip() or (urls.contact or "").strip()
            form_trace(
                "6_generate_booking",
                "Enquiry / My Enquiries (or Contact fallback)",
                "navigate_if_configured_then_click_Generate_Booking_toolbar",
                target_url_truncated=enq_u[:200] if enq_u else "",
            )
            if enq_u:
                _goto(page, enq_u, "enquiry_for_booking", nav_timeout_ms=nav_timeout_ms)
                _siebel_after_goto_wait(page, floor_ms=1200)
            else:
                note("Stage 6: no DMS_REAL_URL_ENQUIRY or DMS_REAL_URL_CONTACT — booking may be on current view.")

            _safe_page_wait(page, 800, log_label="before_generate_booking")
            form_trace("6_generate_booking", "current Siebel view", "click_Generate_Booking_toolbar_pattern_match")
            if _try_click_generate_booking(
                page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
            ):
                note("Stage 6: clicked Generate Booking.")
                ms_done("Booking generated")
                step("Generate Booking was completed (stage 6).")
                return True
            else:
                note("Stage 6: Generate Booking control not found or not visible.")
                step("Stopped: Generate Booking was not found (stage 6).")
                out["error"] = (
                    "Siebel: Generate Booking control was not found. "
                    "Booking is mandatory when no existing order is present."
                )
                return False

        def stage_7_allotment_if_applicable() -> None:
            if in_transit_state:
                note("Stage 7: Price All / Allocate skipped (In Transit path).")
                step("Allotment skipped — vehicle was In Transit (stage 7).")
                return
            note("Stage 7: order line / allotment (non–In Transit only, after booking).")
            step("Opening allotment / line items after booking (stage 7).")
            line_u = (urls.line_items or "").strip()
            form_trace(
                "7_allotment",
                "Order line / Allotment (DMS_REAL_URL_LINE_ITEMS)",
                "navigate_then_Price_All_and_Allocate_toolbars_if_present",
                line_items_url_truncated=line_u[:200] if line_u else "",
            )
            if line_u:
                _goto(page, line_u, "line_items_allotment", nav_timeout_ms=nav_timeout_ms)
                _siebel_after_goto_wait(page, floor_ms=1200)
                ms_done("Allotment view opened")
                step("Allotment / order line view opened.")
                if _try_click_price_all(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
                    note("Clicked Price All (best-effort).")
                    step("Price All was clicked.")
                if _try_click_allocate_line(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
                    note("Clicked Allocate / Allocate All.")
                    ms_done("Vehicle allocated")
                    step("Allocation was completed (Allocate / Allocate All).")
                else:
                    note("Allocate / Allocate All not found; operator may allocate manually.")
                    step("Allocation control was not found — complete allocation manually if required.")
            else:
                note("DMS_REAL_URL_LINE_ITEMS not set; skipping allotment view.")
                step("Line items / allotment URL is not set — skipped allocation in UI.")

        def stage_8_invoice_hook() -> None:
            form_trace(
                "8_invoice",
                "(no Siebel form — operator completes manually)",
                "automation_hook_only_no_field_updates",
            )
            note("Invoice step pending (not automated).")
            step("Ready for invoice creation.")

        if not skip_contact_find:
            ok1, matched1 = find_customer()
            if not ok1:
                return out
            created_basic = False
            if not matched1:
                note("Stage 1: no contact table match — trying Add Enquiry (vehicle + Opportunities).")
                _ae_ok, _ae_det, _ae_enq = _add_enquiry_opportunity(
                    page,
                    dms_values,
                    urls,
                    action_timeout_ms=action_timeout_ms,
                    nav_timeout_ms=nav_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                    form_trace=form_trace,
                    vehicle_merge=out.setdefault("vehicle", {}),
                )
                if _ae_ok:
                    created_basic = True
                    ms_done("Add enquiry saved")
                    if _ae_enq:
                        out.setdefault("vehicle", {})["enquiry_number"] = _ae_enq
                    log_vehicle_snapshot("linear_add_enquiry_saved")
                    _persist_dms_scrape_to_db(customer_id, vehicle_id, out.get("vehicle") or {}, note)
                else:
                    note("Add Enquiry branch failed — falling back to basic enquiry form (stage 2).")
                    created_basic = stage_2_create_enquiry_if_needed(matched1)
            else:
                created_basic = stage_2_create_enquiry_if_needed(matched1)
            if not stage_3_refind_customer(created_basic):
                return out
            fill_relation_name_from_care_of(matched1)
        else:
            enquiry_url = (urls.enquiry or "").strip() or (urls.contact or "").strip()
            if not enquiry_url:
                out["error"] = (
                    "Siebel skip_find: set DMS_REAL_URL_ENQUIRY or DMS_REAL_URL_CONTACT to the "
                    "enquiry view (e.g. Buyer/CoBuyer My Enquiries) so the customer can be added "
                    "before vehicle search."
                )
                return out
            note("skip_find: stage 1 (Find) bypassed — staged basic enquiry → re-find → care-of.")
            step("skip_find path: enquiry view opened.")
            form_trace(
                "skip_find_open_enquiry",
                "Enquiry / My Enquiries (or Contact fallback)",
                "goto_enquiry_URL_before_mobile_and_basic_fields",
                enquiry_url_truncated=enquiry_url[:200],
            )
            _goto(page, enquiry_url, "enquiry_or_contact", nav_timeout_ms=nav_timeout_ms)
            _siebel_after_goto_wait(page, floor_ms=1400)

            form_trace(
                "skip_find_mobile_on_enquiry",
                "Enquiry / customer form (mobile field)",
                "fill_Mobile_Phone_on_enquiry_applet",
                mobile_phone=mobile,
            )
            form_mobile_ok = _try_fill_mobile_on_enquiry_form(
                page,
                mobile,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                mobile_aria_hints=mobile_aria_hints,
            )
            if not form_mobile_ok:
                out["error"] = (
                    "Siebel skip_find: could not fill mobile on the enquiry/customer form "
                    "(or Mobile Phone # is missing in DMS fill values). "
                    "Set DMS_SIEBEL_CONTENT_FRAME_SELECTOR (use >> to chain iframes), "
                    "DMS_SIEBEL_AUTO_IFRAME_SELECTORS, DMS_SIEBEL_POST_GOTO_WAIT_MS, "
                    "or DMS_SIEBEL_MOBILE_ARIA_HINTS if needed."
                )
                return out

            note("skip_find stage 2: basic enquiry details only (no care-of).")
            form_trace(
                "skip_find_basic_enquiry",
                "Enquiry / customer form (basic fields)",
                "fill_FirstName_LastName_Address_State_PIN",
                first_name=first,
                last_name=last,
                address_line_1=(addr[:220] + "…") if len(addr) > 220 else addr,
                state=state,
                pin_code=pin,
            )
            _fill_basic_enquiry_details(
                page,
                first=first,
                last=last,
                addr=addr,
                state=state,
                pin=pin,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
            if landline:
                form_trace(
                    "skip_find_basic_enquiry",
                    "same form",
                    "fill_landline_or_alternate_phone_if_configured",
                    landline=(landline[:80] + "…") if len(landline) > 80 else landline,
                )
                dup = True
                _try_fill_field(
                    page,
                    [
                        'input[aria-label*="Work Phone" i]',
                        'input[aria-label*="Alternate" i]',
                        'input[aria-label*="Landline" i]',
                    ],
                    landline,
                    timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    prefer_second_if_duplicate=dup,
                )
            form_trace("skip_find_basic_enquiry", "same form", "click_Save_or_Commit_toolbar_after_basic_fields")
            save_customer_record(
                "skip_find: Save after basic enquiry details.",
                "skip_find: Save not detected after basic enquiry details.",
            )
            ms_done("Enquiry created")
            _safe_page_wait(page, 800, log_label="after_skip_find_basic_save")
            if not contact_url:
                out["error"] = (
                    "Siebel skip_find: set DMS_REAL_URL_CONTACT for mandatory re-find (stage 3) after enquiry save."
                )
                return out
            form_trace(
                "skip_find_refind",
                "Contact view — Find pane",
                "goto_contact_fill_mobile_FindGo_after_enquiry_save",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
            )
            if not _refind_customer_after_enquiry(
                page,
                contact_url=contact_url,
                mobile=mobile,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                mobile_aria_hints=mobile_aria_hints,
                note=note,
                step=step,
                first_name=first.strip(),
            ):
                out["error"] = (
                    "Siebel skip_find: mandatory re-find failed — could not run Find by mobile on Contact view."
                )
                return out
            fill_relation_name_from_care_of(False)
            step("skip_find: stages 2–4 complete (basic → re-find → care-of).")

        if not stage_5_vehicle_flow():
            return out
        _persist_dms_scrape_to_db(customer_id, vehicle_id, out.get("vehicle") or {}, note)

        if not stage_6_generate_booking():
            return out
        stage_7_allotment_if_applicable()
        stage_8_invoice_hook()

    except PlaywrightTimeout as e:
        out["error"] = f"Siebel automation timeout: {e!s}"
        logger.warning("siebel_dms: PlaywrightTimeout %s", e)
    except RuntimeError as e:
        # e.g. browser/tab closed during ``_safe_page_wait`` — message is already operator-facing
        out["error"] = str(e)
        logger.warning("siebel_dms: %s", e)
    except Exception as e:
        out["error"] = f"Siebel automation error: {e!s}"
        logger.warning("siebel_dms: exception %s", e, exc_info=True)
    finally:
        out["dms_milestones"] = _sort_milestone_labels(list(out.get("dms_milestones") or []))
        if log_fp is not None:
            try:
                log_fp.write(
                    f"\n{datetime.now(timezone.utc).isoformat()} [END] "
                    f"error={out.get('error')!s}\n"
                )
            except OSError:
                pass
            try:
                log_fp.close()
            except OSError:
                pass

    return out


def run_hero_siebel_dms_flow(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    skip_contact_find: bool = False,
    execution_log_path: Path | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Backward-compatible alias for older callers.
    Prefer ``Playwright_Hero_DMS_fill`` for new integrations/modules.
    """
    return Playwright_Hero_DMS_fill(
        page,
        dms_values,
        urls,
        action_timeout_ms=action_timeout_ms,
        nav_timeout_ms=nav_timeout_ms,
        content_frame_selector=content_frame_selector,
        mobile_aria_hints=mobile_aria_hints,
        skip_contact_find=skip_contact_find,
        execution_log_path=execution_log_path,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
    )
