from fastapi import APIRouter, File, Form, UploadFile

from app.services.upload_service import UploadService

router = APIRouter(prefix="/uploads", tags=["uploads"])

upload_service = UploadService()


@router.post("/scans")
async def upload_scans(
    aadhar_last4: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict:
    return await upload_service.save_and_queue(aadhar_last4, files)


@router.post("/scans-v2")
async def upload_scans_v2(
    mobile: str = Form(...),
    aadhar_scan: UploadFile = File(...),
    sales_detail: UploadFile = File(...),
) -> dict:
    """V2: subfolder = mobile_ddmmyy, files saved as Aadhar.jpg and Details.jpg."""
    return await upload_service.save_and_queue_v2(mobile, aadhar_scan, sales_detail)
