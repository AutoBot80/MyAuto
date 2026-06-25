"""Read-only settings derived from environment (for client navigation parity with Playwright)."""

from fastapi import APIRouter, Query

from app.config import (
    DMS_MODE,
    DMS_REAL_URL_CONTACT,
    ENVIRONMENT_IS_PRODUCTION,
    INSURANCE_BASE_URL,
    VAHAN_BASE_URL,
    dms_automation_is_real_siebel,
)
from app.services.hero_dms_portal_service import hero_dms_base_url_for_dealer

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/site-urls")
def get_site_urls(dealer_id: int | None = Query(None, description="Saathi dealer_id for HMCL vs ASC DMS portal")) -> dict:
    """DMS / Vahan / Insurance base URLs — DMS portal from ``dealer_ref.dms_siebel_portal`` when *dealer_id* set."""
    dms_base = hero_dms_base_url_for_dealer(dealer_id) if dealer_id and dealer_id > 0 else None
    if not dms_base:
        from app.config import DMS_BASE_URL

        dms_base = (DMS_BASE_URL or "").strip()
    return {
        "dms_base_url": dms_base,
        "dms_mode": DMS_MODE or "real",
        "dms_real_siebel": dms_automation_is_real_siebel(),
        "dms_real_contact_url_configured": bool((DMS_REAL_URL_CONTACT or "").strip()),
        "vahan_base_url": VAHAN_BASE_URL,
        "insurance_base_url": INSURANCE_BASE_URL,
        "environment_is_production": ENVIRONMENT_IS_PRODUCTION,
    }
