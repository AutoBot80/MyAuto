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

# --- Testing / build: edit here only (not .env). Use 0 / False / "" for production full SOP. ---
# ``RTO_FILL_SKIP_TO_SCREEN``: 0 = run all screens; 1–6 = start at that screen (skips dealer-home reset and earlier).
RTO_FILL_SKIP_TO_SCREEN = 3
# Screen 3: skip **Home** (you are already on ``home.xhtml`` with the grid). Also on when SKIP is 3.
RTO_FILL_SCREEN3_SKIP_HOME = False
# Screen 3: skip **Entry** — only with ``skip_home``; go straight to **Vehicle Details** sub-tab (already past Entry on the form).
# On when SKIP is 3 (and ``RTO_FILL_SCREEN3_SKIP_HOME`` is implied for that path).
RTO_FILL_SCREEN3_SKIP_ENTRY = False
# Optional seed for ``data["rto_application_id"]`` when the queue row has no app id (logging / return merge only).
RTO_FILL_TEST_APPLICATION_ID = ""

# Screen 3 — locators aligned with RTO trace page dumps (``ocr_output/.../*_RTO.txt``): sub-tab strip
# ``ul.ui-tabs-nav`` / ``a text='Hypothecation/Insurance Information'``, panel ``workbench_tabview:veh_info_tab``,
# and the same ``workbench_tabview:*`` id style as other workbench fields.
_SCREEN3_TAX_MODE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:tax_mode"]',
    '[id="workbench_tabview:taxMode"]',
)
_SCREEN3_TAX_MODE_NATIVE_SELECTORS: tuple[str, ...] = (
    'select[id="workbench_tabview:tax_mode_input"]',
    'select[id="workbench_tabview:taxMode_input"]',
    "select[id*='taxMode'], select[name*='taxMode']",
)
_SCREEN3_SAVE_VEHICLE_DETAILS_SELECTORS: tuple[str, ...] = (
    '[id="workbench_tabview:save_vehicle_dtls_btn"]',
    '[id="workbench_tabview:saveVehDtls_btn"]',
    "button:has-text('Save Vehicle Details')",
    "input[value*='Save Vehicle Details']",
    "a:has-text('Save Vehicle Details')",
)
_SCREEN3_HYP_INS_TAB_ANCHOR_RE = re.compile(
    r"Hypothecation\s*/\s*Insurance\s*Information", re.I
)

# 3c / 3d — Insurance & Hypothecation (same tab). Ids follow ``workbench_tabview:*`` like other workbench fields
# (RTO page dumps list the tab strip; field ids match this naming family when not in the first 150 elements).
_SCREEN3_INSURANCE_TYPE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:insurance_type"]',
    '[id="workbench_tabview:insuranceType"]',
    '[id="workbench_tabview:ins_type"]',
)
_SCREEN3_INSURANCE_TYPE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:insurance_type_input"]',
    'select[id="workbench_tabview:insuranceType_input"]',
    "select[id*='insuranceType'], select[name*='insuranceType']",
)
_SCREEN3_INSURANCE_COMPANY_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:insurance_company"]',
    '[id="workbench_tabview:insuranceCompany"]',
)
_SCREEN3_INSURANCE_COMPANY_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:insurance_company_input"]',
    'select[id="workbench_tabview:insuranceCompany_input"]',
    "select[id*='insuranceCompany'], select[name*='insuranceCompany']",
)
_SCREEN3_POLICY_NO_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:policy_no"]',
    '[id="workbench_tabview:policyNo"]',
    "input[id*='policyNo'], input[id*='coverNote'], input[name*='policyNo']",
)
_SCREEN3_INSURANCE_FROM_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:insurance_from"]',
    '[id="workbench_tabview:insuranceFrom"]',
    "input[id*='insuranceFrom'], input[name*='insuranceFrom']",
)
_SCREEN3_IDV_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:idv"]',
    "input[id*='idv'], input[id*='declaredValue'], input[name*='idv']",
)
_SCREEN3_HYP_CHECKBOX_SELECTORS: tuple[str, ...] = (
    "input[type='checkbox'][id*='hypothecated']",
    "input[type='checkbox'][id*='isHypothecated']",
    "input[type='checkbox'][name*='hypothecated']",
)
_SCREEN3_HYP_TYPE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:hypothecation_type"]',
    '[id="workbench_tabview:hypothecationType"]',
)
_SCREEN3_HYP_TYPE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:hypothecation_type_input"]',
    'select[id="workbench_tabview:hypothecationType_input"]',
    "select[id*='hypothecationType'], select[name*='hypothecationType']",
)
_SCREEN3_FINANCIER_NAME_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:financier_name"]',
    "input[id*='financierName'], input[id*='financer'], input[name*='financierName']",
)
_SCREEN3_HYP_FROM_DATE_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:hyp_from_dt"]',
    '[id="workbench_tabview:hypothecationFrom"]',
    "input[id*='fromDate'][id*='hyp'], input[id*='hypothecationFrom'], input[name*='fromDate']",
)
_SCREEN3_FIN_STATE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:fin_state"]',
    '[id="workbench_tabview:hyp_state"]',
)
_SCREEN3_FIN_STATE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:fin_state_input"]',
    'select[id="workbench_tabview:hyp_state_input"]',
    "select[id*='finState'], select[id*='hypState']",
)
_SCREEN3_FIN_DISTRICT_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:fin_district"]',
    '[id="workbench_tabview:hyp_district"]',
)
_SCREEN3_FIN_DISTRICT_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:fin_district_input"]',
    'select[id="workbench_tabview:hyp_district_input"]',
    "select[id*='finDistrict'], select[id*='hypDistrict'], select[id*='fin_district']",
)
_SCREEN3_FIN_PIN_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:fin_pin"]',
    "input[id*='finPin'], input[id*='hypPin'], input[name*='finPin']",
)
_SCREEN3_SAVE_FILE_MOVEMENT_SELECTORS: tuple[str, ...] = (
    '[id="workbench_tabview:save_file_movement_btn"]',
    "button:has-text('Save and File Movement')",
    "button:has-text('Save and file movement')",
    "input[value*='Save and File Movement']",
)

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
# Fast UI timing: **200ms** per attempt; **2s** total budget when looping (retries / polls).
_FIRST_TRY_MS = 200
_LOOP_BUDGET_MS = 2_000
# Delay after each discrete UI action (s) — not a Playwright timeout.
_ACTION_WAIT_S = 0.2
# ``_ensure_vahan_dealer_home_for_screen1``: 200ms per selector, ≤2s total across attempts.
_ENSURE_HOME_SELECTOR_WAIT_MS = _FIRST_TRY_MS
_ENSURE_HOME_SELECTORS_TOTAL_S = _LOOP_BUDGET_MS / 1000.0

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

    Waits use **200ms** first slices and **up to 2s** total when polling or looping.
    """
    wrapper_id = "regnNoSelectionForAPS"

    def _label_shows_no_text(text: str) -> bool:
        u = text.upper()
        return "NO" in u and "SELECT" not in u

    def _wait_label_no() -> None:
        if not _poll_choice_no_label_ok(page):
            logger.warning("fill_rto: choice number label did not show NO in time")

    native = page.locator('select[id="regnNoSelectionForAPS_input"]').first
    try:
        _wait_native_choice_select_attached(native)
        if not _native_select_choice_no_loop(native):
            raise PwTimeout()
        native.evaluate(
            """(el) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        _pause()
        # Fast path: label often updates immediately — 200ms read, then loop up to 2s if needed.
        try:
            tx = (
                page.locator("label#regnNoSelectionForAPS_label").first.inner_text(timeout=_FIRST_TRY_MS)
                or ""
            )
            if _label_shows_no_text(tx):
                _rto_log("choice number NO: label OK after native (fast path)")
            else:
                _wait_for_progress_close_loop(page)
                _wait_label_no()
        except Exception:
            _wait_for_progress_close_loop(page)
            _wait_label_no()
    except Exception as e:
        logger.debug("fill_rto: choice NO via native select: %s", e)

    try:
        lbl = page.locator("label#regnNoSelectionForAPS_label").first
        t = (lbl.inner_text(timeout=_FIRST_TRY_MS) or "").upper()
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
            _wait_for_progress_close_loop(page)
            _wait_label_no()
    except Exception as e:
        logger.warning("fill_rto: choice NO overlay fallback: %s", e)

    # Close stuck panel without Escape (see docstring).
    try:
        panel = page.locator(_pf_selectonemenu_panel_selector(wrapper_id)).first
        if not _wait_locator_hidden_loop(panel):
            page.locator("div#regnNoSelectionForAPS").first.click(position={"x": 4, "y": 8})
            _pause()
    except Exception:
        try:
            page.locator("div#regnNoSelectionForAPS").first.click(position={"x": 4, "y": 8})
            _pause()
        except Exception:
            pass

    try:
        lbl = page.locator("label#regnNoSelectionForAPS_label").first
        t = (lbl.inner_text(timeout=_LOOP_BUDGET_MS) or "").strip()
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


def _find_visible_otp_dialog(page: Page):
    """Return a locator for a visible Vahan OTP dialog, or ``None``."""
    selectors = (
        "[role='dialog']:has-text('OTP for Verify Owner')",
        ".ui-dialog:has-text('OTP for Verify Owner')",
        "[role='dialog']:has-text('Verify Owner')",
        ".ui-dialog:has-text('Verify Owner')",
        "[role='dialog']:has-text('OTP')",
        ".ui-dialog:has-text('OTP')",
        "div.ui-dialog-content:has-text('OTP')",
        "[role='dialog']:has-text('One Time Password')",
        ".ui-dialog:has-text('One Time Password')",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=_FIRST_TRY_MS):
                return loc
        except Exception:
            continue
    return None


def _poll_otp_dialog(page: Page, max_ms: int = 12_000):
    """Poll until an OTP dialog is visible or ``max_ms`` elapses."""
    deadline = time.monotonic() + max_ms / 1000.0
    while time.monotonic() < deadline:
        dlg = _find_visible_otp_dialog(page)
        if dlg is not None:
            return dlg
        time.sleep(_FIRST_TRY_MS / 1000.0)
    return None


def _otp_dialog_mobile_from_text(dlg) -> str | None:
    """Parse 10-digit Indian mobile from OTP dialog body text."""
    try:
        txt = dlg.inner_text(timeout=5000) or ""
        compact = re.sub(r"\s+", " ", txt)
        m = re.search(r"(?:\+91[\s\-]*)?([6-9]\d{9})\b", compact)
        return m.group(1) if m else None
    except Exception:
        return None


def _fill_workbench_owner_mobile(page: Page, mobile: str) -> None:
    """Update owner mobile on workbench (same field as Screen 2)."""
    _fill(
        page,
        "input[id*='tf_mobNo'], input[id*='mobileNo'], input[name*='mobileNo'], input[id*='mobNo']",
        mobile,
        label="Mobile No (update for OTP)",
    )


def _click_inward_partial_save(page: Page) -> None:
    _click(
        page,
        "button:has-text('Inward Application'), button:has-text('Partial Save'), "
        "input[value*='Inward'], input[value*='Partial Save'], "
        "input[value*='Save'], button:has-text('Save and file movement'), "
        "button:has-text('Save and File Movement')",
        label="Inward Application (Partial Save)",
        timeout=_DEFAULT_TIMEOUT_MS,
    )


def _cancel_vahan_otp_dialog(page: Page) -> None:
    """Click **Cancel** on the open OTP verification dialog (returns to workbench)."""
    dlg = _find_visible_otp_dialog(page)
    if dlg is None:
        raise RuntimeError("OTP dialog is not visible — cannot cancel")
    clicked = False
    try:
        dlg.get_by_role("button", name=re.compile(r"^\s*Cancel\s*$", re.I)).first.click(timeout=5000)
        clicked = True
    except Exception:
        pass
    if not clicked:
        try:
            dlg.locator("button, a").filter(has_text=re.compile(r"^\s*Cancel\s*$", re.I)).first.click(timeout=5000)
            clicked = True
        except Exception:
            pass
    if not clicked:
        try:
            page.locator(".ui-dialog:visible").get_by_role("button", name=re.compile(r"Cancel", re.I)).first.click(
                timeout=5000
            )
            clicked = True
        except Exception as e:
            raise RuntimeError("Could not click Cancel on Vahan OTP dialog") from e
    _rto_log("OTP dialog: Cancel")
    _pause()
    _wait_for_progress_close_loop(page)


def _submit_vahan_otp_in_dialog(page: Page, dlg, otp: str) -> None:
    """Fill OTP into the open dialog and click the primary action button."""
    otp_clean = re.sub(r"\D", "", otp) or otp
    filled = False
    for sel in (
        "input[id*='otp' i]",
        "input[name*='otp' i]",
        "input[type='password']",
        "input.otp",
        "input[maxlength='6']",
        "input[maxlength='8']",
    ):
        try:
            cand = dlg.locator(sel)
            if cand.count() == 0:
                continue
            inp = cand.first
            inp.wait_for(state="visible", timeout=3000)
            inp.fill(otp_clean, timeout=5000)
            filled = True
            _rto_log(f"OTP: filled field matching {sel!r}")
            break
        except Exception:
            continue
    if not filled:
        try:
            dlg.locator("input[type='text']").first.fill(otp_clean, timeout=5000)
            filled = True
            _rto_log("OTP: filled first text input in dialog")
        except Exception as e:
            _rto_log(f"OTP: could not find OTP input: {e!s}")
            raise RuntimeError("Could not find OTP input in Vahan dialog") from e
    _pause()
    clicked = False
    for pattern, log_name in (
        (r"Confirm\s+And\s+Inward\s+Application", "Confirm And Inward Application"),
        (r"^\s*Submit\s*$", "Submit"),
        (r"^\s*Verify\s*$", "Verify"),
        (r"^\s*Validate\s*$", "Validate"),
        (r"^\s*OK\s*$", "OK"),
        (r"^\s*Confirm\s*$", "Confirm"),
        (r"^\s*Continue\s*$", "Continue"),
    ):
        try:
            dlg.get_by_role("button", name=re.compile(pattern, re.I)).first.click(timeout=5000)
            clicked = True
            _rto_log(f"OTP dialog: clicked {log_name}")
            break
        except Exception:
            continue
    if not clicked:
        try:
            dlg.locator("input[type='submit'], input[type='button']").filter(
                has_text=re.compile(r"submit|verify|ok|confirm|inward", re.I)
            ).first.click(timeout=5000)
            clicked = True
        except Exception:
            try:
                page.locator(
                    ".ui-dialog-buttonpane button, .ui-dialog-footer input[type='submit']"
                ).first.click(timeout=4000)
                clicked = True
            except Exception as e:
                raise RuntimeError("Could not click OTP submit on Vahan dialog") from e
    if not clicked:
        raise RuntimeError("Could not click OTP submit on Vahan dialog")
    _pause()


def _clear_batch_otp_flags(dealer_id: int) -> None:
    from app.services.rto_payment_service import _write_batch_status

    _write_batch_status(
        int(dealer_id),
        otp_pending=False,
        otp_rto_queue_id=None,
        otp_customer_mobile=None,
        otp_prompt=None,
        otp_allow_change_mobile=False,
    )


def _find_visible_generated_application_dialog(page: Page):
    """Modal *Generated Application No* shown after successful Inward / OTP (workbench)."""
    selectors = (
        "[role='dialog']:has-text('Generated Application No')",
        ".ui-dialog:has-text('Generated Application No')",
        "[role='dialog']:has-text('Generated Application')",
        ".ui-dialog:has-text('Generated Application')",
        ".ui-dialog-content:has-text('Application No')",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=_FIRST_TRY_MS):
                return loc
        except Exception:
            continue
    return None


def _poll_generated_application_dialog(page: Page, max_ms: int = 15_000):
    deadline = time.monotonic() + max_ms / 1000.0
    while time.monotonic() < deadline:
        dlg = _find_visible_generated_application_dialog(page)
        if dlg is not None:
            return dlg
        time.sleep(_FIRST_TRY_MS / 1000.0)
    return None


def _scrape_application_id_from_dialog_text(text: str) -> str:
    """Parse Vahan application number from dialog body (e.g. ``Application No. :RJ26041148051328``)."""
    raw = (text or "").replace("\xa0", " ")
    m = re.search(
        r"Application\s*No\.?\s*[:\-]?\s*([A-Z]{2}\d{10,20})\b",
        raw,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().upper()
    m = re.search(
        r"Application\s*(?:no\.?|number)\s*[:\-]?\s*(\S+)",
        raw,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().rstrip(".,;")
    found = re.findall(r"\b[A-Z]{2}\d{10,20}\b", raw)
    return found[0].upper() if found else ""


def _dismiss_generated_application_no_dialog(page: Page) -> str:
    """If the *Generated Application No* modal is open: scrape id, click Ok, wait. Returns id or empty string."""
    dlg = _find_visible_generated_application_dialog(page) or _poll_generated_application_dialog(page, max_ms=15_000)
    if dlg is None:
        return ""
    try:
        text = dlg.inner_text(timeout=5000) or ""
    except Exception:
        text = ""
    app_id = _scrape_application_id_from_dialog_text(text)
    if app_id:
        _rto_log(f"dialog: Generated Application No — application_id={app_id}")
        logger.info("fill_rto: Generated Application No dialog — application_id=%s", app_id)
    else:
        _rto_log("dialog: Generated Application No — could not parse application id from dialog text")
    clicked = False
    for pat in (r"^\s*Ok\s*$", r"^\s*OK\s*$"):
        try:
            dlg.get_by_role("button", name=re.compile(pat, re.I)).first.click(timeout=5000)
            clicked = True
            _rto_log("Generated Application dialog: Ok")
            break
        except Exception:
            continue
    if not clicked:
        try:
            dlg.locator("button, a").filter(has_text=re.compile(r"^\s*Ok\s*$", re.I)).first.click(timeout=5000)
            clicked = True
            _rto_log("Generated Application dialog: Ok (link/button)")
        except Exception:
            try:
                _dismiss_dialog(page, "OK", timeout=4000)
            except Exception:
                pass
    _pause()
    _wait_for_progress_close_loop(page)
    return app_id


def _handle_inward_partial_save_followup(page: Page, data: dict) -> None:
    """After *Inward Application (Partial Save)*: OTP popup (operator) or legacy Yes/Close dialogs."""
    _wait_for_progress_close_loop(page)
    _pause()
    dlg = _poll_otp_dialog(page, max_ms=12_000)
    if dlg is None:
        _rto_log("Inward save: no OTP dialog detected within 12s — dismissing Yes/Close if present")
        _dismiss_dialog(page, "Yes")
        _pause()
        _dismiss_dialog(page, "Close", timeout=_DEFAULT_TIMEOUT_MS)
        scraped = _dismiss_generated_application_no_dialog(page)
        if scraped:
            data["rto_application_id"] = scraped
        return

    _rto_log("Inward save: OTP dialog detected — use app to enter OTP or change mobile")
    dealer_id = data.get("dealer_id")
    rto_queue_id = data.get("rto_queue_id")
    parsed_m = _otp_dialog_mobile_from_text(dlg)
    mobile_display = parsed_m or (str(data.get("mobile") or "").strip()) or "—"
    if parsed_m:
        _rto_log(f"OTP dialog: parsed mobile from portal text: {parsed_m}")

    prompt = (
        "Vahan is verifying the owner's mobile. Enter the OTP here, or switch to **Use a different mobile** — "
        "automation will click Cancel on the popup, update the mobile on the form, and press Partial Save again."
    )

    if dealer_id is None or rto_queue_id is None:
        raise RuntimeError(
            "Vahan is asking for OTP but dealer or queue id is missing — cannot sync with the app. "
            "Enter the OTP manually in the Vahan browser window."
        )

    from app.services.rto_otp_bridge import OperatorAction, OperatorOtpTimeout, wait_for_operator_action
    from app.services.rto_payment_service import _write_batch_status

    while True:

        def notify() -> None:
            _write_batch_status(
                int(dealer_id),
                otp_pending=True,
                otp_rto_queue_id=int(rto_queue_id),
                otp_customer_mobile=mobile_display,
                otp_prompt=prompt,
                otp_allow_change_mobile=True,
                message=f"Vahan OTP — mobile {mobile_display} (queue {rto_queue_id})",
            )

        try:
            action = wait_for_operator_action(int(dealer_id), int(rto_queue_id), notify, timeout_s=600.0)
        except OperatorOtpTimeout:
            _rto_log("Inward save: operator action timed out")
            _clear_batch_otp_flags(int(dealer_id))
            raise
        except Exception:
            _clear_batch_otp_flags(int(dealer_id))
            raise

        if action.kind == "otp":
            dlg2 = _find_visible_otp_dialog(page) or _poll_otp_dialog(page, max_ms=5000)
            try:
                if dlg2 is None:
                    raise RuntimeError("OTP received but Vahan OTP dialog is no longer visible")
                _submit_vahan_otp_in_dialog(page, dlg2, action.value)
                _wait_for_progress_close(page)
                scraped = _dismiss_generated_application_no_dialog(page)
                if scraped:
                    data["rto_application_id"] = scraped
                _dismiss_dialog(page, "Yes")
                _pause()
                _dismiss_dialog(page, "Close", timeout=_DEFAULT_TIMEOUT_MS)
            finally:
                _clear_batch_otp_flags(int(dealer_id))
            return

        if action.kind == "change_mobile":
            new_mobile = action.value
            data["mobile"] = new_mobile
            mobile_display = new_mobile
            try:
                _cancel_vahan_otp_dialog(page)
                _fill_workbench_owner_mobile(page, new_mobile)
                _pause()
                _click_inward_partial_save(page)
                _pause()
                _wait_for_progress_close_loop(page)
                dlg = _poll_otp_dialog(page, max_ms=15_000)
                if dlg is None:
                    raise RuntimeError(
                        "OTP dialog did not reappear after updating mobile — check Vahan for validation errors."
                    )
                parsed = _otp_dialog_mobile_from_text(dlg)
                if parsed:
                    mobile_display = parsed
                else:
                    mobile_display = new_mobile
                _rto_log(f"Inward save: mobile set to {new_mobile}; OTP dialog shown again (display {mobile_display})")
            except Exception:
                _clear_batch_otp_flags(int(dealer_id))
                raise
            continue

        _clear_batch_otp_flags(int(dealer_id))
        raise RuntimeError(f"Unknown operator action: {action.kind!r}")


def _wait_for_progress_close(page: Page, timeout_ms: int = _LONG_TIMEOUT_MS) -> None:
    """Wait until a progress/loading overlay disappears (PrimeFaces ``ui-blockui``, etc.).

    Uses ``timeout_ms`` max wait per call — many flows chain this after AJAX. For short
    PrimeFaces overlays prefer ``_wait_for_progress_close_loop`` (200ms slices, 2s cap).
    """
    try:
        overlay = page.locator(".ui-blockui, .blockUI, .loading-overlay, .ui-dialog-loading").first
        overlay.wait_for(state="hidden", timeout=timeout_ms)
    except PwTimeout:
        pass
    _pause()


def _wait_for_progress_close_loop(page: Page) -> None:
    """Block UI gone: ``_FIRST_TRY_MS`` slices until hidden or ``_LOOP_BUDGET_MS`` total."""
    overlay = page.locator(".ui-blockui, .blockUI, .loading-overlay, .ui-dialog-loading").first
    t0 = time.monotonic()
    while True:
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms >= _LOOP_BUDGET_MS:
            break
        slice_ms = int(min(_FIRST_TRY_MS, _LOOP_BUDGET_MS - elapsed_ms))
        if slice_ms < 1:
            break
        try:
            overlay.wait_for(state="hidden", timeout=max(slice_ms, 1))
            break
        except PwTimeout:
            continue
    _pause()


def _poll_choice_no_label_ok(page: Page) -> bool:
    """True when choice-number label shows NO — polls every ``_FIRST_TRY_MS`` up to ``_LOOP_BUDGET_MS``."""
    deadline = time.monotonic() + _LOOP_BUDGET_MS / 1000.0
    while time.monotonic() < deadline:
        try:
            ok = page.evaluate(
                """() => {
                    const el = document.querySelector('label#regnNoSelectionForAPS_label');
                    if (!el) return false;
                    const t = (el.textContent || '').toUpperCase();
                    return t.includes('NO') && !t.includes('SELECT');
                }"""
            )
            if ok:
                return True
        except Exception:
            pass
        rem = deadline - time.monotonic()
        if rem <= 0:
            break
        time.sleep(min(_FIRST_TRY_MS / 1000.0, rem))
    return False


def _wait_locator_hidden_loop(loc) -> bool:
    """Wait for locator hidden: ``_FIRST_TRY_MS`` slices, ``_LOOP_BUDGET_MS`` total. Returns True if hidden."""
    t0 = time.monotonic()
    while True:
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms >= _LOOP_BUDGET_MS:
            return False
        slice_ms = int(min(_FIRST_TRY_MS, _LOOP_BUDGET_MS - elapsed_ms))
        if slice_ms < 1:
            return False
        try:
            loc.wait_for(state="hidden", timeout=max(slice_ms, 1))
            return True
        except PwTimeout:
            continue


def _native_select_choice_no_loop(native) -> bool:
    """Set native ``regnNoSelectionForAPS`` to NO; 200ms attempts, 2s total."""
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < _LOOP_BUDGET_MS:
        sm = int(min(_FIRST_TRY_MS, _LOOP_BUDGET_MS - (time.monotonic() - t0) * 1000))
        if sm < 50:
            break
        try:
            native.select_option(label="NO", timeout=max(sm, 1))
            return True
        except PwTimeout:
            try:
                native.select_option(label=re.compile(r"^\s*NO\s*$", re.I), timeout=max(sm, 1))
                return True
            except PwTimeout:
                continue
    return False


def _wait_native_choice_select_attached(native) -> None:
    """``attached`` on native select: 200ms slices, 2s total."""
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < _LOOP_BUDGET_MS:
        sm = int(min(_FIRST_TRY_MS, _LOOP_BUDGET_MS - (time.monotonic() - t0) * 1000))
        if sm < 1:
            break
        try:
            native.wait_for(state="attached", timeout=max(sm, 1))
            return
        except PwTimeout:
            continue


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
       Each selector waits up to **0.5s** for visible; the whole Home-link loop stops after **5s** total.
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
    t_home = time.monotonic()
    for sel in home_link_selectors:
        elapsed = time.monotonic() - t_home
        if elapsed >= _ENSURE_HOME_SELECTORS_TOTAL_S:
            break
        remaining_ms = int((_ENSURE_HOME_SELECTORS_TOTAL_S - elapsed) * 1000)
        wait_ms = min(_ENSURE_HOME_SELECTOR_WAIT_MS, max(remaining_ms, 1))
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=wait_ms)
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


def _screen_3_click_entry(page: Page, data: dict, *, skip_home: bool) -> None:
    """Click **Entry** on the pending-work grid—prefer the **Inwarded** row for ``rto_application_id`` when ``skip_home``."""
    app = (str(data.get("rto_application_id") or "").strip() or RTO_FILL_TEST_APPLICATION_ID or "").strip()
    if skip_home and app:
        try:
            row = (
                page.locator("tbody tr, table[role='grid'] tr, .ui-datatable-data tr, tr")
                .filter(has_text=re.compile(re.escape(app), re.I))
                .filter(has_text=re.compile(r"Inwarded", re.I))
                .first
            )
            row.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            row.locator("button, a, input[type='button'], input[type='submit']").filter(
                has_text=re.compile(r"^\s*Entry\s*$", re.I)
            ).first.click(timeout=_DEFAULT_TIMEOUT_MS)
            _rto_log(f"Screen 3: Entry on Inwarded row for Application No {app}")
            _pause()
            _wait_for_progress_close(page)
            return
        except Exception as e:
            logger.warning("fill_rto: pending-work row Entry failed: %s", e)
            _rto_log(f"WARNING: row-scoped Entry failed ({e!s}) — generic Entry")
    _click(
        page,
        "input[value='Entry'], button:has-text('Entry'), a:has-text('Entry')",
        label="Entry button",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause()
    _wait_for_progress_close(page)


def _screen_3_pf_subtab_probe_log(page: Page, label_pattern: str) -> None:
    """Log DOM hints for a sub-tab (ids/classes) to the RTO file for selector tuning."""
    rx = re.compile(label_pattern, re.I)
    try:
        # Prefer workbench sub-tab row (matches RTO dumps: ``ul.ui-tabs-nav`` / ``li`` / ``a``).
        cand = page.locator("ul.ui-tabs-nav li a").filter(has_text=rx).first
        if cand.count() == 0:
            cand = page.get_by_role("tab", name=rx).first
        if cand.count() == 0:
            cand = page.locator(".ui-tabs-nav a, .ui-tabmenu-nav a, [role='tab']").filter(has_text=rx).first
        if cand.count() == 0:
            cand = page.locator("a, span").filter(has_text=rx).first
        if cand.count() == 0:
            _rto_log(f"tab probe [{label_pattern}]: no matching node")
            return
        bits = [
            f"id={cand.get_attribute('id')!r}",
            f"class={cand.get_attribute('class')!r}",
            f"href={cand.get_attribute('href')!r}",
            f"role={cand.get_attribute('role')!r}",
            f"aria-controls={cand.get_attribute('aria-controls')!r}",
            f"data-index={cand.get_attribute('data-index')!r}",
        ]
        tx = ""
        try:
            tx = (cand.inner_text(timeout=2000) or "").strip()[:120]
        except Exception:
            pass
        _rto_log(f"tab probe [{label_pattern}]: {'; '.join(bits)}; text={tx!r}")
    except Exception as e:
        _rto_log(f"tab probe [{label_pattern}]: error {e!s}")


def _screen_3_try_pf_subtab_click(page: Page, label_pattern: str, *, log_name: str) -> bool:
    """Activate a PrimeFaces-style sub-tab by visible title. Returns True if click path succeeded."""
    rx = re.compile(label_pattern, re.I)
    try:
        try:
            nav = page.locator("ul.ui-tabs-nav li a").filter(has_text=rx).first
            nav.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            nav.click(timeout=_DEFAULT_TIMEOUT_MS)
        except Exception:
            try:
                page.get_by_role("tab", name=rx).first.click(timeout=_DEFAULT_TIMEOUT_MS)
            except Exception:
                page.locator(".ui-tabs-nav a, .ui-tabmenu-nav a, ul.ui-tabs-nav li a").filter(has_text=rx).first.click(
                    timeout=_DEFAULT_TIMEOUT_MS
                )
        _pause()
        _wait_for_progress_close_loop(page)
        _rto_log(f"Screen 3: sub-tab {log_name}")
        return True
    except Exception as e:
        _rto_log(f"sub-tab try {log_name!r}: {e!s}")
        return False


def _screen_3_pf_subtab_click(page: Page, label_pattern: str, *, log_name: str) -> None:
    """Activate a PrimeFaces-style sub-tab by visible title (e.g. *Vehicle Details*, *Hypothecation*)."""
    if not _screen_3_try_pf_subtab_click(page, label_pattern, log_name=log_name):
        _rto_log(f"WARNING: sub-tab {log_name} click failed after retries")
        logger.warning("fill_rto: sub-tab %s: could not activate", log_name)


def _screen_3_scroll_to_tax_mode(page: Page) -> None:
    """Scroll the Tax Mode control into view (Vehicle Details tab is often long)."""
    for sel in _SCREEN3_TAX_MODE_PF_WRAPPERS + _SCREEN3_TAX_MODE_NATIVE_SELECTORS:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="attached", timeout=3000)
            loc.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
            _pause()
            _rto_log(f"Screen 3: scrolled to Tax Mode ({sel!r})")
            return
        except Exception:
            continue
    _rto_log("WARNING: scroll to Tax Mode: no matching locator")


def _screen_3_select_tax_mode_one_time(page: Page) -> None:
    """Set Tax Mode to **ONE TIME** using workbench PF widgets / native ``select`` (RTO log ids)."""
    for wsel in _SCREEN3_TAX_MODE_PF_WRAPPERS:
        try:
            wid_m = re.search(r'id="([^"]+)"', wsel)
            wrapper_id = wid_m.group(1) if wid_m else ""
            _select_pf_dropdown(page, wsel, "ONE TIME", label="Tax Mode", timeout=_DEFAULT_TIMEOUT_MS)
            if wrapper_id:
                _close_pf_selectonemenu_overlay(page, wrapper_id)
            _pause()
            return
        except Exception:
            continue
    for nsel in _SCREEN3_TAX_MODE_NATIVE_SELECTORS:
        try:
            _select(page, nsel, "ONE TIME", label="Tax Mode", timeout=_DEFAULT_TIMEOUT_MS)
            return
        except Exception:
            continue
    logger.debug("fill_rto: Tax Mode could not be set (ONE TIME)")
    _rto_log("WARNING: Tax Mode not set — check workbench_tabview:tax_mode")


def _screen_3_click_save_vehicle_details(page: Page) -> None:
    """Persist Vehicle Details tab (Tax Mode, etc.) before switching to Hypothecation/Insurance."""
    last_err: Exception | None = None
    for sel in _SCREEN3_SAVE_VEHICLE_DETAILS_SELECTORS:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            loc.click(timeout=_DEFAULT_TIMEOUT_MS)
            _pause()
            _wait_for_progress_close_loop(page)
            _rto_log(f"Screen 3: Save Vehicle Details ({sel!r})")
            return
        except Exception as e:
            last_err = e
            continue
    msg = f"Save Vehicle Details: {last_err!s}" if last_err else "Save Vehicle Details: no selector matched"
    _rto_log(f"TIMEOUT click: {msg}")
    _dump_page_state(page, "Save Vehicle Details")
    raise PwTimeout(msg)


def _screen_3_open_hypothecation_insurance_tab(page: Page) -> None:
    """Open **Hypothecation/Insurance Information** (log: ``ul.ui-tabs-nav`` / ``a`` with that text)."""
    _screen_3_pf_subtab_probe_log(page, r"Hypothecation\s*/\s*Insurance")
    try:
        tab = page.locator("ul.ui-tabs-nav li a").filter(has_text=_SCREEN3_HYP_INS_TAB_ANCHOR_RE).first
        tab.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        tab.click(timeout=_DEFAULT_TIMEOUT_MS)
        _pause()
        _wait_for_progress_close_loop(page)
        _rto_log("Screen 3: sub-tab Hypothecation/Insurance Information (ui-tabs-nav)")
        return
    except Exception as e:
        _rto_log(f"Hypothecation/Insurance (nav anchor): {e!s} — regex fallbacks")
    tries = (
        (r"Hypothecation\s*/\s*Insurance\s*Information", "Hypothecation/Insurance Information"),
        (r"Hypothecation\s*/\s*Insurance", "Hypothecation/Insurance"),
        (r"Hypothecation\.\.\.", "Hypothecation…"),
    )
    for pat, log_name in tries:
        if _screen_3_try_pf_subtab_click(page, pat, log_name=log_name):
            return
    _rto_log("WARNING: Hypothecation/Insurance sub-tab not opened — check tab labels")
    logger.warning("fill_rto: Hypothecation/Insurance tab not activated")


def _screen_3_scroll_subtab_bar_into_view(page: Page) -> None:
    """Scroll the horizontal sub-tab strip into view (after saving, page may be at bottom)."""
    nav = page.locator(".ui-tabs-nav, .ui-tabmenu-nav, ul.ui-tabs-nav").first
    try:
        nav.wait_for(state="attached", timeout=_DEFAULT_TIMEOUT_MS)
        nav.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
        _pause()
        _rto_log("Screen 3: scrolled to sub-tab bar")
    except Exception as e:
        _rto_log(f"WARNING: scroll to sub-tab bar: {e!s}")


def _screen_2(page: Page, data: dict) -> None:
    """Screen 2: Chassis/engine, owner details, address, Save."""
    _set_screen("Screen 2")
    logger.info("fill_rto: Screen 2 — chassis=%s", data.get("chassis_num", "")[:8])
    _rto_log("--- Screen 2: Chassis, engine, owner, address, Inward Application (Partial Save) ---")

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

    # Same as Current Address — after Pin, focus stays in PIN; **Tab** moves to this checkbox (portal
    # tab order). **Space** checks it and lets PrimeFaces copy correspondence → permanent fields
    # (avoids scroll-into-view / retry jitter from clicking off-screen).
    try:
        same_cb = page.locator(
            '[id="workbench_tabview:samePermAdd_input"], '
            "input[type='checkbox'][id*='samePermAdd']"
        ).first
        same_cb.wait_for(state="attached", timeout=_DEFAULT_TIMEOUT_MS)
        page.keyboard.press("Tab")
        _pause()
        page.keyboard.press("Space")
        _pause()
        if not same_cb.is_checked():
            same_cb.click(timeout=_DEFAULT_TIMEOUT_MS, force=True)
            _pause()
        if not same_cb.is_checked():
            page.locator("label:has-text('Same as Current')").first.click(timeout=_DEFAULT_TIMEOUT_MS)
            _pause()
        _wait_for_progress_close_loop(page)
        if same_cb.is_checked():
            _rto_log("checkbox: Same as Current Address — checked (Tab/Space / click)")
        else:
            _rto_log("WARNING: Same as Current Address still unchecked")
    except PwTimeout:
        logger.debug("fill_rto: Same as Current Address checkbox not found, skipping")

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

    # 2e: Partial save / inward (workbench label: ``Inward Application(Partial Save)``)
    _pause()
    _click_inward_partial_save(page)
    _pause()
    _handle_inward_partial_save_followup(page, data)


def _screen_3_pf_dropdown_chain(
    page: Page,
    wrappers: tuple[str, ...],
    value: str,
    *,
    label: str,
    option_label_regex: re.Pattern | None = None,
) -> bool:
    """Try PrimeFaces ``ui-selectonemenu`` wrappers in order (``workbench_tabview:*`` ids)."""
    if not (value or "").strip() and option_label_regex is None:
        return False
    for wsel in wrappers:
        try:
            wid_m = re.search(r'id="([^"]+)"', wsel)
            wrapper_id = wid_m.group(1) if wid_m else ""
            _select_pf_dropdown(
                page,
                wsel,
                value,
                label=label,
                option_label_regex=option_label_regex,
                timeout=_DEFAULT_TIMEOUT_MS,
            )
            if wrapper_id:
                _close_pf_selectonemenu_overlay(page, wrapper_id)
            _pause()
            return True
        except Exception:
            continue
    return False


def _screen_3_native_select_chain(
    page: Page,
    selectors: tuple[str, ...],
    value: str,
    *,
    label: str,
) -> bool:
    if not (value or "").strip():
        return False
    for sel in selectors:
        try:
            _select(page, sel, value, label=label, timeout=_DEFAULT_TIMEOUT_MS)
            return True
        except Exception:
            continue
    return False


def _fill_first_matching(page: Page, selectors: tuple[str, ...], value: object, *, label: str) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if text == "":
        return True
    for sel in selectors:
        try:
            _fill(page, sel, text, label=label, timeout=_DEFAULT_TIMEOUT_MS)
            return True
        except Exception:
            continue
    _rto_log(f"WARNING: {label} not filled (no matching field)")
    return False


def _screen_3c_insurance_information(page: Page, data: dict) -> None:
    """3c: On Hypothecation/Insurance tab — Insurance Type, Company, policy, dates, IDV."""
    _rto_log("--- Screen 3c: Insurance (Hypothecation/Insurance Information tab) ---")
    for scroll_sel in (
        _SCREEN3_INSURANCE_TYPE_PF_WRAPPERS[0],
        _SCREEN3_INSURANCE_TYPE_NATIVE[0],
    ):
        try:
            loc = page.locator(scroll_sel).first
            loc.wait_for(state="attached", timeout=3000)
            loc.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
            _pause()
            break
        except Exception:
            continue

    if not _screen_3_pf_dropdown_chain(
        page, _SCREEN3_INSURANCE_TYPE_PF_WRAPPERS, "THIRD PARTY", label="Insurance Type"
    ):
        if not _screen_3_native_select_chain(
            page, _SCREEN3_INSURANCE_TYPE_NATIVE, "THIRD PARTY", label="Insurance Type"
        ):
            try:
                _type_typeahead(
                    page,
                    "input[id*='insuranceType']",
                    "THIRD PARTY",
                    label="Insurance Type typeahead",
                    timeout=_DEFAULT_TIMEOUT_MS,
                )
            except PwTimeout:
                _rto_log("WARNING: Insurance Type not set (THIRD PARTY)")

    insurer = (data.get("insurer") or "").strip()
    if insurer:
        if not _screen_3_pf_dropdown_chain(
            page, _SCREEN3_INSURANCE_COMPANY_PF_WRAPPERS, insurer, label="Insurance Company"
        ):
            if not _screen_3_native_select_chain(
                page, _SCREEN3_INSURANCE_COMPANY_NATIVE, insurer, label="Insurance Company"
            ):
                try:
                    _type_typeahead(
                        page,
                        "input[id*='insuranceCompany'], input[name*='insuranceCompany']",
                        insurer,
                        label="Insurance Company typeahead",
                        timeout=_DEFAULT_TIMEOUT_MS,
                    )
                except PwTimeout:
                    _rto_log("WARNING: Insurance Company not set")

    _fill_first_matching(
        page, _SCREEN3_POLICY_NO_INPUT, data.get("policy_num", ""), label="Policy/Cover Note No."
    )
    _fill_first_matching(
        page, _SCREEN3_INSURANCE_FROM_INPUT, data.get("policy_from_str", ""), label="Insurance From"
    )
    idv_v = data.get("idv")
    idv_s = "" if idv_v is None else str(idv_v).strip()
    if idv_s:
        _fill_first_matching(page, _SCREEN3_IDV_INPUT, idv_s, label="Insurance Declared Value")


def _screen_3_click_save_file_movement(page: Page) -> None:
    """Click **Save and File Movement** (workbench id or button text)."""
    last_err: Exception | None = None
    for sel in _SCREEN3_SAVE_FILE_MOVEMENT_SELECTORS:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            loc.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
            loc.click(timeout=_DEFAULT_TIMEOUT_MS)
            _pause()
            _rto_log(f"click: Save and File Movement ({sel!r})")
            return
        except Exception as e:
            last_err = e
            continue
    msg = f"Save and File Movement: {last_err!s}" if last_err else "Save and File Movement: no selector"
    _rto_log(f"TIMEOUT click: {msg}")
    _dump_page_state(page, "Save and File Movement")
    raise PwTimeout(msg)


def _screen_3_scrape_generated_application_id(page: Page) -> str:
    """Read application number from success dialog (e.g. *Application generated successfully*)."""
    application_id = ""
    try:
        dialog_text = page.locator(
            ".ui-dialog-content, .ui-messages-info, .ui-growl-message, "
            "[class*='dialog'] [class*='message'], [class*='success']"
        ).first
        dialog_text.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        text = dialog_text.inner_text()
        match = re.search(
            r"(?:application\s*(?:no\.?|number)\s*[:\-]?\s*|generated\s+successfully[^\n]*\s*)([A-Z0-9]{8,})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            application_id = match.group(1).strip()
        if not application_id:
            match2 = re.search(
                r"(?:application\s*(?:no\.?|number)\s*[:\-]?\s*)(\S+)", text, re.IGNORECASE
            )
            if match2:
                application_id = match2.group(1).strip()
        if not application_id:
            nums = re.findall(r"\b[A-Z]{2,}[0-9]{6,}\b|\b[0-9]{10,}\b", text)
            if nums:
                application_id = nums[0]
        logger.info("fill_rto: scraped application_id=%s", application_id)
        if application_id:
            _rto_log(f"scraped: rto_application_id = {application_id}")
    except PwTimeout:
        logger.warning("fill_rto: could not scrape application number from popup")
        _rto_log("WARNING: could not scrape application number from popup")
        _dump_page_state(page, "scrape application number failed")
    return application_id


def _screen_3d_hypothecation_save_confirm_scrape(page: Page, data: dict) -> str:
    """3d: Hypothecation if financier; Save and File Movement; Yes / Yes; scrape app no.; OK."""
    _rto_log("--- Screen 3d: Hypothecation (if financier), Save and File Movement, popups ---")
    financier = (data.get("financier") or "").strip()
    invoice_date = (data.get("invoice_date_str") or data.get("billing_date_str") or "").strip()

    if financier:
        logger.info("fill_rto: Screen 3d — hypothecation, financier=%s", financier[:30])
        hyp_ok = False
        for csel in _SCREEN3_HYP_CHECKBOX_SELECTORS:
            try:
                loc = page.locator(csel).first
                loc.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
                loc.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
                if not loc.is_checked():
                    loc.click()
                hyp_ok = True
                _rto_log("checkbox: Is vehicle hypothecated — checked")
                break
            except Exception:
                continue
        if not hyp_ok:
            try:
                page.get_by_label(re.compile(r"hypothecat", re.I)).first.click(timeout=_DEFAULT_TIMEOUT_MS)
                _rto_log("checkbox: Is vehicle hypothecated — label click")
            except Exception:
                _rto_log("WARNING: Is vehicle hypothecated checkbox not set")
        _pause()

        if not _screen_3_pf_dropdown_chain(
            page, _SCREEN3_HYP_TYPE_PF_WRAPPERS, "Hypothecation", label="Hypothecation Type"
        ):
            _screen_3_native_select_chain(
                page, _SCREEN3_HYP_TYPE_NATIVE, "Hypothecation", label="Hypothecation Type"
            )

        fn_ok = _fill_first_matching(
            page, _SCREEN3_FINANCIER_NAME_INPUT, financier, label="Financier Name"
        )
        if not fn_ok:
            try:
                _type_typeahead(
                    page,
                    "input[id*='financierName'], input[id*='financer']",
                    financier,
                    label="Financier typeahead",
                    timeout=_DEFAULT_TIMEOUT_MS,
                )
            except PwTimeout:
                _rto_log("WARNING: Financier Name not set")

        if invoice_date:
            _fill_first_matching(
                page,
                _SCREEN3_HYP_FROM_DATE_INPUT,
                invoice_date,
                label="Hypothecation From Date (invoice / billing date)",
            )

        st = (data.get("state") or "").strip()
        if st:
            st_disp = _init_cap_place_name(st)
            if not _screen_3_pf_dropdown_chain(
                page, _SCREEN3_FIN_STATE_PF_WRAPPERS, st_disp, label="Financier State"
            ):
                if not _screen_3_native_select_chain(
                    page, _SCREEN3_FIN_STATE_NATIVE, st_disp, label="Financier State"
                ):
                    _screen_3_native_select_chain(
                        page, _SCREEN3_FIN_STATE_NATIVE, st, label="Financier State (raw)"
                    )

        dist = (data.get("district") or "").strip()
        if dist:
            d_disp = _init_cap_place_name(dist)
            if not _screen_3_pf_dropdown_chain(
                page, _SCREEN3_FIN_DISTRICT_PF_WRAPPERS, d_disp, label="Financier District"
            ):
                if not _screen_3_native_select_chain(
                    page, _SCREEN3_FIN_DISTRICT_NATIVE, d_disp, label="Financier District"
                ):
                    _screen_3_native_select_chain(
                        page, _SCREEN3_FIN_DISTRICT_NATIVE, dist, label="Financier District (raw)"
                    )

        if data.get("pin"):
            _fill_first_matching(page, _SCREEN3_FIN_PIN_INPUT, data["pin"], label="Financier Pincode")

    _screen_3_click_save_file_movement(page)
    _wait_for_progress_close_loop(page)
    _dismiss_dialog(page, "Yes")
    _pause()
    _wait_for_progress_close_loop(page)
    _dismiss_dialog(page, "Yes")
    _rto_log("Screen 3: confirmation popups — Yes, Yes (incl. Are you sure)")
    _pause()
    _wait_for_progress_close_loop(page)

    application_id = _screen_3_scrape_generated_application_id(page)
    _dismiss_dialog(page, "OK", timeout=_DEFAULT_TIMEOUT_MS)
    _pause()

    wb_app = (data.get("rto_application_id") or "").strip()
    if not (str(application_id or "").strip()) and wb_app:
        application_id = wb_app
        _rto_log(f"Screen 3: using application id from workbench: {application_id!r}")

    return application_id


def _screen_3(page: Page, data: dict, *, skip_home: bool, skip_entry: bool = False) -> str:
    """Screen 3: optional Home → Entry, Tax mode, Insurance, Hypothecation, Save. Returns application_id.

    ``skip_home``: when True (skip point at screen 3, or ``RTO_FILL_SCREEN3_SKIP_HOME``), do not click Home.
    ``skip_entry``: when True (only valid with ``skip_home``), do not click Entry—already on the post-Entry form;
    activate **Vehicle Details** sub-tab only.
    """
    _set_screen("Screen 3")
    logger.info("fill_rto: Screen 3 — insurer=%s", data.get("insurer", "")[:20])
    _rto_log("--- Screen 3: Home, Entry, tax, insurance, hypothecation, application no ---")

    # 3a: Home (unless skip point) → Entry on pending-work grid (unless skip_entry).
    if skip_home:
        if skip_entry:
            _rto_log("Screen 3: skip Home + skip Entry — Vehicle Details sub-tab only (already on form)")
        else:
            _rto_log("Screen 3: skip Home — Entry only (already on home.xhtml)")
    else:
        _click(page, "a:has-text('Home'), button:has-text('Home'), [id*='home']", label="Home link")
        _pause()
        _wait_for_progress_close(page)
        try:
            page.wait_for_url(re.compile(r"home\.xhtml", re.I), timeout=15_000)
        except Exception:
            pass

    if not skip_entry:
        _screen_3_click_entry(page, data, skip_home=skip_home)

    # 3a2: Sub-tab **Vehicle Details** (post-Entry form uses PF tabs; Tax Mode at bottom of this tab).
    _screen_3_pf_subtab_click(page, r"Vehicle\s*Details", log_name="Vehicle Details")

    # 3b: Tax Mode — scroll into view, ONE TIME, then **Save Vehicle Details** before other sub-tabs.
    _screen_3_scroll_to_tax_mode(page)
    _screen_3_select_tax_mode_one_time(page)

    _screen_3_click_save_vehicle_details(page)

    # 3c: Scroll to sub-tab strip, open **Hypothecation/Insurance Information**, then fill insurance.
    _screen_3_scroll_subtab_bar_into_view(page)
    _screen_3_open_hypothecation_insurance_tab(page)
    _screen_3c_insurance_information(page, data)

    # 3d: Hypothecation (if financier), Save and File Movement, Yes / Yes, scrape app no., OK.
    return _screen_3d_hypothecation_save_confirm_scrape(page, data)


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
    _rqid = row.get("rto_queue_id")
    _billing_fmt = _fmt_date(row.get("billing_date"))
    data: dict = {
        "dealer_id": dealer_id,
        "rto_queue_id": int(_rqid) if _rqid is not None else None,
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
        "billing_date_str": _billing_fmt,
        "invoice_date_str": _billing_fmt,
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

        skip_from = max(0, min(6, int(RTO_FILL_SKIP_TO_SCREEN)))
        if RTO_FILL_TEST_APPLICATION_ID and not str(data.get("rto_application_id") or "").strip():
            data["rto_application_id"] = RTO_FILL_TEST_APPLICATION_ID

        if skip_from > 0:
            extra = ""
            if skip_from == 3:
                extra = " Screen 3: skip Home + Entry → Vehicle Details sub-tab first."
            _rto_log(
                f"TEMP SKIP: RTO_FILL_SKIP_TO_SCREEN={skip_from} — start at Screen {skip_from} "
                f"(skipped: dealer-home reset + screens 1..{skip_from - 1}){extra}"
            )
            logger.warning("fill_rto_row: RTO_FILL_SKIP_TO_SCREEN=%s (dev/testing)", skip_from)

        if skip_from <= 0:
            _ensure_vahan_dealer_home_for_screen1(page)

        if skip_from <= 1:
            _screen_1(page, office)
        if skip_from <= 2:
            _screen_2(page, data)

        application_id = ""
        if skip_from <= 3:
            screen3_skip_home = (skip_from == 3) or RTO_FILL_SCREEN3_SKIP_HOME
            screen3_skip_entry = screen3_skip_home and (
                (skip_from == 3) or RTO_FILL_SCREEN3_SKIP_ENTRY
            )
            application_id = _screen_3(
                page, data, skip_home=screen3_skip_home, skip_entry=screen3_skip_entry
            )
            if not (str(application_id or "").strip()):
                application_id = str(data.get("rto_application_id") or "").strip()
        else:
            application_id = str(data.get("rto_application_id") or "").strip()

        if skip_from <= 4:
            _screen_4(page)
        if skip_from <= 5:
            _screen_5(page, docs)
        total_payable = None
        if skip_from <= 6:
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
