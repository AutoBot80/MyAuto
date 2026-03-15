"""RTO payment details: insert, list, and pay for RTO Payments Pending page."""

import asyncio
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import UPLOADS_DIR, VAHAN_BASE_URL
from app.repositories import rto_payment_details as repo
from app.repositories import rc_status_sms_queue as rc_sms_repo
from app.services.rto_payment_service import run_rto_pay

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
    subfolder: str | None = None


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
            subfolder=payload.subfolder,
        )
        return {"application_id": app_id, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RtoPayRequest(BaseModel):
    vahan_base_url: str | None = None


@router.post("/{application_id}/pay")
async def pay_rto(application_id: str, body: RtoPayRequest | None = None) -> dict:
    """Run Playwright: open Vahan search, fill Application ID and Chassis No., screenshot, Pay, update DB."""
    row = repo.get_by_application_id(application_id)
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    if row.get("status") == "Paid":
        return {"ok": True, "application_id": application_id, "pay_txn_id": row.get("pay_txn_id"), "message": "Already paid"}

    vahan_url = _ensure_absolute_url((body.vahan_base_url if body else None) or VAHAN_BASE_URL or "")
    if not vahan_url:
        raise HTTPException(status_code=400, detail="vahan_base_url required")

    rto_dealer_id = "RTO" + str(row.get("dealer_id") or 100001)
    loop = asyncio.get_event_loop()
    pay_result = await loop.run_in_executor(
        None,
        lambda: run_rto_pay(
            application_id=application_id,
            chassis_num=row.get("chassis_num"),
            subfolder=row.get("subfolder"),
            vahan_base_url=vahan_url,
            rto_dealer_id=rto_dealer_id,
            customer_name=row.get("name"),
            rto_fees=float(row.get("rto_fees") or 200),
            uploads_dir=Path(UPLOADS_DIR),
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
    """Get RTO payment row for a sale (customer_id, vehicle_id). Used to restore application_id/rto_fees on Add Sales page."""
    row = repo.get_by_customer_vehicle(customer_id, vehicle_id)
    if not row:
        return None
    d = dict(row)
    if d.get("register_date"):
        d["register_date"] = d["register_date"].strftime("%d-%m-%Y")
    if d.get("payment_date"):
        d["payment_date"] = d["payment_date"].strftime("%d-%m-%Y")
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    return d


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
