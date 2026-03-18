"""AI reader queue endpoints. Queue disabled (table dropped); extracted-details still works from JSON files."""
from fastapi import APIRouter, HTTPException, Query

from app.config import DEALER_ID, get_ocr_output_dir, get_uploads_dir
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
def get_extracted_details(
    subfolder: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Return structured vehicle (and customer) details for a subfolder from JSON files.
    May include extraction_error when Aadhar QR failed; vehicle and insurance are still returned."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    service = OcrService(
        uploads_dir=get_uploads_dir(did),
        ocr_output_dir=get_ocr_output_dir(did),
    )
    details = service.get_extracted_details(subfolder)
    if details is None:
        raise HTTPException(status_code=404, detail="No extracted details for this subfolder")
    return details


@router.get("/insurance-extraction")
def get_insurance_extraction(
    subfolder: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Debug: return what Textract extracted from Insurance.jpg (raw + parsed) and file status."""
    import re
    from pathlib import Path

    did = dealer_id if dealer_id is not None else DEALER_ID
    ocr_dir = get_ocr_output_dir(did)
    uploads_dir = get_uploads_dir(did)

    def _safe(s: str) -> str:
        return re.sub(r"[^\w\-]", "_", (s or "").strip()) or "default"

    safe = _safe(subfolder)
    subfolder_path = ocr_dir / safe
    upload_path = uploads_dir / subfolder

    result: dict = {
        "subfolder": subfolder,
        "insurance_jpg_exists": (upload_path / "Insurance.jpg").exists(),
        "ocr_files": sorted(p.name for p in subfolder_path.iterdir()) if subfolder_path.exists() else [],
        "insurance_from_details": None,
        "insurance_ocr_json": None,
        "raw_ocr_txt": None,
    }

    service = OcrService(uploads_dir=uploads_dir, ocr_output_dir=ocr_dir)
    details = service.get_extracted_details(subfolder)
    if details and details.get("insurance"):
        result["insurance_from_details"] = details["insurance"]

    insurance_ocr = subfolder_path / "insurance_ocr.json"
    if insurance_ocr.exists():
        try:
            import json
            result["insurance_ocr_json"] = json.loads(insurance_ocr.read_text(encoding="utf-8"))
        except Exception as e:
            result["insurance_ocr_json_error"] = str(e)

    raw_ocr_txt = subfolder_path / "Raw_OCR.txt"
    if raw_ocr_txt.exists():
        result["raw_ocr_txt"] = raw_ocr_txt.read_text(encoding="utf-8")

    insurance_txt = subfolder_path / "Insurance.txt"
    if insurance_txt.exists():
        result["insurance_txt_preview"] = insurance_txt.read_text(encoding="utf-8", errors="replace")[:2000]

    return result


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
