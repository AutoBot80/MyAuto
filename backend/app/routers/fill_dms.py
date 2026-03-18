"""POST /fill-dms: run Playwright to fill DMS, scrape vehicle row, download Form 21 & 22 into upload subfolder."""
import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import (
    DEALER_ID,
    DMS_BASE_URL,
    DMS_LOGIN_USER,
    DMS_LOGIN_PASSWORD,
    VAHAN_BASE_URL,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.services.fill_dms_service import run_fill_dms, run_fill_dms_only, run_fill_vahan_only, update_vehicle_master_from_dms as _update_vehicle_master_from_dms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fill-dms", tags=["fill-dms"])


class FillDmsCustomer(BaseModel):
    name: str | None = None
    care_of: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    pin_code: str | None = None
    mobile_number: str | None = None
    mobile: str | None = None
    aadhar_id: str | None = None


class FillDmsVehicle(BaseModel):
    key_no: str | None = None
    frame_no: str | None = None
    engine_no: str | None = None


class FillDmsRequest(BaseModel):
    subfolder: str
    dms_base_url: str | None = None
    vahan_base_url: str | None = None
    rto_dealer_id: str | None = None
    dealer_id: int | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    customer: FillDmsCustomer = FillDmsCustomer()
    vehicle: FillDmsVehicle = FillDmsVehicle()


class FillDmsResponse(BaseModel):
    success: bool
    vehicle: dict
    pdfs_saved: list[str]
    application_id: str | None = None
    rto_fees: float | None = None
    error: str | None = None


class FillVahanRequest(BaseModel):
    vahan_base_url: str
    rto_dealer_id: str | None = None
    customer_name: str | None = None
    chassis_no: str | None = None
    vehicle_model: str | None = None
    vehicle_colour: str | None = None
    fuel_type: str | None = None
    year_of_mfg: str | None = None
    total_cost: float | None = None


class FillVahanResponse(BaseModel):
    success: bool
    application_id: str | None = None
    rto_fees: float | None = None
    error: str | None = None


class PrintForm20Request(BaseModel):
    subfolder: str
    customer: FillDmsCustomer = FillDmsCustomer()
    vehicle: dict = {}
    vehicle_id: int | None = None
    dealer_id: int | None = None


class PrintForm20Response(BaseModel):
    success: bool
    pdfs_saved: list[str]
    error: str | None = None


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name for ocr_output."""
    import re
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _ensure_absolute_url(url: str, fallback_base: str = "http://127.0.0.1:8000") -> str:
    """Convert relative URL (e.g. /dummy-vaahan) to absolute for Playwright page.goto()."""
    if not url or not url.strip():
        return url
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return f"{fallback_base.rstrip('/')}{url}"
    return url


@router.get("/data-from-dms")
def get_data_from_dms(subfolder: str, dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted")) -> dict:
    """Read Data from DMS.txt for a subfolder; return parsed vehicle and customer. Used when Fill Forms data was written but UI state was lost."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    safe_name = _safe_subfolder_name(subfolder)
    path = get_ocr_output_dir(did) / safe_name / "Data from DMS.txt"
    if not path.exists():
        return {"vehicle": {}, "customer": {}}
    text = path.read_text(encoding="utf-8", errors="replace")
    vehicle: dict = {}
    customer: dict = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "--- Vehicle" in line:
            section = "vehicle"
            continue
        if "--- Customer" in line:
            section = "customer"
            continue
        if ":" in line and section:
            key_part, _, val = line.partition(":")
            key = key_part.strip().lower().replace(" ", "_").replace("/", "_")
            val = val.strip()
            if val == "—" or not val:
                val = ""
            if section == "vehicle":
                key_map = {"key_num": "key_num", "frame_chassis_num": "frame_num", "frame___chassis_num": "frame_num", "engine_num": "engine_num", "model": "model", "color": "color", "cubic_capacity": "cubic_capacity", "seating_capacity": "seating_capacity", "body_type": "body_type", "vehicle_type": "vehicle_type", "num_cylinders": "num_cylinders", "horse_power": "horse_power", "horsepower": "horse_power", "total_amount": "total_amount", "year_of_mfg": "year_of_mfg"}
                out_key = key_map.get(key, key)
                if val:
                    vehicle[out_key] = val
            elif section == "customer":
                key_map = {"name": "name", "care_of": "care_of", "address": "address", "city": "city", "state": "state", "pin_code": "pin_code", "mobile": "mobile_number"}
                out_key = key_map.get(key, key)
                if val:
                    customer[out_key] = val
    return {"vehicle": vehicle, "customer": customer}


@router.post("/dms", response_model=FillDmsResponse)
async def fill_dms_only(req: FillDmsRequest) -> FillDmsResponse:
    """Run only DMS (login, enquiry, vehicle search, scrape, PDFs). Independent process."""
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    base_url = _ensure_absolute_url(base_url)
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
        lambda: run_fill_dms_only(
            dms_base_url=base_url,
            subfolder=req.subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            login_user=DMS_LOGIN_USER,
            login_password=DMS_LOGIN_PASSWORD,
            uploads_dir=uploads_dir,
            ocr_output_dir=Path(get_ocr_output_dir(did)),
        ),
    )
    scraped = result.get("vehicle") or {}
    has_vehicle = bool(scraped.get("key_num") or scraped.get("frame_num") or scraped.get("engine_num"))
    if req.vehicle_id and has_vehicle:
        try:
            _update_vehicle_master_from_dms(req.vehicle_id, scraped)
        except Exception as e:
            logger.warning("fill_dms: vehicle_master update failed vehicle_id=%s: %s", req.vehicle_id, e)

    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=scraped,
        pdfs_saved=result.get("pdfs_saved") or [],
        application_id=None,
        rto_fees=None,
        error=result.get("error"),
    )


@router.get("/form20-status")
def form20_status() -> dict:
    """Debug: check Form 20 template paths and fitz availability."""
    from pathlib import Path

    from app.config import FORM20_TEMPLATE_SINGLE, FORM20_TEMPLATE_FRONT, FORM20_TEMPLATE_BACK, FORM20_TEMPLATE_DOCX, GATE_PASS_TEMPLATE_DOCX, UPLOADS_DIR

    project_root = Path(get_uploads_dir(DEALER_ID)).resolve().parent
    single = Path(FORM20_TEMPLATE_SINGLE).resolve()
    front = Path(FORM20_TEMPLATE_FRONT).resolve()
    back = Path(FORM20_TEMPLATE_BACK).resolve()
    fallback_single = project_root / "Raw Scans" / "Official FORM-20 english.pdf"
    try:
        import fitz  # noqa: F401
        fitz_ok = True
    except ImportError:
        fitz_ok = False

    docx_template = Path(FORM20_TEMPLATE_DOCX).resolve()
    docx_exists = docx_template.exists()
    gate_pass_template = Path(GATE_PASS_TEMPLATE_DOCX).resolve()
    gate_pass_exists = gate_pass_template.exists()
    single_exists = single.exists() or fallback_single.exists()
    return {
        "docx_template": str(docx_template),
        "docx_exists": docx_exists,
        "gate_pass_template": str(gate_pass_template),
        "gate_pass_exists": gate_pass_exists,
        "single_template": str(single),
        "single_exists": single.exists(),
        "fallback_single": str(fallback_single),
        "fallback_single_exists": fallback_single.exists(),
        "front_template": str(front),
        "front_exists": front.exists(),
        "back_template": str(back),
        "back_exists": back.exists(),
        "project_root": str(project_root),
        "fitz_available": fitz_ok,
        "will_use_word": docx_exists,
        "will_use_pdf_overlay": single_exists and fitz_ok and not docx_exists,
    }


@router.post("/print-form20", response_model=PrintForm20Response)
async def print_form20(req: PrintForm20Request) -> PrintForm20Response:
    """Generate Form 20 (all pages) and save to Uploaded scans/subfolder. Called from Print forms button."""
    did = req.dealer_id if req.dealer_id is not None else DEALER_ID
    uploads_dir = Path(get_uploads_dir(did))
    if not uploads_dir.is_dir():
        raise HTTPException(status_code=500, detail="Uploads directory not found")
    customer_dict = req.customer.model_dump(exclude_none=True)
    if req.customer.mobile_number:
        customer_dict["mobile_number"] = req.customer.mobile_number
    if req.customer.mobile:
        customer_dict["mobile"] = req.customer.mobile
    # Map client vehicle keys (key_no, frame_no, engine_no) to form20 expected keys
    vehicle_dict = dict(req.vehicle or {})
    if "key_no" in vehicle_dict and "key_num" not in vehicle_dict:
        vehicle_dict["key_num"] = vehicle_dict.get("key_no")
    if "frame_no" in vehicle_dict and "frame_num" not in vehicle_dict:
        vehicle_dict["frame_num"] = vehicle_dict.get("frame_no")
    if "engine_no" in vehicle_dict and "engine_num" not in vehicle_dict:
        vehicle_dict["engine_num"] = vehicle_dict.get("engine_no")
    try:
        from app.services.form20_service import generate_form20_pdfs

        form20_saved = generate_form20_pdfs(
            subfolder=req.subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            vehicle_id=req.vehicle_id,
            dealer_id=req.dealer_id,
            uploads_dir=uploads_dir,
        )
        return PrintForm20Response(success=True, pdfs_saved=form20_saved)
    except Exception as e:
        logger.warning("print_form20: Form 20 generation failed: %s", e)
        return PrintForm20Response(success=False, pdfs_saved=[], error=str(e))


@router.post("/vahan", response_model=FillVahanResponse)
async def fill_vahan_only(req: FillVahanRequest) -> FillVahanResponse:
    """Run only Vahan (RTO registration). Independent process."""
    vahan_url = (req.vahan_base_url or VAHAN_BASE_URL or "").strip()
    if not vahan_url:
        raise HTTPException(status_code=400, detail="vahan_base_url required")
    vahan_url = _ensure_absolute_url(vahan_url)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: run_fill_vahan_only(
            vahan_base_url=vahan_url,
            rto_dealer_id=(req.rto_dealer_id or "").strip() or "RTO100001",
            customer_name=str(req.customer_name or ""),
            chassis_no=str(req.chassis_no or ""),
            vehicle_model=str(req.vehicle_model or ""),
            vehicle_colour=str(req.vehicle_colour or ""),
            fuel_type=str(req.fuel_type or "Petrol"),
            year_of_mfg=str(req.year_of_mfg or ""),
            total_cost=float(req.total_cost or 72000),
        ),
    )
    return FillVahanResponse(
        success=result.get("error") is None,
        application_id=result.get("application_id"),
        rto_fees=result.get("rto_fees"),
        error=result.get("error"),
    )


@router.post("", response_model=FillDmsResponse)
async def fill_dms(req: FillDmsRequest) -> FillDmsResponse:
    logger.info("fill_dms: start subfolder=%s dms=%s vahan=%s", req.subfolder, bool(req.dms_base_url), bool(req.vahan_base_url))
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        logger.warning("fill_dms: dms_base_url missing")
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    base_url = _ensure_absolute_url(base_url)
    did = req.dealer_id if req.dealer_id is not None else DEALER_ID
    uploads_dir = Path(get_uploads_dir(did))
    if not uploads_dir.is_dir():
        raise HTTPException(status_code=500, detail="Uploads directory not found")
    customer_dict = req.customer.model_dump(exclude_none=True)
    if req.customer.mobile_number:
        customer_dict["mobile_number"] = req.customer.mobile_number
    if req.customer.mobile:
        customer_dict["mobile"] = req.customer.mobile
    vehicle_dict = req.vehicle.model_dump(exclude_none=True)
    vahan_url = (req.vahan_base_url or VAHAN_BASE_URL or "").strip() or None
    if vahan_url:
        vahan_url = _ensure_absolute_url(vahan_url)
    logger.info("fill_dms: calling run_fill_dms base_url=%s vahan_url=%s", base_url[:60] if base_url else None, (vahan_url[:60] if vahan_url else None))
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
            ocr_output_dir=Path(get_ocr_output_dir(did)),
            vahan_base_url=vahan_url,
            rto_dealer_id=req.rto_dealer_id,
        ),
    )
    scraped = result.get("vehicle") or {}
    has_vehicle = bool(scraped.get("key_num") or scraped.get("frame_num") or scraped.get("engine_num"))
    logger.info(
        "fill_dms: run_fill_dms done success=%s vehicle=%s application_id=%s rto_fees=%s error=%s",
        result.get("error") is None,
        has_vehicle,
        result.get("application_id"),
        result.get("rto_fees"),
        result.get("error"),
    )
    if req.vehicle_id and has_vehicle:
        try:
            _update_vehicle_master_from_dms(req.vehicle_id, scraped)
        except Exception as e:
            logger.warning("fill_dms: vehicle_master update failed vehicle_id=%s: %s", req.vehicle_id, e)

    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=scraped,
        pdfs_saved=result.get("pdfs_saved") or [],
        application_id=result.get("application_id"),
        rto_fees=result.get("rto_fees"),
        error=result.get("error"),
    )
