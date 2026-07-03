"""Operator PATCH for In-process Add Sales staging ``payload_json`` (whitelist merge)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_connection
from app.repositories.add_sales_staging import (
    _load_payload_json_for_update_on_cursor,
    _normalize_cpi_reqd_flag,
    merge_staging_payload_on_cursor,
    normalize_staging_natural_key_mobile,
)
from app.repositories.insurance_addon_ref import (
    fetch_dealer_prefer_insurer_on_cursor,
    get_by_id,
)
from app.schemas.add_sales_staging_patch import PatchAddSalesStagingPayloadRequest
from app.services.customer_address_infer import (
    enrich_customer_address_from_freeform,
    normalize_operator_freeform_address,
    validate_operator_freeform_address,
)
from app.services.dms_relation_prefix import compute_dms_relation_prefix
from app.services.sale_folder_rename_service import (
    compute_new_subfolder_leaf,
    patch_ocr_to_be_used_json,
    rename_sale_folders_for_mobile_change,
)
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


def _int_or_none(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    s = str(val).strip()
    if s.isdigit():
        n = int(s)
        return n if n > 0 else None
    return None


def _existing_customer_mobile_int(existing: dict[str, Any]) -> int | None:
    cust = existing.get("customer")
    if not isinstance(cust, dict):
        return None
    return _int_or_none(cust.get("mobile_number") or cust.get("mobile"))


def _existing_alt_phone(existing: dict[str, Any]) -> str:
    cust = existing.get("customer")
    if not isinstance(cust, dict):
        return ""
    return str(
        cust.get("alt_phone_num")
        or cust.get("alternate_no")
        or cust.get("alternate_mobile_number")
        or ""
    ).strip()


def _normalize_patch_alt_phone(val: Any, *, fields_set: bool) -> tuple[str | None, bool]:
    """Return (normalized alt, alt_clear). Raises ValueError on invalid non-empty alt."""
    if not fields_set:
        return None, False
    if val is None:
        return None, True
    s = str(val).strip()
    if not s:
        return None, True
    key = normalize_staging_natural_key_mobile(s)
    if not key or len(key) != 10:
        raise ValueError("Alternate number must be exactly 10 digits when provided.")
    return key, False


def _fetch_dms_state_on_cursor(cur, *, staging_id: str, dealer_id: int) -> int:
    cur.execute(
        """
        SELECT COALESCE(dms_state, 0) AS dms_state
        FROM add_sales_staging
        WHERE staging_id = %s::uuid AND dealer_id = %s
          AND status IN ('draft', 'committed')
        """,
        (staging_id, int(dealer_id)),
    )
    row = cur.fetchone()
    if not row:
        return 0
    raw = row["dms_state"] if isinstance(row, dict) else row[0]
    return int(raw or 0)


def _assert_phone_edit_allowed(existing: dict[str, Any], dms_state: int) -> None:
    if int(dms_state) >= 2:
        raise ValueError("Mobile cannot be changed after customer has been saved in DMS.")
    cid = existing.get("customer_id")
    if cid is not None and str(cid).strip() not in ("", "0"):
        raise ValueError("Mobile cannot be changed after customer master has been created.")


def _update_staging_subfolder_on_cursor(
    cur, *, staging_id: str, dealer_id: int, subfolder: str | None
) -> None:
    sf = (subfolder or "").strip() or None
    cur.execute(
        """
        UPDATE add_sales_staging
        SET subfolder = %s,
            updated_at = now()
        WHERE staging_id = %s::uuid AND dealer_id = %s
          AND status IN ('draft', 'committed')
        """,
        (sf, staging_id, int(dealer_id)),
    )


def _build_patch_from_request(req: PatchAddSalesStagingPayloadRequest) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if req.customer is not None:
        cust_patch: dict[str, Any] = {}
        fields_set = req.customer.model_fields_set
        if "care_of" in fields_set:
            cust_patch["care_of"] = _str_or_none(req.customer.care_of, 255)
        if "address" in fields_set:
            cust_patch["address"] = _str_or_none(req.customer.address)
        if "mobile_number" in fields_set:
            mob = req.customer.mobile_number
            if mob is None:
                raise ValueError("Mobile number is required.")
            cust_patch["mobile_number"] = int(mob)
        if "alt_phone_num" in fields_set:
            alt_norm, alt_clear = _normalize_patch_alt_phone(
                req.customer.alt_phone_num, fields_set=True
            )
            if alt_clear:
                cust_patch["alt_phone_num"] = None
            else:
                cust_patch["alt_phone_num"] = alt_norm
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


def _fetch_insurance_state_on_cursor(cur, *, staging_id: str, dealer_id: int) -> int | None:
    cur.execute(
        """
        SELECT insurance_state
        FROM add_sales_staging
        WHERE staging_id = %s::uuid AND dealer_id = %s
          AND status IN ('draft', 'committed')
        """,
        (staging_id, int(dealer_id)),
    )
    row = cur.fetchone()
    if not row:
        return None
    raw = row["insurance_state"] if isinstance(row, dict) else row[0]
    if raw is None:
        return None
    return int(raw)


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
    cpi_patch = req.cpi_reqd if "cpi_reqd" in req.model_fields_set else None
    addon_patch = req.insurance_addon if "insurance_addon" in req.model_fields_set else None
    if not patch and cpi_patch is None and addon_patch is None:
        raise ValueError("At least one editable field is required")

    cust_patch = patch.get("customer")
    if isinstance(cust_patch, dict) and cust_patch.get("address") is not None:
        raw_addr = str(cust_patch.get("address") or "")
        addr_err = validate_operator_freeform_address(raw_addr)
        if addr_err:
            raise ValueError(addr_err)
        normed = normalize_operator_freeform_address(raw_addr)
        if not normed:
            raise ValueError(addr_err or "Address could not be normalized.")
        cust_patch["address"] = normed["address"]
        cust_patch["city"] = normed["city"]
        cust_patch["state"] = normed["state"]
        cust_patch["pin"] = normed["pin"]
        cust_patch["pin_code"] = normed["pin_code"]

    new_file_location: str | None = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            existing = _load_payload_json_for_update_on_cursor(
                cur, staging_id=sid, dealer_id=int(dealer_id)
            )
            if existing is None:
                raise ValueError("Staging not found or not accessible for this dealer.")

            dms_state = _fetch_dms_state_on_cursor(cur, staging_id=sid, dealer_id=int(dealer_id))
            cust_patch = patch.get("customer")
            phone_fields_requested = (
                isinstance(req.customer, object)
                and req.customer is not None
                and (
                    "mobile_number" in req.customer.model_fields_set
                    or "alt_phone_num" in req.customer.model_fields_set
                )
            )
            if phone_fields_requested:
                _assert_phone_edit_allowed(existing, dms_state)

            new_file_location = None
            if isinstance(cust_patch, dict) and "mobile_number" in cust_patch:
                new_mob = int(cust_patch["mobile_number"])
                old_mob = _existing_customer_mobile_int(existing)
                alt_ocr_val: str | None = None
                alt_ocr_clear = False
                if "alt_phone_num" in cust_patch:
                    alt_ocr_val, alt_ocr_clear = _normalize_patch_alt_phone(
                        cust_patch.get("alt_phone_num"),
                        fields_set=True,
                    )
                if old_mob != new_mob:
                    old_leaf = (existing.get("file_location") or "").strip()
                    if old_leaf:
                        new_leaf = compute_new_subfolder_leaf(old_leaf, new_mob)
                        rename_sale_folders_for_mobile_change(
                            int(dealer_id),
                            old_leaf,
                            new_leaf,
                            mobile_number=new_mob,
                            alt_phone_num=alt_ocr_val,
                            alt_clear=alt_ocr_clear,
                        )
                        new_file_location = new_leaf
                        patch["file_location"] = new_leaf
                        cust_patch["file_location"] = new_leaf
                    elif "alt_phone_num" in cust_patch:
                        old_leaf = (existing.get("file_location") or "").strip()
                        if old_leaf:
                            from app.config import get_ocr_output_dir
                            from app.services.ocr_sale_artifacts import _safe_subfolder_name

                            ocr_dir = get_ocr_output_dir(int(dealer_id)) / _safe_subfolder_name(old_leaf)
                            patch_ocr_to_be_used_json(
                                ocr_dir,
                                alt_phone_num=alt_ocr_val,
                                alt_clear=alt_ocr_clear,
                            )

            elif isinstance(cust_patch, dict) and "alt_phone_num" in cust_patch:
                alt_val, alt_clear = _normalize_patch_alt_phone(
                    cust_patch.get("alt_phone_num"),
                    fields_set=True,
                )
                old_leaf = (existing.get("file_location") or "").strip()
                if old_leaf:
                    from app.config import get_ocr_output_dir
                    from app.services.ocr_sale_artifacts import _safe_subfolder_name

                    ocr_dir = get_ocr_output_dir(int(dealer_id)) / _safe_subfolder_name(old_leaf)
                    patch_ocr_to_be_used_json(
                        ocr_dir,
                        alt_phone_num=alt_val,
                        alt_clear=alt_clear,
                    )

            ins_patch = patch.get("insurance")
            if isinstance(ins_patch, dict) and "insurer" in ins_patch:
                ins_state = _fetch_insurance_state_on_cursor(
                    cur, staging_id=sid, dealer_id=int(dealer_id)
                )
                if ins_state is not None and ins_state != 0:
                    raise ValueError(
                        "Insurance provider cannot be changed after insurance processing has started."
                    )

            updated = False
            if patch:
                _enrich_customer_patch(patch, existing)
                if merge_staging_payload_on_cursor(cur, sid, int(dealer_id), patch) >= 1:
                    updated = True
                if new_file_location:
                    _update_staging_subfolder_on_cursor(
                        cur,
                        staging_id=sid,
                        dealer_id=int(dealer_id),
                        subfolder=new_file_location,
                    )
                    if int(cur.rowcount or 0) >= 1:
                        updated = True

            if cpi_patch is not None:
                cpi_val = _normalize_cpi_reqd_flag(cpi_patch)
                cur.execute(
                    """
                    UPDATE add_sales_staging
                    SET updated_at = now(),
                        cpi_reqd = %s
                    WHERE staging_id = %s::uuid AND dealer_id = %s
                      AND status IN ('draft', 'committed')
                    """,
                    (cpi_val, sid, int(dealer_id)),
                )
                if int(cur.rowcount or 0) >= 1:
                    updated = True

            if addon_patch is not None:
                prefer = fetch_dealer_prefer_insurer_on_cursor(cur, dealer_id=int(dealer_id))
                if not prefer:
                    raise ValueError("Dealer prefer_insurer is not set; cannot assign insurance add-on preset.")
                addon_row = get_by_id(conn, int(addon_patch))
                if not addon_row:
                    raise ValueError("insurance_addon id not found.")
                if str(addon_row.get("insurer") or "") != prefer:
                    raise ValueError(
                        "insurance_addon must belong to the same insurer as dealer prefer_insurer."
                    )
                cur.execute(
                    """
                    UPDATE add_sales_staging
                    SET updated_at = now(),
                        insurance_addon = %s
                    WHERE staging_id = %s::uuid AND dealer_id = %s
                      AND status IN ('draft', 'committed')
                    """,
                    (int(addon_patch), sid, int(dealer_id)),
                )
                if int(cur.rowcount or 0) >= 1:
                    updated = True

            if not updated:
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

    return {
        "ok": True,
        "staging_id": sid,
        "updated_at": updated_at,
        "file_location": new_file_location,
    }
