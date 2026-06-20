"""Admin portal-only manual issue: insurer + policy number → staging resume for Print Policy / PDF."""

from __future__ import annotations

import json
from typing import Any

from app.db import get_connection
from app.repositories.add_sales_staging import (
    _load_payload_json_for_update_on_cursor,
    deep_merge_staging_payload,
    sales_id_from_staging_payload,
)
from app.repositories.master_ref import list_portal_insurers
from app.services.fill_hero_insurance_service import _normalize_policy_num_for_db


class InsuranceManuallyFilledError(ValueError):
    """Business rule violation for manual insurance fill."""


def _int_from_payload(val: Any) -> int | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    if isinstance(val, float) and val.is_integer():
        n = int(val)
        return n if n > 0 else None
    s = str(val).strip()
    return int(s) if s.isdigit() else None


def mark_insurance_manually_filled(
    *,
    staging_id: str,
    dealer_id: int,
    insurer: str,
    policy_num: str,
) -> dict[str, Any]:
    """
    Portal-only manual issue: operator filled policy on MISP without Generate Insurance.

    Requires ``insurance_state=0``, invoice recorded, and issued **policy_num**. Sets
    ``insurance_state=2`` and persists ``payload_json.insurance.policy_num`` for print resume.
    Does not INSERT ``insurance_master`` (that runs after PDF in GI flow).
    """
    sid = (staging_id or "").strip()
    did = int(dealer_id)
    ins = (insurer or "").strip()
    pn = _normalize_policy_num_for_db((policy_num or "").strip())
    if not ins:
        raise InsuranceManuallyFilledError("insurer is required")
    if not pn:
        raise InsuranceManuallyFilledError("policy_num is required")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            portal = list_portal_insurers(conn)
        if ins not in portal:
            raise InsuranceManuallyFilledError(
                f"insurer must be one of the portal insurers ({len(portal)} configured)"
            )
    finally:
        conn.close()

    conn = get_connection()
    result: dict[str, Any] = {
        "staging_id": sid,
        "dealer_id": did,
        "insurer": ins,
        "policy_num": pn,
        "insurance_state": 2,
        "insurance_master_deleted": 0,
    }
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT insurance_state
                    FROM add_sales_staging
                    WHERE staging_id = %s::uuid AND dealer_id = %s
                    FOR UPDATE
                    """,
                    (sid, did),
                )
                st_row = cur.fetchone()
                if st_row is None:
                    raise InsuranceManuallyFilledError("Staging row not found")
                ins_state_raw = (
                    st_row["insurance_state"] if isinstance(st_row, dict) else st_row[0]
                )
                try:
                    ins_state = int(ins_state_raw or 0)
                except (TypeError, ValueError):
                    ins_state = 0
                if ins_state != 0:
                    raise InsuranceManuallyFilledError(
                        "Portal manual issue requires insurance_state=0 "
                        "(automation already submitted — use Gen. Insurance from In-process)"
                    )

                existing = _load_payload_json_for_update_on_cursor(
                    cur, staging_id=sid, dealer_id=did
                )
                if existing is None:
                    raise InsuranceManuallyFilledError("Staging row not found")

                sales_id = sales_id_from_staging_payload(existing)
                customer_id = _int_from_payload(existing.get("customer_id"))
                vehicle_id = _int_from_payload(existing.get("vehicle_id"))

                if sales_id is not None:
                    cur.execute(
                        """
                        SELECT customer_id, vehicle_id, invoice_number
                        FROM sales_master
                        WHERE sales_id = %s
                        """,
                        (sales_id,),
                    )
                    sm = cur.fetchone()
                    if not sm:
                        raise InsuranceManuallyFilledError(
                            "sales_master row not found for staging sales_id"
                        )
                    customer_id = int(sm["customer_id"] if isinstance(sm, dict) else sm[0])
                    vehicle_id = int(sm["vehicle_id"] if isinstance(sm, dict) else sm[1])
                    inv = sm["invoice_number"] if isinstance(sm, dict) else sm[2]
                    inv_s = str(inv or "").strip()
                    if not inv_s:
                        raise InsuranceManuallyFilledError(
                            "Invoice must be recorded before portal manual issue"
                        )
                else:
                    raise InsuranceManuallyFilledError(
                        "Staging row has no sales_id; Create Invoice must complete first"
                    )

                cur.execute(
                    """
                    SELECT insurance_id, TRIM(COALESCE(policy_num, '')) AS policy_num
                    FROM insurance_master
                    WHERE customer_id = %s
                      AND vehicle_id = %s
                      AND insurance_type = 'Main'
                    LIMIT 1
                    """,
                    (customer_id, vehicle_id),
                )
                ins_row = cur.fetchone()
                if ins_row:
                    im_policy = (
                        ins_row["policy_num"]
                        if isinstance(ins_row, dict)
                        else ins_row[1]
                    )
                    if str(im_policy or "").strip():
                        raise InsuranceManuallyFilledError(
                            "A policy number is already stored in insurance_master; "
                            "use Gen. Insurance resume or Cancel Invoice"
                        )
                    ins_id = (
                        ins_row["insurance_id"]
                        if isinstance(ins_row, dict)
                        else ins_row[0]
                    )
                    cur.execute(
                        "DELETE FROM insurance_master WHERE insurance_id = %s",
                        (int(ins_id),),
                    )
                    result["insurance_master_deleted"] = int(cur.rowcount or 0)

                merged = deep_merge_staging_payload(
                    existing,
                    {"insurance": {"insurer": ins, "policy_num": pn}},
                )
                merged.pop("insurance_id", None)
                frag = json.dumps(merged, default=str)
                cur.execute(
                    """
                    UPDATE add_sales_staging
                    SET updated_at = now(),
                        insurance_state = 2,
                        payload_json = %s::jsonb
                    WHERE staging_id = %s::uuid AND dealer_id = %s
                    """,
                    (frag, sid, did),
                )
                if cur.rowcount != 1:
                    raise InsuranceManuallyFilledError("Staging payload update failed")
        return result
    finally:
        conn.close()
