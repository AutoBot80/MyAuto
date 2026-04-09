"""
Hero Connect / Oracle Siebel Open UI — shared Playwright utilities.

Siebel-specific frame helpers, error popup handling, fill/click primitives,
hint config loaders, and execution log writers used by the vehicle, customer,
and invoice domain modules.
"""

from __future__ import annotations

import logging
import json
from typing import TextIO
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

logger = logging.getLogger(__name__)


def _hero_default_payment_lines_root_hint() -> dict[str, object]:
    """
    Built-in fast-path hint for Hero Connect **Contact → Payments** (Payment Lines applet).

    Uses **stable** URL fragments (no ``SWERowId`` / session tokens). When these stop matching
    after a Siebel upgrade, update this dict or set ``DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_*`` to override.
    See **LLD §2.4d.1**.
    """
    _tail = (
        "HHML+LS+CIM+Contact+Site+Payments+View&SWEApplet0=Contact+Form+Applet"
        "&SWEApplet1=Payment+List+Applet"
    )
    _top = (
        "SWECmd=GotoView&SWEView=HHML+LS+CIM+Contact+Site+Payments+View"
        "&SWEApplet1=Payment+List+Applet"
    )
    return {
        "schema_version": 1,
        "hint_source": "builtin",
        "dealer_id": "",
        "page_url_top": _top,
        "payment_lines_root_index_primary": 0,
        "ordered_frames_count": 0,
        "content_frame_selector": "",
        "receipts_field_name": "s_2_1_1_0",
        "playwright_package_version": "",
        "roots_sorted": [
            {
                "index": 0,
                "match_reason": "toolbar",
                "type": "Frame",
                "frame_url_tail": _tail,
                "frame_name": "",
                "iframe_element_title": "",
            }
        ],
    }


def _hero_default_mobile_search_hit_root_hint() -> dict[str, object]:
    """
    Built-in fast-path for **Contact Find** left **Search Results** mobile drilldown and for
    :func:`_contact_mobile_drilldown_plans` (same Hero view / grid).

    Stable URL fragments only (no ``SWERowId``). Update when Hero changes the Find Contact view.
    Optional env still overrides via :func:`_load_mobile_search_hit_hint_dict_from_config`.
    See **LLD §2.4d.2**.
    """
    _tail = (
        "SWEView=eAuto+Contact+Opportunity+Buyer/CoBuyer+View+(SDW)"
        "&SWEHo=&SWEBU=1&SWEApplet0=Opportunity+List+Applet"
    )
    _top = (
        "SWECmd=GotoView&SWEView=eAuto+Contact+Opportunity+Buyer/CoBuyer+View+(SDW)"
        "&SWEApplet0=Opportunity+List+Applet"
    )
    return {
        "schema_version": 1,
        "hint_source": "builtin",
        "page_url_top": _top,
        "mobile_search_hit_root_index_primary": 0,
        "roots_sorted": [
            {
                "frame_url_tail": _tail,
                "match_reason": "builtin_hero_contact_find_opportunity_list",
            }
        ],
    }


def _hero_default_contact_enquiry_subgrid_hint() -> dict[str, object]:
    """
    Built-in frame priority for **Contact_Enquiry** jqGrid eval after a contact drilldown
    (**Visible Contact List for Find Enquiry** view). Stable fragments only.
    Optional env overrides via :func:`_load_contact_enquiry_subgrid_hint_dict_from_config`.
    See **LLD §2.4d.4** / **6.101**.
    """
    _tail = (
        "SWEView=Visible+Contact+List+View+Clone+For+Find+Enquiry"
        "&SWEHo=&SWEBU=1&SWEApplet0=Contact+List+Applet+Clone+For+Find+Enquiry"
    )
    _top = (
        "SWECmd=GotoView&SWEView=Visible+Contact+List+View+Clone+For+Find+Enquiry"
        "&SWEApplet0=Contact+List+Applet+Clone+For+Find+Enquiry"
    )
    return {
        "schema_version": 1,
        "hint_source": "builtin",
        "page_url_top": _top,
        "roots_sorted": [
            {
                "frame_url_tail": _tail,
                "match_reason": "builtin_hero_contact_enquiry_clone_find",
            }
        ],
    }


# Siebel DMS and operator-entered dates/times are **IST** (Asia/Kolkata, UTC+05:30).
_SIEBEL_TZ = ZoneInfo("Asia/Kolkata")


def _siebel_ist_now() -> datetime:
    """Current wall-clock time in Asia/Kolkata (IST)."""
    return datetime.now(_SIEBEL_TZ)


def _ts_ist_iso() -> str:
    """ISO-8601 timestamps with +05:30 for Playwright_DMS and in-flow debug JSON (IST)."""
    return _siebel_ist_now().isoformat(timespec="milliseconds")


def _siebel_ist_today() -> date:
    """Calendar *today* in Asia/Kolkata (IST)."""
    return _siebel_ist_now().date()


def _siebel_naive_datetime_as_ist(dt: datetime) -> datetime:
    """Treat naive parsed datetimes as IST (Siebel shows local IST; no offset in cells)."""
    if dt.tzinfo is not None:
        return dt.astimezone(_SIEBEL_TZ)
    return dt.replace(tzinfo=_SIEBEL_TZ)


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
            "timestamp": _ts_ist_iso(),
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
        _err_kw = (
            "error",
            "required",
            "sbl-",
            "invalid",
            "cannot",
            "failed",
            "mandatory",
            "not valid",
            "missing",
            "exception",
            "unable",
            "must",
            "empty",
            "financier",
        )
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


_SIEBEL_JS_DISMISS_TOP_ERROR_DIALOG = """() => {
  const vis = (el) => {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const label = (el) =>
    (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().toLowerCase();
  const isOkish = (t) =>
    /^(ok|close|yes|dismiss|got\\s*it|continue)$/i.test(t) || t === 'ok' || t.startsWith('ok ');
  const clickIf = (btn) => {
    if (!btn || !vis(btn)) return false;
    if (!isOkish(label(btn))) return false;
    try {
      btn.click();
      return true;
    } catch (e) {}
    return false;
  };
  const shells = document.querySelectorAll(
    "[role='alertdialog'], [role='alert'], [role='dialog'], .ui-dialog, " +
    ".siebui-popup, .siebui-msg-popup, .siebui-popup-error, [id*='ErrorPopup' i], [id*='_swe_alert' i]"
  );
  for (const shell of shells) {
    if (!vis(shell)) continue;
    const cand = shell.querySelectorAll(
      'button, input[type="button"], input[type="submit"], a[role="button"], ' +
      '.ui-dialog-buttonpane button, .siebui-btn-ctrl, .siebui-popup-btn-ok, .siebui-btn-primary'
    );
    for (const b of cand) {
      if (clickIf(b)) return 'shell-btn';
    }
  }
  for (const b of document.querySelectorAll('.ui-dialog-buttonpane button, button.siebui-btn-primary')) {
    if (clickIf(b)) return 'global-btn';
  }
  return '';
}"""


def _try_dismiss_siebel_error_dialog(page: Page, content_frame_selector: str | None) -> bool:
    """
    Best-effort dismiss of the top Siebel / jQuery UI error or alert dialog (OK / Close / Yes).
    Tries each search root and main ordered frames; then Escape + Enter on the page.
    """
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
    seen: set[int] = set()
    for root in roots:
        k = id(root)
        if k in seen:
            continue
        seen.add(k)
        try:
            hit = root.evaluate(_SIEBEL_JS_DISMISS_TOP_ERROR_DIALOG)
            if hit:
                return True
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        _safe_page_wait(page, 120, log_label="after_siebel_error_escape")
    except Exception:
        pass
    try:
        page.keyboard.press("Enter")
        _safe_page_wait(page, 120, log_label="after_siebel_error_enter")
    except Exception:
        pass
    return False


def _poll_and_handle_siebel_error_popup(
    page: Page,
    content_frame_selector: str | None,
    note: Callable[..., object],
    *,
    context: str,
    total_ms: int = 1100,
    step_ms: int = 280,
) -> str | None:
    """
    After an action that may trigger Siebel validation errors, poll for a visible error/alert dialog.

    If found: log via ``note``, attempt to dismiss (OK/Close), return the message text so the caller
    can fail with a clear ``out["error"]``. Returns ``None`` if no popup was detected within the budget.
    """
    cap = max(200, min(int(total_ms), 8000))
    step = max(120, min(int(step_ms), 1200))
    deadline = time.monotonic() + cap / 1000.0
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            last = _detect_siebel_error_popup(page, content_frame_selector)
        except Exception:
            last = None
        if last:
            try:
                note(f"{context}: Siebel error popup → {last!r:.420}")
            except Exception:
                pass
            for _ in range(3):
                _try_dismiss_siebel_error_dialog(page, content_frame_selector)
                _safe_page_wait(page, 200, log_label="after_siebel_error_dismiss_try")
                try:
                    if not _detect_siebel_error_popup(page, content_frame_selector):
                        break
                except Exception:
                    break
            return last
        _safe_page_wait(page, step, log_label="poll_siebel_error_popup")
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

    **Main document first** — then frames whose URL matches the builtin (or optional env) enquiry
    subgrid hint **before** other iframes, then the rest of ``_ordered_frames`` so sweeps still
    find subgrids only inside an iframe.
    """
    main = page.main_frame
    hint = _load_contact_enquiry_subgrid_hint_dict_from_config()
    roots_sorted = hint.get("roots_sorted") if isinstance(hint, dict) else None
    page_top = str(hint.get("page_url_top") or "") if isinstance(hint, dict) else ""
    hinted: list[Frame] = []
    hinted_ids: set[int] = set()
    if isinstance(roots_sorted, list) and roots_sorted:
        for entry in roots_sorted:
            if not isinstance(entry, dict):
                continue
            for frame in _ordered_frames(page):
                fu = frame.url or ""
                if _frame_url_matches_payment_hint(fu, entry, page_top):
                    fid = id(frame)
                    if fid not in hinted_ids:
                        hinted_ids.add(fid)
                        hinted.append(frame)
                    break
    out: list[Frame] = [main]
    seen: set[int] = {id(main)}
    for f in hinted:
        if f != main and id(f) not in seen:
            seen.add(id(f))
            out.append(f)
    for f in _ordered_frames(page):
        if f != main and id(f) not in seen:
            seen.add(id(f))
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


def _siebel_locator_search_roots(page: Page, content_frame_selector: str | None):
    """Frames and chained ``FrameLocator`` roots (Siebel list is often inside inner iframes)."""
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        yield fl
    for frame in _ordered_frames(page):
        yield frame


def _load_payment_lines_hint_dict_from_config() -> dict[str, object]:
    """
    Payment Lines fast-path hint: optional **env / file** override; else **Hero built-in default**
    (``_hero_default_payment_lines_root_hint``).
    """
    try:
        from app.config import (
            DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE,
            DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_JSON,
        )
    except ImportError:
        return dict(_hero_default_payment_lines_root_hint())
    raw = ""
    fp = (DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_FILE or "").strip()
    if fp:
        try:
            p = Path(fp)
            if p.is_file():
                raw = p.read_text(encoding="utf-8")
        except OSError:
            raw = ""
    if not raw:
        raw = (DMS_SIEBEL_PAYMENT_LINES_ROOT_HINT_JSON or "").strip()
    if raw:
        try:
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
                raw = re.sub(r"\s*```\s*$", "", raw)
            d = json.loads(raw)
            if isinstance(d, dict) and int(d.get("schema_version") or 0) >= 1 and d.get("roots_sorted"):
                return d
        except Exception:
            pass
    return dict(_hero_default_payment_lines_root_hint())


def _frame_url_matches_payment_hint(fu: str, entry: dict[str, object], page_url_top: str) -> bool:
    """Loose URL match: tail substring and/or stable ``SWEView`` / ``SWEApplet1`` fragments from hint."""
    fu = fu or ""
    tail = str(entry.get("frame_url_tail") or "").strip()
    if tail and len(tail) >= 32:
        if tail in fu:
            return True
    top = page_url_top or ""
    if top:
        m = re.search(r"SWEView=([^&]+)", top)
        if m:
            frag = m.group(1)
            if len(frag) > 12 and frag in fu:
                return True
        m2 = re.search(r"SWEApplet1=([^&]+)", top)
        if m2:
            frag2 = m2.group(1)
            if len(frag2) > 8 and frag2 in fu:
                return True
        m3 = re.search(r"SWEApplet0=([^&]+)", top)
        if m3:
            frag3 = m3.group(1)
            if len(frag3) > 8 and frag3 in fu:
                return True
    return False


def _load_mobile_search_hit_hint_dict_from_config() -> dict[str, object]:
    """
    **Contact Find** Search Results / title-drilldown frame priority: optional **env / file** override;
    else **Hero built-in** (:func:`_hero_default_mobile_search_hit_root_hint`). Same JSON shape as
    Payment Lines (**``roots_sorted``**, **``page_url_top``**).
    """
    try:
        from app.config import (
            DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_FILE,
            DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_JSON,
        )
    except ImportError:
        return dict(_hero_default_mobile_search_hit_root_hint())
    raw = ""
    fp = (DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_FILE or "").strip()
    if fp:
        try:
            p = Path(fp)
            if p.is_file():
                raw = p.read_text(encoding="utf-8")
        except OSError:
            raw = ""
    if not raw:
        raw = (DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_JSON or "").strip()
    if raw:
        try:
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
                raw = re.sub(r"\s*```\s*$", "", raw)
            d = json.loads(raw)
            if isinstance(d, dict) and int(d.get("schema_version") or 0) >= 1 and d.get("roots_sorted"):
                return d
        except Exception:
            pass
    return dict(_hero_default_mobile_search_hit_root_hint())


def _load_contact_enquiry_subgrid_hint_dict_from_config() -> dict[str, object]:
    """
    **Contact_Enquiry** subgrid eval frame order: optional **env / file** override; else
    :func:`_hero_default_contact_enquiry_subgrid_hint`. Env keys mirror mobile search:
    **DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_FILE** / **DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_JSON**
    (optional; empty → builtin).
    """
    try:
        from app.config import (
            DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_FILE,
            DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_JSON,
        )
    except ImportError:
        return dict(_hero_default_contact_enquiry_subgrid_hint())
    raw = ""
    fp = (DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_FILE or "").strip()
    if fp:
        try:
            p = Path(fp)
            if p.is_file():
                raw = p.read_text(encoding="utf-8")
        except OSError:
            raw = ""
    if not raw:
        raw = (DMS_SIEBEL_CONTACT_ENQUIRY_SUBGRID_HINT_JSON or "").strip()
    if raw:
        try:
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
                raw = re.sub(r"\s*```\s*$", "", raw)
            d = json.loads(raw)
            if isinstance(d, dict) and int(d.get("schema_version") or 0) >= 1 and d.get("roots_sorted"):
                return d
        except Exception:
            pass
    return dict(_hero_default_contact_enquiry_subgrid_hint())


def _iter_siebel_root_search_order(
    page: Page,
    content_frame_selector: str | None,
    hint: dict[str, object],
):
    """
    **FrameLocator** roots first, then **Frame**\\ s matching **hint** ``roots_sorted`` (URL tail),
    then remaining frames. Shared by mobile search hit eval/click and **Contact** title drilldown plans.
    """
    roots_sorted = hint.get("roots_sorted") if isinstance(hint, dict) else None
    page_top = str(hint.get("page_url_top") or "") if isinstance(hint, dict) else ""
    hinted_ordered: list[Frame] = []
    hinted_ids: set[int] = set()
    if isinstance(roots_sorted, list) and roots_sorted:
        for entry in roots_sorted:
            if not isinstance(entry, dict):
                continue
            for frame in _ordered_frames(page):
                fu = frame.url or ""
                if _frame_url_matches_payment_hint(fu, entry, page_top):
                    fid = id(frame)
                    if fid not in hinted_ids:
                        hinted_ids.add(fid)
                        hinted_ordered.append(frame)
                    break
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        yield fl
    if hinted_ordered:
        for f in hinted_ordered:
            yield f
        for frame in _ordered_frames(page):
            if id(frame) not in hinted_ids:
                yield frame
    else:
        for frame in _ordered_frames(page):
            yield frame


def _iter_mobile_search_hit_roots(page: Page, content_frame_selector: str | None):
    """
    Like :func:`_siebel_locator_search_roots`, but **Frame**\\ s matching the mobile-search hint
    (builtin Hero default or optional env override) are yielded **early**. **FrameLocator** roots stay first.
    """
    yield from _iter_siebel_root_search_order(
        page,
        content_frame_selector,
        _load_mobile_search_hit_hint_dict_from_config(),
    )


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


def _siebel_note_frame_focus_snapshot(
    page: Page,
    note: Callable[..., object],
    step: str,
    *,
    log_prefix: str = "prepare_vehicle",
    content_frame_selector: str | None = None,
) -> None:
    """
    Historical hook for per-frame focus / URL JSON (``[frame-focus]``) after Serial → Features →
    Pre-check / PDI. **No longer written** to ``Playwright_DMS*.txt`` — it was verbose and rarely
    used by operators. Call sites remain for a possible future opt-in (e.g. env flag) or debugger.
    """
    _ = (page, note, step, log_prefix, content_frame_selector)
    return


def _siebel_scrape_text_by_id_anywhere(
    page: Page, element_id: str, *, content_frame_selector: str | None
) -> str:
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            val = root.evaluate(f"""() => {{
                const el = document.getElementById("{element_id}");
                if (!el) return '';
                return (
                    el.value ||
                    el.textContent ||
                    el.innerText ||
                    el.getAttribute('title') ||
                    ''
                ).trim();
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
    is_visible_timeout_ms: int = 700,
) -> bool:
    tmo = min(int(timeout_ms or 3000), 4000)
    _vis = max(1, int(is_visible_timeout_ms or 700))
    for root in _siebel_all_search_roots(page, content_frame_selector):
        try:
            loc = root.locator(f"#{element_id}").first
            if loc.count() > 0 and loc.is_visible(timeout=_vis):
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


def _write_playwright_vehicle_master_section(
    log_fp,
    merged: dict,
    critical: list[str],
    informational: list[str],
) -> None:
    """Append merged vehicle-master keys and gap notes to the Playwright DMS execution log."""
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
    """Append ``contact_id`` and optional enquiry# to the Playwright DMS execution log (operator-facing)."""
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


def _write_playwright_dms_masters_section(
    log_fp,
    *,
    attach_ex_showroom: str,
    sales_master_prep: dict,
    atomic_db_committed: bool,
    atomic_db_error: str | None = None,
) -> None:
    """Append attach ex-showroom scrape, ``sales_master`` prep payload, and atomic DB outcome to the log."""
    if log_fp is None:
        return
    try:
        log_fp.write("\n--- dms_master_persist (single DB transaction) ---\n")
        log_fp.write(f"attach_ex_showroom_after_price_allocate={attach_ex_showroom!r}\n")
        log_fp.write(f"sales_master_prep={sales_master_prep!r}\n")
        if atomic_db_committed:
            log_fp.write("atomic_db_transaction=committed\n")
        else:
            log_fp.write(f"atomic_db_transaction=failed error={atomic_db_error!r}\n")
        log_fp.flush()
    except OSError:
        pass
