"""Add Sales helpers: Create Invoice / Generate Insurance eligibility (natural keys; no dealer filter)."""

from fastapi import APIRouter, HTTPException, Query

from app.db import get_connection

router = APIRouter(prefix="/add-sales", tags=["add-sales"])


def _digits_mobile(mobile: str) -> int | None:
    digits = "".join(c for c in (mobile or "") if c.isdigit())
    if len(digits) >= 10:
        return int(digits[-10:])
    if len(digits) > 0:
        return int(digits)
    return None


@router.get("/create-invoice-eligibility")
def get_create_invoice_eligibility(
    chassis_num: str = Query(
        ...,
        min_length=1,
        description="Chassis / frame; matches vehicle_master.raw_frame_num (trimmed)",
    ),
    engine_num: str = Query(..., min_length=1, description="Matches vehicle_master.raw_engine_num (trimmed)"),
    mobile: str = Query(..., min_length=1, description="Customer mobile; matches customer_master.mobile_number (10-digit int)"),
) -> dict:
    """
    Eligibility uses **vehicle** identity (``raw_frame_num`` + ``raw_engine_num``) and **customer**
    identity (``mobile_number``) only — **not** ``dealer_id``.

    **Create Invoice:** enabled when there is **no** ``sales_master`` row for the resolved
    ``(customer_id, vehicle_id)``, **or** the row exists but ``invoice_number`` is blank (bad commit).

    **Generate Insurance:** enabled only when a **``sales_master``** row exists, **and** an invoice is
    recorded (non-blank ``invoice_number``), **and** no ``insurance_master`` row for that pair has a
    non-empty ``policy_num``.
    """
    ch = (chassis_num or "").strip()
    eng = (engine_num or "").strip()
    mob_i = _digits_mobile(mobile)
    if not ch or not eng:
        raise HTTPException(status_code=400, detail="chassis_num and engine_num are required after trim.")
    if mob_i is None:
        raise HTTPException(status_code=400, detail="mobile must contain digits for customer_master.mobile_number.")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT vehicle_id
                FROM vehicle_master
                WHERE TRIM(COALESCE(raw_frame_num, '')) = %s
                  AND TRIM(COALESCE(raw_engine_num, '')) = %s
                LIMIT 1
                """,
                (ch, eng),
            )
            vrow = cur.fetchone()

            cur.execute(
                "SELECT customer_id FROM customer_master WHERE mobile_number = %s LIMIT 1",
                (mob_i,),
            )
            crow = cur.fetchone()

            srow = None
            ins_row = None
            if vrow and crow:
                vid = vrow["vehicle_id"]
                cid = crow["customer_id"]
                cur.execute(
                    """
                    SELECT invoice_number
                    FROM sales_master
                    WHERE customer_id = %s AND vehicle_id = %s
                    LIMIT 1
                    """,
                    (cid, vid),
                )
                srow = cur.fetchone()

                if srow is not None:
                    inv_chk = (srow.get("invoice_number") or "")
                    inv_chk = str(inv_chk).strip() if inv_chk is not None else ""
                    has_inv = bool(inv_chk)
                    if has_inv:
                        cur.execute(
                            """
                            SELECT im.insurance_id
                            FROM insurance_master im
                            WHERE im.customer_id = %s
                              AND im.vehicle_id = %s
                              AND TRIM(COALESCE(im.policy_num, '')) <> ''
                            LIMIT 1
                            """,
                            (cid, vid),
                        )
                        ins_row = cur.fetchone()
    finally:
        conn.close()

    resolved_cid: int | None = None
    resolved_vid: int | None = None
    if vrow and crow:
        resolved_cid = int(crow["customer_id"])
        resolved_vid = int(vrow["vehicle_id"])

    if not vrow or not crow:
        return {
            "create_invoice_enabled": True,
            "matched_sales_row": False,
            "invoice_number": None,
            "reason": None,
            "invoice_recorded": False,
            "generate_insurance_enabled": False,
            "generate_insurance_reason": "Create Invoice before Generating Insurance",
            "resolved_customer_id": None,
            "resolved_vehicle_id": None,
        }

    if srow is None:
        return {
            "create_invoice_enabled": True,
            "matched_sales_row": False,
            "invoice_number": None,
            "reason": None,
            "invoice_recorded": False,
            "generate_insurance_enabled": False,
            "generate_insurance_reason": (
                "Record the sale with Create Invoice (DMS) first; Generate Insurance unlocks after a "
                "sales row and invoice exist."
            ),
            "resolved_customer_id": resolved_cid,
            "resolved_vehicle_id": resolved_vid,
        }

    inv_raw = srow["invoice_number"]
    inv = (inv_raw or "").strip() if inv_raw is not None else ""
    has_invoice = bool(inv)
    invoice_recorded = has_invoice
    create_invoice_enabled = not has_invoice
    reason = "Invoice already recorded for this sale." if has_invoice else None

    has_insurance_policy_num = ins_row is not None and ins_row.get("insurance_id") is not None
    generate_insurance_enabled = invoice_recorded and not has_insurance_policy_num
    gen_reason: str | None = None
    if not invoice_recorded:
        gen_reason = "Record the invoice in DMS first (Create Invoice); Generate Insurance unlocks after the invoice is saved."
    elif has_insurance_policy_num:
        gen_reason = "A policy number is already stored for this sale; Generate Insurance is not available."

    return {
        "create_invoice_enabled": create_invoice_enabled,
        "matched_sales_row": True,
        "invoice_number": inv or None,
        "reason": reason,
        "invoice_recorded": invoice_recorded,
        "generate_insurance_enabled": generate_insurance_enabled,
        "generate_insurance_reason": gen_reason,
        "resolved_customer_id": resolved_cid,
        "resolved_vehicle_id": resolved_vid,
    }
