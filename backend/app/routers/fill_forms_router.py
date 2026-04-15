"""POST /fill-forms: Playwright fill for DMS, insurance (hero), Form 21/22, etc."""
import asyncio
import logging
import re
from copy import deepcopy
from functools import partial
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import (
    DMS_BASE_URL,
    DMS_LOGIN_USER,
    DMS_LOGIN_PASSWORD,
    INSURANCE_BASE_URL,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.services.fill_hero_dms_service import (
    run_fill_dms,
    run_fill_dms_only,
    warm_dms_browser_session,
)
from app.services.fill_hero_insurance_service import (
    main_process,
    post_process,
    pre_process,
)
from app.services.fill_rto_service import warm_vahan_browser_session
from app.services.playwright_executor import get_playwright_executor
from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.validation.text_limits import enforce_max_text_depth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fill-forms", tags=["fill-forms"])


async def _run_playwright_work(call):
    """Run sync Playwright-backed work off the Uvicorn asyncio thread."""
    return await asyncio.get_running_loop().run_in_executor(get_playwright_executor(), call)

DMS_NO_VEHICLE_ERROR = (
    "No such vehicle found in DMS. Please edit Vehicle Info and submit form again."
)

# Shown when Siebel automation did not complete (no contact/vehicle fill).
DMS_REAL_MODE_FORMS_NOT_FILLED_WARNING = (
    "Siebel automation did not complete (no contact/vehicle fill). "
    "Check backend logs and set DMS_SIEBEL_CONTENT_FRAME_SELECTOR / DMS_SIEBEL_MOBILE_ARIA_HINTS if needed."
)


def _normalize_automation_error(raw_error: str | None) -> str | None:
    if not raw_error:
        return raw_error
    message = str(raw_error).strip()
    if not message:
        return None

    if "Missing required DMS DB values:" in message or "Missing required DMS fields" in message:
        if "Missing required DMS fields" in message:
            tail = message.split("Missing required DMS fields", 1)[-1].lstrip()
            return (
                "DMS cannot continue because required fields are missing in the staging payload or database. "
                + tail
            )
        fields = [p.strip() for p in message.split(":", 1)[1].split(",") if p.strip()]
        if not fields:
            return "DMS cannot continue because required database fields are missing."
        return (
            "DMS cannot continue because required DB fields are missing. "
            "Please complete Submit Info data and retry. Missing: "
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
    dealer_id: int | None = None
    staging_id: str | None = Field(
        None,
        description="add_sales_staging UUID (draft or committed); DMS fill from payload_json when set.",
    )
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
    customer_id: int | None = Field(
        default=None,
        description="After staging-path DMS success: committed customer_master id.",
    )
    vehicle_id: int | None = Field(
        default=None,
        description="After staging-path DMS success: committed vehicle_master id.",
    )
    # When set, UI must not claim forms were auto-filled.
    warning: str | None = None
    dms_automation_mode: str | None = None
    # Ordered checklist labels completed during the last DMS run (Add Sales banner).
    dms_milestones: list[str] = Field(default_factory=list)
    # Real Siebel: ordered operator-facing sentences (where the flow got to); preferred over milestones in UI when non-empty.
    dms_step_messages: list[str] = Field(default_factory=list)
    # Real Siebel: My Orders grid already showed Invoice# — UI may enable Create Invoice without waiting for DB scrape commit.
    ready_for_client_create_invoice: bool | None = None
    # After staging commit: Run Report PDF batch status (GST Retail Invoice, GST Booking Receipt); see BR-21 / LLD 6.276.
    hero_dms_form22_print: dict | None = None


class FillHeroInsuranceRequest(BaseModel):
    """
    ``insurance_base_url`` overrides ``INSURANCE_BASE_URL`` from ``.env``.
    With ``customer_id`` and ``vehicle_id``, **pre_process** loads insurer / mobile / KYC steps; **main_process**
    fills VIN (chassis from DB), **I agree**, and the proposal form (master-backed fields + hardcoded proposal defaults).
    """

    insurance_base_url: str | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    subfolder: str | None = None
    dealer_id: int | None = None
    staging_id: str | None = Field(
        None,
        description="Optional add_sales_staging UUID (draft or committed); merges OCR/Submit snapshot into insurer/nominee when form_insurance_view is sparse.",
    )


class FillHeroInsuranceResponse(BaseModel):
    success: bool
    error: str | None = None
    page_url: str | None = None
    login_url: str | None = None
    match_base: str | None = None


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


class WarmDmsBrowserRequest(BaseModel):
    dms_base_url: str | None = Field(
        default=None,
        description="Absolute DMS base URL; defaults to DMS_BASE_URL (Hero defaults in app.hero_dms_defaults).",
    )


class WarmDmsBrowserResponse(BaseModel):
    success: bool
    error: str | None = None


class WarmVahanBrowserResponse(BaseModel):
    success: bool
    error: str | None = None
    message: str | None = None


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name for ocr_output."""
    import re
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _fill_dms_staging_or_ids(
    staging_id: str | None,
    dealer_id: int,
    customer_id: int | None,
    vehicle_id: int | None,
    customer_dict: dict,
    vehicle_dict: dict,
) -> tuple[dict | None, int | None, int | None]:
    """
    Returns ``(staging_payload, customer_id, vehicle_id)``.
    When ``staging_id`` is set, loads ``add_sales_staging`` (``draft`` or ``committed``) and merges request overrides.
    Otherwise requires both ``customer_id`` and ``vehicle_id`` (legacy master-backed path).
    """
    sid = (staging_id or "").strip()
    if sid:
        try:
            UUID(sid)
        except ValueError:
            raise HTTPException(status_code=400, detail="staging_id must be a valid UUID") from None
        from app.repositories.add_sales_staging import fetch_staging_payload

        raw = fetch_staging_payload(sid, dealer_id)
        if not raw:
            raise HTTPException(
                status_code=404,
                detail="Staging not found, abandoned, or dealer_id does not match.",
            )
        payload = deepcopy(raw)
        if customer_dict:
            c = {k: v for k, v in customer_dict.items() if v is not None and str(v).strip() != ""}
            if c:
                payload.setdefault("customer", {})
                if not isinstance(payload["customer"], dict):
                    payload["customer"] = {}
                payload["customer"].update(c)
        if vehicle_dict:
            v = {k: v2 for k, v2 in vehicle_dict.items() if v2 is not None and str(v2).strip() != ""}
            if v:
                payload.setdefault("vehicle", {})
                if not isinstance(payload["vehicle"], dict):
                    payload["vehicle"] = {}
                payload["vehicle"].update(v)
        return payload, None, None
    if customer_id is None or vehicle_id is None:
        raise HTTPException(
            status_code=400,
            detail="Provide staging_id (OCR staging snapshot) or both customer_id and vehicle_id.",
        )
    return None, customer_id, vehicle_id


def _require_absolute_http_url(url: str, field_name: str) -> str:
    """Playwright needs an absolute URL; .env must supply full URLs (no host/path fallbacks)."""
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be an absolute URL (http:// or https://). Set INSURANCE_BASE_URL in backend/.env or override DMS_BASE_URL in server config.",
        )
    return u.rstrip("/")


@router.get("/data-from-dms")
def get_data_from_dms(
    subfolder: str,
    principal: Principal = Depends(get_principal),
    dealer_id: int | None = Query(None, description="Dealer ID; uses token dealer if omitted"),
) -> dict:
    """Read Data from DMS.txt for a subfolder; return parsed vehicle and customer. Used when Fill Forms data was written but UI state was lost."""
    did = resolve_dealer_id(principal, dealer_id)
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
                key_map = {"key_num": "key_num", "frame_chassis_num": "frame_num", "frame___chassis_num": "frame_num", "engine_num": "engine_num", "model": "model", "color": "color", "cubic_capacity": "cubic_capacity", "seating_capacity": "seating_capacity", "body_type": "body_type", "vehicle_type": "vehicle_type", "num_cylinders": "num_cylinders", "total_amount": "vehicle_price", "vehicle_price": "vehicle_price", "year_of_mfg": "year_of_mfg"}
                out_key = key_map.get(key, key)
                if val:
                    vehicle[out_key] = val
            elif section == "customer":
                key_map = {"name": "name", "care_of": "care_of", "address": "address", "city": "city", "state": "state", "pin_code": "pin_code", "mobile": "mobile_number"}
                out_key = key_map.get(key, key)
                if val:
                    customer[out_key] = val
    return {"vehicle": vehicle, "customer": customer}


@router.post("/dms/warm-browser", response_model=WarmDmsBrowserResponse)
async def warm_dms_browser(req: WarmDmsBrowserRequest) -> WarmDmsBrowserResponse:
    """
    Open or attach to DMS (login wait only); no fill. Add Sales calls this **after** OCR text has been
    applied in the client (upload extraction or polling), so the browser starts once the form has data.
    """
    enforce_max_text_depth(req.model_dump())
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    base_url = _require_absolute_http_url(base_url, "dms_base_url")
    result = await _run_playwright_work(partial(warm_dms_browser_session, base_url))
    return WarmDmsBrowserResponse(
        success=bool(result.get("success")),
        error=result.get("error"),
    )


@router.post("/vahan/warm-browser", response_model=WarmVahanBrowserResponse)
async def warm_vahan_browser() -> WarmVahanBrowserResponse:
    """Open or attach to Vahan (no fill). RTO Queue: first click runs this; operator logs in; second click starts batch."""
    result = await _run_playwright_work(warm_vahan_browser_session)
    return WarmVahanBrowserResponse(
        success=bool(result.get("success")),
        error=result.get("error"),
        message=result.get("message"),
    )


@router.post("/dms", response_model=FillDmsResponse)
async def fill_dms_only(
    req: FillDmsRequest,
    principal: Principal = Depends(get_principal),
) -> FillDmsResponse:
    """Run only DMS (login, enquiry, vehicle search, scrape, PDFs). Independent process."""
    enforce_max_text_depth(req.model_dump())
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    base_url = _require_absolute_http_url(base_url, "dms_base_url")
    did = resolve_dealer_id(principal, req.dealer_id)
    uploads_dir = Path(get_uploads_dir(did))
    if not uploads_dir.is_dir():
        raise HTTPException(status_code=500, detail="Uploads directory not found")
    customer_dict = req.customer.model_dump(exclude_none=True)
    if req.customer.mobile_number:
        customer_dict["mobile_number"] = req.customer.mobile_number
    if req.customer.mobile:
        customer_dict["mobile"] = req.customer.mobile
    vehicle_dict = req.vehicle.model_dump(exclude_none=True)
    staging_payload, cid, vid = _fill_dms_staging_or_ids(
        req.staging_id,
        did,
        req.customer_id,
        req.vehicle_id,
        customer_dict,
        vehicle_dict,
    )
    sid_for_commit = (req.staging_id or "").strip() or None
    result = await _run_playwright_work(
        partial(
            run_fill_dms_only,
            dms_base_url=base_url,
            subfolder=req.subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            login_user=DMS_LOGIN_USER,
            login_password=DMS_LOGIN_PASSWORD,
            uploads_dir=uploads_dir,
            ocr_output_dir=Path(get_ocr_output_dir(did)),
            dealer_id=did,
            customer_id=cid,
            vehicle_id=vid,
            staging_payload=staging_payload,
            staging_id=sid_for_commit,
        )
    )
    scraped = result.get("vehicle") or {}
    has_vehicle = _has_scraped_vehicle(scraped)
    # Real Siebel before automation, or incomplete run: do not force "no vehicle" when we never searched.
    skip_no_vehicle = result.get("dms_automation_mode") == "real" and not result.get("dms_siebel_forms_filled")
    if result.get("error") is None and not has_vehicle and not skip_no_vehicle:
        result["error"] = DMS_NO_VEHICLE_ERROR

    warn, dms_mode = _dms_response_warning_and_mode(result)
    cc = result.get("committed_customer_id")
    vv = result.get("committed_vehicle_id")
    if result.get("error") is None:
        from app.services.upload_scans_invoice_print import schedule_dispatch_pdfs_after_create_invoice

        schedule_dispatch_pdfs_after_create_invoice(
            did,
            req.subfolder,
            str(customer_dict.get("mobile_number") or customer_dict.get("mobile") or ""),
            list(result.get("pdfs_saved") or []),
        )
    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=scraped,
        pdfs_saved=result.get("pdfs_saved") or [],
        application_id=None,
        rto_fees=None,
        error=_normalize_automation_error(result.get("error")),
        customer_id=int(cc) if cc is not None else None,
        vehicle_id=int(vv) if vv is not None else None,
        warning=warn,
        dms_automation_mode=dms_mode,
        dms_milestones=list(result.get("dms_milestones") or []),
        dms_step_messages=list(result.get("dms_step_messages") or []),
        ready_for_client_create_invoice=result.get("ready_for_client_create_invoice"),
        hero_dms_form22_print=result.get("hero_dms_form22_print"),
    )


@router.get("/form20-status")
def form20_status(principal: Principal = Depends(get_principal)) -> dict:
    """Debug: check Form 20 template paths and fitz availability."""
    from pathlib import Path

    from app.config import FORM20_TEMPLATE_SINGLE, FORM20_TEMPLATE_FRONT, FORM20_TEMPLATE_BACK, FORM20_TEMPLATE_DOCX, GATE_PASS_TEMPLATE_DOCX, UPLOADS_DIR

    project_root = Path(get_uploads_dir(principal.dealer_id)).resolve().parent
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
async def print_form20(
    req: PrintForm20Request,
    principal: Principal = Depends(get_principal),
) -> PrintForm20Response:
    """Generate Form 20 (all pages) and save to Uploaded scans/subfolder. Called from Print forms button."""
    enforce_max_text_depth(req.model_dump())
    did = resolve_dealer_id(principal, req.dealer_id)
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
            dealer_id=did,
            uploads_dir=uploads_dir,
        )
        return PrintForm20Response(success=True, pdfs_saved=form20_saved)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("print_form20: Form 20 generation failed: %s", e)
        return PrintForm20Response(success=False, pdfs_saved=[], error=str(e))


@router.post("/print-gate-pass", response_model=PrintForm20Response)
async def print_gate_pass(
    req: PrintForm20Request,
    principal: Principal = Depends(get_principal),
) -> PrintForm20Response:
    """
    Generate ``Gate Pass.pdf`` from ``templates/word/Gate Pass Template.docx`` (or ``GATE_PASS_TEMPLATE_DOCX``),
    save under Uploaded scans, then schedule print/open per ``ENVIRONMENT`` (non-blocking).
    """
    enforce_max_text_depth(req.model_dump())
    did = resolve_dealer_id(principal, req.dealer_id)
    uploads_dir = Path(get_uploads_dir(did))
    if not uploads_dir.is_dir():
        raise HTTPException(status_code=500, detail="Uploads directory not found")
    customer_dict = req.customer.model_dump(exclude_none=True)
    if req.customer.mobile_number:
        customer_dict["mobile_number"] = req.customer.mobile_number
    if req.customer.mobile:
        customer_dict["mobile"] = req.customer.mobile
    vehicle_dict = dict(req.vehicle or {})
    if "key_no" in vehicle_dict and "key_num" not in vehicle_dict:
        vehicle_dict["key_num"] = vehicle_dict.get("key_no")
    if "frame_no" in vehicle_dict and "frame_num" not in vehicle_dict:
        vehicle_dict["frame_num"] = vehicle_dict.get("frame_no")
    if "engine_no" in vehicle_dict and "engine_num" not in vehicle_dict:
        vehicle_dict["engine_num"] = vehicle_dict.get("engine_no")
    try:
        from app.services.form20_service import generate_gate_pass_pdf_only
        from app.services.upload_scans_pdf_dispatch import schedule_dispatch_local_pdf

        pdf_path = generate_gate_pass_pdf_only(
            subfolder=req.subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            vehicle_id=req.vehicle_id,
            dealer_id=did,
            uploads_dir=uploads_dir,
        )
        schedule_dispatch_local_pdf(pdf_path)
        return PrintForm20Response(success=True, pdfs_saved=["Gate Pass.pdf"])
    except HTTPException:
        raise
    except FileNotFoundError as e:
        logger.warning("print_gate_pass: %s", e)
        return PrintForm20Response(success=False, pdfs_saved=[], error=str(e))
    except Exception as e:
        logger.warning("print_gate_pass: Gate Pass generation failed: %s", e)
        return PrintForm20Response(success=False, pdfs_saved=[], error=str(e))


@router.post("/insurance/hero", response_model=FillHeroInsuranceResponse)
async def fill_hero_insurance(
    req: FillHeroInsuranceRequest = FillHeroInsuranceRequest(),
    principal: Principal = Depends(get_principal),
) -> FillHeroInsuranceResponse:
    """
    Hero Insurance: ``pre_process`` (Sign In → 2W → New Policy → insurer / OVD / mobile / KYC **Proceed**
    or uploads) then ``main_process`` (VIN/chassis from ``form_insurance_view``, proposal defaults hardcoded).
    Requires ``customer_id`` and ``vehicle_id`` for the main stage. Optional ``staging_id`` merges
    ``add_sales_staging.payload_json`` (OCR merge) for insurer/nominee when the view has no ``insurance_master`` row yet.
    """
    enforce_max_text_depth(req.model_dump())
    url = (req.insurance_base_url or INSURANCE_BASE_URL or "").strip()
    if url:
        url = _require_absolute_http_url(url, "insurance_base_url")
    did = resolve_dealer_id(principal, req.dealer_id)
    ocr_dir = Path(get_ocr_output_dir(did))

    staging_payload = None
    sid = (req.staging_id or "").strip()
    if sid:
        try:
            UUID(sid)
        except ValueError:
            raise HTTPException(status_code=400, detail="staging_id must be a valid UUID") from None
        from app.repositories.add_sales_staging import fetch_staging_payload

        staging_payload = fetch_staging_payload(sid, did)
        if not staging_payload:
            raise HTTPException(
                status_code=404,
                detail="Staging not found, abandoned, or dealer_id does not match.",
            )

    def _hero_insurance_run() -> dict:
        pre = pre_process(
            insurance_base_url=url if url else None,
            customer_id=req.customer_id,
            vehicle_id=req.vehicle_id,
            subfolder=req.subfolder,
            ocr_output_dir=ocr_dir,
            staging_payload=staging_payload,
            dealer_id=did,
        )
        main = main_process(
            pre_result=pre,
            customer_id=req.customer_id,
            vehicle_id=req.vehicle_id,
            subfolder=req.subfolder,
            ocr_output_dir=ocr_dir,
            staging_payload=staging_payload,
            staging_id=sid if sid else None,
            dealer_id=did,
        )
        return post_process(pre_result=pre, main_result=main)

    result = await _run_playwright_work(_hero_insurance_run)
    if result.get("success") and (req.subfolder or "").strip():
        from app.services.upload_scans_invoice_print import schedule_dispatch_pdf_after_generate_insurance

        schedule_dispatch_pdf_after_generate_insurance(did, (req.subfolder or "").strip())
    return FillHeroInsuranceResponse(
        success=bool(result.get("success")),
        error=result.get("error"),
        page_url=result.get("page_url"),
        login_url=result.get("login_url"),
        match_base=result.get("match_base"),
    )


@router.post("", response_model=FillDmsResponse)
async def fill_dms(
    req: FillDmsRequest,
    principal: Principal = Depends(get_principal),
) -> FillDmsResponse:
    enforce_max_text_depth(req.model_dump())
    logger.info("fill_dms: start subfolder=%s dms=%s", req.subfolder, bool(req.dms_base_url))
    base_url = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base_url:
        logger.warning("fill_dms: dms_base_url missing")
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    base_url = _require_absolute_http_url(base_url, "dms_base_url")
    did = resolve_dealer_id(principal, req.dealer_id)
    uploads_dir = Path(get_uploads_dir(did))
    if not uploads_dir.is_dir():
        raise HTTPException(status_code=500, detail="Uploads directory not found")
    customer_dict = req.customer.model_dump(exclude_none=True)
    if req.customer.mobile_number:
        customer_dict["mobile_number"] = req.customer.mobile_number
    if req.customer.mobile:
        customer_dict["mobile"] = req.customer.mobile
    vehicle_dict = req.vehicle.model_dump(exclude_none=True)
    logger.info("fill_dms: calling run_fill_dms base_url=%s", base_url[:60] if base_url else None)
    staging_payload, cid, vid = _fill_dms_staging_or_ids(
        req.staging_id,
        did,
        req.customer_id,
        req.vehicle_id,
        customer_dict,
        vehicle_dict,
    )
    sid_for_commit = (req.staging_id or "").strip() or None
    result = await _run_playwright_work(
        partial(
            run_fill_dms,
            dms_base_url=base_url,
            subfolder=req.subfolder,
            customer=customer_dict,
            vehicle=vehicle_dict,
            login_user=DMS_LOGIN_USER,
            login_password=DMS_LOGIN_PASSWORD,
            uploads_dir=uploads_dir,
            ocr_output_dir=Path(get_ocr_output_dir(did)),
            dealer_id=did,
            customer_id=cid,
            vehicle_id=vid,
            staging_payload=staging_payload,
            staging_id=sid_for_commit,
        )
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

    warn, dms_mode = _dms_response_warning_and_mode(result)
    cc = result.get("committed_customer_id")
    vv = result.get("committed_vehicle_id")
    if result.get("error") is None:
        from app.services.upload_scans_invoice_print import schedule_dispatch_pdfs_after_create_invoice

        schedule_dispatch_pdfs_after_create_invoice(
            did,
            req.subfolder,
            str(customer_dict.get("mobile_number") or customer_dict.get("mobile") or ""),
            list(result.get("pdfs_saved") or []),
        )
    return FillDmsResponse(
        success=result.get("error") is None,
        vehicle=scraped,
        pdfs_saved=result.get("pdfs_saved") or [],
        application_id=result.get("application_id"),
        rto_fees=result.get("rto_fees"),
        error=_normalize_automation_error(result.get("error")),
        customer_id=int(cc) if cc is not None else None,
        vehicle_id=int(vv) if vv is not None else None,
        warning=warn,
        dms_automation_mode=dms_mode,
        dms_milestones=list(result.get("dms_milestones") or []),
        dms_step_messages=list(result.get("dms_step_messages") or []),
        ready_for_client_create_invoice=result.get("ready_for_client_create_invoice"),
        hero_dms_form22_print=result.get("hero_dms_form22_print"),
    )
