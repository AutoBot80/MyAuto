"""List and serve customer documents (uploaded scans)."""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import UPLOADS_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _safe_subfolder(name: str | None) -> str | None:
    """Allow only alphanumeric, underscore, hyphen. No path traversal."""
    if not name or not name.strip():
        return None
    safe = re.sub(r"[^\w\-]", "_", name.strip())
    return safe if safe else None


@router.get("/{subfolder}/list")
def list_documents(subfolder: str) -> dict:
    """List files in a customer's document subfolder (e.g. mobile_ddmmyy)."""
    try:
        safe = _safe_subfolder(subfolder)
        if not safe:
            raise HTTPException(status_code=400, detail="Invalid subfolder")
        folder = Path(UPLOADS_DIR) / safe
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")
        files: list[dict] = []
        for f in sorted(folder.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size})
        return {"subfolder": safe, "files": files}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_documents failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{subfolder}/{filename}")
def get_document(subfolder: str, filename: str) -> FileResponse:
    """Serve a single file from a customer's document subfolder."""
    safe_sub = _safe_subfolder(subfolder)
    if not safe_sub:
        raise HTTPException(status_code=400, detail="Invalid subfolder")
    # Filename: only allow safe chars, no path traversal
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe_name = re.sub(r"[^\w\-.]", "_", filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = Path(UPLOADS_DIR) / safe_sub / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=safe_name)
