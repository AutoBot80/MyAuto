from fastapi import APIRouter, File, Form, UploadFile

from app.config import DEALER_ID
from app.services.upload_service import UploadService

router = APIRouter(prefix="/uploads", tags=["uploads"])

upload_service = UploadService()


@router.post("/scans")
async def upload_scans(
    aadhar_last4: str = Form(...),
    files: list[UploadFile] = File(...),
    dealer_id: int | None = Form(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    did = dealer_id if dealer_id is not None else DEALER_ID
    return await upload_service.save_and_queue(aadhar_last4, files, dealer_id=did)


@router.post("/scans-v2")
async def upload_scans_v2(
    mobile: str = Form(...),
    aadhar_scan: UploadFile = File(...),
    aadhar_back: UploadFile = File(...),
    sales_detail: UploadFile = File(...),
    insurance_sheet: UploadFile | None = File(None),
    financing_doc: UploadFile | None = File(None),
    dealer_id: int | None = Form(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Subfolder = mobile_ddmmyy; files saved as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    return await upload_service.save_and_queue_v2(
        mobile, aadhar_scan, aadhar_back, sales_detail, insurance_sheet, financing_doc, dealer_id=did
    )
