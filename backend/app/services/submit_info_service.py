"""
Submit Info: validate and merge extracted data, persist **draft** ``add_sales_staging`` only.
Masters (``customer_master``, ``vehicle_master``, ``sales_master``, ``insurance_master``) are
committed after successful Create Invoice (DMS); see ``add_sales_commit_service``.
"""

from pathlib import Path
from typing import Any

from app.config import DEALER_ID
from app.db import get_connection
from app.repositories.add_sales_staging import persist_staging_for_submit
from app.services.customer_address_infer import enrich_customer_address_from_freeform
from app.services.dms_relation_prefix import compute_dms_relation_prefix


def _int_or_none(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _str_or_none(val: Any, max_len: int | None = None) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if max_len is not None and len(s) > max_len:
        return s[:max_len]
    return s


def _last4(aadhar_id: str | None) -> str | None:
    if not aadhar_id or not str(aadhar_id).strip():
        return None
    digits = "".join(c for c in str(aadhar_id) if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits or None


def submit_info(
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    insurance: dict[str, Any],
    dealer_id: int | None,
    file_location: str | None = None,
    staging_id: str | None = None,
) -> dict[str, Any]:
    """
    Validate, enrich address, INSERT/UPDATE draft ``add_sales_staging`` only.
    Returns ``{ "ok": true, "staging_id": "<uuid>" }``.
    """
    aadhar_last4 = _last4(customer.get("aadhar_id"))
    mobile = _int_or_none(customer.get("mobile_number"))
    if not aadhar_last4 or mobile is None:
        raise ValueError("Customer aadhar (last 4) and mobile_number are required")

    customer = enrich_customer_address_from_freeform(dict(customer))

    loc = _str_or_none(file_location) or _str_or_none(customer.get("file_location"))
    if loc:
        from app.config import get_ocr_output_dir
        from app.services.ocr_service import validate_name_match_for_subfolder

        ocr_dir = get_ocr_output_dir(dealer_id if dealer_id is not None else DEALER_ID)
        name_err = validate_name_match_for_subfolder(Path(ocr_dir).resolve(), loc)
        if name_err:
            raise ValueError(name_err)

    name = _str_or_none(customer.get("name")) or ""
    address = _str_or_none(customer.get("address"))
    alt_phone_num = _str_or_none(customer.get("alt_phone_num"), 16)
    pin = _str_or_none(customer.get("pin"), 6)
    city = _str_or_none(customer.get("city"))
    state = _str_or_none(customer.get("state"))
    gender = _str_or_none(customer.get("gender"), 8)
    date_of_birth = _str_or_none(customer.get("date_of_birth"), 20)
    profession = _str_or_none(customer.get("profession"), 16)
    financier = _str_or_none(customer.get("financier"), 255)
    marital_status = _str_or_none(customer.get("marital_status"), 32)
    rel_computed = compute_dms_relation_prefix(address or "", gender)
    dms_relation_prefix = _str_or_none(rel_computed, 8) or rel_computed
    care_of = _str_or_none(customer.get("care_of"), 255)
    dms_contact_path = _str_or_none(customer.get("dms_contact_path"), 16) or "found"
    if dms_contact_path.lower() not in ("found", "new_enquiry", "skip_find"):
        dms_contact_path = "found"

    frame_no = _str_or_none(vehicle.get("frame_no"), 64)
    engine_no = _str_or_none(vehicle.get("engine_no"), 64)
    key_no = _str_or_none(vehicle.get("key_no"), 32)
    battery_no = _str_or_none(vehicle.get("battery_no"), 64)

    nominee_name = _str_or_none(insurance.get("nominee_name"))
    nominee_age_raw = insurance.get("nominee_age")
    nominee_age = _int_or_none(nominee_age_raw)
    if nominee_age_raw is not None and str(nominee_age_raw).strip() and nominee_age is None:
        raise ValueError("Nominee Age must be a number (1–150)")
    if nominee_age is not None and (nominee_age < 1 or nominee_age > 150):
        raise ValueError("Nominee Age must be between 1 and 150")
    nominee_relationship = _str_or_none(insurance.get("nominee_relationship"), 64)
    nominee_gender = _str_or_none(insurance.get("nominee_gender"), 16)
    insurer = _str_or_none(insurance.get("insurer"), 255)
    policy_num = _str_or_none(insurance.get("policy_num"), 24)
    policy_from_str = _str_or_none(insurance.get("policy_from"), 20)
    policy_to_str = _str_or_none(insurance.get("policy_to"), 20)
    premium_val = insurance.get("premium")
    premium = None
    if premium_val is not None:
        try:
            premium = float(str(premium_val).replace(",", "").strip())
        except (TypeError, ValueError):
            pass

    effective_dealer_id = int(dealer_id) if dealer_id is not None else int(DEALER_ID)

    staging_payload: dict[str, Any] = {
        "dealer_id": effective_dealer_id,
        "file_location": loc,
        "customer": {
            "aadhar_id": _str_or_none(customer.get("aadhar_id")),
            "name": name or None,
            "gender": gender,
            "date_of_birth": date_of_birth,
            "address": address,
            "pin": pin,
            "city": city,
            "state": state,
            "mobile_number": mobile,
            "alt_phone_num": alt_phone_num,
            "profession": profession,
            "financier": financier,
            "marital_status": marital_status,
            "care_of": care_of,
            "dms_relation_prefix": dms_relation_prefix,
            "dms_contact_path": dms_contact_path,
            "file_location": loc,
        },
        "vehicle": {
            "frame_no": frame_no,
            "engine_no": engine_no,
            "key_no": key_no,
            "battery_no": battery_no,
        },
        "insurance": {
            "nominee_name": nominee_name,
            "nominee_age": nominee_age,
            "nominee_relationship": nominee_relationship,
            "nominee_gender": nominee_gender,
            "insurer": insurer,
            "policy_num": policy_num,
            "policy_from": policy_from_str,
            "policy_to": policy_to_str,
            "premium": premium,
        },
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            staging_row_id = persist_staging_for_submit(
                cur,
                dealer_id=effective_dealer_id,
                payload=staging_payload,
                staging_id_existing=staging_id,
            )
        conn.commit()

    return {
        "ok": True,
        "staging_id": staging_row_id,
    }
