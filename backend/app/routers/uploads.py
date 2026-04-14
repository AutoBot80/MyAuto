from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.services.upload_service import UploadService

router = APIRouter(prefix="/uploads", tags=["uploads"])

upload_service = UploadService()


@router.post("/scans")
async def upload_scans(
    principal: Principal = Depends(get_principal),
    aadhar_last4: str = Form(...),
    files: list[UploadFile] = File(...),
    dealer_id: int | None = Form(None, description="Dealer ID; uses token dealer if omitted"),
) -> dict:
    did = resolve_dealer_id(principal, dealer_id)
    return await upload_service.save_and_queue(aadhar_last4, files, dealer_id=did)


@router.post("/scans-v2")
async def upload_scans_v2(
    principal: Principal = Depends(get_principal),
    mobile: str = Form(...),
    aadhar_scan: UploadFile = File(...),
    aadhar_back: UploadFile = File(...),
    sales_detail: UploadFile = File(...),
    insurance_sheet: UploadFile | None = File(None),
    financing_doc: UploadFile | None = File(None),
    dealer_id: int | None = Form(None, description="Dealer ID; uses token dealer if omitted"),
) -> dict:
    """Subfolder = mobile_ddmmyy; files saved as Aadhar_front.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg."""
    did = resolve_dealer_id(principal, dealer_id)
    return await upload_service.save_and_queue_v2(
        mobile, aadhar_scan, aadhar_back, sales_detail, insurance_sheet, financing_doc, dealer_id=did
    )


@router.post("/scans-v2-consolidated")
async def upload_scans_v2_consolidated(
    principal: Principal = Depends(get_principal),
    consolidated_pdf: UploadFile = File(..., description="Single PDF: Aadhaar + sales detail (multi-page ok)"),
    dealer_id: int | None = Form(None, description="Dealer ID; uses token dealer if omitted"),
    mobile: str = Form(
        "",
        description="Optional 10-digit Customer Mobile from Add Sales; used when OCR cannot read it on the scan.",
    ),
) -> dict:
    """Direct pre-OCR (in-process ``run_pre_ocr_and_prepare``) + Textract — not bulk ingest / ``bulk_loads``."""
    did = resolve_dealer_id(principal, dealer_id)
    return await upload_service.save_and_queue_v2_consolidated(
        consolidated_pdf,
        dealer_id=did,
        form_mobile=mobile or None,
    )


@router.post("/scans-v2-consolidated-stream")
async def upload_scans_v2_consolidated_stream(
    principal: Principal = Depends(get_principal),
    consolidated_pdf: UploadFile = File(..., description="Single PDF: Aadhaar + sales detail (multi-page ok)"),
    dealer_id: int | None = Form(None, description="Dealer ID; uses token dealer if omitted"),
    mobile: str = Form(
        "",
        description="Optional 10-digit Customer Mobile from Add Sales; used when OCR cannot read it on the scan.",
    ),
) -> StreamingResponse:
    """
    Same processing as ``/scans-v2-consolidated``, but responds with **SSE** (``text/event-stream``):
    ``event: partial`` when Aadhaar or Details merge is persisted (includes ``details`` + ``saved_to``),
    then ``event: complete`` with the same final JSON as the non-streaming route.
    """
    did = resolve_dealer_id(principal, dealer_id)
    return StreamingResponse(
        upload_service.save_and_queue_v2_consolidated_stream(
            consolidated_pdf,
            dealer_id=did,
            form_mobile=mobile or None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/manual-session/{session_id}/page/{page_1based}")
def get_manual_session_page(
    session_id: str,
    page_1based: int,
    principal: Principal = Depends(get_principal),
    dealer_id: int | None = Query(None, description="Dealer ID; uses token dealer if omitted"),
) -> FileResponse:
    """Serve a JPEG page from a pending manual split session (pre-OCR fallback)."""
    from app.services.manual_fallback_service import manual_session_page_path

    did = resolve_dealer_id(principal, dealer_id)
    path = manual_session_page_path(did, session_id, page_1based)
    if not path:
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@router.post("/scans-v2-consolidated/manual-apply")
async def apply_consolidated_manual_fallback(
    principal: Principal = Depends(get_principal),
    session_id: str = Form(..., description="Session id from manual_fallback response"),
    mobile: str = Form(..., description="10-digit customer mobile; determines upload subfolder"),
    assignments_json: str = Form(
        ...,
        description='JSON object mapping page index (string "0","1",…) to '
        '"aadhar_front" | "aadhar_back" | "details" | "unused"',
    ),
    dealer_id: int | None = Form(None, description="Dealer ID; uses token dealer if omitted"),
) -> dict:
    """Materialize ``for_OCR/`` from a manual session without running Textract/OCR extraction."""
    did = resolve_dealer_id(principal, dealer_id)
    return await upload_service.apply_consolidated_manual_fallback(
        session_id,
        mobile,
        assignments_json,
        dealer_id=did,
    )
