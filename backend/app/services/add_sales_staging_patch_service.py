"""Operator PATCH for In-process Add Sales staging ``payload_json`` (whitelist merge)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_connection
from app.repositories.add_sales_staging import (
    _load_payload_json_for_update_on_cursor,
    merge_staging_payload_on_cursor,
)
from app.schemas.add_sales_staging_patch import PatchAddSalesStagingPayloadRequest
from app.services.customer_address_infer import enrich_customer_address_from_freeform
from app.services.dms_relation_prefix import compute_dms_relation_prefix
from app.services.utility_functions import (
    normalize_nominee_relationship_value,
    sanitize_details_sheet_insurer_value,
)


def _str_or_none(val: Any, max_len: int | None = None) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if max_len is not None and len(s) > max_len:
        return s[:max_len]
    return s


def _build_patch_from_request(req: PatchAddSalesStagingPayloadRequest) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if req.customer is not None:
        cust_patch: dict[str, Any] = {}
        fields_set = req.customer.model_fields_set
        if "care_of" in fields_set:
            cust_patch["care_of"] = _str_or_none(req.customer.care_of, 255)
        if "address" in fields_set:
            cust_patch["address"] = _str_or_none(req.customer.address)
        if cust_patch:
            patch["customer"] = cust_patch
    if req.vehicle is not None:
        veh_patch: dict[str, Any] = {}
        for key, max_len in (
            ("frame_no", 64),
            ("engine_no", 64),
            ("key_no", 32),
            ("battery_no", 64),
        ):
            if key in req.vehicle.model_fields_set:
                veh_patch[key] = _str_or_none(getattr(req.vehicle, key), max_len)
        if veh_patch:
            patch["vehicle"] = veh_patch
    if req.insurance is not None:
        ins_patch: dict[str, Any] = {}
        if "insurer" in req.insurance.model_fields_set:
            raw_ins = req.insurance.insurer
            if raw_ins is None:
                ins_patch["insurer"] = None
            else:
                ok = sanitize_details_sheet_insurer_value(_str_or_none(raw_ins, 255))
                ins_patch["insurer"] = ok
        if "nominee_name" in req.insurance.model_fields_set:
            ins_patch["nominee_name"] = _str_or_none(req.insurance.nominee_name)
        if "nominee_relationship" in req.insurance.model_fields_set:
            ins_patch["nominee_relationship"] = _str_or_none(
                normalize_nominee_relationship_value(req.insurance.nominee_relationship), 64
            )
        if ins_patch:
            patch["insurance"] = ins_patch
    return patch


def _enrich_customer_patch(
    patch: dict[str, Any], existing: dict[str, Any]
) -> None:
    cust_patch = patch.get("customer")
    if not isinstance(cust_patch, dict):
        return
    base_cust = existing.get("customer")
    merged_cust = dict(base_cust) if isinstance(base_cust, dict) else {}
    merged_cust.update(cust_patch)
    if cust_patch.get("address") is not None:
        enriched = enrich_customer_address_from_freeform(dict(merged_cust))
        for key in ("address", "city", "state", "pin", "pin_code"):
            if key in enriched and enriched[key] is not None:
                cust_patch[key] = enriched[key]
    if cust_patch.get("care_of") is not None:
        rel = compute_dms_relation_prefix(
            care_of=cust_patch.get("care_of"),
            address=str(merged_cust.get("address") or ""),
            gender=str(merged_cust.get("gender") or ""),
        )
        if rel:
            cust_patch["dms_relation_prefix"] = _str_or_none(rel, 8) or rel


def patch_add_sales_staging_payload(
    *,
    staging_id: str,
    dealer_id: int,
    req: PatchAddSalesStagingPayloadRequest,
) -> dict[str, Any]:
    """
    Deep-merge whitelisted fields into ``add_sales_staging.payload_json``.
    Returns ``{ ok, staging_id, updated_at }`` or raises ``ValueError`` with a user message.
    """
    sid = (staging_id or "").strip()
    if not sid:
        raise ValueError("staging_id is required")
    patch = _build_patch_from_request(req)
    if not patch:
        raise ValueError("At least one editable field is required")

    with get_connection() as conn:
        with conn.cursor() as cur:
            existing = _load_payload_json_for_update_on_cursor(
                cur, staging_id=sid, dealer_id=int(dealer_id)
            )
            if existing is None:
                raise ValueError("Staging not found or not accessible for this dealer.")
            _enrich_customer_patch(patch, existing)
            rows = merge_staging_payload_on_cursor(
                cur, sid, int(dealer_id), patch
            )
            if rows < 1:
                raise ValueError(
                    "Staging row could not be updated (missing, wrong dealer, or abandoned)."
                )
            cur.execute(
                """
                SELECT updated_at
                FROM add_sales_staging
                WHERE staging_id = %s::uuid AND dealer_id = %s
                """,
                (sid, int(dealer_id)),
            )
            row = cur.fetchone()
        conn.commit()

    updated_at: str | None = None
    if row:
        u = row.get("updated_at") if isinstance(row, dict) else row[0]
        if isinstance(u, datetime):
            updated_at = u.isoformat()
        elif u is not None:
            updated_at = str(u)

    return {"ok": True, "staging_id": sid, "updated_at": updated_at}
