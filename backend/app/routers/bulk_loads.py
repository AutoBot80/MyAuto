"""Bulk Loads: list and filter bulk upload processing results."""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

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
    status: str | None = Query(None, description="Filter: Success, Error, or both (omit)"),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict]:
    """List bulk loads, sorted latest first. Filter by status if provided."""
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        rows = BulkLoadsRepository.list_all(conn, limit=limit, status_filter=status)
        for r in rows:
            for k in ("created_at", "updated_at"):
                if k in r and isinstance(r[k], datetime):
                    r[k] = r[k].isoformat()
        return rows
    finally:
        conn.close()


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
            raise HTTPException(status_code=400, detail="Only Error records can be re-processed")
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
