"""Temporary endpoint: upload image, decode QR and return parsed payload (no signature verification)."""

import asyncio
from fastapi import APIRouter, File, UploadFile

from app.services.qr_decode_service import decode_qr_from_image_bytes

router = APIRouter(prefix="/qr-decode", tags=["qr-decode"])

MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB — larger images often cause OpenCV to hang
DECODE_TIMEOUT_SECONDS = 25  # Fail fast so client gets a response


async def _read_file_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """Read upload up to max_bytes so we never buffer huge files."""
    chunks = []
    total = 0
    while total < max_bytes:
        to_read = min(64 * 1024, max_bytes - total)
        chunk = await upload.read(to_read)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


@router.post("")
async def decode_qr(file: UploadFile = File(..., description="Scan image containing a QR code")) -> dict:
    """
    Decode QR code(s) from uploaded image and parse payload.
    No signature verification; returns raw and parsed fields for inspection.
    """
    raw = await _read_file_capped(file, MAX_IMAGE_BYTES + 1)
    if not raw:
        return {"decoded": [], "error": "Empty file"}
    if len(raw) > MAX_IMAGE_BYTES:
        return {"decoded": [], "error": "Image too large (max 2 MB). Use a smaller or cropped image."}
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: decode_qr_from_image_bytes(raw)),
            timeout=DECODE_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        return {"decoded": [], "error": "Decode timed out. Use an image under 2 MB or crop to the QR only."}
    except Exception as e:
        return {"decoded": [], "error": f"Decode failed: {str(e)}"}
