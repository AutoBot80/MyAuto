"""POST /fill-dms: run Playwright to fill DMS, scrape vehicle row, download Form 21 & 22 into upload subfolder."""
import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import (
    DEALER_ID,
    DMS_BASE_URL,
    DMS_LOGIN_USER,
    DMS_LOGIN_PASSWORD,
    INSURANCE_BASE_URL,
    VAHAN_BASE_URL,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.services.fill_dms_service import (
    run_fill_dms,
    run_fill_dms_only,
    run_fill_insurance_only,
    run_fill_vahan_only,
    update_vehicle_master_from_dms as _update_vehicle_master_from_dms,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fill-dms", tags=["fill-dms"])

DMS_NO_VEHICLE_ERROR = (
    "No such vehicle found in DMS. Please edit Vehicle Info and submit form again."
)

# Shown when DMS_MODE=real: URLs open but Siebel field entry is not implemented yet.
DMS_REAL_MODE_FORMS_NOT_FILLED_WARNING = (
    "DMS_MODE=real: Siebel automation did not complete (no contact/vehicle fill). "
    "Check backend logs, set DMS_SIEBEL_CONTENT_FRAME_SELECTOR / DMS_SIEBEL_MOBILE_ARIA_HINTS if needed, "
    "or set DMS_MODE=dummy for the training site."
)


def _normalize_automation_error(raw_error: str | None) -> str | None:
    if not raw_error:
        return raw_error
    message = str(raw_error).strip()
    if not message:
        return None

    if "Missing required DMS DB values:" in message:
        fields = [p.strip() for p in message.split(":", 1)[1].split(",") if p.strip()]
        if not fields:
            return "DMS cannot continue because required database fields are missing."
        return (
            "DMS cannot continue because required DB fields are missing. "
            "Please complete Submit Info data and retry. Missing: "
            + ", ".join(fields)
            + "."
        )

    if "Missing required Vahan DB values:" in message:
        fields = [p.strip() for p in message.split(":", 1)[1].split(",") if p.strip()]
        if not fields:
            return "Vahan cannot continue because required database fields are missing."
        return (
            "Vahan cannot continue because required DB fields are missing. "
            "Please complete Submit Info / DMS data and retry. Missing: "
            + ", ".join(fields)
            + "."
        )
    if "Missing required Insurance DB values:" in message:
        fields = [p.strip() for p in message.split(":", 1)[1].split(",") if p.strip()]
        if not fields:
            return "Insurance cannot continue because required database fields are missing."
        return (
            "Insurance cannot continue because required DB fields are missing. "
            "Please complete Submit Info data and retry. Missing: "
            + ", ".join(fields)
            + "."
        )

    if "form_vahan_view.vehicle_price is empty" in message or "vehicle_price must be positive" in message:
        return (
            "Vahan cannot continue because vehicle price is missing in DB. "
            "Run DMS first so vehicle price is scraped and saved, then retry."
        )

    if "Vahan result missing data-rto-fees value" in message:
        return "Vahan completed navigation but RTO fees could not be scraped from the result screen. Please retry."
    if "Vahan result row not found for rto_fees scrape" in message:
        return "Vahan result section did not return a fees row. Please retry after confirming site session is active."
    if "Vahan rto_fees could not be scraped" in message:
        return "Vahan could not scrape RTO fees. Please retry."

    return message


def _has_scraped_vehicle(scraped: dict) -> bool:
    """True when DMS scrape returned at least one key vehicle identifier."""
    key_num = str(scraped.get("key_num") or "").strip()
    frame_num = str(scraped.get("frame_num") or "").strip()
    engine_num = str(scraped.get("engine_num") or "").strip()
    full_chassis = str(scraped.get("full_chassis") or "").strip()
    full_engine = str(scraped.get("full_engine") or "").strip()
    return bool(key_num or frame_num or engine_num or full_chassis or full_engine)


def _dms_response_warning_and_mode(result: dict) -> tuple[str | None, str | None]:
    """If real Siebel ran but automation did not finish, warn so UI does not claim full success."""
    raw = result.get("dms_automation_mode")
    mode = raw if isinstance(raw, str) and raw.strip() else None
    if result.get("error") is not None:
        return None, mode
    if mode == "real" and result.get("dms_siebel_forms_filled"):
        return None, mode
    if mode == "real":
        return DMS_REAL_MODE_FORMS_NOT_FILLED_WARNING, mode
    return None, mode


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
    # When set (e.g. DMS_MODE=real), UI must not claim forms were auto-filled.
    warning: str | None = None
    dms_automation_mode: str | None = None
    # Ordered checklist labels completed during the last DMS run (Add Sales banner).
    dms_milestones: list[str] = Field(default_factory=list)
    # Real Siebel: ordered operator-facing sentences (where the flow got to); preferred over milestones in UI when non-empty.
    dms_step_messages: list[str] = Field(default_factory=list)


class FillVahanRequest(BaseModel):
    vahan_base_url: str
    rto_dealer_id: str | None = None
    dealer_id: int | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    subfolder: str | None = None
    customer_name: str | None = None
    chassis_no: str | None = None
    vehicle_model: str | None = None
    vehicle_colour: str | None = None
    fuel_type: str | None = None
    year_of_mfg: str | None = None
    vehicle_price: float | None = None


class FillVahanResponse(BaseModel):
    success: bool
    application_id: str | None = None
    rto_fees: float | None = None
    error: str | None = None


class FillInsuranceRequest(BaseModel):
    insurance_base_url: str | None = None
    dealer_id: int | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    subfolder: str | None = None


class FillInsuranceResponse(BaseModel):
    success: bool
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


def _require_absolute_http_url(url: str, field_name: str) -> str:
    """Playwright needs an absolute URL; .env must supply full URLs (no host/path fallbacks)."""
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be an absolute URL (http:// or https://). Set DMS_BASE_URL, VAHAN_BASE_URL, or INSURANCE_BASE_URL in backend/.env.",
        )
    return u.rstrip("/")


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
                key_map = {"key_num": "key_num", "frame_chassis_num": "frame_num", "frame___chassis_num": "frame_num", "engine_num": "engine_num", "model": "model", "color": "color", "cubic_capacity": "cubic_capacity", "seating_capacity": "seating_capacity", "body_type": "body_type", "vehicle_type": "vehicle_type", "num_cylinders": "num_cylinders", "horse_power": "horse_power", "horsepower": "horse_power", "total_amount": "vehicle_price", "vehicle_price": "vehicle_price", "year_of_mfg": "year_of_mfg"}
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
    base_url = _require_absolute_http_url(base_url, "dms_base_url")
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
            customer_id=req.customer_id,
            vehicle_id=req.vehicle_id,
        ),
    )
    scraped = result.get("vehicle") or {}
    has_vehicle = _has_scraped_vehicle(scraped)
    # Real Siebel before automation, or incomplete run: do not force "no vehicle" when we never searched.
    skip_no_vehicle = result.get("dms_automation_mode") == "real" and not result.get("dms_siebel_forms_filled")
    if result.get("error") is None and not has_vehicle and not skip_no_vehicle:
        result["error"] = DMS_NO_VEHICLE_ERROR
    if req.vehicle_id and has_vehicle:
        try:
            _update_vehicle_master_from_dms(req.vehicle_id, scraped)
        except Exception as e:
            logger.warning("fill_dms: vehicle_master update failed vehicle_id=%s: %s", req.vehicle_id, e)

    warn, dms_mode = _dms_response_warning_and_mode(result)
    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=scraped,
        pdfs_saved=result.get("pdfs_saved") or [],
        application_id=None,
        rto_fees=None,
        error=_normalize_automation_error(result.get("error")),
        warning=warn,
        dms_automation_mode=dms_mode,
        dms_milestones=list(result.get("dms_milestones") or []),
        dms_step_messages=list(result.get("dms_step_messages") or []),
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
    vahan_url = _require_absolute_http_url(vahan_url, "vahan_base_url")
    did = req.dealer_id if req.dealer_id is not None else DEALER_ID
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: run_fill_vahan_only(
            vahan_base_url=vahan_url,
            rto_dealer_id=(req.rto_dealer_id or "").strip(),
            customer_name=str(req.customer_name or ""),
            chassis_no=str(req.chassis_no or ""),
            vehicle_model=str(req.vehicle_model or ""),
            vehicle_colour=str(req.vehicle_colour or ""),
            fuel_type=str(req.fuel_type or ""),
            year_of_mfg=str(req.year_of_mfg or ""),
            vehicle_price=float(req.vehicle_price or 0),
            ocr_output_dir=Path(get_ocr_output_dir(did)),
            subfolder=req.subfolder,
            customer_id=req.customer_id,
            vehicle_id=req.vehicle_id,
        ),
    )
    return FillVahanResponse(
        success=result.get("error") is None,
        application_id=result.get("application_id"),
        rto_fees=result.get("rto_fees"),
        error=_normalize_automation_error(result.get("error")),
    )


@router.post("/insurance", response_model=FillInsuranceResponse)
async def fill_insurance_only(req: FillInsuranceRequest) -> FillInsuranceResponse:
    """Run Insurance flow only. Fills fields but does not submit/issue policy."""
    insurance_url = (req.insurance_base_url or INSURANCE_BASE_URL or "").strip()
    if not insurance_url:
        raise HTTPException(status_code=400, detail="insurance_base_url required")
    insurance_url = _require_absolute_http_url(insurance_url, "insurance_base_url")
    did = req.dealer_id if req.dealer_id is not None else DEALER_ID
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: run_fill_insurance_only(
            insurance_base_url=insurance_url,
            subfolder=req.subfolder,
            customer_id=req.customer_id,
            vehicle_id=req.vehicle_id,
            ocr_output_dir=Path(get_ocr_output_dir(did)),
        ),
    )
    return FillInsuranceResponse(
        success=result.get("error") is None and bool(result.get("success")),
        error=_normalize_automation_error(result.get("error")),
    )


@router.post("", response_model=FillDmsResponse)
async def fill_dms(req: FillDmsRequest) -> FillDmsResponse:
    logger.info("fill_dms: start subfolder=%s dms=%s vahan=%s", req.subfolder, bool(req.dms_base_url), bool(req.vahan_base_url))
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        logger.warning("fill_dms: dms_base_url missing")
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    base_url = _require_absolute_http_url(base_url, "dms_base_url")
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
        vahan_url = _require_absolute_http_url(vahan_url, "vahan_base_url")
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
            customer_id=req.customer_id,
            vehicle_id=req.vehicle_id,
        ),
    )
    scraped = result.get("vehicle") or {}
    has_vehicle = _has_scraped_vehicle(scraped)
    logger.info(
        "fill_dms: run_fill_dms done success=%s vehicle=%s application_id=%s rto_fees=%s error=%s",
        result.get("error") is None,
        has_vehicle,
        result.get("application_id"),
        result.get("rto_fees"),
        result.get("error"),
    )
    skip_nv = result.get("dms_automation_mode") == "real" and not result.get("dms_siebel_forms_filled")
    if result.get("error") is None and not has_vehicle and not skip_nv:
        result["error"] = DMS_NO_VEHICLE_ERROR

    if req.vehicle_id and has_vehicle:
        try:
            _update_vehicle_master_from_dms(req.vehicle_id, scraped)
        except Exception as e:
            logger.warning("fill_dms: vehicle_master update failed vehicle_id=%s: %s", req.vehicle_id, e)

    warn, dms_mode = _dms_response_warning_and_mode(result)
    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=scraped,
        pdfs_saved=result.get("pdfs_saved") or [],
        application_id=result.get("application_id"),
        rto_fees=result.get("rto_fees"),
        error=_normalize_automation_error(result.get("error")),
        warning=warn,
        dms_automation_mode=dms_mode,
        dms_milestones=list(result.get("dms_milestones") or []),
        dms_step_messages=list(result.get("dms_step_messages") or []),
    )
