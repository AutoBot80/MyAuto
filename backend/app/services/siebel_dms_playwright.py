"""
Hero Connect / Oracle Siebel Open UI — Playwright helpers for real DMS automation.

**Linear SOP** in ``run_hero_siebel_dms_flow`` (BRD §6.1a aligned): **Find → mobile → Go**; optional
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
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeout

from app.config import (
    DMS_SIEBEL_AUTO_IFRAME_SELECTORS,
    DMS_SIEBEL_INTER_ACTION_DELAY_MS,
    DMS_SIEBEL_POST_GOTO_WAIT_MS,
)

logger = logging.getLogger(__name__)


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
        if select_contact_on_native_selects(root):
            return True
        if open_find_dropdown_then_contact(page_, root):
            return True
        return False

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
    """Click Find / Go / Query on Siebel toolbar (common on list & form applets)."""

    def try_on_fl(fl) -> bool:
        for role, name_pat in (
            ("button", re.compile(r"(Find|Go|Query)", re.I)),
            ("link", re.compile(r"(Find|Go|Query)", re.I)),
        ):
            try:
                loc = fl.get_by_role(role, name=name_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s (%s) in scoped frame", role, name_pat.pattern)
                    return True
            except Exception:
                continue
        for css in (
            'input[type="submit"][value*="Find" i]',
            'input[type="button"][value*="Find" i]',
            'button[title*="Find" i]',
            'a[title*="Find" i]',
        ):
            try:
                loc = fl.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s (scoped)", css[:60])
                    return True
            except Exception:
                continue
        return False

    for fl in _iter_frame_locator_roots(page, content_frame_selector):
        if try_on_fl(fl):
            return True

    for frame in _ordered_frames(page):
        for role, name_pat in (
            ("button", re.compile(r"(Find|Go|Query)", re.I)),
            ("link", re.compile(r"(Find|Go|Query)", re.I)),
        ):
            try:
                loc = frame.get_by_role(role, name=name_pat).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s (%s)", role, name_pat.pattern)
                    return True
            except Exception:
                continue
        for css in (
            'input[type="submit"][value*="Find" i]',
            'input[type="button"][value*="Find" i]',
            'button[title*="Find" i]',
            'a[title*="Find" i]',
        ):
            try:
                loc = frame.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    loc.click(timeout=timeout_ms)
                    logger.info("siebel_dms: clicked %s", css[:60])
                    return True
            except Exception:
                continue
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
    filled_mobile = _try_fill_field(
        page,
        _mobile_selectors(mobile_aria_hints),
        mobile,
        timeout_ms=action_timeout_ms,
        content_frame_selector=content_frame_selector,
        visible_timeout_ms=_mobile_vis,
    )
    if not filled_mobile:
        filled_mobile = _try_fill_mobile_semantic(
            page,
            mobile,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            extra_hints=mobile_aria_hints,
            label_visible_ms=_mobile_vis,
        )
    if not filled_mobile:
        filled_mobile = _try_fill_mobile_dom_scan(page, mobile)
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

    Prefer the staged flow in ``run_hero_siebel_dms_flow`` (basic save → re-find → care-of).
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
                'select[aria-label*="Relation" i]',
                'select[aria-label*="S/O" i]',
            ],
            relation,
            timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            prefer_second_if_duplicate=dup,
        )


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

    def mark_precheck(clicked: bool, ok_msg: str, fail_msg: str) -> None:
        if clicked:
            note(ok_msg)
            ms_done("Pre check completed")
        else:
            note(fail_msg)

    if pu and du and pu == du:
        _goto(page, du, "pdi_precheck_same_url", nav_timeout_ms=nav_timeout_ms)
        _siebel_after_goto_wait(page, floor_ms=1000)
        pc = _try_click_precheck_complete(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        )
        mark_precheck(
            pc,
            "Clicked Pre Check complete on combined PreCheck/PDI view.",
            "Pre Check control not found on combined view; operator may complete Pre Check manually.",
        )
        _safe_page_wait(page, 600, log_label="precheck_pdi_gap")
        pdi_ok = _try_click_pdi_submit(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
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
        pc = _try_click_precheck_complete(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        )
        mark_precheck(
            pc,
            "Clicked Pre Check complete.",
            "Pre Check control not found; operator may complete Pre Check manually.",
        )
        _safe_page_wait(page, 600, log_label="after_precheck_view")
        if pc:
            say("Pre-check step was completed on the pre-check view.")
        else:
            say("Pre-check view opened; complete control was not found — finish pre-check manually if needed.")

    if du:
        _goto(page, du, "pdi", nav_timeout_ms=nav_timeout_ms)
        _siebel_after_goto_wait(page, floor_ms=1000)
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
    Hero Connect / Siebel **linear SOP** (stages 1–8 inside the main ``try``):

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
    father = (dms_values.get("father_husband_name") or "").strip()
    relation = (dms_values.get("relation_prefix") or "").strip()
    key_p = (dms_values.get("key_partial") or "").strip()
    frame_p = (dms_values.get("frame_partial") or "").strip()
    engine_p = (dms_values.get("engine_partial") or "").strip()
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
        log_fp.write(f"father_husband_name={father!r}\n")
        log_fp.write(f"relation_prefix={relation!r}\n")
        log_fp.write(f"key_partial={key_p!r}\n")
        log_fp.write(f"frame_partial={frame_p!r}\n")
        log_fp.write(f"engine_partial={engine_p!r}\n")
        cu = (urls.contact or "").strip()
        log_fp.write(f"url_contact_truncated={cu[:200]!r}\n")
        log_fp.write(f"url_enquiry_truncated={(urls.enquiry or '')[:200]!r}\n")
        log_fp.write(f"url_vehicle_truncated={(urls.vehicle or '')[:200]!r}\n")
        log_fp.write("\n--- trace ---\n\n")
        log_fp.flush()

    def _exec_log(prefix: str, msg: str) -> None:
        if not log_fp or not (msg or "").strip():
            return
        try:
            log_fp.write(f"{datetime.now(timezone.utc).isoformat()} [{prefix}] {msg}\n")
            log_fp.flush()
        except OSError:
            pass

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

        def stage_4_add_care_of() -> None:
            note("Stage 4: care-of only (S/o, father/husband) — always runs per SOP.")
            step("Adding care-of / relation (stage 4 — mandatory after find / re-find).")
            _fill_siebel_care_of_only(
                page,
                father=father,
                relation=relation,
                action_timeout_ms=action_timeout_ms,
                content_frame_selector=content_frame_selector,
            )
            if father or relation:
                ms_done("Care of filled")
            save_customer_record(
                "Stage 4: Save after care-of update.",
                "Stage 4: Save not detected after care-of update.",
            )
            step("Care-of step completed (stage 4).")

        def stage_1_find_customer() -> tuple[bool, bool]:
            if not contact_url:
                step("Stopped: DMS_REAL_URL_CONTACT is not configured.")
                out["error"] = (
                    "Siebel: set DMS_REAL_URL_CONTACT to the Contact / Find view GotoView URL "
                    "so mobile search can run (stage 1)."
                )
                return False, False
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

            if in_transit_state:
                note("Stage 5b: vehicle grid suggests In Transit — Process Receipt, Pre Check, PDI.")
                step("Vehicle appears in transit (receipt / pre-check / PDI path).")
                recv_u = (urls.vehicles or "").strip()
                if recv_u:
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
                )
            else:
                note("Stage 5b: vehicle not In Transit — receipt/PDI branch skipped.")
                step("Vehicle does not appear in transit.")

            return True

        def stage_6_generate_booking() -> None:
            note("Stage 6: Generate Booking (always after vehicle processing per SOP).")
            step("Generate Booking (stage 6 — always, regardless of In Transit).")
            enq_u = (urls.enquiry or "").strip() or (urls.contact or "").strip()
            if enq_u:
                _goto(page, enq_u, "enquiry_for_booking", nav_timeout_ms=nav_timeout_ms)
                _siebel_after_goto_wait(page, floor_ms=1200)
            else:
                note("Stage 6: no DMS_REAL_URL_ENQUIRY or DMS_REAL_URL_CONTACT — booking may be on current view.")

            _safe_page_wait(page, 800, log_label="before_generate_booking")
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
            note("Invoice step pending (not automated).")
            step("Ready for invoice creation.")

        if not skip_contact_find:
            ok1, matched1 = stage_1_find_customer()
            if not ok1:
                return out
            created_basic = stage_2_create_enquiry_if_needed(matched1)
            if not stage_3_refind_customer(created_basic):
                return out
            stage_4_add_care_of()
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
            _goto(page, enquiry_url, "enquiry_or_contact", nav_timeout_ms=nav_timeout_ms)
            _siebel_after_goto_wait(page, floor_ms=1400)

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
            stage_4_add_care_of()
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
