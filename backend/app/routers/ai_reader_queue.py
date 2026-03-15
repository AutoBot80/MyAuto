"""AI reader queue endpoints. Queue disabled (table dropped); extracted-details still works from JSON files."""
from fastapi import APIRouter, HTTPException

from app.services.ocr_service import OcrService

router = APIRouter(prefix="/ai-reader-queue", tags=["ai-reader-queue"])


@router.get("")
def list_ai_reader_queue(limit: int = 200) -> list[dict]:
    """Queue disabled; returns empty list."""
    return []


@router.post("/process-next")
def process_next_ocr() -> None:
    """Queue disabled; returns None."""
    return None


@router.get("/extractions")
def list_extractions(limit: int = 200) -> list[dict]:
    """Queue disabled; returns empty list."""
    return []


@router.get("/extracted-details")
def get_extracted_details(subfolder: str) -> dict:
    """Return structured vehicle (and customer) details for a subfolder from JSON files."""
    service = OcrService()
    details = service.get_extracted_details(subfolder)
    if details is None:
        raise HTTPException(status_code=404, detail="No extracted details for this subfolder")
    return details


@router.get("/process-status")
def process_status() -> dict:
    """Queue disabled; returns sleeping."""
    return {"status": "sleeping", "processed_count": 0}


@router.post("/empty")
def empty_queue() -> dict:
    """Queue disabled; no-op."""
    return {"ok": True, "deleted": 0}


@router.post("/process-all")
def process_all() -> dict:
    """Queue disabled; no-op."""
    return {"started": False, "message": "AI reader queue is disabled"}


@router.post("/{item_id:int}/reprocess")
def reprocess_item(item_id: int) -> dict:
    """Queue disabled; returns 404."""
    raise HTTPException(status_code=404, detail="AI reader queue is disabled")
