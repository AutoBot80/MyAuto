"""POST /fill-dms: run Playwright to fill DMS, scrape vehicle row, download Form 21 & 22 into upload subfolder."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import UPLOADS_DIR, OCR_OUTPUT_DIR, DMS_BASE_URL, DMS_LOGIN_USER, DMS_LOGIN_PASSWORD
from app.services.fill_dms_service import run_fill_dms

router = APIRouter(prefix="/fill-dms", tags=["fill-dms"])


class FillDmsCustomer(BaseModel):
    name: str | None = None
    address: str | None = None
    state: str | None = None
    pin_code: str | None = None
    mobile_number: str | None = None
    mobile: str | None = None


class FillDmsVehicle(BaseModel):
    key_no: str | None = None
    frame_no: str | None = None
    engine_no: str | None = None


class FillDmsRequest(BaseModel):
    subfolder: str
    dms_base_url: str | None = None
    customer: FillDmsCustomer = FillDmsCustomer()
    vehicle: FillDmsVehicle = FillDmsVehicle()


class FillDmsResponse(BaseModel):
    success: bool
    vehicle: dict
    pdfs_saved: list[str]
    error: str | None = None


@router.post("", response_model=FillDmsResponse)
async def fill_dms(req: FillDmsRequest) -> FillDmsResponse:
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    uploads_dir = Path(UPLOADS_DIR)
    if not uploads_dir.is_dir():
        raise HTTPException(status_code=500, detail="Uploads directory not found")
    customer_dict = req.customer.model_dump(exclude_none=True)
    if req.customer.mobile_number:
        customer_dict["mobile_number"] = req.customer.mobile_number
    if req.customer.mobile:
        customer_dict["mobile"] = req.customer.mobile
    vehicle_dict = req.vehicle.model_dump(exclude_none=True)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: run_fill_dms(
            dms_base_url=base_url,
            subfolder=req.subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            login_user=DMS_LOGIN_USER,
            login_password=DMS_LOGIN_PASSWORD,
            uploads_dir=uploads_dir,
            ocr_output_dir=Path(OCR_OUTPUT_DIR).resolve(),
        ),
    )
    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=result.get("vehicle") or {},
        pdfs_saved=result.get("pdfs_saved") or [],
        error=result.get("error"),
    )
