"""Temporary endpoint: upload image, decode QR and return parsed payload (no signature verification)."""

import asyncio
from fastapi import APIRouter, File, UploadFile

from app.services.qr_decode_service import decode_qr_from_image_bytes

router = APIRouter(prefix="/qr-decode", tags=["qr-decode"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("")
async def decode_qr(file: UploadFile = File(..., description="Scan image containing a QR code")) -> dict:
    """
    Decode QR code(s) from uploaded image and parse payload.
    No signature verification; returns raw and parsed fields for inspection.
    """
    raw = await file.read()
    if not raw:
        return {"decoded": [], "error": "Empty file"}
    if len(raw) > MAX_IMAGE_BYTES:
        return {"decoded": [], "error": f"Image too large (max {MAX_IMAGE_BYTES // (1024*1024)} MB). Use a smaller file."}
    try:
        # Run CPU-heavy OpenCV decode in thread pool so the server doesn't block
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: decode_qr_from_image_bytes(raw))
    except Exception as e:
        return {"decoded": [], "error": f"Decode failed: {str(e)}"}
