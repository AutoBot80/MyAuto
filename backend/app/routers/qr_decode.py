"""Temporary endpoint: upload image, decode QR and return parsed payload (no signature verification)."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

router = APIRouter(prefix="/qr-decode", tags=["qr-decode"])

MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
DECODE_TIMEOUT_SECONDS = 25  # Hard kill worker after this so server always responds


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


def _run_decode_subprocess(image_path: Path, timeout: int) -> tuple[dict | None, str | None]:
    """
    Run QR decode in a subprocess. Returns (result_dict, error_message).
    If the process hangs, we get TimeoutExpired and kill it, then return (None, "Decode timed out...").
    """
    # Same Python as this process (so OpenCV/env is available); run from backend dir so "app" resolves
    backend_dir = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.qr_decode_worker", str(image_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(backend_dir),
        text=False,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return None, "Decode timed out. Try a smaller image or crop to the QR only."
    if proc.returncode != 0:
        return None, (err.decode("utf-8", errors="replace") or "Decode failed.")
    try:
        return json.loads(out.decode("utf-8")), None
    except json.JSONDecodeError:
        return None, "Invalid output from decoder."


@router.post("")
async def decode_qr(file: UploadFile = File(..., description="Scan image containing a QR code")) -> dict:
    """
    Decode QR code(s) from uploaded image and parse payload.
    Runs in a subprocess so a hanging OpenCV does not freeze the server; we kill the process after timeout.
    """
    raw = await _read_file_capped(file, MAX_IMAGE_BYTES + 1)
    if not raw:
        return {"decoded": [], "error": "Empty file"}
    if len(raw) > MAX_IMAGE_BYTES:
        return {"decoded": [], "error": "Image too large (max 2 MB). Use a smaller or cropped image."}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(raw)
        tmp_path = Path(f.name)
    try:
        loop = asyncio.get_event_loop()
        result, err = await loop.run_in_executor(
            None,
            lambda: _run_decode_subprocess(tmp_path, DECODE_TIMEOUT_SECONDS),
        )
        if err:
            return {"decoded": [], "error": err}
        return result
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
