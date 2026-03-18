"""Bulk Loads: list and filter bulk upload processing results."""

import logging
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.config import DEALER_ID, get_bulk_upload_dir, get_ocr_output_dir, get_uploads_dir
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bulk-loads", tags=["bulk-loads"])


def _dealer_id(dealer_id: int | None) -> int:
    """Use provided dealer_id or app default."""
    return dealer_id if dealer_id is not None else DEALER_ID


@router.delete("")
def clear_bulk_loads(dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted")) -> dict:
    """Clear bulk_loads rows for the given dealer."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        deleted = BulkLoadsRepository.clear_for_dealer(conn, dealer_id=did)
        conn.commit()
        return {"ok": True, "deleted": deleted, "message": f"Bulk loads cleared for dealer {did}"}
    finally:
        conn.close()


@router.get("")
def list_bulk_loads(
    status: str | None = Query(None, description="Filter: Success, Error, Rejected, Processing"),
    status_in: str | None = Query(None, description="Comma-separated statuses, e.g. Success,Error,Processing"),
    date_from: str | None = Query(None, description="Filter from date (dd-mm-yyyy)"),
    date_to: str | None = Query(None, description="Filter to date (dd-mm-yyyy)"),
    limit: int = Query(200, ge=1, le=500),
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> list[dict]:
    """List bulk loads, sorted latest first. Filter by status and/or date range."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        status_list = [s.strip() for s in (status_in or "").split(",") if s.strip()] or None
        rows = BulkLoadsRepository.list_all(
            conn,
            limit=limit,
            status_filter=status,
            status_in=status_list,
            date_from=date_from,
            date_to=date_to,
            dealer_id=did,
        )
        for r in rows:
            for k in ("created_at", "updated_at"):
                if k in r and isinstance(r[k], datetime):
                    r[k] = r[k].isoformat()
        return rows
    except Exception as e:
        logger.exception("list_bulk_loads failed: %s", e)
        raise
    finally:
        conn.close()


@router.get("/counts")
def get_bulk_load_counts(
    date_from: str | None = Query(None, description="Filter from date (dd-mm-yyyy)"),
    date_to: str | None = Query(None, description="Filter to date (dd-mm-yyyy)"),
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict[str, int]:
    """Return counts per status. Error and Rejected exclude action_taken records (for tab display)."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        return BulkLoadsRepository.count_by_status_pending(conn, date_from=date_from, date_to=date_to, dealer_id=did)
    except Exception as e:
        logger.exception("get_bulk_load_counts failed: %s", e)
        raise
    finally:
        conn.close()


@router.post("/reset-stale-processing")
def reset_stale_processing(
    stale_sec: int = Query(60, ge=10, le=3600, description="Mark Processing older than this (seconds) as Error"),
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Reset stuck Processing rows so the watcher can pick up new files. Use when scan 2 (etc.) does not start."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        count = BulkLoadsRepository.reset_stale_processing(conn, stale_sec, dealer_id=did)
        conn.commit()
        return {"ok": True, "reset_count": count, "message": f"Reset {count} stuck Processing row(s)"}
    finally:
        conn.close()


@router.get("/pending-count")
def get_pending_attention_count(dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted")) -> dict[str, int]:
    """Return count of Error + Rejected records not yet marked as action taken (for tab badge)."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        return {"pending": BulkLoadsRepository.count_pending_attention(conn, dealer_id=did)}
    finally:
        conn.close()


@router.patch("/{bulk_load_id:int}/action-taken")
def set_action_taken(
    bulk_load_id: int,
    action_taken: bool = True,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Mark a Failure or Rejected record as action taken (operator has addressed it)."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        updated = BulkLoadsRepository.set_action_taken(conn, bulk_load_id, action_taken, dealer_id=did)
        conn.commit()
        if not updated:
            raise HTTPException(status_code=404, detail="Record not found or not Error/Rejected")
        return {"ok": True, "action_taken": action_taken}
    finally:
        conn.close()


@router.patch("/{bulk_load_id:int}/mark-success")
def mark_bulk_load_success(
    bulk_load_id: int,
    subfolder: str = Query(..., description="Uploads subfolder for documents link"),
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Mark an Error record as Success after manual completion via Add Customer (Re-Try flow)."""
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        row = BulkLoadsRepository.get_by_id(conn, bulk_load_id, dealer_id=did)
        if not row:
            raise HTTPException(status_code=404, detail="Bulk load not found")
        if row.get("status") != "Error":
            raise HTTPException(status_code=400, detail="Only Error records can be marked Success")
        BulkLoadsRepository.update_status(
            conn, bulk_load_id, "Success",
            error_message=None,
            subfolder=subfolder,
            folder_path=subfolder,
        )
        BulkLoadsRepository.update_result_folder(conn, bulk_load_id, subfolder)
        conn.commit()
        return {"ok": True, "status": "Success"}
    finally:
        conn.close()


def _list_bulk_folder_files(folder_path: str, dealer_id: int) -> list[dict]:
    """List files in a Bulk Upload subfolder. Returns list of {name, size}."""
    if ".." in folder_path or folder_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    folder = get_bulk_upload_dir(dealer_id) / folder_path
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")
    files: list[dict] = []
    for f in sorted(folder.iterdir()):
        if f.is_file():
            files.append({"name": f.name, "size": f.stat().st_size})
    return files


@router.get("/folder/{folder_path:path}/list")
def list_bulk_folder(
    folder_path: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """List files in a Bulk Upload subfolder (JSON for in-app display)."""
    did = _dealer_id(dealer_id)
    files = _list_bulk_folder_files(folder_path, did)
    return {"folder_path": folder_path, "files": files}


@router.get("/folder/{folder_path:path}", response_class=HTMLResponse)
def browse_bulk_folder(
    folder_path: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> HTMLResponse:
    """List files in a Bulk Upload subfolder (legacy HTML for direct links)."""
    from urllib.parse import quote
    did = _dealer_id(dealer_id)
    files = _list_bulk_folder_files(folder_path, did)
    rows = "".join(
        f'<tr><td><a href="/bulk-loads/file/{quote(folder_path + "/" + f["name"], safe="")}">{f["name"]}</a></td></tr>'
        for f in files
    )
    safe_path = folder_path.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Bulk Upload / {safe_path}</title>
<style>body{{font-family:sans-serif;margin:1rem}} table{{border-collapse:collapse}} td{{padding:0.25rem 0.5rem}} a{{color:#0066cc}}</style></head>
<body><h1>Bulk Upload / {safe_path}</h1><p>Files in this folder:</p><table><tbody>{rows or "<tr><td>No files</td></tr>"}</tbody></table></body></html>"""
    return HTMLResponse(html)


@router.get("/file/{file_path:path}")
def get_bulk_file(
    file_path: str,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
):
    """Serve a file from Bulk Upload (path: Rejected scans/Scan1_15032025/file.pdf)."""
    from fastapi.responses import FileResponse
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    did = _dealer_id(dealer_id)
    path = get_bulk_upload_dir(did) / file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@router.post("/{bulk_load_id:int}/prepare-reprocess")
def prepare_reprocess(
    bulk_load_id: int,
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """
    Prepare an error record for re-processing: split PDF from result_folder into Uploaded scans,
    run OCR, return subfolder and mobile for Add Customer view.
    """
    did = _dealer_id(dealer_id)
    conn = get_connection()
    try:
        row = BulkLoadsRepository.get_by_id(conn, bulk_load_id, dealer_id=did)
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

    archive_dir = get_bulk_upload_dir(did) / result_folder
    if not archive_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Archive folder not found: {result_folder}")

    from app.services.upload_service import UploadService
    from app.services.bulk_upload_service import _copy_classified_to_uploads, _split_pdf_to_images

    if mobile:
        subfolder = UploadService().get_subdir_name_mobile(mobile)
    else:
        subfolder = f"reprocess_{bulk_load_id}_{datetime.now().strftime('%H%M%S')}"

    uploads_subdir = get_uploads_dir(did) / subfolder
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

    uploaded_files = [p.name for p in saved]

    def run_ocr_background() -> None:
        try:
            from app.services.ocr_service import OcrService
            OcrService(
                uploads_dir=get_uploads_dir(did),
                ocr_output_dir=get_ocr_output_dir(did),
            ).process_uploaded_subfolder(subfolder)
        except Exception as e:
            logger.exception("prepare_reprocess: background OCR failed for %s: %s", subfolder, e)

    threading.Thread(target=run_ocr_background, daemon=True).start()

    return {"bulk_load_id": bulk_load_id, "subfolder": subfolder, "mobile": mobile, "uploadedFiles": uploaded_files}
