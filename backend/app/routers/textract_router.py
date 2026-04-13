"""AWS Textract: extract text from uploaded document (e.g. Details sheet) to compare with Tesseract."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.config import UPLOAD_MAX_FILE_BYTES, get_uploads_dir
from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.services.sales_textract_service import (
    extract_text_from_bytes,
    extract_text_from_path,
    extract_forms_from_bytes,
)
from app.services.upload_file_validation import read_upload_capped

router = APIRouter(prefix="/textract", tags=["textract"])


@router.post("/extract")
async def textract_extract(
    file: UploadFile = File(..., description="Document image (JPEG/PNG, max 500 KB)"),
) -> dict:
    """
    Run AWS Textract on the uploaded image. Returns full_text (all lines) and blocks
    so you can compare output with Tesseract (e.g. for Sales Detail Sheet).
    """
    try:
        raw = await read_upload_capped(file, UPLOAD_MAX_FILE_BYTES)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    return extract_text_from_bytes(raw)


@router.post("/extract-forms")
async def textract_extract_forms(
    file: UploadFile = File(..., description="Document image (JPEG/PNG, max 500 KB)"),
) -> dict:
    """
    Run Textract AnalyzeDocument with FORMS + TABLES. Returns full_text and key_value_pairs
    (form fields as key-value list). Best for structured forms/sheets.
    """
    try:
        raw = await read_upload_capped(file, UPLOAD_MAX_FILE_BYTES)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    return extract_forms_from_bytes(raw)


@router.get("/extract-from-queue")
def textract_extract_from_queue(
    subfolder: str,
    filename: str,
    principal: Principal = Depends(get_principal),
    forms: bool = False,
    dealer_id: int | None = Query(None, description="Dealer ID; uses token dealer if omitted"),
) -> dict:
    """
    Run Textract on a file already in Uploaded scans.
    Query: ?subfolder=...&filename=...&forms=true for form key-value output.
    """
    did = resolve_dealer_id(principal, dealer_id)
    path = get_uploads_dir(did) / subfolder / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found in uploads")
    try:
        sz = path.stat().st_size
    except OSError:
        raise HTTPException(status_code=404, detail="File not found in uploads") from None
    if sz > UPLOAD_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size ({UPLOAD_MAX_FILE_BYTES // 1024} KB).",
        )
    raw = path.read_bytes()
    if len(raw) > UPLOAD_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size ({UPLOAD_MAX_FILE_BYTES // 1024} KB).",
        )
    if forms:
        return extract_forms_from_bytes(raw)
    return extract_text_from_path(path)
