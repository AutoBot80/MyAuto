"""Bulk Loads: list and filter bulk upload processing results."""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.config import BULK_UPLOAD_DIR, UPLOADS_DIR
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bulk-loads", tags=["bulk-loads"])


@router.delete("")
def clear_bulk_loads() -> dict:
    """Clear all rows from bulk_loads table."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {BulkLoadsRepository.TABLE_NAME} RESTART IDENTITY")
        conn.commit()
        return {"ok": True, "message": "Bulk loads table cleared"}
    finally:
        conn.close()


@router.get("")
def list_bulk_loads(
    status: str | None = Query(None, description="Filter: Success, Error, Rejected, Processing"),
    status_in: str | None = Query(None, description="Comma-separated statuses, e.g. Success,Error,Processing"),
    date_from: str | None = Query(None, description="Filter from date (dd-mm-yyyy)"),
    date_to: str | None = Query(None, description="Filter to date (dd-mm-yyyy)"),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict]:
    """List bulk loads, sorted latest first. Filter by status and/or date range."""
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        status_list = [s.strip() for s in status_in.split(",") if s.strip()] if status_in else None
        rows = BulkLoadsRepository.list_all(
            conn,
            limit=limit,
            status_filter=status,
            status_in=status_list,
            date_from=date_from,
            date_to=date_to,
        )
        for r in rows:
            for k in ("created_at", "updated_at"):
                if k in r and isinstance(r[k], datetime):
                    r[k] = r[k].isoformat()
        return rows
    finally:
        conn.close()


@router.get("/counts")
def get_bulk_load_counts(
    date_from: str | None = Query(None, description="Filter from date (dd-mm-yyyy)"),
    date_to: str | None = Query(None, description="Filter to date (dd-mm-yyyy)"),
) -> dict[str, int]:
    """Return counts per status (Success, Error, Processing, Rejected) within date range."""
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        return BulkLoadsRepository.count_by_status(conn, date_from=date_from, date_to=date_to)
    finally:
        conn.close()


@router.get("/pending-count")
def get_pending_attention_count() -> dict[str, int]:
    """Return count of Error + Rejected records not yet marked as action taken (for tab badge)."""
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        return {"pending": BulkLoadsRepository.count_pending_attention(conn)}
    finally:
        conn.close()


@router.patch("/{bulk_load_id:int}/action-taken")
def set_action_taken(bulk_load_id: int, action_taken: bool = True) -> dict:
    """Mark a Failure or Rejected record as action taken (operator has addressed it)."""
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        updated = BulkLoadsRepository.set_action_taken(conn, bulk_load_id, action_taken)
        conn.commit()
        if not updated:
            raise HTTPException(status_code=404, detail="Record not found or not Error/Rejected")
        return {"ok": True, "action_taken": action_taken}
    finally:
        conn.close()


@router.get("/folder/{folder_path:path}", response_class=HTMLResponse)
def browse_bulk_folder(folder_path: str) -> HTMLResponse:
    """List files in a Bulk Upload subfolder (e.g. Rejected scans/Scan1_15032025)."""
    from urllib.parse import quote
    if ".." in folder_path or folder_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    folder = Path(BULK_UPLOAD_DIR) / folder_path
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")
    files = sorted(f.name for f in folder.iterdir() if f.is_file())
    rows = "".join(
        f'<tr><td><a href="/bulk-loads/file/{quote(folder_path + "/" + f, safe="")}">{f}</a></td></tr>'
        for f in files
    )
    safe_path = folder_path.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Bulk Upload / {safe_path}</title>
<style>body{{font-family:sans-serif;margin:1rem}} table{{border-collapse:collapse}} td{{padding:0.25rem 0.5rem}} a{{color:#0066cc}}</style></head>
<body><h1>Bulk Upload / {safe_path}</h1><p>Files in this folder:</p><table><tbody>{rows or "<tr><td>No files</td></tr>"}</tbody></table></body></html>"""
    return HTMLResponse(html)


@router.get("/file/{file_path:path}")
def get_bulk_file(file_path: str):
    """Serve a file from Bulk Upload (path: Rejected scans/Scan1_15032025/file.pdf)."""
    from fastapi.responses import FileResponse
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    path = Path(BULK_UPLOAD_DIR) / file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name)


@router.post("/{bulk_load_id:int}/prepare-reprocess")
def prepare_reprocess(bulk_load_id: int) -> dict:
    """
    Prepare an error record for re-processing: split PDF from result_folder into Uploaded scans,
    run OCR, return subfolder and mobile for Add Customer view.
    """
    conn = get_connection()
    try:
        row = BulkLoadsRepository.get_by_id(conn, bulk_load_id)
        if not row:
            raise HTTPException(status_code=404, detail="Bulk load not found")
        if row.get("status") != "Error":
            raise HTTPException(status_code=400, detail="Only Error records (not Rejected) can be re-processed")
        result_folder = row.get("result_folder")
        if not result_folder:
            raise HTTPException(status_code=400, detail="No result folder; cannot locate files for reprocess")
        mobile = row.get("mobile")
    finally:
        conn.close()

    archive_dir = Path(BULK_UPLOAD_DIR) / result_folder
    if not archive_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Archive folder not found: {result_folder}")

    from app.services.upload_service import UploadService
    from app.services.bulk_upload_service import _copy_classified_to_uploads, _split_pdf_to_images

    if mobile:
        subfolder = UploadService().get_subdir_name_mobile(mobile)
    else:
        subfolder = f"reprocess_{bulk_load_id}_{datetime.now().strftime('%H%M%S')}"

    uploads_subdir = Path(UPLOADS_DIR) / subfolder
    uploads_subdir.mkdir(parents=True, exist_ok=True)

    # Prefer pre-classified images if present; else split PDF by position
    saved = _copy_classified_to_uploads(archive_dir, uploads_subdir)
    if not saved:
        pdf_path = next((f for f in archive_dir.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"), None)
        if not pdf_path:
            raise HTTPException(status_code=404, detail="No PDF or classified images found in archive folder")
        saved = _split_pdf_to_images(pdf_path, uploads_subdir)
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to prepare files for reprocess")

    from app.services.ocr_service import OcrService
    ocr = OcrService()
    ocr.process_uploaded_subfolder(subfolder)

    return {"subfolder": subfolder, "mobile": mobile}
