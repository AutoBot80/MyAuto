"""Vision API: send document images to ChatGPT and return analysis (document type, photo region)."""

import base64

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import UPLOAD_MAX_FILE_BYTES
from app.services.upload_file_validation import read_upload_capped, validate_magic_jpeg_or_png
from app.services.vision_service import analyze_aadhar_image

router = APIRouter(prefix="/vision", tags=["vision"])


@router.post("/aadhar-analyze")
async def aadhar_analyze(
    image: UploadFile = File(..., description="Aadhar scan image (JPEG/PNG, max 500 KB)"),
) -> dict:
    """
    Send an Aadhar (or similar) scan to ChatGPT vision. Returns:
    - Document identification
    - Location of customer photo on the left (as text / bounding box percentages if possible).
    """
    try:
        raw = await read_upload_capped(image, UPLOAD_MAX_FILE_BYTES)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not raw:
        return {"content": None, "document_type": None, "raw_response": None, "error": "Empty file"}
    try:
        validate_magic_jpeg_or_png(raw, label="Image")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    b64 = base64.standard_b64encode(raw).decode("ascii")
    result = analyze_aadhar_image(image_base64=b64)
    return result
