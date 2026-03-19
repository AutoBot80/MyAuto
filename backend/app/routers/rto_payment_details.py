"""RTO queue: insert, list, and optionally pay queued RTO rows."""

import asyncio
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import DEALER_ID, VAHAN_BASE_URL, get_uploads_dir
from app.repositories import rto_payment_details as repo
from app.repositories import rc_status_sms_queue as rc_sms_repo
from app.services.rto_payment_service import get_dealer_batch_status, run_rto_pay, start_dealer_rto_batch

router = APIRouter(prefix="/rto-queue", tags=["rto-queue"])


def _parse_date(s: str | None) -> date | None:
    if not s or not s.strip():
        return None
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _ensure_absolute_url(url: str, fallback: str = "http://127.0.0.1:8000") -> str:
    if not url or not url.strip():
        return url
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return f"{fallback.rstrip('/')}{url}"
    return url


class RtoPaymentInsertPayload(BaseModel):
    application_id: str | None = None
    customer_id: int
    vehicle_id: int
    dealer_id: int | None = None
    name: str | None = None
    mobile: str | None = None
    chassis_num: str | None = None
    register_date: str  # dd-mm-yyyy
    rto_fees: float
    status: str = "Queued"
    pay_txn_id: str | None = None
    operator_id: str | None = None
    payment_date: str | None = None
    rto_status: str = "Pending"
    subfolder: str | None = None


class RtoBatchStartPayload(BaseModel):
    dealer_id: int | None = None
    operator_id: str | None = None
    limit: int = Field(default=7, ge=1, le=7)
    vahan_base_url: str | None = None


def _serialize_row(row: dict) -> dict:
    d = dict(row)
    d["queue_id"] = d.get("application_id")
    for key in ("register_date", "payment_date"):
        if d.get(key):
            d[key] = d[key].strftime("%d-%m-%Y")
    for key in ("leased_until", "started_at", "uploaded_at", "finished_at", "created_at", "updated_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    return d


@router.post("")
def insert_rto_payment(payload: RtoPaymentInsertPayload) -> dict:
    """Insert one RTO queue row after Fill Forms completes DMS/Form 20 work."""
    reg_date = _parse_date(payload.register_date)
    if not reg_date:
        raise HTTPException(status_code=400, detail="register_date required (dd-mm-yyyy)")
    pay_date = _parse_date(payload.payment_date) if payload.payment_date else None
    try:
        queue_id = repo.insert(
            application_id=(payload.application_id or "").strip() or None,
            customer_id=payload.customer_id,
            vehicle_id=payload.vehicle_id,
            dealer_id=payload.dealer_id,
            name=payload.name,
            mobile=payload.mobile,
            chassis_num=payload.chassis_num,
            register_date=reg_date,
            rto_fees=payload.rto_fees,
            status=payload.status or "Queued",
            pay_txn_id=payload.pay_txn_id,
            operator_id=payload.operator_id,
            payment_date=pay_date,
            rto_status=payload.rto_status or "Pending",
            subfolder=payload.subfolder,
        )
        return {"queue_id": queue_id, "application_id": queue_id, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process-batch")
def process_rto_batch(payload: RtoBatchStartPayload) -> dict:
    """Start dealer-scoped Vahan batch processing up to the upload/cart step."""
    did = payload.dealer_id if payload.dealer_id is not None else DEALER_ID
    vahan_url = _ensure_absolute_url((payload.vahan_base_url or VAHAN_BASE_URL or "").strip())
    if not vahan_url:
        raise HTTPException(status_code=400, detail="vahan_base_url required (set VAHAN_BASE_URL in backend/.env)")
    result = start_dealer_rto_batch(
        dealer_id=did,
        operator_id=(payload.operator_id or "").strip() or None,
        vahan_base_url=vahan_url,
        limit=payload.limit,
    )
    if not result.get("started"):
        raise HTTPException(status_code=409, detail=result.get("message") or "Dealer batch already running")
    return result


@router.get("/process-batch/status")
def get_process_batch_status(
    dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted"),
) -> dict:
    """Return the latest dealer batch progress snapshot."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    return get_dealer_batch_status(did)


class RtoPayRequest(BaseModel):
    vahan_base_url: str | None = None


@router.post("/{application_id}/pay")
async def pay_rto(application_id: str, body: RtoPayRequest | None = None) -> dict:
    """Run Playwright payment flow for a queued row that already has a Vahan application id."""
    row = repo.get_by_application_id(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    if row.get("status") == "Paid":
        return {"ok": True, "application_id": application_id, "pay_txn_id": row.get("pay_txn_id"), "message": "Already paid"}

    vahan_url = _ensure_absolute_url((body.vahan_base_url if body else None) or VAHAN_BASE_URL or "")
    if not vahan_url:
        raise HTTPException(status_code=400, detail="vahan_base_url required")

    rto_dealer_id = "RTO" + str(row.get("dealer_id") or DEALER_ID)
    uploads_dir = get_uploads_dir(row.get("dealer_id") or DEALER_ID)
    loop = asyncio.get_event_loop()
    pay_result = await loop.run_in_executor(
        None,
        lambda: run_rto_pay(
            application_id=row.get("vahan_application_id") or application_id,
            chassis_num=row.get("chassis_num"),
            subfolder=row.get("subfolder"),
            vahan_base_url=vahan_url,
            rto_dealer_id=rto_dealer_id,
            customer_name=row.get("name"),
            rto_fees=float(row.get("rto_fees") or 200),
            uploads_dir=Path(uploads_dir),
        ),
    )

    if not pay_result.get("success"):
        raise HTTPException(status_code=500, detail=pay_result.get("error") or "Pay failed")

    pay_txn_id = pay_result.get("pay_txn_id")
    if not pay_txn_id:
        raise HTTPException(status_code=500, detail="Could not capture TC number")

    today = date.today()
    repo.update_payment(application_id=application_id, pay_txn_id=pay_txn_id, payment_date=today, status="Paid")
    try:
        rc_sms_repo.insert(
            sales_id=row["sales_id"],
            dealer_id=row.get("dealer_id"),
            vehicle_id=row["vehicle_id"],
            customer_id=row["customer_id"],
            customer_mobile=row.get("mobile"),
            message_type="RC File Submitted",
            sms_status="Pending",
        )
    except Exception:
        pass
    return {"ok": True, "application_id": application_id, "pay_txn_id": pay_txn_id, "status": "Paid"}


@router.get("/by-sale")
def get_rto_payment_by_sale(customer_id: int, vehicle_id: int) -> dict | None:
    """Get RTO queue row for a sale (customer_id, vehicle_id)."""
    row = repo.get_by_customer_vehicle(customer_id, vehicle_id)
    if not row:
        return None
    return _serialize_row(row)


@router.get("")
def list_rto_payments(dealer_id: int | None = Query(None, description="Dealer ID; uses app default if omitted")) -> list[dict]:
    """List RTO queue rows, filtered by dealer."""
    did = dealer_id if dealer_id is not None else DEALER_ID
    rows = repo.list_all(dealer_id=did)
    return [_serialize_row(r) for r in rows]


@router.post("/{application_id}/retry")
def retry_rto_queue_row(application_id: str) -> dict:
    """Retry one failed row by setting it back to Queued."""
    ok = repo.retry_failed_row(application_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Failed row not found for retry")
    return {"ok": True, "application_id": application_id, "status": "Queued"}
