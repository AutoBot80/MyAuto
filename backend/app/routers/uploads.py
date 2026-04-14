from fastapi import APIRouter, Depends, File, Form, UploadFile

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
