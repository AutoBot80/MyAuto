"""
Fill DMS flow using Playwright: login, fill enquiry, search vehicle, scrape row, save PDFs.
Optionally fills dummy Vahan registration and returns application_id and rto_fees.
Runs in Edge only (channel msedge). Requires: pip install playwright && playwright install msedge.
Uses headed browser by default (set DMS_PLAYWRIGHT_HEADED=false for headless).
Writes pulled data to ocr_output/subfolder/Data from DMS.txt for consistency with other OCR outputs.
"""
import re
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.config import DMS_PLAYWRIGHT_HEADED, OCR_OUTPUT_DIR


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
    total_cost: float,
) -> tuple[str | None, float]:
    """
    Fill dummy Vahan registration form, submit, wait for redirect, return (application_id, rto_fees).
    rto_fees = 1% of total_cost + 200 (same as dummy Vahan formula).
    """
    base = vahan_base_url.rstrip("/")
    page.goto(f"{base}/index.html", wait_until="domcontentloaded", timeout=20000)
    page.fill("#vahan-rto-dealer-id", (rto_dealer_id or "").strip())
    page.fill("#vahan-customer-name", (customer_name or "").strip())
    page.fill("#vahan-chassis-no", (chassis_no or "").strip())
    page.fill("#vahan-vehicle-model", (vehicle_model or "").strip())
    page.fill("#vahan-vehicle-colour", (vehicle_colour or "").strip())
    page.select_option("#vahan-fuel-type", value=(fuel_type or "Petrol").strip())
    page.fill("#vahan-year-of-mfg", (year_of_mfg or "").strip())
    page.fill("#vahan-total-cost", str(int(total_cost)) if total_cost else "0")
    page.click("#vahan-reg-submit")
    page.wait_for_url("**/search.html*", timeout=15000)
    url = page.url
    application_id = None
    if "application_id=" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        ids = qs.get("application_id", [])
        if ids:
            application_id = ids[0]
    total = float(total_cost or 0)
    rto_fees = round(total * 0.01 + 200, 2) if total else 200.0
    return (application_id, rto_fees)


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
        ("Total amount", "total_amount"),
        ("Year of Mfg", "year_of_mfg"),
    ]:
        val = vehicle.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_total_cost(vehicle: dict) -> float:
    """Parse total_amount from vehicle (e.g. '72000' or '72,000') for Vahan total_cost."""
    raw = vehicle.get("total_amount")
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


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
) -> dict:
    """
    Run Playwright: open DMS, login, fill enquiry, submit, go to Vehicle, search, scrape first row,
    go to Reports, save Form 21 and Form 22 into uploads_dir/subfolder.
    If vahan_base_url is set, then fill dummy Vahan registration and set result application_id and rto_fees.
    Writes pulled data to ocr_output_dir/subfolder/Data from DMS.txt (same subfolder as other OCR outputs).
    Returns dict with vehicle details (key_num, frame_num, ...), optional application_id, rto_fees, and any error.
    """
    result: dict = {
        "vehicle": {},
        "pdfs_saved": [],
        "application_id": None,
        "rto_fees": None,
        "error": None,
    }
    if not dms_base_url:
        result["error"] = "DMS_BASE_URL not set"
        return result
    subfolder_path = uploads_dir / subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    ocr_dir = Path(ocr_output_dir or OCR_OUTPUT_DIR).resolve()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=not DMS_PLAYWRIGHT_HEADED,
            )
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            # Shorter default timeout so actions fail fast instead of feeling slow
            page.set_default_timeout(12_000)

            def accept_dialog(dialog):
                dialog.accept()

            page.on("dialog", accept_dialog)
            base = dms_base_url.rstrip("/")

            # 1) Login
            page.goto(f"{base}/", wait_until="domcontentloaded", timeout=20000)
            page.fill("#dms-username", login_user)
            page.fill("#dms-password", login_password)
            page.click("#dms-login-btn")
            page.wait_for_url("**/enquiry.html**", timeout=15000)

            # 2) Enquiry: fill customer and submit (only essential fields to speed up)
            first_name, last_name = _split_name(customer.get("name"))
            page.fill("#dms-contact-first-name", first_name or "")
            page.fill("#dms-contact-last-name", last_name or "")
            page.fill("#dms-mobile-phone", str(customer.get("mobile_number") or customer.get("mobile") or "")[:10])
            addr = (customer.get("address") or "")[:200]
            if addr:
                page.fill("#dms-address-line-1", addr)
            state = (customer.get("state") or "").strip()
            if state:
                try:
                    page.select_option("#dms-state", label=state)
                except Exception:
                    pass
            pin = str(customer.get("pin_code") or customer.get("pin") or "")[:6]
            if pin:
                page.fill("#dms-pin-code", pin)
            page.click("#dms-submit-enquiry")
            # Brief wait for dialog accept and page settle before navigating
            page.wait_for_timeout(50)

            # 3) Vehicle page: fill search keys and search
            page.goto(f"{base}/vehicle.html", wait_until="domcontentloaded", timeout=15000)
            key_partial = str(vehicle.get("key_no") or "").strip()[:8]
            frame_partial = str(vehicle.get("frame_no") or "").strip()[:12]
            engine_partial = str(vehicle.get("engine_no") or "").strip()[:12]
            page.fill("#dms-vehicle-key", key_partial)
            page.fill("#dms-vehicle-frame", frame_partial)
            page.fill("#dms-vehicle-engine", engine_partial)
            page.click("#dms-vehicle-search")
            page.wait_for_timeout(150)

            # 4) Scrape first result row (all 8 columns: key, frame, engine, model, color, cubic_capacity, total_amount, year_of_mfg)
            page.wait_for_selector("#dms-vehicle-results:visible", timeout=8000)
            row = page.locator("#dms-vehicle-results-table tbody tr").first
            if row.count() > 0:
                cells = row.locator("td")
                n = cells.count()
                if n >= 8:
                    result["vehicle"] = {
                        "key_num": cells.nth(0).inner_text().strip(),
                        "frame_num": cells.nth(1).inner_text().strip(),
                        "engine_num": cells.nth(2).inner_text().strip(),
                        "model": cells.nth(3).inner_text().strip(),
                        "color": cells.nth(4).inner_text().strip(),
                        "cubic_capacity": cells.nth(5).inner_text().strip(),
                        "total_amount": cells.nth(6).inner_text().strip(),
                        "year_of_mfg": cells.nth(7).inner_text().strip(),
                    }

            # 5) Reports: fetch Form 21 and Form 22 PDFs by URL (avoids download event / target="_blank" issues)
            page.goto(f"{base}/reports.html", wait_until="domcontentloaded", timeout=15000)
            path21 = subfolder_path / "form21.pdf"
            path22 = subfolder_path / "form22.pdf"
            try:
                r21 = page.request.get(f"{base}/downloads/form21.pdf", timeout=15000)
                if r21.ok:
                    path21.write_bytes(r21.body())
                    result["pdfs_saved"].append("form21.pdf")
            except Exception:
                pass
            try:
                r22 = page.request.get(f"{base}/downloads/form22.pdf", timeout=15000)
                if r22.ok:
                    path22.write_bytes(r22.body())
                    result["pdfs_saved"].append("form22.pdf")
            except Exception:
                pass

            # 6) Optional: fill dummy Vahan registration and scrape application_id + rto_fees
            if vahan_base_url and vahan_base_url.strip():
                try:
                    scraped = result.get("vehicle") or {}
                    vahan_page = context.new_page()
                    vahan_page.set_default_timeout(15_000)
                    total_cost = _parse_total_cost(scraped or vehicle)
                    app_id, fees = _fill_vahan_and_scrape(
                        vahan_page,
                        vahan_base_url=vahan_base_url.strip(),
                        rto_dealer_id=(rto_dealer_id or "").strip() or "RTO100001",
                        customer_name=str(customer.get("name") or ""),
                        chassis_no=str(scraped.get("frame_num") or vehicle.get("frame_no") or vehicle.get("frame_num") or ""),
                        vehicle_model=str(scraped.get("model") or vehicle.get("model") or ""),
                        vehicle_colour=str(scraped.get("color") or vehicle.get("color") or ""),
                        fuel_type="Petrol",
                        year_of_mfg=str(scraped.get("year_of_mfg") or vehicle.get("year_of_mfg") or ""),
                        total_cost=total_cost or 72000.0,
                    )
                    result["application_id"] = app_id
                    result["rto_fees"] = fees
                    vahan_page.close()
                except Exception as e:
                    result["error"] = (result.get("error") or "") + f"; Vahan: {e!s}"

            browser.close()
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
    except Exception as e:
        result["error"] = str(e)

    # Always write Data from DMS to ocr_output/mobile_ddmmyy/Data from DMS.txt
    try:
        _write_data_from_dms(ocr_dir, subfolder, customer, result.get("vehicle") or {})
    except Exception as e:
        result["error"] = (result.get("error") or "") + f"; DMS file write: {e!s}"
    return result
