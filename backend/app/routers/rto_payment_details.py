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
    customer_id: int
    name: str | None = None
    mobile: str | None = None
    chassis_num: str | None = None
    application_num: str
    submission_date: str  # dd-mm-yyyy
    rto_payment_due: float
    status: str = "Pending"
    pos_mgr_id: str | None = None
    txn_id: str | None = None
    payment_date: str | None = None  # dd-mm-yyyy when paid


@router.post("")
def insert_rto_payment(payload: RtoPaymentInsertPayload) -> dict:
    """Insert one RTO payment row (e.g. after Fill Forms completes Vahan step)."""
    sub_date = _parse_date(payload.submission_date)
    if not sub_date:
        raise HTTPException(status_code=400, detail="submission_date required (dd-mm-yyyy)")
    pay_date = _parse_date(payload.payment_date) if payload.payment_date else None
    try:
        id_ = repo.insert(
            customer_id=payload.customer_id,
            name=payload.name,
            mobile=payload.mobile,
            chassis_num=payload.chassis_num,
            application_num=payload.application_num,
            submission_date=sub_date,
            rto_payment_due=payload.rto_payment_due,
            status=payload.status or "Pending",
            pos_mgr_id=payload.pos_mgr_id,
            txn_id=payload.txn_id,
            payment_date=pay_date,
        )
        return {"id": id_, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_rto_payments() -> list[dict]:
    """List all RTO payment details for RTO Payments Pending table."""
    rows = repo.list_all()
    # Serialize dates for JSON
    out = []
    for r in rows:
        d = dict(r)
        if d.get("submission_date"):
            d["submission_date"] = d["submission_date"].strftime("%d-%m-%Y")
        if d.get("payment_date"):
            d["payment_date"] = d["payment_date"].strftime("%d-%m-%Y")
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
