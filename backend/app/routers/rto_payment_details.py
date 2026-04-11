"""RTO queue: insert, list, batch processing, and retry endpoints."""

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import DEALER_ID
from app.repositories import rto_payment_details as repo
from app.services.rto_otp_bridge import deliver_operator_otp
from app.services.rto_payment_service import get_dealer_batch_status, start_dealer_rto_batch

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


class RtoPaymentInsertPayload(BaseModel):
    sales_id: int | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    insurance_id: int | None = None
    customer_mobile: str | None = None
    rto_application_date: str | None = None
    rto_payment_amount: float | None = None
    status: str = "Queued"


class RtoBatchStartPayload(BaseModel):
    dealer_id: int | None = None
    limit: int = Field(default=7, ge=1, le=7)


class RtoOtpSubmitPayload(BaseModel):
    dealer_id: int | None = None
    rto_queue_id: int
    otp: str = Field(..., min_length=4, max_length=14)


def _serialize_row(row: dict) -> dict:
    d = dict(row)
    for key in ("rto_application_date",):
        if d.get(key):
            d[key] = d[key].strftime("%d-%m-%Y")
    for key in ("leased_until", "started_at", "uploaded_at", "finished_at", "created_at", "updated_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    return d


@router.post("")
def insert_rto_payment(payload: RtoPaymentInsertPayload) -> dict:
    """Insert one RTO queue row after Fill Forms completes DMS/Form 20 work."""
    sid = payload.sales_id
    if sid is None and payload.customer_id is not None and payload.vehicle_id is not None:
        sid = repo._get_sales_id(payload.customer_id, payload.vehicle_id)
    if sid is None:
        raise HTTPException(status_code=400, detail="sales_id required (or provide customer_id + vehicle_id)")
    app_date = _parse_date(payload.rto_application_date) if payload.rto_application_date else None
    try:
        queue_id = repo.insert(
            sales_id=sid,
            insurance_id=payload.insurance_id,
            customer_mobile=payload.customer_mobile,
            rto_application_date=app_date,
            rto_payment_amount=payload.rto_payment_amount,
            status=payload.status or "Queued",
        )
        return {"rto_queue_id": queue_id, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process-batch")
def process_rto_batch(payload: RtoBatchStartPayload) -> dict:
    """Start dealer-scoped batch processing."""
    did = payload.dealer_id if payload.dealer_id is not None else DEALER_ID
    result = start_dealer_rto_batch(
        dealer_id=did,
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


@router.post("/submit-operator-otp")
def submit_operator_otp(payload: RtoOtpSubmitPayload) -> dict:
    """Deliver operator-entered OTP to the in-progress Vahan fill (after Partial Save OTP popup)."""
    did = payload.dealer_id if payload.dealer_id is not None else DEALER_ID
    st = get_dealer_batch_status(did)
    if not st.get("otp_pending"):
        raise HTTPException(status_code=400, detail="No OTP request is pending for this dealer")
    want = st.get("otp_rto_queue_id")
    if want is None or int(want) != int(payload.rto_queue_id):
        raise HTTPException(
            status_code=400,
            detail=f"Active OTP request is for queue {want}, not {payload.rto_queue_id}",
        )
    ok = deliver_operator_otp(did, payload.rto_queue_id, payload.otp.strip())
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Could not accept OTP (automation may have moved on — enter OTP in Vahan if the popup is still open)",
        )
    return {"ok": True}


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


@router.post("/{rto_queue_id}/retry")
def retry_rto_queue_row(rto_queue_id: int) -> dict:
    """Retry one failed row by setting it back to Queued."""
    ok = repo.retry_failed_row(rto_queue_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Failed row not found for retry")
    return {"ok": True, "rto_queue_id": rto_queue_id, "status": "Queued"}
