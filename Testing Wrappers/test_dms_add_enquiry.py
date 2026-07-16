"""
Open DMS, Find Contact by mobile, then Add Enquiry only.

Double-click ``test_dms_add_enquiry.bat`` or:
  python test_dms_add_enquiry.py

Current fixture: same-state customer (RAJASTHAN / Bharatpur) @ dealer 100003
(KAMAN) — exercises dealer_ref defaults for State/District/Tehsil/City.
Requires ``backend/.env`` (DMS_BASE_URL, DMS_MODE=real, DMS_REAL_URL_CONTACT, login).
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

# Same-state vs dealer 100003 (Rajasthan / KAMAN): expect dealer city/tehsil KAMAN
CUSTOMER_MOBILE = "9772098406"
CUSTOMER_FIRST_NAME = "KHOOB"
CUSTOMER_LAST_NAME = "KALA"
CUSTOMER_GENDER = "female"  # Care of W/O → set Mr/Ms to Ms
CUSTOMER_LANDLINE = "7878874921"
# NAGLA LODHA PEER NAGAR, Kaman, BHARATPUR, RAJASTHAN, 321001
CUSTOMER_ADDRESS = "NAGLA LODHA PEER NAGAR"
CUSTOMER_CITY = "Kaman"
CUSTOMER_DISTRICT = "BHARATPUR"
CUSTOMER_STATE = "RAJASTHAN"
CUSTOMER_PIN = "321001"
CUSTOMER_FINANCIER = "Hinduja Leyland Finance"
CUSTOMER_FRAME_PARTIAL = "20236"
CUSTOMER_ENGINE_PARTIAL = "T4463"
CUSTOMER_KEY = "2355"
CUSTOMER_BATTERY = "769516"
CUSTOMER_AADHAR_LAST4 = "1234"
CUSTOMER_AGE = "34"
CUSTOMER_DOB = ""  # optional if CUSTOMER_AGE set
CUSTOMER_MODEL = "Splendor"
CUSTOMER_COLOR = "RBG"
CUSTOMER_YEAR = "2025"
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
    from app.repositories import form_dms as form_dms_repo
    from app.services.customer_address_infer import canonical_states_differ
    from app.services.fill_hero_dms_service import _install_playwright_js_dialog_handler
    from app.services.handle_browser_opening import (
        get_or_open_site_page,
        retain_automation_browser_for_operator_manual_close,
    )
    from app.services.hero_dms_playwright_customer import (
        _add_enquiry_opportunity,
        _contact_mobile_drilldown_plans,
        _contact_view_find_by_mobile_strategy_two,
        _resolve_add_enquiry_address_fields,
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

    missing = [
        name
        for name, val in (
            ("CUSTOMER_AADHAR_LAST4", CUSTOMER_AADHAR_LAST4),
            ("CUSTOMER_PIN", CUSTOMER_PIN),
            ("CUSTOMER_AGE or CUSTOMER_DOB", CUSTOMER_AGE or CUSTOMER_DOB),
            ("CUSTOMER_MODEL", CUSTOMER_MODEL),
            ("CUSTOMER_COLOR", CUSTOMER_COLOR),
            ("CUSTOMER_YEAR", CUSTOMER_YEAR),
            ("CUSTOMER_CITY", CUSTOMER_CITY),
            ("CUSTOMER_STATE", CUSTOMER_STATE),
        )
        if not (val or "").strip()
    ]
    if missing:
        logger.error(
            "Fill these constants in test_dms_add_enquiry.py before running: %s",
            ", ".join(missing),
        )
        return 1

    dealer_addr = form_dms_repo.lookup_dealer_enquiry_address(int(DEALER_ID_TEST))
    dms_values: dict[str, Any] = {
        "dealer_id": DEALER_ID_TEST,
        "dealer_enquiry_address": dealer_addr,
        "mobile_phone": CUSTOMER_MOBILE,
        "first_name": CUSTOMER_FIRST_NAME,
        "last_name": CUSTOMER_LAST_NAME,
        "gender": CUSTOMER_GENDER,
        "landline": CUSTOMER_LANDLINE,
        "aadhar_id": CUSTOMER_AADHAR_LAST4,
        "address_line_1": CUSTOMER_ADDRESS,
        "city": CUSTOMER_CITY,
        "district": CUSTOMER_DISTRICT,
        "pin_code": CUSTOMER_PIN,
        "state": CUSTOMER_STATE,
        "age": CUSTOMER_AGE,
        "date_of_birth": CUSTOMER_DOB,
        "frame_partial": CUSTOMER_FRAME_PARTIAL,
        "engine_partial": CUSTOMER_ENGINE_PARTIAL,
        "key_partial": CUSTOMER_KEY,
        "battery_partial": CUSTOMER_BATTERY,
        "finance_required": "Y" if CUSTOMER_FINANCIER.strip() else "N",
        "financier_name": CUSTOMER_FINANCIER,
        "dms_contact_path": "found",
    }
    state_use, dist_use, tehsil_use, city_use = _resolve_add_enquiry_address_fields(
        dms_values, dealer_addr
    )
    interstate = canonical_states_differ(CUSTOMER_STATE, dealer_addr.get("state") or "")
    logger.info(
        "Address resolve preview — interstate=%s dealer=%s customer_state=%s → "
        "state=%r district=%r tehsil=%r city=%r pin=%r addr=%r",
        interstate,
        dealer_addr,
        CUSTOMER_STATE,
        state_use,
        dist_use,
        tehsil_use,
        city_use,
        CUSTOMER_PIN,
        CUSTOMER_ADDRESS,
    )
    if interstate:
        logger.warning(
            "Unexpected interstate for same-state fixture "
            "(customer RAJASTHAN vs dealer). Check dealer_ref.state for dealer_id=%s.",
            DEALER_ID_TEST,
        )
    else:
        logger.info(
            "Same-state path — expect dealer defaults (e.g. KAMAN tehsil/city, Bharatpur district)."
        )

    vehicle: dict[str, Any] = {
        "model": CUSTOMER_MODEL,
        "color": CUSTOMER_COLOR,
        "year_of_mfg": CUSTOMER_YEAR,
        "key_num": CUSTOMER_KEY,
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

    note(
        "Find Contact by mobile (same-state → expect dealer_ref KAMAN / RAJASTHAN on enquiry)."
    )
    ok_find = _contact_view_find_by_mobile_strategy_two(
        page,
        contact_url=contact_url,
        mobile=CUSTOMER_MOBILE,
        first_name=CUSTOMER_FIRST_NAME,
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
            CUSTOMER_MOBILE,
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

    note("Add Enquiry (same-state dealer address defaults).")
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
