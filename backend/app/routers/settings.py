"""Read-only settings derived from environment (for client navigation parity with Playwright)."""

from fastapi import APIRouter

from app.config import DMS_BASE_URL, INSURANCE_BASE_URL, VAHAN_BASE_URL

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/site-urls")
def get_site_urls() -> dict:
    """DMS / Vahan / Insurance base URLs from backend/.env — required at server startup."""
    return {
        "dms_base_url": DMS_BASE_URL,
        "vahan_base_url": VAHAN_BASE_URL,
        "insurance_base_url": INSURANCE_BASE_URL,
    }
