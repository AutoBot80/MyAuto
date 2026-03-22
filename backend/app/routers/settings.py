"""Read-only settings derived from environment (for client navigation parity with Playwright)."""

from fastapi import APIRouter

from app.config import (
    DMS_BASE_URL,
    DMS_MODE,
    DMS_REAL_URL_CONTACT,
    INSURANCE_BASE_URL,
    VAHAN_BASE_URL,
    dms_automation_is_real_siebel,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/site-urls")
def get_site_urls() -> dict:
    """DMS / Vahan / Insurance base URLs from backend/.env — required at server startup."""
    return {
        "dms_base_url": DMS_BASE_URL,
        "dms_mode": DMS_MODE or "dummy",
        "dms_real_siebel": dms_automation_is_real_siebel(),
        "dms_real_contact_url_configured": bool((DMS_REAL_URL_CONTACT or "").strip()),
        "vahan_base_url": VAHAN_BASE_URL,
        "insurance_base_url": INSURANCE_BASE_URL,
    }
