"""
Hero Connect / Oracle Siebel Open UI — Playwright helpers for real DMS automation.

**Linear SOP** in ``Playwright_Hero_DMS_fill`` (BRD §6.1a aligned) when ``SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES``
is False. When True, only the operator **Find Contact Enquiry** path runs (Find → Contact → mobile → Go →
drill hit → Contacts → Contact_Enquiry → Enquiry → All Enquiries), then returns with the browser left open.

Default staged flow (flag False): **Find → mobile → Go**; optional
**basic enquiry** (name/address/state/PIN) + Save + **mandatory re-find** when created; **always**
care-of + Save; **Auto Vehicle List** + **In Transit** (receipt / Pre Check / PDI); **Generate Booking**
**after vehicle for all paths**; allotment (line items) when **not** In Transit; invoice = message hook
only. No **Create Invoice** automation.

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
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeout

from app.config import (
    DMS_SIEBEL_AUTO_IFRAME_SELECTORS,
    DMS_SIEBEL_INTER_ACTION_DELAY_MS,
    DMS_SIEBEL_POST_GOTO_WAIT_MS,
)

logger = logging.getLogger(__name__)

# Operator video: ``Find Contact Enquiry.mp4`` — Find → Contact → mobile → Go; if **no contact table
# rows**, **Add Enquiry** (vehicle chassis/VIN + engine, Enquiry tab, **Opportunity Form:New**, DB fields,
# Ctrl+S) then stop; else drill → Contacts → relation fill → Payments ``+``. Set False to restore the full
# BRD linear SOP inside ``Playwright_Hero_DMS_fill``.
SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES = True


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
) -> bool:
    """
    Keep interaction inside the opened global Find->Contact applet (right fly-in): fill Mobile Phone,
    then click the local Find icon/button in the same applet.
    """
    if not (mobile or "").strip():
        return False
    mobile_selectors = _mobile_selectors(mobile_aria_hints)
    mobile_selectors = [
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
                    return True
            except Exception:
                pass
        return False

    # Strong fallback for custom Find popup: fill first visible field and click Find inside that popup.
    for frame in _ordered_frames(page):
        try:
            if bool(frame.evaluate(_FILL_FIRST_IN_RIGHT_FIND_PANEL_JS, mobile.strip())):
                logger.info(
                    "siebel_dms: filled first visible input + clicked Find in right Contact popup (DOM fallback)"
                )
                return True
        except Exception:
            continue

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

    def try_on_root(page_: Page, root) -> bool:
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

    vin_css = (
        'input[title*="VIN" i]',
        'input[aria-label*="VIN" i]',
        'input[title*="Chassis" i]',
        'input[aria-label*="Chassis" i]',
    )
    eng_css = (
        'input[title*="Engine#" i]',
        'input[title*="Engine #" i]',
        'input[aria-label*="Engine#" i]',
        'input[aria-label*="Engine #" i]',
        'input[title^="Engine" i]',
        'input[aria-label*="Engine" i]',
    )

    _FILL_VIN_ENGINE_RIGHT_PANEL_JS = """(args) => {
      const chassisW = String(args.chassis || '').trim();
      const engineW = String(args.engine || '').trim();
      if (!chassisW || !engineW) return false;
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
      const scorePanel = (d) => {
        const t = (d.innerText || '').toLowerCase();
        let s = 0;
        if (t.includes('vin')) s += 5;
        if (t.includes('engine')) s += 5;
        if (t.includes('vehicle')) s += 2;
        if (t.includes('registration')) s += 1;
        return s;
      };
      const panelCandidates = Array.from(document.querySelectorAll('div'))
        .filter(vis)
        .filter((d) => {
          const r = d.getBoundingClientRect();
          if (r.width < 200 || r.width > 520 || r.height < 160 || r.height > 720) return false;
          if (r.left < window.innerWidth * 0.48) return false;
          return scorePanel(d) >= 8;
        });
      if (!panelCandidates.length) return false;
      panelCandidates.sort((a, b) => scorePanel(b) - scorePanel(a));
      for (const panel of panelCandidates) {
        const inputs = Array.from(panel.querySelectorAll('input')).filter((i) => isTxt(i) && vis(i));
        if (inputs.length < 2) continue;
        const vinEl = inputs.find((i) => {
          const t = String(i.getAttribute('title') || '').toLowerCase();
          const a = String(i.getAttribute('aria-label') || '').toLowerCase();
          return t.includes('vin') || a.includes('vin') || t.includes('chassis') || a.includes('chassis');
        });
        const engEl = inputs.find((i) => {
          const t = String(i.getAttribute('title') || '').toLowerCase();
          const a = String(i.getAttribute('aria-label') || '').toLowerCase();
          return t.includes('engine') || a.includes('engine');
        });
        if (!vinEl || !engEl) continue;
        try {
          vinEl.focus();
          vinEl.value = '';
          vinEl.value = chassisW;
          vinEl.dispatchEvent(new Event('input', { bubbles: true }));
          vinEl.dispatchEvent(new Event('change', { bubbles: true }));
          engEl.focus();
          engEl.value = '';
          engEl.value = engineW;
          engEl.dispatchEvent(new Event('input', { bubbles: true }));
          engEl.dispatchEvent(new Event('change', { bubbles: true }));
          engEl.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
          engEl.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
        } catch (e) {
          continue;
        }
        return true;
      }
      return false;
    }"""

    def try_root(root) -> bool:
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
            vin_loc = None
            for css in vin_css:
                try:
                    loc = applet.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        vin_loc = loc
                        break
                except Exception:
                    continue
            if vin_loc is None:
                try:
                    loc = applet.get_by_label(re.compile(r"^\s*VIN\s*$", re.I)).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        vin_loc = loc
                except Exception:
                    pass
            eng_loc = None
            for css in eng_css:
                try:
                    loc = applet.locator(css).first
                    if loc.count() > 0 and loc.is_visible(timeout=700):
                        eng_loc = loc
                        break
                except Exception:
                    continue
            if eng_loc is None:
                for pat in (
                    re.compile(r"Engine\s*#", re.I),
                    re.compile(r"^\s*Engine\s*#\s*$", re.I),
                ):
                    try:
                        loc = applet.get_by_label(pat).first
                        if loc.count() > 0 and loc.is_visible(timeout=700):
                            eng_loc = loc
                            break
                    except Exception:
                        continue
            if vin_loc is None or eng_loc is None:
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
                return True
            except Exception:
                continue
        return False

    for frame in _ordered_frames(page):
        try:
            if bool(
                frame.evaluate(
                    _FILL_VIN_ENGINE_RIGHT_PANEL_JS,
                    {"chassis": cw, "engine": ew},
                )
            ):
                logger.info("siebel_dms: VIN+Engine filled via right-panel DOM fallback (Vehicles Find)")
                return True
        except Exception:
            continue

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
) -> bool:
    """
    Open Contact Find view, set object type to Contact when possible, fill **mobile only**, Go.
    Shared by initial find and mandatory re-find after enquiry creation.
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
    )
    if scoped_applet_find_clicked:
        note(
            "Filled mobile and clicked Find inside the Contact applet (scoped; includes first-field "
            "right-popup fallback when labels are non-standard)."
        )
        _safe_page_wait(page, wait_after_go_ms, log_label="after_contact_find_go_scoped")
        return True

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
) -> bool:
    """SOP: after saving a **basic** enquiry, always Find → mobile → Go again before care-of."""
    note("Stage 3 (mandatory re-find): searching again by mobile after enquiry save.")
    step("Re-finding customer by mobile after enquiry creation (mandatory SOP).")
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
        stage_msg="Contact view: re-find by mobile (post-enquiry).",
        wait_after_go_ms=2000,
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
    father_husband_name: str,
    care_of: str,
    gender: str,
) -> tuple[str, str]:
    """
    Use DB ``care_of`` when present: first marker (S/O, W/O, D/O) picks relation;
    remaining text becomes Relation's Name.
    """
    rel = (relation_prefix or "").strip().upper().replace(".", "")
    name = (father_husband_name or "").strip()
    g = (gender or "").strip().lower()
    default_prefix = "S/o" if g.startswith("m") else "D/o"
    co = (care_of or "").strip() or name
    if not co:
        return rel, name

    m = re.match(r"^\s*(S\s*/?\s*O|W\s*/?\s*O|D\s*/?\s*O)\s*[:\-]?\s*(.*)\s*$", co, re.I)
    if not m:
        # Parsed/derived fallback: prefix the name by gender rule.
        if name and not re.match(r"^\s*[SWD]\s*/?\s*O\b", name, re.I):
            name = f"{default_prefix} {name}".strip()
        return rel, name
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
    elif name and not re.match(r"^\s*[SWD]\s*/?\s*O\b", name, re.I):
        name = f"{default_prefix} {name}".strip()[:255]
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
    Loads **Vehicle Information** / detail so model, color, and year can be scraped from inputs or rows.
    """
    vin_key = _vin_match_key(chassis)
    if not vin_key or len(vin_key) < 5:
        return False
    sub_pat = re.compile(".*" + re.escape(vin_key) + ".*", re.I)

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
        if not row_compact:
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

    def try_click_in_root(root) -> bool:
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
            # Title column / list: any visible anchor whose text contains the chassis key (VIN link).
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
) -> bool:
    """
    Steps after **Find + Go** from operator recording *Find Contact Enquiry*:
    optional **Siebel Find** tab → click the **Search Results** mobile drill-in → **Contacts** →
    **Contact_Enquiry** (Contacts + Enquiries tables, Enquiry# link) → **Enquiry** → **All Enquiries**.
    """
    _safe_page_wait(page, 2200, log_label="after_find_go_before_drill")
    if _siebel_try_click_named_in_frames(
        page,
        re.compile(r"Siebel\s*Find", re.I),
        roles=("tab", "link"),
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Activated Siebel Find tab in search results (video SOP).")
        _safe_page_wait(page, 700, log_label="after_siebel_find_tab")

    if not _siebel_try_click_mobile_search_hit_link(
        page,
        mobile,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    ):
        note("Could not click a search-result link for the mobile — check left Search Results grid.")
        return False
    note("Opened contact from search hit hyperlink (video SOP).")
    _safe_page_wait(page, 1200, log_label="after_contact_drill_link")

    care_val = (care_of or "").strip()
    if not care_val:
        return True

    # Deterministic navigation: after left hit, always open record via First Name.
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

    _safe_page_wait(page, 700, log_label="after_first_name_click_before_relation_fill")

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
        import json as _j, time as _t
        _dl = "debug-08e634.log"
        for i in range(max(1, attempts)):
            try:
                cnt = loc.count()
                if cnt <= 0:
                    # #region agent log
                    try:
                        with open(_dl, "a") as _f:
                            _f.write(_j.dumps({"sessionId":"08e634","hypothesisId":"C","location":"_fill_with_retry","message":"count_zero","data":{"lbl":_lbl,"attempt":i+1,"count":cnt},"timestamp":int(_t.time()*1000)})+"\n")
                    except Exception:
                        pass
                    # #endregion
                    return False
                vis = loc.is_visible(timeout=visible_ms)
                if not vis:
                    # #region agent log
                    try:
                        with open(_dl, "a") as _f:
                            _f.write(_j.dumps({"sessionId":"08e634","hypothesisId":"A","location":"_fill_with_retry","message":"not_visible","data":{"lbl":_lbl,"attempt":i+1,"visible_ms":visible_ms},"timestamp":int(_t.time()*1000)})+"\n")
                    except Exception:
                        pass
                    # #endregion
                    continue
                # #region agent log
                _pre = None
                try:
                    _pre = loc.evaluate("el => ({readOnly:el.readOnly,val:(el.value||'').substring(0,50),tag:el.tagName,name:el.name||'',id:el.id||''})")
                except Exception:
                    pass
                try:
                    with open(_dl, "a") as _f:
                        _f.write(_j.dumps({"sessionId":"08e634","hypothesisId":"D","location":"_fill_with_retry","message":"pre_fill","data":{"lbl":_lbl,"attempt":i+1,"el":_pre},"timestamp":int(_t.time()*1000)})+"\n")
                except Exception:
                    pass
                # #endregion
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
                # #region agent log
                try:
                    with open(_dl, "a") as _f:
                        _f.write(_j.dumps({"sessionId":"08e634","hypothesisId":"B","location":"_fill_with_retry","message":"readback","data":{"lbl":_lbl,"attempt":i+1,"got":got[:80],"match":_ok,"value":value[:50]},"timestamp":int(_t.time()*1000)})+"\n")
                except Exception:
                    pass
                # #endregion
                if _ok:
                    return True
            except Exception as _ex:
                # #region agent log
                try:
                    with open(_dl, "a") as _f:
                        _f.write(_j.dumps({"sessionId":"08e634","hypothesisId":"E","location":"_fill_with_retry","message":"exception","data":{"lbl":_lbl,"attempt":i+1,"err":str(_ex)[:200]},"timestamp":int(_t.time()*1000)})+"\n")
                except Exception:
                    pass
                # #endregion
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

    # Exact-input path first (from provided DOM snippet), then label fallback.
    _read_first_name_probe()
    import json as _j2, time as _t2
    _dl2 = "debug-08e634.log"
    _rn_t0 = _t2.time()

    # #region agent log
    try:
        _dom_snap = None
        for _fr in _ordered_frames(page):
            try:
                _dom_snap = _fr.evaluate("""() => {
                    const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                    const all = Array.from(document.querySelectorAll('input,textarea,select')).filter(vis);
                    const rn = all.filter(i => {
                        const a = (i.getAttribute('aria-label')||'').toLowerCase();
                        const t = (i.getAttribute('title')||'').toLowerCase();
                        const n = (i.name||'').toLowerCase();
                        return a.includes('relation') || t.includes('relation') || n.includes('relation');
                    });
                    return { total: all.length, relation_fields: rn.map(i => ({
                        tag: i.tagName, name: i.name||'', id: i.id||'',
                        ariaLabel: i.getAttribute('aria-label')||'',
                        title: i.getAttribute('title')||'',
                        readOnly: i.readOnly, val: (i.value||'').substring(0,50),
                        cls: i.className||''
                    })) };
                }""")
                if _dom_snap and _dom_snap.get("relation_fields"):
                    break
            except Exception:
                continue
        with open(_dl2, "a") as _f2:
            _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"ADE","location":"rn_dom_probe","message":"dom_before_fill","data":_dom_snap,"timestamp":int(_t2.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion

    exact_selectors = (
        "input[aria-label=\"Relation's Name\"]",
        "textarea[aria-label=\"Relation's Name\"]",
        "input[name='s_4_1_89_0'][aria-label=\"Relation's Name\"]",
        "input[name='s_4_1_89_0']",
        "input[aria-labelledby=\"Relation's_Name_Label_4\"]",
        "input.s_4_1_89_0",
    )
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        for css in exact_selectors:
            try:
                loc = fl.locator(css).first
                if _fill_with_retry(loc, care_val, attempts=3, visible_ms=900, _lbl=f"fl:{css[:40]}"):
                    # #region agent log
                    try:
                        with open(_dl2, "a") as _f2:
                            _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"OK","location":"rn_filled","message":"fl_exact","data":{"css":css,"ms":int((_t2.time()-_rn_t0)*1000)},"timestamp":int(_t2.time()*1000)})+"\n")
                    except Exception:
                        pass
                    # #endregion
                    return _after_relation_fill_nav()
            except Exception:
                continue
    for frame in _ordered_frames(page):
        for css in exact_selectors:
            try:
                loc = frame.locator(css).first
                if _fill_with_retry(loc, care_val, attempts=3, visible_ms=900, _lbl=f"fr:{css[:40]}"):
                    # #region agent log
                    try:
                        with open(_dl2, "a") as _f2:
                            _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"OK","location":"rn_filled","message":"fr_exact","data":{"css":css,"ms":int((_t2.time()-_rn_t0)*1000)},"timestamp":int(_t2.time()*1000)})+"\n")
                    except Exception:
                        pass
                    # #endregion
                    return _after_relation_fill_nav()
            except Exception:
                continue

    # #region agent log
    try:
        with open(_dl2, "a") as _f2:
            _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"EXACT_FAIL","location":"rn_exact_failed","message":"all_exact_selectors_failed","data":{"ms":int((_t2.time()-_rn_t0)*1000)},"timestamp":int(_t2.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion

    # Restore earlier working style: label-based fill fallback.
    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        try:
            loc = fl.get_by_label("Relation's Name", exact=True).first
            if _fill_with_retry(loc, care_val, attempts=3, visible_ms=700, _lbl="lbl_fl"):
                # #region agent log
                try:
                    with open(_dl2, "a") as _f2:
                        _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"OK","location":"rn_filled","message":"fl_label","data":{"ms":int((_t2.time()-_rn_t0)*1000)},"timestamp":int(_t2.time()*1000)})+"\n")
                except Exception:
                    pass
                # #endregion
                return _after_relation_fill_nav()
        except Exception:
            continue
    for frame in _ordered_frames(page):
        try:
            loc = frame.get_by_label("Relation's Name", exact=True).first
            if _fill_with_retry(loc, care_val, attempts=3, visible_ms=700, _lbl="lbl_fr"):
                # #region agent log
                try:
                    with open(_dl2, "a") as _f2:
                        _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"OK","location":"rn_filled","message":"fr_label","data":{"ms":int((_t2.time()-_rn_t0)*1000)},"timestamp":int(_t2.time()*1000)})+"\n")
                except Exception:
                    pass
                # #endregion
                return _after_relation_fill_nav()
        except Exception:
            continue

    # #region agent log
    try:
        with open(_dl2, "a") as _f2:
            _f2.write(_j2.dumps({"sessionId":"08e634","hypothesisId":"FAIL","location":"rn_all_failed","message":"all_paths_exhausted","data":{"ms":int((_t2.time()-_rn_t0)*1000),"care_val":care_val[:60]},"timestamp":int(_t2.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion

    _read_first_name_probe()
    note("Could not fill Relation's Name on opened customer record (video SOP).")
    return False


def _add_customer_payment(
    page: Page,
    *,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
) -> bool:
    """
    New isolated step: click "+" (new) on current Payments frame and fill payment row.
    """
    _safe_page_wait(page, 250, log_label="before_payments_plus_click")
    try:
        note(f"Payment debug: ordered frames count={len(_ordered_frames(page))}.")
    except Exception:
        pass

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
        "button",
        "a",
    )
    plus_patterns = (
        re.compile(r"^\s*\+\s*$"),
        re.compile(r"^\s*new\s*$", re.I),
        re.compile(r"add", re.I),
    )

    def _is_payment_action_root(root) -> bool:
        """Toolbar frame/root that hosts Payment Lines New/Save controls."""
        try:
            return bool(
                root.evaluate(
                    """() => {
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
                        const el = document.querySelector(s);
                        if (vis(el)) return true;
                      }
                      return false;
                    }"""
                )
            )
        except Exception:
            return False

    def _click_plus_in_root(root) -> bool:
        # Exact selectors first
        for css in plus_selectors[:-2]:
            try:
                c = root.locator(css).first
                if c.count() > 0 and c.is_visible(timeout=500):
                    try:
                        c.click(timeout=action_timeout_ms)
                    except Exception:
                        c.click(timeout=action_timeout_ms, force=True)
                    return True
            except Exception:
                continue
        # Text/aria/title fallback across generic clickable nodes
        for css in plus_selectors[-2:]:
            try:
                cands = root.locator(css)
                n = cands.count()
                for i in range(min(n, 30)):
                    c = cands.nth(i)
                    if not c.is_visible(timeout=350):
                        continue
                    try:
                        txt = (c.inner_text(timeout=250) or "").strip()
                    except Exception:
                        txt = ""
                    try:
                        title = (c.get_attribute("title") or "").strip()
                    except Exception:
                        title = ""
                    try:
                        aria = (c.get_attribute("aria-label") or "").strip()
                    except Exception:
                        aria = ""
                    blob = " ".join([txt, title, aria]).strip()
                    if not blob:
                        continue
                    if any(p.search(blob) for p in plus_patterns):
                        try:
                            c.click(timeout=action_timeout_ms)
                        except Exception:
                            c.click(timeout=action_timeout_ms, force=True)
                        return True
            except Exception:
                continue
        return False

    action_roots = []
    for root in _siebel_locator_search_roots(page, content_frame_selector):
        try:
            if _is_payment_action_root(root):
                action_roots.append(root)
        except Exception:
            continue
    for frame in _ordered_frames(page):
        try:
            if _is_payment_action_root(frame):
                action_roots.append(frame)
        except Exception:
            continue
    root_candidates = action_roots if action_roots else list(_siebel_locator_search_roots(page, content_frame_selector))
    note(
        "Payment debug: root candidates prepared "
        f"(action_roots={len(action_roots)}, total_candidates={len(root_candidates)})."
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
                    scoped_roots, _TXN_TYPE_SELS, "Payments", label="Transaction_Type",
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
                            page.keyboard.type("Payments")
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
                                page.keyboard.type("0")
                                _safe_page_wait(page, 120, log_label="tab_nav_amount_fill")
                                page.keyboard.press("Tab")
                                _tab_filled = True
                                note(f"Payment direct: Transaction_Amount filled via Tab navigation (tab {_ti}, name={_ae.get('name')!r}).")
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
                    f"Type=Payments(ok={type_ok!r}), Mode=Cash(ok={mode_ok!r}), Amount=0(ok={amount_ok!r})."
                )
                _safe_page_wait(page, 400, log_label="after_amount_before_save")

                # Re-detect action roots after row creation; toolbar can be in a sibling frame.
                save_action_roots = []
                for sroot in _siebel_locator_search_roots(page, content_frame_selector):
                    try:
                        if _is_payment_action_root(sroot):
                            save_action_roots.append(sroot)
                    except Exception:
                        continue
                for sframe in _ordered_frames(page):
                    try:
                        if _is_payment_action_root(sframe):
                            save_action_roots.append(sframe)
                    except Exception:
                        continue
                if not save_action_roots:
                    save_action_roots = list(_siebel_locator_search_roots(page, content_frame_selector))
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
                    return True
                note("Could not click Save icon after filling payment fields.")
                return False
        except Exception as e:
            note(f"Add customer payment flow failed after '+' click attempt: {e}")
            return False
    note("Could not click '+' icon on Payments tab (Payment Lines List:New not visible).")
    return False


def _create_order(
    page: Page,
    *,
    mobile: str,
    full_chassis: str,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
) -> tuple[bool, str | None, dict]:
    """
    Vehicle Sales -> Sales Orders New flow:
    - Open Vehicle Sales (first-level view bar)
    - Click Sales Orders New:List (+)
    - Set Booking Order Type
    - Pick contact by mobile from pick applet
    - Save and open created Order#
    - On order line items: Line Items List:New -> VIN (name=VIN) -> full chassis + Enter;
      optional pick applet (search by Vin#) -> fill chassis, select row, OK; then scrape inventory
    - If inventory not In transit: Price all + Allocate all
    - Scrape Total (Ex-showroom)
    """
    scraped: dict = {"inventory_location": "", "ex_showroom_price": ""}

    def _roots():
        return _siebel_locator_search_roots(page, content_frame_selector)

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

    try:
        if _try_expand_find_flyin(page, timeout_ms=min(action_timeout_ms, 5000), content_frame_selector=content_frame_selector):
            note("Create Order: expanded Find fly-in for Vehicle Sales list query.")
            _safe_page_wait(page, 1000, log_label="after_vs_find_expand")
    except Exception:
        pass

    _mobile_selectors = (
        "input[id='1_Mobile_Phone']",
        "input[name='Mobile_Phone']",
        "input[aria-label*='Mobile Phone' i]",
        "input[title*='Mobile Phone' i]",
        "input[id='1_Mobile_Number']",
        "input[name='Mobile_Number']",
        "input[aria-label*='Mobile Number' i]",
        "input[title*='Mobile Number' i]",
        "input[id*='Mobile_Phone' i]",
        "input[id*='Mobile_Number' i]",
    )

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

    def _fill_mobile_via_label() -> bool:
        m = (mobile or "").strip()
        if not m:
            return False
        for root in _roots():
            for pat in (
                re.compile(r"mobile\s*phone", re.I),
                re.compile(r"mobile\s*number", re.I),
                re.compile(r"cell\s*phone", re.I),
            ):
                try:
                    loc = root.get_by_label(pat).first
                    if loc.count() > 0 and loc.is_visible(timeout=600):
                        try:
                            loc.click(timeout=min(action_timeout_ms, 2000))
                        except Exception:
                            loc.click(timeout=min(action_timeout_ms, 2000), force=True)
                        try:
                            loc.fill(m, timeout=min(action_timeout_ms, 3000))
                        except Exception:
                            try:
                                loc.press("Control+a", timeout=800)
                            except Exception:
                                pass
                            loc.type(m, delay=25, timeout=min(action_timeout_ms, 4000))
                        return True
                except Exception:
                    continue
        return False

    _invoice_selected_ready = _go_to_invoice_selected_direct()
    if not _invoice_selected_ready:
        return False, "Could not open Invoice Selected context (including id='ui-id-429').", scraped
    note("Create Order: Invoice Selected context opened directly; Find→Vehicle Sales→Mobile fallback disabled.")

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

    _existing_order_opened = True
    note("Create Order: staying on Invoice Selected page; skipping order-list drill-in.")

    if False:
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
        _set_ok = False
        for root in _roots():
            try:
                if _fill_by_label_on_frame(root, "Booking Order Type", "Normal Booking", action_timeout_ms=action_timeout_ms):
                    _set_ok = True
                    break
                if _select_dropdown_by_label_on_frame(
                    root, label="Booking Order Type", value="Normal Booking", action_timeout_ms=min(action_timeout_ms, 8000)
                ):
                    _set_ok = True
                    break
            except Exception:
                continue
        if not _set_ok:
            return False, "Could not set Booking Order Type = Normal Booking.", scraped
        _safe_page_wait(page, 600, log_label="after_booking_order_type")
        note("Create Order: set Booking Order Type = Normal Booking.")

        # 4) Contact Last Name pick/search icon -> opens pick applet
        if not _click_any(
            (
                "a[aria-label*='Contact Last Name' i][aria-label*='Pick' i]",
                "button[aria-label*='Contact Last Name' i][aria-label*='Pick' i]",
                "a[title*='Contact Last Name' i][title*='Pick' i]",
                "a[aria-label*='Pick Contact' i]",
                "button[aria-label*='Pick Contact' i]",
            ),
            timeout=min(action_timeout_ms, 3000),
        ):
            return False, "Could not open Contact Last Name pick/search applet.", scraped
        _safe_page_wait(page, 900, log_label="after_open_contact_pick")
        note("Create Order: opened Contact Last Name pick/search applet.")

        # 5) Pick Contact List:Query
        if not _click_any(
            (
                "a[aria-label='Pick Contact List:Query']",
                "button[aria-label='Pick Contact List:Query']",
                "a[title='Pick Contact List:Query']",
                "button[title='Pick Contact List:Query']",
                "a[aria-label*='Pick Contact List' i][aria-label*='Query' i]",
            ),
            timeout=min(action_timeout_ms, 3000),
        ):
            return False, "Could not click Pick Contact List:Query.", scraped
        _safe_page_wait(page, 700, log_label="after_pick_contact_query")
        note("Create Order: clicked Pick Contact List:Query.")

        # 6) Fill id=1_Mobile_Phone + Enter
        if not _fill_any(
            (
                "input[id='1_Mobile_Phone']",
                "input[name='Mobile_Phone']",
                "input[aria-label*='Mobile Phone' i]",
            ),
            mobile,
            timeout=min(action_timeout_ms, 3000),
        ):
            return False, "Could not fill mobile number in Pick Contact List.", scraped
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        _safe_page_wait(page, 900, log_label="after_pick_contact_mobile_enter")
        note(f"Create Order: queried Pick Contact List by mobile={mobile!r}.")

        # 7) OK in pick applet
        if not _click_any(
            (
                "button:has-text('OK')",
                "a:has-text('OK')",
                "button[aria-label='OK']",
                "a[aria-label='OK']",
            ),
            timeout=min(action_timeout_ms, 3000),
        ):
            return False, "Could not click OK in Pick Contact List applet.", scraped
        _safe_page_wait(page, 1200, log_label="after_pick_contact_ok")
        note("Create Order: selected contact and closed pick applet via OK.")

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
    else:
        note("Create Order: mobile branch matched; skipped + and contact-pick flow.")

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
    scraped["ex_showroom_price"] = total_ex
    if total_ex:
        note(f"Create Order: scraped Total (Ex-showroom)={total_ex!r}.")
    else:
        note("Create Order: could not scrape Total (Ex-showroom).")

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


def _try_click_precheck_complete(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    """Hero Connect: complete **Pre Check** / PDI Pre-Check list before main PDI submit."""
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"complete\s+pre[-\s]?check", re.I),
            re.compile(r"pre[-\s]?check\s+complete", re.I),
            re.compile(r"complete\s+precheck", re.I),
            re.compile(r"^\s*pre[-\s]?check\s*$", re.I),
            re.compile(r"submit\s+pre[-\s]?check", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="Pre Check",
    )


def _try_click_pdi_submit(
    page: Page, *, timeout_ms: int, content_frame_selector: str | None
) -> bool:
    # Avoid bare "Submit" first — it matches unrelated Siebel applets. Prefer PDI-specific labels.
    return _try_click_toolbar_by_name(
        page,
        (
            re.compile(r"pdi\s+complete", re.I),
            re.compile(r"complete\s+pdi", re.I),
            re.compile(r"submit\s+pdi", re.I),
            re.compile(r"pdi\s+submit", re.I),
            re.compile(r"submit\s+record", re.I),
            re.compile(r"finalize\s+pdi", re.I),
        ),
        timeout_ms=timeout_ms,
        content_frame_selector=content_frame_selector,
        log_tag="PDI Submit",
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
    Maps 13+ columns like the dummy DMS table when possible.
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
        return {
            "key_num": texts[0].strip(),
            "frame_num": texts[1].strip(),
            "engine_num": texts[2].strip(),
            "model": texts[3].strip(),
            "color": texts[4].strip(),
            "cubic_capacity": texts[5].strip(),
            "seating_capacity": texts[6].strip(),
            "body_type": texts[7].strip(),
            "vehicle_type": texts[8].strip(),
            "num_cylinders": texts[9].strip(),
            "horse_power": texts[10].strip(),
            "vehicle_price": ex_show,
            "ex_showroom_price": ex_show,
            "year_of_mfg": texts[12].strip(),
            "in_transit": in_tr,
        }
    if len(texts) >= 6:
        return {
            "key_num": texts[0].strip(),
            "frame_num": texts[1].strip() if len(texts) > 1 else "",
            "engine_num": texts[2].strip() if len(texts) > 2 else "",
            "model": texts[3].strip() if len(texts) > 3 else "",
            "color": texts[4].strip() if len(texts) > 4 else "",
            "vehicle_price": texts[-2].strip() if len(texts) > 2 else "",
            "year_of_mfg": texts[-1].strip() if len(texts) > 1 else "",
            "in_transit": in_tr,
        }
    return {"in_transit": in_tr} if in_tr else {}


def _siebel_goto_vehicle_list_and_scrape(
    page: Page,
    vehicle_url: str,
    key_p: str,
    frame_p: str,
    engine_p: str,
    *,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    form_trace=None,
) -> tuple[dict, str | None]:
    """Navigate to Auto Vehicle List, run key/chassis/engine query, return (scraped, error)."""
    _goto(page, vehicle_url, "vehicle_list", nav_timeout_ms=nav_timeout_ms)
    _safe_page_wait(page, 1500, log_label="vehicle_list_open")

    key_ok = _try_fill_field(
        page,
        [
            'input[aria-label*="Key" i]:not([aria-label*="Keyboard" i])',
            'input[title*="Key" i]',
            'input[name*="Key" i]',
        ],
        key_p,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    frame_ok = _try_fill_field(
        page,
        [
            'input[aria-label*="Chassis" i]',
            'input[aria-label*="Frame" i]',
            'input[aria-label*="VIN" i]',
            'input[title*="Chassis" i]',
        ],
        frame_p,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    engine_ok = _try_fill_field(
        page,
        [
            'input[aria-label*="Engine" i]',
            'input[title*="Engine" i]',
        ],
        engine_p,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
    )
    if callable(form_trace):
        form_trace(
            "5_vehicle_list",
            "Auto Vehicle List — search/query row",
            "attempted_fill_on_key_chassis_engine_inputs_then_FindGo",
            key_partial=key_p,
            key_input_located=key_ok,
            frame_partial=frame_p,
            chassis_vin_input_located=frame_ok,
            engine_partial=engine_p,
            engine_input_located=engine_ok,
        )
    if not (key_ok or frame_ok or engine_ok):
        return {}, (
            "Siebel: could not find key/chassis/engine search inputs on the vehicle view. "
            "Open Auto Vehicle List in the browser, inspect the query fields, and set "
            "DMS_SIEBEL_CONTENT_FRAME_SELECTOR if they live inside a specific iframe."
        )

    if _click_find_go_query(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
        note("Clicked Find/Go on vehicle search.")
    else:
        note("Vehicle search: Find/Go not detected; waiting for grid anyway.")

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
    if not filled_flyin:
        chassis_ok = _try_fill_field(
            page,
            [
                'input[aria-label*="VIN" i]',
                'input[aria-label*="Chassis" i]',
                'input[aria-label*="Frame" i]',
                'input[title*="VIN" i]',
                'input[title*="Chassis" i]',
            ],
            cw,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        engine_ok = _try_fill_field(
            page,
            [
                'input[aria-label*="Engine#" i]',
                'input[aria-label*="Engine #" i]',
                'input[title*="Engine#" i]',
                'input[aria-label*="Engine" i]',
                'input[title*="Engine" i]',
            ],
            ew,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
        )
        if chassis_ok and engine_ok:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            _safe_page_wait(page, 400, log_label="after_vehicle_find_enter_fallback")

    if not chassis_ok or not engine_ok:
        note("Add Enquiry: could not fill VIN/chassis and Engine# on Vehicles Find (fly-in + fallback).")
        return False, {}
    if not filled_flyin:
        note("Add Enquiry: used generic input fill + Enter for vehicle query.")

    _safe_page_wait(page, 400, log_label="after_vehicle_find_enter")
    if not filled_flyin:
        if _click_find_go_query(page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector):
            note("Add Enquiry: clicked Find/Go after vehicle query.")
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
                    if _click_first_visible(frame.locator(css)):
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
                if _click_first_visible(page.locator(css)):
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
    pats = (
        re.compile(rf"^\s*{re.escape(label)}\s*$", re.I),
        re.compile(re.escape(label), re.I),
    )
    for pat in pats:
        try:
            loc = frame.get_by_label(pat).first
            if loc.count() <= 0 or not loc.is_visible(timeout=700):
                continue
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
) -> tuple[bool, str | None]:
    """
    Contact Find returned no table rows: vehicle find + scrape, **Enquiry** tab, **Opportunity Form:New**,
    fill opportunity fields from DB + scraped model/color (**Financier** fields are skipped), Ctrl+S.

    Returns ``(success, error_detail)`` — ``error_detail`` is a short operator-facing reason when
    ``success`` is False (used for API ``error`` text; see Playwright_DMS.txt for full NOTES).
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
        return False, "Missing customer Aadhaar last 4 for UIN No."

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
        return False, "Vehicle find failed (chassis/engine query or VIN fly-in)."

    _apply_year_of_mfg_yyyy(scraped_v)

    if not _add_enquiry_vehicle_scrape_has_model_year_color(scraped_v):
        note(
            "Add Enquiry: vehicle search did not yield model, year of manufacture, and color in the grid — "
            "not opening Enquiry / new opportunity (confirm list applet column layout vs scrape)."
        )
        return (
            False,
            "Vehicle scrape did not yield model, YYYY year of manufacture, and color (see NOTES above).",
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

    if not _try_click_enquiry_top_tab(
        page, action_timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
    ):
        note("Add Enquiry: Enquiry tab not found (tried aria-label Enquiry Selected and Enquiry).")
        return False, "Enquiry main tab not found."
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
        return False, "New opportunity form not found (no Enquiry Type field)."

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
                t = date.today()
                return str(max(0, t.year - dob_dt.year - ((t.month, t.day) < (dob_dt.month, dob_dt.day))))
            except Exception:
                continue
        m = re.search(r"\b(19\d{2}|20\d{2})\b", s)
        if m:
            try:
                return str(max(0, date.today().year - int(m.group(1))))
            except Exception:
                return ""
        return ""

    def _normalize_gender_for_form(raw: str) -> str:
        s = (raw or "").strip().lower()
        if s in ("m", "male"):
            return "Male"
        if s in ("f", "female"):
            return "Female"
        if s in ("o", "other"):
            return "Other"
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

    def _select_variant_first_value(frame: Frame) -> bool:
        for pat in (re.compile(r"^\s*Variant\s*$", re.I), re.compile(r"\bVariant\b", re.I)):
            try:
                fld = frame.get_by_label(pat).first
                if fld.count() <= 0 or not fld.is_visible(timeout=700):
                    continue
                try:
                    fld.click(timeout=action_timeout_ms)
                except Exception:
                    fld.click(timeout=action_timeout_ms, force=True)
                try:
                    opts = fld.evaluate(
                        """el => (el.tagName === 'SELECT') ? [...el.options].map(o => ({v:o.value,t:(o.textContent||'').trim()})) : []"""
                    )
                    if opts:
                        first_non_blank = None
                        for o in opts:
                            if str(o.get("t") or "").strip():
                                first_non_blank = str(o.get("v") or "")
                                break
                        if first_non_blank is not None:
                            fld.select_option(value=first_non_blank, timeout=action_timeout_ms)
                            return True
                except Exception:
                    pass
                try:
                    fld.press("ArrowDown", timeout=1200)
                    fld.press("Enter", timeout=1200)
                    return True
                except Exception:
                    continue
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
    last = (dms_values.get("last_name") or "").strip()
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
    today_str = date.today().strftime("%d/%m/%Y")

    if not try_field(("Contact First Name", "First Name"), first, required=True):
        return False, "Could not set Contact First Name."
    try_field(("Contact Last Name", "Last Name"), last, required=False)
    if not try_field(("Mobile Phone", "Mobile Phone #", "Cellular Phone"), mobile, required=True):
        return False, "Could not set Mobile Phone."
    landline_use = landline or mobile
    if not try_field(("Landline", "Land Line", "Alternate Phone", "Alternate Number"), landline_use, required=True):
        return False, "Could not set Landline."
    if not try_field(("Email", "Email Address", "E-mail"), "NA", required=True):
        return False, "Could not set Email."

    if not try_field(("UIN Type",), "Aadhaar Card", required=True):
        if not try_field_any(("UIN Type",), ("Aadhaar",)):
            return False, "Could not set UIN Type (Aadhaar)."
    if not try_field(("UIN No.", "UIN Number", "UIN No"), aadhar, required=True):
        return False, "Could not set UIN No."

    if not try_field(("State",), state, required=True):
        return False, "Could not set State."
    dist_use = district or city
    tehsil_use = tehsil or city
    if not try_field(("District",), dist_use, required=True):
        return False, "Could not set District."
    if not try_field(("Tehsil", "Tehsil/Taluka", "Taluka"), tehsil_use, required=True):
        return False, "Could not set Tehsil/Taluka."
    if not try_field(("City", "City/Town/Village"), city, required=True):
        return False, "Could not set City/Town/Village."
    if not _city_pick_any_then_ok(enq_frame):
        return False, "Could not pick City/Town/Village from search sub form."
    if not try_field(("Address Line 1", "Address Line1", "Address"), addr, required=True):
        return False, "Could not set Address Line 1."
    if not try_field(("Pin Code", "Pin code", "PIN Code", "Postal Code"), pin, required=True):
        return False, "Could not set Pin Code."

    if not try_field(("Age",), age, required=True):
        return False, "Could not set Age."
    if not try_field(("Gender",), gender, required=True):
        return False, "Could not set Gender."

    if not try_field(
        ("Model Interested In", "Model Interested in", "Interested Model", "Model"),
        model_i,
        required=True,
    ):
        return False, "Could not set Model Interested In (from vehicle scrape)."
    if not try_field(("Color", "Colour"), color_i, required=True):
        return False, "Could not set Color (from vehicle scrape)."

    if not try_field(("Finance Required",), finance_required, required=True):
        return False, "Could not set Finance Required."
    if not try_field(("Booking Order Type",), "Normal Booking", required=True):
        return False, "Could not set Booking Order Type."
    if not _select_variant_first_value(enq_frame):
        return False, "Could not select first Variant dropdown value."

    try_field_any(("Enquiry Source",), ("Walk-In", "Walk In", "Walkin"))
    if not try_field(("Point of Contact",), "Customer Walk-in", required=True):
        if not try_field_any(
            ("Point of Contact",),
            ("Customer Walk-In", "Walk-In", "Customer Walk In"),
        ):
            return False, "Could not set Point of Contact."

    try_field_any(
        ("Actual Enquiry Date", "Enquiry Date", "Actual Enquiry Dt"),
        (today_str,),
    )

    note("Add Enquiry: Financier fields skipped by design (tenant control).")

    try:
        page.keyboard.press("Control+s")
    except Exception:
        try:
            page.keyboard.press("Meta+s")
        except Exception:
            note("Add Enquiry: Ctrl+S failed.")
            return False, "Ctrl+S save failed on new opportunity form."
    note("Add Enquiry: pressed Ctrl+S to save enquiry.")
    _safe_page_wait(page, 1500, log_label="after_add_enquiry_save")
    enquiry_no = _scrape_enquiry_number_from_frame(enq_frame)
    if pre_save_enquiry_no and enquiry_no and enquiry_no == pre_save_enquiry_no:
        note(
            "Add Enquiry: Enquiry# did not change after save; likely not persisted "
            f"(before={pre_save_enquiry_no!r}, after={enquiry_no!r})."
        )
        return False, "Enquiry# did not change after Ctrl+S (save not confirmed)."
    if enquiry_no:
        note(f"Add Enquiry: saved Enquiry#={enquiry_no!r}.")
        if callable(form_trace):
            form_trace(
                "add_enquiry_saved",
                "Opportunity Form",
                "post_save_scrape_enquiry_number",
                enquiry_number=enquiry_no,
            )
    else:
        note("Add Enquiry: Enquiry# not readable after save (best-effort).")
        if callable(form_trace):
            form_trace(
                "add_enquiry_saved",
                "Opportunity Form",
                "post_save_scrape_enquiry_number",
                enquiry_number="",
            )
    return True, None


def _siebel_run_precheck_and_pdi(
    page: Page,
    *,
    precheck_url: str,
    pdi_url: str,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    content_frame_selector: str | None,
    note,
    ms_done,
    step=None,
    form_trace=None,
) -> None:
    """
    §6.1a In Transit: **Pre Check** must complete before **PDI**.

    - Same URL for both: one ``goto``, Pre Check click, then PDI submit.
    - Different URLs: ``goto`` precheck view, complete, then ``goto`` PDI, submit.
    - Only ``pdi`` URL: ``goto`` PDI view, try Pre Check first (combined Hero screen), then PDI submit.
    """
    say = step if callable(step) else (lambda _m: None)

    pu = (precheck_url or "").strip()
    du = (pdi_url or "").strip()
    if not pu and not du:
        note("Neither DMS_REAL_URL_PRECHECK nor DMS_REAL_URL_PDI is set; skipping pre-check and PDI.")
        say("Pre-check and PDI were skipped — PDI / pre-check URLs are not configured.")
        return

    if callable(form_trace):
        form_trace(
            "5b_precheck_pdi",
            "Pre Check / PDI (In Transit branch)",
            "start_url_branching",
            precheck_url_truncated=pu[:180] if pu else "",
            pdi_url_truncated=du[:180] if du else "",
            same_url_for_both=(pu == du) if (pu and du) else False,
        )

    def mark_precheck(clicked: bool, ok_msg: str, fail_msg: str) -> None:
        if clicked:
            note(ok_msg)
            ms_done("Pre check completed")
        else:
            note(fail_msg)

    if pu and du and pu == du:
        _goto(page, du, "pdi_precheck_same_url", nav_timeout_ms=nav_timeout_ms)
        _siebel_after_goto_wait(page, floor_ms=1000)
        if callable(form_trace):
            form_trace(
                "5b_precheck_pdi",
                "Combined Pre Check + PDI view",
                "toolbar_click_PreCheck_Complete_then_PDI_Submit",
                navigated_url_truncated=du[:180],
            )
        pc = _try_click_precheck_complete(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        )
        mark_precheck(
            pc,
            "Clicked Pre Check complete on combined PreCheck/PDI view.",
            "Pre Check control not found on combined view; operator may complete Pre Check manually.",
        )
        if callable(form_trace):
            form_trace(
                "5b_precheck_pdi",
                "Combined Pre Check + PDI view",
                "after_PreCheck_click",
                precheck_complete_clicked=pc,
            )
        _safe_page_wait(page, 600, log_label="precheck_pdi_gap")
        pdi_ok = _try_click_pdi_submit(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        )
        if callable(form_trace):
            form_trace(
                "5b_precheck_pdi",
                "Combined Pre Check + PDI view",
                "after_PDI_Submit_click",
                pdi_submit_clicked=pdi_ok,
            )
        if pdi_ok:
            note("Clicked PDI Submit.")
        else:
            note("PDI Submit not found; operator may complete PDI manually.")
        ms_done("Vehicle inspection done")
        if pc and pdi_ok:
            say("Pre-check and PDI were completed on the combined screen.")
        elif pc:
            say("Pre-check was completed; PDI submit was not found — finish PDI manually if needed.")
        elif pdi_ok:
            say("PDI submit was completed (pre-check control was not found).")
        else:
            say("Pre-check and PDI controls were not found — complete both manually if required.")
        return

    if pu:
        _goto(page, pu, "precheck", nav_timeout_ms=nav_timeout_ms)
        _siebel_after_goto_wait(page, floor_ms=1000)
        if callable(form_trace):
            form_trace(
                "5b_precheck_pdi",
                "Dedicated Pre Check view",
                "toolbar_PreCheck_Complete",
                navigated_url_truncated=pu[:180],
            )
        pc = _try_click_precheck_complete(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        )
        mark_precheck(
            pc,
            "Clicked Pre Check complete.",
            "Pre Check control not found; operator may complete Pre Check manually.",
        )
        if callable(form_trace):
            form_trace("5b_precheck_pdi", "Dedicated Pre Check view", "after_PreCheck", precheck_complete_clicked=pc)
        _safe_page_wait(page, 600, log_label="after_precheck_view")
        if pc:
            say("Pre-check step was completed on the pre-check view.")
        else:
            say("Pre-check view opened; complete control was not found — finish pre-check manually if needed.")

    if du:
        _goto(page, du, "pdi", nav_timeout_ms=nav_timeout_ms)
        _siebel_after_goto_wait(page, floor_ms=1000)
        if callable(form_trace):
            form_trace(
                "5b_precheck_pdi",
                "PDI / Auto Vehicle PDI Assessment view",
                "navigated_for_PDI_submit_path",
                navigated_url_truncated=du[:180],
            )
        if not pu:
            pc2 = _try_click_precheck_complete(
                page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
            )
            if pc2:
                note("Clicked Pre Check complete on PDI view (no separate PRECHECK URL).")
                ms_done("Pre check completed")
                say("Pre-check was completed on the PDI screen.")
            else:
                note(
                    "Pre Check control not found before PDI (single PDI URL); "
                    "operator may complete Pre Check manually if the screen requires it."
                )
                say("Pre-check control was not found before PDI — complete manually if the screen requires it.")
            _safe_page_wait(page, 600, log_label="before_pdi_submit")
        pdi_ok = _try_click_pdi_submit(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        )
        if callable(form_trace):
            form_trace("5b_precheck_pdi", "PDI view", "after_PDI_Submit_attempt", pdi_submit_clicked=pdi_ok)
        if pdi_ok:
            note("Clicked PDI Submit.")
            say("PDI was submitted.")
        else:
            note("PDI Submit not found; operator may complete PDI manually.")
            say("PDI submit was not found — complete PDI manually if required.")
        ms_done("Vehicle inspection done")
    elif pu and not du:
        note("DMS_REAL_URL_PDI is not set; only Pre Check URL was opened — set PDI URL to finish PDI.")
        say("PDI URL is not set — only pre-check was opened; configure DMS_REAL_URL_PDI to finish PDI.")


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
) -> dict:
    """
    Hero Connect / Siebel automation. If ``SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`` is True (module constant),
    runs only the **Find Contact Enquiry** video SOP through **All Enquiries**, then returns (browser
    not closed by this function).

    Otherwise **linear SOP** (stages 1–8 inside the main ``try``):

    1. **Find** customer by mobile (Contact view). 2. If not matched (or ``new_enquiry``), **basic
    enquiry** only (name, address, state, PIN — no care-of) + Save. 3. **Mandatory re-find** by
    mobile after a new basic enquiry. 4. **Care-of** (father/relation) + Save — **always** runs.
    5. **Vehicle** — nested ``stage_5_vehicle_flow()`` (list search/scrape; if **In Transit** →
    Process Receipt + Pre Check + PDI; unchanged helpers). 6. **Generate Booking** — **always** after
    vehicle processing (in-transit or not). 7.
    **Allotment** (line items, Price All, Allocate) — **non–In Transit only**, after booking. 8.
    **Invoice hook** (message only; no automation).

    **skip_find** (``skip_contact_find=True``): only for special callers — enquiry view → basic details +
    Save → mandatory re-find on ``DMS_REAL_URL_CONTACT`` → stage 4 care-of, then stages 5–8. Real
    Siebel fill from ``fill_dms_service`` always passes ``skip_contact_find=False`` (Find runs even if
    ``dms_contact_path`` in DB is ``skip_find``).

    Never clicks **Create Invoice**. Returns ``vehicle``, ``error``, ``dms_siebel_forms_filled``,
    notes, milestones, and ``dms_step_messages``.

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
        if dms_path == "skip_find" and not skip_contact_find:
            note(
                "dms_contact_path=skip_find in form data — real Siebel still runs Stage 1 Contact Find "
                "(mobile + Go) so the existing customer is loaded in the correct Siebel context."
            )

        contact_url = (urls.contact or "").strip()
        in_transit_state = False

        if SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES:
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
            step("Video SOP (Find Contact Enquiry): Find → Contact → mobile → Go → open All Enquiries; then stop.")
            form_trace(
                "v1_find_contact",
                "Global Find → Contact (Mobile Phone) + Go",
                "goto_contact_find_URL_then_prepare_Find_Contact_fill_mobile_FindGo",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
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
                stage_msg="Video SOP: Find customer by mobile (Contact view).",
            )
            if not ok_find:
                step("Stopped: could not complete Find by mobile on contact view.")
                out["error"] = (
                    "Siebel: video SOP — could not fill mobile or run Find/Go on the contact view. "
                    "Check Find pane, iframe selectors, and DMS_SIEBEL_* tuning."
                )
                return out
            contact_matched = _siebel_ui_suggests_contact_match(page, mobile)
            note(f"DECISION: contact_table_match_after_find={contact_matched!r}")
            if not contact_matched:
                note(
                    "No contact rows after Find/Go — Add Enquiry branch: vehicle chassis/VIN + engine, "
                    "Enquiry tab, Opportunity Form:New, DB fields, Ctrl+S."
                )
                ae_ok, ae_detail = _add_enquiry_opportunity(
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
                    step("Stopped: Add Enquiry branch failed (empty contact search).")
                    out["error"] = (
                        "Siebel: video SOP — contact search returned no table rows and Add Enquiry did not complete. "
                        f"{ae_detail or 'See Playwright_DMS.txt [NOTE] lines for the failing step.'}"
                    )
                    return out
                ms_done("Add enquiry saved")
                note("Add Enquiry saved; resuming normal Find→Contact mobile flow.")
                form_trace(
                    "v1b_refind_after_add_enquiry",
                    "Global Find → Contact (Mobile Phone) + Go",
                    "rerun_find_mobile_after_add_enquiry_then_continue_normal_route",
                    contact_url_truncated=contact_url[:200],
                    mobile_phone=mobile,
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
                    stage_msg="Post Add Enquiry: re-find customer by mobile (Contact view).",
                )
                if not ok_refind:
                    step("Stopped: Add Enquiry saved but post-save re-find by mobile failed.")
                    out["error"] = (
                        "Siebel: Add Enquiry was saved, but the follow-up Find→Contact mobile query "
                        "did not complete."
                    )
                    return out
                contact_matched = _siebel_ui_suggests_contact_match(page, mobile)
                note(f"DECISION: contact_table_match_after_add_enquiry_refind={contact_matched!r}")
                if not contact_matched:
                    step("Stopped: Add Enquiry saved but customer still not visible after re-find.")
                    out["error"] = (
                        "Siebel: Add Enquiry was saved, but contact search still returned no table row "
                        "after re-find."
                    )
                    return out
            form_trace(
                "v2_drill_and_nav",
                "Search Results + Contacts detail",
                "Siebel_Find_tab_optional_then_link_hit_then_click_first_name_then_fill_Relations_Name_only",
                mobile_phone=mobile,
                first_name=first,
                care_of=care_of,
            )
            if not _siebel_video_path_after_find_go_to_all_enquiries(
                page,
                mobile=mobile,
                first_name=first,
                care_of=care_of,
                address_line_1=addr,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
            ):
                step("Stopped: video SOP failed while opening customer record or filling Relation's Name.")
                out["error"] = (
                    "Siebel: video SOP — after Find/Go, could not fill Relation's Name from care_of. "
                    "Confirm right-pane selectors/labels and iframe scope."
                )
                return out
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
            form_trace(
                "v4_create_order",
                "Vehicle Sales / Sales Orders",
                "vehicle_sales_new_order_then_pick_contact_then_vin_search_price_allocate",
                mobile_phone=mobile,
                full_chassis=full_chassis,
            )
            ok_order, order_err, order_scraped = _create_order(
                page,
                mobile=mobile,
                full_chassis=full_chassis,
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
                if order_scraped.get("ex_showroom_price"):
                    veh["vehicle_price"] = order_scraped.get("ex_showroom_price")
                    veh["ex_showroom_price"] = order_scraped.get("ex_showroom_price")
                out["vehicle"] = veh

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
                "Contact view — Find pane (mobile search)",
                "goto_DMS_REAL_URL_CONTACT_expand_Find_fill_Mobile_Phone_click_FindGo",
                contact_url_truncated=contact_url[:200],
                mobile_phone=mobile,
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
                stage_msg="Stage 1: Find customer by mobile (Contact view).",
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
            matched = _siebel_ui_suggests_contact_match(page, mobile)
            note(f"DECISION: customer_found_from_contact_grid={matched!r} (heuristic: mobile in table row with ≥3 cells).")
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
            Vehicle list search/scrape; if grid suggests In Transit → receipt,
            Pre Check, PDI.             Sets ``in_transit_state`` and ``out["vehicle"]``.
            Returns False on configuration or vehicle-search failure (``out["error"]`` set).
            """
            nonlocal in_transit_state
            note("Stage 5: vehicle list search, scrape, and In-Transit handling.")
            step("Vehicle flow: key / chassis / engine search (stage 5).")
            vehicle_url = (urls.vehicle or "").strip()
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
                step("Stopped: DMS_REAL_URL_VEHICLE is not configured.")
                out["error"] = (
                    "Siebel: set DMS_REAL_URL_VEHICLE to the Auto Vehicle List (or stock search) "
                    "GotoView URL so key/chassis/engine search can run."
                )
                return False

            scraped, veh_err = _siebel_goto_vehicle_list_and_scrape(
                page,
                vehicle_url,
                key_p,
                frame_p,
                engine_p,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
                note=note,
                form_trace=form_trace,
            )
            if veh_err:
                step("Stopped during vehicle list search.")
                out["error"] = veh_err
                return False

            out["vehicle"] = scraped
            out["dms_siebel_forms_filled"] = bool(customer_save_clicked)
            if not customer_save_clicked:
                note(
                    "Siebel Save was not detected on the customer/enquiry step — vehicle search still ran; "
                    "verify the contact record in Hero Connect. dms_siebel_forms_filled=false for API consumers."
                )
            step("Stage 5: vehicle list query completed; result row read when present.")

            in_transit_state = bool(scraped.get("in_transit"))
            note(f"DECISION: vehicle_in_transit={in_transit_state!r} (from scraped grid text).")
            form_trace(
                "5_vehicle_list",
                "Auto Vehicle List — results grid (scraped row)",
                "read_first_matching_row_from_grid",
                key_num=str(scraped.get("key_num") or ""),
                frame_num=str(scraped.get("frame_num") or ""),
                engine_num=str(scraped.get("engine_num") or ""),
                model=str(scraped.get("model") or ""),
                in_transit=in_transit_state,
            )

            if in_transit_state:
                note("Stage 5b: vehicle grid suggests In Transit — Process Receipt, Pre Check, PDI.")
                step("Vehicle appears in transit (receipt / pre-check / PDI path).")
                recv_u = (urls.vehicles or "").strip()
                if recv_u:
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
                        step("Vehicle received — Process Receipt was completed in DMS.")
                    else:
                        note("Process Receipt control not found; operator may complete receipt manually.")
                        step(
                            "Receipt / in-transit screen opened; Process Receipt was not found — "
                            "complete receiving manually if required."
                        )
                    ms_done("Vehicle received")
                else:
                    note(
                        "DMS_REAL_URL_VEHICLES is not set — cannot navigate to receipt/in-transit view; "
                        "set it to HMCL In Transit (or equivalent) GotoView URL."
                    )
                    step("Receipt URL (DMS_REAL_URL_VEHICLES) is not set — skipped receiving in UI.")

                _siebel_run_precheck_and_pdi(
                    page,
                    precheck_url=urls.precheck,
                    pdi_url=urls.pdi,
                    nav_timeout_ms=nav_timeout_ms,
                    action_timeout_ms=action_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                    ms_done=ms_done,
                    step=step,
                    form_trace=form_trace,
                )
            else:
                note("Stage 5b: vehicle not In Transit — receipt/PDI branch skipped.")
                step("Vehicle does not appear in transit.")

            return True

        def stage_6_generate_booking() -> None:
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
            else:
                note("Stage 6: Generate Booking control not found or not visible.")
                step("Generate Booking was not found — complete manually if required (stage 6).")

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
                if _add_enquiry_opportunity(
                    page,
                    dms_values,
                    urls,
                    action_timeout_ms=action_timeout_ms,
                    nav_timeout_ms=nav_timeout_ms,
                    content_frame_selector=content_frame_selector,
                    note=note,
                    form_trace=form_trace,
                    vehicle_merge=out.setdefault("vehicle", {}),
                )[0]:
                    created_basic = True
                    ms_done("Add enquiry saved")
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
                    "(or Mobile Phone # is missing in form_dms_view). "
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
            ):
                out["error"] = (
                    "Siebel skip_find: mandatory re-find failed — could not run Find by mobile on Contact view."
                )
                return out
            fill_relation_name_from_care_of(False)
            step("skip_find: stages 2–4 complete (basic → re-find → care-of).")

        if not stage_5_vehicle_flow():
            return out

        stage_6_generate_booking()
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
    )
