"""Per-row Vahan site fill logic. Called by rto_payment_service during batch processing.

Implements the 6-screen Playwright SOP for new vehicle registration on the
Vahan (parivahan.gov.in) dealer portal.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PwTimeout

from app.config import VAHAN_BASE_URL, get_uploads_dir
from app.services.handle_browser_opening import get_or_open_site_page

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS = 15_000
_LONG_TIMEOUT_MS = 60_000
_SHORT_WAIT_S = 0.6

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _transform_dealer_rto(raw_rto_name: str) -> str:
    """'RTO-Bharatpur' -> 'BHARATPUR RTO'  (strip first 4 chars, upper, append ' RTO')."""
    if not raw_rto_name or len(raw_rto_name) <= 4:
        return (raw_rto_name or "").upper()
    return raw_rto_name[4:].strip().upper() + " RTO"


def _fmt_date(d: date | datetime | str | None) -> str:
    """Format a date as dd-mm-yyyy for Vahan portal fields."""
    if d is None:
        return ""
    if isinstance(d, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                d = datetime.strptime(d, fmt).date()
                break
            except ValueError:
                continue
        else:
            return d
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d-%m-%Y")


# ---------------------------------------------------------------------------
# Document resolution
# ---------------------------------------------------------------------------

_DOC_PATTERNS: list[tuple[str, list[str], list[str]]] = [
    ("FORM 20", ["*Form_20*", "*Form 20*", "*FORM_20*", "*FORM 20*"], [".pdf"]),
    ("FORM 21", ["*Sale_Certificate*", "*Sale Certificate*", "*Form_21*", "*Form 21*", "*FORM_21*"], [".pdf"]),
    ("FORM 22", ["*Form_22*", "*Form 22*", "*FORM_22*", "*FORM 22*"], [".pdf"]),
    ("INSURANCE CERTIFICATE", ["*Insurance*"], [".pdf"]),
    ("INVOICE ORIGINAL", ["*GST_Retail_Invoice*", "*GST*Invoice*", "*Tax_Invoice*", "*Tax Invoice*", "*Retail_Invoice*"], [".pdf"]),
    ("AADHAAR_FRONT", ["*Aadhar*front*", "*aadhaar*front*", "*adhaar*front*",
                        "*Aadhar*consolidated*", "*aadhaar*consolidated*", "*adhaar*consolidated*",
                        "*Aadhar_Card*", "*Aadhaar*"], [".jpg", ".jpeg", ".png"]),
    ("AADHAAR_BACK", ["*Aadhar*back*", "*aadhaar*back*", "*adhaar*back*"], [".jpg", ".jpeg", ".png"]),
    ("OWNER UNDERTAKING FORM", ["*Detail*", "*Sales_Detail*", "*Detail_Sheet*", "*Sales Detail*"], [".pdf"]),
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

def _pause(seconds: float = _SHORT_WAIT_S) -> None:
    time.sleep(seconds)


def _click(page: Page, selector: str, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Wait for a selector and click it."""
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.click(timeout=timeout)
    logger.debug("fill_rto: clicked %s (%s)", selector, label)


def _fill(page: Page, selector: str, value: str, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Clear and fill a text field."""
    if not value:
        return
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.fill(value, timeout=timeout)
    logger.debug("fill_rto: filled %s = %s (%s)", selector, value[:40], label)


def _select(page: Page, selector: str, value: str, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Select an option from a <select> dropdown by visible text."""
    if not value:
        return
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.select_option(label=value, timeout=timeout)
    logger.debug("fill_rto: selected %s = %s (%s)", selector, value, label)


def _type_typeahead(page: Page, selector: str, value: str, *, timeout: int = _DEFAULT_TIMEOUT_MS, label: str = "") -> None:
    """Type into a typeahead/autocomplete field and pick the first suggestion."""
    if not value:
        return
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.click()
    loc.fill("")
    loc.type(value, delay=50)
    _pause(1.0)
    suggestion = page.locator(".ui-autocomplete li, .ui-menu-item, [role='option']").first
    try:
        suggestion.wait_for(state="visible", timeout=5000)
        suggestion.click()
    except PwTimeout:
        page.keyboard.press("Enter")
    logger.debug("fill_rto: typeahead %s = %s (%s)", selector, value, label)


def _dismiss_dialog(page: Page, button_text: str = "OK", *, timeout: int = 8000) -> None:
    """Click a dialog/popup button by its text."""
    btn = page.get_by_role("button", name=re.compile(button_text, re.IGNORECASE)).first
    try:
        btn.wait_for(state="visible", timeout=timeout)
        btn.click()
        logger.debug("fill_rto: dismissed dialog with '%s'", button_text)
    except PwTimeout:
        logger.debug("fill_rto: no dialog with '%s' found (timeout), continuing", button_text)


def _wait_for_progress_close(page: Page, timeout_ms: int = _LONG_TIMEOUT_MS) -> None:
    """Wait until a progress/loading overlay disappears."""
    try:
        overlay = page.locator(".ui-blockui, .blockUI, .loading-overlay, .ui-dialog-loading").first
        overlay.wait_for(state="hidden", timeout=timeout_ms)
    except PwTimeout:
        pass
    _pause(0.5)


def _upload_file(page: Page, file_path: Path, *, timeout: int = _DEFAULT_TIMEOUT_MS) -> None:
    """Set the file on the visible file input and wait for upload to settle."""
    file_input = page.locator("input[type='file']").first
    file_input.set_input_files(str(file_path), timeout=timeout)
    _pause(2.0)
    _wait_for_progress_close(page)


# ---------------------------------------------------------------------------
# Screen implementations
# ---------------------------------------------------------------------------

def _screen_1(page: Page, office: str) -> None:
    """Screen 1: Select office, action, Show Form."""
    logger.info("fill_rto: Screen 1 — office=%s", office)

    _type_typeahead(
        page,
        "input[id*='officeName'], input[id*='assignedOffice'], input[name*='officeName']",
        office,
        label="Select Assigned Office",
    )
    _pause(0.5)

    _select(
        page,
        "select[id*='action'], select[id*='selectAction'], select[name*='action']",
        "Entry-New Registeration",
        label="Select Action",
    )
    _pause(0.3)

    _click(page, "input[value='Show Form'], button:has-text('Show Form')", label="Show Form")
    _pause(1.0)

    _dismiss_dialog(page, "OK")


def _screen_2(page: Page, data: dict) -> None:
    """Screen 2: Chassis/engine, owner details, address, Save."""
    logger.info("fill_rto: Screen 2 — chassis=%s", data.get("chassis_num", "")[:8])

    # 2a: Chassis and engine
    _fill(page, "input[id*='chassisNo'], input[name*='chassisNo']", data["chassis_num"], label="Chassis No")
    _fill(page, "input[id*='engineNo'], input[name*='engineNo']", data["engine_short"], label="Engine No (last 5)")
    _click(page, "input[value='Get Details'], button:has-text('Get Details')", label="Get Details")
    _pause(2.0)
    _wait_for_progress_close(page)

    # 2b: Choice number = No
    try:
        _select(page, "select[id*='choiceNo'], select[id*='choice']", "No", label="Choice number opt", timeout=5000)
    except PwTimeout:
        logger.debug("fill_rto: choice number dropdown not found, skipping")

    # Owner Details tab
    _fill(
        page,
        "input[id*='purchaseDate'], input[id*='deliveryDate'], input[name*='purchaseDate']",
        data["billing_date_str"],
        label="Purchase/Delivery Date",
    )
    _fill(page, "input[id*='ownerName'], input[name*='ownerName']", data["customer_name"], label="Owner Name")
    _pause(0.5)

    # Popup: pick "Not Available" radio
    try:
        not_avail = page.locator("input[type='radio'][value*='Not Available'], label:has-text('Not Available') input[type='radio']").first
        not_avail.wait_for(state="visible", timeout=5000)
        not_avail.click()
        _pause(0.3)
    except PwTimeout:
        logger.debug("fill_rto: 'Not Available' radio not found, skipping")

    _fill(
        page,
        "input[id*='sonWife'], input[id*='relation'], input[name*='sonWife']",
        data.get("care_of", ""),
        label="Son/Wife/Daughter of",
    )

    # 2c: Mobile
    _fill(page, "input[id*='mobileNo'], input[name*='mobileNo']", data.get("mobile", ""), label="Mobile No")

    # 2d: Address fields
    _fill(
        page,
        "input[id*='houseNo'], input[id*='streetName'], input[name*='houseNo']",
        data.get("address", ""),
        label="House no & street",
    )
    _fill(
        page,
        "input[id*='village'], input[id*='town'], input[id*='city'], input[name*='village']",
        data.get("city", ""),
        label="Village/Town/City",
    )

    if data.get("state"):
        try:
            _select(
                page,
                "select[id*='state'], select[name*='state']",
                data["state"],
                label="State",
                timeout=5000,
            )
        except (PwTimeout, Exception):
            _type_typeahead(
                page,
                "input[id*='state'], input[name*='state']",
                data["state"],
                label="State (typeahead)",
                timeout=5000,
            )

    # Skip district per user instruction

    _fill(page, "input[id*='pin'], input[name*='pin']", data.get("pin", ""), label="Pin")
    _pause(0.3)

    # Same as Current Address checkbox
    try:
        same_addr = page.locator(
            "input[type='checkbox'][id*='sameAddress'], "
            "input[type='checkbox'][id*='currentAddress'], "
            "label:has-text('Same as Current') input[type='checkbox']"
        ).first
        same_addr.wait_for(state="visible", timeout=5000)
        if not same_addr.is_checked():
            same_addr.click()
    except PwTimeout:
        logger.debug("fill_rto: 'Same as Current Address' checkbox not found, skipping")

    # 2e: Save and file movement
    _pause(0.5)
    _click(
        page,
        "input[value*='Save'], button:has-text('Save and file movement'), button:has-text('Save and File Movement')",
        label="Save and file movement",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause(1.0)
    _dismiss_dialog(page, "Yes")
    _pause(1.0)
    _dismiss_dialog(page, "Close", timeout=5000)


def _screen_3(page: Page, data: dict) -> str:
    """Screen 3: Home > Entry, Tax mode, Insurance, Hypothecation, Save. Returns application_id."""
    logger.info("fill_rto: Screen 3 — insurer=%s", data.get("insurer", "")[:20])

    # 3a: Home then Entry
    _click(page, "a:has-text('Home'), button:has-text('Home'), [id*='home']", label="Home link")
    _pause(1.5)
    _wait_for_progress_close(page)

    _click(page, "input[value='Entry'], button:has-text('Entry'), a:has-text('Entry')", label="Entry button")
    _pause(1.5)
    _wait_for_progress_close(page)

    # 3b: Vehicle Details — Tax Mode
    try:
        _select(
            page,
            "select[id*='taxMode'], select[name*='taxMode']",
            "ONE TIME",
            label="Tax Mode",
            timeout=8000,
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
            timeout=8000,
        )
    except (PwTimeout, Exception):
        logger.debug("fill_rto: Insurance Type dropdown issue, trying typeahead")
        try:
            _type_typeahead(page, "input[id*='insuranceType']", "THIRD PARTY", label="Insurance Type typeahead", timeout=5000)
        except PwTimeout:
            pass

    if data.get("insurer"):
        try:
            _select(
                page,
                "select[id*='insuranceCompany'], select[name*='insuranceCompany']",
                data["insurer"],
                label="Insurance Company",
                timeout=8000,
            )
        except (PwTimeout, Exception):
            _type_typeahead(
                page,
                "input[id*='insuranceCompany'], input[name*='insuranceCompany']",
                data["insurer"],
                label="Insurance Company typeahead",
                timeout=8000,
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
            hyp_check.wait_for(state="visible", timeout=5000)
            if not hyp_check.is_checked():
                hyp_check.click()
            _pause(0.5)
        except PwTimeout:
            logger.debug("fill_rto: hypothecation checkbox not found")

        try:
            _select(
                page,
                "select[id*='hypothecationType'], select[name*='hypothecationType']",
                "Hypothecation",
                label="Hypothecation Type",
                timeout=5000,
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
                timeout=8000,
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
                    timeout=5000,
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
    _pause(1.5)
    _dismiss_dialog(page, "Yes")
    _pause(1.0)

    # "Are you sure" popup
    _dismiss_dialog(page, "Yes", timeout=5000)
    _pause(1.0)

    # Scrape Application No from success popup
    application_id = ""
    try:
        dialog_text = page.locator(
            ".ui-dialog-content, .ui-messages-info, .ui-growl-message, "
            "[class*='dialog'] [class*='message'], [class*='success']"
        ).first
        dialog_text.wait_for(state="visible", timeout=10000)
        text = dialog_text.inner_text()
        match = re.search(r'(?:application\s*(?:no\.?|number)\s*[:\-]?\s*)(\S+)', text, re.IGNORECASE)
        if match:
            application_id = match.group(1).strip()
        else:
            nums = re.findall(r'[A-Z0-9]{5,}', text)
            if nums:
                application_id = nums[0]
        logger.info("fill_rto: scraped application_id=%s", application_id)
    except PwTimeout:
        logger.warning("fill_rto: could not scrape application number from popup")

    _dismiss_dialog(page, "OK", timeout=5000)
    _pause(0.5)

    return application_id


def _screen_4(page: Page) -> None:
    """Screen 4: Verify, File Movement, Dealer Document Upload."""
    logger.info("fill_rto: Screen 4 — Verify & Document Upload nav")

    # Scroll down and click Verify
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause(0.5)
    _click(page, "input[value='Verify'], button:has-text('Verify')", label="Verify", timeout=_DEFAULT_TIMEOUT_MS)
    _pause(1.5)
    _wait_for_progress_close(page)

    # Save Options > File Movement
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _pause(0.5)

    try:
        _click(
            page,
            "input[value*='Save'], button:has-text('Save Options'), a:has-text('Save Options')",
            label="Save Options",
            timeout=_DEFAULT_TIMEOUT_MS,
        )
        _pause(0.5)
        _click(
            page,
            "a:has-text('File Movement'), [id*='fileMovement'], button:has-text('File Movement')",
            label="File Movement menu item",
            timeout=8000,
        )
    except PwTimeout:
        _click(
            page,
            "button:has-text('File Movement'), input[value*='File Movement']",
            label="File Movement direct",
            timeout=_DEFAULT_TIMEOUT_MS,
        )

    _pause(1.0)

    # Popups: State -> Save, then Yes
    _dismiss_dialog(page, "Save", timeout=5000)
    _pause(0.5)
    _dismiss_dialog(page, "Yes", timeout=5000)
    _pause(1.0)

    # Dealer Document Upload
    _click(
        page,
        "input[value*='Dealer Document Upload'], button:has-text('Dealer Document Upload'), a:has-text('Dealer Document Upload')",
        label="Dealer Document Upload",
    )
    _pause(2.0)
    _wait_for_progress_close(page)


def _screen_5(page: Page, docs: dict[str, Path | None]) -> None:
    """Screen 5: Upload documents per sub-category."""
    logger.info("fill_rto: Screen 5 — uploading %d document categories", len(docs))

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
            continue

        logger.info("fill_rto: uploading %s -> %s (%s)", doc_key, sub_category_text, file_path.name)

        # Select sub-category
        try:
            _select(
                page,
                "select[id*='subCategory'], select[id*='docCategory'], select[name*='subCategory']",
                sub_category_text,
                label=f"Sub Category: {sub_category_text}",
                timeout=8000,
            )
        except (PwTimeout, Exception):
            _type_typeahead(
                page,
                "input[id*='subCategory']",
                sub_category_text,
                label=f"Sub Category typeahead: {sub_category_text}",
                timeout=8000,
            )

        _pause(0.5)

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
            chevron.wait_for(state="visible", timeout=8000)
            chevron.click()
            _pause(1.5)
            _wait_for_progress_close(page)
        except PwTimeout:
            logger.warning("fill_rto: right-chevron not found for %s, continuing", doc_key)

    # After all uploads, click File Movement
    _pause(0.5)
    _click(
        page,
        "input[value*='File Movement'], button:has-text('File Movement'), a:has-text('File Movement')",
        label="File Movement (after uploads)",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause(1.0)
    _dismiss_dialog(page, "Yes")
    _pause(0.5)
    _dismiss_dialog(page, "Ok", timeout=5000)
    _pause(0.5)


def _screen_6(page: Page) -> float | None:
    """Screen 6: Dealer Regn Fee Tax — scrape total payable amount."""
    logger.info("fill_rto: Screen 6 — fee details")

    _click(
        page,
        "input[value*='Dealer Regn Fee'], button:has-text('Dealer Regn Fee'), a:has-text('Dealer Regn Fee Tax')",
        label="Dealer Regn Fee Tax",
        timeout=_DEFAULT_TIMEOUT_MS,
    )
    _pause(2.0)
    _wait_for_progress_close(page)

    # Scrape Total Payable Amount
    total: float | None = None
    try:
        amount_el = page.locator(
            "[id*='totalPayable'], [id*='totalAmount'], "
            "td:has-text('Total Payable') + td, "
            "span:has-text('Total Payable'), "
            "label:has-text('Total Payable')"
        ).first
        amount_el.wait_for(state="visible", timeout=10000)
        text = amount_el.inner_text().strip()
        nums = re.findall(r'[\d,]+\.?\d*', text)
        if nums:
            total = float(nums[-1].replace(",", ""))
        logger.info("fill_rto: scraped total payable = %s", total)
    except PwTimeout:
        logger.warning("fill_rto: could not scrape total payable amount")

    # Dismiss popups
    _dismiss_dialog(page, "Yes", timeout=5000)
    _pause(0.5)
    try:
        close_btn = page.locator(
            "button:has-text('×'), button:has-text('X'), "
            "[class*='ui-dialog-titlebar-close'], a[class*='close']"
        ).first
        close_btn.wait_for(state="visible", timeout=5000)
        close_btn.click()
    except PwTimeout:
        pass
    _pause(0.5)

    return total


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
        "state": row.get("state") or "",
        "pin": (row.get("pin") or "").strip(),
        "financier": row.get("financier") or "",
        "insurer": row.get("insurer") or "",
        "policy_num": row.get("policy_num") or "",
        "idv": row.get("idv"),
        "billing_date_str": _fmt_date(row.get("billing_date")),
        "policy_from_str": _fmt_date(row.get("policy_from")),
    }

    office = _transform_dealer_rto(row.get("dealer_rto") or "")

    # --- Resolve document files ---
    subfolder = row.get("subfolder") or ""
    sale_dir = get_uploads_dir(dealer_id) / subfolder if subfolder else None
    docs = _resolve_sale_documents(sale_dir) if sale_dir else {cat: None for cat, _, _ in _DOC_PATTERNS}

    missing = [k for k, v in docs.items() if v is None]
    if missing:
        logger.warning("fill_rto: missing documents: %s", ", ".join(missing))

    # --- Open Vahan browser ---
    page, open_error = get_or_open_site_page(
        VAHAN_BASE_URL,
        "Vahan",
        require_login_on_open=True,
    )
    if page is None:
        raise RuntimeError(f"Vahan site not open or login failed: {open_error}")

    page.set_default_timeout(_DEFAULT_TIMEOUT_MS)

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

    return {
        "rto_application_id": application_id or None,
        "rto_payment_amount": total_payable,
        "completed": True,
    }
