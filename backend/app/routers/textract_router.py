"""AWS Textract: extract text from uploaded document (e.g. Details sheet) to compare with Tesseract."""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import UPLOADS_DIR
from app.services.textract_service import (
    extract_text_from_bytes,
    extract_text_from_path,
    extract_forms_from_bytes,
)

router = APIRouter(prefix="/textract", tags=["textract"])


@router.post("/extract")
async def textract_extract(file: UploadFile = File(..., description="Document image (JPEG/PNG, max 5 MB)")) -> dict:
    """
    Run AWS Textract on the uploaded image. Returns full_text (all lines) and blocks
    so you can compare output with Tesseract (e.g. for Sales Detail Sheet).
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    return extract_text_from_bytes(raw)


@router.post("/extract-forms")
async def textract_extract_forms(file: UploadFile = File(..., description="Document image (JPEG/PNG, max 5 MB)")) -> dict:
    """
    Run Textract AnalyzeDocument with FORMS + TABLES. Returns full_text and key_value_pairs
    (form fields as key-value list). Best for structured forms/sheets.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    return extract_forms_from_bytes(raw)


@router.get("/extract-from-queue")
def textract_extract_from_queue(subfolder: str, filename: str, forms: bool = False) -> dict:
    """
    Run Textract on a file already in Uploaded scans.
    Query: ?subfolder=...&filename=...&forms=true for form key-value output.
    """
    path = UPLOADS_DIR / subfolder / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found in uploads")
    if forms:
        return extract_forms_from_bytes(path.read_bytes())
    return extract_text_from_path(path)
