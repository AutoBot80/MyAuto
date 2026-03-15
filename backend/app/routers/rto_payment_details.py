"""RTO payment details: insert and list for RTO Payments Pending page."""

from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.repositories import rto_payment_details as repo

router = APIRouter(prefix="/rto-payment-details", tags=["rto-payment-details"])


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
    application_id: str
    customer_id: int
    vehicle_id: int
    dealer_id: int | None = None
    name: str | None = None
    mobile: str | None = None
    chassis_num: str | None = None
    register_date: str  # dd-mm-yyyy
    rto_fees: float
    status: str = "Pending"
    pay_txn_id: str | None = None
    operator_id: str | None = None
    payment_date: str | None = None
    rto_status: str = "Registered"


@router.post("")
def insert_rto_payment(payload: RtoPaymentInsertPayload) -> dict:
    """Insert one RTO payment row (e.g. after Fill Forms completes Vahan step)."""
    reg_date = _parse_date(payload.register_date)
    if not reg_date:
        raise HTTPException(status_code=400, detail="register_date required (dd-mm-yyyy)")
    pay_date = _parse_date(payload.payment_date) if payload.payment_date else None
    if not payload.application_id or not payload.application_id.strip():
        raise HTTPException(status_code=400, detail="application_id required")
    try:
        app_id = repo.insert(
            application_id=payload.application_id.strip(),
            customer_id=payload.customer_id,
            vehicle_id=payload.vehicle_id,
            dealer_id=payload.dealer_id,
            name=payload.name,
            mobile=payload.mobile,
            chassis_num=payload.chassis_num,
            register_date=reg_date,
            rto_fees=payload.rto_fees,
            status=payload.status or "Pending",
            pay_txn_id=payload.pay_txn_id,
            operator_id=payload.operator_id,
            payment_date=pay_date,
            rto_status=payload.rto_status or "Registered",
        )
        return {"application_id": app_id, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_rto_payments() -> list[dict]:
    """List all RTO payment details for RTO Payments Pending table."""
    rows = repo.list_all()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("register_date"):
            d["register_date"] = d["register_date"].strftime("%d-%m-%Y")
        if d.get("payment_date"):
            d["payment_date"] = d["payment_date"].strftime("%d-%m-%Y")
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
