"""Add Sales helpers: Create Invoice / Generate Insurance eligibility (natural keys; no dealer filter)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import MAX_TEXT_CHARS
from app.db import get_connection
from app.repositories.add_sales_invoices import list_recent_sales_invoices
from app.repositories.add_sales_staging import fetch_staging_payload, list_in_process_staging_rows
from app.repositories.master_ref import (
    REF_TYPE_FINANCER,
    list_cpa_portals,
    list_portal_insurers,
    list_ref_values,
)
from app.schemas.add_sales_staging_patch import PatchAddSalesStagingPayloadRequest
from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.services.add_sales_staging_patch_service import patch_add_sales_staging_payload
from app.validation.text_limits import enforce_max_text_depth

router = APIRouter(prefix="/add-sales", tags=["add-sales"])


def _digits_mobile(mobile: str) -> int | None:
    digits = "".join(c for c in (mobile or "") if c.isdigit())
    if len(digits) >= 10:
        return int(digits[-10:])
    if len(digits) > 0:
        return int(digits)
    return None


def _has_cpa_insurance_master_row(cur: Any, customer_id: int, vehicle_id: int) -> bool:
    """True when ``insurance_master`` has a CPA row for this sale and current calendar year."""
    cur.execute(
        """
        SELECT im.insurance_id
        FROM insurance_master im
        WHERE im.customer_id = %s
          AND im.vehicle_id = %s
          AND im.insurance_year = %s
          AND im.insurance_type = 'CPA'
        LIMIT 1
        """,
        (int(customer_id), int(vehicle_id), date.today().year),
    )
    row = cur.fetchone()
    return row is not None and row.get("insurance_id") is not None


def _cpa_alliance_insurance_eligibility(*, has_cpa_row: bool, ids_resolved: bool) -> dict[str, object]:
    if not ids_resolved:
        return {
            "cpa_alliance_insurance_enabled": False,
            "cpa_alliance_insurance_reason": (
                "Customer and vehicle master IDs are required for CPA Insurance."
            ),
        }
    if has_cpa_row:
        return {
            "cpa_alliance_insurance_enabled": False,
            "cpa_alliance_insurance_reason": "CPA insurance is already recorded for this sale.",
        }
    return {
        "cpa_alliance_insurance_enabled": True,
        "cpa_alliance_insurance_reason": None,
    }


def _cpa_eligibility_extras(dealer_id: int | None) -> dict[str, object]:
    """
    Optional CPA Alliance portal context when ``dealer_id`` is provided on eligibility requests.

    ``cpa_alliance_portal_enabled``: dealer is not on Hero CPI MISP add-on (``hero_cpi`` not **Y**)
    and at least one CPA row with a URL exists in ``master_ref``.

    ``dealer_cpa_insurer`` / ``hero_cpi`` come from ``dealer_ref`` (Add Sales CPA Provider display when ``hero_cpi = 'N'``).
    """
    blank: dict[str, object] = {
        "cpa_insurers": None,
        "hero_cpi": None,
        "dealer_cpa_insurer": None,
        "cpa_alliance_portal_enabled": False,
        "portal_insurers": [],
        "financiers": [],
    }
    if dealer_id is None or int(dealer_id) < 1:
        return blank
    conn = get_connection()
    try:
        portals = list_cpa_portals(conn)
        portal_insurers = list_portal_insurers(conn)
        financiers = list_ref_values(conn, REF_TYPE_FINANCER)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hero_cpi, cpa_insurer FROM dealer_ref WHERE dealer_id = %s LIMIT 1",
                (int(dealer_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    hero = "N"
    dcpa: str | None = None
    if row:
        if isinstance(row, dict):
            hero_raw = row.get("hero_cpi")
            dcpa_raw = row.get("cpa_insurer")
        else:
            hero_raw = row[0] if row else None
            dcpa_raw = row[1] if row and len(row) > 1 else None
        hero = str(hero_raw or "N").strip().upper()[:1] or "N"
        if hero not in ("Y", "N"):
            hero = "N"
        if dcpa_raw is not None and str(dcpa_raw).strip():
            dcpa = str(dcpa_raw).strip()
    enabled = hero != "Y" and len(portals) > 0
    return {
        "cpa_insurers": portals,
        "hero_cpi": hero,
        "dealer_cpa_insurer": dcpa,
        "cpa_alliance_portal_enabled": enabled,
        "portal_insurers": portal_insurers,
        "financiers": financiers,
    }


def _serialize_in_process_row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    u = out.get("updated_at")
    if isinstance(u, datetime):
        out["updated_at"] = u.isoformat()
    return out


@router.get("/in-process")
def list_add_sales_in_process(
    dealer_id: int | None = Query(None, ge=1),
    days: int = Query(7, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Open Add Sales staging rows (no RTO queue) in the last ``days`` IST calendar days for this dealer."""
    did = resolve_dealer_id(principal, dealer_id)
    rows = list_in_process_staging_rows(dealer_id=did, days=days)
    ser = [_serialize_in_process_row(dict(x)) for x in rows]
    return {"count": len(ser), "rows": ser}


@router.get("/invoices")
def list_add_sales_invoices(
    dealer_id: int | None = Query(None, ge=1),
    days: int = Query(7, ge=1, le=365),
    mobile: str | None = Query(None, max_length=MAX_TEXT_CHARS, description="Customer mobile"),
    chassis: str | None = Query(None, max_length=MAX_TEXT_CHARS, description="Chassis / VIN partial or wildcard"),
    engine: str | None = Query(None, max_length=MAX_TEXT_CHARS, description="Engine partial or wildcard"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Committed ``sales_master`` rows for Add Sales Invoices tab (last ``days`` IST days on billing_date)."""
    did = resolve_dealer_id(principal, dealer_id)
    rows = list_recent_sales_invoices(
        dealer_id=did,
        days=days,
        mobile=mobile,
        chassis=chassis,
        engine=engine,
    )
    return {"count": len(rows), "rows": rows}


@router.get("/staging/{staging_id}/payload")
def get_add_sales_staging_payload(
    staging_id: str,
    dealer_id: int | None = Query(None, ge=1),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Full ``payload_json`` for a staging row (draft or committed) when dealer matches."""
    try:
        uuid.UUID((staging_id or "").strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="staging_id must be a UUID") from e
    did = resolve_dealer_id(principal, dealer_id)
    payload = fetch_staging_payload(staging_id.strip(), did)
    if not payload:
        raise HTTPException(status_code=404, detail="Staging not found or not accessible for this dealer.")
    return {"staging_id": staging_id.strip(), "payload_json": payload}


@router.patch("/staging/{staging_id}/payload")
def patch_add_sales_staging_payload_endpoint(
    staging_id: str,
    req: PatchAddSalesStagingPayloadRequest,
    dealer_id: int | None = Query(None, ge=1),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Merge operator edits into ``payload_json`` (In-process Sales Details whitelist)."""
    try:
        uuid.UUID((staging_id or "").strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="staging_id must be a UUID") from e
    enforce_max_text_depth(req.model_dump())
    did = resolve_dealer_id(principal, dealer_id)
    try:
        return patch_add_sales_staging_payload(
            staging_id=staging_id.strip(),
            dealer_id=did,
            req=req,
        )
    except ValueError as e:
        msg = str(e).strip() or "Update failed"
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg) from e


@router.get("/dealer-cpa-context")
def get_dealer_cpa_context(
    dealer_id: int = Query(
        ...,
        ge=1,
        description="``dealer_ref`` row for ``hero_cpi``, ``cpa_insurer``, and CPA portal list (no sale natural keys).",
    ),
) -> dict:
    """Hero CPI + dealer CPA + CPA portals for Add Sales section C — use as soon as ``dealer_id`` is known."""
    return _cpa_eligibility_extras(dealer_id)


def _eligibility_by_customer_vehicle_ids(customer_id: int, vehicle_id: int) -> dict:
    """
    Same rules as natural-key eligibility, but keyed by committed ``sales_master`` pair.
    Used after Create Invoice when chassis/engine strings from the UI may not match ``raw_*`` LIKE patterns.
    """
    conn = get_connection()
    srow = None
    ins_row = None
    has_cpa_row = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT invoice_number
                FROM sales_master
                WHERE customer_id = %s AND vehicle_id = %s
                LIMIT 1
                """,
                (customer_id, vehicle_id),
            )
            srow = cur.fetchone()
            has_cpa_row = _has_cpa_insurance_master_row(cur, customer_id, vehicle_id)

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
                          AND im.insurance_type = 'Main'
                          AND TRIM(COALESCE(im.policy_num, '')) <> ''
                        LIMIT 1
                        """,
                        (customer_id, vehicle_id),
                    )
                    ins_row = cur.fetchone()
    finally:
        conn.close()

    resolved_cid = int(customer_id)
    resolved_vid = int(vehicle_id)
    cpa_fields = _cpa_alliance_insurance_eligibility(has_cpa_row=has_cpa_row, ids_resolved=True)

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
            **cpa_fields,
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
        **cpa_fields,
    }


@router.get("/create-invoice-eligibility")
def get_create_invoice_eligibility(
    chassis_num: str | None = Query(
        default=None,
        max_length=MAX_TEXT_CHARS,
        description="Chassis / frame; matches vehicle_master.raw_frame_num (trimmed). Omit when using customer_id + vehicle_id.",
    ),
    engine_num: str | None = Query(
        default=None,
        max_length=MAX_TEXT_CHARS,
        description="Matches vehicle_master.raw_engine_num (trimmed). Omit when using customer_id + vehicle_id.",
    ),
    mobile: str | None = Query(
        default=None,
        max_length=MAX_TEXT_CHARS,
        description="Customer mobile; matches customer_master.mobile_number (10-digit int). Omit when using customer_id + vehicle_id.",
    ),
    customer_id: int | None = Query(
        None,
        ge=1,
        description="Optional: after Create Invoice, use with vehicle_id to avoid chassis/mobile mismatch with DB.",
    ),
    vehicle_id: int | None = Query(
        None,
        ge=1,
        description="Optional: use with customer_id for eligibility by committed master IDs.",
    ),
    dealer_id: int | None = Query(
        None,
        ge=1,
        description="Optional: when set, response includes CPA portal list and dealer hero_cpi / cpa_insurer for Add Sales UI.",
    ),
) -> dict:
    """
    Eligibility uses **vehicle** identity (``raw_frame_num`` + ``raw_engine_num``) and **customer**
    identity (``mobile_number``) only — **not** ``dealer_id``.

    **Create Invoice:** enabled when there is **no** ``sales_master`` row for the resolved
    ``(customer_id, vehicle_id)``, **or** the row exists but ``invoice_number`` is blank (bad commit).

    **Generate Insurance:** enabled only when a **``sales_master``** row exists, **and** an invoice is
    recorded (non-blank ``invoice_number``), **and** no ``insurance_master`` **Main** row for that pair has a
    non-empty ``policy_num``.

    **CPA Insurance:** enabled when resolved ``(customer_id, vehicle_id)`` exist and no ``insurance_master``
    row exists for that pair with ``insurance_type = 'CPA'`` and ``insurance_year`` equal to the current
    calendar year (same key as Alliance CPA commit).

    When **customer_id** and **vehicle_id** are both provided, eligibility is resolved from ``sales_master`` /
    ``insurance_master`` only (ignores chassis / engine / mobile). Prefer this after a successful Create Invoice
    response that includes committed IDs.

    When **dealer_id** is provided, the response also includes **CPA Alliance** portal metadata
    (``cpa_insurers``, ``hero_cpi``, ``dealer_cpa_insurer``, ``cpa_alliance_portal_enabled``).
    """
    cpa_x = _cpa_eligibility_extras(dealer_id)
    if customer_id is not None and vehicle_id is not None:
        return {**_eligibility_by_customer_vehicle_ids(int(customer_id), int(vehicle_id)), **cpa_x}

    ch = (chassis_num or "").strip()
    eng = (engine_num or "").strip()
    mob_i = _digits_mobile(mobile or "")
    if not ch or not eng:
        raise HTTPException(status_code=400, detail="chassis_num and engine_num are required after trim.")
    if mob_i is None:
        raise HTTPException(status_code=400, detail="mobile must contain digits for customer_master.mobile_number.")

    conn = get_connection()
    vrow = None
    crow = None
    srow = None
    ins_row = None
    has_cpa_row = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT vehicle_id
                FROM vehicle_master
                WHERE TRIM(COALESCE(raw_frame_num, '')) LIKE '%%' || %s
                  AND TRIM(COALESCE(raw_engine_num, '')) LIKE '%%' || %s
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

            if vrow and crow:
                vid = vrow["vehicle_id"]
                cid = crow["customer_id"]
                has_cpa_row = _has_cpa_insurance_master_row(cur, cid, vid)
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
                              AND im.insurance_type = 'Main'
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
            **_cpa_alliance_insurance_eligibility(has_cpa_row=False, ids_resolved=False),
            **cpa_x,
        }

    cpa_fields = _cpa_alliance_insurance_eligibility(has_cpa_row=has_cpa_row, ids_resolved=True)

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
            **cpa_fields,
            **cpa_x,
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
        **cpa_fields,
        **cpa_x,
    }
