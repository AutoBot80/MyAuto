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
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PwTimeout

from app.config import (
    ENVIRONMENT_IS_PRODUCTION,
    VAHAN_BASE_URL,
    VAHAN_DEALER_HOME_URL,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.services.handle_browser_opening import get_or_open_site_page
from app.services.hero_dms_shared_utilities import _ts_ist_iso
from app.services.utility_functions import normalize_nominee_relationship_value

logger = logging.getLogger(__name__)

# Integer progress on ``rto_queue.rto_status`` after successful Vahan scrapes.
RTO_STATUS_AFTER_SCREEN2 = 1  # Application No scraped (Inward / Generated Application dialog)
RTO_STATUS_AFTER_SCREEN3D = 2  # Save and File Movement scrape checkpoint
RTO_STATUS_AFTER_SCREEN5 = 3  # Document uploads complete (before payment / Screen 6)

_PENDING_APPLICATIONS_LABEL_RE = re.compile(r"Pending\s*Applications?", re.I)
_GET_PENDING_WORK_LABEL_RE = re.compile(r"Get\s*Pending\s*Work", re.I)
_RESUME_ROW_ACTION_PREFERRED_RE = re.compile(
    r"^(Entry|Verify|Edit|Dealer-Document-Upload|Dealer\s*Document\s*Upload)$",
    re.I,
)
_APPROVE_ACTION_RE = re.compile(r"^\s*Approve\s*$", re.I)
_VERIFY_ACTION_RE = re.compile(r"^\s*Verify\s*$", re.I)
_ENTRY_ACTION_RE = re.compile(r"^\s*Entry\s*$", re.I)
_DEALER_DOC_UPLOAD_ACTION_RE = re.compile(
    r"^\s*(?:Dealer-Document-Upload|Dealer\s*Document\s*Upload)\s*$",
    re.I,
)


def _resume_row_action_priority(rto_status: int | None) -> tuple[re.Pattern[str], ...]:
    """Action button preference on pending-work grid.

    Status 2 (post 3d): **Verify** when still on workbench; **Dealer Document Upload** when File
    Movement already completed and the grid action advanced.
    """
    status = int(rto_status or 0)
    if status == RTO_STATUS_AFTER_SCREEN3D:
        return (_VERIFY_ACTION_RE, _DEALER_DOC_UPLOAD_ACTION_RE, _ENTRY_ACTION_RE)
    if status == RTO_STATUS_AFTER_SCREEN2:
        return (_ENTRY_ACTION_RE, _VERIFY_ACTION_RE)
    return (_ENTRY_ACTION_RE, _VERIFY_ACTION_RE)


def _screen4_skip_verify_on_resume(*, use_resume_nav: bool, rto_status: int | None) -> bool:
    """True when **Verify** was already clicked on the pending grid (``rto_status=2`` resume)."""
    return use_resume_nav and int(rto_status or 0) == RTO_STATUS_AFTER_SCREEN3D


def _resolve_skip_from_rto_status(rto_status: int | None) -> int:
    """Map ``rto_queue.rto_status`` to the Vahan screen to start at (0 = full SOP)."""
    if rto_status is None:
        return 0
    try:
        status = int(rto_status)
    except (TypeError, ValueError):
        return 0
    if status <= 0:
        return 0
    if status == RTO_STATUS_AFTER_SCREEN2:
        return 3
    if status == RTO_STATUS_AFTER_SCREEN3D:
        return 4
    if status == RTO_STATUS_AFTER_SCREEN5:
        return 6
    return 0


def _screen3_resume_at_3b(*, use_resume_nav: bool, rto_status: int | None) -> bool:
    """True when DB resume should open pending work then start Screen 3 at 3b (not 3a)."""
    return use_resume_nav and int(rto_status or 0) == RTO_STATUS_AFTER_SCREEN2


# --- Testing / build: edit here only (not .env). Use 0 / False / "" for production full SOP. ---
# ``RTO_FILL_SKIP_TO_SCREEN``: 0 = run all screens; 1–6 = start at that screen (skips dealer-home reset and earlier).
RTO_FILL_SKIP_TO_SCREEN = 0
# Screen 3: skip **Home** (you are already on ``home.xhtml`` with the grid). Also on when SKIP is 3.
RTO_FILL_SCREEN3_SKIP_HOME = False
# Screen 3: skip **Entry** — only with ``skip_home``; go straight to **Vehicle Details** sub-tab (already past Entry on the form).
# On when SKIP is 3 (and ``RTO_FILL_SCREEN3_SKIP_HOME`` is implied for that path).
RTO_FILL_SCREEN3_SKIP_ENTRY = False
# Screen 4: skip Verify (already clicked) — jump straight to Save-Options.
RTO_FILL_SCREEN4_SKIP_VERIFY = False
# Screen 4: skip everything up to **Dealer Document Upload** (Save-Options, File Movement, popups) — start at that tab/button.
RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD = False
# Optional seed for ``data["rto_application_id"]`` when the queue row has no app id (logging / return merge only).
RTO_FILL_TEST_APPLICATION_ID = ""

# Screen 3c — run-only overrides (reset to defaults before production / next sale).
# Insurance From: when True, use yesterday (local date) instead of ``policy_from`` from DB.
RTO_FILL_INSURANCE_FROM_USE_YESTERDAY = False
# Exact Vahan dropdown label when DB ``insurer`` fuzzy-match picks the wrong company.
# Live portal option (Bagaj): ``Bajaj General Insurance Co. Ltd.``
RTO_FILL_INSURER_PORTAL_LABEL = "Bajaj General Insurance Co. Ltd."
# Re-pick Insurance Company when the visible label does not match the portal label / DB insurer.
RTO_FILL_FORCE_INSURER_RESELECT = True
# When set (not None), overrides ``insurance_master.idv`` for this run.
RTO_FILL_IDV_OVERRIDE: int | None = None
# Execution log verbosity — ``False`` (default): compact trace (~25–40 lines on success;
# failure dumps ~10–15 lines). ``True``: full tab probes, scroll confirmations, page-state dumps.
RTO_FILL_LOG_VERBOSE = False
# Screen 3: at most **one** compact page dump per run (first failure); skips repeat dumps on 3c/3d.
RTO_FILL_SCREEN3_ONE_DUMP = True
# Screen 3c insurance/nominee field inventory → companion ``*_3c_inventory.txt`` (off — dump already captured).
RTO_FILL_SCREEN3_INSURANCE_FIELD_DUMP = False

# Screen 5 — dealer document upload form (``formDocumentUpload:*`` in RTO dumps, e.g. ``9650693610_RTO.txt``):
# Sub Category PF wrapper + native ``select`` … ``subCatgId_input``; file input ``selectAndUploadFile_input``;
# **Upload Document** span; right chevron ``nextBtn``; **File Movement** ``fileFlowId``.
_SCREEN5_PF_SUBCAT_WRAPPER = '[id="formDocumentUpload:subCatgId"]'
_SCREEN5_FILE_INPUT = '[id="formDocumentUpload:selectAndUploadFile_input"]'
_SCREEN5_NEXT_BTN = '[id="formDocumentUpload:nextBtn"]'
_SCREEN5_FILE_MOVEMENT_BTN = '[id="formDocumentUpload:fileFlowId"]'
# Pause after each document-upload chevron so the next row renders (portal carousel).
_SCREEN5_NEXT_CHEVRON_SETTLE_MS = 300
# Pause after **Upload Document** so the portal commits the file before the next chevron.
_SCREEN5_POST_UPLOAD_SETTLE_MS = 300
# After navigation to Documents Upload, wait for carousel before Sub Category (portal ~1s render).
_SCREEN5_UPLOAD_FORM_SETTLE_MS = 1000
_SCREEN5_DOCUMENT_COUNTER_RE = re.compile(r"Document\s*\(\s*\d+", re.I)
# When ``RTO_FILL_SKIP_TO_SCREEN >= 5`` and this is **False**, Screen 5 begins with this many **next** chevrons (align before Form 20). Set **0** when skipping straight to Owner Undertaking.
RTO_FILL_SCREEN5_START_WITH_NEXT_N = 0
# When ``RTO_FILL_SKIP_TO_SCREEN >= 5`` and **True**, Screen 5 runs **only** Owner Undertaking: Sub Category → **Owner Undertaking Form** → file → Upload Document → next → File Movement (skips Form 20…Aadhaar). Ignored if SKIP_TO_FILE_MOVEMENT_ONLY.
RTO_FILL_SCREEN5_SKIP_TO_OWNER_UNDERTAKING_ONLY = False
# When ``RTO_FILL_SKIP_TO_SCREEN >= 5`` and **True**, Screen 5 **only** scrolls and clicks **File Movement** (``formDocumentUpload:fileFlowId``) + dialogs — no uploads. Takes precedence over OWNER_UNDERTAKING_ONLY.
RTO_FILL_SCREEN5_SKIP_TO_FILE_MOVEMENT_ONLY = False
# Legacy: unused by title-driven carousel (Owner Undertaking is found by Document title).
RTO_FILL_SCREEN5_NEXT_BEFORE_OWNER_UNDERTAKING = 0
# Native / overlay ``<option>`` text varies (``Form 20`` vs ``FORM 20``) — match with regex per queue key.
_SCREEN5_SUBCAT_REGEX_BY_DOC_KEY: dict[str, re.Pattern] = {
    "FORM 20": re.compile(r"Form\s*20\b", re.I),
    "FORM 21": re.compile(r"Form\s*21\b|Sale\s*Certificate", re.I),
    "FORM 22": re.compile(r"Form\s*22\b", re.I),
    "INSURANCE CERTIFICATE": re.compile(r"INSURANCE\s*CERTIFICATE|Insurance\s*Certificate", re.I),
    "INVOICE ORIGINAL": re.compile(r"INVOICE\s*ORIGINAL|Invoice\s*Original|GST\s*Retail", re.I),
    "AADHAAR_FRONT": re.compile(r"AADHAAR\s*CARD|Aadhaar", re.I),
    "AADHAAR_BACK": re.compile(r"AADHAAR\s*CARD|Aadhaar", re.I),
    # Overlay list shows **Owner Undertaking Form** (also **Owners Signature**) — match full label, not bare "Undertaking".
    "OWNER UNDERTAKING FORM": re.compile(
        r"Owner\s+Undertaking\s+Form|OWNER\s+UNDERTAKING\s+FORM",
        re.I,
    ),
}
# Portal Document field title → doc_key (most-specific first). None = portal-only skip slot.
# Special keys: ``AADHAAR`` → front then back; ``AADHAAR_BACK`` → back only (Proof of address).
_SCREEN5_PORTAL_TITLE_TO_DOC_KEY: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"Proof\s*of\s*[Aa]ddress|Proof\s*of\s*[Aa]d[d]?haar", re.I), "AADHAAR_BACK"),
    (re.compile(r"Owner\s+Undertaking|OWNER\s+UNDERTAKING", re.I), "OWNER UNDERTAKING FORM"),
    (re.compile(r"INSURANCE\s*CERTIFICATE|Insurance\s*Certificate", re.I), "INSURANCE CERTIFICATE"),
    (re.compile(r"INVOICE\s*ORIGINAL|Invoice\s*Original|GST\s*Retail", re.I), "INVOICE ORIGINAL"),
    (re.compile(r"Form\s*20\b|FORM\s*20\b", re.I), "FORM 20"),
    (re.compile(r"Form\s*21\b|FORM\s*21\b|Sale\s*Certificate", re.I), "FORM 21"),
    (re.compile(r"Form\s*22\b|FORM\s*22\b", re.I), "FORM 22"),
    (re.compile(r"AADHAAR\s*CARD|Aadhaar\s*Card", re.I), "AADHAAR"),
    (re.compile(r"Affidavit|Parking|AFFADEVIT", re.I), None),
]
# Short filter strings for Sub Category overlay search box.
_SCREEN5_SUBCAT_FILTER_HINT: dict[str, str] = {
    "FORM 20": "Form 20",
    "FORM 21": "Form 21",
    "FORM 22": "Form 22",
    "INSURANCE CERTIFICATE": "Insurance",
    "INVOICE ORIGINAL": "Invoice",
    "AADHAAR_FRONT": "Aadhaar",
    "AADHAAR_BACK": "address",
    "OWNER UNDERTAKING FORM": "Undertaking",
}
# Vahan document upload size limit (~400 KB); target slightly under for headroom.
VAHAN_UPLOAD_MAX_BYTES = 384 * 1024
_SCREEN5_DOCUMENT_OF_TOTAL_RE = re.compile(
    r"Document\s*\(\s*(\d+)\s*of\s*(\d+)\s*\)", re.I
)

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
# Live Vahan ids (9650693610 RTO log): ``ins_type`` / ``ins_cd`` — legacy names kept as fallback.
_SCREEN3_INSURANCE_TYPE_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:ins_type"]',
    '[id="workbench_tabview:insurance_type"]',
    '[id="workbench_tabview:insuranceType"]',
)
_SCREEN3_INSURANCE_TYPE_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:ins_type_input"]',
    'select[id="workbench_tabview:insurance_type_input"]',
    'select[id="workbench_tabview:insuranceType_input"]',
    "select[id*='insuranceType'], select[name*='insuranceType']",
)
_SCREEN3_INSURANCE_COMPANY_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:ins_cd"]',
    '[id="workbench_tabview:insurance_company"]',
    '[id="workbench_tabview:insuranceCompany"]',
)
_SCREEN3_INSURANCE_COMPANY_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:ins_cd_input"]',
    'select[id="workbench_tabview:insurance_company_input"]',
    'select[id="workbench_tabview:insuranceCompany_input"]',
    "select[id*='insuranceCompany'], select[name*='insuranceCompany']",
)
_SCREEN3_INSURANCE_COMPANY_WRAPPER_ID = "workbench_tabview:ins_cd"
_INSURANCE_PERIOD_YEARS = 5
_INSURANCE_PERIOD_LABEL = "5 Year"
_POLICY_NO_BLANK_ALERT_RE = re.compile(r"Policy\s*No\s*Can'?t\s*be\s*Blank", re.I)
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
_SCREEN3_INSURANCE_UPTO_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:ins_upto_input"]',
    '[id="workbench_tabview:ins_to_input"]',
    '[id="workbench_tabview:insurance_to"]',
    '[id="workbench_tabview:insuranceTo"]',
    '[id="workbench_tabview:insurance_upto"]',
    "input[id*='ins_upto'], input[id*='insUpto'], input[id*='insuranceTo'], input[name*='insuranceTo']",
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
_SCREEN3_NOMINATION_DATE_INPUT: tuple[str, ...] = (
    '[id="workbench_tabview:nominationdate1_input"]',
    '[id="workbench_tabview:nominationDate1_input"]',
    "input[id*='nominationdate'], input[id*='nominationDate']",
)
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
_SCREEN2_CORR_DISTRICT_PF_WRAPPERS: tuple[str, ...] = (
    '[id="workbench_tabview:tf_c_district"]',
)
_SCREEN2_CORR_DISTRICT_NATIVE: tuple[str, ...] = (
    'select[id="workbench_tabview:tf_c_district_input"]',
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
_screen3_dump_captured: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "rto_screen3_dump_captured", default=False
)


# India Standard Time (no DST) — fixed offset avoids Windows ``tzdata`` dependency for ``zoneinfo``.
_IST = timezone(timedelta(hours=5, minutes=30))


class RtoActionLog:
    """Per-run action log under ``ocr_output/{dealer_id}/[{subfolder}/]{mobile}_RTO_{stamp}.txt``.

    *subfolder* is taken from sales ``file_location`` when present (see ``_rto_action_log_path``).
    Each ``fill_rto_row`` run creates a **new** timestamped file (same pattern as ``Playwright_DMS_*.txt``).
    Trace lines use ISO IST timestamps (``+05:30``), matching Fill DMS / Print RTO queue logs.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.inventory_path = path.with_name(f"{path.stem}_3c_inventory.txt")
        self._started = False
        self._inventory_started = False

    def write_run_header(self, header_lines: list[str]) -> None:
        """Write the run preamble once (``started_ist``, queue ids, etc.) before trace lines."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                f.write(
                    "Vahan RTO Fill — execution log (this run only; IST / Asia/Kolkata timestamps)\n\n"
                )
                for line in header_lines:
                    f.write(f"{line}\n")
                f.write("\n--- trace ---\n\n")
            self._started = True
        except OSError as e:
            logger.warning("fill_rto: RTO log header write failed %s: %s", self.path, e)

    def line(self, message: str) -> None:
        ts = _ts_ist_iso()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self._started:
                self.write_run_header([f"started_ist={ts}"])
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"{ts} {message}\n")
        except OSError as e:
            logger.warning("fill_rto: RTO log write failed %s: %s", self.path, e)

    def inventory_line(self, message: str) -> None:
        """Append to the companion Screen 3c field-inventory file (same folder as ``self.path``)."""
        ts = _ts_ist_iso()
        try:
            self.inventory_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._inventory_started:
                with self.inventory_path.open("w", encoding="utf-8") as f:
                    f.write(
                        "Vahan RTO Fill — Screen 3c insurance field inventory "
                        "(companion to main RTO log; IST timestamps)\n\n"
                    )
                    f.write(f"main_log={self.path.name}\n")
                    f.write(f"started_ist={ts}\n\n--- inventory ---\n\n")
                self._inventory_started = True
            with self.inventory_path.open("a", encoding="utf-8") as f:
                f.write(f"{ts} {message}\n")
        except OSError as e:
            logger.warning(
                "fill_rto: RTO inventory log write failed %s: %s", self.inventory_path, e
            )


def _set_screen(label: str) -> None:
    """Set the current screen label that prefixes all subsequent log lines."""
    _current_screen.set(label)
    if label == "Screen 3":
        _screen3_dump_captured.set(False)


def _rto_log(msg: str) -> None:
    log = _rto_action_log.get()
    if log is not None:
        screen = _current_screen.get()
        prefix = f"[{screen}] " if screen else ""
        log.line(f"{prefix}{msg}")


def _rto_inventory_log(msg: str) -> None:
    """Write to the companion ``*_3c_inventory.txt`` file (not the main RTO trace)."""
    log = _rto_action_log.get()
    if log is not None:
        screen = _current_screen.get()
        prefix = f"[{screen}] " if screen else ""
        log.inventory_line(f"{prefix}{msg}")


def _rto_log_verbose(msg: str) -> None:
    """Trace line only when ``RTO_FILL_LOG_VERBOSE`` is True."""
    if RTO_FILL_LOG_VERBOSE:
        _rto_log(msg)


def _rto_log_path_display(path: Path, *, dealer_id: int | None = None) -> str:
    """Prefer ``ocr_output/…``-relative paths in RTO logs (avoid repeating long absolutes)."""
    try:
        resolved = path.resolve()
        if dealer_id is not None:
            base = get_ocr_output_dir(dealer_id).resolve()
            try:
                return str(resolved.relative_to(base))
            except ValueError:
                pass
        parts = resolved.parts
        if "ocr_output" in parts:
            idx = parts.index("ocr_output")
            return str(Path(*parts[idx + 1 :]))
        return str(resolved)
    except Exception:
        return str(path)


def _mobile_digits_for_filename(mobile: str | None) -> str:
    d = re.sub(r"\D", "", str(mobile or ""))
    if len(d) >= 10:
        return d[-10:]
    if d:
        return d.zfill(10)[:10]
    return "unknown_mobile"


def _rto_execution_log_filename(mob_fn: str) -> str:
    """Per-run trace file: ``{mobile}_RTO_{ddmmyyyy}_{hhmmss}.txt`` (IST), like ``Playwright_DMS_*.txt``."""
    stamp = datetime.now(_IST).strftime("%d%m%Y_%H%M%S")
    return f"{mob_fn}_RTO_{stamp}.txt"


def _rto_action_log_path(dealer_id: int, row: dict, mob_fn: str) -> Path:
    """``ocr_output/{dealer_id}/[{subfolder}/]{mobile}_RTO_{ddmmyyyy}_{hhmmss}.txt``.

    *subfolder* is ``row['subfolder']`` from the RTO batch query
    (``COALESCE(sm.file_location, cm.file_location)``). Matches the per-sale folder under uploads
    (e.g. ``{mobile}_{ddmmyy}``). If missing or path cannot be resolved safely, falls back to
    ``ocr_output/{dealer_id}/{mobile}_RTO_{stamp}.txt``.
    """
    log_name = _rto_execution_log_filename(mob_fn)
    base = get_ocr_output_dir(dealer_id)
    default = base / log_name
    raw = (row.get("subfolder") or "").strip()
    if not raw:
        return default

    p = Path(raw.replace("\\", "/"))
    uploads = get_uploads_dir(dealer_id)
    rel: Path | None = None
    try:
        if p.is_absolute():
            rp = p.resolve()
            for anchor in (uploads, base):
                try:
                    rel = rp.relative_to(anchor.resolve())
                    break
                except ValueError:
                    continue
            if rel is None:
                rel = Path(p.name)
        else:
            rel = p
    except OSError:
        logger.warning("fill_rto: could not resolve file_location for RTO log, using default path")
        return default

    parts = [x for x in rel.parts if x and x not in (".", "..")]
    if not parts:
        return default
    return base.joinpath(*parts) / log_name


# Fast UI timing: **200ms** per attempt; **2s** total budget when looping (retries / polls).
_FIRST_TRY_MS = 200
_LOOP_BUDGET_MS = 2_000
# Playwright locator/action waits (ms) — 2s default (matches loop budget).
_DEFAULT_TIMEOUT_MS = _LOOP_BUDGET_MS
_LONG_TIMEOUT_MS = 10_000
# Delay after each discrete UI action (s) — not a Playwright timeout.
_ACTION_WAIT_S = 0.2
# Screen 3c/3d: shorter settle (many micro-steps on Hypothecation/Insurance tab).
_SCREEN3_ACTION_WAIT_S = 0.05
_SCREEN3_LOOP_BUDGET_MS = 700
_SCREEN3_ACTION_TIMEOUT_MS = 450
_SCREEN3_PERIOD_DIALOG_POLL_S = 1.5
_SCREEN3_INS_UPTO_ENABLE_BUDGET_MS = 1200
_SCREEN3_ENTRY_DETAILS_WAIT_S = 0.5
# Insurance Company PF overlay is slow/intermittent at 450ms — keep 2s for this field only.
_SCREEN3_INSURANCE_COMPANY_TIMEOUT_MS = 2_000
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


def _vahan_dealer_home_ready(page: Page) -> bool:
    """True when the attached Vahan tab is past login and Screen 1 (``officeList``) is present."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    for pat in _VAHAN_SESSION_DEAD_PATTERNS:
        if pat in url:
            return False
    try:
        page.locator("div#officeList").first.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
        return True
    except Exception:
        return False


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


def _parse_vahan_date(d: date | datetime | str | None) -> date | None:
    """Parse queue / workbench dates into ``date`` for insurance upto math."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _insurance_upto_from_from_date(from_d: date, *, years: int = _INSURANCE_PERIOD_YEARS) -> str:
    """Vahan **Insurance upto**: from date + ``years`` − 1 day (default 5-year cover)."""
    try:
        anniversary = from_d.replace(year=from_d.year + years)
    except ValueError:
        anniversary = from_d.replace(year=from_d.year + years, day=28)
    return _fmt_date(anniversary - timedelta(days=1))


def _resolve_policy_upto_str(data: dict) -> str:
    """Best policy end date for Screen 3c — DB ``policy_to`` or computed from ``policy_from``."""
    explicit = (data.get("policy_to_str") or "").strip()
    if explicit:
        return explicit
    from_d = _parse_vahan_date(data.get("policy_from_str"))
    if from_d is None:
        from_d = _parse_vahan_date(data.get("policy_from"))
    if from_d is None:
        return ""
    return _insurance_upto_from_from_date(from_d)


def _normalize_idv_for_vahan(value: object) -> str:
    """Vahan IDV must be a whole number string (no decimals or grouping commas)."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, Decimal):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value))
    s = str(value).strip().replace(",", "")
    if not s:
        return ""
    if "." in s:
        whole, frac = s.split(".", 1)
        if frac.strip("0") == "":
            return whole.lstrip("0") or "0"
    try:
        return str(int(float(s)))
    except ValueError:
        digits = re.sub(r"\D", "", s)
        return digits.lstrip("0") or ("0" if digits else "")


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


def _district_from_dealer_rto(raw_rto_name: str) -> str:
    """``RTO-Bharatpur`` / ``BHARATPUR RTO`` → ``Bharatpur`` for the Vahan district dropdown."""
    raw = (raw_rto_name or "").strip()
    if not raw:
        return ""
    if len(raw) > 4 and raw[:4].upper() == "RTO-":
        return _init_cap_place_name(raw[4:].strip())
    if raw.upper().endswith(" RTO"):
        return _init_cap_place_name(raw[:-4].strip())
    return ""


def _resolve_vahan_district(data: dict) -> str:
    """District for Vahan address fields — use explicit district or dealer RTO, not village/city."""
    explicit = _init_cap_place_name((data.get("district") or "").strip())
    if explicit:
        return explicit
    from_rto = _district_from_dealer_rto((data.get("dealer_rto") or "").strip())
    if from_rto:
        return from_rto
    return ""


# ---------------------------------------------------------------------------
# Document resolution
# ---------------------------------------------------------------------------
#
# Typical per-sale folder (``uploads/{dealer_id}/{mobile}_{ddmmyyyy}/``) — optional ``{mobile}_`` prefix
# on PDFs; Aadhaar often unprefixed. Example layout::
#
#   ``{mobile}_Form_20`` / ``_Form_20`` / ``Form 20``  → FORM 20
#   ``{mobile}_Sale_Certificate``  → FORM 21
#   ``{mobile}_Form22`` / ``_Form_22``  → FORM 22
#   ``{mobile}_GST_Retail_Invoice``  → INVOICE ORIGINAL
#   ``{mobile}_Insurance_{ddmmyyyy}.pdf`` (``ddmmyyyy`` = same 8 digits as folder ``{mobile}_{ddmmyyyy}``)
#   → INSURANCE CERTIFICATE; legacy ``*Insurance*.pdf`` names if absent
#   ``Aadhar_front`` (use pattern ``*aadhaar_front*``)  → AADHAAR FRONT; ``Aadhar_back``  → AADHAAR BACK
#   ``Sales Detail Sheet`` / ``*Detail Sheet*``  → OWNER UNDERTAKING
#
# Matching: each pattern has ``*`` stripped, then the remainder must appear as a **single substring** of
# the lowercased filename (e.g. ``*Aadhar*front*`` → ``aadharfront`` does **not** match ``aadhar_front``).
# Use ``*aadhar_front*`` for underscore-separated names. ``pencil_mark`` and similar are intentionally
# not mapped to an upload category.

_DOC_PATTERNS: list[tuple[str, list[str], list[str]]] = [
    ("FORM 20", ["*Form_20*", "*Form 20*", "*FORM_20*", "*FORM 20*"], [".pdf"]),
    ("FORM 21", ["*Sale_Certificate*", "*Sale Certificate*", "*Form_21*", "*Form 21*", "*FORM_21*"], [".pdf"]),
    # e.g. ``{mobile}_Form22.pdf`` (no underscore before 22) or ``Form_22``
    ("FORM 22", ["*Form_22*", "*Form22*", "*FORM22*", "*Form 22*", "*FORM_22*", "*FORM 22*"], [".pdf"]),
    # Canonical file is resolved separately (``{mobile}_Insurance_{ddmmyyyy}.pdf``); keep patterns for fallback.
    (
        "INSURANCE CERTIFICATE",
        [
            "*Insurance_Certificate*",
            "*Certificate_Of_Insurance*",
            "*_Insurance*",
            "*Insurance*",
        ],
        [".pdf"],
    ),
    (
        "INVOICE ORIGINAL",
        [
            "*GST_Retail_Invoice*",
            "*GST*Invoice*",
            "*Tax_Invoice*",
            "*Tax Invoice*",
            "*Retail_Invoice*",
        ],
        [".pdf"],
    ),
    # Spelled ``Aadhar`` or ``Aadhaar``; do not add a bare ``*back*`` subpattern. FRONT skips ``*_back*``.
    # Substring = pattern with all ``*`` removed (e.g. ``*aadhaar_consolidated*`` → ``aadhaar_consolidated``). Prefer
    # ``*aadhar_front*`` before ``*aadhar*`` (broad) so ``Aadhar_back`` is not the front file if order changes.
    (
        "AADHAAR_FRONT",
        [
            "*aadhar_front*",
            "*aadhaar_front*",
            "*adhaar_front*",
            "*aadhaar_consolidated*",
            "*Aadhaar_Card*",
            "*Aadhaar*",
            "*aadhar*",
        ],
        [".jpg", ".jpeg", ".png"],
    ),
    # ``*Aadhar*back*`` → ``aadharback``; use ``*Aadhar_back*`` (→ ``aadhar_back``) for ``Aadhar_back.jpg``
    (
        "AADHAAR_BACK",
        ["*Aadhar_back*", "*aadhaar_back*", "*adhaar_back*"],
        [".jpg", ".jpeg", ".png"],
    ),
    # Most specific first (``iterdir`` order is arbitrary; a lone ``*Detail*`` can false-positive in other names).
    (
        "OWNER UNDERTAKING FORM",
        [
            "*Sales Detail Sheet*",
            "*Detail Sheet*",
            "*Sales_Detail_Sheet*",
            "*Sales_Detail*",
            "*Sales Detail*",
            "*Details*",
            "*Detail*",
        ],
        [".pdf", ".jpg", ".jpeg", ".png"],
    ),
]


def _ddmmyyyy_suffix_from_sale_subfolder(subfolder: str, mob_fn: str) -> str | None:
    """If subfolder leaf is ``{mob_fn}_{ddmmyy}`` or ``{mob_fn}_{ddmmyyyy}``, return the date segment."""
    leaf = Path(str(subfolder).strip().replace("\\", "/")).name
    prefix = f"{mob_fn}_"
    if leaf.startswith(prefix):
        rest = leaf[len(prefix) :]
        if re.fullmatch(r"\d{6,8}", rest):
            return rest
    m = re.match(r"^\d{10}_(\d{6,8})$", leaf)
    return m.group(1) if m else None


def _rto_sale_subfolder_leaf(subfolder: str) -> str:
    """One path segment for uploads/OCR sale folder (handles full ``file_location`` paths)."""
    from app.services.fill_hero_dms_service import _ocr_sale_folder_leaf

    return _ocr_sale_folder_leaf(subfolder)


def resolve_rto_sale_dir(dealer_id: int, subfolder: str) -> Path:
    """``uploads/{dealer_id}/{leaf}/`` for RTO document resolution and operator uploads."""
    leaf = _rto_sale_subfolder_leaf(subfolder)
    return get_uploads_dir(int(dealer_id)) / leaf


def _insurance_certificate_dest_path(
    sale_dir: Path,
    subfolder: str,
    mob_fn: str,
    *,
    original_filename: str = "",
) -> Path:
    """Target path when placing an insurance PDF upload."""
    suf = _ddmmyyyy_suffix_from_sale_subfolder(subfolder, mob_fn)
    if suf:
        return sale_dir / f"{mob_fn}_Insurance_{suf}.pdf"
    name = Path((original_filename or "").strip()).name
    if name.lower().endswith(".pdf"):
        return sale_dir / name
    return sale_dir / f"{mob_fn}_Insurance.pdf"


def _canonical_insurance_certificate_pdf(sale_dir: Path, subfolder: str, mob_fn: str) -> Path | None:
    """``{mob_fn}_Insurance_{ddmmyyyy}.pdf`` with ``ddmmyyyy`` aligned to the sale subfolder name."""
    suf = _ddmmyyyy_suffix_from_sale_subfolder(subfolder, mob_fn)
    if not suf:
        return None
    p = sale_dir / f"{mob_fn}_Insurance_{suf}.pdf"
    return p if p.is_file() else None


def _canonical_cpa_certificate_pdf(sale_dir: Path, subfolder: str, mob_fn: str) -> Path | None:
    """``{mob_fn}_CPA_{ddmmyyyy}.pdf`` — same date suffix convention as insurance."""
    suf = _ddmmyyyy_suffix_from_sale_subfolder(subfolder, mob_fn)
    if not suf:
        return None
    p = sale_dir / f"{mob_fn}_CPA_{suf}.pdf"
    return p if p.is_file() else None


def _resolve_cpa_certificate(
    sale_dir: Path,
    all_files: list[Path],
    subfolder: str,
    mob_fn: str,
) -> Path | None:
    canon = _canonical_cpa_certificate_pdf(sale_dir, subfolder, mob_fn)
    if canon is not None:
        return canon
    for f in all_files:
        if not f.is_file():
            continue
        name_lower = f.name.lower()
        if "cpa" in name_lower and f.suffix.lower() == ".pdf":
            return f
    return None


def _mobile_fn_from_mobile(mobile: str) -> str:
    dig = re.sub(r"\D", "", str(mobile or ""))
    if len(dig) >= 10:
        return dig[-10:]
    if dig:
        return dig.zfill(10)[:10]
    return "0000000000"


def _resolve_insurance_certificate(
    sale_dir: Path,
    all_files: list[Path],
    subfolder: str,
    mob_fn: str,
) -> Path | None:
    canon = _canonical_insurance_certificate_pdf(sale_dir, subfolder, mob_fn)
    if canon is not None:
        return canon
    ins_entry = next(x for x in _DOC_PATTERNS if x[0] == "INSURANCE CERTIFICATE")
    patterns, extensions = ins_entry[1], ins_entry[2]
    for pat in patterns:
        for f in all_files:
            if not f.is_file():
                continue
            name_lower = f.name.lower()
            pat_lower = pat.lower().replace("*", "")
            parts = [p for p in pat_lower.split("*") if p] if "*" in pat_lower else [pat_lower]
            if all(p in name_lower for p in parts) and f.suffix.lower() in extensions:
                return f
    return None


def _resolve_sale_documents(
    sale_dir: Path,
    *,
    subfolder: str = "",
    mob_fn: str = "",
) -> dict[str, Path | None]:
    """Map Vahan sub-category names to files found in the sale directory."""
    result: dict[str, Path | None] = {}
    if not sale_dir.is_dir():
        logger.warning("fill_rto: sale directory not found: %s", sale_dir)
        for cat, _, _ in _DOC_PATTERNS:
            result[cat] = None
        return result

    all_files = list(sale_dir.iterdir())
    for cat, patterns, extensions in _DOC_PATTERNS:
        if cat == "INSURANCE CERTIFICATE":
            continue
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

    result["INSURANCE CERTIFICATE"] = _resolve_insurance_certificate(
        sale_dir, all_files, subfolder, mob_fn
    )
    from app.services.form20_pencil_overlay import find_form20_pdf, find_form20_with_cover_pdf

    merged = find_form20_with_cover_pdf(sale_dir, mob_fn)
    result["FORM 20"] = merged if merged is not None else find_form20_pdf(sale_dir, mob_fn)
    return result


VAHAN_DOC_CATEGORY_LABELS: dict[str, str] = {
    "FORM 20": "Form 20 with cover (merged PDF)",
    "FORM 21": "Sale Certificate / Form 21",
    "FORM 22": "Form 22",
    "INSURANCE CERTIFICATE": "Insurance certificate",
    "INVOICE ORIGINAL": "GST / retail invoice",
    "AADHAAR_FRONT": "Aadhaar front",
    "AADHAAR_BACK": "Aadhaar back",
    "OWNER UNDERTAKING FORM": "Owner undertaking (Sales Detail Sheet)",
}

VAHAN_UPLOAD_READINESS_KEYS: tuple[str, ...] = (
    "FORM 21",
    "FORM 22",
    "INSURANCE CERTIFICATE",
    "INVOICE ORIGINAL",
    "AADHAAR_FRONT",
    "AADHAAR_BACK",
    "OWNER UNDERTAKING FORM",
)

RTO_QUEUE_UPLOAD_FILENAMES: dict[str, str] = {
    "FORM 20": "Form_20_Cover_Page.jpg",
    "AADHAAR_FRONT": "Aadhar_front.jpg",
    "AADHAAR_BACK": "Aadhar_back.jpg",
    "OWNER UNDERTAKING FORM": "Sales_Detail_Sheet.pdf",
}


def resolve_mob_fn_for_row(*, mobile: str, subfolder: str) -> str:
    mob_fn = _mobile_fn_from_mobile(mobile)
    if mob_fn and mob_fn != "0000000000":
        return mob_fn
    leaf = _rto_sale_subfolder_leaf(subfolder)
    m = re.match(r"^(\d{10})", leaf)
    return m.group(1) if m else mob_fn


def resolve_vahan_upload_readiness(
    sale_dir: Path,
    *,
    subfolder: str,
    mobile: str,
    try_build_form20: bool = False,
) -> tuple[bool, list[str]]:
    """Return (ready, missing_labels) for Screen 5 Vahan document uploads."""
    if not sale_dir.is_dir():
        return False, ["Sale folder not found on disk"]

    mob_fn = resolve_mob_fn_for_row(mobile=mobile, subfolder=subfolder)
    missing: list[str] = []

    from app.services.form20_pencil_overlay import build_form20_with_cover_pdf, is_form20_with_cover_ready

    if try_build_form20:
        try:
            build_form20_with_cover_pdf(sale_dir, mob_fn)
        except Exception as exc:
            logger.warning("fill_rto: Form 20 merge skipped: %s", exc)
    if not is_form20_with_cover_ready(sale_dir, mob_fn):
        missing.append(VAHAN_DOC_CATEGORY_LABELS["FORM 20"])

    doc_map = _resolve_sale_documents(sale_dir, subfolder=subfolder, mob_fn=mob_fn)
    for key in VAHAN_UPLOAD_READINESS_KEYS:
        if doc_map.get(key) is None:
            missing.append(VAHAN_DOC_CATEGORY_LABELS[key])

    return len(missing) == 0, missing


def place_rto_queue_category_upload(
    sale_dir: Path,
    category_key: str,
    source_path: Path,
    *,
    subfolder: str,
    mobile: str,
    original_filename: str = "",
) -> Path:
    """Copy one operator upload into the sale folder with a canonical name when known."""
    import shutil

    from app.services.form20_pencil_overlay import form20_with_cover_pdf_path
    from app.services.hero_dms_playwright_invoice import _mobile_report_pdf_filename

    if not source_path.is_file():
        raise FileNotFoundError(f"Upload source not found: {source_path}")

    sale_dir.mkdir(parents=True, exist_ok=True)
    key = (category_key or "").strip().upper()
    mob_fn = resolve_mob_fn_for_row(mobile=mobile, subfolder=subfolder)
    orig_name = (original_filename or source_path.name or "").strip()
    orig_low = orig_name.lower()

    if key == "FORM 20":
        if source_path.suffix.lower() == ".pdf":
            if "with_cover" in orig_low.replace("-", "_"):
                dest = form20_with_cover_pdf_path(sale_dir, mob_fn)
            elif "form" in orig_low and "20" in orig_low:
                dest = sale_dir / _mobile_report_pdf_filename(mob_fn, "Form 20")
            else:
                dest = form20_with_cover_pdf_path(sale_dir, mob_fn)
        else:
            dest = sale_dir / RTO_QUEUE_UPLOAD_FILENAMES["FORM 20"]
        shutil.copy2(source_path, dest)
        return dest

    fixed = RTO_QUEUE_UPLOAD_FILENAMES.get(key)
    if fixed:
        dest = sale_dir / fixed
        if dest.suffix.lower() != source_path.suffix.lower() and key == "OWNER UNDERTAKING FORM":
            if source_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                dest = sale_dir / "Sales_Detail_Sheet.jpg"
        shutil.copy2(source_path, dest)
        return dest

    pdf_names = {
        "FORM 21": _mobile_report_pdf_filename(mob_fn, "Sale Certificate"),
        "FORM 22": _mobile_report_pdf_filename(mob_fn, "Form 22"),
    }
    if key in pdf_names:
        dest = sale_dir / pdf_names[key]
        shutil.copy2(source_path, dest)
        return dest

    if key == "INSURANCE CERTIFICATE":
        dest = _insurance_certificate_dest_path(
            sale_dir, subfolder, mob_fn, original_filename=orig_name
        )
        shutil.copy2(source_path, dest)
        return dest

    if key == "INVOICE ORIGINAL":
        dest = sale_dir / (Path(orig_name).name if orig_name else source_path.name)
        if "gst" not in dest.name.lower() and "invoice" not in dest.name.lower():
            dest = sale_dir / f"{mob_fn}_GST_Retail_Invoice.pdf"
        shutil.copy2(source_path, dest)
        return dest

    dest = sale_dir / (Path(orig_name).name if orig_name else source_path.name)
    shutil.copy2(source_path, dest)
    return dest


def resolve_rto_print_bundle_pdfs(
    sale_dir: Path,
    *,
    subfolder: str,
    mobile: str,
) -> tuple[Path | None, Path | None]:
    """
    Paths for **Print forms & queue RTO** (Sale Certificate, then Insurance — Gate Pass is separate).

    Uses the same rules as Vahan document resolution: **FORM 21** (*Sale Certificate*, …) and
    ``{mobile}_Insurance_{ddmmyyyy}.pdf`` aligned to the sale folder name, with legacy *Insurance*
    fallbacks (see module docstring above ``_DOC_PATTERNS``).

    Returns ``(sale_certificate_pdf, insurance_pdf)``.
    """
    mob_fn = _mobile_fn_from_mobile(mobile)
    doc_map = _resolve_sale_documents(sale_dir, subfolder=subfolder, mob_fn=mob_fn)
    return doc_map.get("FORM 21"), doc_map.get("INSURANCE CERTIFICATE")


def resolve_cpa_certificate_pdf(
    sale_dir: Path,
    *,
    subfolder: str,
    mobile: str,
) -> Path | None:
    """Optional CPA certificate for Print / Queue RTO (canonical name, then ``*CPA*.pdf`` fallback)."""
    if not sale_dir.is_dir():
        return None
    mob_fn = _mobile_fn_from_mobile(mobile)
    return _resolve_cpa_certificate(sale_dir, list(sale_dir.iterdir()), subfolder, mob_fn)


def resolve_print_rto_push_upload_paths(
    sale_dir: Path,
    *,
    subfolder: str,
    mobile: str,
) -> list[Path]:
    """
    Local **uploads** files to push after Print / Queue RTO (not scans, OCR JSON, or gate pass).

    Uses the on-disk Form 20 PDF after dealer signature / pencil overlay (canonical ``{mobile}_Form_20.pdf``).

    Order: Form 20, Form 22, Sale Certificate (Form 21), GST Retail Invoice, Insurance, optional CPA.
    """
    if not sale_dir.is_dir():
        return []

    mob_fn = _mobile_fn_from_mobile(mobile)
    sub = (subfolder or sale_dir.name).strip()
    doc_map = _resolve_sale_documents(sale_dir, subfolder=sub, mob_fn=mob_fn)

    from app.services.form20_pencil_overlay import build_form20_with_cover_pdf, find_form20_pdf

    form20 = build_form20_with_cover_pdf(sale_dir, mob_fn) or find_form20_pdf(sale_dir, mob_fn) or doc_map.get("FORM 20")

    ordered: list[Path | None] = [
        form20,
        doc_map.get("FORM 22"),
        doc_map.get("FORM 21"),
        doc_map.get("INVOICE ORIGINAL"),
        doc_map.get("INSURANCE CERTIFICATE"),
        resolve_cpa_certificate_pdf(sale_dir, subfolder=sub, mobile=mobile),
    ]

    seen: set[Path] = set()
    out: list[Path] = []
    for p in ordered:
        if p is None or not p.is_file():
            continue
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _merge_pdfs_to_temp(paths: list[Path], *, subfolder: str) -> Path:
    """Merge ordered PDFs into one temp file for a single Electron print dialog."""
    import tempfile

    import fitz

    safe = re.sub(r"[^\w\-]", "_", (subfolder or "default").strip()) or "default"
    ts = int(time.time() * 1000)
    out = Path(tempfile.gettempdir()) / f"saathi-rto-print-{safe}-{ts}.pdf"
    merged = fitz.open()
    try:
        for p in paths:
            doc = fitz.open(str(p))
            merged.insert_pdf(doc, from_page=0, to_page=-1)
            doc.close()
        merged.save(str(out))
    finally:
        merged.close()
    return out


def build_local_rto_print_jobs(
    sale_dir: Path,
    *,
    subfolder: str,
    mobile: str,
    gate_pass_pdf: Path,
) -> tuple[list[dict[str, str]], list[str]]:
    """
    Single merged print job for local Electron print (sale_certificate → insurance → optional cpa → gate_pass).

    Returns ``(print_jobs, missing_required_labels)``.
    """
    sale_pdf, ins_pdf = resolve_rto_print_bundle_pdfs(sale_dir, subfolder=subfolder, mobile=mobile)
    cpa_pdf = resolve_cpa_certificate_pdf(sale_dir, subfolder=subfolder, mobile=mobile)
    missing: list[str] = []
    if sale_pdf is None or not sale_pdf.is_file():
        missing.append(
            "Sale Certificate (*Sale Certificate*, *Sale_Certificate*, or Form 21 PDF from DMS Run Report)"
        )
    if ins_pdf is None or not ins_pdf.is_file():
        missing.append(
            "Insurance certificate ({mobile}_Insurance_{ddmmyyyy}.pdf from Generate Insurance, or *Insurance*.pdf)"
        )
    if gate_pass_pdf is None or not gate_pass_pdf.is_file():
        missing.append("Gate Pass.pdf")
    if missing:
        return [], missing

    ordered_paths: list[Path] = [
        sale_pdf.resolve(),
        ins_pdf.resolve(),
    ]
    if cpa_pdf is not None and cpa_pdf.is_file():
        ordered_paths.append(cpa_pdf.resolve())
    # ordered_paths.append(gate_pass_pdf.resolve())  # TEMP: skip gate pass print; PDF still generated on disk

    merged_path = _merge_pdfs_to_temp(ordered_paths, subfolder=subfolder)
    jobs = [
        {
            "filename": merged_path.name,
            "presigned_url": str(merged_path),
            "kind": "rto_print_bundle",
        }
    ]
    return jobs, []


def build_view_customer_sale_files_print_jobs(
    sale_dir: Path,
    *,
    subfolder: str,
    mobile: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """
    Merged print job for View Customer **Print File**: Form 20, Form 22, GST Retail Invoice.

    Same merge + ``rto_print_bundle`` kind as Print / Queue RTO (one dialog, temp PDF cleaned up by Electron).
    """
    if not sale_dir.is_dir():
        return [], ["Sale folder not found on this PC — sync documents from the server first."]

    mob_fn = _mobile_fn_from_mobile(mobile)
    sub = (subfolder or sale_dir.name).strip()
    doc_map = _resolve_sale_documents(sale_dir, subfolder=sub, mob_fn=mob_fn)

    from app.services.form20_pencil_overlay import build_form20_with_cover_pdf, find_form20_pdf

    form20 = build_form20_with_cover_pdf(sale_dir, mob_fn) or find_form20_pdf(sale_dir, mob_fn) or doc_map.get("FORM 20")
    form22 = doc_map.get("FORM 22")
    gst = doc_map.get("INVOICE ORIGINAL")

    labeled: list[tuple[str, Path | None]] = [
        ("Form 20 (*Form_20*, *Form 20*, or DMS-generated PDF)", form20),
        ("Form 22 (*Form_22*, *Form22*, or DMS Run Report)", form22),
        ("GST Retail Invoice (*GST_Retail_Invoice*, *GST*Invoice*, etc.)", gst),
    ]
    missing: list[str] = []
    ordered_paths: list[Path] = []
    for label, p in labeled:
        if p is None or not p.is_file():
            missing.append(label)
            continue
        ordered_paths.append(p.resolve())

    if missing:
        return [], missing
    if not ordered_paths:
        return [], ["No printable PDFs found in the sale folder."]

    merged_path = _merge_pdfs_to_temp(ordered_paths, subfolder=subfolder)
    jobs = [
        {
            "filename": merged_path.name,
            "presigned_url": str(merged_path),
            "kind": "rto_print_bundle",
        }
    ]
    return jobs, []


# ---------------------------------------------------------------------------
# Playwright micro-helpers
# ---------------------------------------------------------------------------

def _pause() -> None:
    if _current_screen.get() == "Screen 3":
        time.sleep(_SCREEN3_ACTION_WAIT_S)
    else:
        time.sleep(_ACTION_WAIT_S)


def _screen3_timeout_ms(default: int | None = None) -> int:
    """Playwright action timeout — ~450ms on Screen 3, else ``_DEFAULT_TIMEOUT_MS``."""
    if _current_screen.get() == "Screen 3":
        return _SCREEN3_ACTION_TIMEOUT_MS
    return _DEFAULT_TIMEOUT_MS if default is None else default


def _progress_overlay_visible(page: Page) -> bool:
    """True when a PrimeFaces / blockUI loading overlay is visible (quick DOM check)."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const sels = ['.ui-blockui', '.blockUI', '.loading-overlay', '.ui-dialog-loading'];
                    for (const sel of sels) {
                        for (const el of document.querySelectorAll(sel)) {
                            const st = window.getComputedStyle(el);
                            const r = el.getBoundingClientRect();
                            if (st.display !== 'none' && st.visibility !== 'hidden'
                                && r.width >= 2 && r.height >= 2) {
                                return true;
                            }
                        }
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _dump_page_state_target_hint(context: str) -> str:
    """Best-effort field label from ``TIMEOUT fill/click: …`` context strings."""
    m = re.search(r"TIMEOUT (?:fill|click):\s*(.+?)(?:\s+selector=|\s*$)", context, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:not found|blank alert|pending application):\s*(.+?)(?:\s*$)", context, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _dump_page_state_compact(page: Page, context: str) -> None:
    """Short failure snapshot: url, active sub-tab, overlays/dialogs, target-field hints."""
    _rto_log(f"=== PAGE STATE DUMP ({context}) ===")
    try:
        _rto_log(f"url: {(page.url or '')[:300]}")
    except Exception:
        _rto_log("url: (could not read)")

    target_hint = _dump_page_state_target_hint(context)
    if target_hint:
        _rto_log(f"target: {target_hint!r}")

    _JS_COMPACT_SNAPSHOT = """(targetHint) => {
        const out = { activeSubtab: '', dialogs: [], overlays: [], fields: [] };
        const tab = document.querySelector(
            'ul.ui-tabs-nav li.ui-state-active a, ul.ui-tabs-nav li.ui-tabs-selected a'
        );
        if (tab) out.activeSubtab = (tab.textContent || '').trim().substring(0, 100);

        document.querySelectorAll('.ui-dialog, [role="dialog"]').forEach((el) => {
            if (out.dialogs.length >= 3) return;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            if (r.width < 2 || st.display === 'none' || st.visibility === 'hidden') return;
            out.dialogs.push({
                id: (el.id || '').substring(0, 80),
                txt: (el.innerText || '').trim().substring(0, 140).replace(/\\s+/g, ' ')
            });
        });

        document.querySelectorAll(
            '.ui-widget-overlay, .ui-dialog-mask, #msgDialog_modal, .ui-blockui, .blockUI'
        ).forEach((el) => {
            if (out.overlays.length >= 5) return;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return;
            const r = el.getBoundingClientRect();
            if (r.width < 2 && r.height < 2) return;
            out.overlays.push((el.id || el.className || '').substring(0, 80));
        });

        const hint = String(targetHint || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
        const hintTokens = hint ? hint.split(/\\s+/).filter(t => t.length > 2) : [];

        function fieldMatches(el) {
            if (!hintTokens.length) return false;
            const id = (el.id || '').toLowerCase();
            const name = (el.getAttribute('name') || '').toLowerCase();
            const blob = id + ' ' + name;
            return hintTokens.some(t => blob.includes(t));
        }

        document.querySelectorAll('input[id], select[id], textarea[id]').forEach((el) => {
            if (out.fields.length >= 10) return;
            const id = el.id || '';
            if (!id) return;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            const vis = r.width >= 2 && r.height >= 2 && st.display !== 'none' && st.visibility !== 'hidden';
            const match = fieldMatches(el);
            if (!match && out.fields.length >= 6 && !/nomination|hpa|hypo|insur|tax|policy/i.test(id)) return;
            if (!match && hintTokens.length && out.fields.length >= 8) return;
            out.fields.push({
                tag: el.tagName,
                id: id.substring(0, 120),
                vis,
                val: (el.value || '').substring(0, 48),
                disabled: !!el.disabled,
                match
            });
        });

        out.selectCount = document.querySelectorAll('select').length;
        return out;
    }"""

    for fi, frame in enumerate(page.frames):
        try:
            snap = frame.evaluate(_JS_COMPACT_SNAPSHOT, target_hint)
            ftag = f"frame[{fi}] {frame.name or 'main'}"
            if snap.get("activeSubtab"):
                _rto_log(f"  [{ftag}] active_subtab={snap['activeSubtab']!r}")
            dialogs = snap.get("dialogs") or []
            if dialogs:
                for d in dialogs:
                    _rto_log(
                        f"  [{ftag}] dialog id={d.get('id', '')!r} "
                        f"text={d.get('txt', '')!r}"
                    )
            overlays = snap.get("overlays") or []
            if overlays:
                _rto_log(f"  [{ftag}] overlays: {overlays!r}")
            fields = snap.get("fields") or []
            if fields:
                _rto_log(f"  [{ftag}] fields ({len(fields)}):")
                for f in fields:
                    parts = [
                        f.get("tag", ""),
                        f"id={f.get('id', '')!r}",
                        f"vis={f.get('vis')}",
                        f"disabled={f.get('disabled')}",
                    ]
                    if f.get("val"):
                        parts.append(f"value={f.get('val')!r}")
                    if f.get("match"):
                        parts.append("target_match")
                    _rto_log(f"    {' '.join(parts)}")
            sel_n = snap.get("selectCount")
            if sel_n is not None:
                _rto_log(
                    f"  [{ftag}] select_count={sel_n} "
                    f"(set RTO_FILL_LOG_VERBOSE=True for full option dump)"
                )
        except Exception as e:
            _rto_log(f"  [frame[{fi}]] compact snapshot error: {e}")

    _rto_log("=== END PAGE STATE DUMP ===")


def _dump_page_state(page: Page, context: str) -> None:
    """Dump page state into the RTO log — compact by default; full dump when ``RTO_FILL_LOG_VERBOSE``."""
    screen = _current_screen.get()
    if (
        not RTO_FILL_LOG_VERBOSE
        and RTO_FILL_SCREEN3_ONE_DUMP
        and screen == "Screen 3"
        and _screen3_dump_captured.get()
    ):
        _rto_log(f"skip page dump (one per Screen 3 run): {context}")
        return

    if not RTO_FILL_LOG_VERBOSE:
        _dump_page_state_compact(page, context)
    else:
        _dump_page_state_full(page, context)

    if screen == "Screen 3" and RTO_FILL_SCREEN3_ONE_DUMP:
        _screen3_dump_captured.set(True)


def _dump_page_state_full(page: Page, context: str) -> None:
    """Full page-state dump (verbose mode only)."""
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


def _fill(
    page: Page,
    selector: str,
    value: object,
    *,
    timeout: int = _DEFAULT_TIMEOUT_MS,
    label: str = "",
    dump_on_failure: bool = True,
) -> None:
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
        if dump_on_failure:
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
    dump_on_failure: bool = True,
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
                        native_sel.select_option(label=option_label_regex, timeout=timeout)
                        log_val = f"regex:{option_label_regex.pattern}"
                    else:
                        native_sel.select_option(label=value, timeout=timeout)
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
        items_panel.wait_for(state="visible", timeout=timeout)

        if option_label_regex is not None:
            item = items_panel.locator("li.ui-selectonemenu-item").filter(has_text=option_label_regex)
        else:
            item = items_panel.locator("li.ui-selectonemenu-item").filter(
                has_text=re.compile(f"^\\s*{re.escape(value)}\\s*$", re.IGNORECASE),
            )
            if item.count() == 0:
                item = items_panel.locator("li.ui-selectonemenu-item").filter(has_text=value)
        item.first.scroll_into_view_if_needed(timeout=timeout)
        item.first.click(timeout=timeout)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT pf-dropdown: {label} selector={wrapper_selector} value={value!r}")
        if dump_on_failure:
            _dump_page_state(page, f"TIMEOUT pf-dropdown: {label}")
        raise
    logger.debug("fill_rto: pf-dropdown %s = %s (%s)", wrapper_selector, value, label)
    if label:
        shown = value or (option_label_regex.pattern if option_label_regex else "")
        _rto_log(f"pf-dropdown (overlay): {label} = {shown}")
    _pause()


def _dismiss_dialog(page: Page, button_text: str = "OK", *, timeout: int = _DEFAULT_TIMEOUT_MS) -> None:
    """Click a dialog/popup button by its text."""
    if _dismiss_dialog_if_present(page, button_text, timeout_ms=timeout):
        return
    logger.debug("fill_rto: no dialog with '%s' found (timeout), continuing", button_text)


def _dismiss_dialog_if_present(
    page: Page, button_text: str = "OK", *, timeout_ms: int = _FIRST_TRY_MS
) -> bool:
    """Click dialog button when visible; return False without long wait when absent."""
    btn = page.get_by_role("button", name=re.compile(button_text, re.IGNORECASE)).first
    try:
        btn.wait_for(state="visible", timeout=timeout_ms)
        btn.click(timeout=timeout_ms)
        logger.debug("fill_rto: dismissed dialog with '%s'", button_text)
        _rto_log(f"dialog: {button_text}")
        return True
    except PwTimeout:
        return False
    except Exception:
        return False


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


def _persist_rto_queue_progress(
    data: dict,
    *,
    rto_status: int,
    rto_application_id: str | None = None,
) -> None:
    """Write scrape progress to ``rto_queue`` (``rto_application_id`` / ``rto_status``)."""
    rqid = data.get("rto_queue_id")
    if rqid is None:
        _rto_log("WARNING: cannot persist rto_queue progress — rto_queue_id missing")
        return
    from app.repositories import rto_payment_details as rto_repo

    app = (rto_application_id or "").strip() or None
    try:
        ok = rto_repo.update_application_progress(
            int(rqid),
            rto_status=int(rto_status),
            rto_application_id=app,
        )
        if ok:
            _rto_log(
                f"persisted: rto_queue_id={rqid} rto_status={rto_status}"
                + (f" rto_application_id={app!r}" if app else "")
            )
        else:
            _rto_log(f"WARNING: rto_queue progress update affected 0 rows (rto_queue_id={rqid})")
    except Exception as e:
        _rto_log(f"WARNING: rto_queue progress persist failed: {e!s}")
        logger.warning("fill_rto: persist rto_queue progress failed: %s", e)


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


_SCREEN3_VALIDATION_DIALOG_SKIP_LINES = frozenset(
    {"alert!", "close", "ok", "yes", "no", "cancel", "information", "warning"}
)

# Vahan nominee relation dropdown uses **Spouse** (not Wife/Husband).
_VAHAN_NOMINEE_RELATION_PORTAL_LABEL: dict[str, str] = {
    "Wife": "Spouse",
    "Husband": "Spouse",
}


def _vahan_nominee_relation_portal_label(rel_norm: str) -> str:
    """Map DB/OCR relation to the Vahan portal dropdown label."""
    canon = (rel_norm or "").strip()
    if not canon:
        return ""
    return _VAHAN_NOMINEE_RELATION_PORTAL_LABEL.get(canon, canon)


def _screen_3_extract_validation_error_messages(text: str) -> list[str]:
    """Human-readable validation lines from Vahan **Alert!** dialogs after Save and File Movement."""
    errors: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.lower() in _SCREEN3_VALIDATION_DIALOG_SKIP_LINES:
            continue
        if re.search(
            r"invalid|blank|can't|cannot|required|please enter|not set|missing",
            s,
            re.I,
        ):
            errors.append(s)
    return errors


def _screen_3_dialog_is_validation_alert(text: str) -> bool:
    """True when post-save dialog is a Vahan validation **Alert** (not a success / app-no dialog)."""
    raw = (text or "").strip()
    if not raw:
        return False
    if _scrape_application_id_from_dialog_text(raw):
        return False
    if re.match(r"^\s*Alert!?(\s|$)", raw, re.I):
        return True
    if _screen_3_extract_validation_error_messages(raw):
        return True
    upper = raw.upper()
    return any(
        p in upper
        for p in (
            "INVALID RELATION",
            "BLANK INSURANCE",
            "CAN'T BE BLANK",
            "CANNOT BE BLANK",
            "INVALID INSURANCE PERIOD",
        )
    )


def _find_first_attached_selector(
    page: Page, selectors: tuple[str, ...], *, timeout_ms: int = _FIRST_TRY_MS
) -> str | None:
    """Return the first selector whose locator is attached (quick scan — no long wait per miss)."""
    for sel in selectors:
        try:
            page.locator(sel).first.wait_for(state="attached", timeout=timeout_ms)
            return sel
        except Exception:
            continue
    return None


def _screen_3_fail_3c_required_field(page: Page, field: str, detail: str = "") -> None:
    """Raise when a required Screen 3c field could not be set — do not proceed to Save and File Movement."""
    _dump_page_state(page, f"Screen 3c required field: {field}")
    msg = f"Screen 3c required field not set: {field}"
    if detail:
        msg = f"{msg} — {detail}"
    raise RuntimeError(msg)


def _screen_3_fail_save_and_file_movement(dialog_text: str) -> None:
    """Raise when Save and File Movement failed (validation) or produced no proceed dialog."""
    errors = _screen_3_extract_validation_error_messages(dialog_text)
    if errors or _screen_3_dialog_is_validation_alert(dialog_text):
        detail = "; ".join(errors) if errors else (dialog_text or "validation alert").strip()[:300]
        raise RuntimeError(f"Screen 3d Save and File Movement failed: {detail}")
    raise RuntimeError(
        "Screen 3d Save and File Movement produced no confirmation dialog"
    )


def _screen_3d_dialog_is_proceed_signal(dialog_text: str) -> bool:
    """True when post-Yes dialog is not a validation alert (Entry Details / numbers / info = proceed)."""
    raw = (dialog_text or "").strip()
    if not raw:
        return False
    if _screen_3_dialog_is_validation_alert(raw):
        return False
    if _screen_3_extract_validation_error_messages(raw):
        return False
    return True


def _screen_3d_is_entry_details_dialog_text(dialog_text: str) -> bool:
    """True for Vahan **Entry Details** summary (Sale Amount / category / Are You Sure?)."""
    raw = (dialog_text or "").strip()
    if not raw:
        return False
    if re.search(r"Entry\s*Details", raw, re.I):
        return True
    if re.search(r"Sale\s*Amount", raw, re.I) and re.search(
        r"Vehicle\s*(Category|Class|Type)", raw, re.I
    ):
        return True
    return False


def _screen_3_find_entry_details_dialog(page: Page) -> Locator | None:
    """Visible **Entry Details** confirm dialog after Save and File Movement."""
    patterns = (
        r"Entry\s*Details",
        r"Sale\s*Amount",
    )
    for pat in patterns:
        try:
            loc = page.locator(".ui-dialog:visible, [role='dialog']:visible").filter(
                has_text=re.compile(pat, re.I)
            )
            if loc.count() > 0 and loc.first.is_visible(timeout=_FIRST_TRY_MS):
                return loc.first
        except Exception:
            continue
    return None


def _screen_3d_confirm_entry_details_if_present(page: Page) -> tuple[str, bool]:
    """If Entry Details is open: capture text, click **Are You Sure?**.

    Returns ``(dialog_text, confirmed)`` — ``confirmed`` is True when the button was clicked.
    """
    dlg = _screen_3_find_entry_details_dialog(page)
    if dlg is None:
        # Dialog can lag ~500ms after the first Yes — poll up to ~1.5s total.
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and dlg is None:
            time.sleep(0.12)
            dlg = _screen_3_find_entry_details_dialog(page)
    if dlg is None:
        return "", False
    dialog_text = ""
    try:
        dialog_text = (dlg.inner_text(timeout=700) or "").strip()
    except Exception:
        try:
            dialog_text = (
                dlg.locator(".ui-dialog-content").first.inner_text(timeout=500) or ""
            ).strip()
        except Exception:
            dialog_text = ""
    if dialog_text:
        _rto_log(f"Screen 3d: Entry Details dialog: {dialog_text[:400]!r}")
    clicked = False
    # Prefer the floppy-disk confirm inside this dialog (not a bare Yes elsewhere).
    for name_re in (
        re.compile(r"Are\s+You\s+Sure\??", re.I),
        re.compile(r"^\s*Yes\s*$", re.I),
    ):
        try:
            btn = dlg.get_by_role("button", name=name_re).first
            btn.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            btn.click(timeout=_SCREEN3_ACTION_TIMEOUT_MS)
            clicked = True
            _rto_log("Screen 3d: Entry Details — Are You Sure? clicked")
            break
        except Exception:
            continue
    if not clicked:
        # Page-level fallback (button sometimes outside dialog role tree).
        if _dismiss_dialog_if_present(page, r"Are\s+You\s+Sure\??", timeout_ms=_SCREEN3_ACTION_TIMEOUT_MS):
            clicked = True
            _rto_log("Screen 3d: Entry Details — Are You Sure? (page fallback)")
    if clicked:
        _wait_for_progress_close_loop(page)
    return dialog_text, clicked


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
            _persist_rto_queue_progress(
                data,
                rto_status=RTO_STATUS_AFTER_SCREEN2,
                rto_application_id=scraped,
            )
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
                    _persist_rto_queue_progress(
                        data,
                        rto_status=RTO_STATUS_AFTER_SCREEN2,
                        rto_application_id=scraped,
                    )
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


def _wait_for_progress_close_loop(page: Page, *, budget_ms: int | None = None) -> None:
    """Block UI gone: quick exit when no overlay; else ``_FIRST_TRY_MS`` slices until budget."""
    if not _progress_overlay_visible(page):
        return
    budget = budget_ms if budget_ms is not None else (
        _SCREEN3_LOOP_BUDGET_MS if _current_screen.get() == "Screen 3" else _LOOP_BUDGET_MS
    )
    overlay = page.locator(".ui-blockui, .blockUI, .loading-overlay, .ui-dialog-loading").first
    t0 = time.monotonic()
    while True:
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms >= budget:
            break
        slice_ms = int(min(_FIRST_TRY_MS, budget - elapsed_ms))
        if slice_ms < 1:
            break
        try:
            overlay.wait_for(state="hidden", timeout=max(slice_ms, 1))
            break
        except PwTimeout:
            if not _progress_overlay_visible(page):
                break
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
        t = (lab.inner_text(timeout=_screen3_timeout_ms()) or "").strip()
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


def _vahan_on_workbench(page: Page) -> bool:
    """True when the attached tab is on the Vahan workbench form (post-Entry / resume path)."""
    try:
        return bool(re.search(r"workbench\.xhtml", page.url or "", re.I))
    except Exception:
        return False


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
            _rto_log_verbose(f"ensure dealer home: clicked Home ({sel})")
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
        _rto_log_verbose(f"ensure dealer home: navigated to {VAHAN_DEALER_HOME_URL}")
        _pause()
        _wait_for_progress_close(page)
        page.locator("div#officeList").first.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
        _rto_log("ensure dealer home: office list visible after goto")
    except Exception as exc:
        _rto_log(f"ensure dealer home: could not reach Screen 1 — {exc!s}")
        logger.warning("fill_rto: ensure dealer home failed: %s", exc)


def _resume_select_pending_applications_radio(page: Page) -> None:
    """Select **Pending Applications** on dealer home (Get Pending Work panel)."""
    rx = _PENDING_APPLICATIONS_LABEL_RE
    tried: list[str] = []
    for label, click_fn in (
        (
            "get_by_label",
            lambda: page.get_by_label(rx).first.click(timeout=_DEFAULT_TIMEOUT_MS),
        ),
        (
            "label filter",
            lambda: page.locator("label").filter(has_text=rx).first.click(timeout=_DEFAULT_TIMEOUT_MS),
        ),
        (
            "ui-radiobutton-label",
            lambda: page.locator("span.ui-radiobutton-label").filter(has_text=rx).first.click(
                timeout=_DEFAULT_TIMEOUT_MS
            ),
        ),
        (
            "radio near text",
            lambda: page.locator("input[type='radio']").locator(
                "xpath=ancestor::*[contains(normalize-space(.), 'Pending')][1]"
            ).first.click(timeout=_DEFAULT_TIMEOUT_MS),
        ),
    ):
        try:
            click_fn()
            _rto_log_verbose(f"Resume: Pending Applications selected ({label})")
            _pause()
            return
        except Exception as exc:
            tried.append(f"{label}: {exc!s}")
    _rto_log(f"WARNING: Pending Applications radio — tried {tried!r}")
    raise RuntimeError("Resume: Pending Applications radio not found on dealer home")


def _resume_pending_grid_nav_succeeded(page: Page) -> bool:
    """True when a pending-grid action opened workbench or the dealer document upload form."""
    if _vahan_on_workbench(page):
        return True
    if _screen_4_on_documents_upload_page(page):
        return True
    try:
        return bool(re.search(r"workbench\.xhtml", page.url or "", re.I))
    except Exception:
        return False


def _resume_documents_upload_open_for_app(page: Page, application_id: str) -> bool:
    """True when the tab is already on Documents Upload for ``application_id``."""
    if not _screen_4_on_documents_upload_page(page):
        return False
    app = (application_id or "").strip()
    if not app:
        return True
    try:
        body = (page.locator("body").inner_text(timeout=2000) or "").replace(" ", "").upper()
        return app.replace(" ", "").upper() in body
    except Exception:
        return False


def _resume_click_get_pending_work(page: Page) -> None:
    """Click **Get Pending Work** after Pending Applications is selected."""
    for label, click_fn in (
        (
            "role button",
            lambda: page.get_by_role("button", name=_GET_PENDING_WORK_LABEL_RE).first.click(
                timeout=_DEFAULT_TIMEOUT_MS
            ),
        ),
        (
            "input submit",
            lambda: page.locator("input[type='submit'], input[type='button']").filter(
                has_text=_GET_PENDING_WORK_LABEL_RE
            ).first.click(timeout=_DEFAULT_TIMEOUT_MS),
        ),
        (
            "button filter",
            lambda: page.locator("button, a").filter(has_text=_GET_PENDING_WORK_LABEL_RE).first.click(
                timeout=_DEFAULT_TIMEOUT_MS
            ),
        ),
    ):
        try:
            click_fn()
            _rto_log_verbose(f"Resume: Get Pending Work clicked ({label})")
            _pause()
            _wait_for_progress_close(page)
            return
        except Exception:
            continue
    raise RuntimeError("Resume: Get Pending Work button not found on dealer home")


def _resume_current_paginator_page_label(page: Page) -> str:
    """Best-effort active paginator page number (defaults to ``1``)."""
    try:
        active = page.locator(".ui-paginator-page.ui-state-active").first
        if active.count() > 0:
            txt = (active.inner_text(timeout=1000) or "").strip()
            if txt.isdigit():
                return txt
    except Exception:
        pass
    return "1"


def _resume_advance_paginator(page: Page, pages_tried: set[str]) -> bool:
    """Open the next unvisited numbered paginator page, or click next / Next-200."""
    try:
        pages = page.locator(".ui-paginator-page")
        count = pages.count()
        for idx in range(count):
            el = pages.nth(idx)
            label = (el.inner_text(timeout=800) or "").strip()
            if not label.isdigit() or label in pages_tried:
                continue
            cls = el.get_attribute("class") or ""
            if "ui-state-disabled" in cls:
                continue
            el.click(timeout=_DEFAULT_TIMEOUT_MS)
            _rto_log_verbose(f"Resume: paginator → page {label}")
            _wait_for_progress_close(page)
            time.sleep(0.25)
            return True
    except Exception as exc:
        _rto_log(f"Resume: numbered paginator advance failed: {exc!s}")

    for sel in (
        ".ui-paginator-next:not(.ui-state-disabled)",
        "a.ui-paginator-next:not(.ui-state-disabled)",
        "button:has-text('Next-200')",
        "a:has-text('Next-200')",
        ".ui-paginator-last:not(.ui-state-disabled)",
    ):
        try:
            nxt = page.locator(sel).first
            if nxt.count() == 0:
                continue
            cls = nxt.get_attribute("class") or ""
            if "ui-state-disabled" in cls:
                continue
            nxt.click(timeout=_DEFAULT_TIMEOUT_MS)
            _rto_log_verbose(f"Resume: paginator advanced via {sel}")
            return True
        except Exception:
            continue
    return False


def _resume_wait_pending_grid_settled(page: Page) -> None:
    """Wait for pending-work datatable rows after Get Pending Work or paginator click."""
    _wait_for_progress_close(page)
    time.sleep(0.35)
    for sel in (".ui-datatable-data tr", "tbody tr", "table[role='grid'] tr"):
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
            return
        except Exception:
            continue


def _resume_find_application_row(page: Page, application_id: str) -> Locator | None:
    """Locate pending-work row containing ``application_id`` (Application No column or row text)."""
    app = (application_id or "").strip()
    if not app:
        return None
    app_re = re.compile(re.escape(app), re.I)
    row_selectors = (
        ".ui-datatable-data tr",
        "tbody tr",
        "table[role='grid'] tr",
        "tr",
    )
    for sel in row_selectors:
        try:
            rows = page.locator(sel).filter(has_text=app_re)
            n = rows.count()
            for idx in range(n):
                row = rows.nth(idx)
                try:
                    if row.locator("th").count() > 0:
                        continue
                except Exception:
                    pass
                try:
                    if row.is_visible(timeout=500):
                        return row
                except Exception:
                    continue
        except Exception:
            continue

    # JS fallback — normalized match when Playwright filter misses nested cell text.
    try:
        row_idx = page.evaluate(
            """(appId) => {
                const norm = (s) => (s || '').replace(/\\s+/g, '').toUpperCase();
                const target = norm(appId);
                const rows = document.querySelectorAll(
                    '.ui-datatable-data tr, tbody tr, table[role="grid"] tr'
                );
                let dataIdx = -1;
                for (const row of rows) {
                    if (row.querySelector('th')) continue;
                    const text = norm(row.innerText || row.textContent || '');
                    if (!text) continue;
                    dataIdx += 1;
                    if (text.includes(target)) return dataIdx;
                }
                return -1;
            }""",
            app,
        )
        if isinstance(row_idx, int) and row_idx >= 0:
            for sel in (".ui-datatable-data tr", "tbody tr"):
                try:
                    all_rows = page.locator(sel)
                    n = all_rows.count()
                    seen = 0
                    for i in range(n):
                        row = all_rows.nth(i)
                        if row.locator("th").count() > 0:
                            continue
                        if seen == row_idx:
                            return row
                        seen += 1
                except Exception:
                    continue
    except Exception as exc:
        logger.debug("fill_rto: resume JS row scan: %s", exc)
    return None


def _resume_click_row_action(
    page: Page,
    row,
    *,
    action_priority: tuple[re.Pattern[str], ...] | None = None,
) -> None:
    """Click the best Action control on a pending-work grid row (Verify / Entry / …)."""
    buttons = row.locator("button, a, input[type='button'], input[type='submit']")
    count = buttons.count()
    priority = action_priority or (_ENTRY_ACTION_RE, _VERIFY_ACTION_RE)
    preferred_idx: int | None = None
    fallback_idx: int | None = None
    for want in priority:
        for idx in range(count):
            btn = buttons.nth(idx)
            try:
                txt = (btn.inner_text(timeout=1000) or btn.get_attribute("value") or "").strip()
            except Exception:
                txt = ""
            if not txt or _APPROVE_ACTION_RE.match(txt):
                continue
            if want.match(txt):
                preferred_idx = idx
                break
        if preferred_idx is not None:
            break
    if preferred_idx is None:
        for idx in range(count):
            btn = buttons.nth(idx)
            try:
                txt = (btn.inner_text(timeout=1000) or btn.get_attribute("value") or "").strip()
            except Exception:
                txt = ""
            if not txt or _APPROVE_ACTION_RE.match(txt):
                continue
            if _RESUME_ROW_ACTION_PREFERRED_RE.match(txt):
                preferred_idx = idx
                break
            if fallback_idx is None:
                fallback_idx = idx
    pick = preferred_idx if preferred_idx is not None else fallback_idx
    if pick is None:
        raise RuntimeError("Resume: no non-Approve action button on matching pending-work row")
    btn = buttons.nth(pick)
    txt = (btn.inner_text(timeout=1000) or btn.get_attribute("value") or "").strip()
    btn.click(timeout=_DEFAULT_TIMEOUT_MS)
    _rto_log(f"Resume: clicked row action {txt!r}")


def _resume_open_application_row(
    page: Page,
    application_id: str,
    *,
    rto_status: int | None,
    cur_page: str = "1",
    target: Locator | None = None,
) -> bool:
    """Click Verify / Entry on a located row. True when workbench opens (even if post-click wait errors)."""
    app = (application_id or "").strip()
    row = target if target is not None else _resume_find_application_row(page, app)
    if row is None:
        return False
    clicked = False
    try:
        row.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
        _resume_click_row_action(
            page, row, action_priority=_resume_row_action_priority(rto_status)
        )
        clicked = True
    except Exception as exc:
        _rto_log(f"WARNING: row action on page {cur_page}: {exc!s}")
    _pause()
    _wait_for_progress_close_loop(page)
    time.sleep(0.5)
    if _resume_pending_grid_nav_succeeded(page):
        try:
            url = (page.url or "")[:220]
            _rto_log(f"Resume: opened {app!r} on page {cur_page} — workbench url={url}")
        except Exception:
            _rto_log(f"Resume: opened {app!r} on page {cur_page} — workbench loaded")
        return True
    if clicked:
        _rto_log(f"WARNING: row action clicked but workbench not detected for {app!r}")
    return False


def _resume_paginate_and_open_application(
    page: Page,
    application_id: str,
    *,
    rto_status: int | None = None,
) -> None:
    """Scan pending-work pages for ``application_id`` and open via row Action (Verify / Entry)."""
    app = (application_id or "").strip()
    if not app:
        raise RuntimeError("Resume: application_id is required to open pending work")

    if _resume_documents_upload_open_for_app(page, app):
        _rto_log(f"Resume: {app!r} — already on Documents Upload page")
        return

    pages_tried: set[str] = set()
    max_attempts = 25

    _resume_wait_pending_grid_settled(page)

    for attempt in range(1, max_attempts + 1):
        cur_page = _resume_current_paginator_page_label(page)
        pages_tried.add(cur_page)
        _rto_log(
            f"Resume: scan pending grid page {cur_page} (attempt {attempt}) for {app!r}"
        )

        target = _resume_find_application_row(page, app)
        if target is not None:
            if _resume_open_application_row(
                page,
                app,
                rto_status=rto_status,
                cur_page=cur_page,
                target=target,
            ):
                return

        if _resume_pending_grid_nav_succeeded(page):
            _rto_log(f"Resume: {app!r} — workbench already open after grid action")
            return

        if not _resume_advance_paginator(page, pages_tried):
            break
        _resume_wait_pending_grid_settled(page)

    if _resume_pending_grid_nav_succeeded(page):
        _rto_log(f"Resume: {app!r} — workbench open (Verify/Entry succeeded during scan)")
        return

    _rto_log(
        f"Resume: {app!r} not in grid after pages {sorted(pages_tried, key=int)!r}"
    )
    _dump_page_state(page, f"pending application not found: {app}")
    raise RuntimeError(
        f"Resume: application {app!r} not found in pending-work grid "
        f"(pages tried: {sorted(pages_tried, key=int)})"
    )


def _resume_open_application_from_pending_grid(
    page: Page,
    office: str,
    application_id: str,
    *,
    rto_status: int | None = None,
) -> None:
    """Resume path: office → Pending Applications → Get Pending Work → paginate → Verify/Entry."""
    _set_screen("Resume")
    logger.info(
        "fill_rto: resume pending grid office=%s application_id=%s rto_status=%s",
        office,
        application_id,
        rto_status,
    )
    _rto_log(
        f"Resume: pending grid office={office!r} app={application_id!r} "
        f"rto_status={rto_status!r}"
    )

    _select_pf_dropdown(
        page,
        "div#officeList",
        office,
        label="Select Assigned Office",
    )
    _pause()

    _resume_select_pending_applications_radio(page)
    _resume_click_get_pending_work(page)
    _resume_paginate_and_open_application(page, application_id, rto_status=rto_status)


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

    # PF action menu can leave focus in the open overlay; Tab reaches Show Form (operator repro).
    page.keyboard.press("Tab")
    _pause()
    _rto_log("Tab → Show Form")

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
    if not RTO_FILL_LOG_VERBOSE:
        return
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
        _rto_log_verbose("Screen 3: scrolled Vehicle Details tab panel toward bottom (Tax Mode region)")
    except Exception as e:
        _rto_log(f"Screen 3: tab panel scroll skipped ({e!s})")

    fast_sel = 'select[id="workbench_tabview:tableTaxMode:0:taxModeType_input"]'
    loc = page.locator(fast_sel).first
    try:
        loc.wait_for(state="attached", timeout=3000)
        loc.evaluate("el => el.scrollIntoView({ block: 'center', inline: 'nearest' })")
        _pause()
        _rto_log_verbose(f"Screen 3: scrolled to Tax Mode (fast tableTaxMode:0 {fast_sel!r})")
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
            _rto_log_verbose(f"Screen 3: scrolled to Tax Mode ({sel!r})")
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
    if not RTO_FILL_LOG_VERBOSE:
        return
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


def _screen_3_find_policy_no_blank_alert_dialog(page: Page) -> Locator | None:
    """Visible dialog whose text includes **Policy No Can't be Blank**."""
    for sel in (".ui-dialog:visible", "[role='dialog']:visible"):
        try:
            loc = page.locator(sel).filter(has_text=_POLICY_NO_BLANK_ALERT_RE)
            if loc.count() > 0 and loc.first.is_visible(timeout=400):
                return loc.first
        except Exception:
            continue
    return None


def _screen_3_dismiss_policy_no_blank_alert(page: Page) -> bool:
    """Dismiss **Policy No Can't be Blank** via **Close**; dump frames if Close missing."""
    dlg = _screen_3_find_policy_no_blank_alert_dialog(page)
    if dlg is None:
        return False
    _rto_log("Screen 3: Policy No blank alert detected — clicking Close")
    dismissed = False
    try:
        dlg.get_by_role("button", name=re.compile(r"^\s*Close\s*$", re.I)).first.click(timeout=2000)
        dismissed = True
        _rto_log("Screen 3: Policy No blank alert — Close clicked")
    except Exception:
        pass
    if not dismissed:
        try:
            dlg.locator("button, a.ui-button, input[type='button'], input[type='submit']").filter(
                has_text=re.compile(r"Close", re.I)
            ).first.click(timeout=2000)
            dismissed = True
            _rto_log("Screen 3: Policy No blank alert — Close (text fallback)")
        except Exception:
            pass
    if not dismissed:
        dismissed = _screen_3_click_dialog_dismiss_any(dlg, page, log_prefix="Screen 3 Policy No alert")
    if not dismissed:
        _rto_log("WARNING: Policy No blank alert visible but Close not found — frame/popup dump")
        _screen_3_dump_frames_and_popup_candidates(page)
        _dump_page_state(page, "Policy No blank alert Close missing")
        return False
    _wait_for_progress_close_loop(page)
    return True


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
    try:
        if page.locator("#msgDialog:visible, [id*='msgDialog']:visible").count() > 0:
            return True
    except Exception:
        pass
    return False


def _screen_3_find_insurance_period_followup_dialog(page: Page) -> Locator | None:
    """Visible dialog after **Insurance Period** change — exclude OTP / owner-verify modals."""
    if _find_visible_otp_dialog(page) is not None:
        return None
    patterns = (
        r"Insurance|insurance|Period|period|Policy|policy|valid|upto|Upto|Information|Message|Alert",
    )
    for pat in patterns:
        try:
            loc = page.locator(".ui-dialog:visible, [role='dialog']:visible").filter(
                has_text=re.compile(pat, re.I)
            )
            if loc.count() > 0 and loc.first.is_visible(timeout=250):
                return loc.first
        except Exception:
            continue
    try:
        generic = page.locator(
            "#msgDialog:visible, .ui-dialog:visible, [role='dialog']:visible"
        ).first
        if generic.is_visible(timeout=250):
            return generic
    except Exception:
        pass
    return None


def _screen_3_dismiss_post_insurance_period_dialog(page: Page) -> bool:
    """Dismiss occasional Vahan popup after **Insurance Period** change."""
    _wait_for_progress_close_loop(page)
    time.sleep(0.35)  # AJAX follow-up modal can lag after period select.
    dismissed = False
    deadline = time.monotonic() + _SCREEN3_PERIOD_DIALOG_POLL_S
    while time.monotonic() < deadline:
        if not _screen_3_any_modal_dialog_visible(page):
            time.sleep(0.12)
            continue
        dlg = _screen_3_find_insurance_period_followup_dialog(page)
        if dlg is None:
            # Generic visible dialog (wording varies by build).
            try:
                dlg = page.locator(".ui-dialog:visible, [role='dialog']:visible").first
                if not dlg.is_visible(timeout=_FIRST_TRY_MS):
                    time.sleep(0.1)
                    continue
            except Exception:
                time.sleep(0.1)
                continue
        try:
            snippet = (dlg.inner_text(timeout=800) or "").strip().replace("\n", " ")[:400]
            if snippet:
                _rto_log(f"Screen 3c: Insurance Period popup: {snippet!r}")
        except Exception:
            pass
        if _screen_3_click_dialog_dismiss_any(
            dlg, page, log_prefix="Screen 3c Insurance Period popup"
        ):
            dismissed = True
            _wait_for_progress_close_loop(page)
            continue
        break
    if dismissed:
        _rto_log("Screen 3c: Insurance Period popup dismissed")
    return dismissed


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
        _rto_log_verbose("Screen 3: scrolled to sub-tab bar")
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

_JS_INSURANCE_TAB_FIELD_SNAPSHOT = """() => {
    const RE = /ins|policy|nomination|nominee|idv|series|vm_rel|hpa|hypo|insur|ins_year|ins_cd|isHypo|nomineeradio/i;
    const clip = (s, n) => String(s || '').replace(/\\s+/g, ' ').trim().substring(0, n);

    function visible(el) {
        const r = el.getBoundingClientRect();
        const st = window.getComputedStyle(el);
        return r.width >= 2 && r.height >= 2 && st.display !== 'none' && st.visibility !== 'hidden';
    }

    function labelFor(el) {
        const id = el.id || '';
        if (id) {
            const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
            if (lab) return clip(lab.innerText, 80);
        }
        const pfLab = document.getElementById(`${id}_label`);
        if (pfLab) return clip(pfLab.innerText, 80);
        const wrap = el.closest('.ui-selectonemenu, .ui-chkbox, .ui-radiobutton, .ui-calendar');
        if (wrap && wrap.id) {
            const wl = document.getElementById(`${wrap.id}_label`);
            if (wl) return clip(wl.innerText, 80);
        }
        return '';
    }

    function parentPfId(el) {
        const w = el.closest('[id].ui-selectonemenu, [id].ui-chkbox, [id].ui-radiobutton, [id].ui-calendar');
        return w && w.id ? w.id.substring(0, 120) : '';
    }

    function selectMeta(el) {
        if (el.tagName !== 'SELECT') return null;
        const opts = Array.from(el.options || []);
        const si = el.selectedIndex;
        const sel = si >= 0 && opts[si] ? opts[si] : null;
        return {
            selectedIndex: si,
            selectedLabel: sel ? clip(sel.textContent, 80) : '',
            selectedValue: sel ? clip(sel.value, 48) : '',
            optionCount: opts.length,
            optionsPreview: opts.slice(0, 8).map((o, i) => ({
                i,
                t: clip(o.textContent, 72),
                v: clip(o.value, 40)
            }))
        };
    }

    const fields = [];
    document.querySelectorAll(
        'input[id], select[id], textarea[id], button[id], a[id], span[id].ui-button, div[id].ui-selectonemenu'
    ).forEach((el) => {
        const id = el.id || '';
        if (!id || !RE.test(id)) return;
        const sm = selectMeta(el);
        fields.push({
            tag: el.tagName,
            id: id.substring(0, 120),
            name: clip(el.getAttribute('name'), 80),
            type: clip(el.getAttribute('type') || (el.tagName === 'SELECT' ? 'select' : ''), 32),
            role: clip(el.getAttribute('role'), 40),
            cls: clip(el.className, 100),
            placeholder: clip(el.getAttribute('placeholder'), 60),
            title: clip(el.getAttribute('title'), 60),
            ariaLabel: clip(el.getAttribute('aria-label'), 60),
            visible: visible(el),
            disabled: !!el.disabled,
            readOnly: !!el.readOnly,
            checked: el.type === 'checkbox' || el.type === 'radio' ? !!el.checked : null,
            hasDatepicker: el.classList ? el.classList.contains('hasDatepicker') : false,
            value: clip(el.value, 48),
            text: clip(el.innerText, 80),
            label: labelFor(el),
            parentPf: parentPfId(el),
            select: sm,
            outerHTML: clip(el.outerHTML, 260)
        });
    });

    const pfLabels = [];
    document.querySelectorAll('[id$="_label"], label.ui-outputlabel, span.ui-outputlabel').forEach((el) => {
        const id = el.id || '';
        const blob = (id + ' ' + (el.getAttribute('for') || '') + ' ' + (el.innerText || '')).toLowerCase();
        if (!RE.test(blob)) return;
        pfLabels.push({
            tag: el.tagName,
            id: id.substring(0, 120),
            forId: clip(el.getAttribute('for'), 120),
            text: clip(el.innerText, 100),
            cls: clip(el.className, 80),
            outerHTML: clip(el.outerHTML, 200)
        });
    });

    const dialogs = [];
    document.querySelectorAll('.ui-dialog, [role="dialog"]').forEach((el) => {
        if (dialogs.length >= 4) return;
        if (!visible(el)) return;
        dialogs.push({
            id: clip(el.id, 80),
            title: clip(el.querySelector('.ui-dialog-title')?.innerText, 80),
            text: clip(el.innerText, 160),
            outerHTML: clip(el.outerHTML, 320)
        });
    });

    const tab = document.querySelector(
        'ul.ui-tabs-nav li.ui-state-active a, ul.ui-tabs-nav li.ui-tabs-selected a'
    );
    return {
        activeSubtab: tab ? clip(tab.textContent, 100) : '',
        fieldCount: fields.length,
        fields,
        pfLabels: pfLabels.slice(0, 50),
        dialogs
    };
}"""


def _screen_3_log_insurance_inventory_row(prefix: str, row: dict) -> None:
    """Format one insurance-tab inventory element for RTO.txt."""
    parts = [
        f"{prefix}{row.get('tag')} id={row.get('id')!r}",
        f"type={row.get('type')!r}" if row.get("type") else None,
        f"name={row.get('name')!r}" if row.get("name") else None,
        f"visible={row.get('visible')}",
        f"disabled={row.get('disabled')}",
        f"readOnly={row.get('readOnly')}" if row.get("readOnly") else None,
        f"checked={row.get('checked')}" if row.get("checked") is not None else None,
        f"hasDatepicker={row.get('hasDatepicker')}" if row.get("hasDatepicker") else None,
        f"value={row.get('value')!r}" if row.get("value") else None,
        f"text={row.get('text')!r}" if row.get("text") else None,
        f"label={row.get('label')!r}" if row.get("label") else None,
        f"parentPf={row.get('parentPf')!r}" if row.get("parentPf") else None,
        f"cls={row.get('cls')!r}" if row.get("cls") else None,
        f"placeholder={row.get('placeholder')!r}" if row.get("placeholder") else None,
        f"aria-label={row.get('ariaLabel')!r}" if row.get("ariaLabel") else None,
    ]
    _rto_inventory_log(" ".join(p for p in parts if p))
    sm = row.get("select")
    if isinstance(sm, dict) and sm:
        _rto_inventory_log(
            f"{prefix}  select: idx={sm.get('selectedIndex')} "
            f"label={sm.get('selectedLabel')!r} value={sm.get('selectedValue')!r} "
            f"options={sm.get('optionCount')}"
        )
        for opt in sm.get("optionsPreview") or []:
            _rto_inventory_log(
                f"{prefix}    opt[{opt.get('i')}] text={opt.get('t')!r} value={opt.get('v')!r}"
            )
    html = (row.get("outerHTML") or "").strip()
    if html:
        _rto_inventory_log(f"{prefix}  html: {html}")


def _screen_3_dump_insurance_tab_field_inventory(page: Page, *, phase: str) -> None:
    """Log insurance/nominee/hypo field ids in every frame → companion ``*_3c_inventory.txt``."""
    if not RTO_FILL_SCREEN3_INSURANCE_FIELD_DUMP:
        return
    log = _rto_action_log.get()
    if log is not None:
        inv_display = _rto_log_path_display(log.inventory_path, dealer_id=None)
        _rto_log(f"Screen 3c field inventory ({phase}) → {inv_display}")
    _rto_inventory_log(f"=== Screen 3c insurance field inventory ({phase}) ===")
    try:
        _rto_inventory_log(f"page url: {(page.url or '')[:300]}")
    except Exception:
        pass
    try:
        for fi, frame in enumerate(page.frames):
            try:
                fn = frame.name or "main"
                fu = (frame.url or "")[:200]
                snap = frame.evaluate(_JS_INSURANCE_TAB_FIELD_SNAPSHOT)
            except Exception as e:
                _rto_inventory_log(f"  frame[{fi}] inventory error: {e!s}")
                continue
            _rto_inventory_log(f"  frame[{fi}] {fn!r} url={fu}")
            _rto_inventory_log(
                f"    active_subtab={snap.get('activeSubtab')!r} "
                f"fields={snap.get('fieldCount')} dialogs={len(snap.get('dialogs') or [])}"
            )
            for dlg in snap.get("dialogs") or []:
                _rto_inventory_log(
                    f"    DIALOG id={dlg.get('id')!r} title={dlg.get('title')!r} "
                    f"text={dlg.get('text')!r}"
                )
                if dlg.get("outerHTML"):
                    _rto_inventory_log(f"      html: {dlg.get('outerHTML')}")
            for row in snap.get("fields") or []:
                _screen_3_log_insurance_inventory_row("    FIELD ", row)
            for row in snap.get("pfLabels") or []:
                _screen_3_log_insurance_inventory_row("    LABEL ", row)
    except Exception as e:
        _rto_inventory_log(f"Screen 3c insurance field inventory error: {e!s}")
    _rto_inventory_log(f"=== End Screen 3c insurance field inventory ({phase}) ===")


def _screen_3_dump_frames_and_popup_candidates(page: Page) -> None:
    """Before Hypothecation sub-tab: log every frame plus dialog / overlay / Ok-button inventory (RTO.txt)."""
    if not RTO_FILL_LOG_VERBOSE:
        return
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

    # State AJAX loads district options — wait even when state was pre-filled on the form.
    if state_val:
        _wait_for_progress_close_loop(page)

    district_disp = _resolve_vahan_district(data)
    city_raw_in = (data.get("city") or "").strip()
    if district_disp:
        _rto_log(
            f"District (correspondence): {district_disp!r} "
            f"(city={city_raw_in!r}, dealer_rto={(data.get('dealer_rto') or '')!r})"
        )
        if not _screen_3_pf_dropdown_chain(
            page,
            _SCREEN2_CORR_DISTRICT_PF_WRAPPERS,
            district_disp,
            label="District (correspondence)",
        ):
            if not _screen_3_native_select_chain(
                page,
                _SCREEN2_CORR_DISTRICT_NATIVE,
                district_disp,
                label="District (correspondence)",
            ):
                _rto_log(f"WARNING: District not set (tried {district_disp!r})")
        _close_pf_selectonemenu_overlay(page, "workbench_tabview:tf_c_district")
        _pause()
    else:
        _rto_log("WARNING: District not resolved (no district or dealer_rto)")

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
    use_native_select: bool = True,
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
                timeout=_screen3_timeout_ms(),
                use_native_select=use_native_select,
                dump_on_failure=False,
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
            _select(page, sel, value, label=label, timeout=_screen3_timeout_ms())
            return True
        except Exception:
            continue
    return False


def _fill_first_matching(
    page: Page, selectors: tuple[str, ...], value: object, *, label: str, dump_on_failure: bool = True
) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if text == "":
        return True
    sel = _find_first_attached_selector(page, selectors, timeout_ms=_FIRST_TRY_MS)
    if sel:
        try:
            _fill(page, sel, text, label=label, timeout=_screen3_timeout_ms(), dump_on_failure=False)
            return True
        except Exception:
            pass
    _rto_log(f"WARNING: {label} not filled (no matching field)")
    if dump_on_failure:
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
    probe_ms = _screen3_timeout_ms()
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=probe_ms)
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


def _screen_3_read_insurance_company_label(page: Page) -> str:
    """Visible **Insurance Company** label or native ``select`` selected option text."""
    label_ids = (
        "workbench_tabview:insurance_company_label",
        "workbench_tabview:insuranceCompany_label",
        "workbench_tabview:ins_cd_label",
    )
    probe_ms = _SCREEN3_INSURANCE_COMPANY_TIMEOUT_MS
    for lid in label_ids:
        try:
            t = (page.locator(f'[id="{lid}"]').first.inner_text(timeout=probe_ms) or "").strip()
            if t and not _screen_3_is_select_placeholder_label(t):
                return t
        except Exception:
            continue
    for sel in _SCREEN3_INSURANCE_COMPANY_NATIVE:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=probe_ms)
            txt = loc.evaluate(
                """el => {
                    const o = el.options[el.selectedIndex];
                    return o ? (o.textContent || '').trim() : '';
                }"""
            )
            if txt and not _screen_3_is_select_placeholder_label(str(txt)):
                return str(txt).strip()
        except Exception:
            continue
    return ""


def _screen_3_insurance_company_already_set(page: Page) -> bool:
    """Vahan may pre-fill **Insurance Company** — do not overwrite."""
    return bool(_screen_3_read_insurance_company_label(page))


def _screen_3_insurer_search_phrases(insurer: str) -> list[str]:
    """Filter phrases for the PF Insurance Company overlay (most specific first)."""
    ins = (insurer or "").strip()
    phrases: list[str] = []
    if re.search(r"bajaj", ins, re.I) and re.search(r"general|allianz", ins, re.I):
        phrases.extend(["Bajaj General", "Bajaj"])
    tokens = _screen_3_insurer_filter_tokens(ins)
    if len(tokens) >= 2:
        phrases.append(" ".join(tokens[:2]))
    phrases.extend(tokens)
    out: list[str] = []
    seen: set[str] = set()
    for p in phrases:
        key = p.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _screen_3_insurer_norm_key(text: str) -> str:
    """Normalize insurer / option text for equality checks (drop punctuation / Co Ltd noise)."""
    s = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    for noise in ("limited", "ltd", "company", "co", "pvt", "private"):
        s = s.replace(noise, "")
    return s


def _screen_3_insurer_option_is_finance_not_insurer(option_text: str) -> bool:
    """True for bank/financier rows that must not match a general-insurance company target."""
    opt = (option_text or "").lower()
    if re.search(r"\b(fin|finance|financier|bank|auto\s*fin)\b", opt):
        if "insurance" not in opt and "general" not in opt:
            return True
    return False


def _screen_3_read_insurance_from_value(page: Page) -> str:
    """Read visible **Insurance from** after Vahan pre-fill or our fill."""
    for sel in _SCREEN3_INSURANCE_FROM_INPUT:
        try:
            val = (page.locator(sel).first.input_value(timeout=1000) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


def _screen_3_idv_has_decimal_format(page: Page) -> bool:
    """True when IDV field shows a decimal fraction (Vahan rejects ``90000.00``)."""
    for sel in _SCREEN3_IDV_INPUT:
        try:
            val = (page.locator(sel).first.input_value(timeout=1000) or "").strip()
            if val and "." in val:
                return True
        except Exception:
            continue
    return False


def _fill_idv_integer(page: Page, selectors: tuple[str, ...], value: object, *, label: str) -> bool:
    """Fill IDV as an integer string via JS (avoids PrimeFaces ``.00`` validation errors)."""
    text = _normalize_idv_for_vahan(value)
    if not text:
        return True
    t_ms = _screen3_timeout_ms()
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=t_ms)
            loc.scroll_into_view_if_needed(timeout=t_ms)
            loc.evaluate(
                """(el, v) => {
                    el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                text,
            )
            _pause()
            _rto_log(f"fill: {label} = {text}")
            return True
        except Exception:
            continue
    return _fill_first_matching(page, selectors, text, label=label)


def _screen_3_read_insurance_period_label(page: Page) -> str:
    """Visible PF label or native ``select`` text for **Insurance Period (in year)**."""
    try:
        t = (
            page.locator('[id="workbench_tabview:ins_year_label"]').first.inner_text(
                timeout=_screen3_timeout_ms()
            )
            or ""
        ).strip()
        if t and not _screen_3_is_select_placeholder_label(t):
            return t
    except Exception:
        pass
    for sel in _SCREEN3_INSURANCE_PERIOD_NATIVE:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=_screen3_timeout_ms())
            txt = loc.evaluate(
                "el => { const o = el.options[el.selectedIndex]; "
                "return o ? (o.textContent || '').trim() : ''; }"
            )
            if txt and not _screen_3_is_select_placeholder_label(str(txt)):
                return str(txt).strip()
        except Exception:
            continue
    return ""


def _screen_3_insurance_period_is_target(page: Page) -> bool:
    """True when Insurance Period is already **5 Year** (do not overwrite)."""
    label = _screen_3_read_insurance_period_label(page)
    if not label:
        return False
    return bool(re.search(r"5\s*Year", label, re.I))


def _screen_3_insurer_filter_tokens(insurer: str) -> list[str]:
    """Search tokens for the PF **Insurance Company** filter (skip generic insurer words)."""
    skip = frozenset(
        {"general", "insurance", "limited", "ltd", "co", "company", "the", "of", "and", "pvt", "private"}
    )
    tokens: list[str] = []
    for w in re.findall(r"[A-Za-z0-9]+", insurer):
        if len(w) < 3 or w.lower() in skip:
            continue
        tokens.append(w)
    if not tokens:
        tokens = [w for w in re.findall(r"[A-Za-z0-9]+", insurer) if len(w) >= 3][:2]
    return tokens[:3]


def _screen_3_insurer_option_matches(insurer: str, option_text: str) -> bool:
    """Match DB / portal insurer label to a Vahan option — reject bank/financer lookalikes."""
    opt = (option_text or "").strip()
    if not opt or _screen_3_is_select_placeholder_label(opt):
        return False
    ins = (insurer or "").strip()
    if not ins:
        return False
    if _screen_3_insurer_option_is_finance_not_insurer(opt):
        return False

    ins_key = _screen_3_insurer_norm_key(ins)
    opt_key = _screen_3_insurer_norm_key(opt)
    if ins_key and opt_key and (ins_key == opt_key or ins_key in opt_key or opt_key in ins_key):
        return True

    ins_lower = ins.lower()
    opt_lower = opt.lower()
    wants_general = "general" in ins_lower or "allianz" in ins_lower
    if "bajaj" in ins_lower and wants_general:
        if re.search(r"bajaj\s+general", opt, re.I):
            return True
        return False

    tokens = _screen_3_insurer_filter_tokens(insurer)
    # Require at least two distinctive tokens — a lone "Bajaj" must not match AUTO FIN.
    if len(tokens) >= 2 and all(t.lower() in opt_lower for t in tokens[:2]):
        if wants_general and "general" not in opt_lower and "insurance" not in opt_lower:
            return False
        return True
    return False


def _screen_3_select_insurance_company_overlay(page: Page, insurer: str) -> bool:
    """Pick **Insurance Company** via ``ins_cd`` PF filter + one matching ``li`` click."""
    wid = _SCREEN3_INSURANCE_COMPANY_WRAPPER_ID
    t_ms = _SCREEN3_INSURANCE_COMPANY_TIMEOUT_MS
    wrap = page.locator(f'[id="{wid}"]').first
    try:
        wrap.wait_for(state="visible", timeout=t_ms)
        wrap.scroll_into_view_if_needed(timeout=t_ms)
        wrap.click(timeout=t_ms)
        _pause()
        panel = page.locator(f'[id="{wid}_panel"]').first
        panel.wait_for(state="visible", timeout=t_ms)
    except Exception:
        return False

    phrases = _screen_3_insurer_search_phrases(insurer)
    for tok in phrases:
        try:
            fin = panel.locator("input.ui-selectonemenu-filter").first
            fin.wait_for(state="visible", timeout=t_ms)
            fin.fill("")
            fin.fill(tok)
            _pause()
            time.sleep(0.15)  # filtered list can lag behind keystrokes
        except Exception:
            continue

        # Prefer exact / Bajaj General regex click — no 80×800ms inner_text scan.
        try:
            if re.search(r"bajaj", insurer, re.I) and re.search(
                r"general|allianz", insurer, re.I
            ):
                item = panel.locator("li.ui-selectonemenu-item").filter(
                    has_text=re.compile(r"Bajaj\s+General\s+Insurance", re.I)
                ).first
                item.wait_for(state="visible", timeout=t_ms)
                txt = (item.inner_text(timeout=t_ms) or "").strip()
                if _screen_3_insurer_option_matches(insurer, txt):
                    item.click(timeout=t_ms)
                    _rto_log(f"pf-dropdown (overlay): Insurance Company = {txt}")
                    _pause()
                    _close_pf_selectonemenu_overlay(page, wid)
                    return True
        except Exception:
            pass

        try:
            # Exact label (escaped) among filtered rows.
            esc = re.escape(insurer.strip())
            item = panel.locator("li.ui-selectonemenu-item").filter(
                has_text=re.compile(rf"^\s*{esc}\s*$", re.I)
            ).first
            item.wait_for(state="visible", timeout=t_ms)
            txt = (item.inner_text(timeout=t_ms) or "").strip()
            item.click(timeout=t_ms)
            _rto_log(f"pf-dropdown (overlay): Insurance Company = {txt}")
            _pause()
            _close_pf_selectonemenu_overlay(page, wid)
            return True
        except Exception:
            pass

        # Small filtered list only (after filter, usually ≤10).
        try:
            items = panel.locator("li.ui-selectonemenu-item:visible")
            n = min(items.count(), 12)
            for i in range(n):
                el = items.nth(i)
                txt = (el.inner_text(timeout=t_ms) or "").strip()
                if _screen_3_insurer_option_matches(insurer, txt):
                    el.click(timeout=t_ms)
                    _rto_log(f"pf-dropdown (overlay): Insurance Company = {txt}")
                    _pause()
                    _close_pf_selectonemenu_overlay(page, wid)
                    return True
        except Exception:
            continue

    try:
        _close_pf_selectonemenu_overlay(page, wid)
    except Exception:
        pass
    return False


def _screen_3_select_insurance_company_native_fuzzy(page: Page, insurer: str) -> bool:
    """Native ``ins_cd_input`` — prefer exact/normalized match; never bank/financer rows."""
    t_ms = _SCREEN3_INSURANCE_COMPANY_TIMEOUT_MS
    target_key = _screen_3_insurer_norm_key(insurer)
    for sel in _SCREEN3_INSURANCE_COMPANY_NATIVE:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=t_ms)
            options: list[str] = loc.evaluate(
                "el => [...el.options].map(o => (o.textContent || '').trim()).filter(Boolean)"
            )
            # Pass 1: exact / normalized equality.
            for opt in options:
                if _screen_3_is_select_placeholder_label(opt):
                    continue
                if _screen_3_insurer_option_is_finance_not_insurer(opt):
                    continue
                if target_key and _screen_3_insurer_norm_key(opt) == target_key:
                    loc.select_option(label=opt, timeout=t_ms)
                    _rto_log(f"pf-dropdown (native exact): Insurance Company = {opt}")
                    _pause()
                    return True
            # Pass 2: fuzzy rules (rejects AUTO FIN when target is Bajaj General).
            for opt in options:
                if _screen_3_insurer_option_matches(insurer, opt):
                    loc.select_option(label=opt, timeout=t_ms)
                    _rto_log(f"pf-dropdown (native fuzzy): Insurance Company = {opt}")
                    _pause()
                    return True
        except Exception:
            continue
    return False


def _screen_3_insurance_company_label_ok(page: Page, insurer: str) -> bool:
    """True when visible PF Insurance Company label matches the target."""
    current = _screen_3_read_insurance_company_label(page)
    return bool(current) and _screen_3_insurer_option_matches(insurer, current)


def _screen_3_select_insurance_company(page: Page, insurer: str) -> bool:
    """Set **Insurance Company** — overlay filter first (correct + fast), then native."""
    if _screen_3_select_insurance_company_overlay(page, insurer):
        if _screen_3_insurance_company_label_ok(page, insurer):
            return True
        _rto_log(
            "NOTE: Insurance Company overlay click did not update visible label — try native"
        )
    if _screen_3_select_insurance_company_native_fuzzy(page, insurer):
        # Native alone often leaves PF label stale — open/close overlay or re-click if needed.
        if _screen_3_insurance_company_label_ok(page, insurer):
            return True
        if _screen_3_select_insurance_company_overlay(page, insurer):
            return _screen_3_insurance_company_label_ok(page, insurer)
    return _screen_3_insurance_company_label_ok(page, insurer)


def _screen_3_pf_label_matches(page: Page, label_id: str, expected: str) -> bool:
    """True when visible PF label shows a non-placeholder value matching ``expected``."""
    try:
        lab = page.locator(f'[id="{label_id}"]').first
        t = (lab.inner_text(timeout=_screen3_timeout_ms()) or "").strip()
        if _screen_3_is_select_placeholder_label(t):
            return False
        exp = (expected or "").strip()
        if not exp:
            return bool(t)
        return exp.lower() in t.lower() or t.lower() in exp.lower()
    except Exception:
        return False


def _screen_3_nominee_relation_option_regex(rel_norm: str) -> re.Pattern[str]:
    """Case-insensitive match for Vahan nominee relation options (incl. Spouse for Wife/Husband)."""
    portal = _vahan_nominee_relation_portal_label(rel_norm)
    canon = (rel_norm or "").strip()
    if portal == "Spouse" and canon in ("Wife", "Husband"):
        return re.compile(r"Spouse", re.I)
    if not portal:
        return re.compile(r"^$")
    return re.compile(re.escape(portal), re.I)


def _screen_3_select_nominee_relation_from_options(page: Page, rel_norm: str) -> bool:
    """Scan native ``vm_rel1_input`` options and pick the best relation label match."""
    from app.services.utility_functions import fuzzy_best_option_label

    portal = _vahan_nominee_relation_portal_label(rel_norm)
    targets = [portal, (rel_norm or "").strip()]
    targets = [t for i, t in enumerate(targets) if t and t not in targets[:i]]

    sel = 'select[id="workbench_tabview:vm_rel1_input"]'
    try:
        loc = page.locator(sel).first
        loc.wait_for(state="attached", timeout=_LOOP_BUDGET_MS)
        options: list[dict] = loc.evaluate(
            """el => Array.from(el.options).map((o, i) => ({
                i,
                t: (o.textContent || '').trim(),
                v: (o.value || '').trim()
            }))"""
        )
        pick_idx: int | None = None
        for target in targets:
            tl_target = target.lower()
            for o in options:
                t = (o.get("t") or "").strip()
                if _screen_3_is_select_placeholder_label(t):
                    continue
                tl = t.lower()
                if tl == tl_target or tl_target in tl or tl in tl_target:
                    pick_idx = int(o["i"])
                    break
            if pick_idx is not None:
                break
        if pick_idx is None:
            labels = [
                (o.get("t") or "").strip()
                for o in options
                if not _screen_3_is_select_placeholder_label((o.get("t") or ""))
            ]
            for target in targets:
                matched = fuzzy_best_option_label(target, labels, min_score=0.5)
                if matched:
                    for o in options:
                        if (o.get("t") or "").strip() == matched:
                            pick_idx = int(o["i"])
                            break
                if pick_idx is not None:
                    break
        if pick_idx is not None:
            loc.select_option(index=pick_idx, force=True)
            _pause()
            picked = (options[pick_idx].get("t") or portal or rel_norm).strip()
            _rto_log(f"select: Relation with nominee = {picked!r} (native index)")
            return True
    except Exception as exc:
        logger.debug("fill_rto: nominee relation native scan: %s", exc)
    return False


def _screen_3_nominee_relation_label_ok(page: Page, label_id: str, rel_norm: str) -> bool:
    """Verify visible PF label — accept **Spouse** when DB has Wife/Husband."""
    portal = _vahan_nominee_relation_portal_label(rel_norm)
    if _screen_3_pf_label_matches(page, label_id, portal):
        return True
    if portal == "Spouse":
        return _screen_3_pf_label_matches(page, label_id, "Spouse")
    return _screen_3_pf_label_matches(page, label_id, rel_norm)


def _screen_3_select_nominee_relation(page: Page, rel_norm: str) -> bool:
    """Nominee relation — overlay click + visible label verify (native-only does not update PF UI)."""
    canon = (rel_norm or "").strip()
    if not canon:
        return False
    portal = _vahan_nominee_relation_portal_label(canon)
    if portal != canon:
        _rto_log(f"nominee relation portal label: {canon!r} → {portal!r}")
    label_id = "workbench_tabview:vm_rel1_label"
    wrapper_id = "workbench_tabview:vm_rel1"
    rx = _screen_3_nominee_relation_option_regex(canon)
    pick_label = portal or canon
    for attempt in range(2):
        ok = _screen_3_pf_dropdown_chain(
            page,
            _SCREEN3_NOMINEE_RELATION_PF_WRAPPERS,
            pick_label,
            label="Relation with nominee",
            option_label_regex=rx,
            use_native_select=False,
        )
        if not ok:
            ok = _screen_3_select_nominee_relation_from_options(page, canon)
        if ok and _screen_3_nominee_relation_label_ok(page, label_id, canon):
            _close_pf_selectonemenu_overlay(page, wrapper_id)
            return True
        if attempt == 0:
            _rto_log(
                "NOTE: Relation with nominee attempt 1 — visible label not verified, retry overlay"
            )
    if _screen_3_nominee_relation_label_ok(page, label_id, canon):
        return True
    _rto_log(
        f"WARNING: Relation with nominee not verified on visible label "
        f"(db={canon!r} portal={portal!r})"
    )
    return False


def _screen_3_select_financier_location_dropdown(
    page: Page,
    *,
    pf_wrappers: tuple[str, ...],
    native_selectors: tuple[str, ...],
    value: str,
    label: str,
    label_id: str,
    overlay_wrapper_id: str,
) -> bool:
    """Financier State/District — overlay click so visible label updates; verify + one retry."""
    disp = (value or "").strip()
    if not disp:
        return False
    for attempt in range(2):
        ok = _screen_3_pf_dropdown_chain(
            page, pf_wrappers, disp, label=label, use_native_select=False
        )
        if not ok:
            ok = _screen_3_native_select_chain(page, native_selectors, disp, label=label)
        if ok and _screen_3_pf_label_matches(page, label_id, disp):
            _close_pf_selectonemenu_overlay(page, overlay_wrapper_id)
            return True
        if attempt == 0:
            _rto_log(f"NOTE: {label} attempt 1 — visible label not verified, retry overlay")
    if _screen_3_pf_label_matches(page, label_id, disp):
        return True
    _rto_log(f"WARNING: {label} not verified on visible label (tried {disp!r})")
    _dump_page_state(page, f"dropdown not set: {label}")
    return False


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
    probe_ms = _screen3_timeout_ms()
    for lid in label_ids:
        try:
            lab = page.locator(f'[id="{lid}"]').first
            t = (lab.inner_text(timeout=probe_ms) or "").strip()
            if t and _SERIES_TYPE_STATE_SERIES_LABEL_RE.search(t):
                return True
        except Exception:
            continue
    for sel in _SCREEN3_SERIES_TYPE_NATIVE:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=probe_ms)
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
    probe_ms = _screen3_timeout_ms()
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=probe_ms)
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


def _screen_3_idv_field_is_blank_or_zero(page: Page) -> bool:
    """True when IDV is missing, zero, or decimal (needs (re)fill)."""
    for sel in _SCREEN3_IDV_INPUT:
        try:
            val = (page.locator(sel).first.input_value(timeout=1000) or "").strip()
            if not val:
                return True
            normalized = val.replace(",", "").strip()
            if normalized in ("0", "0.0", "0.00", "0.000"):
                return True
            if "." in normalized:
                whole, frac = normalized.split(".", 1)
                if whole.strip("0") == "" or frac.strip("0") == "":
                    return True
            return False
        except Exception:
            continue
    return True


def _screen_3_read_insurance_upto_value(page: Page) -> str:
    """Read visible **Insurance upto** field value."""
    for sel in _SCREEN3_INSURANCE_UPTO_INPUT:
        try:
            val = (page.locator(sel).first.input_value(timeout=1000) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


def _screen_3_insurance_upto_needs_fill(page: Page, expected: str) -> bool:
    """True when **Insurance upto** is blank or does not match the expected date."""
    exp = (expected or "").strip()
    if not exp:
        return False
    current = _screen_3_read_insurance_upto_value(page)
    if not current:
        return True
    exp_d = _parse_vahan_date(exp)
    cur_d = _parse_vahan_date(current)
    if exp_d and cur_d:
        return exp_d != cur_d
    return current.strip().lower() != exp.strip().lower()


def _screen_3_insurance_upto_input_enabled(page: Page) -> bool:
    """True when ``ins_upto_input`` is attached and not disabled (after Insurance Period AJAX)."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const el = document.getElementById('workbench_tabview:ins_upto_input');
                    if (!el) return false;
                    if (el.disabled) return false;
                    if ((el.className || '').includes('ui-state-disabled')) return false;
                    return true;
                }"""
            )
        )
    except Exception:
        return False


def _screen_3_wait_insurance_upto_enabled(
    page: Page, *, budget_ms: int = _SCREEN3_INS_UPTO_ENABLE_BUDGET_MS
) -> bool:
    """Poll until Insurance Upto is enabled after Period = 5 Year (short budget)."""
    if _screen_3_insurance_upto_input_enabled(page):
        return True
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < budget_ms:
        _wait_for_progress_close_loop(page, budget_ms=_FIRST_TRY_MS)
        if _screen_3_insurance_upto_input_enabled(page):
            return True
        time.sleep(_FIRST_TRY_MS / 1000.0)
    return _screen_3_insurance_upto_input_enabled(page)


def _screen_3_fill_insurance_upto_date(page: Page, date_str: str) -> bool:
    """Fill **Insurance upto** — wait until enabled, then JS set + verify once."""
    if not (date_str or "").strip():
        return False
    if not _screen_3_wait_insurance_upto_enabled(page):
        _rto_log("WARNING: Insurance Upto still disabled after Insurance Period")
        return False
    sel = _find_first_attached_selector(page, _SCREEN3_INSURANCE_UPTO_INPUT, timeout_ms=_FIRST_TRY_MS)
    if not sel:
        _rto_log("WARNING: Insurance Upto input not found in DOM")
        return False
    try:
        page.locator(sel).first.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
        _pause()
        _screen_3_fill_datepicker_js(
            page, sel, date_str, label="Insurance Upto", dump_on_failure=False
        )
    except PwTimeout:
        return False
    except Exception:
        return False
    if not _screen_3_insurance_upto_needs_fill(page, date_str):
        return True
    current = _screen_3_read_insurance_upto_value(page)
    _rto_log(
        f"WARNING: Insurance Upto not verified after fill "
        f"(expected {date_str!r}, current={current!r})"
    )
    return False


def _screen_3_read_nomination_date_value(page: Page) -> str:
    """Read visible **Nomination Date** field value."""
    for sel in _SCREEN3_NOMINATION_DATE_INPUT:
        try:
            val = (page.locator(sel).first.input_value(timeout=500) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


def _screen_3_nomination_date_needs_fill(page: Page, expected: str) -> bool:
    """True when nomination date is blank.

    Accepts either the expected billing/invoice date **or today's date** (Vahan often
    defaults nomination date to today).
    """
    current = _screen_3_read_nomination_date_value(page)
    if not current:
        return True
    cur_d = _parse_vahan_date(current)
    if cur_d is None:
        return True
    today = date.today()
    if cur_d == today:
        return False
    exp = (expected or "").strip()
    if not exp:
        return False
    exp_d = _parse_vahan_date(exp)
    if exp_d and cur_d == exp_d:
        return False
    return True


def _screen_3_fill_nomination_date(page: Page, date_str: str) -> bool:
    """Fill **Nomination Date** — prefer billing date; accept today on verify."""
    if not (date_str or "").strip():
        date_str = _fmt_date(date.today())
    if not _screen_3_nomination_date_needs_fill(page, date_str):
        current = _screen_3_read_nomination_date_value(page)
        _rto_log(f"skip: Nomination Date already set ({current!r})")
        return True
    sel = _find_first_attached_selector(page, _SCREEN3_NOMINATION_DATE_INPUT, timeout_ms=_FIRST_TRY_MS)
    if not sel:
        _rto_log("WARNING: Nomination Date input not found in DOM")
        return False
    # Prefer today when billing date is older — matches portal default / operator expectation.
    candidates = [_fmt_date(date.today()), date_str]
    seen: set[str] = set()
    for candidate in candidates:
        c = (candidate or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            page.locator(sel).first.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
            _pause()
            _screen_3_fill_datepicker_js(
                page, sel, c, label="Nomination Date", dump_on_failure=False
            )
        except PwTimeout:
            continue
        except Exception:
            continue
        if not _screen_3_nomination_date_needs_fill(page, date_str):
            return True
    current = _screen_3_read_nomination_date_value(page)
    _rto_log(
        f"WARNING: Nomination Date not verified after fill "
        f"(expected billing={date_str!r} or today, current={current!r})"
    )
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
    if not RTO_FILL_LOG_VERBOSE:
        try:
            snap = page.evaluate(
                """() => {
                    const inp = document.getElementById("workbench_tabview:isHypo_input");
                    const hpa = document.getElementById("workbench_tabview:hpa");
                    return {
                        isHypoChecked: inp ? !!inp.checked : null,
                        hpaControlCount: hpa
                            ? hpa.querySelectorAll("input, select, textarea").length
                            : 0,
                        nomineeTable: !!document.getElementById("workbench_tabview:nomineeradiobtn1"),
                    };
                }"""
            )
            _rto_log(f"hypo wiring (compact): {snap!r}")
        except Exception as e:
            _rto_log(f"hypo wiring (compact) failed: {e!s}")
        return
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
        _rto_log_verbose("hpa: financier name input visible")
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


def _screen_3_fill_datepicker_js(
    page: Page, sel: str, date_str: str, *, label: str, dump_on_failure: bool = True
) -> None:
    """Fill a ``hasDatepicker`` field via JS — mirrors ``_fill_workbench_purchase_date``."""
    if not date_str:
        return
    loc = page.locator(sel).first
    attach_ms = _SCREEN3_LOOP_BUDGET_MS if _current_screen.get() == "Screen 3" else _LOOP_BUDGET_MS
    try:
        loc.wait_for(state="attached", timeout=attach_ms)
        loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)

        try:
            loc.evaluate(
                """(el, v) => {
                    el.focus();
                    el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    try {
                        if (window.jQuery && typeof jQuery.fn.datepicker === 'function') {
                            const $el = jQuery(el);
                            if ($el.hasClass('hasDatepicker') || $el.data('datepicker')) {
                                $el.datepicker('setDate', v);
                                $el.datepicker('hide');
                            }
                        }
                    } catch (e) {}
                }""",
                date_str,
            )
        except Exception:
            try:
                loc.fill(date_str, timeout=_screen3_timeout_ms())
            except Exception:
                loc.fill(date_str, timeout=_screen3_timeout_ms(), force=True)

        _pause()
        _close_workbench_datepicker_if_open(page)
    except PwTimeout:
        _assert_vahan_session_alive(page)
        _rto_log(f"TIMEOUT fill: {label} selector={sel} value={date_str[:40]}")
        if dump_on_failure:
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
        _screen_3_select_financier_location_dropdown(
            page,
            pf_wrappers=_SCREEN3_FIN_STATE_PF_WRAPPERS,
            native_selectors=_SCREEN3_FIN_STATE_NATIVE,
            value=st_disp,
            label="Financier State",
            label_id="workbench_tabview:hpa_fncr_state_label",
            overlay_wrapper_id="workbench_tabview:hpa_fncr_state",
        )
        # State AJAX populates district options — wait for progress overlay to close
        _wait_for_progress_close_loop(page)

    # District — dealer RTO place name (e.g. Bharatpur), not village/city (e.g. Barakhur).
    dist_disp = _resolve_vahan_district(data)
    if dist_disp:
        _screen_3_select_financier_location_dropdown(
            page,
            pf_wrappers=_SCREEN3_FIN_DISTRICT_PF_WRAPPERS,
            native_selectors=_SCREEN3_FIN_DISTRICT_NATIVE,
            value=dist_disp,
            label="Financier District",
            label_id="workbench_tabview:hpa_fncr_district_label",
            overlay_wrapper_id="workbench_tabview:hpa_fncr_district",
        )

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
    _screen_3_dump_insurance_tab_field_inventory(page, phase="after nominee Yes")

    # Wait for nominee name input to appear (AJAX loads the panel after Yes click)
    nm_loc = page.locator(_SCREEN3_NOMINEE_NAME_INPUT[0]).first
    try:
        nm_loc.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
        _rto_log_verbose("nominee: name input visible after Yes click")
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
        try:
            rel_loc = page.locator(_SCREEN3_NOMINEE_RELATION_PF_WRAPPERS[0]).first
            rel_loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
            _pause()
        except Exception:
            pass
        if not _screen_3_select_nominee_relation(page, rel_norm):
            portal = _vahan_nominee_relation_portal_label(rel_norm)
            _screen_3_fail_3c_required_field(
                page,
                "Relation with nominee",
                f"db={rel_norm!r} portal={portal!r}",
            )

    # Nomination Date — billing/invoice preferred; today is also accepted (Vahan default).
    nom_date = (data.get("invoice_date_str") or data.get("billing_date_str") or "").strip()
    if want_nominee:
        if not nom_date:
            nom_date = _fmt_date(date.today())
        if not _screen_3_fill_nomination_date(page, nom_date):
            current = _screen_3_read_nomination_date_value(page)
            _screen_3_fail_3c_required_field(
                page,
                "Nomination Date",
                f"expected billing={nom_date!r} or today, current={current!r}",
            )
        # Nomination date can open Are you sure / residual info popups — dismiss quickly.
        for btn in ("Yes", "OK", "Ok", "Close"):
            _dismiss_dialog_if_present(page, btn, timeout_ms=_FIRST_TRY_MS)


def _apply_rto_fill_screen3c_run_overrides(data: dict) -> None:
    """Apply module-level Screen 3c run-only overrides into ``data`` (dev/testing)."""
    if RTO_FILL_INSURANCE_FROM_USE_YESTERDAY:
        data["policy_from_str"] = _fmt_date(date.today() - timedelta(days=1))
        data["force_policy_from"] = True
        _rto_log(f"override: Insurance From = yesterday ({data['policy_from_str']})")
    portal = (RTO_FILL_INSURER_PORTAL_LABEL or "").strip()
    if portal:
        data["insurer_portal_label"] = portal
        data["force_insurer_reselect"] = True
        _rto_log(f"override: Insurance Company portal label = {portal!r}")
    elif RTO_FILL_FORCE_INSURER_RESELECT:
        data["force_insurer_reselect"] = True
    if RTO_FILL_IDV_OVERRIDE is not None:
        data["idv"] = int(RTO_FILL_IDV_OVERRIDE)
        data["force_idv"] = True
        _rto_log(f"override: IDV = {data['idv']}")


def _screen_3c_insurance_information(page: Page, data: dict) -> None:
    """3c: On Hypothecation/Insurance tab — insurance fields, Series Type (STATE SERIES), IDV."""
    _rto_log("--- Screen 3c: Insurance (Hypothecation/Insurance Information tab) ---")
    _screen_3_dump_insurance_tab_field_inventory(page, phase="3c start")
    for scroll_sel in (
        _SCREEN3_INSURANCE_TYPE_PF_WRAPPERS[0],
        _SCREEN3_INSURANCE_TYPE_NATIVE[0],
    ):
        try:
            loc = page.locator(scroll_sel).first
            loc.wait_for(state="attached", timeout=_FIRST_TRY_MS)
            loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
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
                        timeout=_screen3_timeout_ms(),
                    )
                except PwTimeout:
                    _rto_log("WARNING: Insurance Type not set (THIRD PARTY)")

    insurer_target = (data.get("insurer_portal_label") or data.get("insurer") or "").strip()
    force_insurer = bool(data.get("force_insurer_reselect"))
    if insurer_target:
        current_insurer = _screen_3_read_insurance_company_label(page)
        already_ok = (
            current_insurer
            and _screen_3_insurer_option_matches(insurer_target, current_insurer)
        )
        if force_insurer or not already_ok:
            if current_insurer and not already_ok:
                _rto_log(
                    f"Insurance Company re-select: visible={current_insurer!r} "
                    f"→ target={insurer_target!r}"
                )
            if not _screen_3_select_insurance_company(page, insurer_target):
                _screen_3_fail_3c_required_field(
                    page, "Insurance Company", f"target={insurer_target!r}"
                )
        else:
            _rto_log(f"skip: Insurance Company already correct ({current_insurer!r})")
    elif _screen_3_insurance_company_already_set(page):
        _rto_log("skip: Insurance Company already set on form (Vahan pre-fill; no insurer in queue data)")

    if _screen_3_input_already_has_value(page, _SCREEN3_POLICY_NO_INPUT):
        _rto_log("skip: Policy/Cover Note No. already set on form (Vahan pre-fill)")
    else:
        _fill_first_matching(
            page, _SCREEN3_POLICY_NO_INPUT, data.get("policy_num", ""), label="Policy/Cover Note No."
        )

    policy_from = (data.get("policy_from_str") or "").strip()
    force_policy_from = bool(data.get("force_policy_from"))
    if policy_from:
        if force_policy_from or not _screen_3_input_already_has_value(page, _SCREEN3_INSURANCE_FROM_INPUT):
            try:
                _screen_3_fill_datepicker_js(
                    page, _SCREEN3_INSURANCE_FROM_INPUT[0], policy_from, label="Insurance From"
                )
            except PwTimeout:
                _rto_log("WARNING: Insurance From not filled (datepicker)")
        else:
            _rto_log("skip: Insurance From already set on form (Vahan pre-fill)")

    if _screen_3_insurance_period_is_target(page):
        _rto_log(f"skip: Insurance Period already set ({_INSURANCE_PERIOD_LABEL})")
        _screen_3_dismiss_post_insurance_period_dialog(page)
    else:
        period_set = False
        # Native first — faster than PF overlay on Screen 3c.
        if _screen_3_native_select_chain(
            page,
            _SCREEN3_INSURANCE_PERIOD_NATIVE,
            _INSURANCE_PERIOD_LABEL,
            label="Insurance Period (in year)",
        ):
            period_set = True
        elif _screen_3_pf_dropdown_chain(
            page,
            _SCREEN3_INSURANCE_PERIOD_PF_WRAPPERS,
            _INSURANCE_PERIOD_LABEL,
            label="Insurance Period (in year)",
        ):
            period_set = True
            _close_pf_selectonemenu_overlay(page, "workbench_tabview:ins_year")
        else:
            _rto_log(f"WARNING: Insurance Period not set ({_INSURANCE_PERIOD_LABEL})")
            _dump_page_state(page, "dropdown not set: Insurance Period")
        _wait_for_progress_close_loop(page)
        _screen_3_dismiss_post_insurance_period_dialog(page)
        if not period_set:
            pass  # still try upto if Vahan enabled it from a partial set

    policy_upto = _resolve_policy_upto_str(data)
    if not policy_upto:
        from_display = _screen_3_read_insurance_from_value(page)
        from_d = _parse_vahan_date(from_display)
        if from_d:
            policy_upto = _insurance_upto_from_from_date(from_d)
    if policy_upto:
        if _screen_3_insurance_upto_needs_fill(page, policy_upto):
            if not _screen_3_fill_insurance_upto_date(page, policy_upto):
                _screen_3_fail_3c_required_field(
                    page, "Insurance Upto", f"expected {policy_upto!r}"
                )
        else:
            _rto_log(f"skip: Insurance Upto already set on form ({policy_upto!r})")

    # Series Type (*Please Select Series Type*) → **STATE SERIES** (skip if already STATE SERIES).
    for scroll_sel in (_SCREEN3_SERIES_TYPE_PF_WRAPPERS[0], _SCREEN3_SERIES_TYPE_NATIVE[0]):
        try:
            s_loc = page.locator(scroll_sel).first
            s_loc.wait_for(state="attached", timeout=_FIRST_TRY_MS)
            s_loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
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
                    nloc.wait_for(state="visible", timeout=_screen3_timeout_ms())
                    nloc.select_option(
                        label=_SERIES_TYPE_STATE_SERIES_LABEL_RE, timeout=_screen3_timeout_ms()
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

    idv_s = _normalize_idv_for_vahan(data.get("idv"))
    if idv_s:
        needs_idv = bool(data.get("force_idv")) or _screen_3_idv_field_is_blank_or_zero(page)
        if needs_idv:
            if data.get("force_idv"):
                _rto_log(f"IDV override fill: {idv_s}")
            if not _fill_idv_integer(page, _SCREEN3_IDV_INPUT, idv_s, label="Insurance Declared Value"):
                _rto_log("WARNING: Insurance Declared Value not filled")
        else:
            _rto_log("skip: Insurance Declared Value already set on form (Vahan pre-fill)")
    elif _screen_3_idv_field_is_blank_or_zero(page):
        _rto_log("WARNING: Insurance Declared Value is blank/zero but idv missing in queue/insurance data")

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


def _screen_3_scrape_generated_application_id(page: Page) -> tuple[str, str]:
    """Read application number from post-save dialog; returns ``(application_id, dialog_text)``.

    Short wait (~700ms) — missing id is OK when dialog is a non-validation proceed signal.
    """
    application_id = ""
    dialog_text = ""
    scrape_timeout_ms = max(_SCREEN3_ACTION_TIMEOUT_MS, 700)
    dlg = _screen_3_find_visible_application_info_dialog(page)
    if dlg is not None:
        try:
            text = dlg.inner_text(timeout=scrape_timeout_ms) or ""
            dialog_text = text
            application_id = _scrape_application_id_from_dialog_text(text)
            if application_id:
                logger.info("fill_rto: scraped application_id=%s", application_id)
                _rto_log(f"scraped: rto_application_id = {application_id} (visible ui-dialog)")
                return application_id, dialog_text
            _rto_log(f"Screen 3d: application dialog snippet (unparsed): {text[:900]!r}")
            return application_id, dialog_text
        except Exception as e:
            logger.debug("fill_rto: scrape from application dialog: %s", e)
    try:
        dialog_text_loc = page.locator(
            ".ui-dialog-content:visible, .ui-dialog:visible, .ui-messages-info, .ui-growl-message, "
            "[class*='dialog'] [class*='message'], [class*='success']"
        ).first
        dialog_text_loc.wait_for(state="visible", timeout=scrape_timeout_ms)
        text = dialog_text_loc.inner_text()
        dialog_text = text or dialog_text
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
            if _screen_3_dialog_is_validation_alert(text):
                errs = _screen_3_extract_validation_error_messages(text)
                if errs:
                    _rto_log(f"Screen 3d: validation errors in dialog: {errs!r}")
    except PwTimeout:
        logger.warning("fill_rto: could not scrape application number from popup")
        _rto_log("WARNING: could not scrape application number from popup")
        # No dump here — caller may still proceed on a numbers-only dialog already read, or fail cleanly.
    return application_id, dialog_text


def _screen_3d_hypothecation_save_confirm_scrape(page: Page, data: dict) -> str:
    """3d: Save → Yes → Entry Details → Generated Application No (scrape + Ok) → ``rto_status=2``."""
    _rto_log("--- Screen 3d: Save and File Movement, confirmation popups ---")

    _screen_3_click_save_file_movement(page)
    _wait_for_progress_close_loop(page)
    # First confirm is a plain **Yes** — do not match **Are You Sure?** (Entry Details).
    if _dismiss_dialog_if_present(page, r"^\s*Yes\s*$"):
        _rto_log("Screen 3d: confirmation — Yes (Are you sure)")
        _wait_for_progress_close_loop(page)

    # Entry Details modal can lag ~500ms after the first Yes.
    time.sleep(_SCREEN3_ENTRY_DETAILS_WAIT_S)
    entry_text, entry_confirmed = _screen_3d_confirm_entry_details_if_present(page)

    if _screen_3_dialog_is_validation_alert(entry_text) or _screen_3_extract_validation_error_messages(
        entry_text
    ):
        _screen_3_fail_save_and_file_movement(entry_text)

    _wait_for_progress_close_loop(page)

    # Third popup: **Generated Application No** — scrape id, click Ok, persist ``rto_status=2``.
    scraped_id = (_dismiss_generated_application_no_dialog(page) or "").strip()
    if not scraped_id:
        application_id, dialog_text = _screen_3_scrape_generated_application_id(page)
        scraped_id = (application_id or "").strip()
        combined_text = (dialog_text or entry_text or "").strip()
        if scraped_id:
            _dismiss_dialog_if_present(page, "Ok", timeout_ms=_SCREEN3_INSURANCE_COMPANY_TIMEOUT_MS)
            _dismiss_dialog_if_present(page, "OK", timeout_ms=300)
            _wait_for_progress_close_loop(page)
        elif _screen_3_dialog_is_validation_alert(combined_text) or _screen_3_extract_validation_error_messages(
            combined_text
        ):
            _screen_3_fail_save_and_file_movement(combined_text)

    existing_id = (data.get("rto_application_id") or "").strip()
    if scraped_id:
        data["rto_application_id"] = scraped_id
        _persist_rto_queue_progress(
            data,
            rto_status=RTO_STATUS_AFTER_SCREEN3D,
            rto_application_id=scraped_id,
        )
        _rto_log(f"Screen 3d: Generated Application No — rto_status={RTO_STATUS_AFTER_SCREEN3D}, id={scraped_id}")
        return scraped_id

    # Fallback: Entry Details confirmed but no Generated Application dialog (older builds).
    if (
        entry_confirmed
        or entry_text
        or _screen_3d_is_entry_details_dialog_text(entry_text)
        or _screen_3d_dialog_is_proceed_signal(entry_text)
    ):
        _rto_log(
            "Screen 3d: proceed without scraped application id "
            f"(dialog snippet={(entry_text or '')[:200]!r})"
        )
        _persist_rto_queue_progress(
            data,
            rto_status=RTO_STATUS_AFTER_SCREEN3D,
            rto_application_id=existing_id or None,
        )
        return existing_id

    _rto_log("Screen 3d: no proceed dialog after Save and File Movement — treating as failure")
    _screen_3_fail_save_and_file_movement(entry_text)
    return ""  # unreachable


def _screen_3(
    page: Page,
    data: dict,
    *,
    skip_home: bool,
    skip_entry: bool = False,
    resume_at_3b: bool = False,
) -> str:
    """Screen 3: optional Home → Entry, Tax mode, Insurance, Hypothecation, Save. Returns application_id.

    ``skip_home``: when True (skip point at screen 3, or ``RTO_FILL_SCREEN3_SKIP_HOME``), do not click Home.
    ``skip_entry``: when True (only valid with ``skip_home``), do not click Entry—already on the post-Entry form;
    activate **Vehicle Details** sub-tab only.
    ``resume_at_3b``: when True (DB resume after pending-work nav), skip 3a and start at Vehicle Details tab.
    """
    _set_screen("Screen 3")
    logger.info("fill_rto: Screen 3 — insurer=%s", data.get("insurer", "")[:20])
    _rto_log("--- Screen 3: Home, Entry, tax, insurance, hypothecation, application no ---")

    start_at_3b = resume_at_3b or (_vahan_on_workbench(page) and skip_home)

    # 3a: Home (unless skip point) → Entry on pending-work grid (unless skip_entry / workbench resume).
    if start_at_3b:
        _rto_log("Screen 3 resume: workbench — start at Vehicle Details (3b), skip 3a Home/Entry")
    elif skip_home:
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

    if not skip_entry and not start_at_3b:
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

    _screen_3_dismiss_policy_no_blank_alert(page)

    _screen_3_click_save_vehicle_details(page)
    _screen_3_dismiss_policy_no_blank_alert(page)
    _screen_3_clear_blocking_overlay_after_vehicle_save(page, data)

    # 3c: Scroll to sub-tab strip, open **Hypothecation/Insurance Information**, then fill insurance.
    _screen_3_scroll_subtab_bar_into_view(page)
    _screen_3_open_hypothecation_insurance_tab(page)
    _screen_3c_insurance_information(page, data)

    # 3d: Save and File Movement, Yes / Yes, scrape app no., OK (hypothecation / nominee handled in 3c).
    return _screen_3d_hypothecation_save_confirm_scrape(page, data)


def _screen_4_office_remarks_needs_none(value: str) -> bool:
    """True when Office Remarks is empty or still the portal placeholder — fill **NONE**."""
    v = (value or "").strip()
    if not v:
        return True
    return bool(re.match(r"^(OFFICE\s*REMARK\s*\?|NONE|None)$", v, re.I))


def _screen_4_find_file_movement_dialog(page: Page, *, quick: bool = False) -> Locator | None:
    """Visible **File Movement** modal after Save-Options → File Movement."""
    deadline = time.monotonic() + (0.35 if quick else 3.0)
    while time.monotonic() < deadline:
        try:
            loc = page.locator(".ui-dialog:visible, [role='dialog']:visible").filter(
                has_text=re.compile(r"File\s*Movement", re.I)
            )
            if loc.count() > 0 and loc.first.is_visible(timeout=400):
                return loc.first
        except Exception:
            pass
        if quick:
            break
        time.sleep(0.12)
    return None


def _screen_4_file_movement_dialog_visible(page: Page) -> bool:
    return _screen_4_find_file_movement_dialog(page, quick=True) is not None


def _screen_4_wait_file_movement_dialog_closed(
    page: Page, *, budget_ms: int = 10_000
) -> bool:
    """Poll until the File Movement modal is gone."""
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < budget_ms:
        if not _screen_4_file_movement_dialog_visible(page):
            return True
        _wait_for_progress_close_loop(page, budget_ms=_FIRST_TRY_MS)
        time.sleep(0.2)
    return not _screen_4_file_movement_dialog_visible(page)


def _screen_4_fill_file_movement_office_remarks(dlg: Locator) -> None:
    """Fill **Office Remarks** with ``NONE`` when empty or placeholder."""
    for sel in ("textarea", "input[type='text']"):
        try:
            inp = dlg.locator(sel).first
            inp.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            current = (inp.input_value() or "").strip()
            if not current:
                try:
                    current = (
                        inp.evaluate("el => (el.value || el.textContent || '').trim()") or ""
                    ).strip()
                except Exception:
                    current = ""
            if not _screen_4_office_remarks_needs_none(current):
                _rto_log(f"skip: Office Remarks already set ({current[:80]!r})")
                return
            inp.fill("NONE")
            _rto_log("fill: File Movement — Office Remarks = NONE")
            return
        except Exception:
            continue
    _rto_log("WARNING: File Movement — Office Remarks input not found in dialog")


def _screen_4_click_file_movement_dialog_save(dlg: Locator, page: Page) -> bool:
    """Click **Save** inside the File Movement modal only (never page-level Save-Options).

    Vahan puts Save in dialog **content** as ``<a class="ui-commandlink ui-button">Save</a>``,
    not in ``.ui-dialog-buttonpane``.
    """
    save_re = re.compile(r"^\s*Save\s*$", re.I)

    def _try_click_save_on_dialog(dialog: Locator) -> bool:
        attempts: tuple[tuple[str, Locator], ...] = (
            (
                "content ui-commandlink Save",
                dialog.locator(
                    ".ui-dialog-content a.ui-commandlink.ui-button, "
                    ".ui-dialog-content a.ui-button"
                ).filter(has_text=save_re).first,
            ),
            (
                "dialog a.ui-button Save",
                dialog.locator("a.ui-commandlink.ui-button, a.ui-button").filter(has_text=save_re).first,
            ),
            (
                "buttonpane exact Save",
                dialog.locator(
                    ".ui-dialog-buttonpane button, .ui-dialog-buttonpane .ui-button, "
                    ".ui-dialog-buttonpane a.ui-button, "
                    ".ui-dialog-buttonpane input[type='button'], "
                    ".ui-dialog-buttonpane input[type='submit']"
                ).filter(has_text=save_re).first,
            ),
            (
                "ui-button-text Save",
                dialog.locator(".ui-button-text").filter(has_text=save_re).first.locator(
                    "xpath=ancestor::button[1] | ancestor::a[1]"
                ),
            ),
        )
        for label, btn in attempts:
            try:
                btn.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
                btn.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
                btn.click(timeout=_DEFAULT_TIMEOUT_MS)
                _rto_log(f"dialog: File Movement — Save ({label})")
                return True
            except Exception:
                continue
        return False

    if _try_click_save_on_dialog(dlg):
        return True

    for fi, frame in enumerate(page.frames):
        try:
            fm = frame.locator(".ui-dialog:visible, [role='dialog']:visible").filter(
                has_text=re.compile(r"File\s*Movement", re.I)
            ).first
            if fm.count() == 0 or not fm.is_visible(timeout=300):
                continue
            if _try_click_save_on_dialog(fm):
                _rto_log(f"dialog: File Movement — Save (frame[{fi}])")
                return True
        except Exception:
            continue

    for fi, frame in enumerate(page.frames):
        try:
            clicked = bool(
                frame.evaluate(
                    """() => {
                        const dlg = [...document.querySelectorAll('.ui-dialog, [role="dialog"]')]
                            .find(d => /File\\s*Movement/i.test(d.innerText || ''));
                        if (!dlg) return false;
                        const candidates = dlg.querySelectorAll(
                            'a.ui-commandlink.ui-button, a.ui-button, button, input[type="button"], input[type="submit"]'
                        );
                        for (const b of candidates) {
                            const t = (b.innerText || b.textContent || b.value || '').trim();
                            if (/^Save$/i.test(t)) {
                                b.click();
                                return true;
                            }
                        }
                        return false;
                    }"""
                )
            )
            if clicked:
                _rto_log(f"dialog: File Movement — Save (JS frame[{fi}])")
                return True
        except Exception:
            continue

    _rto_log("WARNING: File Movement — Save button not clicked inside dialog")
    return False


def _screen_4_dismiss_post_save_yes_dialog(page: Page) -> None:
    """After File Movement **Save**: dismiss confirmation **Yes** when the portal asks to proceed."""
    yes_re = re.compile(r"^\s*Yes\s*$", re.I)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        try:
            dlg = page.locator(".ui-dialog:visible, [role='dialog']:visible").first
            if dlg.is_visible(timeout=_FIRST_TRY_MS):
                btn = dlg.locator(
                    ".ui-dialog-buttonpane button, .ui-dialog-buttonpane .ui-button, button"
                ).filter(has_text=yes_re).first
                if btn.count() > 0 and btn.is_visible(timeout=_FIRST_TRY_MS):
                    btn.click(timeout=_DEFAULT_TIMEOUT_MS)
                    _rto_log("dialog: File Movement post-Save — Yes (in dialog)")
                    _wait_for_progress_close_loop(page)
                    return
        except Exception:
            pass
        if _dismiss_dialog_if_present(page, r"^\s*Yes\s*$", timeout_ms=_FIRST_TRY_MS):
            _rto_log("dialog: File Movement post-Save — Yes")
            _wait_for_progress_close_loop(page)
            return
        time.sleep(0.12)


def _screen_4_dismiss_followup_save_dialog(page: Page) -> None:
    """Optional second Save confirmation — scoped to a visible dialog, not Save-Options."""
    try:
        dialogs = page.locator(".ui-dialog:visible, [role='dialog']:visible")
        if dialogs.count() == 0:
            return
        dlg = dialogs.first
        text = (dlg.inner_text(timeout=1000) or "")
        if re.search(r"File\s*Movement", text, re.I):
            return
        save_re = re.compile(r"^\s*Save\s*$", re.I)
        btn = dlg.locator(
            ".ui-dialog-buttonpane button, .ui-dialog-buttonpane .ui-button, button"
        ).filter(has_text=save_re).first
        btn.wait_for(state="visible", timeout=_FIRST_TRY_MS)
        btn.click(timeout=_DEFAULT_TIMEOUT_MS)
        _rto_log("dialog: File Movement follow-up — Save")
        _wait_for_progress_close_loop(page)
    except Exception:
        pass


def _screen_4_file_movement_dialogs(page: Page) -> None:
    """After **File Movement**: Office Remarks ``NONE`` → **Save** → **Yes** → wait until modal closes."""
    _pause()
    _wait_for_progress_close_loop(page)
    dlg = _screen_4_find_file_movement_dialog(page)
    if dlg is None:
        try:
            dlg = page.locator(".ui-dialog:visible, [role='dialog']:visible").first
            dlg.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
            snippet = (dlg.inner_text(timeout=1000) or "")[:200]
            if not re.search(r"File\s*Movement", snippet, re.I):
                _rto_log(f"WARNING: visible dialog is not File Movement: {snippet!r}")
        except PwTimeout:
            _rto_log("WARNING: File Movement — dialog not visible within budget")
            _dump_page_state(page, "File Movement first dialog missing")
            return

    _screen_4_fill_file_movement_office_remarks(dlg)
    _pause()

    if not _screen_4_click_file_movement_dialog_save(dlg, page):
        raise RuntimeError("File Movement — could not click Save inside dialog")

    _pause()
    _wait_for_progress_close_loop(page)
    _screen_4_dismiss_post_save_yes_dialog(page)
    _pause()
    _wait_for_progress_close_loop(page)

    if not _screen_4_wait_file_movement_dialog_closed(page):
        dlg_retry = _screen_4_find_file_movement_dialog(page, quick=True)
        if dlg_retry is not None:
            _rto_log("WARNING: File Movement still open — retry Save")
            if not _screen_4_click_file_movement_dialog_save(dlg_retry, page):
                raise RuntimeError("File Movement — could not click Save inside dialog (retry)")
            _pause()
            _wait_for_progress_close_loop(page)
            _screen_4_dismiss_post_save_yes_dialog(page)
            _pause()
            _wait_for_progress_close_loop(page)
        if not _screen_4_wait_file_movement_dialog_closed(page):
            _dump_page_state(page, "File Movement dialog still open after Save")
            raise RuntimeError("File Movement dialog still open after Save")

    _screen_4_dismiss_followup_save_dialog(page)
    _pause()
    _wait_for_progress_close_loop(page)


def _screen_4_documents_upload_url(url: str) -> bool:
    """True when the browser is on Vahan **Documents Upload** (Screen 5 entry)."""
    u = (url or "").lower()
    return "form_documents_upload" in u or "documentupload" in u


def _screen_5_upload_form_markers_visible(page: Page) -> bool:
    """True when Sub Category, chevron, and document counter are visible."""
    try:
        page.locator(_SCREEN5_PF_SUBCAT_WRAPPER).first.wait_for(state="visible", timeout=_FIRST_TRY_MS)
        page.locator(_SCREEN5_NEXT_BTN).first.wait_for(state="visible", timeout=_FIRST_TRY_MS)
        page.get_by_text(_SCREEN5_DOCUMENT_COUNTER_RE).first.wait_for(state="visible", timeout=_FIRST_TRY_MS)
        return True
    except Exception:
        return False


def _screen_5_wait_for_upload_form_ready(page: Page, *, budget_ms: int = 5000) -> bool:
    """Poll until the document-upload carousel is interactive, then settle."""
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < budget_ms:
        _wait_for_progress_close(page)
        if _screen_5_upload_form_markers_visible(page):
            time.sleep(_SCREEN5_UPLOAD_FORM_SETTLE_MS / 1000.0)
            _rto_log("Screen 5: upload form ready")
            return True
        time.sleep(0.15)
    if _screen_5_upload_form_markers_visible(page):
        time.sleep(_SCREEN5_UPLOAD_FORM_SETTLE_MS / 1000.0)
        _rto_log("Screen 5: upload form ready (final probe)")
        return True
    _rto_log("WARNING: upload form not ready within budget")
    return False


def _screen_4_on_documents_upload_page(page: Page) -> bool:
    """True when the dealer document upload form is loaded and interactive."""
    try:
        on_url = _screen_4_documents_upload_url(page.url or "")
    except Exception:
        on_url = False
    if not on_url and not _screen_5_upload_form_markers_visible(page):
        return False
    return _screen_5_wait_for_upload_form_ready(page)


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


def _screen_4(page: Page, *, skip_verify: bool = False) -> None:
    """Screen 4: Verify → … → Save-Options → File Movement → (None + Save) → (Save) → Dealer Document Upload tab → progress wheel."""
    _set_screen("Screen 4")
    logger.info("fill_rto: Screen 4 — Verify & Document Upload nav")
    _rto_log("--- Screen 4: Verify, Save Options / File Movement, Dealer Document Upload ---")

    if _screen_4_on_documents_upload_page(page):
        _rto_log("SKIP: Screen 4 — already on Documents Upload page")
        _pause()
        _wait_for_progress_close_loop(page)
        return

    if not RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD:
        # 4a: Scroll down and click **Verify** (skip if flag set or already clicked from pending grid)
        if skip_verify or RTO_FILL_SCREEN4_SKIP_VERIFY:
            reason = "pending-grid Verify" if skip_verify else "RTO_FILL_SCREEN4_SKIP_VERIFY=True"
            _rto_log(f"SKIP: Verify ({reason})")
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

    if _screen_4_file_movement_dialog_visible(page):
        _rto_log("WARNING: File Movement still open before Dealer Document Upload — retry dialog Save")
        _screen_4_file_movement_dialogs(page)

    # 4d: **Dealer Document Upload** — or skip when File Movement already opened the upload page.
    if _screen_4_on_documents_upload_page(page):
        _rto_log(
            "SKIP: Dealer Document Upload — already on Documents Upload page "
            f"({(page.url or '')[:120]})"
        )
    else:
        # column (text ``Dealer-Document-Upload`` with hyphens; id like ``workDetails:0:j_idt273``).  On
        # ``workbench.xhtml`` it may be a top tab.  Scroll to bottom first — the grid action is below the fold.
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


def _screen_5_escape_overlays(page: Page) -> None:
    """Dismiss open Sub Category / PF panels so we do not stay stuck in the cell (Escape)."""
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
        except Exception:
            break
        _pause()


def _screen_5_read_document_slot(page: Page) -> tuple[str, int, int]:
    """Return ``(document_title, index, total)`` from the Documents Upload carousel."""
    title = ""
    index = 1
    total = 20
    try:
        snap = page.evaluate(
            """() => {
                const clip = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
                const body = clip(document.body.innerText || '');
                let index = 1, total = 20, title = '';
                const m = body.match(/Document\\s*\\(\\s*(\\d+)\\s*of\\s*(\\d+)\\s*\\)/i);
                if (m) {
                    index = parseInt(m[1], 10);
                    total = parseInt(m[2], 10);
                }
                const prefer = (v) => {
                    const t = clip(v);
                    if (!t || /^-+\\s*SELECT/i.test(t) || t.length > 80) return false;
                    return /FORM\\s*\\d+|INSURANCE|INVOICE|AADHAAR|AADHAR|UNDERTAKING|AFFIDAVIT|AFFADEVIT|PARKING|SALE\\s*CERTIFICATE|PROOF\\s*OF\\s*ADDRESS/i.test(t);
                };
                for (const el of document.querySelectorAll('input, textarea')) {
                    if (prefer(el.value)) { title = clip(el.value); break; }
                }
                if (!title) {
                    for (const el of document.querySelectorAll(
                        '[id*="doc"], [id*="Doc"], [id*="document"], [id*="Document"]'
                    )) {
                        const v = el.value != null ? el.value : (el.innerText || el.textContent);
                        if (prefer(v)) { title = clip(v); break; }
                    }
                }
                if (!title && m) {
                    // Text after "Document (N of M)" often includes the slot name on the next line.
                    const after = body.slice(body.indexOf(m[0]) + m[0].length, body.indexOf(m[0]) + m[0].length + 120);
                    const line = clip(after).split(/Sub\\s*Category|Upload\\s*Document|Owner\\s*Details/i)[0];
                    if (prefer(line)) title = clip(line);
                }
                return { title, index, total };
            }"""
        )
        title = ((snap or {}).get("title") or "").strip()
        index = int((snap or {}).get("index") or 1)
        total = int((snap or {}).get("total") or 20)
    except Exception as exc:
        _rto_log(f"WARNING: read Document slot failed: {exc!s}")
    return title, index, total


def _screen_5_doc_key_for_portal_title(
    title: str, *, uploaded: set[str]
) -> str | None:
    """Map portal Document title to our ``doc_key``. ``None`` = skip (portal-only / unknown)."""
    t = (title or "").strip()
    if not t:
        return None
    for rx, key in _SCREEN5_PORTAL_TITLE_TO_DOC_KEY:
        if not rx.search(t):
            continue
        if key is None:
            return None
        if key == "AADHAAR_BACK":
            if "AADHAAR_BACK" not in uploaded:
                return "AADHAAR_BACK"
            return None
        if key == "AADHAAR":
            if "AADHAAR_FRONT" not in uploaded:
                return "AADHAAR_FRONT"
            if "AADHAAR_BACK" not in uploaded:
                return "AADHAAR_BACK"
            return None
        return key
    return None


def _screen_5_click_next_once(
    page: Page, *, settle_ms: int = _SCREEN5_NEXT_CHEVRON_SETTLE_MS
) -> bool:
    """Click document-upload right chevron once; wait for progress and carousel settle."""
    try:
        nxt = page.locator(_SCREEN5_NEXT_BTN).first
        nxt.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
        nxt.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
        nxt.click(timeout=_FIRST_TRY_MS)
        _pause()
        _wait_for_progress_close(page)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        return True
    except Exception as exc:
        _rto_log(f"WARNING: nextBtn failed: {exc!s}")
        return False


def _screen_5_click_next_n(
    page: Page,
    n: int,
    *,
    reason: str,
    settle_ms: int = _SCREEN5_NEXT_CHEVRON_SETTLE_MS,
) -> None:
    """Click **next** (right chevron) ``n`` times — used to skip portal-only document rows."""
    if n <= 0:
        return
    _rto_log(f"nextBtn ×{n} — {reason}")
    for i in range(n):
        if not _screen_5_click_next_once(page, settle_ms=settle_ms):
            _rto_log(f"WARNING: nextBtn {i + 1}/{n} failed")
            break
        _rto_log(f"nextBtn click {i + 1}/{n}")
    _screen_5_escape_overlays(page)


def _screen_5_subcategory_is_disabled(class_attr: str) -> bool:
    """True when Vahan locked Sub Category (document already uploaded for this carousel slot)."""
    return "ui-state-disabled" in (class_attr or "")


def _screen_5_slot_already_uploaded(page: Page) -> bool:
    """True when the current document slot already has a file on the portal."""
    try:
        wrap = page.locator(_SCREEN5_PF_SUBCAT_WRAPPER).first
        wrap.wait_for(state="attached", timeout=_FIRST_TRY_MS)
        if _screen_5_subcategory_is_disabled(wrap.get_attribute("class") or ""):
            return True
    except Exception:
        pass
    try:
        file_input = page.locator(_SCREEN5_FILE_INPUT).first
        file_input.wait_for(state="attached", timeout=_FIRST_TRY_MS)
        if not file_input.is_enabled(timeout=_FIRST_TRY_MS):
            return True
    except Exception:
        pass
    return False


def _screen_5_select_subcategory_overlay(page: Page, doc_key: str) -> None:
    """Pick Sub Category via PrimeFaces overlay + filter (native ``<select>`` is unreliable here)."""
    wid = "formDocumentUpload:subCatgId"
    opt_re = _SCREEN5_SUBCAT_REGEX_BY_DOC_KEY.get(doc_key)
    if opt_re is None:
        opt_re = re.compile(re.escape(doc_key).replace(r"\ ", r"\s+"), re.I)
    filter_hint = _SCREEN5_SUBCAT_FILTER_HINT.get(doc_key, doc_key.split()[0] if doc_key else "")

    _screen_5_escape_overlays(page)
    wrap = page.locator(f'[id="{wid}"]').first
    wrap.wait_for(state="visible", timeout=_DEFAULT_TIMEOUT_MS)
    wrap.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
    wrap_cls = wrap.get_attribute("class") or ""
    if _screen_5_subcategory_is_disabled(wrap_cls):
        raise RuntimeError(f"Sub Category disabled for {doc_key} (document already on portal)")
    wrap.click(timeout=_FIRST_TRY_MS)
    _pause()
    panel = page.locator(f'[id="{wid}_panel"]').first
    panel.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)

    if filter_hint:
        try:
            fin = panel.locator("input.ui-selectonemenu-filter").first
            fin.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            fin.fill("")
            fin.fill(filter_hint)
            _pause()
        except Exception:
            pass

    item = panel.locator("li.ui-selectonemenu-item").filter(has_text=opt_re).first
    try:
        item.wait_for(state="visible", timeout=_LOOP_BUDGET_MS)
        item.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
        item.click(timeout=_FIRST_TRY_MS)
    except Exception:
        items = panel.locator("li.ui-selectonemenu-item")
        n = items.count()
        clicked = False
        for idx in range(n):
            el = items.nth(idx)
            txt = (el.inner_text(timeout=800) or "").strip()
            if not txt or re.match(r"^[\s\-]*SELECT[\s\-]*$", txt, re.I):
                continue
            if opt_re.search(txt):
                el.click(timeout=_FIRST_TRY_MS)
                clicked = True
                _rto_log(f"pf-dropdown: Sub Category ({doc_key}) — fallback text={txt[:80]!r}")
                break
        if not clicked:
            _screen_5_escape_overlays(page)
            raise RuntimeError(f"Sub Category option not found for {doc_key}")

    _rto_log(f"pf-dropdown: Sub Category ({doc_key}) = overlay filter={filter_hint!r}")
    _pause()


def _screen_5_select_subcategory(page: Page, doc_key: str, sub_category_text: str = "") -> None:
    """PrimeFaces **Sub Category** on document upload: ``formDocumentUpload:subCatgId``."""
    del sub_category_text  # title-driven path uses doc_key regex / filter hint only
    _screen_5_select_subcategory_overlay(page, doc_key)


def _screen_5_post_upload_settle() -> None:
    """Let Vahan commit the uploaded file before carousel navigation."""
    if _SCREEN5_POST_UPLOAD_SETTLE_MS > 0:
        time.sleep(_SCREEN5_POST_UPLOAD_SETTLE_MS / 1000.0)


def _screen_5_click_upload_document_trigger(page: Page) -> None:
    """After choosing a file, Vahan often requires **Upload Document** (span with ui-button)."""
    clicked = False
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
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        try:
            page.get_by_role(
                "button", name=re.compile(r"^\s*\+?\s*Upload\s+Documents?\s*$", re.I)
            ).first.click(timeout=_LOOP_BUDGET_MS)
            _rto_log("click: Upload Documents (get_by_role)")
            clicked = True
        except Exception:
            _rto_log(
                "WARNING: Upload Document control not clicked — continuing "
                "(portal may submit on file input alone)"
            )
    if clicked:
        _pause()
        _wait_for_progress_close(page)
        _screen_5_post_upload_settle()


def _screen_5_click_file_movement_trigger(page: Page) -> None:
    """Click **File Movement** on document upload; require visible control and opening modal."""
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause()
    _wait_for_progress_close_loop(page)
    selectors = (
        _SCREEN5_FILE_MOVEMENT_BTN,
        f"{_SCREEN5_FILE_MOVEMENT_BTN} a",
        f"{_SCREEN5_FILE_MOVEMENT_BTN} button",
        f"{_SCREEN5_FILE_MOVEMENT_BTN} span.ui-button",
        "a:has-text('File Movement')",
        "button:has-text('File Movement')",
    )
    clicked = False
    last_err: Exception | None = None
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=_FIRST_TRY_MS)
            loc.scroll_into_view_if_needed(timeout=_FIRST_TRY_MS)
            loc.click(timeout=_FIRST_TRY_MS)
            _rto_log(f"click: File Movement ({sel})")
            clicked = True
            break
        except Exception as exc:
            last_err = exc
            continue
    if not clicked:
        _dump_page_state(page, "Screen 5 File Movement button missing")
        detail = f": {last_err!s}" if last_err else ""
        raise RuntimeError(f"Screen 5 File Movement button not found or not clickable{detail}")
    _pause()
    _wait_for_progress_close_loop(page)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if _screen_4_find_file_movement_dialog(page, quick=True) is not None:
            return
        time.sleep(0.15)
    _dump_page_state(page, "Screen 5 File Movement dialog missing")
    raise RuntimeError("Screen 5 File Movement — dialog did not open after click")


def _screen_5_finish_file_movement(page: Page) -> None:
    """After all uploads: File Movement trigger → remarks/Save (Screen 4 modal) → follow-up dialogs."""
    _screen_5_click_file_movement_trigger(page)
    _screen_4_file_movement_dialogs(page)
    if _screen_4_file_movement_dialog_visible(page):
        _dump_page_state(page, "Screen 5 File Movement still open after Save")
        raise RuntimeError("Screen 5 File Movement — dialog still open after Save")
    for label in ("Yes", "Ok", "OK"):
        if _dismiss_dialog_if_present(page, label, timeout_ms=_FIRST_TRY_MS):
            _pause()
            _wait_for_progress_close_loop(page)
    _rto_log("Screen 5: File Movement complete")


def _screen_5_prepare_upload_file(doc_key: str, source: Path) -> Path:
    """Return path to upload; compress insurance PDFs to Vahan size limit when needed."""
    if doc_key != "INSURANCE CERTIFICATE" or source.suffix.lower() != ".pdf":
        return source
    try:
        original_size = source.stat().st_size
    except OSError:
        return source
    if original_size <= VAHAN_UPLOAD_MAX_BYTES:
        try:
            import fitz

            with fitz.open(str(source)) as doc:
                if doc.page_count <= 1:
                    return source
        except Exception:
            return source
    from app.services.post_ocr_service import compress_pdf_for_upload

    dest = source.parent / f"{source.stem}_vahan_upload.pdf"
    try:
        data = compress_pdf_for_upload(source, VAHAN_UPLOAD_MAX_BYTES, grayscale=True)
        dest.write_bytes(data)
        _rto_log(
            f"Screen 5: insurance compressed {original_size}B → {len(data)}B ({dest.name})"
        )
        return dest
    except Exception as exc:
        _rto_log(f"WARNING: insurance compress failed — using original: {exc!s}")
        return source


def _screen_5_upload_one(page: Page, doc_key: str, file_path: Path) -> None:
    """Select Sub Category, attach file, click Upload Document for one carousel slot."""
    logger.info("fill_rto: uploading %s (%s)", doc_key, file_path.name)
    upload_path = _screen_5_prepare_upload_file(doc_key, file_path)
    _screen_5_escape_overlays(page)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause()
    _screen_5_select_subcategory(page, doc_key)
    _pause()
    _upload_file(
        page,
        upload_path,
        file_input_selector=_SCREEN5_FILE_INPUT,
        wait_progress_after=False,
    )
    _screen_5_click_upload_document_trigger(page)
    if _screen_5_click_next_once(page):
        _rto_log(f"chevron next after upload: {doc_key} ({_SCREEN5_NEXT_BTN})")
    else:
        logger.warning("fill_rto: nextBtn not found for %s", doc_key)
        _rto_log(f"WARNING: nextBtn not found for {doc_key}")


def _screen_5(page: Page, docs: dict[str, Path | None]) -> None:
    """Screen 5: title-driven carousel — upload when Document title matches a pending file."""
    _set_screen("Screen 5")
    logger.info("fill_rto: Screen 5 — uploading %d document categories", len(docs))
    _rto_log("--- Screen 5: Document uploads by portal title ---")
    _screen_5_wait_for_upload_form_ready(page)

    if int(RTO_FILL_SKIP_TO_SCREEN) >= 5 and RTO_FILL_SCREEN5_SKIP_TO_FILE_MOVEMENT_ONLY:
        _rto_log(
            "SKIP: Screen 5 — File Movement only "
            f"(scroll bottom → {_SCREEN5_FILE_MOVEMENT_BTN} → Yes / Ok / OK Application ID)"
        )
        _screen_5_finish_file_movement(page)
        return

    if int(RTO_FILL_SKIP_TO_SCREEN) >= 5 and RTO_FILL_SCREEN5_SKIP_TO_OWNER_UNDERTAKING_ONLY:
        required = {"OWNER UNDERTAKING FORM"} if docs.get("OWNER UNDERTAKING FORM") else set()
        _rto_log("SKIP: Screen 5 — OWNER UNDERTAKING FORM only (title-driven carousel)")
    else:
        required = {k for k, v in docs.items() if v is not None}
        if (
            int(RTO_FILL_SKIP_TO_SCREEN) >= 5
            and int(RTO_FILL_SCREEN5_START_WITH_NEXT_N or 0) > 0
        ):
            _screen_5_click_next_n(
                page,
                int(RTO_FILL_SCREEN5_START_WITH_NEXT_N),
                reason="skip to Screen 5 — nextBtn first (dev align)",
            )

    if not required:
        _rto_log("WARNING: Screen 5 — no document files to upload")
        _screen_5_finish_file_movement(page)
        return

    uploaded: set[str] = set()
    stalls = 0
    _, _, total_slots = _screen_5_read_document_slot(page)
    max_stalls = max(int(total_slots or 0), 20)

    while required - uploaded:
        title, index, total = _screen_5_read_document_slot(page)
        if total > 0:
            max_stalls = max(total, max_stalls)
        doc_key = _screen_5_doc_key_for_portal_title(title, uploaded=uploaded)
        _rto_log(
            f'Screen 5: Document({index} of {total})={title!r} → {doc_key!r} '
            f"(pending={sorted(required - uploaded)})"
        )

        if doc_key and doc_key in required and doc_key not in uploaded:
            if _screen_5_slot_already_uploaded(page):
                _rto_log(f"Screen 5: skip upload (already on portal): {doc_key}")
                uploaded.add(doc_key)
                stalls = 0
                if not _screen_5_click_next_once(page):
                    _rto_log("WARNING: Screen 5 carousel — nextBtn failed after already-uploaded slot")
                    break
                continue
            file_path = docs.get(doc_key)
            if not file_path:
                _rto_log(f"skip upload (missing file): {doc_key}")
                uploaded.add(doc_key)  # avoid infinite retry
                stalls = 0
                continue
            try:
                _screen_5_upload_one(page, doc_key, file_path)
                uploaded.add(doc_key)
                stalls = 0
            except Exception as exc:
                if _screen_5_slot_already_uploaded(page) or "already on portal" in str(exc):
                    _rto_log(f"Screen 5: already on portal: {doc_key}")
                    uploaded.add(doc_key)
                    stalls = 0
                    if not _screen_5_click_next_once(page):
                        break
                    continue
                _rto_log(f"WARNING: upload failed for {doc_key}: {exc!s}")
                _screen_5_escape_overlays(page)
                if not _screen_5_click_next_once(page):
                    break
                stalls += 1
                if stalls >= max_stalls:
                    break
            continue

        if not _screen_5_click_next_once(page):
            _rto_log("WARNING: Screen 5 carousel — nextBtn failed; stopping")
            break
        stalls += 1
        if stalls >= max_stalls:
            _rto_log(
                f"WARNING: Screen 5 carousel stalled after {stalls} nexts "
                f"(still missing {sorted(required - uploaded)})"
            )
            break

    missing = sorted(required - uploaded)
    if missing:
        raise RuntimeError(f"Screen 5 incomplete — missing uploads: {missing}")

    _pause()
    _screen_5_finish_file_movement(page)


def _screen_6(page: Page) -> float | None:
    """Screen 6: **Intentional stop** before **Dealer Regn Fee Tax** (no fee click, no payment automation).

    In **production** (``ENVIRONMENT`` = ``prod`` / ``production``), raises ``RuntimeError`` so the run
    surfaces that fee/payment is still manual. In any other ``ENVIRONMENT``, logs the same stop and
    returns ``None`` so ``fill_rto_row`` completes with ``completed: True`` (dev/test full SOP without payment).
    """
    _set_screen("Screen 6")
    logger.info("fill_rto: Screen 6 — hard stop before Dealer Regn Fee Tax")
    _rto_log("--- Screen 6: hard stop before Dealer Regn Fee Tax ---")

    msg = (
        "Screen 6: intentional stop before clicking Dealer Regn Fee Tax. "
        "Open fee details and complete payment steps manually on Vahan."
    )
    logger.warning("fill_rto: %s", msg)
    _rto_log(f"HARD STOP (Screen 6): {msg}")
    if ENVIRONMENT_IS_PRODUCTION:
        raise RuntimeError(msg)
    logger.info(
        "fill_rto: Screen 6 — non-production ENVIRONMENT: stopping before Dealer Regn Fee Tax; "
        "returning success (completed=True, rto_payment_amount=None)."
    )
    _rto_log(
        "Screen 6: non-production ENVIRONMENT — same hard stop (no payment automation); "
        "fill_rto_row returns completed=True."
    )
    return None


# Message when warm-browser opens Vahan but the operator still needs to log in manually.
VAHAN_WARM_LOGIN_MESSAGE = "Vahan Opened. Please login. And then press button again"
# Back-compat alias (older logs / docs).
VAHAN_WARM_THEN_CONTINUE_MESSAGE = VAHAN_WARM_LOGIN_MESSAGE


def warm_vahan_browser_session() -> dict:
    """Open or attach to the Vahan browser without running fill automation (no login gate).

    Returns ``ready_for_batch`` when Screen 1 is already visible so the client can start the
    RTO batch on the same button click (no extra Continue step).
    """
    out: dict = {"success": False, "error": None, "message": None, "ready_for_batch": False}
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
        if _vahan_dealer_home_ready(page):
            out["ready_for_batch"] = True
            out["message"] = "Vahan session ready — batch can start."
        else:
            out["message"] = VAHAN_WARM_LOGIN_MESSAGE
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
        "mobile": (row.get("customer_mobile") or row.get("mobile") or "").strip(),
        "address": row.get("address") or "",
        "city": row.get("city") or "",
        "district": (row.get("district") or "").strip(),
        "dealer_rto": (row.get("dealer_rto") or "").strip(),
        "state": row.get("state") or "",
        "pin": (row.get("pin") or "").strip(),
        "financier": row.get("financier") or "",
        "insurer": row.get("insurer") or "",
        "policy_num": row.get("policy_num") or "",
        "idv": row.get("idv"),
        "billing_date_str": _billing_fmt,
        "invoice_date_str": _billing_fmt,
        "policy_from_str": _fmt_date(row.get("policy_from")),
        "policy_to_str": _fmt_date(row.get("policy_to")),
        "policy_from": row.get("policy_from"),
        "policy_to": row.get("policy_to"),
        "nominee_name": (row.get("nominee_name") or "").strip(),
        "nominee_relationship": (row.get("nominee_relationship") or "").strip(),
        "rto_application_id": (row.get("rto_application_id") or "").strip(),
        "rto_status": row.get("rto_status"),
    }

    mob_fn = _mobile_digits_for_filename(data.get("mobile"))
    log_path = _rto_action_log_path(dealer_id, row, mob_fn)
    log_display = _rto_log_path_display(log_path, dealer_id=dealer_id)
    rlog = RtoActionLog(log_path)
    inventory_display = _rto_log_path_display(rlog.inventory_path, dealer_id=dealer_id)
    started_ist = _ts_ist_iso()
    rlog.write_run_header(
        [
            f"started_ist={started_ist}",
            f"rto_queue_id={rto_queue_id}",
            f"sales_id={row.get('sales_id')}",
            f"dealer_id={dealer_id}",
            f"customer_mobile={data.get('mobile')!r}",
            f"rto_status={data.get('rto_status')!r}",
            f"rto_application_id={data.get('rto_application_id')!r}",
            f"log_file={log_display}",
            f"inventory_log_file={inventory_display}",
        ]
    )
    token = _rto_action_log.set(rlog)
    screen_token = _current_screen.set("Setup")
    try:
        _rto_log(
            f"fill_rto_row start rto_queue_id={rto_queue_id} sales_id={row.get('sales_id')} "
            f"dealer_id={dealer_id}"
        )
        _apply_rto_fill_screen3c_run_overrides(data)

        office = _transform_dealer_rto(row.get("dealer_rto") or "")

        # --- Resolve document files (log: uploads root, searched folder, one summary line) ---
        subfolder = row.get("subfolder") or ""
        uploads_root = get_uploads_dir(dealer_id)
        _rto_log_verbose(f"uploads root (dealer): {uploads_root.resolve()}")
        if subfolder:
            sale_dir = uploads_root / subfolder
            _rto_log(f"documents folder: {subfolder!r}")
        else:
            sale_dir = None
            _rto_log(
                f"documents folder: (none — file_location empty for sales_id={row.get('sales_id')})"
            )

        docs = (
            _resolve_sale_documents(sale_dir, subfolder=subfolder or "", mob_fn=mob_fn)
            if sale_dir
            else {cat: None for cat, _, _ in _DOC_PATTERNS}
        )

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

        if RTO_FILL_TEST_APPLICATION_ID and not str(data.get("rto_application_id") or "").strip():
            data["rto_application_id"] = RTO_FILL_TEST_APPLICATION_ID

        db_skip = _resolve_skip_from_rto_status(data.get("rto_status"))
        dev_skip = max(0, min(6, int(RTO_FILL_SKIP_TO_SCREEN)))
        use_resume_nav = False
        if dev_skip > 0:
            skip_from = dev_skip
        else:
            skip_from = db_skip
            use_resume_nav = db_skip >= 3

        screen3_resume_at_3b = _screen3_resume_at_3b(
            use_resume_nav=use_resume_nav,
            rto_status=data.get("rto_status"),
        )
        screen4_skip_verify = _screen4_skip_verify_on_resume(
            use_resume_nav=use_resume_nav,
            rto_status=data.get("rto_status"),
        ) or RTO_FILL_SCREEN4_SKIP_VERIFY

        if use_resume_nav and not str(data.get("rto_application_id") or "").strip():
            raise RuntimeError(
                "Cannot resume RTO fill: rto_status>=1 but rto_application_id is missing on rto_queue row"
            )

        if dev_skip > 0:
            extra = ""
            if skip_from == 3:
                extra = " Screen 3: skip Home + Entry → Vehicle Details sub-tab first."
            elif skip_from == 4:
                if RTO_FILL_SCREEN4_SKIP_TO_DEALER_DOC_UPLOAD:
                    s4_hint = "start at Dealer Document Upload tab only"
                elif RTO_FILL_SCREEN4_SKIP_VERIFY:
                    s4_hint = "Save-Options → File Movement → NONE+Save → Yes → Dealer Document Upload"
                else:
                    s4_hint = "Verify → Save-Options → File Movement → NONE+Save → Yes → Dealer Document Upload"
                extra = f" Screen 4: {s4_hint}."
            elif skip_from == 5:
                if RTO_FILL_SCREEN5_SKIP_TO_FILE_MOVEMENT_ONLY:
                    extra = (
                        " Screen 5: **File Movement** only (scroll → fileFlowId → Yes → Ok → OK Application ID)."
                    )
                elif RTO_FILL_SCREEN5_SKIP_TO_OWNER_UNDERTAKING_ONLY:
                    extra = (
                        " Screen 5: **Owner Undertaking Form** only — pick Sub Category, upload file, next; "
                        "File Movement at end."
                    )
                else:
                    extra = (
                        " Screen 5: title-driven carousel (Document title → upload / next); "
                        f"optional nextBtn ×{int(RTO_FILL_SCREEN5_START_WITH_NEXT_N or 0)} first (when >0)."
                    )
            _rto_log(
                f"TEMP SKIP: RTO_FILL_SKIP_TO_SCREEN={skip_from} — start at Screen {skip_from} "
                f"(skipped: dealer-home reset + screens 1..{skip_from - 1}){extra}"
            )
            logger.warning("fill_rto_row: RTO_FILL_SKIP_TO_SCREEN=%s (dev/testing)", skip_from)
        elif db_skip > 0:
            _rto_log(
                f"RESUME: rto_status={data.get('rto_status')} skip_from={skip_from} "
                f"rto_application_id={data.get('rto_application_id')!r} "
                f"(skipped screens 1..{skip_from - 1}; pending-work grid open when skip_from>=3)"
            )
            if screen3_resume_at_3b:
                _rto_log(
                    "Screen 3 resume: start at 3b (Vehicle Details tab), skip 3a Home/Entry"
                )
            if screen4_skip_verify and db_skip >= 4:
                _rto_log(
                    "Screen 4 resume: pending grid Verify already clicked — Save Options → File Movement → Yes"
                )
            logger.info(
                "fill_rto_row: resume rto_status=%s skip_from=%s app=%s",
                data.get("rto_status"),
                skip_from,
                data.get("rto_application_id"),
            )

        if use_resume_nav:
            _ensure_vahan_dealer_home_for_screen1(page)
            _resume_open_application_from_pending_grid(
                page,
                office,
                str(data.get("rto_application_id") or "").strip(),
                rto_status=data.get("rto_status"),
            )
        elif skip_from <= 0:
            _ensure_vahan_dealer_home_for_screen1(page)

        if skip_from <= 1:
            _screen_1(page, office)
        if skip_from <= 2:
            _screen_2(page, data)

        application_id = ""
        if skip_from <= 3:
            screen3_skip_home = (
                screen3_resume_at_3b or (skip_from == 3) or RTO_FILL_SCREEN3_SKIP_HOME
            )
            screen3_skip_entry = screen3_resume_at_3b or (
                screen3_skip_home and ((skip_from == 3) or RTO_FILL_SCREEN3_SKIP_ENTRY)
            )
            application_id = _screen_3(
                page,
                data,
                skip_home=screen3_skip_home,
                skip_entry=screen3_skip_entry,
                resume_at_3b=screen3_resume_at_3b,
            )
            if not str(application_id or "").strip():
                raise RuntimeError(
                    "Screen 3 did not yield an application number after Save and File Movement"
                )
        else:
            application_id = str(data.get("rto_application_id") or "").strip()

        if skip_from <= 4:
            _screen_4(page, skip_verify=screen4_skip_verify)
        if skip_from <= 5:
            _screen_5(page, docs)
            _persist_rto_queue_progress(data, rto_status=RTO_STATUS_AFTER_SCREEN5)
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
