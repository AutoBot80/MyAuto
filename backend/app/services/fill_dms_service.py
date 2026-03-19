"""
Fill DMS flow using Playwright: login, fill enquiry, search vehicle, scrape row, save PDFs.
Optionally fills dummy Vahan registration and returns application_id and rto_fees.
Uses Chromium (faster launch). Requires: pip install playwright && playwright install chromium.
Uses headed browser by default (set DMS_PLAYWRIGHT_HEADED=false for headless).
Writes pulled data to ocr_output/subfolder/Data from DMS.txt for consistency with other OCR outputs.
"""
import logging
import os
import re
import urllib.parse
import atexit
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.config import DMS_PLAYWRIGHT_HEADED, OCR_OUTPUT_DIR, PLAYWRIGHT_KEEP_OPEN
from app.repositories import form_dms as form_dms_repo
from app.repositories import form_vahan as form_vahan_repo

logger = logging.getLogger(__name__)

_PW = None
_KEEP_OPEN_BROWSERS: list = []
_CDP_BROWSERS_BY_URL: dict[str, object] = {}


def _get_playwright():
    """Persistent Playwright instance (only used when keep-open is enabled)."""
    global _PW
    if _PW is None:
        _PW = sync_playwright().start()
    return _PW


@atexit.register
def _cleanup_keep_open_browsers() -> None:
    global _PW
    for b in list(_KEEP_OPEN_BROWSERS):
        try:
            b.close()
        except Exception:
            pass
    _KEEP_OPEN_BROWSERS.clear()
    for browser in list(_CDP_BROWSERS_BY_URL.values()):
        try:
            browser.close()
        except Exception:
            pass
    _CDP_BROWSERS_BY_URL.clear()
    if _PW is not None:
        try:
            _PW.stop()
        except Exception:
            pass
        _PW = None


def _candidate_cdp_urls() -> list[str]:
    urls: list[str] = []
    explicit = (os.getenv("PLAYWRIGHT_CDP_URL") or "").strip()
    if explicit:
        urls.append(explicit)
    explicit_many = (os.getenv("PLAYWRIGHT_CDP_URLS") or "").strip()
    if explicit_many:
        urls.extend([u.strip() for u in explicit_many.split(",") if u.strip()])
    # Common local CDP endpoints used by operators.
    # Edge/Chrome can both run on any port, but these defaults are typical.
    urls.append("http://127.0.0.1:9222")
    urls.append("http://127.0.0.1:9223")
    # Keep order and uniqueness stable.
    seen: set[str] = set()
    unique_urls: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)
    return unique_urls


def _refresh_cdp_browsers() -> None:
    """Connect to CDP endpoints (if available) so we can inspect existing Edge tabs."""
    pw = _get_playwright()
    for cdp_url in _candidate_cdp_urls():
        existing = _CDP_BROWSERS_BY_URL.get(cdp_url)
        if existing is not None:
            try:
                _ = existing.contexts
                continue
            except Exception:
                _CDP_BROWSERS_BY_URL.pop(cdp_url, None)
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            _CDP_BROWSERS_BY_URL[cdp_url] = browser
            logger.info("fill_dms_service: connected to browser CDP at %s", cdp_url)
        except Exception:
            # Endpoint may not be available; retry on next call.
            continue


def _launch_managed_browser_for_site(base_url: str):
    """Launch Edge/Chrome for operators when no debuggable session is currently available."""
    pw = _get_playwright()
    channels = ["msedge", "chrome"]
    headless = not bool(DMS_PLAYWRIGHT_HEADED)
    for channel in channels:
        try:
            browser = pw.chromium.launch(channel=channel, headless=headless)
            _KEEP_OPEN_BROWSERS.append(browser)
            context = browser.new_context()
            page = context.new_page()
            page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            logger.info("fill_dms_service: launched managed %s browser for %s", channel, base_url)
            return page, channel
        except Exception as exc:
            logger.warning("fill_dms_service: failed to launch %s browser: %s", channel, exc)
            continue
    return None, None


def _get_or_open_site_page(base_url: str, site_label: str):
    """
    Try finding an already-open site tab.
    If not found, open a managed browser tab for operator login and return a guidance error.
    """
    page = _find_open_site_page(base_url)
    if page is not None:
        return page, None

    opened_page, channel = _launch_managed_browser_for_site(base_url)
    if opened_page is not None:
        return None, f"{site_label} Opened. Please login. And then press button again"

    return None, (
        f"{site_label} site not open. Please open {site_label} site and keep it logged in. "
        "Start Edge or Chrome with a remote debugging port (for example 9222), or allow the app "
        "to auto-open one and retry."
    )


def _find_open_site_page(base_url: str):
    """Find an already-open tab for the given site base URL."""
    target = (base_url or "").rstrip("/")
    if not target:
        return None
    _refresh_cdp_browsers()
    browsers_to_scan = list(_KEEP_OPEN_BROWSERS) + list(_CDP_BROWSERS_BY_URL.values())
    for browser in browsers_to_scan:
        try:
            for context in browser.contexts:
                for page in context.pages:
                    url = (page.url or "").rstrip("/")
                    if url.startswith(target) or target in url:
                        return page
        except Exception:
            continue
    return None


def _fill_vahan_and_scrape(
    page,
    vahan_base_url: str,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
) -> tuple[str | None, float]:
    """
    Fill dummy Vahan registration flow, move the file to worklist, and return (application_id, rto_fees).
    rto_fees = 1% of vehicle_price + 200 (same as dummy Vahan formula).
    """
    base = vahan_base_url.rstrip("/")
    # Dummy Vahan rejects 0/blank vehicle_price because the input is required with min=1.
    # When DMS did not provide vehicle_price, fall back to a safe positive value so the
    # registration step can still continue and compute fees.
    effective_vehicle_price = float(vehicle_price or 0)
    if effective_vehicle_price <= 0:
        effective_vehicle_price = 72000.0
    effective_rto_dealer_id = (rto_dealer_id or "").strip() or "RTO100001"
    effective_customer_name = (customer_name or "").strip() or "Customer"
    effective_chassis_no = (chassis_no or "").strip() or "UNKNOWN-CHASSIS"
    effective_vehicle_model = (vehicle_model or "").strip() or "UNKNOWN MODEL"
    effective_vehicle_colour = (vehicle_colour or "").strip() or "Black"
    effective_year_of_mfg = (year_of_mfg or "").strip() or "2026"
    page.goto(f"{base}/index.html", wait_until="domcontentloaded", timeout=20000)
    page.locator("#vahan-rto-dealer-id").evaluate("(el, value) => el.value = value", effective_rto_dealer_id)
    page.locator("#vahan-customer-name").evaluate("(el, value) => el.value = value", effective_customer_name)
    page.locator("#vahan-chassis-no").evaluate("(el, value) => el.value = value", effective_chassis_no)
    page.locator("#vahan-vehicle-model").evaluate("(el, value) => el.value = value", effective_vehicle_model)
    page.locator("#vahan-vehicle-colour").evaluate("(el, value) => el.value = value", effective_vehicle_colour)
    page.fill("#vahan-chassis-no-visible", effective_chassis_no)
    engine_tail = effective_chassis_no[-5:] if effective_chassis_no else "12345"
    page.fill("#vahan-engine-last5-visible", engine_tail)
    page.locator("#vahan-fuel-type").evaluate("(el, value) => el.value = value", (fuel_type or "Petrol").strip())
    page.locator("#vahan-year-of-mfg").evaluate("(el, value) => el.value = value", effective_year_of_mfg)
    page.locator("#vahan-total-cost").evaluate("(el, value) => el.value = value", str(int(effective_vehicle_price)))
    page.click("#vahan-reg-submit")
    page.wait_for_url("**/application.html*", timeout=15000)
    page.click("#vahan-save-movement")
    page.wait_for_url("**/search.html*", timeout=15000)
    url = page.url
    application_id = None
    if "application_id=" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        ids = qs.get("application_id", [])
        if ids:
            application_id = ids[0]
    # Wait for search result to render (auto-submit on load), then scrape rto_fees
    rto_fees = 200.0
    total = effective_vehicle_price
    fallback_fees = round(total * 0.01 + 200, 2) if total else 200.0
    try:
        page.wait_for_selector("#vahan-result-section:visible", timeout=8000)
        el = page.locator("#vahan-result-rto-fees")
        if el.count() > 0:
            val = el.get_attribute("data-rto-fees")
            if val and val.strip():
                rto_fees = round(float(val), 2)
            else:
                rto_fees = fallback_fees
        else:
            rto_fees = fallback_fees
    except Exception:
        rto_fees = fallback_fees
    return (application_id, rto_fees)


def _complete_vahan_upload_step(page) -> bool:
    """Advance the dummy Vahan flow to the files-uploaded / cart checkpoint."""
    page.click("#vahan-upload-btn")
    page.wait_for_selector("#vahan-upload-status[data-uploaded='1']", timeout=8000)
    return True


def _split_name(full_name: str | None) -> tuple[str, str]:
    if not full_name or not full_name.strip():
        return "", ""
    parts = full_name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0], "")


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name (one segment) for ocr_output and uploads."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _write_data_from_dms(ocr_output_dir: Path, subfolder: str, customer: dict, vehicle: dict) -> None:
    """Write all pulled DMS data to ocr_output/subfolder/Data from DMS.txt (subfolder = mobile_ddmmyy)."""
    safe_name = _safe_subfolder_name(subfolder)
    base = Path(ocr_output_dir).resolve()
    dir_path = base / safe_name
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / "Data from DMS.txt"
    lines = ["Data from DMS", ""]

    lines.append("--- Customer (filled on enquiry) ---")
    for label, key in [
        ("Name", "name"),
        ("Address", "address"),
        ("State", "state"),
        ("Pin code", "pin_code"),
    ]:
        val = customer.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")
    mobile = customer.get("mobile_number") or customer.get("mobile")
    lines.append(f"Mobile: {mobile or '—'}")

    lines.append("")
    lines.append("--- Vehicle (from DMS search result) ---")
    for label, key in [
        ("Key num", "key_num"),
        ("Frame / Chassis num", "frame_num"),
        ("Engine num", "engine_num"),
        ("Model", "model"),
        ("Color", "color"),
        ("Cubic capacity", "cubic_capacity"),
        ("Seating capacity", "seating_capacity"),
        ("Body type", "body_type"),
        ("Vehicle type", "vehicle_type"),
        ("Num cylinders", "num_cylinders"),
        ("Horsepower", "horse_power"),
        ("Vehicle price", "vehicle_price"),
        ("Year of Mfg", "year_of_mfg"),
    ]:
        val = vehicle.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_vehicle_price(vehicle: dict) -> float:
    """Parse vehicle_price from vehicle (e.g. '72000' or '72,000') for Vahan automation."""
    raw = vehicle.get("vehicle_price")
    if raw is None:
        raw = vehicle.get("total_amount")
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_amount(value: object | None) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value).strip()


def _parse_float_or_zero(value: object | None) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _require_customer_vehicle_ids(customer_id: int | None, vehicle_id: int | None, view_name: str) -> tuple[int, int]:
    if customer_id is None or vehicle_id is None:
        raise ValueError(f"customer_id and vehicle_id are required because automation now reads from {view_name} only")
    return customer_id, vehicle_id


def _load_form_vahan_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    if customer_id is None or vehicle_id is None:
        return {}
    try:
        return form_vahan_repo.get_by_customer_vehicle(customer_id, vehicle_id) or {}
    except Exception as exc:
        logger.warning(
            "fill_dms_service: form_vahan_view lookup failed customer_id=%s vehicle_id=%s: %s",
            customer_id,
            vehicle_id,
            exc,
        )
        return {}


def _load_required_form_vahan_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "form_vahan_view")
    row = _load_form_vahan_row(cid, vid)
    if not row:
        raise ValueError(f"No form_vahan_view row found for customer_id={cid} vehicle_id={vid}")
    return row


def _load_form_dms_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    if customer_id is None or vehicle_id is None:
        return {}
    try:
        return form_dms_repo.get_by_customer_vehicle(customer_id, vehicle_id) or {}
    except Exception as exc:
        logger.warning(
            "fill_dms_service: form_dms_view lookup failed customer_id=%s vehicle_id=%s: %s",
            customer_id,
            vehicle_id,
            exc,
        )
        return {}


def _load_required_form_dms_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "form_dms_view")
    row = _load_form_dms_row(cid, vid)
    if not row:
        raise ValueError(f"No form_dms_view row found for customer_id={cid} vehicle_id={vid}")
    return row


def _build_dms_fill_values(customer_id: int | None, vehicle_id: int | None, subfolder: str | None = None) -> dict:
    row = _load_required_form_dms_row(customer_id, vehicle_id)
    first_name = _clean_text(row.get("Contact First Name"))
    last_name = _clean_text(row.get("Contact Last Name"))
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    effective_subfolder = _clean_text(row.get("subfolder")) or _clean_text(subfolder)
    return {
        "row": row,
        "subfolder": effective_subfolder,
        "customer_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "mobile_phone": _clean_text(row.get("Mobile Phone #"))[:10],
        "address_line_1": _clean_text(row.get("Address Line 1"))[:80],
        "state": _clean_text(row.get("State")),
        "pin_code": _clean_text(row.get("Pin Code"))[:6],
        "key_partial": _clean_text(row.get("Key num (partial)"))[:8],
        "frame_partial": _clean_text(row.get("Frame / Chassis num (partial)"))[:12],
        "engine_partial": _clean_text(row.get("Engine num (partial)"))[:12],
        "customer_export": {
            "name": full_name,
            "address": _clean_text(row.get("Address Line 1")),
            "state": _clean_text(row.get("State")),
            "pin_code": _clean_text(row.get("Pin Code")),
            "mobile_number": _clean_text(row.get("Mobile Phone #")),
        },
    }


def _build_vahan_fill_values(customer_id: int | None, vehicle_id: int | None, subfolder: str | None = None) -> dict:
    row = _load_required_form_vahan_row(customer_id, vehicle_id)
    vehicle_price = _parse_float_or_zero(row.get("vehicle_price"))
    if vehicle_price <= 0:
        raise ValueError(
            f"form_vahan_view.vehicle_price is empty for customer_id={customer_id} vehicle_id={vehicle_id}; "
            "run DMS first so vehicle_price is stored in vehicle_master"
        )
    effective_subfolder = _clean_text(row.get("subfolder")) or _clean_text(subfolder)
    return {
        "row": row,
        "subfolder": effective_subfolder,
        "rto_dealer_id": _clean_text(row.get("rto_dealer_id")) or "RTO100001",
        "customer_name": _clean_text(row.get("Owner Name *")) or "Customer",
        "chassis_no": _clean_text(row.get("Chassis No *")) or "UNKNOWN-CHASSIS",
        "vehicle_model": _clean_text(row.get("vehicle_model")) or "UNKNOWN MODEL",
        "vehicle_colour": _clean_text(row.get("vehicle_colour")) or "Black",
        "fuel_type": _clean_text(row.get("fuel_type")) or "Petrol",
        "year_of_mfg": _clean_text(row.get("year_of_mfg")) or "2026",
        "vehicle_price": vehicle_price,
    }


def _write_dms_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    customer_name: str,
    mobile_number: str,
    address: str,
    state: str,
    pin_code: str,
    key_no: str,
    frame_no: str,
    engine_no: str,
) -> None:
    if not subfolder or not str(subfolder).strip():
        return

    row = _load_form_dms_row(customer_id, vehicle_id)
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "DMS_Form_Values.txt"

    row_first_name = _clean_text(row.get("Contact First Name"))
    row_last_name = _clean_text(row.get("Contact Last Name"))
    first_name, last_name = _split_name(customer_name or "")
    effective_first_name = _clean_text(first_name) or row_first_name
    effective_last_name = _clean_text(last_name) or row_last_name
    effective_mobile = _clean_text(mobile_number)[:10] or _clean_text(row.get("Mobile Phone #"))[:10]
    effective_address = _clean_text(address)[:80] or _clean_text(row.get("Address Line 1"))[:80]
    effective_state = _clean_text(state) or _clean_text(row.get("State"))
    effective_pin = _clean_text(pin_code)[:6] or _clean_text(row.get("Pin Code"))[:6]
    effective_key = _clean_text(key_no)[:8] or _clean_text(row.get("Key num (partial)"))
    effective_frame = _clean_text(frame_no)[:12] or _clean_text(row.get("Frame / Chassis num (partial)"))
    effective_engine = _clean_text(engine_no)[:12] or _clean_text(row.get("Engine num (partial)"))

    label_values: list[tuple[str, str]] = [
        ("Mr/Ms", _clean_text(row.get("Mr/Ms")) or "Mr."),
        ("Contact First Name", effective_first_name),
        ("Contact Last Name", effective_last_name),
        ("Mobile Phone #", effective_mobile),
        ("State", effective_state),
        ("Address Line 1", effective_address),
        ("Pin Code", effective_pin),
        ("Key num (partial)", effective_key),
        ("Frame / Chassis num (partial)", effective_frame),
        ("Engine num (partial)", effective_engine),
    ]

    runtime_values: list[tuple[str, str]] = [
        ("sales_id", _clean_text(row.get("sales_id"))),
        ("customer_id", _clean_text(customer_id or row.get("customer_id"))),
        ("vehicle_id", _clean_text(vehicle_id or row.get("vehicle_id"))),
        ("dealer_id", _clean_text(row.get("dealer_id"))),
        ("subfolder", safe_subfolder),
        ("dealer_name", _clean_text(row.get("dealer_name"))),
        ("oem_name", _clean_text(row.get("oem_name"))),
        ("source_customer_name", _clean_text(customer_name)),
        ("source_mobile_number", _clean_text(mobile_number)),
        ("source_address", _clean_text(address)),
        ("source_state", _clean_text(state)),
        ("source_pin_code", _clean_text(pin_code)),
        ("source_key_no", _clean_text(key_no)),
        ("source_frame_no", _clean_text(frame_no)),
        ("source_engine_no", _clean_text(engine_no)),
        ("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]

    lines = ["DMS Form Values", "", "--- Values sent to DMS labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")

    lines.extend(["", "--- Runtime values used by Playwright ---"])
    for label, value in runtime_values:
        lines.append(f"{label}: {value or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_vahan_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
    application_id: str | None,
    rto_fees: float | None,
) -> None:
    if not subfolder or not str(subfolder).strip():
        return

    row = _load_form_vahan_row(customer_id, vehicle_id)
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "Vahan_Form_Values.txt"

    effective_rto_dealer_id = _clean_text(rto_dealer_id) or _clean_text(row.get("rto_dealer_id")) or "RTO100001"
    effective_customer_name = _clean_text(customer_name) or _clean_text(row.get("Owner Name *")) or "Customer"
    effective_chassis_no = _clean_text(chassis_no) or _clean_text(row.get("Chassis No *")) or "UNKNOWN-CHASSIS"
    effective_vehicle_model = _clean_text(vehicle_model) or _clean_text(row.get("vehicle_model")) or "UNKNOWN MODEL"
    effective_vehicle_colour = _clean_text(vehicle_colour) or _clean_text(row.get("vehicle_colour")) or "Black"
    effective_fuel_type = _clean_text(fuel_type) or _clean_text(row.get("fuel_type")) or "Petrol"
    effective_year_of_mfg = _clean_text(year_of_mfg) or _clean_text(row.get("year_of_mfg")) or "2026"
    effective_vehicle_price = float(vehicle_price or 0)
    if effective_vehicle_price <= 0:
        effective_vehicle_price = 72000.0

    label_values: list[tuple[str, str]] = [
        ("Registration Type *", _clean_text(row.get("Registration Type *")) or "New Registration"),
        ("Chassis No *", effective_chassis_no),
        ("Engine/Motor No (Last 5 Chars)", effective_chassis_no[-5:] if effective_chassis_no else "12345"),
        ("Purchase Delivery Date", _clean_text(row.get("Purchase Delivery Date"))),
        ("Do You want to Opt Choice Number / Fancy Number / Retention Number", _clean_text(row.get("Do You want to Opt Choice Number / Fancy Number / Retention Number")) or "SELECT"),
        ("Owner Name *", effective_customer_name),
        ("Owner Type", _clean_text(row.get("Owner Type")) or "Individual"),
        ("Son/Wife/Daughter of", _clean_text(row.get("Son/Wife/Daughter of"))),
        ("Ownership Serial", _clean_text(row.get("Ownership Serial")) or "1"),
        ("Aadhaar Mode", _clean_text(row.get("Aadhaar Mode")) or "Aadhaar OTP"),
        ("Category *", _clean_text(row.get("Category *")) or "General"),
        ("Mobile No", _clean_text(row.get("Mobile No"))),
        ("PAN Card", _clean_text(row.get("PAN Card"))),
        ("Voter ID", _clean_text(row.get("Voter ID"))),
        ("Aadhaar No", _clean_text(row.get("Aadhaar No"))),
        ("Permanent Address", _clean_text(row.get("Permanent Address"))),
        ("House No & Street Name", _clean_text(row.get("House No & Street Name"))),
        ("Village/Town/City", _clean_text(row.get("Village/Town/City"))),
        ("Insurance Type", _clean_text(row.get("Insurance Type"))),
        ("Insurer", _clean_text(row.get("Insurer"))),
        ("Policy No", _clean_text(row.get("Policy No"))),
        ("Insurance From (DD-MMM-YYYY)", _clean_text(row.get("Insurance From (DD-MMM-YYYY)"))),
        ("Insurance Upto (DD-MMM-YYYY)", _clean_text(row.get("Insurance Upto (DD-MMM-YYYY)"))),
        ("Insured Declared Value", _clean_text(row.get("Insured Declared Value"))),
        ("Please Select Series Type", _clean_text(row.get("Please Select Series Type")) or "State Series"),
        ("Financier / Bank", _clean_text(row.get("Financier / Bank"))),
        ("Application No", _clean_text(application_id) or _clean_text(row.get("Application No"))),
        ("Assigned Office & Action", _clean_text(row.get("Assigned Office & Action")) or effective_rto_dealer_id),
        ("Registration No", _clean_text(row.get("Registration No"))),
        ("Amount", _format_amount(rto_fees) or _clean_text(row.get("Amount"))),
    ]

    runtime_values: list[tuple[str, str]] = [
        ("sales_id", _clean_text(row.get("sales_id"))),
        ("customer_id", _clean_text(customer_id or row.get("customer_id"))),
        ("vehicle_id", _clean_text(vehicle_id or row.get("vehicle_id"))),
        ("dealer_id", _clean_text(row.get("dealer_id"))),
        ("subfolder", safe_subfolder),
        ("rto_dealer_id", effective_rto_dealer_id),
        ("vehicle_model", effective_vehicle_model),
        ("vehicle_colour", effective_vehicle_colour),
        ("fuel_type", effective_fuel_type),
        ("year_of_mfg", effective_year_of_mfg),
        ("vehicle_price", _format_amount(effective_vehicle_price)),
        ("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]

    lines = ["Vahan Form Values", "", "--- Values sent to Vahan labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")

    lines.extend(["", "--- Runtime values used by Playwright ---"])
    for label, value in runtime_values:
        lines.append(f"{label}: {value or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _run_vahan_in_context(
    context,
    vahan_base_url: str,
    *,
    customer_id: int | None,
    vehicle_id: int | None,
    subfolder: str | None,
    ocr_output_dir: Path | None,
    complete_upload_step: bool,
) -> dict:
    """Run Vahan using an existing browser context so batches can reuse one session."""
    vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
    effective_subfolder = vahan_values.get("subfolder") or subfolder
    page = context.new_page()
    page.set_default_timeout(15_000)
    try:
        app_id, fees = _fill_vahan_and_scrape(
            page,
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
        )
        added_to_cart = False
        if complete_upload_step:
            added_to_cart = _complete_vahan_upload_step(page)
        if ocr_output_dir is not None and effective_subfolder:
            _write_vahan_form_values(
                ocr_output_dir=ocr_output_dir,
                subfolder=effective_subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                rto_dealer_id=vahan_values["rto_dealer_id"],
                customer_name=vahan_values["customer_name"],
                chassis_no=vahan_values["chassis_no"],
                vehicle_model=vahan_values["vehicle_model"],
                vehicle_colour=vahan_values["vehicle_colour"],
                fuel_type=vahan_values["fuel_type"],
                year_of_mfg=vahan_values["year_of_mfg"],
                vehicle_price=vahan_values["vehicle_price"],
                application_id=app_id,
                rto_fees=fees,
            )
        return {
            "application_id": app_id,
            "rto_fees": fees,
            "added_to_cart": added_to_cart,
            "subfolder": effective_subfolder,
        }
    finally:
        page.close()


def run_fill_vahan_batch_row(
    context,
    vahan_base_url: str,
    *,
    customer_id: int,
    vehicle_id: int,
    subfolder: str | None,
    ocr_output_dir: Path | None,
) -> dict:
    """Batch-safe Vahan helper that reuses one browser/context and stops after cart upload."""
    del context  # Existing open tab mode does not create/reuse server-owned contexts.
    page, open_error = _get_or_open_site_page(vahan_base_url, "Vahan")
    if page is None:
        raise ValueError(open_error or "Vahan site not open. Please open Vahan site and keep it logged in.")
    vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
    app_id, fees = _fill_vahan_and_scrape(
        page,
        vahan_base_url=vahan_base_url.strip(),
        rto_dealer_id=vahan_values["rto_dealer_id"],
        customer_name=vahan_values["customer_name"],
        chassis_no=vahan_values["chassis_no"],
        vehicle_model=vahan_values["vehicle_model"],
        vehicle_colour=vahan_values["vehicle_colour"],
        fuel_type=vahan_values["fuel_type"],
        year_of_mfg=vahan_values["year_of_mfg"],
        vehicle_price=vahan_values["vehicle_price"],
    )
    added_to_cart = _complete_vahan_upload_step(page)
    if ocr_output_dir is not None and (vahan_values.get("subfolder") or subfolder):
        _write_vahan_form_values(
            ocr_output_dir=ocr_output_dir,
            subfolder=vahan_values.get("subfolder") or subfolder,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
            application_id=app_id,
            rto_fees=fees,
        )
    return {
        "application_id": app_id,
        "rto_fees": fees,
        "added_to_cart": added_to_cart,
        "subfolder": vahan_values.get("subfolder") or subfolder,
    }


def update_vehicle_master_from_dms(vehicle_id: int, scraped: dict) -> None:
    """Update vehicle_master with DMS-scraped data (chassis, engine, key_num, model, colour, seating_capacity, etc.)."""
    from app.db import get_connection

    chassis = (scraped.get("frame_num") or "").strip() or None
    engine = (scraped.get("engine_num") or "").strip() or None
    key_num = (scraped.get("key_num") or "").strip() or None
    model = (scraped.get("model") or "").strip() or None
    colour = (scraped.get("color") or "").strip() or None
    cubic_capacity = scraped.get("cubic_capacity")
    seating_capacity = scraped.get("seating_capacity")
    body_type = (scraped.get("body_type") or "").strip() or None
    vehicle_type = (scraped.get("vehicle_type") or "").strip() or None
    num_cylinders = scraped.get("num_cylinders")
    horse_power = scraped.get("horse_power")
    year_of_mfg = scraped.get("year_of_mfg")
    vehicle_price = scraped.get("vehicle_price")
    if vehicle_price is None:
        vehicle_price = scraped.get("total_amount")
    if cubic_capacity:
        try:
            cubic_capacity = float(str(cubic_capacity).replace(",", ""))
        except (ValueError, TypeError):
            cubic_capacity = None
    if seating_capacity:
        try:
            seating_capacity = int(str(seating_capacity).strip())
        except (ValueError, TypeError):
            seating_capacity = None
    if num_cylinders:
        try:
            num_cylinders = int(str(num_cylinders).strip())
        except (ValueError, TypeError):
            num_cylinders = None
    if horse_power:
        try:
            horse_power = float(str(horse_power).replace(",", ""))
        except (ValueError, TypeError):
            horse_power = None
    if year_of_mfg:
        try:
            year_of_mfg = int(str(year_of_mfg).strip())
        except (ValueError, TypeError):
            year_of_mfg = None
    if vehicle_price:
        try:
            vehicle_price = float(str(vehicle_price).replace(",", ""))
        except (ValueError, TypeError):
            vehicle_price = None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_master SET
                    chassis = COALESCE(%s, chassis),
                    engine = COALESCE(%s, engine),
                    key_num = COALESCE(%s, key_num),
                    model = COALESCE(%s, model),
                    colour = COALESCE(%s, colour),
                    cubic_capacity = COALESCE(%s, cubic_capacity),
                    seating_capacity = COALESCE(%s, seating_capacity),
                    body_type = COALESCE(%s, body_type),
                    vehicle_type = COALESCE(%s, vehicle_type),
                    num_cylinders = COALESCE(%s, num_cylinders),
                    horse_power = COALESCE(%s, horse_power),
                    year_of_mfg = COALESCE(%s, year_of_mfg),
                    vehicle_price = COALESCE(%s, vehicle_price)
                WHERE vehicle_id = %s
                """,
                (chassis, engine, key_num, model, colour, cubic_capacity, seating_capacity, body_type, vehicle_type, num_cylinders, horse_power, year_of_mfg, vehicle_price, vehicle_id),
            )
            conn.commit()
            if cur.rowcount > 0:
                logger.info("fill_dms: updated vehicle_master vehicle_id=%s with DMS data", vehicle_id)
    finally:
        conn.close()


def run_fill_dms_only(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Run only DMS steps: login, enquiry, vehicle search, scrape, PDFs.
    Separate Playwright process. Returns vehicle, pdfs_saved, error.
    """
    result: dict = {"vehicle": {}, "pdfs_saved": [], "error": None}
    if not dms_base_url:
        result["error"] = "DMS_BASE_URL not set"
        return result
    ocr_dir = Path(ocr_output_dir or OCR_OUTPUT_DIR).resolve()
    try:
        dms_values = _build_dms_fill_values(customer_id, vehicle_id, subfolder)
    except Exception as e:
        result["error"] = str(e)
        return result
    effective_subfolder = dms_values.get("subfolder") or subfolder
    subfolder_path = uploads_dir / effective_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("fill_dms_service: run_fill_dms_only starting dms=%s", dms_base_url[:50])
        page, open_error = _get_or_open_site_page(dms_base_url, "DMS")
        if page is None:
            result["error"] = open_error
            return result

        base = dms_base_url.rstrip("/")
        page.set_default_timeout(12_000)
        page.goto(f"{base}/enquiry.html", wait_until="domcontentloaded", timeout=20000)

        mobile_phone = dms_values["mobile_phone"]
        addr = dms_values["address_line_1"]
        state = dms_values["state"]
        pin = dms_values["pin_code"]
        key_partial = dms_values["key_partial"]
        frame_partial = dms_values["frame_partial"]
        engine_partial = dms_values["engine_partial"]

        page.fill("#dms-contact-first-name", dms_values["first_name"])
        page.fill("#dms-contact-last-name", dms_values["last_name"])
        page.fill("#dms-mobile-phone", mobile_phone)
        if addr:
            page.fill("#dms-address-line-1", addr)
        if state:
            try:
                page.select_option("#dms-state", label=state)
            except Exception:
                pass
        if pin:
            page.fill("#dms-pin-code", pin)
        page.click("#dms-submit-enquiry")
        page.wait_for_timeout(10)

        page.goto(f"{base}/vehicle.html", wait_until="domcontentloaded", timeout=15000)
        _write_dms_form_values(
            ocr_output_dir=ocr_dir,
            subfolder=effective_subfolder,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            customer_name=dms_values["customer_name"],
            mobile_number=mobile_phone,
            address=addr,
            state=state,
            pin_code=pin,
            key_no=key_partial,
            frame_no=frame_partial,
            engine_no=engine_partial,
        )
        page.fill("#dms-vehicle-key", key_partial)
        page.fill("#dms-vehicle-frame", frame_partial)
        page.fill("#dms-vehicle-engine", engine_partial)
        page.click("#dms-vehicle-search")
        page.wait_for_timeout(10)

        page.wait_for_selector("#dms-vehicle-results:visible", timeout=8000)
        row = page.locator("#dms-vehicle-results-table tbody tr").first
        if row.count() > 0:
            cells = row.locator("td")
            n = cells.count()
            if n >= 13:
                result["vehicle"] = {
                    "key_num": cells.nth(0).inner_text().strip(),
                    "frame_num": cells.nth(1).inner_text().strip(),
                    "engine_num": cells.nth(2).inner_text().strip(),
                    "model": cells.nth(3).inner_text().strip(),
                    "color": cells.nth(4).inner_text().strip(),
                    "cubic_capacity": cells.nth(5).inner_text().strip(),
                    "seating_capacity": cells.nth(6).inner_text().strip(),
                    "body_type": cells.nth(7).inner_text().strip(),
                    "vehicle_type": cells.nth(8).inner_text().strip(),
                    "num_cylinders": cells.nth(9).inner_text().strip(),
                    "horse_power": cells.nth(10).inner_text().strip(),
                    "vehicle_price": cells.nth(11).inner_text().strip(),
                    "year_of_mfg": cells.nth(12).inner_text().strip(),
                }
        logger.info("fill_dms_service: run_fill_dms_only scraped vehicle=%s", result.get("vehicle"))
        if vehicle_id and result.get("vehicle"):
            try:
                update_vehicle_master_from_dms(vehicle_id, result.get("vehicle") or {})
            except Exception as exc:
                logger.warning("fill_dms_service: vehicle_master update failed vehicle_id=%s: %s", vehicle_id, exc)

        page.goto(f"{base}/reports.html", wait_until="domcontentloaded", timeout=15000)
        path21 = subfolder_path / "form21.pdf"
        path22 = subfolder_path / "form22.pdf"
        path_invoice = subfolder_path / "invoice_details.pdf"
        for url_suffix, path, name in [
            ("form21.pdf", path21, "form21.pdf"),
            ("form22.pdf", path22, "form22.pdf"),
            ("invoice_details.pdf", path_invoice, "invoice_details.pdf"),
        ]:
            try:
                r = page.request.get(f"{base}/downloads/{url_suffix}", timeout=15000)
                if r.ok:
                    path.write_bytes(r.body())
                    result["pdfs_saved"].append(name)
            except Exception:
                pass
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: run_fill_dms_only PlaywrightTimeout %s", e)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("fill_dms_service: run_fill_dms_only exception %s", e)

    try:
        _write_data_from_dms(ocr_dir, effective_subfolder, dms_values.get("customer_export") or {}, result.get("vehicle") or {})
    except Exception as e:
        result["error"] = (result.get("error") or "") + f"; DMS file write: {e!s}"
    return result


def run_fill_vahan_only(
    vahan_base_url: str,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Run only Vahan step: fill registration form, submit, scrape application_id and rto_fees.
    Separate Playwright process (new browser). Returns application_id, rto_fees, error.
    """
    result: dict = {"application_id": None, "rto_fees": None, "added_to_cart": False, "error": None}
    if not vahan_base_url or not vahan_base_url.strip():
        result["error"] = "vahan_base_url required"
        return result
    try:
        logger.info("fill_dms_service: run_fill_vahan_only starting")
        page, open_error = _get_or_open_site_page(vahan_base_url, "Vahan")
        if page is None:
            result["error"] = open_error
            return result
        vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
        app_id, fees = _fill_vahan_and_scrape(
            page,
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
        )
        result.update(
            {
                "application_id": app_id,
                "rto_fees": fees,
                "added_to_cart": False,
                "subfolder": vahan_values.get("subfolder") or subfolder,
            }
        )
        if ocr_output_dir is not None and (vahan_values.get("subfolder") or subfolder):
            _write_vahan_form_values(
                ocr_output_dir=ocr_output_dir,
                subfolder=vahan_values.get("subfolder") or subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                rto_dealer_id=vahan_values["rto_dealer_id"],
                customer_name=vahan_values["customer_name"],
                chassis_no=vahan_values["chassis_no"],
                vehicle_model=vahan_values["vehicle_model"],
                vehicle_colour=vahan_values["vehicle_colour"],
                fuel_type=vahan_values["fuel_type"],
                year_of_mfg=vahan_values["year_of_mfg"],
                vehicle_price=vahan_values["vehicle_price"],
                application_id=app_id,
                rto_fees=fees,
            )
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: run_fill_vahan_only PlaywrightTimeout %s", e)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("fill_dms_service: run_fill_vahan_only exception %s", e)
    return result


def run_fill_dms(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    vahan_base_url: str | None = None,
    rto_dealer_id: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    headless: bool | None = None,
) -> dict:
    """
    Run Playwright: open DMS, login, fill enquiry, submit, go to Vehicle, search, scrape first row,
    go to Reports, save Form 21 and Form 22 into uploads_dir/subfolder.
    If vahan_base_url is set, then fill dummy Vahan registration and set result application_id and rto_fees.
    Writes pulled data to ocr_output_dir/subfolder/Data from DMS.txt (same subfolder as other OCR outputs).
    Returns dict with vehicle details (key_num, frame_num, ...), optional application_id, rto_fees, and any error.
    """
    result = run_fill_dms_only(
        dms_base_url=dms_base_url,
        subfolder=subfolder,
        customer=customer,
        vehicle=vehicle,
        login_user=login_user,
        login_password=login_password,
        uploads_dir=uploads_dir,
        ocr_output_dir=ocr_output_dir,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
    )
    if result.get("error"):
        return {
            "vehicle": result.get("vehicle") or {},
            "pdfs_saved": result.get("pdfs_saved") or [],
            "application_id": None,
            "rto_fees": None,
            "error": result.get("error"),
        }

    if vahan_base_url and vahan_base_url.strip():
        vahan_result = run_fill_vahan_only(
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=rto_dealer_id or "",
            customer_name=str((customer or {}).get("name") or ""),
            chassis_no=str((result.get("vehicle") or {}).get("frame_num") or (vehicle or {}).get("frame_no") or ""),
            vehicle_model=str((result.get("vehicle") or {}).get("model") or ""),
            vehicle_colour=str((result.get("vehicle") or {}).get("color") or ""),
            fuel_type=str((result.get("vehicle") or {}).get("fuel_type") or "Petrol"),
            year_of_mfg=str((result.get("vehicle") or {}).get("year_of_mfg") or ""),
            vehicle_price=_parse_vehicle_price(result.get("vehicle") or {}),
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
        )
        if vahan_result.get("error"):
            return {
                "vehicle": result.get("vehicle") or {},
                "pdfs_saved": result.get("pdfs_saved") or [],
                "application_id": None,
                "rto_fees": None,
                "error": vahan_result.get("error"),
            }
        return {
            "vehicle": result.get("vehicle") or {},
            "pdfs_saved": result.get("pdfs_saved") or [],
            "application_id": vahan_result.get("application_id"),
            "rto_fees": vahan_result.get("rto_fees"),
            "error": None,
        }

    return {
        "vehicle": result.get("vehicle") or {},
        "pdfs_saved": result.get("pdfs_saved") or [],
        "application_id": None,
        "rto_fees": None,
        "error": None,
    }
