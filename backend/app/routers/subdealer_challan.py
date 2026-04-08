"""Subdealer challan: OCR parse Daily Delivery Report uploads + staging / DMS batch."""

import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.config import DMS_BASE_URL, DEALER_ID
from app.repositories import challan_details_staging as detail_repo
from app.repositories import challan_master_staging as master_repo
from app.services.add_subdealer_challan_service import (
    create_challan_staging_batch,
    retry_failed_staging_row,
    retry_order_only_batch,
    run_subdealer_challan_batch,
)
from app.services.subdealer_challan_ocr_service import parse_subdealer_challan

router = APIRouter(prefix="/subdealer-challan", tags=["subdealer-challan"])


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


class ProcessChallanRequest(BaseModel):
    dms_base_url: str | None = Field(None, description="Defaults to server DMS_BASE_URL")
    dealer_id: int | None = Field(None, description="Defaults to DEALER_ID")


@router.post("/staging", response_model=CreateChallanStagingResponse)
def create_staging(req: CreateChallanStagingRequest) -> CreateChallanStagingResponse:
    """
    Insert ``challan_master_staging`` + ``challan_details_staging`` rows (details **Queued**) for each non-empty line.
    """
    lines_in = [ln.model_dump() for ln in req.lines]
    lines = [
        {"raw_engine": (x.get("raw_engine") or "").strip(), "raw_chassis": (x.get("raw_chassis") or "").strip()}
        for x in lines_in
    ]
    lines = [x for x in lines if x["raw_engine"] or x["raw_chassis"]]
    if not lines:
        raise HTTPException(status_code=400, detail="At least one line with engine or chassis is required.")
    bid = create_challan_staging_batch(
        from_dealer_id=req.from_dealer_id,
        to_dealer_id=req.to_dealer_id,
        challan_date=req.challan_date,
        challan_book_num=req.challan_book_num,
        lines=lines,
    )
    return CreateChallanStagingResponse(challan_batch_id=str(bid))


@router.post("/process/{challan_batch_id}")
def process_batch(
    challan_batch_id: str,
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
    did = int(req.dealer_id) if req.dealer_id is not None else int(DEALER_ID)
    base = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    result = run_subdealer_challan_batch(challan_batch_id=bid, dms_base_url=base, dealer_id=did)
    return result


@router.get("/staging/recent")
def list_recent_staging(
    dealer_id: int | None = Query(None, description="from_dealer_id; defaults to server DEALER_ID"),
    days: int = Query(15, ge=1, le=365, description="Masters with created_at in the last N days"),
) -> list[dict]:
    """Recent ``challan_master_staging`` rows with ``failed_lines`` (detail rows in Failed) for the Processed tab."""
    did = int(dealer_id) if dealer_id is not None else int(DEALER_ID)
    masters = master_repo.list_masters_recent(did, days=days)
    out: list[dict] = []
    for m in masters:
        row = dict(m)
        try:
            bid = uuid.UUID(str(row["challan_batch_id"]))
        except ValueError:
            row["failed_lines"] = []
            out.append(row)
            continue
        row["failed_lines"] = detail_repo.fetch_failed_details_for_batch(bid)
        out.append(row)
    return out


@router.get("/staging/failed-count")
def staging_failed_count(
    dealer_id: int | None = Query(None),
    days: int = Query(15, ge=1, le=365),
) -> dict[str, int]:
    """Count of Failed **detail** lines in the window (badges)."""
    did = int(dealer_id) if dealer_id is not None else int(DEALER_ID)
    return {"failed": master_repo.count_failed_detail_lines_recent(did, days=days)}


@router.post("/staging/{challan_detail_staging_id}/retry")
def retry_staging_row_endpoint(
    challan_detail_staging_id: int,
    req: ProcessChallanRequest = ProcessChallanRequest(),
) -> dict:
    """Re-queue one Failed detail line and run prepare + order for the batch (long-running)."""
    did = int(req.dealer_id) if req.dealer_id is not None else int(DEALER_ID)
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
    req: ProcessChallanRequest = ProcessChallanRequest(),
) -> dict:
    """Run create order / invoice phase only (all detail lines must be Ready)."""
    try:
        bid = uuid.UUID(challan_batch_id.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid challan_batch_id") from e
    did = int(req.dealer_id) if req.dealer_id is not None else int(DEALER_ID)
    base = (req.dms_base_url or DMS_BASE_URL or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="dms_base_url required (or set DMS_BASE_URL)")
    return retry_order_only_batch(challan_batch_id=bid, dms_base_url=base, dealer_id=did)


@router.post("/parse-scan")
async def parse_scan(
    file: UploadFile = File(..., description="Challan scan (JPEG/PNG/PDF, max 5 MB)"),
) -> dict:
    """
    Run Textract FORMS+TABLES, parse challan no / date / engine-chassis rows,
    write Raw_OCR.txt and OCR_To_be_Used.json under CHALLANS_DIR.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    result = parse_subdealer_challan(raw, write_artifacts=True)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result
