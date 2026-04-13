"""Subdealer challan: OCR parse Daily Delivery Report uploads + staging / DMS batch."""

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from psycopg2 import errors as pg_errors
from pydantic import BaseModel, Field

from app.config import DMS_BASE_URL, UPLOAD_MAX_FILE_BYTES
from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.repositories import challan_details_staging as detail_repo
from app.repositories import challan_master_staging as master_repo
from app.services.add_subdealer_challan_service import (
    create_challan_staging_batch,
    retry_failed_staging_row,
    retry_order_only_batch,
    run_subdealer_challan_batch,
)
from app.services.subdealer_challan_ocr_service import dedupe_raw_challan_lines, parse_subdealer_challan
from app.services.upload_file_validation import read_upload_capped, validate_magic_jpeg_png_or_pdf
from app.validation.text_limits import enforce_max_text_depth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subdealer-challan", tags=["subdealer-challan"])

# Returned as HTTP 503 detail when DDL 23/24 was not applied (relation does not exist).
CHALLAN_STAGING_SCHEMA_HINT = (
    "Subdealer challan tables are missing. Apply, in order: DDL/23_challan_master_staging.sql, "
    "DDL/24_challan_details_staging.sql (requires dealer_ref and vehicle_inventory_master). "
    "See Documentation/Database DDL.md and DDL/README.md."
)


class ChallanLineIn(BaseModel):
    raw_engine: str | None = None
    raw_chassis: str | None = None


class CreateChallanStagingRequest(BaseModel):
    from_dealer_id: int = Field(..., description="Logged-in / source dealer")
    to_dealer_id: int = Field(..., description="Subdealer receiving stock")
    challan_date: str | None = Field(None, description="dd/mm/yyyy when known")
    challan_book_num: str | None = None
    lines: list[ChallanLineIn] = Field(default_factory=list)


class CreateChallanStagingResponse(BaseModel):
    challan_batch_id: str
    ok: bool = True
    dropped_existing_same_book_date: int = Field(
        0,
        description=(
            "Lines removed because the same engine/chassis already exists on a challan for this book+date "
            "(any status: Queued, Failed, Ready, Committed)."
        ),
    )
    dropped_duplicate_in_request: int = Field(
        0,
        description="Extra duplicate engine/chassis rows removed within this submission (first kept).",
    )


class ProcessChallanRequest(BaseModel):
    dms_base_url: str | None = Field(None, description="Defaults to server DMS_BASE_URL")
    dealer_id: int | None = Field(None, description="Defaults to DEALER_ID")


@router.post("/staging", response_model=CreateChallanStagingResponse)
def create_staging(
    req: CreateChallanStagingRequest,
    principal: Principal = Depends(get_principal),
) -> CreateChallanStagingResponse:
    """
    Insert ``challan_master_staging`` + ``challan_details_staging`` rows (details **Queued**) for each non-empty line.

    ``from_dealer_id`` must match the logged-in dealer unless the user has an admin role.

    Same ``challan_book_num`` + ``challan_date`` as a previous upload is allowed: a **new** batch is created so DMS can
    receive a new order/invoice. Lines matching vehicles **already present** on any prior batch for that book+date
    (Queued / Failed / Ready / Committed) are dropped to avoid duplicate detail rows; duplicate rows within the
    request are deduped. If nothing remains, HTTP 400.
    """
    enforce_max_text_depth(req.model_dump())
    if not principal.admin and int(req.from_dealer_id) != int(principal.dealer_id):
        raise HTTPException(
            status_code=403,
            detail="from_dealer_id must match your logged-in dealer",
        )

    lines_in = [ln.model_dump() for ln in req.lines]
    lines = [
        {"raw_engine": (x.get("raw_engine") or "").strip(), "raw_chassis": (x.get("raw_chassis") or "").strip()}
        for x in lines_in
    ]
    lines = [x for x in lines if x["raw_engine"] or x["raw_chassis"]]
    if not lines:
        raise HTTPException(status_code=400, detail="At least one line with engine or chassis is required.")

    lines, dropped_dup_req = dedupe_raw_challan_lines(lines)

    book = (str(req.challan_book_num).strip() if req.challan_book_num is not None else "") or None
    date = (str(req.challan_date).strip() if req.challan_date is not None else "") or None

    dropped_existing = 0
    if book and date:
        existing_keys = detail_repo.fetch_existing_vehicle_keys_for_dealer_book_date(
            int(req.from_dealer_id),
            book,
            date,
        )
        if existing_keys:
            kept: list[dict] = []
            for x in lines:
                ek = ((x.get("raw_engine") or "").strip().upper(), (x.get("raw_chassis") or "").strip().upper())
                if ek in existing_keys:
                    dropped_existing += 1
                    continue
                kept.append(x)
            lines = kept

    if not lines:
        if dropped_existing > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "All vehicles in this submission already appear on a challan for this book number and date "
                    "(including queued or failed lines). Add only vehicles that are not already listed, or open the "
                    "Processed tab to continue or retry the existing batch."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail="No vehicle lines left after removing duplicates. Add at least one distinct engine/chassis line.",
        )

    try:
        bid = create_challan_staging_batch(
            from_dealer_id=req.from_dealer_id,
            to_dealer_id=req.to_dealer_id,
            challan_date=date,
            challan_book_num=book,
            lines=lines,
        )
    except pg_errors.UndefinedTable as e:
        logger.warning("subdealer_challan: missing schema: %s", e)
        raise HTTPException(status_code=503, detail=CHALLAN_STAGING_SCHEMA_HINT) from e
    return CreateChallanStagingResponse(
        challan_batch_id=str(bid),
        dropped_existing_same_book_date=dropped_existing,
        dropped_duplicate_in_request=dropped_dup_req,
    )


@router.post("/process/{challan_batch_id}")
def process_batch(
    challan_batch_id: str,
    principal: Principal = Depends(get_principal),
    req: ProcessChallanRequest = ProcessChallanRequest(),
) -> dict:
    """
    Run DMS automation for a batch: ``prepare_vehicle`` per line, discounts, one ``prepare_order``, DB commit.
    Long-running (same order of magnitude as Fill DMS); client should use an extended timeout.
    Returns ``ok: false`` and ``error`` on failure (HTTP 200 so the client always receives the body).
    """
    try:
        bid = uuid.UUID(challan_batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid challan_batch_id") from e
    enforce_max_text_depth(req.model_dump())
    did = resolve_dealer_id(principal, req.dealer_id)
    base = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    result = run_subdealer_challan_batch(challan_batch_id=bid, dms_base_url=base, dealer_id=did)
    return result


@router.get("/staging/recent")
def list_recent_staging(
    principal: Principal = Depends(get_principal),
    dealer_id: int | None = Query(None, description="from_dealer_id; uses token dealer if omitted"),
    days: int = Query(
        15,
        ge=1,
        le=365,
        description="Default list: last N days, batches with failed detail lines or failed invoice_status",
    ),
    challan_book_num: str | None = Query(
        None,
        description="Trimmed challan book number; when set, matches challan_book_num for this dealer (any age); ignores failed-only and days window",
    ),
) -> list[dict]:
    """``challan_master_staging`` rows with ``failed_lines`` and ``detail_lines`` for the Processed tab.

    ``detail_lines``: every vehicle line (Queued / Failed / Ready / Committed). ``failed_lines``: Failed only.
    Default: batches from the last *days* matching the attention filter (see ``list_masters_recent``).
    With ``challan_book_num``: batches matching that book number (``challan_book_num`` column), no date limit.
    """
    did = resolve_dealer_id(principal, dealer_id)
    book = (challan_book_num or "").strip() or None
    try:
        masters = master_repo.list_masters_recent(did, days=days, challan_book_num=book)
    except pg_errors.UndefinedTable as e:
        logger.warning("subdealer_challan: missing schema: %s", e)
        raise HTTPException(status_code=503, detail=CHALLAN_STAGING_SCHEMA_HINT) from e
    out: list[dict] = []
    for m in masters:
        row = dict(m)
        try:
            bid = uuid.UUID(str(row["challan_batch_id"]))
        except ValueError:
            row["failed_lines"] = []
            row["detail_lines"] = []
            out.append(row)
            continue
        row["failed_lines"] = detail_repo.fetch_failed_details_for_batch(bid)
        row["detail_lines"] = detail_repo.fetch_all_detail_lines_for_batch(bid)
        out.append(row)
    return out


@router.get("/staging/failed-count")
def staging_failed_count(
    principal: Principal = Depends(get_principal),
    dealer_id: int | None = Query(None),
    days: int = Query(15, ge=1, le=365),
) -> dict[str, int]:
    """Count of **master** batches in the default Processed window (same rows as ``GET …/staging/recent`` without ``challan_book_num``)."""
    did = resolve_dealer_id(principal, dealer_id)
    try:
        n = master_repo.count_masters_needing_attention_recent(did, days=days)
    except pg_errors.UndefinedTable as e:
        logger.warning("subdealer_challan: missing schema: %s", e)
        raise HTTPException(status_code=503, detail=CHALLAN_STAGING_SCHEMA_HINT) from e
    return {"failed": n}


@router.post("/staging/{challan_detail_staging_id}/retry")
def retry_staging_row_endpoint(
    challan_detail_staging_id: int,
    principal: Principal = Depends(get_principal),
    req: ProcessChallanRequest = ProcessChallanRequest(),
) -> dict:
    """Re-queue one Failed detail line and run prepare + order for the batch (long-running)."""
    enforce_max_text_depth(req.model_dump())
    did = resolve_dealer_id(principal, req.dealer_id)
    base = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    return retry_failed_staging_row(
        challan_staging_id=challan_detail_staging_id,
        dms_base_url=base,
        dealer_id=did,
    )


@router.post("/batch/{challan_batch_id}/retry-order")
def retry_order_endpoint(
    challan_batch_id: str,
    principal: Principal = Depends(get_principal),
    req: ProcessChallanRequest = ProcessChallanRequest(),
) -> dict:
    """Run create order / invoice phase only (all detail lines must be Ready)."""
    try:
        bid = uuid.UUID(challan_batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid challan_batch_id") from e
    enforce_max_text_depth(req.model_dump())
    did = resolve_dealer_id(principal, req.dealer_id)
    base = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    return retry_order_only_batch(challan_batch_id=bid, dms_base_url=base, dealer_id=did)


@router.post("/parse-scan")
async def parse_scan(
    file: UploadFile = File(..., description="Challan scan (JPEG/PNG/PDF, max 500 KB)"),
) -> dict:
    """
    Run Textract FORMS+TABLES, parse challan no / date / engine-chassis rows,
    write Raw_OCR.txt and OCR_To_be_Used.json under CHALLANS_DIR.
    """
    try:
        raw = await read_upload_capped(file, UPLOAD_MAX_FILE_BYTES)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        validate_magic_jpeg_png_or_pdf(raw, label="Challan scan")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result = parse_subdealer_challan(raw, write_artifacts=True)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result
