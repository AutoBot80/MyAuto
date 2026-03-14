"""
Fill DMS flow using Playwright: login, fill enquiry, search vehicle, scrape row, download PDFs.
Run in a thread from the API to avoid blocking. Requires: pip install playwright && playwright install chromium.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def _split_name(full_name: str | None) -> tuple[str, str]:
    if not full_name or not full_name.strip():
        return "", ""
    parts = full_name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0], "")


def run_fill_dms(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
) -> dict:
    """
    Run Playwright: open DMS, login, fill enquiry, submit, go to Vehicle, search, scrape first row,
    go to Reports, download Form 21 and Form 22 into uploads_dir/subfolder.
    Returns dict with vehicle details (key_num, frame_num, engine_num, model, color, etc.) and any error.
    """
    result: dict = {
        "vehicle": {},
        "pdfs_saved": [],
        "error": None,
    }
    if not dms_base_url:
        result["error"] = "DMS_BASE_URL not set"
        return result
    subfolder_path = uploads_dir / subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            def accept_dialog(dialog):
                dialog.accept()

            page.on("dialog", accept_dialog)
            base = dms_base_url.rstrip("/")

            # 1) Login
            page.goto(f"{base}/", wait_until="domcontentloaded", timeout=15000)
            page.fill("#dms-username", login_user)
            page.fill("#dms-password", login_password)
            page.click("#dms-login-btn")
            page.wait_for_url(f"**/enquiry.html**", timeout=10000)

            # 2) Enquiry: fill customer and submit
            first_name, last_name = _split_name(customer.get("name"))
            page.fill("#dms-contact-first-name", first_name)
            page.fill("#dms-contact-last-name", last_name)
            page.fill("#dms-mobile-phone", str(customer.get("mobile_number") or customer.get("mobile") or "")[:10])
            addr = customer.get("address") or ""
            page.fill("#dms-address-line-1", addr[:200] if addr else "")
            state = (customer.get("state") or "").strip()
            if state:
                try:
                    page.select_option("#dms-state", label=state)
                except Exception:
                    pass
            page.fill("#dms-pin-code", str(customer.get("pin_code") or customer.get("pin") or "")[:6])
            page.click("#dms-submit-enquiry")

            # 3) Vehicle page: search
            page.goto(f"{base}/vehicle.html", wait_until="domcontentloaded", timeout=10000)
            key_partial = str(vehicle.get("key_no") or "")[:8]
            frame_partial = str(vehicle.get("frame_no") or "")[:12]
            engine_partial = str(vehicle.get("engine_no") or "")[:12]
            page.fill("#dms-vehicle-key", key_partial)
            page.fill("#dms-vehicle-frame", frame_partial)
            page.fill("#dms-vehicle-engine", engine_partial)
            page.click("#dms-vehicle-search")

            # 4) Scrape first result row
            page.wait_for_selector("#dms-vehicle-results:visible", timeout=5000)
            row = page.locator("#dms-vehicle-results-table tbody tr").first
            if row.count() > 0:
                cells = row.locator("td")
                if cells.count() >= 8:
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

            # 5) Reports: download Form 21 and Form 22
            page.goto(f"{base}/reports.html", wait_until="domcontentloaded", timeout=10000)
            with page.expect_download(timeout=10000) as download_info:
                page.click("#dms-reports-form21")
            download = download_info.value
            path21 = subfolder_path / "form21.pdf"
            download.save_as(path21)
            result["pdfs_saved"].append("form21.pdf")

            with page.expect_download(timeout=10000) as download_info2:
                page.click("#dms-reports-form22")
            download2 = download_info2.value
            path22 = subfolder_path / "form22.pdf"
            download2.save_as(path22)
            result["pdfs_saved"].append("form22.pdf")

            browser.close()
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
    except Exception as e:
        result["error"] = str(e)
    return result
