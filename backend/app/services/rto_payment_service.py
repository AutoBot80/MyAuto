"""
RTO Payment flow: navigate to Vahan search, fill Application ID and Chassis No.,
take screenshot, click Pay, capture TC number, update DB.
"""
import re
import urllib.parse
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.config import DMS_PLAYWRIGHT_HEADED, UPLOADS_DIR


def _safe_subfolder(name: str | None) -> str:
    """Safe directory name (one segment)."""
    if not name or not str(name).strip():
        return "rto_default"
    return re.sub(r"[^\w\-]", "_", str(name).strip()) or "rto_default"


def run_rto_pay(
    application_id: str,
    chassis_num: str | None,
    subfolder: str | None,
    vahan_base_url: str,
    rto_dealer_id: str = "RTO100001",
    customer_name: str | None = None,
    rto_fees: float = 200.0,
    uploads_dir: Path | None = None,
) -> dict:
    """
    Open Vahan search page with URL params, take screenshot, click Pay, capture TC number.
    Returns { success, pay_txn_id, screenshot_path, error }.
    """
    result: dict = {"success": False, "pay_txn_id": None, "screenshot_path": None, "error": None}
    base = vahan_base_url.rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        result["error"] = "vahan_base_url must be absolute (http/https)"
        return result

    safe_sub = _safe_subfolder(subfolder) if subfolder else f"rto_{_safe_subfolder(application_id)}"
    uploads_path = Path(uploads_dir or UPLOADS_DIR).resolve()
    subfolder_path = uploads_path / safe_sub
    subfolder_path.mkdir(parents=True, exist_ok=True)
    screenshot_path = subfolder_path / "RTO Payment Proof.png"

    params = {"application_id": application_id}
    if chassis_num and str(chassis_num).strip():
        params["chassis_no"] = str(chassis_num).strip()
    if rto_dealer_id:
        params["rto_dealer_id"] = str(rto_dealer_id).strip()
    if customer_name and str(customer_name).strip():
        params["customer_name"] = str(customer_name).strip()[:100]
    if rto_fees:
        params["rto_fees"] = str(rto_fees)

    query = urllib.parse.urlencode(params)
    url = f"{base}/search.html?{query}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=not DMS_PLAYWRIGHT_HEADED,
            )
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(15_000)

            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#vahan-result-section:visible", timeout=8000)
            page.screenshot(path=str(screenshot_path))
            result["screenshot_path"] = str(screenshot_path)

            page.click("#vahan-payment-btn")
            page.wait_for_selector("#vahan-result-tc-number", timeout=5000)
            el = page.locator("#vahan-result-tc-number")
            tc = el.get_attribute("data-tc") if el.count() > 0 else None
            if tc and str(tc).strip():
                result["pay_txn_id"] = str(tc).strip()
                result["success"] = True
            else:
                result["error"] = "Could not capture TC number after Pay"

            browser.close()
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
    except Exception as e:
        result["error"] = str(e)

    return result
