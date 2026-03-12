"""Vision API: send document images to ChatGPT and return analysis (document type, photo region)."""

import base64
from fastapi import APIRouter, File, UploadFile

from app.services.vision_service import analyze_aadhar_image

router = APIRouter(prefix="/vision", tags=["vision"])


@router.post("/aadhar-analyze")
async def aadhar_analyze(image: UploadFile = File(..., description="Aadhar scan image (JPEG/PNG)")) -> dict:
    """
    Send an Aadhar (or similar) scan to ChatGPT vision. Returns:
    - Document identification
    - Location of customer photo on the left (as text / bounding box percentages if possible).
    """
    raw = await image.read()
    if not raw:
        return {"content": None, "document_type": None, "raw_response": None, "error": "Empty file"}
    b64 = base64.standard_b64encode(raw).decode("ascii")
    result = analyze_aadhar_image(image_base64=b64)
    return result
