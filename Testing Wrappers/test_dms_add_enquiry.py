"""
Open DMS, Find Contact by mobile, then Add Enquiry only.

Double-click ``test_dms_add_enquiry.bat`` or:
  python test_dms_add_enquiry.py

Edit the ASKAR_* constants below for another customer. Requires ``backend/.env``
(DMS_BASE_URL, DMS_MODE=real, DMS_REAL_URL_CONTACT, login).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DEALER_ID", "100003")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("test_dms_add_enquiry")

# In-process row: Askar @ dealer 100003 (edit for other runs)
ASKAR_MOBILE = "8094061172"
ASKAR_FIRST_NAME = "Askar"
ASKAR_AADHAR_LAST4 = "5761"
ASKAR_FRAME_PARTIAL = "57936"
ASKAR_ENGINE_PARTIAL = "58197"
ASKAR_KEY = "1975"
ASKAR_MODEL = "SPLENDOR +"
ASKAR_COLOR = "IDG"
ASKAR_YEAR = "2026"
ASKAR_ADDRESS = "Village Kukarpuri"
ASKAR_PIN = "321023"
ASKAR_STATE = "RAJASTHAN"
DEALER_ID_TEST = "100003"


def main() -> int:
    from app.config import (
        DMS_BASE_URL,
        DMS_REAL_URL_CONTACT,
        DMS_SIEBEL_ACTION_TIMEOUT_MS,
        DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
        DMS_SIEBEL_NAV_TIMEOUT_MS,
        dms_automation_is_real_siebel,
    )
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_customer import (
        _add_enquiry_opportunity,
        _contact_mobile_drilldown_plans,
        _contact_view_find_by_mobile_strategy_two,
    )
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls

    if not (DMS_BASE_URL or "").strip():
        logger.error("DMS_BASE_URL is not set in backend/.env")
        return 1
    if not dms_automation_is_real_siebel():
        logger.error("DMS_MODE must be real/siebel for this test.")
        return 1
    contact_url = (DMS_REAL_URL_CONTACT or "").strip()
    if not contact_url:
        logger.error("DMS_REAL_URL_CONTACT is not set in backend/.env")
        return 1

    dms_values: dict[str, str] = {
        "dealer_id": DEALER_ID_TEST,
        "mobile_phone": ASKAR_MOBILE,
        "first_name": ASKAR_FIRST_NAME,
        "aadhar_id": ASKAR_AADHAR_LAST4,
        "address_line_1": ASKAR_ADDRESS,
        "pin_code": ASKAR_PIN,
        "state": ASKAR_STATE,
        "frame_partial": ASKAR_FRAME_PARTIAL,
        "engine_partial": ASKAR_ENGINE_PARTIAL,
        "key_partial": ASKAR_KEY,
        "finance_required": "N",
        "financier_name": "",
        "dms_contact_path": "found",
    }
    vehicle: dict[str, Any] = {
        "model": ASKAR_MODEL,
        "color": ASKAR_COLOR,
        "year_of_mfg": ASKAR_YEAR,
        "full_chassis": "MBLHAW471THE57936",
        "full_engine": "HA11F6THE58197",
        "key_num": ASKAR_KEY,
    }

    tmo = int(DMS_SIEBEL_ACTION_TIMEOUT_MS or 8000)
    nav = int(DMS_SIEBEL_NAV_TIMEOUT_MS or 90000)
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
    urls = SiebelDmsUrls(
        contact=contact_url,
        vehicles="",
        precheck="",
        pdi="",
        vehicle="",
        enquiry="",
        line_items="",
        reports="",
    )

    def note(msg: str) -> None:
        logger.info("%s", msg)

    page, err = get_or_open_site_page(
        (DMS_BASE_URL or "").strip(),
        "DMS",
        require_login_on_open=True,
    )
    if page is None:
        logger.error("Could not open DMS: %s", err)
        return 1

    _install_playwright_js_dialog_handler(page)
    page.set_default_timeout(tmo)
    logger.info("Browser open — log in if needed (15s).")
    time.sleep(15)

    note("Find Contact by mobile.")
    ok_find = _contact_view_find_by_mobile_strategy_two(
        page,
        contact_url=contact_url,
        mobile=ASKAR_MOBILE,
        first_name=ASKAR_FIRST_NAME,
        nav_timeout_ms=nav,
        action_timeout_ms=tmo,
        content_frame_selector=frame_sel,
        mobile_aria_hints=[],
        note=note,
        step=lambda m: note(f"[step] {m}"),
        stage_msg_mobile_only="Find by mobile.",
        stage_msg_mobile_and_first="Find by mobile + first name.",
    )
    if not ok_find:
        logger.error("Find Contact failed.")
        retain_automation_browser_for_operator_manual_close()
        return 1

    n_rows = len(
        _contact_mobile_drilldown_plans(
            page,
            ASKAR_MOBILE,
            content_frame_selector=frame_sel,
            first_name_exact=None,
        )
    )
    note(f"Contact drilldown rows after Find: N={n_rows}")
    if n_rows > 0:
        logger.warning(
            "Contact already exists (N=%s). Add Enquiry branch expects N=0. "
            "Continuing anyway — Enquiry tab must be on the current Siebel view.",
            n_rows,
        )

    note("Add Enquiry.")
    ok, detail, enq_no = _add_enquiry_opportunity(
        page,
        dms_values,
        urls,
        action_timeout_ms=tmo,
        nav_timeout_ms=nav,
        content_frame_selector=frame_sel,
        note=note,
        form_trace=None,
        vehicle_merge=vehicle,
    )
    if not ok:
        logger.error("Add Enquiry failed: %s", detail or "unknown")
        retain_automation_browser_for_operator_manual_close()
        return 1

    logger.info("Add Enquiry OK — Enquiry#=%s", enq_no or "(not scraped)")
    retain_automation_browser_for_operator_manual_close()
    try:
        input("Press Enter to exit (browser stays open)...")
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
