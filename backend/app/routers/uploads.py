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
