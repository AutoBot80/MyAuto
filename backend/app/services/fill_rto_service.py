"""Per-row Vahan site fill logic. Called by rto_payment_service during batch processing.

Implements the 6-screen Playwright SOP for new vehicle registration on the
Vahan (parivahan.gov.in) dealer portal.

Browser lifetime: does not call ``Browser.close()``, ``BrowserContext.close()``, or ``Page.close()`` —
the operator Vahan tab stays open for the next row or manual use (same policy as Fill DMS).
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PwTimeout

from app.config import VAHAN_BASE_URL, VAHAN_DEALER_HOME_URL, get_ocr_output_dir, get_uploads_dir
from app.services.handle_browser_opening import get_or_open_site_page
from app.services.utility_functions import normalize_nominee_relationship_value

logger = logging.getLogger(__name__)

# --- Testing / build: edit here only (not .env). Use 0 / False / "" for production full SOP. ---
# ``RTO_FILL_SKIP_TO_SCREEN``: 0 = run all screens; 1–6 = start at that screen (skips dealer-home reset and earlier).
RTO_FILL_SKIP_TO_SCREEN = 5
# Screen 3: skip **Home** (you are already on ``home.xhtml`` with the grid). Also on when SKIP is 3.
RTO_FILL_SCREEN3_SKIP_HOME = False
# Screen 3: skip **Entry** — only with ``skip_home``; go straight to **Vehicle Details** sub-tab (already past Entry on the form).
# On when SKIP is 3 (and ``RTO_FILL_SCREEN3_SKIP_HOME`` is implied for that path).
RTO_FILL_SCREEN3_SKIP_ENTRY = False
# Screen 4: skip Verify (already clicked) — jump straight to Save-Options.
RTO_FILL_SCREEN4_SKIP_VERIFY = True
# Screen 4: skip everything up to **Dealer Document Upload** (Save-Options, File Movement, popups) — start at that tab/button.
RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD = True
# Optional seed for ``data["rto_application_id"]`` when the queue row has no app id (logging / return merge only).
RTO_FILL_TEST_APPLICATION_ID = ""

# Screen 5 — dealer document upload form (``formDocumentUpload:*`` in RTO dumps, e.g. ``9650693610_RTO.txt``):
# Sub Category PF wrapper + native ``select`` … ``subCatgId_input``; file input ``selectAndUploadFile_input``;
# **Upload Document** span; right chevron ``nextBtn``; **File Movement** ``fileFlowId``.
_SCREEN5_PF_SUBCAT_WRAPPER = '[id="formDocumentUpload:subCatgId"]'
_SCREEN5_FILE_INPUT = '[id="formDocumentUpload:selectAndUploadFile_input"]'
_SCREEN5_NEXT_BTN = '[id="formDocumentUpload:nextBtn"]'
_SCREEN5_FILE_MOVEMENT_BTN = '[id="formDocumentUpload:fileFlowId"]'
# Native ``<option>`` text varies (``Form 20`` vs ``FORM 20``) — match with regex per queue key.
_SCREEN5_SUBCAT_REGEX_BY_DOC_KEY: dict[str, re.Pattern] = {
    "FORM 20": re.compile(r"Form\s*20\b", re.I),
    "FORM 21": re.compile(r"Form\s*21\b", re.I),
    "FORM 22": re.compile(r"Form\s*22\b", re.I),
    "INSURANCE CERTIFICATE": re.compile(r"INSURANCE\s*CERTIFICATE|Insurance\s*Certificate", re.I),
    "INVOICE ORIGINAL": re.compile(r"INVOICE\s*ORIGINAL|Invoice\s*Original|GST\s*Retail", re.I),
    "AADHAAR_FRONT": re.compile(r"AADHAAR\s*CARD|Aadhaar", re.I),
    "AADHAAR_BACK": re.compile(r"AADHAAR\s*CARD|Aadhaar", re.I),
    "OWNER UNDERTAKING FORM": re.compile(r"OWNER\s*UNDERTAKING|Undertaking", re.I),
}

# Screen 3 — locators aligned with RTO trace page dumps (``ocr_output/.../*_RTO.txt``): sub-tab strip
# ``ul.ui-tabs-nav`` / ``a text='Hypothecation/Insurance Information'``, panel ``workbench_tabview:veh_info_tab``,
# and the same ``workbench_tabview:*`` id style as other workbench fields.
# **Tax Mode Details** is a PF ``ui-datatable`` row — live ids (``9650693610_RTO.txt``):
# ``workbench_tabview:tableTaxMode:0:taxModeType`` / ``...taxModeType_input``. Prefer these first (fast).
_SCREEN3_TAX_MODE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:tableTaxMode:0:taxModeType"]',
    '[id="workbench_tabview:tax_mode"]',
    '[id="workbench_tabview:taxMode"]',
)
_SCREEN3_TAX_MODE_NATIVE_SELECTORS: tuple[str, ...] = (
    'select[id="workbench_tabview:tableTaxMode:0:taxModeType_input"]',
    "select[id*='taxMode'], select[name*='taxMode']",
    'select[id="workbench_tabview:tax_mode_input"]',
    'select[id="workbench_tabview:taxMode_input"]',
)
_TAX_MODE_ONE_TIME_LABEL_RE = re.compile(r"ONE\s*TIME", re.I)
# MV Tax row / Tax Mode Details table (RTO logs often omit these — past element-dump cap).
_MV_TAX_ROW_RE = re.compile(r"MV\s*Tax", re.I)
_TAX_MODE_DETAILS_HDR_RE = re.compile(r"Tax\s*Mode\s*Details", re.I)
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
# (RTO page dumps: see ``_dump_page_state`` — all ``<select>`` + tax id hints; mixed list cap ``_DUMP_PAGE_STATE_GENERAL_CAP``).
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
    '[id="workbench_tabview:ins_from_input"]',
    '[id="workbench_tabview:insurance_from"]',
    '[id="workbench_tabview:insuranceFrom"]',
    "input[id*='insuranceFrom'], input[name*='insuranceFrom']",
)
_SCREEN3_INSURANCE_PERIOD_PF_WRAPPERS: tuple[str, ...] = ('[id="workbench_tabview:ins_year"]',)
_SCREEN3_INSURANCE_PERIOD_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:ins_year_input"]',
    "select[id*='ins_year'], select[name*='ins_year']",
)
# Hypothecation/Insurance — *Please Select Series Type* (often after policy / insurance from).
_SERIES_TYPE_STATE_SERIES_LABEL_RE = re.compile(r"STATE\s*SERIES", re.I)
_SCREEN3_SERIES_TYPE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:series_type"]',
    '[id="workbench_tabview:seriesType"]',
    '[id="workbench_tabview:reg_series_type"]',
    '[id="workbench_tabview:vh_series_type"]',
)
_SCREEN3_SERIES_TYPE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:series_type_input"]',
    'select[id="workbench_tabview:seriesType_input"]',
    "select[id*='seriesType'], select[name*='seriesType'], select[id*='series_type']",
)
_SCREEN3_IDV_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:idv"]',
    "input[id*='idv'], input[id*='declaredValue'], input[name*='idv']",
)
_SCREEN3_ISHYPO_WRAPPER = '[id="workbench_tabview:isHypo"]'
_SCREEN3_ISHYPO_BOX = '[id="workbench_tabview:isHypo"] .ui-chkbox-box'
_SCREEN3_ISHYPO_INPUT = '[id="workbench_tabview:isHypo_input"]'
_SCREEN3_HYP_CHECKBOX_SELECTORS: tuple[str, ...] = (
    _SCREEN3_ISHYPO_INPUT,
    "input[type='checkbox'][id*='hypothecated']",
    "input[type='checkbox'][id*='isHypothecated']",
    "input[type='checkbox'][name*='hypothecated']",
)
_SCREEN3_NOMINEE_NAME_INPUT: tuple[str, ...] = ('[id="workbench_tabview:nominationname1"]',)
_SCREEN3_NOMINEE_RELATION_PF_WRAPPERS: tuple[str, ...] = ('[id="workbench_tabview:vm_rel1"]',)
_SCREEN3_NOMINATION_DATE_INPUT = '[id="workbench_tabview:nominationdate1_input"]'
_SCREEN3_HYP_TYPE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_hp_type"]',
)
_SCREEN3_HYP_TYPE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:hpa_hp_type_input"]',
)
_SCREEN3_FINANCIER_NAME_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_fncr_name"]',
)
_SCREEN3_HYP_FROM_DATE_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_from_dt_input"]',
)
_SCREEN3_FIN_STATE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_fncr_state"]',
)
_SCREEN3_FIN_STATE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:hpa_fncr_state_input"]',
)
_SCREEN3_FIN_DISTRICT_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_fncr_district"]',
)
_SCREEN3_FIN_DISTRICT_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:hpa_fncr_district_input"]',
)
_SCREEN3_FIN_HOUSE_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_fncr_add1"]',
)
_SCREEN3_FIN_CITY_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_fncr_add2"]',
)
_SCREEN3_FIN_PIN_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:hpa_fncr_pincode"]',
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

# Fast UI timing: **200ms** per attempt; **2s** total budget when looping (retries / polls).
_FIRST_TRY_MS = 200
_LOOP_BUDGET_MS = 2_000
# Playwright locator/action waits (ms) — 2s default (matches loop budget).
_DEFAULT_TIMEOUT_MS = _LOOP_BUDGET_MS
_LONG_TIMEOUT_MS = 10_000
# Delay after each discrete UI action (s) — not a Playwright timeout.
_ACTION_WAIT_S = 0.2
# ``_dump_page_state``: cap for mixed interactive list; **all** ``<select>`` are also logged separately.
_DUMP_PAGE_STATE_GENERAL_CAP = 150
_DUMP_PAGE_STATE_OPTION_PREVIEW = 32
_DUMP_PAGE_STATE_PF_TAX_HINT_CAP = 40
_DUMP_PAGE_STATE_PF_SERIES_HINT_CAP = 40
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
    """Dump frames, **all** ``<select>`` (with option previews), and mixed visible controls into the RTO log.

    The legacy **150** cap applied in **DOM order**, so late controls (e.g. Tax Mode under Vehicle Details)
    never appeared. We now (1) list **every** ``select`` with options / ``inVehTab`` / visibility, and
    (2) build the mixed list with **visible selects first**, then other controls, up to
    ``_DUMP_PAGE_STATE_GENERAL_CAP``.
    """
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

    cap = _DUMP_PAGE_STATE_GENERAL_CAP
    opt_prev = _DUMP_PAGE_STATE_OPTION_PREVIEW
    pf_cap = _DUMP_PAGE_STATE_PF_TAX_HINT_CAP
    pf_series_cap = _DUMP_PAGE_STATE_PF_SERIES_HINT_CAP

    _JS_PAGE_STATE_SNAPSHOT = f"""() => {{
        const GENERAL_CAP = {cap};
        const OPT_PREVIEW = {opt_prev};
        const PF_TAX_CAP = {pf_cap};
        const PF_SERIES_CAP = {pf_series_cap};

        function inHypInsurancePanel(el) {{
            let p = el;
            for (let d = 0; d < 22 && p; d++) {{
                const id = (p.id || '');
                if (/hypothec|Hypothec|HypothecationOwner|hyp_ins|insurance.*tab/i.test(id)) return true;
                p = p.parentElement;
            }}
            return false;
        }}

        function visible(el) {{
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            return true;
        }}

        function pushGen(els, el) {{
            const tag = el.tagName.toLowerCase();
            const id = el.id || '';
            const name = el.getAttribute('name') || '';
            const type = el.getAttribute('type') || '';
            const role = el.getAttribute('role') || '';
            const value = (el.value || '').substring(0, 60);
            const text = (el.innerText || '').substring(0, 60).replace(/\\n/g, ' ');
            const cls = (el.className || '').substring(0, 80);
            els.push({{tag, id, name, type, role, value, text, cls}});
        }}

        const selector = 'input, select, textarea, button, a, [role], label, h1, h2, h3, h4, span.ui-outputlabel';
        const allNodes = Array.from(document.querySelectorAll(selector));
        const selectNodes = allNodes.filter(e => e.tagName === 'SELECT');
        const otherNodes = allNodes.filter(e => e.tagName !== 'SELECT');

        const general = [];
        for (const el of selectNodes) {{
            if (!visible(el)) continue;
            if (general.length >= GENERAL_CAP) break;
            pushGen(general, el);
        }}
        for (const el of otherNodes) {{
            if (general.length >= GENERAL_CAP) break;
            if (!visible(el)) continue;
            pushGen(general, el);
        }}

        const tab = document.querySelector('[id="workbench_tabview:veh_info_tab"]');
        const selectsDetailed = [];
        document.querySelectorAll('select').forEach((el, i) => {{
            const opts = Array.from(el.options).map(o => ({{
                t: (o.textContent || '').trim().substring(0, 72),
                v: (o.value || '').substring(0, 40)
            }}));
            const selLabel = el.options[el.selectedIndex]
                ? el.options[el.selectedIndex].text.trim().substring(0, 120)
                : '';
            const idLower = (el.id || '').toLowerCase();
            const looksSeries =
                idLower.includes('series') ||
                idLower.includes('regn_series') ||
                idLower.includes('stateseries');
            selectsDetailed.push({{
                i,
                id: el.id || '',
                name: el.name || '',
                cls: (el.className || '').substring(0, 100),
                si: el.selectedIndex,
                selLabel,
                nOpt: el.options.length,
                opts: opts.slice(0, OPT_PREVIEW),
                vis: visible(el),
                inVeh: !!(tab && tab.contains(el)),
                inHypIns: inHypInsurancePanel(el),
                seriesHint: looksSeries
            }});
        }});

        const pfTaxHints = [];
        document.querySelectorAll(
            '[id*="tax_mode"], [id*="taxMode"], [id*="mv_tax"], [id*="mvTax"], [id*="TaxMode"]'
        ).forEach((el) => {{
            if (pfTaxHints.length >= PF_TAX_CAP) return;
            if (el.tagName === 'SELECT') return;
            const id = el.id || '';
            if (!id) return;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            const hidden = st.display === 'none' || st.visibility === 'hidden' || r.width < 2 || r.height < 2;
            pfTaxHints.push({{
                tag: el.tagName,
                id,
                cls: (el.className || '').substring(0, 80),
                txt: (el.innerText || '').substring(0, 120).replace(/\\n/g, ' '),
                hidden
            }});
        }});

        const pfSeriesHints = [];
        document.querySelectorAll(
            '[id*="series_type"], [id*="seriesType"], [id*="regn_series"], [id*="SeriesType"], '
            + '[id*="vh_series"], [id*="state_series"], [id*="STATE_SERIES"]'
        ).forEach((el) => {{
            if (pfSeriesHints.length >= PF_SERIES_CAP) return;
            const id = el.id || '';
            if (!id) return;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            const hidden = st.display === 'none' || st.visibility === 'hidden' || r.width < 2 || r.height < 2;
            pfSeriesHints.push({{
                tag: el.tagName,
                id,
                cls: (el.className || '').substring(0, 80),
                txt: (el.innerText || '').substring(0, 120).replace(/\\n/g, ' '),
                hidden,
                inHypIns: inHypInsurancePanel(el)
            }});
        }});

        return {{ general, selectsDetailed, pfTaxHints, pfSeriesHints }};
    }}"""

    def _log_element_list(tag: str, elements: list[dict]) -> None:
        _rto_log(f"  [{tag}] visible interactive elements ({len(elements)}, selects first — cap {cap}):")
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

    def _log_select_dump(frame_tag: str, rows: list[dict]) -> None:
        _rto_log(f"  [{frame_tag}] ALL <select> ({len(rows)} — ids, selected label, options preview):")
        for row in rows:
            oid = row.get("id") or ""
            oname = row.get("name") or ""
            opts = row.get("opts") or []
            parts = [
                f"select[{row.get('i', '')}]",
                f"id={oid!r}" if oid else "id=",
                f"name={oname!r}" if oname else "",
                f"si={row.get('si')}",
                f"sel={row.get('selLabel', '')!r}",
                f"nOpt={row.get('nOpt')}",
                f"vis={row.get('vis')}",
                f"inVehTab={row.get('inVeh')}",
                f"inHypIns={row.get('inHypIns')}",
                f"seriesHint={row.get('seriesHint')}",
            ]
            _rto_log(f"    {' '.join(p for p in parts if p != '')}")
            opt_bits = []
            for o in opts:
                t = (o.get("t") or "").strip()
                v = (o.get("v") or "").strip()
                if v:
                    opt_bits.append(f"{t!r} [value={v!r}]")
                else:
                    opt_bits.append(f"{t!r}")
            joined = " | ".join(opt_bits)
            if len(joined) > 2000:
                joined = joined[:1997] + "..."
            _rto_log(f"      options: {joined}")

    def _log_pf_tax_hints(frame_tag: str, hints: list[dict]) -> None:
        if not hints:
            return
        _rto_log(
            f"  [{frame_tag}] PF / id hints (tax / taxMode / mv_tax, non-SELECT, cap {pf_cap}):"
        )
        for h in hints:
            _rto_log(
                f"    {h.get('tag', '')} id={h.get('id', '')!r} hidden={h.get('hidden')} "
                f"class={h.get('cls', '')!r} text={h.get('txt', '')!r}"
            )

    def _log_pf_series_hints(frame_tag: str, hints: list[dict]) -> None:
        if not hints:
            return
        _rto_log(
            f"  [{frame_tag}] PF / id hints (series_type / seriesType / regn_series / state_series, "
            f"cap {pf_series_cap}):"
        )
        for h in hints:
            _rto_log(
                f"    {h.get('tag', '')} id={h.get('id', '')!r} hidden={h.get('hidden')} "
                f"inHypIns={h.get('inHypIns')} class={h.get('cls', '')!r} text={h.get('txt', '')!r}"
            )

    for fi, frame in enumerate(page.frames):
        try:
            ftag = f"frame[{fi}] {frame.name or 'main'}"
            snap = frame.evaluate(_JS_PAGE_STATE_SNAPSHOT)
            general = snap.get("general") or []
            selects_detailed = snap.get("selectsDetailed") or []
            pf_hints = snap.get("pfTaxHints") or []
            pf_series = snap.get("pfSeriesHints") or []
            _log_element_list(ftag, general)
            _log_select_dump(ftag, selects_detailed)
            _log_pf_tax_hints(ftag, pf_hints)
            _log_pf_series_hints(ftag, pf_series)
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
        _dump_page_state(page, f"TIMEOUT click: {label}")
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
        _dump_page_state(page, f"TIMEOUT fill: {label}")
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
        _dump_page_state(page, f"TIMEOUT fill: {label}")
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
        _dump_page_state(page, f"TIMEOUT select: {label}")
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
        _dump_page_state(page, f"TIMEOUT typeahead: {label}")
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
        _dump_page_state(page, f"TIMEOUT pf-dropdown: {label}")
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
    """Modal *Generated Application No* shown after successful Inward / OTP / Save Vehicle Details (PrimeFaces)."""
    try:
        loc = page.locator(".ui-dialog:visible").filter(
            has_text=re.compile(r"Generated\s+Application|Application\s*No\.?\s*:", re.I)
        )
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    for sel in (
        ".ui-dialog:has-text('Generated Application No')",
        ".ui-dialog:has-text('Generated Application')",
    ):
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=1500):
                return loc
        except Exception:
            continue
    return None


def _poll_generated_application_dialog(page: Page, max_ms: int = 15_000):
    deadline = time.monotonic() + max_ms / 1000.0
    while time.monotonic() < deadline:
        dlg = _find_visible_generated_application_dialog(page)
        if dlg is None:
            dlg = _screen_3_find_visible_application_info_dialog(page)
        if dlg is not None:
            return dlg
        time.sleep(_FIRST_TRY_MS / 1000.0)
    return None


def _scrape_application_id_from_dialog_text(text: str) -> str:
    """Parse Vahan application number from dialog body (e.g. ``Application No. :RJ26041148051328``)."""
    raw = (text or "").replace("\xa0", " ")
    # Common on workbench: ``Application No. :RJ26...`` (space before colon)
    m = re.search(
        r"Application\s*No\.?\s*:\s*([A-Z]{2}\d{8,24})\b",
        raw,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().upper()
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
    m = re.search(r"[:]\s*([A-Z]{2}\d{8,24})\b", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip().upper()
    found = re.findall(r"\b[A-Z]{2}\d{10,20}\b", raw)
    return found[0].upper() if found else ""


def _generated_application_dialog_full_text(dlg: Locator) -> str:
    """Full text from PrimeFaces dialog (title bar + content)."""
    try:
        t = (dlg.inner_text(timeout=6000) or "").strip()
        if t:
            return t
    except Exception:
        pass
    try:
        return (dlg.locator(".ui-dialog-content").first.inner_text(timeout=4000) or "").strip()
    except Exception:
        return ""


def _click_ok_on_generated_application_dialog(page: Page, dlg: Locator) -> bool:
    """Click **Ok** on *Generated Application No* — fast: text filter first, then buttonpane fallback."""
    try:
        dlg.locator("button, .ui-button, a.ui-button, input[type='button']").filter(
            has_text=re.compile(r"^\s*Ok\s*$", re.I)
        ).first.click(timeout=2000, force=True)
        _rto_log("Generated Application dialog: Ok (control with Ok text)")
        return True
    except Exception:
        pass
    try:
        dlg.locator(
            ".ui-dialog-buttonpane button, .ui-dialog-buttonpane .ui-button"
        ).first.click(timeout=2000, force=True)
        _rto_log("Generated Application dialog: Ok (buttonpane)")
        return True
    except Exception:
        pass
    try:
        page.locator(".ui-dialog:visible button").filter(
            has_text=re.compile(r"^\s*Ok\s*$", re.I)
        ).first.click(timeout=2000, force=True)
        _rto_log("Generated Application dialog: Ok (visible dialog)")
        return True
    except Exception:
        pass
    try:
        dlg.get_by_role("button", name=re.compile(r"^\s*Ok\s*$", re.I)).first.click(
            timeout=2000, force=True
        )
        _rto_log("Generated Application dialog: Ok (role=button)")
        return True
    except Exception:
        pass
    return False


def _dismiss_generated_application_no_dialog(page: Page) -> str:
    """If the *Generated Application No* modal is open: scrape id, click Ok, wait. Returns id or empty string."""
    dlg = _find_visible_generated_application_dialog(page) or _poll_generated_application_dialog(page, max_ms=15_000)
    if dlg is None:
        return ""
    text = _generated_application_dialog_full_text(dlg)
    app_id = _scrape_application_id_from_dialog_text(text)
    if app_id:
        _rto_log(f"dialog: Generated Application No — application_id={app_id}")
        logger.info("fill_rto: Generated Application No dialog — application_id=%s", app_id)
    else:
        _rto_log(f"dialog: Generated Application No — could not parse id; dialog text snippet: {text[:500]!r}")
    if not _click_ok_on_generated_application_dialog(page, dlg):
        _dismiss_dialog(page, "OK", timeout=3000)
        _dismiss_dialog(page, "Ok", timeout=3000)
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


def _upload_file(
    page: Page,
    file_path: Path,
    *,
    timeout: int = _DEFAULT_TIMEOUT_MS,
    file_input_selector: str | None = None,
    wait_progress_after: bool = True,
) -> None:
    """Set the file on the file input (first ``input[type=file]`` unless *file_input_selector* is set)."""
    sel = file_input_selector or "input[type='file']"
    try:
        file_input = page.locator(sel).first
        file_input.set_input_files(str(file_path), timeout=timeout)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT upload: {file_path.name}")
        _dump_page_state(page, f"upload failed: {file_path.name}")
        raise
    _rto_log(f"upload: {file_path.name}")
    _pause()
    if wait_progress_after:
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
    """Scroll so **Tax Mode** is visible — it sits far down the Vehicle Details tab (often off-screen).

    PrimeFaces tab body ``veh_info_tab`` may be the scroll container; we scroll that panel toward
    the bottom, then ``scrollIntoView({ block: 'center' })`` on the tax control.
    """
    tab_panel = page.locator('[id="workbench_tabview:veh_info_tab"]').first
    try:
        tab_panel.wait_for(state="attached", timeout=4000)
        tab_panel.evaluate(
            "(el) => el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)"
        )
        _pause()
        _rto_log("Screen 3: scrolled Vehicle Details tab panel toward bottom (Tax Mode region)")
    except Exception as e:
        _rto_log(f"Screen 3: tab panel scroll skipped ({e!s})")

    fast_sel = 'select[id="workbench_tabview:tableTaxMode:0:taxModeType_input"]'
    loc = page.locator(fast_sel).first
    try:
        loc.wait_for(state="attached", timeout=3000)
        loc.evaluate("el => el.scrollIntoView({ block: 'center', inline: 'nearest' })")
        _pause()
        _rto_log(f"Screen 3: scrolled to Tax Mode (fast tableTaxMode:0 {fast_sel!r})")
        return
    except Exception:
        pass

    for sel in (
        '[id="workbench_tabview:tableTaxMode:0:taxModeType"]',
        *_SCREEN3_TAX_MODE_NATIVE_SELECTORS,
        *_SCREEN3_TAX_MODE_PF_WRAPPERS,
    ):
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="attached", timeout=3000)
            loc.evaluate("el => el.scrollIntoView({ block: 'center', inline: 'nearest' })")
            _pause()
            _rto_log(f"Screen 3: scrolled to Tax Mode ({sel!r})")
            return
        except Exception:
            continue
    _rto_log("WARNING: scroll to Tax Mode: no matching locator")


def _screen_3_tax_mode_selected_option_text(loc: Locator) -> str:
    """Visible label of the selected ``option`` (native ``select``)."""
    try:
        return (
            loc.evaluate(
                "el => { const o = el.options[el.selectedIndex]; "
                "return o ? (o.textContent || '').trim() : ''; }"
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _screen_3_verify_tax_mode_one_time(loc: Locator) -> bool:
    t = _screen_3_tax_mode_selected_option_text(loc)
    return bool(t and _TAX_MODE_ONE_TIME_LABEL_RE.search(t))


def _screen_3_try_select_tax_mode_on_locator(loc: Locator, log_tag: str) -> bool:
    """Select **ONE TIME** on a native ``select`` via JS index scan (fastest path)."""
    try:
        loc.wait_for(state="attached", timeout=3000)
        idx = loc.evaluate(
            """el => {
                for (let i = 0; i < el.options.length; i++) {
                    if (/ONE\\s*TIME/i.test((el.options[i].textContent || '').trim())) return i;
                }
                return -1;
            }"""
        )
        if idx is None or int(idx) < 0:
            return False
        loc.select_option(index=int(idx), force=True)
        _pause()
        if _screen_3_verify_tax_mode_one_time(loc):
            _rto_log(f"select: Tax Mode = ONE TIME ({log_tag})")
            return True
    except Exception as e:
        logger.debug("fill_rto: Tax Mode try %s: %s", log_tag, e)
    return False


def _screen_3_select_tax_mode_js_in_veh_tab(page: Page) -> bool:
    """Last-resort: set ``selectedIndex`` on the matching ``select`` inside ``veh_info_tab``."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const tab = document.querySelector('[id="workbench_tabview:veh_info_tab"]');
                    if (!tab) return false;
                    const sels = tab.querySelectorAll('select');
                    for (const s of sels) {
                        for (let i = 0; i < s.options.length; i++) {
                            const raw = (s.options[i].textContent || '').trim();
                            if (/ONE\\s*TIME/i.test(raw)) {
                                s.selectedIndex = i;
                                s.dispatchEvent(new Event('input', { bubbles: true }));
                                s.dispatchEvent(new Event('change', { bubbles: true }));
                                return true;
                            }
                        }
                    }
                    return false;
                }"""
            )
        )
    except Exception as e:
        logger.debug("fill_rto: Tax Mode JS veh tab: %s", e)
        return False


def _screen_3_any_tax_mode_one_time_in_veh_tab(page: Page) -> bool:
    """True if any ``select`` inside Vehicle Details shows **ONE TIME**."""
    tab = page.locator('[id="workbench_tabview:veh_info_tab"]')
    try:
        n = tab.locator("select").count()
        for i in range(min(n, 48)):
            loc = tab.locator("select").nth(i)
            if _screen_3_verify_tax_mode_one_time(loc):
                return True
    except Exception:
        pass
    return False


def _screen_3_select_tax_mode_one_time(page: Page) -> bool:
    """Set Tax Mode to **ONE TIME** — direct ``tableTaxMode:0`` ids first; then MV Tax row; PF; JS.

    Returns ``True`` only when the MV Tax (or verified) ``select`` shows **ONE TIME**. On failure,
    returns ``False`` so Screen 3 does not save or advance.
    """
    veh = page.locator('[id="workbench_tabview:veh_info_tab"]')

    fast_native = 'select[id="workbench_tabview:tableTaxMode:0:taxModeType_input"]'
    fast_pf = '[id="workbench_tabview:tableTaxMode:0:taxModeType"]'
    try:
        fast_loc = page.locator(fast_native)
        if fast_loc.count() > 0 and _screen_3_try_select_tax_mode_on_locator(
            fast_loc.first, "tableTaxMode:0:taxModeType_input (direct)"
        ):
            return True
    except Exception as e:
        logger.debug("fill_rto: Tax Mode fast native: %s", e)

    try:
        if page.locator(fast_pf).count() > 0:
            wid_m = re.search(r'id="([^"]+)"', fast_pf)
            wrapper_id = wid_m.group(1) if wid_m else ""
            _select_pf_dropdown(
                page,
                fast_pf,
                "ONE TIME",
                label="Tax Mode",
                option_label_regex=_TAX_MODE_ONE_TIME_LABEL_RE,
                timeout=_DEFAULT_TIMEOUT_MS,
            )
            if wrapper_id:
                _close_pf_selectonemenu_overlay(page, wrapper_id)
            _pause()
            if _screen_3_any_tax_mode_one_time_in_veh_tab(page):
                _rto_log(f"pf-dropdown: Tax Mode = ONE TIME ({fast_pf!r})")
                return True
    except Exception as e:
        logger.debug("fill_rto: Tax Mode fast PF: %s", e)

    try:
        row_sel = veh.locator("tr").filter(has_text=_MV_TAX_ROW_RE).locator("select").first
        if row_sel.count() > 0 and _screen_3_try_select_tax_mode_on_locator(row_sel, "MV Tax row"):
            return True
    except Exception as e:
        logger.debug("fill_rto: Tax Mode MV Tax row: %s", e)

    try:
        tbl = veh.locator("table").filter(has_text=_TAX_MODE_DETAILS_HDR_RE).first
        if tbl.count() > 0:
            tsel = tbl.locator("select").last
            if tsel.count() > 0 and _screen_3_try_select_tax_mode_on_locator(
                tsel, "Tax Mode Details table"
            ):
                return True
    except Exception as e:
        logger.debug("fill_rto: Tax Mode Details table: %s", e)

    try:
        gsel = page.locator("tr").filter(has_text=_MV_TAX_ROW_RE).locator("select").first
        if gsel.count() > 0 and _screen_3_try_select_tax_mode_on_locator(gsel, "MV Tax row (page)"):
            return True
    except Exception as e:
        logger.debug("fill_rto: Tax Mode MV Tax row global: %s", e)

    for nsel in _SCREEN3_TAX_MODE_NATIVE_SELECTORS:
        try:
            all_l = page.locator(nsel)
            c = all_l.count()
            if c == 0:
                continue
            try_order = (c - 1, 0)
            tried: set[int] = set()
            for i in try_order:
                if i in tried or i < 0:
                    continue
                tried.add(i)
                loc = all_l.nth(i)
                if _screen_3_try_select_tax_mode_on_locator(loc, f"native[{i}] {nsel!r}"):
                    return True
        except Exception as e:
            logger.debug("fill_rto: Tax Mode native %s: %s", nsel, e)

    for wsel in _SCREEN3_TAX_MODE_PF_WRAPPERS:
        if page.locator(wsel).count() == 0:
            continue
        try:
            wid_m = re.search(r'id="([^"]+)"', wsel)
            wrapper_id = wid_m.group(1) if wid_m else ""
            _select_pf_dropdown(
                page,
                wsel,
                "ONE TIME",
                label="Tax Mode",
                option_label_regex=_TAX_MODE_ONE_TIME_LABEL_RE,
                timeout=_DEFAULT_TIMEOUT_MS,
            )
            if wrapper_id:
                _close_pf_selectonemenu_overlay(page, wrapper_id)
            _pause()
            if _screen_3_any_tax_mode_one_time_in_veh_tab(page):
                _rto_log(f"pf-dropdown: Tax Mode = ONE TIME ({wsel!r})")
                return True
        except Exception as e:
            logger.debug("fill_rto: Tax Mode PF try %s: %s", wsel, e)

    if _screen_3_select_tax_mode_js_in_veh_tab(page):
        _pause()
        _wait_for_progress_close_loop(page)
        if _screen_3_any_tax_mode_one_time_in_veh_tab(page):
            _rto_log("select: Tax Mode = ONE TIME (JS in veh_info_tab + verify)")
            return True

    logger.debug("fill_rto: Tax Mode could not be set (ONE TIME)")
    _rto_log("WARNING: Tax Mode not set (ONE TIME)")
    return False


def _screen_3_log_tax_mode_wiring_snapshot(page: Page) -> None:
    """Log Tax Mode control ids / selected labels (RTO.txt) so selectors can be wired for a fast path."""
    try:
        snap = page.evaluate(
            """() => {
                const tab = document.querySelector('[id="workbench_tabview:veh_info_tab"]');
                const out = { taxSelects: [], mvTax: null };
                if (!tab) return out;
                tab.querySelectorAll('select').forEach((s) => {
                    const id = (s.id || '').toLowerCase();
                    if (!/tax|mv_tax|mvtax|taxmode|tax_mode/.test(id)) return;
                    const o = s.options[s.selectedIndex];
                    out.taxSelects.push({
                        id: s.id || '',
                        name: s.name || '',
                        si: s.selectedIndex,
                        sel: o ? o.textContent.trim().substring(0, 100) : '',
                        nOpt: s.options.length
                    });
                });
                for (const r of tab.querySelectorAll('tr')) {
                    if (!/MV\\s*Tax/i.test(r.textContent || '')) continue;
                    const s = r.querySelector('select');
                    if (!s) continue;
                    const o = s.options[s.selectedIndex];
                    out.mvTax = {
                        selectId: s.id || '',
                        selectName: s.name || '',
                        sel: o ? o.textContent.trim().substring(0, 100) : '',
                        nOpt: s.options.length
                    };
                    break;
                }
                return out;
            }"""
        )
        _rto_log(f"Screen 3: Tax Mode wiring snapshot (ids + MV Tax row): {snap!r}")
    except Exception as e:
        logger.debug("fill_rto: Tax Mode wiring snapshot: %s", e)


def _screen_3_click_dialog_dismiss_any(dlg: Locator, page: Page, *, log_prefix: str = "Screen 3") -> bool:
    """Click OK / Ok / Close / Yes / first button on a PrimeFaces ``ui-dialog`` (or similar)."""
    for pat in (
        r"^\s*OK\s*$",
        r"^\s*Ok\s*$",
        r"^\s*Close\s*$",
        r"^\s*Yes\s*$",
        r"^\s*Proceed\s*$",
    ):
        try:
            dlg.get_by_role("button", name=re.compile(pat, re.I)).first.click(timeout=1500)
            _rto_log(f"{log_prefix}: dialog dismiss ({pat})")
            return True
        except Exception:
            continue
    try:
        dlg.locator(
            "button, a.ui-button, input[type='button'], input[type='submit']"
        ).first.click(timeout=1500)
        _rto_log(f"{log_prefix}: dialog dismiss (first control)")
        return True
    except Exception:
        pass
    try:
        page.locator(".ui-dialog-buttonpane:visible button").first.click(timeout=4000)
        _rto_log(f"{log_prefix}: dialog dismiss (visible buttonpane)")
        return True
    except Exception:
        pass
    _dismiss_dialog(page, "OK", timeout=2000)
    return False


def _screen_3_find_visible_application_info_dialog(page: Page) -> Locator | None:
    """Fallback: any **visible** dialog whose text suggests application / success (titles vary by build)."""
    try:
        loc = page.locator(".ui-dialog:visible").filter(
            has_text=re.compile(
                r"Application\s*No|Generated|successfully|saved|registration|No\.?\s*:",
                re.I,
            )
        )
        if loc.count() > 0 and loc.first.is_visible(timeout=400):
            return loc.first
    except Exception:
        pass
    try:
        loc2 = page.locator("[role='dialog']:visible").filter(
            has_text=re.compile(r"Application\s*No|Generated|successfully|saved", re.I)
        )
        if loc2.count() > 0 and loc2.first.is_visible(timeout=400):
            return loc2.first
    except Exception:
        pass
    return None


def _screen_3_any_modal_dialog_visible(page: Page) -> bool:
    try:
        if page.locator(".ui-dialog:visible").count() > 0:
            return True
    except Exception:
        pass
    try:
        if page.locator("[role='dialog']:visible").count() > 0:
            return True
    except Exception:
        pass
    return False


def _screen_3_post_save_vehicle_details_dialogs(page: Page, data: dict | None) -> str:
    """After **Save Vehicle Details**: scrape application no. if shown; dismiss OK/Close; return id or ``''``."""
    _wait_for_progress_close_loop(page)
    app_id = ""
    dlg0 = _find_visible_generated_application_dialog(page) or _poll_generated_application_dialog(
        page, max_ms=6_000
    )
    if dlg0 is not None:
        text0 = _generated_application_dialog_full_text(dlg0)
        _rto_log(f"Screen 3: Save Vehicle Details — dialog text snippet: {(text0 or '')[:900]!r}")
        app_id = _scrape_application_id_from_dialog_text(text0)
        if app_id:
            _rto_log(f"Screen 3: application_id from Save Vehicle Details dialog: {app_id!r}")
            if data is not None:
                data["rto_application_id"] = app_id
        else:
            _rto_log("Screen 3: could not parse application id from Save Vehicle Details dialog (see snippet)")
        if not _click_ok_on_generated_application_dialog(page, dlg0):
            _screen_3_click_dialog_dismiss_any(dlg0, page)
        _wait_for_progress_close_loop(page)
    # Residual popups / alternate wording
    for _ in range(12):
        if not _screen_3_any_modal_dialog_visible(page):
            break
        dlg = _screen_3_find_visible_application_info_dialog(page)
        if dlg is None:
            try:
                dlg = page.locator(".ui-dialog:visible").first
                if not dlg.is_visible(timeout=300):
                    break
            except Exception:
                break
        try:
            t = dlg.inner_text(timeout=4000) or ""
        except Exception:
            t = ""
        if t:
            _rto_log(f"Screen 3: follow-up dialog snippet: {t[:900]!r}")
        aid = _scrape_application_id_from_dialog_text(t)
        if aid and not app_id:
            app_id = aid
            if data is not None:
                data["rto_application_id"] = aid
            _rto_log(f"Screen 3: application_id from follow-up dialog: {aid!r}")
        _screen_3_click_dialog_dismiss_any(dlg, page)
        _wait_for_progress_close_loop(page)
        time.sleep(0.2)
    for btn in ("OK", "Yes", "Close"):
        _dismiss_dialog(page, btn, timeout=1200)
    return app_id


def _screen_3_click_save_vehicle_details(page: Page) -> None:
    """Persist Vehicle Details tab (Tax Mode, etc.) before switching to Hypothecation/Insurance."""
    last_err: Exception | None = None
    for sel in _SCREEN3_SAVE_VEHICLE_DETAILS_SELECTORS:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=5000)
            loc.click(timeout=5000)
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


def _screen_3_clear_blocking_overlay_after_vehicle_save(page: Page, data: dict | None = None) -> None:
    """After **Save Vehicle Details**: scrape/dismiss application dialogs; clear ``#msgDialog_modal`` mask."""
    _screen_3_post_save_vehicle_details_dialogs(page, data)
    try:
        page.locator("#msgDialog_modal").first.wait_for(state="hidden", timeout=4000)
    except PwTimeout:
        _rto_log("WARNING: msgDialog_modal still visible — Escape then retry")
        try:
            page.keyboard.press("Escape")
            _pause()
            page.locator("#msgDialog_modal").first.wait_for(state="hidden", timeout=3000)
        except PwTimeout:
            _rto_log("WARNING: msgDialog_modal overlay may block next step")
    _wait_for_progress_close_loop(page)


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


_JS_POPUP_PREFLIGHT_SNAPSHOT = """() => {
    const MAX_D = 28, MAX_B = 48, MAX_O = 20;
    function clip(s, n) {
        return String(s || '').substring(0, n).replace(/\\s+/g, ' ');
    }
    const seen = new Set();
    function pushEl(el, arr, lim) {
        if (!el || arr.length >= lim || seen.has(el)) return;
        seen.add(el);
        const r = el.getBoundingClientRect();
        const st = window.getComputedStyle(el);
        const vis = r.width >= 2 && r.height >= 2 && st.display !== 'none' && st.visibility !== 'hidden';
        arr.push({
            tag: el.tagName,
            id: clip(el.id, 140),
            cls: clip(el.className, 100),
            vis: vis,
            z: st.zIndex || '',
            txt: clip(el.innerText, 200)
        });
    }
    const dialogs = [];
    document.querySelectorAll('.ui-dialog, [role="dialog"], .ui-confirm-dialog').forEach((e) => pushEl(e, dialogs, MAX_D));
    const overlays = [];
    document.querySelectorAll(
        '.ui-widget-overlay, .ui-dialog-mask, #msgDialog_modal, .ui-blockui, .blockUI, [class*="ui-overlay"]'
    ).forEach((e) => pushEl(e, overlays, MAX_O));
    const dialogButtons = [];
    document.querySelectorAll('.ui-dialog, [role="dialog"]').forEach((dlg) => {
        const pid = clip(dlg.id, 100);
        dlg.querySelectorAll(
            'button, input[type="button"], input[type="submit"], a.ui-button, .ui-button, span.ui-button'
        ).forEach((b) => {
            if (dialogButtons.length >= MAX_B) return;
            const t = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
            dialogButtons.push({
                parentId: pid,
                tag: b.tagName,
                id: clip(b.id, 100),
                cls: clip(b.className, 80),
                typ: b.type || '',
                val: clip(b.value, 48),
                txt: clip(t, 72)
            });
        });
    });
    const extra = [];
    document.querySelectorAll(
        '[id*="dialog"], [id*="Dialog"], [id*="popup"], [id*="Popup"], [class*="modal"], [class*="Modal"]'
    ).forEach((e) => {
        if (extra.length >= 15) return;
        if (e.closest('.ui-dialog')) return;
        pushEl(e, extra, 15);
    });
    return { dialogs: dialogs, overlays: overlays, dialogButtons: dialogButtons, extraPopupLike: extra };
}"""


def _screen_3_dump_frames_and_popup_candidates(page: Page) -> None:
    """Before Hypothecation sub-tab: log every frame plus dialog / overlay / Ok-button inventory (RTO.txt)."""
    _rto_log("=== Pre–Hypothecation sub-tab: frames + popup-like DOM (for Ok / modal selectors) ===")
    try:
        _rto_log(f"page url: {(page.url or '')[:500]}")
    except Exception:
        _rto_log("page url: (unavailable)")
    try:
        frames = page.frames
        _rto_log(f"frame count: {len(frames)}")
        for fi, frame in enumerate(frames):
            try:
                fn = frame.name or "(main)"
                fu = (frame.url or "")[:260]
                _rto_log(f"  frame[{fi}] name={fn!r} url={fu}")
            except Exception as e:
                _rto_log(f"  frame[{fi}] meta error: {e!s}")
            try:
                snap = frame.evaluate(_JS_POPUP_PREFLIGHT_SNAPSHOT)
            except Exception as e:
                _rto_log(f"  frame[{fi}] evaluate error: {e!s}")
                continue
            try:
                raw = json.dumps(snap, ensure_ascii=False)
            except Exception:
                raw = str(snap)
            max_chunk = 6000
            if len(raw) <= max_chunk:
                _rto_log(f"  frame[{fi}] popup inventory JSON: {raw}")
            else:
                _rto_log(f"  frame[{fi}] popup inventory JSON (truncated, {len(raw)} chars):")
                for start in range(0, len(raw), max_chunk):
                    _rto_log(f"  frame[{fi}] ... chunk: {raw[start:start + max_chunk]}")
    except Exception as e:
        _rto_log(f"Pre–Hypothecation frame/popup dump error: {e!s}")
    _rto_log("=== End Pre–Hypothecation frame / popup dump ===")


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
    _dump_page_state(page, f"field not filled: {label}")
    return False


def _screen_3_is_select_placeholder_label(t: str) -> bool:
    """True if ``t`` looks like an empty PF/native dropdown placeholder (do not treat as user value)."""
    u = t.upper().strip()
    if not u:
        return True
    if u in ("--SELECT--", "--SELECT-", "SELECT", "--SELECT---"):
        return True
    if "SELECT" in u and "--" in t:
        return True
    return False


def _screen_3_native_select_has_non_placeholder_value(page: Page, selectors: tuple[str, ...]) -> bool:
    """True if the first matching ``select`` shows a non-placeholder selected option."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=2000)
            txt = (
                loc.evaluate(
                    "el => { const o = el.options[el.selectedIndex]; "
                    "return o ? (o.textContent || '').trim() : ''; }"
                )
                or ""
            ).strip()
            if txt and not _screen_3_is_select_placeholder_label(txt):
                return True
        except Exception:
            continue
    return False


def _screen_3_insurance_type_already_set(page: Page) -> bool:
    """Vahan may pre-fill **Insurance Type** on Hypothecation/Insurance — do not overwrite."""
    label_ids = (
        "workbench_tabview:insurance_type_label",
        "workbench_tabview:insuranceType_label",
        "workbench_tabview:ins_type_label",
    )
    if any(_workbench_pf_menu_label_has_value(page, lid) for lid in label_ids):
        return True
    return _screen_3_native_select_has_non_placeholder_value(page, _SCREEN3_INSURANCE_TYPE_NATIVE)


def _screen_3_insurance_company_already_set(page: Page) -> bool:
    """Vahan may pre-fill **Insurance Company** — do not overwrite."""
    label_ids = (
        "workbench_tabview:insurance_company_label",
        "workbench_tabview:insuranceCompany_label",
        "workbench_tabview:ins_cd_label",
    )
    if any(_workbench_pf_menu_label_has_value(page, lid) for lid in label_ids):
        return True
    return _screen_3_native_select_has_non_placeholder_value(page, _SCREEN3_INSURANCE_COMPANY_NATIVE)


def _screen_3_insurance_period_already_set(page: Page) -> bool:
    """Vahan may pre-fill **Insurance Period (in year)** — do not overwrite."""
    if _workbench_pf_menu_label_has_value(page, "workbench_tabview:ins_year_label"):
        return True
    return _screen_3_native_select_has_non_placeholder_value(page, _SCREEN3_INSURANCE_PERIOD_NATIVE)


def _screen_3_log_series_type_ids_probe(page: Page) -> None:
    """Log distinct element ids matching **Series Type** / STATE SERIES naming (happy-path discovery for RTO.txt)."""
    try:
        ids = page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll("[id]").forEach((el) => {
                    const id = el.id || "";
                    if (!id || out.length >= 40) return;
                    if (/series|regn_series|state_series|SeriesType|series_type/i.test(id)) out.push(id);
                });
                return [...new Set(out)];
            }"""
        )
        if ids:
            _rto_log(f"Screen 3c: series-related ids in DOM (probe): {ids!r}")
    except Exception as e:
        logger.debug("fill_rto: series id probe: %s", e)


def _screen_3_series_type_is_state_series(page: Page) -> bool:
    """True if **Series Type** already shows **STATE SERIES** (Vahan pre-fill)."""
    label_ids = (
        "workbench_tabview:series_type_label",
        "workbench_tabview:seriesType_label",
        "workbench_tabview:reg_series_type_label",
        "workbench_tabview:vh_series_type_label",
    )
    for lid in label_ids:
        try:
            lab = page.locator(f'[id="{lid}"]').first
            t = (lab.inner_text(timeout=2000) or "").strip()
            if t and _SERIES_TYPE_STATE_SERIES_LABEL_RE.search(t):
                return True
        except Exception:
            continue
    for sel in _SCREEN3_SERIES_TYPE_NATIVE:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=2000)
            txt = (
                loc.evaluate(
                    "el => { const o = el.options[el.selectedIndex]; "
                    "return o ? (o.textContent || '').trim() : ''; }"
                )
                or ""
            ).strip()
            if txt and _SERIES_TYPE_STATE_SERIES_LABEL_RE.search(txt):
                return True
        except Exception:
            continue
    return False


def _screen_3_input_already_has_value(page: Page, selectors: tuple[str, ...]) -> bool:
    """True if the first matching control already has non-empty value (input or display)."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=2000)
            v = (
                loc.evaluate(
                    "el => (el.value || el.getAttribute('value') || el.textContent || '').trim()"
                )
                or ""
            ).strip()
            if v:
                return True
        except Exception:
            continue
    return False


def _rto_log_json_chunks(label: str, payload: object, *, max_chunk: int = 6500) -> None:
    """Log JSON in chunks so RTO.txt stays readable (same idea as frame popup dump)."""
    try:
        raw = json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        _rto_log(f"{label} (JSON error: {e!s})")
        return
    if len(raw) <= max_chunk:
        _rto_log(f"{label}: {raw}")
        return
    _rto_log(f"{label} (truncated, {len(raw)} chars):")
    for start in range(0, len(raw), max_chunk):
        _rto_log(f"  ... chunk: {raw[start : start + max_chunk]}")


def _screen_3_log_hypothecation_nominee_wiring(page: Page) -> None:
    """Log stable ids + hpa contents for **Is Hypothecated**, nominee, financier wiring (RTO.txt)."""
    _rto_log("=== Hypothecation / nominee wiring snapshot (element ids for automation) ===")
    _rto_log(
        "direct: isHypo wrapper="
        f"{_SCREEN3_ISHYPO_WRAPPER}; click box={_SCREEN3_ISHYPO_BOX}; "
        f"hidden input={_SCREEN3_ISHYPO_INPUT}"
    )
    _rto_log(
        "direct: Add Nominee Yes/No = input[name='workbench_tabview:nomineeradiobtn1'][value=Y|N]; "
        "nominee name = [id='workbench_tabview:nominationname1']; "
        "relation = [id='workbench_tabview:vm_rel1'] / select#workbench_tabview:vm_rel1_input"
    )
    try:
        snap = page.evaluate(
            """() => {
                const out = { isHypo: null, hpa: null, nominee: null, hypRelatedIds: [] };
                const inp = document.getElementById("workbench_tabview:isHypo_input");
                const wrap = document.getElementById("workbench_tabview:isHypo");
                if (inp || wrap) {
                    out.isHypo = {
                        inputId: inp ? inp.id : null,
                        checked: inp ? !!inp.checked : null,
                        wrapperClass: wrap ? (wrap.className || "").slice(0, 120) : null,
                        hasBox: wrap ? !!wrap.querySelector(".ui-chkbox-box") : false,
                    };
                }
                const hpa = document.getElementById("workbench_tabview:hpa");
                if (hpa) {
                    const controls = [];
                    hpa.querySelectorAll("input, select, textarea").forEach((el) => {
                        if (controls.length < 45) {
                            controls.push({
                                tag: el.tagName,
                                id: el.id || "",
                                name: el.name || "",
                                type: el.type || "",
                                vis: !!(el.offsetParent !== null && el.getClientRects().length),
                            });
                        }
                    });
                    out.hpa = { controlCount: controls.length, controls };
                }
                const nt = document.getElementById("workbench_tabview:nomineeradiobtn1");
                if (nt) {
                    out.nominee = {
                        tableId: "workbench_tabview:nomineeradiobtn1",
                        radios: ["workbench_tabview:nomineeradiobtn1:0", "workbench_tabview:nomineeradiobtn1:1"],
                    };
                }
                document.querySelectorAll("[id]").forEach((el) => {
                    const id = el.id || "";
                    if (!id || out.hypRelatedIds.length >= 40) return;
                    if (/hpa|hypothec|Hypothec|financ|isHypo/i.test(id)) out.hypRelatedIds.push(id);
                });
                return out;
            }"""
        )
        _rto_log_json_chunks("Hypothecation/nominee wiring JSON", snap)
    except Exception as e:
        _rto_log(f"Hypothecation/nominee wiring snapshot JS failed: {e!s}")
    _rto_log("=== End Hypothecation / nominee wiring snapshot ===")


def _screen_3_wait_hypothecation_ajax_panel(page: Page) -> None:
    """After toggling **Is Hypothecated**, PrimeFaces updates ``workbench_tabview:hpa`` — wait for financier name input."""
    _wait_for_progress_close_loop(page)
    try:
        page.locator('[id="workbench_tabview:hpa_fncr_name"]').first.wait_for(
            state="visible", timeout=_LOOP_BUDGET_MS
        )
        _rto_log("hpa: financier name input visible")
    except Exception:
        _rto_log("NOTE: hpa financier name input not visible after AJAX wait")


def _screen_3_toggle_pf_boolean_hypo(page: Page, *, want_checked: bool) -> bool:
    """Toggle **Is Vehicle Hypothecated** — click ``.ui-chkbox-box`` inside wrapper (fast, direct)."""
    inp = page.locator(_SCREEN3_ISHYPO_INPUT).first
    try:
        inp.wait_for(state="attached", timeout=_LOOP_BUDGET_MS)
    except Exception:
        _rto_log("WARNING: isHypo_input not in DOM")
        return False
    try:
        if inp.is_checked() == want_checked:
            _rto_log(
                f"skip: isHypo already {'checked' if want_checked else 'unchecked'}"
            )
            return True
    except Exception:
        pass
    box = page.locator(_SCREEN3_ISHYPO_BOX).first
    try:
        box.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
        box.click(timeout=_FIRST_TRY_MS)
        _pause()
        if inp.is_checked() == want_checked:
            _rto_log("checkbox: Is vehicle hypothecated — toggled via .ui-chkbox-box")
            return True
    except Exception:
        pass
    try:
        page.evaluate(
            """(want) => {
                const box = document.querySelector("#workbench_tabview\\\\:isHypo .ui-chkbox-box");
                if (box) { box.click(); return; }
                const inp = document.getElementById("workbench_tabview:isHypo_input");
                if (inp && !!inp.checked !== want) inp.click();
            }""",
            want_checked,
        )
        _pause()
        if inp.is_checked() == want_checked:
            _rto_log("checkbox: Is vehicle hypothecated — toggled via JS")
            return True
    except Exception as e:
        _rto_log(f"WARNING: isHypo toggle failed: {e!s}")
    return inp.is_checked() == want_checked


def _screen_3_set_vehicle_hypothecated_checkbox(page: Page, *, has_financier: bool) -> None:
    """Set **Is Vehicle Hypothecated?** to match finance (checked when ``has_financier``)."""
    want = bool(has_financier)
    ok = _screen_3_toggle_pf_boolean_hypo(page, want_checked=want)
    if not ok:
        _screen_3_log_hypothecation_nominee_wiring(page)
        _rto_log(
            f"WARNING: Is vehicle hypothecated could not be set to "
            f"{'checked' if want else 'unchecked'} (see wiring snapshot above)"
        )
    elif want:
        _screen_3_wait_hypothecation_ajax_panel(page)
    else:
        _wait_for_progress_close_loop(page)


def _screen_3_fill_datepicker_js(page: Page, sel: str, date_str: str, *, label: str) -> None:
    """Fill a ``hasDatepicker`` field via JS — mirrors ``_fill_workbench_purchase_date``."""
    if not date_str:
        return
    loc = page.locator(sel).first
    try:
        loc.wait_for(state="attached", timeout=_LOOP_BUDGET_MS)
        loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)

        try:
            loc.evaluate(
                """(el, v) => {
                    el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                date_str,
            )
        except Exception:
            try:
                loc.fill(date_str, timeout=_LOOP_BUDGET_MS)
            except Exception:
                loc.fill(date_str, timeout=_LOOP_BUDGET_MS, force=True)

        _pause()
        _close_workbench_datepicker_if_open(page)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT fill: {label} selector={sel} value={date_str[:40]}")
        _dump_page_state(page, f"TIMEOUT fill: {label}")
        raise
    logger.debug("fill_rto: filled %s = %s", label, date_str[:40])
    _rto_log(f"fill: {label} = {date_str[:80]}{'…' if len(date_str) > 80 else ''}")


def _screen_3_fill_financier_hypothecation_details(page: Page, data: dict) -> None:
    """After hypothecation is checked — fill hpa_* fields (live ids from RTO log)."""
    financier = (data.get("financier") or "").strip()
    if not financier:
        return
    invoice_date = (data.get("invoice_date_str") or data.get("billing_date_str") or "").strip()

    # Hypothecation Type — use overlay click (use_native_select=False) because
    # PrimeFaces does not update the visible label from the native <select> alone.
    try:
        _select_pf_dropdown(
            page,
            _SCREEN3_HYP_TYPE_PF_WRAPPERS[0],
            "Hypothecation",
            label="Hypothecation Type",
            use_native_select=False,
        )
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:hpa_hp_type")
    except Exception:
        if not _screen_3_native_select_chain(
            page, _SCREEN3_HYP_TYPE_NATIVE, "Hypothecation", label="Hypothecation Type"
        ):
            _rto_log("WARNING: Hypothecation Type not set")
            _dump_page_state(page, "dropdown not set: Hypothecation Type")

    _fill_first_matching(page, _SCREEN3_FINANCIER_NAME_INPUT, financier, label="Financier Name")

    if invoice_date:
        _screen_3_fill_datepicker_js(
            page, _SCREEN3_HYP_FROM_DATE_INPUT[0], invoice_date, label="Hypothecation From Date"
        )

    # House No. & Street Name → financier address
    addr = (data.get("address") or "").strip()
    if addr:
        _fill_first_matching(page, _SCREEN3_FIN_HOUSE_INPUT, addr, label="Financier House No.")

    # Village/Town/City
    city = (data.get("city") or "").strip()
    if city:
        _fill_first_matching(page, _SCREEN3_FIN_CITY_INPUT, _init_cap_place_name(city), label="Financier City")

    st = (data.get("state") or "").strip()
    if st:
        st_disp = _init_cap_place_name(st)
        if not _screen_3_pf_dropdown_chain(
            page, _SCREEN3_FIN_STATE_PF_WRAPPERS, st_disp, label="Financier State"
        ):
            if not _screen_3_native_select_chain(
                page, _SCREEN3_FIN_STATE_NATIVE, st_disp, label="Financier State"
            ):
                _rto_log("WARNING: Financier State not set")
                _dump_page_state(page, "dropdown not set: Financier State")
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:hpa_fncr_state")
        # State AJAX populates district options — wait for progress overlay to close
        _wait_for_progress_close_loop(page)

    # District — use district from data; fall back to city (initcap) when district is empty
    dist = (data.get("district") or "").strip()
    if not dist:
        dist = city
    if dist:
        d_disp = _init_cap_place_name(dist)
        if not _screen_3_pf_dropdown_chain(
            page, _SCREEN3_FIN_DISTRICT_PF_WRAPPERS, d_disp, label="Financier District"
        ):
            if not _screen_3_native_select_chain(
                page, _SCREEN3_FIN_DISTRICT_NATIVE, d_disp, label="Financier District"
            ):
                _rto_log(f"WARNING: Financier District not set (tried {d_disp!r})")
                _dump_page_state(page, "dropdown not set: Financier District")
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:hpa_fncr_district")

    if data.get("pin"):
        _fill_first_matching(page, _SCREEN3_FIN_PIN_INPUT, data["pin"], label="Financier Pincode")


def _screen_3c_click_nominee_radio(page: Page, *, want_yes: bool) -> bool:
    """Click **Add Nominee Details** Yes/No via PrimeFaces ``.ui-radiobutton-box`` to trigger AJAX.

    The raw ``<input type="radio">`` is hidden; clicking it with ``force=True`` does not fire
    PrimeFaces behaviours, so the nominee fields panel never loads.  We click the visible
    ``.ui-radiobutton-box`` associated with the target radio instead.
    """
    idx = 0 if want_yes else 1
    label = "Yes" if want_yes else "No"
    inp_sel = f'[id="workbench_tabview:nomineeradiobtn1:{idx}"]'

    try:
        td = page.locator(f'td:has({inp_sel}) .ui-radiobutton-box').first
        td.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
        td.click(timeout=_FIRST_TRY_MS)
        _rto_log(f"radio: Add Nominee Details — {label}")
        return True
    except Exception:
        pass

    try:
        tbl = page.locator('[id="workbench_tabview:nomineeradiobtn1"]').first
        lbl = tbl.locator("label", has_text=re.compile(rf"^\s*{label}\s*$", re.I)).first
        lbl.click(timeout=_FIRST_TRY_MS)
        _rto_log(f"radio: Add Nominee Details — {label} (label click)")
        return True
    except Exception:
        pass

    try:
        page.locator(inp_sel).first.click(force=True, timeout=_FIRST_TRY_MS)
        _rto_log(f"radio: Add Nominee Details — {label} (force input)")
        return True
    except Exception as e:
        _rto_log(f"WARNING: Add Nominee Details {label}: {e!s}")
    return False


def _screen_3c_nominee_add_details(page: Page, data: dict) -> None:
    """**Add Nominee Details** Yes/No radio; when Yes, fill nominee name, relation, nomination date.

    Direct IDs from RTO log:
      - Yes radio:       ``workbench_tabview:nomineeradiobtn1:0``  (value='Y')
      - No radio:        ``workbench_tabview:nomineeradiobtn1:1``  (value='N')
      - Name:            ``workbench_tabview:nominationname1``
      - Relation:        ``workbench_tabview:vm_rel1`` (PF) / ``workbench_tabview:vm_rel1_input`` (native)
      - Nomination Date: ``workbench_tabview:nominationdate1_input`` (hasDatepicker)
    """
    nm_raw = (data.get("nominee_name") or "").strip()
    rel_raw = (data.get("nominee_relationship") or "").strip()
    rel_norm = normalize_nominee_relationship_value(rel_raw) if rel_raw else ""
    want_nominee = bool(nm_raw or rel_norm)

    _rto_log(
        f"nominee: want={want_nominee}, name={nm_raw!r}, relationship={rel_norm!r}"
    )

    if not want_nominee:
        _screen_3c_click_nominee_radio(page, want_yes=False)
        _pause()
        return

    if not _screen_3c_click_nominee_radio(page, want_yes=True):
        return
    _pause()
    _wait_for_progress_close_loop(page)

    # Wait for nominee name input to appear (AJAX loads the panel after Yes click)
    nm_loc = page.locator(_SCREEN3_NOMINEE_NAME_INPUT[0]).first
    try:
        nm_loc.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
        _rto_log("nominee: name input visible after Yes click")
    except Exception:
        _rto_log("NOTE: nominee name input not visible, trying scroll")
        try:
            nm_loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
        except Exception:
            _rto_log("WARNING: nominee name input not found in DOM after Yes click")
            _dump_page_state(page, "nominee name input missing after Yes click")

    if nm_raw:
        _fill_first_matching(page, _SCREEN3_NOMINEE_NAME_INPUT, nm_raw, label="Nominee Name")

    if rel_norm:
        if not _screen_3_pf_dropdown_chain(
            page,
            _SCREEN3_NOMINEE_RELATION_PF_WRAPPERS,
            rel_norm,
            label="Relation with nominee",
            option_label_regex=re.compile(rf"^\s*{re.escape(rel_norm)}\s*$", re.I),
        ):
            _screen_3_native_select_chain(
                page,
                ('select[id="workbench_tabview:vm_rel1_input"]',),
                rel_norm,
                label="Relation with nominee (native)",
            )
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:vm_rel1")

    # Nomination Date — invoice date in DD-Mon-YYYY (same datepicker pattern)
    nom_date = (data.get("invoice_date_str") or data.get("billing_date_str") or "").strip()
    if nom_date:
        _screen_3_fill_datepicker_js(
            page, _SCREEN3_NOMINATION_DATE_INPUT, nom_date, label="Nomination Date"
        )


def _screen_3c_insurance_information(page: Page, data: dict) -> None:
    """3c: On Hypothecation/Insurance tab — insurance fields, Series Type (STATE SERIES), IDV."""
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

    if _screen_3_insurance_type_already_set(page):
        _rto_log("skip: Insurance Type already set on form (Vahan pre-fill)")
    else:
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
        if _screen_3_insurance_company_already_set(page):
            _rto_log("skip: Insurance Company already set on form (Vahan pre-fill)")
        elif not _screen_3_pf_dropdown_chain(
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
    elif _screen_3_insurance_company_already_set(page):
        _rto_log("skip: Insurance Company already set on form (Vahan pre-fill; no insurer in queue data)")

    if _screen_3_input_already_has_value(page, _SCREEN3_POLICY_NO_INPUT):
        _rto_log("skip: Policy/Cover Note No. already set on form (Vahan pre-fill)")
    else:
        _fill_first_matching(
            page, _SCREEN3_POLICY_NO_INPUT, data.get("policy_num", ""), label="Policy/Cover Note No."
        )

    if _screen_3_input_already_has_value(page, _SCREEN3_INSURANCE_FROM_INPUT):
        _rto_log("skip: Insurance From already set on form (Vahan pre-fill)")
    else:
        _fill_first_matching(
            page, _SCREEN3_INSURANCE_FROM_INPUT, data.get("policy_from_str", ""), label="Insurance From"
        )

    if _screen_3_insurance_period_already_set(page):
        _rto_log("skip: Insurance Period (in year) already set on form (Vahan pre-fill)")
    else:
        _rto_log(
            "NOTE: Insurance Period (in year) not pre-filled — leaving as-is "
            "(add insurance_period_label to queue data if automation must set it)"
        )

    # Series Type (*Please Select Series Type*) → **STATE SERIES** (skip if already STATE SERIES).
    for scroll_sel in (_SCREEN3_SERIES_TYPE_PF_WRAPPERS[0], _SCREEN3_SERIES_TYPE_NATIVE[0]):
        try:
            s_loc = page.locator(scroll_sel).first
            s_loc.wait_for(state="attached", timeout=2500)
            s_loc.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
            _pause()
            break
        except Exception:
            continue

    if _screen_3_series_type_is_state_series(page):
        _rto_log("skip: Series Type already STATE SERIES (Vahan pre-fill)")
    else:
        if not _screen_3_pf_dropdown_chain(
            page,
            _SCREEN3_SERIES_TYPE_PF_WRAPPERS,
            "STATE SERIES",
            label="Series Type",
            option_label_regex=_SERIES_TYPE_STATE_SERIES_LABEL_RE,
        ):
            ser_ok = False
            for sel in _SCREEN3_SERIES_TYPE_NATIVE:
                try:
                    nloc = page.locator(sel).first
                    nloc.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
                    nloc.select_option(
                        label=_SERIES_TYPE_STATE_SERIES_LABEL_RE, timeout=_DEFAULT_TIMEOUT_MS
                    )
                    ser_ok = True
                    _rto_log(f"select: Series Type = STATE SERIES ({sel!r})")
                    break
                except Exception:
                    continue
            if not ser_ok:
                try:
                    _type_typeahead(
                        page,
                        "input[id*='seriesType'], input[id*='series_type'], input[name*='seriesType']",
                        "STATE SERIES",
                        label="Series Type typeahead",
                        timeout=_DEFAULT_TIMEOUT_MS,
                    )
                except PwTimeout:
                    _rto_log("WARNING: Series Type not set (STATE SERIES)")

    idv_v = data.get("idv")
    idv_s = "" if idv_v is None else str(idv_v).strip()
    if idv_s:
        _fill_first_matching(page, _SCREEN3_IDV_INPUT, idv_s, label="Insurance Declared Value")

    financier = (data.get("financier") or "").strip()
    _screen_3_set_vehicle_hypothecated_checkbox(page, has_financier=bool(financier))
    if financier:
        logger.info("fill_rto: Screen 3c — financier hypothecation details, financier=%s", financier[:40])
        _screen_3_fill_financier_hypothecation_details(page, data)
    _screen_3c_nominee_add_details(page, data)


def _screen_3_click_save_file_movement(page: Page) -> None:
    """Click **Save and File Movement** (workbench id or button text)."""
    try:
        btn = page.get_by_role(
            "button",
            name=re.compile(r"^\s*Save and File Movement\s*$", re.I),
        ).first
        btn.wait_for(state="visible", timeout=4000)
        btn.scroll_into_view_if_needed(timeout=_DEFAULT_TIMEOUT_MS)
        btn.click(timeout=_DEFAULT_TIMEOUT_MS)
        _pause()
        _rto_log("click: Save and File Movement (exact button name)")
        return
    except Exception as e:
        logger.debug("fill_rto: Save and File Movement by role: %s", e)

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
    dlg = _screen_3_find_visible_application_info_dialog(page)
    if dlg is not None:
        try:
            text = dlg.inner_text(timeout=6000) or ""
            application_id = _scrape_application_id_from_dialog_text(text)
            if application_id:
                logger.info("fill_rto: scraped application_id=%s", application_id)
                _rto_log(f"scraped: rto_application_id = {application_id} (visible ui-dialog)")
                return application_id
            _rto_log(f"Screen 3d: application dialog snippet (unparsed): {text[:900]!r}")
        except Exception as e:
            logger.debug("fill_rto: scrape from application dialog: %s", e)
    try:
        dialog_text = page.locator(
            ".ui-dialog-content:visible, .ui-dialog:visible, .ui-messages-info, .ui-growl-message, "
            "[class*='dialog'] [class*='message'], [class*='success']"
        ).first
        dialog_text.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        text = dialog_text.inner_text()
        application_id = _scrape_application_id_from_dialog_text(text)
        if not application_id:
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
        elif text:
            _rto_log(f"Screen 3d: scrape fallback dialog snippet: {text[:900]!r}")
    except PwTimeout:
        logger.warning("fill_rto: could not scrape application number from popup")
        _rto_log("WARNING: could not scrape application number from popup")
        _dump_page_state(page, "scrape application number failed")
    return application_id


def _screen_3d_hypothecation_save_confirm_scrape(page: Page, data: dict) -> str:
    """3d: Save and File Movement; Yes / Yes; scrape app no.; OK. (Hypothecation / nominee filled in 3c.)"""
    _rto_log("--- Screen 3d: Save and File Movement, confirmation popups, scrape app no. ---")

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
    if not _screen_3_select_tax_mode_one_time(page):
        _rto_log(
            "ABORT Screen 3: Tax Mode (ONE TIME) not verified — skipping Save Vehicle Details, "
            "Hypothecation/Insurance, and Save and File Movement"
        )
        raise RuntimeError(
            "RTO Screen 3: Tax Mode must be ONE TIME before Save Vehicle Details — automation stopped"
        )

    _screen_3_click_save_vehicle_details(page)
    _screen_3_clear_blocking_overlay_after_vehicle_save(page, data)

    # 3c: Scroll to sub-tab strip, open **Hypothecation/Insurance Information**, then fill insurance.
    _screen_3_scroll_subtab_bar_into_view(page)
    _screen_3_open_hypothecation_insurance_tab(page)
    _screen_3c_insurance_information(page, data)

    # 3d: Save and File Movement, Yes / Yes, scrape app no., OK (hypothecation / nominee handled in 3c).
    return _screen_3d_hypothecation_save_confirm_scrape(page, data)


def _screen_4_file_movement_dialogs(page: Page) -> None:
    """After **File Movement**: first popup — type ``None`` in the input, **Save**; second popup — **Save** only."""
    _pause()
    try:
        dlg = page.locator(".ui-dialog:visible, [role='dialog']:visible").first
        dlg.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
    except PwTimeout:
        _rto_log("WARNING: File Movement — first dialog not visible within budget")
        _dump_page_state(page, "File Movement first dialog missing")
        return

    filled = False
    for sel in ("input[type='text']", "textarea", "input:not([type='hidden'])"):
        try:
            inp = dlg.locator(sel).first
            inp.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            inp.fill("None")
            _rto_log("fill: File Movement dialog — input = None")
            filled = True
            break
        except Exception:
            continue
    if not filled:
        try:
            page.locator(".ui-dialog:visible input[type='text']").first.fill("None")
            _rto_log("fill: File Movement dialog — input = None (page-scoped fallback)")
        except Exception as e:
            _rto_log(f"WARNING: File Movement dialog — could not fill None: {e!s}")

    _pause()
    _dismiss_dialog(page, "Save", timeout=_LOOP_BUDGET_MS)
    _pause()
    _wait_for_progress_close_loop(page)

    _dismiss_dialog(page, "Save", timeout=_LOOP_BUDGET_MS)
    _pause()
    _wait_for_progress_close_loop(page)


def _screen_4_click_first(
    page: Page,
    selectors: tuple[str, ...],
    *,
    label: str,
    scroll: bool = True,
    optional: bool = False,
) -> bool:
    """Try each selector with ``_FIRST_TRY_MS``; on total failure dump page state.

    Returns ``True`` if a selector was clicked, ``False`` only when *optional* is set
    and nothing was found.  When *optional* is ``False`` (default), raises ``PwTimeout``.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            if scroll:
                loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
            loc.click(timeout=_FIRST_TRY_MS)
            _rto_log(f"click: {label} ({sel})")
            return True
        except Exception:
            continue
    _rto_log(f"WARNING: {label} not found — dumping page state")
    _dump_page_state(page, f"button not found: {label}")
    if optional:
        return False
    raise RuntimeError(f"{label} button not found on page (tried {len(selectors)} selectors)")


def _screen_4(page: Page) -> None:
    """Screen 4: Verify → … → Save-Options → File Movement → (None + Save) → (Save) → Dealer Document Upload tab → progress wheel."""
    _set_screen("Screen 4")
    logger.info("fill_rto: Screen 4 — Verify & Document Upload nav")
    _rto_log("--- Screen 4: Verify, Save Options / File Movement, Dealer Document Upload ---")

    if not RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD:
        # 4a: Scroll down and click **Verify** (skip if flag set or button absent)
        if RTO_FILL_SCREEN4_SKIP_VERIFY:
            _rto_log("SKIP: Verify (RTO_FILL_SCREEN4_SKIP_VERIFY=True)")
        else:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            _pause()

            verify_clicked = _screen_4_click_first(
                page,
                (
                    "button:has-text('Verify')",
                    "input[value='Verify']",
                    "input[type='submit'][value*='Verify']",
                    "a:has-text('Verify')",
                ),
                label="Verify",
                optional=True,
            )

            if verify_clicked:
                _pause()
                _wait_for_progress_close_loop(page)
            else:
                _rto_log("Verify button not visible — assuming already clicked, continuing")

        # 4b: Scroll to bottom, click **Save-Options**, pick **File Movement**
        #     id from page dump: button  app_disapp_form:j_idt1913_button  text='Save-Options'
        #     File Movement:     a       app_disapp_form:fileMove          text='File Movement'
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _pause()

        _screen_4_click_first(
            page,
            (
                '[id="app_disapp_form:j_idt1913_button"]',
                "button:has-text('Save-Options')",
                "button:has-text('Save Options')",
                "input[value*='Save-Options']",
                "input[value*='Save Options']",
                "a:has-text('Save-Options')",
                "a:has-text('Save Options')",
            ),
            label="Save-Options",
        )
        _pause()

        _screen_4_click_first(
            page,
            (
                '[id="app_disapp_form:fileMove"]',
                'a:has-text("File Movement")',
                '[id*="fileMove"]',
            ),
            label="File Movement",
            scroll=False,
        )

        _pause()
        _wait_for_progress_close_loop(page)

        # 4c: First popup — fill **None** in input, **Save**; second popup — **Save** only
        _screen_4_file_movement_dialogs(page)
    else:
        _rto_log(
            "SKIP: Save-Options / File Movement / popups "
            "(RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD=True) — start at Dealer Document Upload"
        )

    # 4d: **Dealer Document Upload** — on ``home.xhtml`` pending-work grid it is a **button** in the Action
    #     column (text ``Dealer-Document-Upload`` with hyphens; id like ``workDetails:0:j_idt273``).  On
    #     ``workbench.xhtml`` it may be a top tab.  Scroll to bottom first — the grid action is below the fold.
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause()
    _screen_4_click_first(
        page,
        (
            "button:has-text('Dealer-Document-Upload')",
            "button[id^='workDetails:'][id$=':j_idt273']",
            "a:has-text('Dealer-Document-Upload')",
            "a:has-text('Dealer Document Upload')",
            "li[role='tab']:has-text('Dealer Doc') a",
            "button:has-text('Dealer Document Upload')",
            "input[value*='Dealer Document Upload']",
            "input[value*='Dealer-Document-Upload']",
            "[role='tab']:has-text('Dealer Document')",
        ),
        label="Dealer Document Upload",
    )

    # Progress wheel — closes by itself
    _pause()
    _wait_for_progress_close_loop(page)


def _screen_5_select_subcategory(page: Page, doc_key: str, sub_category_text: str) -> None:
    """PrimeFaces **Sub Category** on document upload: ``formDocumentUpload:subCatgId`` (see RTO dumps)."""
    rx = _SCREEN5_SUBCAT_REGEX_BY_DOC_KEY.get(doc_key)
    if rx is None:
        rx = re.compile(re.escape(sub_category_text).replace(r"\ ", r"\s+"), re.I)
    _select_pf_dropdown(
        page,
        _SCREEN5_PF_SUBCAT_WRAPPER,
        "",
        label=f"Sub Category ({doc_key})",
        option_label_regex=rx,
        use_native_select=True,
        timeout=_DEFAULT_TIMEOUT_MS,
    )


def _screen_5_click_upload_document_trigger(page: Page) -> None:
    """After choosing a file, Vahan often requires **Upload Document** (span with ui-button)."""
    for sel in (
        "span.ui-button:has-text('Upload Document')",
        "button:has-text('Upload Document')",
        "[id='formDocumentUpload:selectAndUploadFile'] span.ui-button",
        "span[role='button']:has-text('Upload')",
    ):
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
            loc.click(timeout=_FIRST_TRY_MS)
            _rto_log(f"click: Upload Document ({sel})")
            _pause()
            _wait_for_progress_close(page)
            return
        except Exception:
            continue
    try:
        page.get_by_role("button", name=re.compile(r"^\s*\+?\s*Upload\s+Documents?\s*$", re.I)).first.click(
            timeout=_LOOP_BUDGET_MS
        )
        _rto_log("click: Upload Documents (get_by_role)")
        _pause()
        _wait_for_progress_close(page)
    except Exception:
        _rto_log(
            "WARNING: Upload Document control not clicked — continuing "
            "(portal may submit on file input alone)"
        )


def _screen_5(page: Page, docs: dict[str, Path | None]) -> None:
    """Screen 5: Upload documents per sub-category (``formDocumentUpload`` form)."""
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

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _pause()

        assert sub_category_text is not None
        _screen_5_select_subcategory(page, doc_key, sub_category_text)
        _pause()

        _upload_file(
            page,
            file_path,
            file_input_selector=_SCREEN5_FILE_INPUT,
            wait_progress_after=False,
        )
        _screen_5_click_upload_document_trigger(page)

        try:
            nxt = page.locator(_SCREEN5_NEXT_BTN).first
            nxt.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            nxt.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
            nxt.click(timeout=_FIRST_TRY_MS)
            _rto_log(f"chevron next after upload: {doc_key} ({_SCREEN5_NEXT_BTN})")
            _pause()
            _wait_for_progress_close(page)
        except PwTimeout:
            logger.warning("fill_rto: nextBtn not found for %s", doc_key)
            _rto_log(f"WARNING: nextBtn not found for {doc_key}")

    _pause()
    _click(
        page,
        f"{_SCREEN5_FILE_MOVEMENT_BTN}, input[value*='File Movement'], "
        "button:has-text('File Movement'), a:has-text('File Movement')",
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
        "nominee_name": (row.get("nominee_name") or "").strip(),
        "nominee_relationship": (row.get("nominee_relationship") or "").strip(),
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
            elif skip_from == 4:
                if RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD:
                    s4_hint = "start at Dealer Document Upload tab only"
                elif RTO_FILL_SCREEN4_SKIP_VERIFY:
                    s4_hint = "Save-Options → File Movement → None+Save → Save → Dealer Document Upload"
                else:
                    s4_hint = "Verify → Save-Options → File Movement → None+Save → Save → Dealer Document Upload"
                extra = f" Screen 4: {s4_hint}."
            elif skip_from == 5:
                extra = (
                    " Screen 5: document upload — Sub Category **Form 20** first, "
                    "then FORM 21/22, insurance, invoice, Aadhaar, undertaking; File Movement at end."
                )
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
        screen = _current_screen.get() or "unknown"
        raw = str(e)
        first_line = raw.split("\n", 1)[0].strip()
        clean_msg = f"[{screen}] {first_line}"
        _rto_log(f"fill_rto_row FAILED: {clean_msg}")
        raise RuntimeError(clean_msg) from e
    finally:
        _current_screen.reset(screen_token)
        _rto_action_log.reset(token)
