"""Subdealer challan: OCR parse Daily Delivery Report uploads."""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.subdealer_challan_ocr_service import parse_subdealer_challan

router = APIRouter(prefix="/subdealer-challan", tags=["subdealer-challan"])


@router.post("/parse-scan")
async def parse_scan(
    file: UploadFile = File(..., description="Challan scan (JPEG/PNG/PDF, max 5 MB)"),
) -> dict:
    """
    Run Textract FORMS+TABLES, parse challan no / date / engine-chassis rows,
    write Raw_OCR.txt and OCR_To_be_Used.json under CHALLANS_DIR.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    result = parse_subdealer_challan(raw, write_artifacts=True)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result
