"""List and serve customer documents (uploaded scans)."""

import logging
import re
from urllib.parse import quote
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from app.config import DEALER_ID, get_uploads_dir

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

def _is_ocr_output_file(name: str) -> bool:
    """True if filename is an OCR output that should not appear in Uploaded scans."""
    base = Path(name).stem.lower().replace(" ", "_")
    return base == "ocr_to_be_used"


def _safe_subfolder(name: str | None) -> str | None:
    """Allow only alphanumeric, underscore, hyphen. No path traversal."""
    if not name or not name.strip():
        return None
    safe = re.sub(r"[^\w\-]", "_", name.strip())
    return safe if safe else None


@router.get("/{subfolder}", response_class=HTMLResponse)
def browse_documents(
    subfolder: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> HTMLResponse:
    """Return HTML page listing files in the subfolder with download links."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    try:
        safe = _safe_subfolder(subfolder)
        if not safe:
            raise HTTPException(status_code=400, detail="Invalid subfolder")
        folder = get_uploads_dir(did) / safe
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")
        files = sorted(
            f.name for f in folder.iterdir()
            if f.is_file() and not _is_ocr_output_file(f.name)
        )
        rows = "".join(
            f'<tr><td><a href="/documents/{quote(safe, safe="")}/{quote(f, safe="")}">{f}</a></td></tr>'
            for f in files
        )
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Uploaded scans / {safe}</title>
<style>body{{font-family:sans-serif;margin:1rem}} table{{border-collapse:collapse}} td{{padding:0.25rem 0.5rem}} a{{color:#0066cc}}</style></head>
<body><h1>Uploaded scans / {safe}</h1><p>Files in this folder:</p><table><tbody>{rows or "<tr><td>No files</td></tr>"}</tbody></table></body></html>"""
        return HTMLResponse(html)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("browse_documents failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{subfolder}/list")
def list_documents(
    subfolder: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """List files in a customer's document subfolder (e.g. mobile_ddmmyy)."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    try:
        safe = _safe_subfolder(subfolder)
        if not safe:
            raise HTTPException(status_code=400, detail="Invalid subfolder")
        folder = get_uploads_dir(did) / safe
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")
        files: list[dict] = []
        for f in sorted(folder.iterdir()):
            if f.is_file() and not _is_ocr_output_file(f.name):
                files.append({"name": f.name, "size": f.stat().st_size})
        return {"subfolder": safe, "files": files}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_documents failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{subfolder}/{filename}")
def get_document(
    subfolder: str,
    filename: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> FileResponse:
    """Serve a single file from a customer's document subfolder."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    safe_sub = _safe_subfolder(subfolder)
    if not safe_sub:
        raise HTTPException(status_code=400, detail="Invalid subfolder")
    # Preserve human-readable filenames like "Form 20.pdf" while still blocking traversal.
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if _is_ocr_output_file(filename):
        raise HTTPException(status_code=404, detail="OCR output files are not served from Uploaded scans")
    requested_name = Path(filename).name
    if requested_name != filename or requested_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = get_uploads_dir(did) / safe_sub / requested_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=requested_name, content_disposition_type="inline")
