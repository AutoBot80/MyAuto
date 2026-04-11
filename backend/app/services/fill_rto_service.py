"""Per-row Vahan site fill logic. Called by rto_payment_service during batch processing.

Implements the 6-screen Playwright SOP for new vehicle registration on the
Vahan (parivahan.gov.in) dealer portal.

Browser lifetime: does not call ``Browser.close()``, ``BrowserContext.close()``, or ``Page.close()`` —
the operator Vahan tab stays open for the next row or manual use (same policy as Fill DMS).
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PwTimeout

from app.config import VAHAN_BASE_URL, VAHAN_DEALER_HOME_URL, get_ocr_output_dir, get_uploads_dir
from app.services.handle_browser_opening import get_or_open_site_page

logger = logging.getLogger(__name__)

_rto_action_log: contextvars.ContextVar["RtoActionLog | None"] = contextvars.ContextVar(
    "rto_action_log", default=None
)
_current_screen: contextvars.ContextVar[str] = contextvars.ContextVar(
    "rto_current_screen", default=""
)


# India Standard Time (no DST) — fixed offset avoids Windows ``tzdata`` dependency for ``zoneinfo``.
_IST = timezone(timedelta(hours=5, minutes=30))


class RtoActionLog:
    """Per-run action log under ``ocr_output/{dealer_id}/{mobile}_RTO.txt``.

    Each ``fill_rto_row`` run **overwrites** the file (no carry-over from prior runs).
    Timestamps use **Asia/Kolkata (IST)**.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._started = False

    def line(self, message: str) -> None:
        ts = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S IST")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            mode = "w" if not self._started else "a"
            self._started = True
            with self.path.open(mode, encoding="utf-8") as f:
                f.write(f"[{ts}] {message}\n")
        except OSError as e:
            logger.warning("fill_rto: RTO log write failed %s: %s", self.path, e)


def _set_screen(label: str) -> None:
    """Set the current screen label that prefixes all subsequent log lines."""
    _current_screen.set(label)


def _rto_log(msg: str) -> None:
    log = _rto_action_log.get()
    if log is not None:
        screen = _current_screen.get()
        prefix = f"[{screen}] " if screen else ""
        log.line(f"{prefix}{msg}")


def _mobile_digits_for_filename(mobile: str | None) -> str:
    d = re.sub(r"\D", "", str(mobile or ""))
    if len(d) >= 10:
        return d[-10:]
    if d:
        return d.zfill(10)[:10]
    return "unknown_mobile"

# Playwright locator/action waits (ms). User preference: 10s.
_DEFAULT_TIMEOUT_MS = 10_000
_LONG_TIMEOUT_MS = 10_000
# Delay after each discrete UI action (s) — not a Playwright timeout.
_ACTION_WAIT_S = 0.2

# Vahan spells this either "Registration" or "Registeration" depending on build / locale.
_ENTRY_NEW_REG_LABEL_RE = re.compile(r"Entry-New Regist(?:ration|eration)", re.IGNORECASE)

_VAHAN_SESSION_DEAD_PATTERNS = (
    "/session/warning",
    "/ui/login/login",
    "swecmd=login",
)


class VahanSessionExpired(RuntimeError):
    """Raised when the Vahan portal redirects to login/session-expired mid-automation."""


def _assert_vahan_session_alive(page: Page) -> None:
    """Check the current URL; raise ``VahanSessionExpired`` if the site kicked us to login."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return
    for pat in _VAHAN_SESSION_DEAD_PATTERNS:
        if pat in url:
            msg = f"Vahan session expired (redirected to {page.url[:200]}). Please re-login and retry."
            _rto_log(f"SESSION EXPIRED: {msg}")
            raise VahanSessionExpired(msg)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _transform_dealer_rto(raw_rto_name: str) -> str:
    """'RTO-Bharatpur' -> 'BHARATPUR RTO'  (strip first 4 chars, upper, append ' RTO')."""
    if not raw_rto_name or len(raw_rto_name) <= 4:
        return (raw_rto_name or "").upper()
    return raw_rto_name[4:].strip().upper() + " RTO"


# English month labels — avoids locale-dependent ``strftime('%b')`` on Windows.
_VAHAN_MONTH_EN = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _fmt_date(d: date | datetime | str | None) -> str:
    """Format a date as dd-Mon-yyyy (English month) for Vahan workbench date fields."""
    if d is None:
        return ""
    if isinstance(d, str):
        s = d.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        else:
            return d
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day:02d}-{_VAHAN_MONTH_EN[d.month - 1]}-{d.year}"


def _init_cap_place_name(s: str) -> str:
    """Init-cap each word so Vahan district/city options match (e.g. ``BHARATPUR`` → ``Bharatpur``)."""
    s = (s or "").strip()
    if not s:
        return ""
    parts: list[str] = []
    for w in s.split():
        if not w:
            continue
        parts.append(w[:1].upper() + w[1:].lower() if len(w) > 1 else w.upper())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Document resolution
# ---------------------------------------------------------------------------

_DOC_PATTERNS: list[tuple[str, list[str], list[str]]] = [
    ("FORM 20", ["*Form_20*", "*Form 20*", "*FORM_20*", "*FORM 20*"], [".pdf"]),
    ("FORM 21", ["*Sale_Certificate*", "*Sale Certificate*", "*Form_21*", "*Form 21*", "*FORM_21*"], [".pdf"]),
    # Underscore optional: e.g. ``8905969604_Form22.pdf``
    ("FORM 22", ["*Form_22*", "*Form22*", "*FORM22*", "*Form 22*", "*FORM_22*", "*FORM 22*"], [".pdf"]),
    ("INSURANCE CERTIFICATE", ["*Insurance*"], [".pdf"]),
    ("INVOICE ORIGINAL", ["*GST_Retail_Invoice*", "*GST*Invoice*", "*Tax_Invoice*", "*Tax Invoice*", "*Retail_Invoice*"], [".pdf"]),
    # ``*Aadhaar*`` vs ``Aadhar``: include ``*aadhar*`` (one 'a').  Do not use a bare ``*back*`` glob here;
    # ``_resolve_sale_documents`` skips ``*_back*`` filenames for FRONT so ``Aadhar_back`` is not taken as front.
    ("AADHAAR_FRONT", ["*Aadhar*front*", "*aadhaar*front*", "*adhaar*front*",
                        "*Aadhar*consolidated*", "*aadhaar*consolidated*", "*adhaar*consolidated*",
                        "*Aadhar_Card*", "*Aadhaar*", "*aadhar*"], [".jpg", ".jpeg", ".png"]),
    # Explicit ``*_back*`` — ``*Aadhar*back*`` alone becomes substring ``aadharback`` and misses ``Aadhar_back``.
    ("AADHAAR_BACK", ["*Aadhar_back*", "*aadhaar_back*", "*adhaar_back*"], [".jpg", ".jpeg", ".png"]),
    ("OWNER UNDERTAKING FORM", ["*Detail*", "*Details*", "*Sales_Detail*", "*Detail_Sheet*", "*Sales Detail*"], [".pdf", ".jpg", ".jpeg", ".png"]),
]


def _resolve_sale_documents(sale_dir: Path) -> dict[str, Path | None]:
    """Map Vahan sub-category names to files found in the sale directory."""
    result: dict[str, Path | None] = {}
    if not sale_dir.is_dir():
        logger.warning("fill_rto: sale directory not found: %s", sale_dir)
        for cat, _, _ in _DOC_PATTERNS:
            result[cat] = None
        return result

    all_files = list(sale_dir.iterdir())
    for cat, patterns, extensions in _DOC_PATTERNS:
        found: Path | None = None
        for pat in patterns:
            for f in all_files:
                if not f.is_file():
                    continue
                name_lower = f.name.lower()
                if cat == "AADHAAR_FRONT" and "_back" in name_lower:
                    continue
                pat_lower = pat.lower().replace("*", "")
                parts = [p for p in pat_lower.split("*") if p] if "*" in pat_lower else [pat_lower]
                if all(p in name_lower for p in parts) and f.suffix.lower() in extensions:
                    found = f
                    break
            if found:
                break
        result[cat] = found
    return result


# ---------------------------------------------------------------------------
# Playwright micro-helpers
# ---------------------------------------------------------------------------

def _pause() -> None:
    time.sleep(_ACTION_WAIT_S)


def _dump_page_state(page: Page, context: str) -> None:
    """Dump frames and visible elements into the RTO log when a selector is not found."""
    _rto_log(f"=== PAGE STATE DUMP ({context}) ===")
    try:
        _rto_log(f"url: {(page.url or '')[:300]}")
    except Exception:
        _rto_log("url: (could not read)")

    try:
        frames = page.frames
        _rto_log(f"frames: {len(frames)}")
        for i, frame in enumerate(frames):
            try:
                name = frame.name or "(main)"
                url = (frame.url or "")[:200]
                _rto_log(f"  frame[{i}] name={name!r} url={url}")
            except Exception:
                _rto_log(f"  frame[{i}] (could not read)")
    except Exception as e:
        _rto_log(f"frames: error listing — {e}")

    _JS_ELEMENT_SNAPSHOT = """() => {
        const els = [];
        for (const el of document.querySelectorAll('input, select, textarea, button, a, [role], label, h1, h2, h3, h4, span.ui-outputlabel')) {
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') continue;
            const tag = el.tagName.toLowerCase();
            const id = el.id || '';
            const name = el.getAttribute('name') || '';
            const type = el.getAttribute('type') || '';
            const role = el.getAttribute('role') || '';
            const value = (el.value || '').substring(0, 60);
            const text = (el.innerText || '').substring(0, 60).replace(/\\n/g, ' ');
            const cls = (el.className || '').substring(0, 80);
            els.push({tag, id, name, type, role, value, text, cls});
            if (els.length >= 150) break;
        }
        return els;
    }"""

    def _log_element_list(tag: str, elements: list[dict]) -> None:
        _rto_log(f"  [{tag}] visible interactive elements ({len(elements)}):")
        for el in elements:
            parts = [el.get("tag", "")]
            if el.get("id"):
                parts.append(f"id={el['id']!r}")
            if el.get("name"):
                parts.append(f"name={el['name']!r}")
            if el.get("type"):
                parts.append(f"type={el['type']!r}")
            if el.get("role"):
                parts.append(f"role={el['role']!r}")
            if el.get("value"):
                parts.append(f"value={el['value']!r}")
            if el.get("text"):
                parts.append(f"text={el['text']!r}")
            if el.get("cls"):
                parts.append(f"class={el['cls']!r}")
            _rto_log(f"    {' '.join(parts)}")

    for fi, frame in enumerate(page.frames):
        try:
            tag = f"frame[{fi}] {frame.name or 'main'}"
            snapshot = frame.evaluate(_JS_ELEMENT_SNAPSHOT)
            _log_element_list(tag, snapshot)
        except Exception as e:
            _rto_log(f"  [frame[{fi}]] element snapshot error: {e}")

    _rto_log("=== END PAGE STATE DUMP ===")


def _click(page: Page, selector: str, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Wait for a selector and click it."""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click(timeout=timeout)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT click: {label} selector={selector}")
        _dump_page_state(page, f"click failed: {label}")
        raise
    logger.debug("fill_rto: clicked %s (%s)", selector, label)
    if label:
        _rto_log(f"click: {label}")


def _fill(page: Page, selector: str, value: object, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Clear and fill a text field. Coerces ``value`` to ``str`` (queue rows often pass mobile/pin as int)."""
    if value is None:
        return
    text = str(value)
    if text == "":
        return
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.fill(text, timeout=timeout)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT fill: {label} selector={selector} value={text[:40]}")
        _dump_page_state(page, f"fill failed: {label}")
        raise
    logger.debug("fill_rto: filled %s = %s (%s)", selector, text[:40], label)
    if label:
        _rto_log(f"fill: {label} = {text[:80]}{'…' if len(text) > 80 else ''}")


def _close_workbench_datepicker_if_open(page: Page) -> None:
    """Close PrimeFaces / jQuery UI datepicker overlay so it does not block later steps."""
    cal = page.locator("div.ui-datepicker, div[id^='ui-datepicker-div']").first
    try:
        cal.wait_for(state="visible", timeout=250)
    except PwTimeout:
        return
    page.keyboard.press("Escape")
    _pause()
    try:
        cal.wait_for(state="hidden", timeout=_DEFAULT_TIMEOUT_MS)
    except PwTimeout:
        page.keyboard.press("Escape")
        _pause()


def _fill_workbench_purchase_date(
    page: Page, value: object, *, timeout: int = _DEFAULT_TIMEOUT_MS
) -> None:
    """Fill workbench Purchase/Delivery Date.

    Click/focus from ``fill()`` often opens the jQuery datepicker overlay. Prefer setting the
    value in-page (no focus) when possible, then dismiss any open calendar with Escape.
    """
    if value is None:
        return
    text = str(value).strip()
    if text == "":
        return
    label = "Purchase/Delivery Date"
    sel = (
        "[id='workbench_tabview:purchase_dt_input'], "
        "input[id*='purchase_dt'], input[id*='purchaseDate'], "
        "input[id*='deliveryDate'], input[name*='purchaseDate'], input[name*='purchase_dt']"
    )
    loc = page.locator(sel).first
    try:
        loc.wait_for(state="attached", timeout=timeout)
        loc.scroll_into_view_if_needed(timeout=timeout)

        # 1) Set value without Playwright focus (reduces unwanted calendar popups).
        try:
            loc.evaluate(
                """(el, v) => {
                    el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                text,
            )
        except Exception:
            try:
                loc.fill(text, timeout=timeout)
            except Exception:
                loc.fill(text, timeout=timeout, force=True)

        _pause()
        _close_workbench_datepicker_if_open(page)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT fill: {label} selector={sel} value={text[:40]}")
        _dump_page_state(page, f"fill failed: {label}")
        raise
    logger.debug("fill_rto: filled purchase date = %s", text[:40])
    _rto_log(f"fill: {label} = {text[:80]}{'…' if len(text) > 80 else ''}")


def _select(page: Page, selector: str, value: object, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Select an option from a <select> dropdown by visible text."""
    if value is None:
        return
    text = str(value)
    if text == "":
        return
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.select_option(label=text, timeout=timeout)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT select: {label} selector={selector} value={text}")
        _dump_page_state(page, f"select failed: {label}")
        raise
    logger.debug("fill_rto: selected %s = %s (%s)", selector, text, label)
    if label:
        _rto_log(f"select: {label} = {text}")


def _pf_native_select_selector(wrapper_id: str) -> str:
    """CSS locator for ``select`` inside a PrimeFaces selectOneMenu (ids may contain ``:``)."""
    sid = f"{wrapper_id}_input"
    if ":" in wrapper_id:
        return f'select[id="{sid}"]'
    return f"select#{sid}"


def _pf_selectonemenu_panel_selector(wrapper_id: str) -> str:
    """CSS locator for the overlay panel ``div[id$='_panel']`` (ids may contain ``:``)."""
    pid = f"{wrapper_id}_panel"
    if ":" in wrapper_id:
        return f'div[id="{pid}"]'
    return f"div#{pid}"


def _close_pf_selectonemenu_overlay(page: Page, wrapper_id: str, *, use_escape: bool = True) -> None:
    """Close an open PrimeFaces selectOneMenu list so it does not stay on screen.

    ``use_escape=False``: only wait for the panel to hide (Escape can revert a selection on
    some PrimeFaces builds when the panel is already closing).
    """
    panel = page.locator(_pf_selectonemenu_panel_selector(wrapper_id)).first
    try:
        panel.wait_for(state="visible", timeout=400)
        if use_escape:
            page.keyboard.press("Escape")
            _pause()
        panel.wait_for(state="hidden", timeout=_DEFAULT_TIMEOUT_MS)
    except PwTimeout:
        pass


def _select_choice_number_no(page: Page, *, timeout: int = _DEFAULT_TIMEOUT_MS) -> None:
    """Set *Choice Number / Fancy / Retention* to **NO** and wait until the PF label reflects it.

    Uses the native ``select`` plus ``input``/``change`` events so PrimeFaces updates the widget,
    then verifies ``label#regnNoSelectionForAPS_label``. Falls back to overlay click.
    Does **not** send Escape after choosing NO (can leave the value unset on some PF versions).
    """
    wrapper_id = "regnNoSelectionForAPS"

    def _wait_label_no() -> None:
        try:
            page.wait_for_function(
                """() => {
                    const el = document.querySelector('label#regnNoSelectionForAPS_label');
                    if (!el) return false;
                    const t = (el.textContent || '').toUpperCase();
                    return t.includes('NO') && !t.includes('SELECT');
                }""",
                timeout=8000,
            )
        except PwTimeout:
            logger.warning("fill_rto: choice number label did not show NO in time")

    native = page.locator('select[id="regnNoSelectionForAPS_input"]').first
    try:
        native.wait_for(state="attached", timeout=timeout)
        try:
            native.select_option(label="NO", timeout=timeout)
        except PwTimeout:
            native.select_option(label=re.compile(r"^\s*NO\s*$", re.I), timeout=timeout)
        native.evaluate(
            """(el) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        _pause()
        _wait_for_progress_close(page)
        _wait_label_no()
    except Exception as e:
        logger.debug("fill_rto: choice NO via native select: %s", e)

    try:
        lbl = page.locator("label#regnNoSelectionForAPS_label").first
        t = (lbl.inner_text(timeout=2000) or "").upper()
        if "NO" not in t or "SELECT" in t:
            _select_pf_dropdown(
                page,
                "div#regnNoSelectionForAPS",
                "NO",
                label="Choice number opt (overlay fallback)",
                timeout=timeout,
                use_native_select=False,
            )
            _pause()
            _wait_for_progress_close(page)
            _wait_label_no()
    except Exception as e:
        logger.warning("fill_rto: choice NO overlay fallback: %s", e)

    # Close stuck panel without Escape (see docstring).
    try:
        panel = page.locator(_pf_selectonemenu_panel_selector(wrapper_id)).first
        panel.wait_for(state="hidden", timeout=3000)
    except PwTimeout:
        try:
            page.locator("div#regnNoSelectionForAPS").first.click(position={"x": 4, "y": 8})
            _pause()
        except Exception:
            pass

    try:
        lbl = page.locator("label#regnNoSelectionForAPS_label").first
        t = (lbl.inner_text(timeout=2000) or "").strip()
        if "NO" in t.upper() and "SELECT" not in t.upper():
            _rto_log(f"verified: Choice number shows NO (label={t!r})")
        else:
            _rto_log(f"WARNING: Choice number label after automation: {t!r} (expected NO)")
    except Exception as e:
        _rto_log(f"WARNING: could not read Choice number label: {e!s}")


def _pick_aadhaar_registration_not_available(page: Page, *, timeout: int = _DEFAULT_TIMEOUT_MS) -> None:
    """In the 'Aadhaar Based Registration Integration' modal, select the **Not Available** option."""
    dlg = page.locator(
        "[role='dialog']:has-text('Aadhaar'), "
        ".ui-dialog:has-text('Aadhaar'), "
        "div.ui-dialog:has-text('Aadhaar Based Registration')"
    ).first
    try:
        dlg.wait_for(state="visible", timeout=timeout)
    except PwTimeout:
        logger.debug("fill_rto: Aadhaar registration modal not shown")
        return
    try:
        lbl = dlg.locator("label:has-text('Not Available')").first
        lbl.wait_for(state="visible", timeout=timeout)
        lbl.click(timeout=timeout)
        _pause()
        _rto_log("dialog: Aadhaar registration — selected Not Available")
    except PwTimeout:
        try:
            rad = dlg.get_by_role("radio", name=re.compile(r"Not\s*Available", re.I)).first
            rad.click(timeout=timeout)
            _pause()
            _rto_log("dialog: Aadhaar registration — selected Not Available (radio role)")
        except Exception as e:
            logger.debug("fill_rto: Not Available in Aadhaar modal: %s", e)
            _rto_log("WARNING: could not select Not Available in Aadhaar registration dialog")


def _type_typeahead(page: Page, selector: str, value: object, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Type into a typeahead/autocomplete field and pick the first suggestion."""
    if value is None:
        return
    text = str(value)
    if text == "":
        return
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click()
        loc.fill("")
        loc.type(text, delay=50)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT typeahead: {label} selector={selector} value={text}")
        _dump_page_state(page, f"typeahead failed: {label}")
        raise
    _pause()
    suggestion = page.locator(".ui-autocomplete li, .ui-menu-item, [role='option']").first
    try:
        suggestion.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        suggestion.click()
    except PwTimeout:
        page.keyboard.press("Enter")
    logger.debug("fill_rto: typeahead %s = %s (%s)", selector, text, label)
    if label:
        _rto_log(f"typeahead: {label} = {text}")


def _select_pf_dropdown(
    page: Page,
    wrapper_selector: str,
    value: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT_MS,
    label: str = "",
    option_label_regex: re.Pattern | None = None,
    use_native_select: bool = True,
) -> None:
    """Select an item from a PrimeFaces ``ui-selectonemenu`` by visible text.

    Tries the hidden native ``select#{wrapper_id}_input`` first (fast, reliable) when
    ``wrapper_selector`` is ``div#something`` with an id.  Falls back to opening the
    overlay and clicking ``li.ui-selectonemenu-item``.

    Pass ``option_label_regex`` when the portal label varies (e.g. Registration vs Registeration).

    Set ``use_native_select=False`` when the portal does not refresh the visible label from
    the native ``<select>`` (operator only sees the change after an overlay click).
    """
    if not value and option_label_regex is None:
        return
    try:
        wrapper = page.locator(wrapper_selector).first
        wrapper.wait_for(state="visible", timeout=timeout)

        wrapper_id = wrapper.get_attribute("id") or ""

        # 1) Native <select> (PrimeFaces keeps options in sync; avoids overlay timing issues).
        if wrapper_id and use_native_select:
            native_sel = page.locator(_pf_native_select_selector(wrapper_id))
            try:
                if native_sel.count() > 0:
                    if option_label_regex is not None:
                        native_sel.select_option(label=option_label_regex, timeout=_DEFAULT_TIMEOUT_MS)
                        log_val = f"regex:{option_label_regex.pattern}"
                    else:
                        native_sel.select_option(label=value, timeout=_DEFAULT_TIMEOUT_MS)
                        log_val = value
                    logger.debug("fill_rto: native select %s = %s", wrapper_selector, log_val)
                    if label:
                        _rto_log(f"pf-dropdown (native): {label} = {log_val}")
                    _pause()
                    return
            except Exception:
                pass

        # 2) Custom overlay panel
        wrapper.click()
        _pause()

        if wrapper_id:
            panel_sel = _pf_selectonemenu_panel_selector(wrapper_id)
        else:
            panel_sel = "div.ui-selectonemenu-panel"
        items_panel = page.locator(panel_sel).first
        items_panel.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)

        if option_label_regex is not None:
            item = items_panel.locator("li.ui-selectonemenu-item").filter(has_text=option_label_regex)
        else:
            item = items_panel.locator("li.ui-selectonemenu-item").filter(
                has_text=re.compile(f"^\\s*{re.escape(value)}\\s*$", re.IGNORECASE),
            )
            if item.count() == 0:
                item = items_panel.locator("li.ui-selectonemenu-item").filter(has_text=value)
        item.first.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
        item.first.click(timeout=_DEFAULT_TIMEOUT_MS)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT pf-dropdown: {label} selector={wrapper_selector} value={value!r}")
        _dump_page_state(page, f"pf-dropdown failed: {label}")
        raise
    logger.debug("fill_rto: pf-dropdown %s = %s (%s)", wrapper_selector, value, label)
    if label:
        shown = value or (option_label_regex.pattern if option_label_regex else "")
        _rto_log(f"pf-dropdown (overlay): {label} = {shown}")
    _pause()


def _dismiss_dialog(page: Page, button_text: str = "OK", *, timeout: int = _DEFAULT_TIMEOUT_MS) -> None:
    """Click a dialog/popup button by its text."""
    btn = page.get_by_role("button", name=re.compile(button_text, re.IGNORECASE)).first
    try:
        btn.wait_for(state="visible", timeout=timeout)
        btn.click()
        logger.debug("fill_rto: dismissed dialog with '%s'", button_text)
        _rto_log(f"dialog: {button_text}")
    except PwTimeout:
        logger.debug("fill_rto: no dialog with '%s' found (timeout), continuing", button_text)


def _wait_for_progress_close(page: Page, timeout_ms: int = _LONG_TIMEOUT_MS) -> None:
    """Wait until a progress/loading overlay disappears."""
    try:
        overlay = page.locator(".ui-blockui, .blockUI, .loading-overlay, .ui-dialog-loading").first
        overlay.wait_for(state="hidden", timeout=timeout_ms)
    except PwTimeout:
        pass
    _pause()


def _upload_file(page: Page, file_path: Path, *, timeout: int = _DEFAULT_TIMEOUT_MS) -> None:
    """Set the file on the visible file input and wait for upload to settle."""
    try:
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(str(file_path), timeout=timeout)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT upload: {file_path.name}")
        _dump_page_state(page, f"upload failed: {file_path.name}")
        raise
    _rto_log(f"upload: {file_path.name}")
    _pause()
    _wait_for_progress_close(page)


# ---------------------------------------------------------------------------
# Screen implementations
# ---------------------------------------------------------------------------


def _workbench_pf_menu_label_has_value(page: Page, label_id: str) -> bool:
    """True if a PrimeFaces ``selectOneMenu`` label is not the empty / ``--SELECT--`` placeholder."""
    try:
        lab = page.locator(f'[id="{label_id}"]').first
        t = (lab.inner_text(timeout=3000) or "").strip()
        if not t:
            return False
        u = t.upper()
        if u == "--SELECT--" or u == "SELECT" or ("SELECT" in u and "--" in t):
            return False
        return True
    except Exception:
        return False


def _workbench_correspondence_state_is_set(page: Page) -> bool:
    """True if correspondence *State* already shows a value (Vahan often pre-fills; do not overwrite)."""
    return _workbench_pf_menu_label_has_value(page, "workbench_tabview:tf_c_state_label")


def _ensure_vahan_dealer_home_for_screen1(page: Page) -> None:
    """Reset the tab to **dealer home** so Screen 1 (``officeList`` / ``actionList`` / Show Form) is present.

    If the operator (or last run) left the tab on ``workbench.xhtml`` or elsewhere, Screen 1 selectors
    will not exist. We do **not** persist a step across restarts — we normalize by:

    1. Clicking the top **Home** link (IDs from live Vahan: ``homeId2`` / ``homeId1``) — no pixel coordinates.
    2. If that fails, ``page.goto(VAHAN_DEALER_HOME_URL)``.

    A **new browser profile** is unchanged: first load still goes through login; after that this keeps
    the flow aligned with Screen 1 → 2 → ….
    """
    _rto_log("ensure dealer home: resetting to Screen 1 entry (Home link or home.xhtml)")

    home_link_selectors = (
        "a#homeId2",
        "a[name='homeId2']",
        "a#homeId1",
        "nav.navbar a:has-text('Home')",
        "a:has-text('Home')",
    )
    clicked = False
    for sel in home_link_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=3000)
            loc.click(timeout=_DEFAULT_TIMEOUT_MS)
            clicked = True
            _rto_log(f"ensure dealer home: clicked Home ({sel})")
            break
        except Exception as exc:
            logger.debug("fill_rto: Home selector %s: %s", sel, exc)
            continue

    _pause()
    _wait_for_progress_close(page)

    try:
        page.locator("div#officeList").first.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        _rto_log("ensure dealer home: office list visible (Screen 1 ready)")
        return
    except PwTimeout:
        pass

    try:
        page.goto(VAHAN_DEALER_HOME_URL, wait_until="domcontentloaded", timeout=20_000)
        _rto_log(f"ensure dealer home: navigated to {VAHAN_DEALER_HOME_URL}")
        _pause()
        _wait_for_progress_close(page)
        page.locator("div#officeList").first.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        _rto_log("ensure dealer home: office list visible after goto")
    except Exception as exc:
        _rto_log(f"ensure dealer home: could not reach Screen 1 — {exc!s}")
        logger.warning("fill_rto: ensure dealer home failed: %s", exc)


def _screen_1(page: Page, office: str) -> None:
    """Screen 1: Select office, action, Show Form."""
    _set_screen("Screen 1")
    logger.info("fill_rto: Screen 1 — office=%s", office)
    _rto_log("--- Screen 1: Assigned office, Entry-New Registration, Show Form ---")

    _select_pf_dropdown(
        page,
        "div#officeList",
        office,
        label="Select Assigned Office",
    )
    _pause()

    _select_pf_dropdown(
        page,
        "div#actionList",
        "Entry-New Registration",
        label="Select Action",
        option_label_regex=_ENTRY_NEW_REG_LABEL_RE,
    )
    _pause()

    _click(page, "button#pending_action", label="Show Form")
    _pause()
    _wait_for_progress_close(page)

    _dismiss_dialog(page, "OK")


def _screen_2(page: Page, data: dict) -> None:
    """Screen 2: Chassis/engine, owner details, address, Save."""
    _set_screen("Screen 2")
    logger.info("fill_rto: Screen 2 — chassis=%s", data.get("chassis_num", "")[:8])
    _rto_log("--- Screen 2: Chassis, engine, owner, address, Save and file movement ---")

    # 2a: Chassis and engine (workbench uses ``chasi_no_new_entry`` / ``eng_no_new_entry`` — not *chassisNo*.)
    _fill(
        page,
        "#chasi_no_new_entry, input[id*='chasi_no'], input[id*='chassisNo'], input[name*='chassisNo']",
        data["chassis_num"],
        label="Chassis No",
    )
    _fill(
        page,
        "#eng_no_new_entry, input[id*='eng_no_new'], input[id*='engineNo'], input[name*='engineNo']",
        data["engine_short"],
        label="Engine No (last 5)",
    )
    _click(
        page,
        "#get_dtls_btn, button[name='get_dtls_btn'], input[value='Get Details'], button:has-text('Get Details')",
        label="Get Details",
    )
    _pause()
    _wait_for_progress_close(page)

    # 2b: Choice number = NO (native + PF events, overlay fallback; do not Escape — can revert value)
    _select_choice_number_no(page, timeout=_DEFAULT_TIMEOUT_MS)

    # Owner Details tab (workbench ids: ``purchase_dt_input``, ``tf_owner_name``, …)
    _fill_workbench_purchase_date(page, data["billing_date_str"], timeout=_DEFAULT_TIMEOUT_MS)

    # Ownership Type = INDIVIDUAL → opens "Aadhaar Based Registration Integration"; pick **Not Available**
    # so Son/Wife/Daughter of enables (must run before that field).
    try:
        _select_pf_dropdown(
            page,
            '[id="workbench_tabview:tf_owner_cd"]',
            "INDIVIDUAL",
            label="Ownership Type",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
    except PwTimeout:
        logger.debug("fill_rto: Ownership Type dropdown failed, skipping")

    _close_pf_selectonemenu_overlay(page, "workbench_tabview:tf_owner_cd")
    _pause()
    _wait_for_progress_close(page)
    _pick_aadhaar_registration_not_available(page, timeout=_DEFAULT_TIMEOUT_MS)

    _fill(
        page,
        "input[id*='tf_owner_name'], input[id*='ownerName'], input[name*='ownerName'], input[id*='owner_name']",
        data["customer_name"],
        label="Owner Name",
    )
    _pause()

    _fill(
        page,
        "input[id*='tf_f_name'], input[id*='sonWife'], input[id*='relation'], input[name*='sonWife']",
        data.get("care_of", ""),
        label="Son/Wife/Daughter of",
    )

    # Owner Category = OTHERS (workbench: ``ownerCatg`` — before Mobile per portal flow)
    try:
        _select_pf_dropdown(
            page,
            '[id="workbench_tabview:ownerCatg"]',
            "OTHERS",
            label="Owner Category",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
    except PwTimeout:
        logger.debug("fill_rto: Owner Category OTHERS failed, skipping")

    _close_pf_selectonemenu_overlay(page, "workbench_tabview:ownerCatg")
    _pause()

    # 2c: Mobile (workbench: ``tf_mobNo`` — not *mobileNo*.)
    _fill(
        page,
        "input[id*='tf_mobNo'], input[id*='mobileNo'], input[name*='mobileNo'], input[id*='mobNo']",
        data.get("mobile", ""),
        label="Mobile No",
    )

    # 2d: Address fields (workbench: ``tf_c_add1`` / ``tf_c_add2``)
    _fill(
        page,
        "input[id*='tf_c_add1'], input[id*='houseNo'], input[id*='streetName'], input[name*='houseNo']",
        data.get("address", ""),
        label="House no & street",
    )
    _fill(
        page,
        "input[id*='tf_c_add2'], input[id*='village'], input[id*='town'], input[id*='city'], input[name*='village']",
        data.get("city", ""),
        label="Village/Town/City",
    )

    # Correspondence State — skip if already pre-filled (avoids timeout on PF / wrong selector)
    state_val = (data.get("state") or "").strip()
    if state_val and not _workbench_correspondence_state_is_set(page):
        try:
            _select_pf_dropdown(
                page,
                '[id="workbench_tabview:tf_c_state"]',
                state_val,
                label="State (correspondence)",
                timeout=_DEFAULT_TIMEOUT_MS,
            )
        except PwTimeout:
            try:
                _type_typeahead(
                    page,
                    "[id='workbench_tabview:tf_c_state_focus'], input[id*='tf_c_state']",
                    state_val,
                    label="State (correspondence) typeahead",
                    timeout=_DEFAULT_TIMEOUT_MS,
                )
            except PwTimeout:
                logger.debug("fill_rto: correspondence state not set via PF/typeahead")
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:tf_c_state")
        _pause()
    elif state_val:
        _rto_log("skip: correspondence State already set on form")

    # District — use ``district`` from row, or default to **city**; if district option missing, retry with city
    district_raw_in = (data.get("district") or "").strip()
    city_raw_in = (data.get("city") or "").strip()
    district_val = _init_cap_place_name(district_raw_in)
    city_val_norm = _init_cap_place_name(city_raw_in)
    primary_district = district_val or city_val_norm
    if primary_district:
        try:
            _select_pf_dropdown(
                page,
                '[id="workbench_tabview:tf_c_district"]',
                primary_district,
                label="District (correspondence)",
                timeout=_DEFAULT_TIMEOUT_MS,
            )
        except PwTimeout:
            if district_raw_in and city_raw_in and district_raw_in.upper() != city_raw_in.upper():
                try:
                    _select_pf_dropdown(
                        page,
                        '[id="workbench_tabview:tf_c_district"]',
                        city_val_norm,
                        label="District (fallback to city)",
                        timeout=_DEFAULT_TIMEOUT_MS,
                    )
                except PwTimeout:
                    _rto_log("WARNING: District not set (tried district and city names)")
            else:
                _rto_log("WARNING: District not set")
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:tf_c_district")
        _pause()

    _fill(page, "input[id*='pin'], input[name*='pin']", data.get("pin", ""), label="Pin")
    _pause()

    # Same as Current Address checkbox
    try:
        same_addr = page.locator(
            "input[type='checkbox'][id*='samePermAdd'], "
            "input[type='checkbox'][id*='sameAddress'], "
            "input[type='checkbox'][id*='currentAddress'], "
            "label:has-text('Same as Current') input[type='checkbox']"
        ).first
        same_addr.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        if not same_addr.is_checked():
            same_addr.click()
            _rto_log("checkbox: Same as Current Address")
    except PwTimeout:
        logger.debug("fill_rto: 'Same as Current Address' checkbox not found, skipping")

    # Vehicle Class / Vehicle Category (partial vehicle block) — skip if already set
    _vh_class = "M-Cycle/Scooter"
    _vh_cat = "TWO WHEELER(NT)"
    if not _workbench_pf_menu_label_has_value(page, "workbench_tabview:partial_vh_class_label"):
        try:
            _select_pf_dropdown(
                page,
                '[id="workbench_tabview:partial_vh_class"]',
                _vh_class,
                label="Vehicle Class",
                timeout=_DEFAULT_TIMEOUT_MS,
            )
        except PwTimeout:
            logger.debug("fill_rto: Vehicle Class dropdown failed")
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:partial_vh_class")
        _pause()
    else:
        _rto_log("skip: Vehicle Class already set on form")

    _cat_labels = (
        "workbench_tabview:partial_vh_catg_label",
        "workbench_tabview:partial_vh_category_label",
    )
    _cat_already = any(
        _workbench_pf_menu_label_has_value(page, lid) for lid in _cat_labels
    )
    if not _cat_already:
        _cat_wrappers: tuple[tuple[str, str], ...] = (
            ("workbench_tabview:partial_vh_catg", '[id="workbench_tabview:partial_vh_catg"]'),
            ("workbench_tabview:partial_vh_category", '[id="workbench_tabview:partial_vh_category"]'),
        )
        for wid, wsel in _cat_wrappers:
            try:
                _select_pf_dropdown(
                    page,
                    wsel,
                    _vh_cat,
                    label="Vehicle Category",
                    timeout=_DEFAULT_TIMEOUT_MS,
                )
                _close_pf_selectonemenu_overlay(page, wid)
                _pause()
                _rto_log(f"Vehicle Category set via wrapper id={wid}")
                break
            except PwTimeout:
                continue
        else:
            _rto_log("WARNING: Vehicle Category could not be set (check portal ids)")
    else:
        _rto_log("skip: Vehicle Category already set on form")

    # 2e: Save and file movement
    _pause()
    _click(
        page,
        "input[value*='Save'], button:has-text('Save and file movement'), button:has-text('Save and File Movement')",
        label="Save and file movement",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause()
    _dismiss_dialog(page, "Yes")
    _pause()
    _dismiss_dialog(page, "Close", timeout=_DEFAULT_TIMEOUT_MS)


def _screen_3(page: Page, data: dict) -> str:
    """Screen 3: Home > Entry, Tax mode, Insurance, Hypothecation, Save. Returns application_id."""
    _set_screen("Screen 3")
    logger.info("fill_rto: Screen 3 — insurer=%s", data.get("insurer", "")[:20])
    _rto_log("--- Screen 3: Home, Entry, tax, insurance, hypothecation, application no ---")

    # 3a: Home then Entry
    _click(page, "a:has-text('Home'), button:has-text('Home'), [id*='home']", label="Home link")
    _pause()
    _wait_for_progress_close(page)

    _click(page, "input[value='Entry'], button:has-text('Entry'), a:has-text('Entry')", label="Entry button")
    _pause()
    _wait_for_progress_close(page)

    # 3b: Vehicle Details — Tax Mode
    try:
        _select(
            page,
            "select[id*='taxMode'], select[name*='taxMode']",
            "ONE TIME",
            label="Tax Mode",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
    except (PwTimeout, Exception):
        logger.debug("fill_rto: Tax Mode dropdown not found or failed, skipping")

    # 3c: Insurance Information
    try:
        _select(
            page,
            "select[id*='insuranceType'], select[name*='insuranceType']",
            "THIRD PARTY",
            label="Insurance Type",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
    except (PwTimeout, Exception):
        logger.debug("fill_rto: Insurance Type dropdown issue, trying typeahead")
        try:
            _type_typeahead(page, "input[id*='insuranceType']", "THIRD PARTY", label="Insurance Type typeahead", timeout=_DEFAULT_TIMEOUT_MS)
        except PwTimeout:
            pass

    if data.get("insurer"):
        try:
            _select(
                page,
                "select[id*='insuranceCompany'], select[name*='insuranceCompany']",
                data["insurer"],
                label="Insurance Company",
                timeout=_DEFAULT_TIMEOUT_MS,
            )
        except (PwTimeout, Exception):
            _type_typeahead(
                page,
                "input[id*='insuranceCompany'], input[name*='insuranceCompany']",
                data["insurer"],
                label="Insurance Company typeahead",
                timeout=_DEFAULT_TIMEOUT_MS,
            )

    _fill(
        page,
        "input[id*='policyNo'], input[id*='coverNote'], input[name*='policyNo']",
        data.get("policy_num", ""),
        label="Policy/Cover Note No",
    )
    _fill(
        page,
        "input[id*='insuranceFrom'], input[name*='insuranceFrom']",
        data.get("policy_from_str", ""),
        label="Insurance From",
    )
    _fill(
        page,
        "input[id*='idv'], input[id*='declaredValue'], input[name*='idv']",
        str(data.get("idv", "")) if data.get("idv") else "",
        label="Insurance Declared Value",
    )

    # 3d: Hypothecation (only if financier exists)
    financier = (data.get("financier") or "").strip()
    if financier:
        logger.info("fill_rto: Screen 3d — hypothecation, financier=%s", financier[:30])
        try:
            hyp_check = page.locator(
                "input[type='checkbox'][id*='hypothecated'], "
                "input[type='checkbox'][id*='isHypothecated']"
            ).first
            hyp_check.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            if not hyp_check.is_checked():
                hyp_check.click()
            _pause()
        except PwTimeout:
            logger.debug("fill_rto: hypothecation checkbox not found")

        try:
            _select(
                page,
                "select[id*='hypothecationType'], select[name*='hypothecationType']",
                "Hypothecation",
                label="Hypothecation Type",
                timeout=_DEFAULT_TIMEOUT_MS,
            )
        except (PwTimeout, Exception):
            pass

        try:
            _fill(
                page,
                "input[id*='financierName'], input[id*='financer'], input[name*='financierName']",
                financier,
                label="Financier Name",
            )
        except PwTimeout:
            _type_typeahead(
                page,
                "input[id*='financierName'], input[id*='financer']",
                financier,
                label="Financier typeahead",
                timeout=_DEFAULT_TIMEOUT_MS,
            )

        _fill(
            page,
            "input[id*='fromDate'][id*='hyp'], input[id*='hypothecationFrom'], input[name*='fromDate']",
            data.get("billing_date_str", ""),
            label="Hypothecation From Date",
        )

        if data.get("state"):
            try:
                _select(
                    page,
                    "select[id*='finState'], select[id*='hypState']",
                    data["state"],
                    label="Financier State",
                    timeout=_DEFAULT_TIMEOUT_MS,
                )
            except (PwTimeout, Exception):
                pass

        if data.get("pin"):
            _fill(
                page,
                "input[id*='finPin'], input[id*='hypPin'], input[name*='finPin']",
                data["pin"],
                label="Financier Pincode",
            )

    # Save and File Movement
    _click(
        page,
        "input[value*='Save'], button:has-text('Save and File Movement'), button:has-text('Save and file movement')",
        label="Save and File Movement (Screen 3)",
    )
    _pause()
    _dismiss_dialog(page, "Yes")
    _pause()

    # "Are you sure" popup
    _dismiss_dialog(page, "Yes", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()

    # Scrape Application No from success popup
    application_id = ""
    try:
        dialog_text = page.locator(
            ".ui-dialog-content, .ui-messages-info, .ui-growl-message, "
            "[class*='dialog'] [class*='message'], [class*='success']"
        ).first
        dialog_text.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        text = dialog_text.inner_text()
        match = re.search(r'(?:application\s*(?:no\.?|number)\s*[:\-]?\s*)(\S+)', text, re.IGNORECASE)
        if match:
            application_id = match.group(1).strip()
        else:
            nums = re.findall(r'[A-Z0-9]{5,}', text)
            if nums:
                application_id = nums[0]
        logger.info("fill_rto: scraped application_id=%s", application_id)
        if application_id:
            _rto_log(f"scraped: rto_application_id = {application_id}")
    except PwTimeout:
        logger.warning("fill_rto: could not scrape application number from popup")
        _rto_log("WARNING: could not scrape application number from popup")
        _dump_page_state(page, "scrape application number failed")

    _dismiss_dialog(page, "OK", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()

    return application_id


def _screen_4(page: Page) -> None:
    """Screen 4: Verify, File Movement, Dealer Document Upload."""
    _set_screen("Screen 4")
    logger.info("fill_rto: Screen 4 — Verify & Document Upload nav")
    _rto_log("--- Screen 4: Verify, Save Options / File Movement, Dealer Document Upload ---")

    # Scroll down and click Verify
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause()
    _click(page, "input[value='Verify'], button:has-text('Verify')", label="Verify", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()
    _wait_for_progress_close(page)

    # Save Options > File Movement
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause()

    try:
        _click(
            page,
            "input[value*='Save'], button:has-text('Save Options'), a:has-text('Save Options')",
            label="Save Options",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
        _pause()
        _click(
            page,
            "a:has-text('File Movement'), [id*='fileMovement'], button:has-text('File Movement')",
            label="File Movement menu item",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
    except PwTimeout:
        _click(
            page,
            "button:has-text('File Movement'), input[value*='File Movement']",
            label="File Movement direct",
            timeout=_DEFAULT_TIMEOUT_MS,
        )

    _pause()

    # Popups: State -> Save, then Yes
    _dismiss_dialog(page, "Save", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()
    _dismiss_dialog(page, "Yes", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()

    # Dealer Document Upload
    _click(
        page,
        "input[value*='Dealer Document Upload'], button:has-text('Dealer Document Upload'), a:has-text('Dealer Document Upload')",
        label="Dealer Document Upload",
    )
    _pause()
    _wait_for_progress_close(page)


def _screen_5(page: Page, docs: dict[str, Path | None]) -> None:
    """Screen 5: Upload documents per sub-category."""
    _set_screen("Screen 5")
    logger.info("fill_rto: Screen 5 — uploading %d document categories", len(docs))
    _rto_log("--- Screen 5: Document uploads by sub-category ---")

    upload_sequence: list[tuple[str, str | None]] = [
        ("FORM 20", "FORM 20"),
        ("FORM 21", "FORM 21"),
        ("FORM 22", "FORM 22"),
        ("INSURANCE CERTIFICATE", "INSURANCE CERTIFICATE"),
        ("INVOICE ORIGINAL", "INVOICE ORIGINAL"),
        ("AADHAAR_FRONT", "AADHAAR CARD"),
        ("AADHAAR_BACK", "AADHAAR CARD"),
        ("OWNER UNDERTAKING FORM", "OWNER UNDERTAKING FORM"),
    ]

    for doc_key, sub_category_text in upload_sequence:
        file_path = docs.get(doc_key)
        if not file_path:
            logger.warning("fill_rto: no file found for %s, skipping upload", doc_key)
            _rto_log(f"skip upload (missing file): {doc_key}")
            continue

        logger.info("fill_rto: uploading %s -> %s (%s)", doc_key, sub_category_text, file_path.name)

        # Select sub-category
        try:
            _select(
                page,
                "select[id*='subCategory'], select[id*='docCategory'], select[name*='subCategory']",
                sub_category_text,
                label=f"Sub Category: {sub_category_text}",
                timeout=_DEFAULT_TIMEOUT_MS,
            )
        except (PwTimeout, Exception):
            _type_typeahead(
                page,
                "input[id*='subCategory']",
                sub_category_text,
                label=f"Sub Category typeahead: {sub_category_text}",
                timeout=_DEFAULT_TIMEOUT_MS,
            )

        _pause()

        # Upload the file
        _upload_file(page, file_path)

        # Click right-chevron to confirm this upload
        try:
            chevron = page.locator(
                "button:has-text('>>'), button:has-text('>'), "
                "input[value='>>'], input[value='>'], "
                "a:has-text('>>'), [class*='chevron-right'], "
                "[class*='ui-icon-arrowthick-1-e']"
            ).first
            chevron.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            chevron.click()
            _rto_log(f"chevron next after upload: {doc_key}")
            _pause()
            _wait_for_progress_close(page)
        except PwTimeout:
            logger.warning("fill_rto: right-chevron not found for %s, continuing", doc_key)

    # After all uploads, click File Movement
    _pause()
    _click(
        page,
        "input[value*='File Movement'], button:has-text('File Movement'), a:has-text('File Movement')",
        label="File Movement (after uploads)",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause()
    _dismiss_dialog(page, "Yes")
    _pause()
    _dismiss_dialog(page, "Ok", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()


def _screen_6(page: Page) -> float | None:
    """Screen 6: Dealer Regn Fee Tax — open fee details and scrape total payable.

    Steps 1–2: click **Dealer Regn Fee Tax**, wait for progress, scrape **Total Payable Amount**.
    Then **hard-fails intentionally** (does not click Yes on popups or close secondary dialogs).
    """
    _set_screen("Screen 6")
    logger.info("fill_rto: Screen 6 — fee details")
    _rto_log("--- Screen 6: Dealer Regn Fee Tax, total payable ---")

    _click(
        page,
        "input[value*='Dealer Regn Fee'], button:has-text('Dealer Regn Fee'), a:has-text('Dealer Regn Fee Tax')",
        label="Dealer Regn Fee Tax",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause()
    _wait_for_progress_close(page)

    # Step 2: Scrape Total Payable Amount
    total: float | None = None
    try:
        amount_el = page.locator(
            "[id*='totalPayable'], [id*='totalAmount'], "
            "td:has-text('Total Payable') + td, "
            "span:has-text('Total Payable'), "
            "label:has-text('Total Payable')"
        ).first
        amount_el.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        text = amount_el.inner_text().strip()
        nums = re.findall(r'[\d,]+\.?\d*', text)
        if nums:
            total = float(nums[-1].replace(",", ""))
        logger.info("fill_rto: scraped total payable = %s", total)
        if total is not None:
            _rto_log(f"scraped: total payable = {total}")
    except PwTimeout:
        logger.warning("fill_rto: could not scrape total payable amount")
        _rto_log("WARNING: could not scrape total payable amount")
        _dump_page_state(page, "scrape total payable failed")

    # Hard stop after step 2: do not automate payment popups (Yes / close).
    msg = (
        "Screen 6: intentional stop after total payable scrape. "
        f"Total Payable scraped: {total!r}. "
        "Dismiss fee/payment dialogs manually on Vahan if needed."
    )
    logger.warning("fill_rto: %s", msg)
    _rto_log(f"HARD STOP (Screen 6): {msg}")
    raise RuntimeError(msg)


# Message aligned with ``handle_browser_opening._wait_login_or_prompt_after_open`` (DMS / Create Invoice UX).
VAHAN_WARM_THEN_CONTINUE_MESSAGE = "Vahan Opened. Please login. And then press button again"


def warm_vahan_browser_session() -> dict:
    """Open or attach to the Vahan browser without running fill automation (no login gate).

    Operator should log in, then start the RTO batch from the client so ``fill_rto_row`` can proceed.
    """
    out: dict = {"success": False, "error": None, "message": None}
    u = (VAHAN_BASE_URL or "").strip()
    if not u:
        out["error"] = "VAHAN_BASE_URL not set"
        return out
    try:
        from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler

        page, open_error = get_or_open_site_page(
            u,
            "Vahan",
            require_login_on_open=False,
        )
        if page is None:
            out["error"] = open_error or "Could not open Vahan browser"
            return out
        _install_playwright_js_dialog_handler(page)
        out["success"] = True
        out["message"] = VAHAN_WARM_THEN_CONTINUE_MESSAGE
    except Exception as e:
        out["error"] = str(e)
        logger.warning("fill_rto_service: warm_vahan_browser_session %s", e)
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fill_rto_row(row: dict) -> dict:
    """Fill one RTO queue row on the Vahan site.

    Args:
        row: dict from rto_queue joined with sales/customer/vehicle/dealer/insurance masters.

    Returns:
        dict with keys:
            rto_application_id: str | None
            rto_payment_amount: float | None
            completed: bool
    """
    rto_queue_id = row.get("rto_queue_id")
    dealer_id = int(row["dealer_id"])
    logger.info("fill_rto_row: starting rto_queue_id=%s dealer=%s", rto_queue_id, dealer_id)

    # --- Compose data dict for screen helpers ---
    data: dict = {
        "chassis_num": row.get("chassis_num") or "",
        "engine_short": row.get("engine_short") or "",
        "customer_name": row.get("customer_name") or "",
        "care_of": row.get("care_of") or "",
        "mobile": row.get("mobile") or row.get("customer_mobile") or "",
        "address": row.get("address") or "",
        "city": row.get("city") or "",
        "district": (row.get("district") or "").strip(),
        "state": row.get("state") or "",
        "pin": (row.get("pin") or "").strip(),
        "financier": row.get("financier") or "",
        "insurer": row.get("insurer") or "",
        "policy_num": row.get("policy_num") or "",
        "idv": row.get("idv"),
        "billing_date_str": _fmt_date(row.get("billing_date")),
        "policy_from_str": _fmt_date(row.get("policy_from")),
    }

    mob_fn = _mobile_digits_for_filename(data.get("mobile") or row.get("customer_mobile"))
    log_path = get_ocr_output_dir(dealer_id) / f"{mob_fn}_RTO.txt"
    rlog = RtoActionLog(log_path)
    token = _rto_action_log.set(rlog)
    screen_token = _current_screen.set("Setup")
    try:
        _rto_log(
            f"fill_rto_row start rto_queue_id={rto_queue_id} sales_id={row.get('sales_id')} "
            f"dealer_id={dealer_id} log_file={log_path}"
        )

        office = _transform_dealer_rto(row.get("dealer_rto") or "")

        # --- Resolve document files (log: uploads root, searched folder, one summary line) ---
        subfolder = row.get("subfolder") or ""
        uploads_root = get_uploads_dir(dealer_id)
        _rto_log(f"uploads root (dealer): {uploads_root.resolve()}")
        if subfolder:
            sale_dir = uploads_root / subfolder
            _rto_log(f"searched folder (absolute): {sale_dir.resolve()}")
        else:
            sale_dir = None
            _rto_log(
                f"searched folder: (none — file_location empty for sales_id={row.get('sales_id')})"
            )

        docs = _resolve_sale_documents(sale_dir) if sale_dir else {cat: None for cat, _, _ in _DOC_PATTERNS}

        found = {k: v.name for k, v in docs.items() if v is not None}
        missing = [k for k, v in docs.items() if v is None]
        if missing:
            logger.warning("fill_rto: missing documents: %s", ", ".join(missing))
        summary = f"documents matched to categories: {found!r}"
        if missing:
            summary += f"; missing: {missing!r}"
        _rto_log(summary)

        # --- Open Vahan browser (reuse existing tab on same host when possible; see handle_browser_opening) ---
        page, open_error = get_or_open_site_page(
            VAHAN_BASE_URL,
            "Vahan",
            require_login_on_open=True,
        )
        if page is None:
            raise RuntimeError(f"Vahan site not open or login failed: {open_error}")

        try:
            _rto_log(f"browser page url: {(page.url or '')[:220]}")
        except Exception:
            pass

        page.set_default_timeout(_DEFAULT_TIMEOUT_MS)

        _ensure_vahan_dealer_home_for_screen1(page)

        # --- Execute screens ---
        _screen_1(page, office)
        _screen_2(page, data)
        application_id = _screen_3(page, data)
        _screen_4(page)
        _screen_5(page, docs)
        total_payable = _screen_6(page)

        logger.info(
            "fill_rto_row: done rto_queue_id=%s app=%s amount=%s",
            rto_queue_id, application_id, total_payable,
        )
        _rto_log(
            f"fill_rto_row completed rto_queue_id={rto_queue_id} "
            f"rto_application_id={application_id!r} rto_payment_amount={total_payable!r}"
        )

        return {
            "rto_application_id": application_id or None,
            "rto_payment_amount": total_payable,
            "completed": True,
        }
    except Exception as e:
        _rto_log(f"fill_rto_row FAILED: {e!s}")
        raise
    finally:
        _current_screen.reset(screen_token)
        _rto_action_log.reset(token)
