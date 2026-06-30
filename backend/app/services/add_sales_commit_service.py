"""
Commit customer_master, vehicle_master, sales_master from Add Sales staging payload
after successful Create Invoice (DMS). ``insert_insurance_master_after_gi`` inserts
``insurance_type = Main`` (Hero/MISP GI) once per ``(customer_id, vehicle_id, insurance_year)``.
``commit_cpa_alliance_certificate`` inserts/updates ``insurance_type = CPA`` (Alliance) on a
separate row for the same sale/year. Uniqueness is
``(customer_id, vehicle_id, insurance_year, insurance_type)``.
``update_insurance_master_policy_after_issue`` updates Main rows only.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from psycopg2 import IntegrityError

from app.config import (
    DEALER_ID,
    HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE,
    HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER,
)
from app.services.dms_relation_prefix import compute_dms_relation_prefix
from app.services.hero_dms_shared_utilities import (
    _coerce_vehicle_chassis_for_db,
    _coerce_vehicle_engine_for_db,
    _strip_invalid_vehicle_identity_from_scrape,
)
from app.services.utility_functions import sanitize_details_sheet_insurer_value

logger = logging.getLogger(__name__)

INSURANCE_TYPE_MAIN = "Main"
INSURANCE_TYPE_CPA = "CPA"
INSURANCE_MASTER_UNIQUE_CONSTRAINT = "uq_insurance_customer_vehicle_year_type"
# PLAN348 RGI total amount on Alliance portal when plan preset is selected
ALLIANCE_CPA_PLAN_PREMIUM_DEFAULT = 348.0

_insert_deferred_to_api = False


def set_insurance_insert_deferred_to_api(deferred: bool) -> None:
    """Sidecar sets True while Playwright defers ``insert_insurance_master_after_gi`` to API commit."""
    global _insert_deferred_to_api
    _insert_deferred_to_api = deferred


def insurance_insert_deferred_to_api() -> bool:
    return _insert_deferred_to_api


def merge_insurance_scrape_for_commit(
    grid_scrape: dict[str, Any] | None,
    proposal_preview_scrape: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Merge PrintPolicyDetails grid scrape with proposal-review preview for API commit.
    Grid/non-empty values win; proposal preview fills gaps (e.g. premium when grid parse fails).
    """
    out: dict[str, Any] = dict(grid_scrape or {})
    prop = proposal_preview_scrape or {}
    for key in ("policy_num", "policy_from", "policy_to", "premium", "idv"):
        cur = out.get(key)
        if cur is not None and str(cur).strip() != "":
            continue
        v = prop.get(key)
        if v is not None and str(v).strip() != "":
            out[key] = v
    return out


def _build_staging_insurance_patch_main(
    *,
    policy_num: str | None = None,
    policy_from: str | None = None,
    policy_to: str | None = None,
    premium: Any = None,
    idv: Any = None,
) -> dict[str, Any]:
    """Merge Hero GI policy fields into ``payload_json.insurance`` for Add Sales UI display."""
    ins: dict[str, Any] = {}
    pn = _str_or_none(policy_num, 24)
    if pn:
        ins["policy_num"] = pn
    pf = _str_or_none(policy_from, 80)
    if pf:
        ins["policy_from"] = pf
    pt = _str_or_none(policy_to, 80)
    if pt:
        ins["policy_to"] = pt
    pr = _float_or_none(premium)
    if pr is not None:
        ins["premium"] = pr
    idv_f = _float_or_none(idv)
    if idv_f is not None:
        ins["idv"] = idv_f
    if not ins:
        return {}
    return {"insurance": ins}


def _staging_insurance_patch_main_from_insert_row(ir: dict[str, Any]) -> dict[str, Any]:
    return _build_staging_insurance_patch_main(
        policy_num=ir.get("policy_num"),
        policy_from=ir.get("policy_from"),
        policy_to=ir.get("policy_to"),
        premium=ir.get("premium"),
        idv=ir.get("idv"),
    )


def _staging_insurance_patch_main_from_scrape(scrape: dict[str, Any]) -> dict[str, Any]:
    return _build_staging_insurance_patch_main(
        policy_num=scrape.get("policy_num"),
        policy_from=scrape.get("policy_from"),
        policy_to=scrape.get("policy_to"),
        premium=scrape.get("premium"),
        idv=scrape.get("idv"),
    )


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


def build_staging_commit_patch(
    merged_payload: dict[str, Any],
    *,
    customer_id: int,
    vehicle_id: int,
    sales_id: int | None = None,
) -> dict[str, Any]:
    """
    JSONB fragment merged into ``add_sales_staging.payload_json`` on Create Invoice commit:
    master ids, optional ``customer.financier``, and DMS ``vehicle`` order/invoice/enquiry numbers.
    """
    patch_obj: dict[str, Any] = {
        "customer_id": int(customer_id),
        "vehicle_id": int(vehicle_id),
    }
    if sales_id is not None:
        patch_obj["sales_id"] = int(sales_id)
    cust_m = merged_payload.get("customer") if isinstance(merged_payload.get("customer"), dict) else {}
    fn = (cust_m.get("financier") or "").strip()
    if fn:
        patch_obj["customer"] = {"financier": fn[:255]}
    veh_m = merged_payload.get("vehicle") if isinstance(merged_payload.get("vehicle"), dict) else {}
    vehicle_patch: dict[str, str] = {}
    for k, max_len in (("order_number", 128), ("invoice_number", 128), ("enquiry_number", 128)):
        v = str(veh_m.get(k) or "").strip()
        if v:
            vehicle_patch[k] = v[:max_len]
    if vehicle_patch:
        patch_obj["vehicle"] = vehicle_patch
    return patch_obj


def _last4(aadhar_id: str | None) -> str | None:
    if not aadhar_id or not str(aadhar_id).strip():
        return None
    digits = "".join(c for c in str(aadhar_id) if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits or None


def _vehicle_scrape_for_commit(vehicle: dict[str, Any], scraped_vehicle: dict[str, Any] | None) -> dict[str, Any]:
    """Merge post-invoice Siebel scrape with staging ``vehicle`` for ``_upsert_vehicle_master_from_scrape_on_cursor``."""
    out = _strip_invalid_vehicle_identity_from_scrape(dict(scraped_vehicle or {}))
    v = dict(vehicle or {})
    if not str(out.get("full_chassis") or out.get("frame_num") or out.get("chassis") or "").strip():
        fn = str(v.get("frame_no") or "").strip()
        if fn and _coerce_vehicle_chassis_for_db(fn):
            out["frame_no"] = fn
    if not str(out.get("full_engine") or out.get("engine_num") or out.get("engine") or "").strip():
        en = str(v.get("engine_no") or "").strip()
        if en and _coerce_vehicle_engine_for_db(en):
            out["engine_num"] = en
    if not str(out.get("key_num") or out.get("raw_key_num") or "").strip():
        kn = str(v.get("key_no") or "").strip()
        if kn:
            out["key_num"] = kn
    if not str(out.get("battery") or "").strip():
        bn = str(v.get("battery_no") or "").strip()
        if bn:
            out["battery"] = bn
    for k in ("order_number", "invoice_number", "enquiry_number"):
        vv = str(v.get(k) or "").strip()
        if vv and not str(out.get(k) or "").strip():
            out[k] = vv
    return out


_VEHICLE_RAW_PARTIAL_SUFFIX_LEN = 5


def _derive_vehicle_raw_partial(staging_value: str, full_value: str) -> str:
    """Map merged full VIN/engine back to Siebel grid partial for ``raw_*`` ON CONFLICT keys."""
    sv = (staging_value or "").strip()
    fv = (full_value or "").strip()
    if not sv:
        if fv and len(fv) >= _VEHICLE_RAW_PARTIAL_SUFFIX_LEN:
            return fv[-_VEHICLE_RAW_PARTIAL_SUFFIX_LEN :]
        return ""
    if fv and sv == fv and len(fv) >= _VEHICLE_RAW_PARTIAL_SUFFIX_LEN:
        return fv[-_VEHICLE_RAW_PARTIAL_SUFFIX_LEN :]
    return sv


def _vehicle_raw_partials_for_commit(
    vehicle: dict[str, Any],
    vehicle_scrape: dict[str, Any],
) -> dict[str, str | None]:
    """
    Partial keys for ``raw_*`` alignment with prepare_vehicle checkpoint upsert.

    Post-invoice merge may replace ``vehicle.frame_no`` / ``engine_no`` with full scrape values;
    prefer grid partials from scrape and derive suffix when staging holds the full VIN/engine.
    """
    v = dict(vehicle or {})
    vs = dict(vehicle_scrape or {})
    full_c = str(vs.get("full_chassis") or vs.get("chassis") or "").strip()
    full_e = str(vs.get("full_engine") or vs.get("engine_num") or vs.get("engine") or "").strip()

    frame = str(vs.get("frame_num") or "").strip()
    if not frame:
        frame = _derive_vehicle_raw_partial(str(v.get("frame_no") or "").strip(), full_c)

    engine = str(vs.get("engine_num") or "").strip()
    if not engine:
        engine = _derive_vehicle_raw_partial(str(v.get("engine_no") or "").strip(), full_e)

    key = str(vs.get("key_num") or vs.get("raw_key_num") or v.get("key_no") or "").strip()
    battery = str(vs.get("battery") or v.get("battery_no") or "").strip()
    return {
        "frame_partial": frame or None,
        "engine_partial": engine or None,
        "key_partial": key or None,
        "battery_partial": battery or None,
    }


def _dms_values_vehicle_from_staging(vehicle: dict[str, Any], vehicle_scrape: dict[str, Any]) -> dict[str, Any]:
    """Partial keys for raw_* alignment with prepare_vehicle checkpoint upsert."""
    return _vehicle_raw_partials_for_commit(vehicle, vehicle_scrape)


def upsert_customer_vehicle_sales(
    cur,
    payload: dict[str, Any],
    *,
    scraped_vehicle: dict[str, Any] | None = None,
) -> tuple[int, int, int]:
    """
    Customer and vehicle via the same ON CONFLICT upserts as prepare_* checkpoints; then ``sales_master``
    INSERT/upsert. Matches raw triple / (aadhar, mobile) keys so a second write after ``dms_state`` 1/2
    does not hit naive INSERT unique violations.

    Returns ``(customer_id, vehicle_id, sales_id)``.
    """
    from app.services.fill_hero_dms_service import (
        _upsert_customer_master_from_dms_on_cursor,
        _upsert_vehicle_master_from_scrape_on_cursor,
    )
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}

    aadhar_last4 = _last4(customer.get("aadhar_id"))
    mobile = _int_or_none(customer.get("mobile_number"))
    if not aadhar_last4 or mobile is None:
        raise ValueError("Staging payload missing customer aadhar (last 4) or mobile_number for DB commit")

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
    care_of = _str_or_none(customer.get("care_of"), 255)
    dms_relation_prefix = _str_or_none(
        compute_dms_relation_prefix(care_of=care_of, address=address or "", gender=gender), 8
    )
    dms_contact_path = _str_or_none(customer.get("dms_contact_path"), 16) or "found"
    if dms_contact_path.lower() not in ("found", "new_enquiry", "skip_find"):
        dms_contact_path = "found"

    loc = _str_or_none(payload.get("file_location")) or _str_or_none(customer.get("file_location"))

    frame_no = _str_or_none(vehicle.get("frame_no"), 64)
    engine_no = _str_or_none(vehicle.get("engine_no"), 64)
    key_no = _str_or_none(vehicle.get("key_no"), 32)
    battery_no = _str_or_none(vehicle.get("battery_no"), 64)

    order_n = _str_or_none(vehicle.get("order_number"), 128)
    inv_n = _str_or_none(vehicle.get("invoice_number"), 128)
    if inv_n is None and not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
        inv_n = HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER[:128]
    enq_n = _str_or_none(vehicle.get("enquiry_number"), 128)

    sale_dealer_id = _int_or_none(payload.get("dealer_id"))
    if sale_dealer_id is None:
        sale_dealer_id = int(DEALER_ID)

    collated_customer = {
        "name": name,
        "mobile_number": mobile,
        "aadhar": aadhar_last4,
        "address": address,
        "pin": pin,
        "city": city,
        "state": state,
        "alt_phone_num": alt_phone_num,
        "gender": gender,
        "date_of_birth": date_of_birth,
        "profession": profession,
        "financier": financier,
        "marital_status": marital_status,
        "care_of": care_of,
        "dms_relation_prefix": dms_relation_prefix,
        "dms_contact_path": dms_contact_path,
    }
    dms_values_customer = {
        "mobile_phone": str(mobile),
        "aadhar_id": customer.get("aadhar_id"),
        "customer_name": name,
        "address_line_1": address,
        "pin_code": pin,
        "city": city,
        "state": state,
        "care_of": care_of,
        "gender": gender,
        "date_of_birth": date_of_birth,
        "landline": alt_phone_num,
        "profession": profession,
        "financier_name": financier,
        "marital_status": marital_status,
        "dms_contact_path": dms_contact_path,
    }
    customer_id = _upsert_customer_master_from_dms_on_cursor(
        cur,
        dms_values_customer,
        collated_customer,
        file_location=loc,
        dealer_id=sale_dealer_id,
    )

    vehicle_scrape = _vehicle_scrape_for_commit(vehicle, scraped_vehicle)
    dms_values_vehicle = _dms_values_vehicle_from_staging(vehicle, vehicle_scrape)
    preexisting_vehicle_id = _int_or_none(payload.get("vehicle_id"))
    vehicle_id = _upsert_vehicle_master_from_scrape_on_cursor(
        cur,
        dms_values_vehicle,
        vehicle_scrape,
        dealer_id=sale_dealer_id,
        preexisting_vehicle_id=preexisting_vehicle_id,
    )

    try:
        cur.execute(
            """
            INSERT INTO sales_master (
                customer_id, vehicle_id, dealer_id, file_location,
                order_number, invoice_number, enquiry_number
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (customer_id, vehicle_id) DO UPDATE SET
                dealer_id = COALESCE(EXCLUDED.dealer_id, sales_master.dealer_id),
                file_location = COALESCE(EXCLUDED.file_location, sales_master.file_location),
                order_number = COALESCE(EXCLUDED.order_number, sales_master.order_number),
                invoice_number = COALESCE(EXCLUDED.invoice_number, sales_master.invoice_number),
                enquiry_number = COALESCE(EXCLUDED.enquiry_number, sales_master.enquiry_number)
            RETURNING sales_id
            """,
            (customer_id, vehicle_id, sale_dealer_id, loc, order_n, inv_n, enq_n),
        )
        row = cur.fetchone()
        sales_id = int(row["sales_id"]) if row else None
    except IntegrityError as exc:
        cname = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
        if cname == "uq_sales_customer_vehicle" or "uq_sales_customer_vehicle" in str(exc):
            cur.execute(
                """
                SELECT sales_id FROM sales_master
                WHERE customer_id = %s AND vehicle_id = %s
                LIMIT 1
                """,
                (customer_id, vehicle_id),
            )
            srow = cur.fetchone()
            if not srow:
                raise ValueError(
                    "A sale already exists for this customer and vehicle; duplicate sales_master row is not allowed."
                ) from exc
            sales_id = int(srow["sales_id"] if isinstance(srow, dict) else srow[0])
            cur.execute(
                """
                UPDATE sales_master SET
                    dealer_id = COALESCE(%s, dealer_id),
                    file_location = COALESCE(%s, file_location),
                    order_number = COALESCE(%s, order_number),
                    invoice_number = COALESCE(%s, invoice_number),
                    enquiry_number = COALESCE(%s, enquiry_number)
                WHERE sales_id = %s
                """,
                (sale_dealer_id, loc, order_n, inv_n, enq_n, sales_id),
            )
        else:
            raise

    if sales_id is None:
        raise RuntimeError("sales_master INSERT did not return sales_id")

    return int(customer_id), int(vehicle_id), sales_id


def commit_staging_masters_and_finalize_row(
    *,
    staging_id: str,
    merged_payload: dict[str, Any],
    scraped_vehicle: dict[str, Any] | None = None,
) -> tuple[int, int, int]:
    """
    Single transaction: upsert masters, mark ``add_sales_staging`` committed, patch payload with ids,
    ``customer.financier``, and ``vehicle`` order/invoice/enquiry when present in ``merged_payload``.
    Called only after **Create Invoice** (Invoice# present in merged scrape); same values are stored on
    ``sales_master`` in the same INSERT — no follow-up UPDATE on masters.
    """
    from app.db import get_connection
    from app.repositories.add_sales_staging import mark_staging_committed_on_cursor

    sid = (staging_id or "").strip()
    if not sid:
        raise ValueError("staging_id required for master commit")

    dealer_raw = merged_payload.get("dealer_id")
    try:
        dealer_id = int(dealer_raw) if dealer_raw is not None else int(DEALER_ID)
    except (TypeError, ValueError):
        dealer_id = int(DEALER_ID)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid, vid, sid_sale = upsert_customer_vehicle_sales(
                cur, merged_payload, scraped_vehicle=scraped_vehicle
            )
            patch_obj = build_staging_commit_patch(
                merged_payload,
                customer_id=cid,
                vehicle_id=vid,
                sales_id=sid_sale,
            )
            mark_staging_committed_on_cursor(cur, sid, dealer_id, patch=patch_obj)
        conn.commit()
    return int(cid), int(vid), int(sid_sale)


def finalize_staging_row_with_master_ids(
    *,
    staging_id: str,
    merged_payload: dict[str, Any],
    customer_id: int,
    vehicle_id: int,
    sales_id: int | None,
) -> None:
    """
    Mark ``add_sales_staging`` committed when ``customer_master`` / ``vehicle_master`` / ``sales_master``
    were already written in the same run (e.g. ``insert_dms_masters_from_siebel_scrape`` inside
    ``persist_masters_after_create_order``). Avoids a second upsert that can duplicate ``vehicle_master``
    when raw-key lookup differs from the Siebel insert path.
    """
    from app.db import get_connection
    from app.repositories.add_sales_staging import mark_staging_committed_on_cursor

    sid = (staging_id or "").strip()
    if not sid:
        raise ValueError("staging_id required for master commit")

    dealer_raw = merged_payload.get("dealer_id")
    try:
        dealer_id = int(dealer_raw) if dealer_raw is not None else int(DEALER_ID)
    except (TypeError, ValueError):
        dealer_id = int(DEALER_ID)

    patch_obj = build_staging_commit_patch(
        merged_payload,
        customer_id=int(customer_id),
        vehicle_id=int(vehicle_id),
        sales_id=sales_id,
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            mark_staging_committed_on_cursor(cur, sid, dealer_id, patch=patch_obj)
        conn.commit()


def _parse_date_loose(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    try:
        return float(str(val).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def compute_insurance_master_insert_snapshot(
    customer_id: int,
    vehicle_id: int,
    *,
    fill_values: dict[str, Any],
    staging_payload: dict[str, Any] | None = None,
    preview_scrape: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    All column values and derivation notes for the ``INSERT INTO insurance_master`` performed by
    ``insert_insurance_master_after_gi`` (same rules as the insert).

    ``preview_scrape`` is the dict from ``scrape_insurance_policy_preview_before_issue`` (policy_num,
    policy_from, policy_to, premium, idv). Those fields fall back to ``staging_payload['insurance']``
    when missing from preview.
    """
    ins_staging: dict[str, Any] = {}
    if staging_payload and isinstance(staging_payload.get("insurance"), dict):
        ins_staging = dict(staging_payload["insurance"])

    pv = preview_scrape or {}

    field_sources: dict[str, str] = {}

    def _insurer_from_payload(raw: Any) -> str | None:
        if raw is None:
            return None
        t = str(raw).strip()
        if not t:
            return None
        ok = sanitize_details_sheet_insurer_value(t)
        return _str_or_none(ok, 255) if ok else None

    fv_ins = _insurer_from_payload(fill_values.get("insurer"))
    st_ins = _insurer_from_payload(ins_staging.get("insurer"))
    insurer = fv_ins or st_ins
    if fv_ins:
        field_sources["insurer"] = "fill_values (MISP merge / view)"
    elif st_ins:
        field_sources["insurer"] = "staging_payload.insurance (OCR merge / staging only)"
    else:
        field_sources["insurer"] = "none"

    fn, sn = _str_or_none(fill_values.get("nominee_name")), _str_or_none(ins_staging.get("nominee_name"))
    nominee_name = fn or sn
    if fn:
        field_sources["nominee_name"] = "fill_values"
    elif sn:
        field_sources["nominee_name"] = "staging_payload.insurance"
    else:
        field_sources["nominee_name"] = "none"

    fr, sr = _str_or_none(fill_values.get("nominee_relationship"), 64), _str_or_none(
        ins_staging.get("nominee_relationship"), 64
    )
    nominee_relationship = fr or sr
    if fr:
        field_sources["nominee_relationship"] = "fill_values"
    elif sr:
        field_sources["nominee_relationship"] = "staging_payload.insurance"
    else:
        field_sources["nominee_relationship"] = "none"

    nominee_raw = fill_values.get("nominee_age")
    if nominee_raw is None or (isinstance(nominee_raw, str) and not str(nominee_raw).strip()):
        nominee_raw = ins_staging.get("nominee_age")
        field_sources["nominee_age"] = "staging_payload.insurance (fill_values missing or blank)"
    else:
        field_sources["nominee_age"] = "fill_values"
    nominee_age = _int_or_none(nominee_raw)

    fg, sg = _str_or_none(fill_values.get("nominee_gender"), 16), _str_or_none(
        ins_staging.get("nominee_gender"), 16
    )
    nominee_gender = fg or sg
    if fg:
        field_sources["nominee_gender"] = "fill_values"
    elif sg:
        field_sources["nominee_gender"] = "staging_payload.insurance"
    else:
        field_sources["nominee_gender"] = "none"

    _FINAL_SCRAPE_SRC = "final policy details scrape"

    pnum = _str_or_none(pv.get("policy_num"), 24) or _str_or_none(ins_staging.get("policy_num"), 24)
    if _str_or_none(pv.get("policy_num"), 24):
        field_sources["policy_num"] = _FINAL_SCRAPE_SRC
    elif _str_or_none(ins_staging.get("policy_num"), 24):
        field_sources["policy_num"] = "staging_payload.insurance"
    else:
        field_sources["policy_num"] = "none"

    pf_pv = _parse_date_loose(_str_or_none(pv.get("policy_from"), 80))
    pt_pv = _parse_date_loose(_str_or_none(pv.get("policy_to"), 80))
    pf_st = _parse_date_loose(_str_or_none(ins_staging.get("policy_from"), 20))
    pt_st = _parse_date_loose(_str_or_none(ins_staging.get("policy_to"), 20))
    policy_from_d = pf_pv or pf_st
    policy_to_d = pt_pv or pt_st
    field_sources["policy_from"] = (
        _FINAL_SCRAPE_SRC
        if pf_pv
        else ("staging_payload.insurance (parsed)" if pf_st else "none")
    )
    field_sources["policy_to"] = (
        _FINAL_SCRAPE_SRC
        if pt_pv
        else ("staging_payload.insurance (parsed)" if pt_st else "none")
    )

    prem_pv = _float_or_none(pv.get("premium"))
    premium = prem_pv
    if premium is None and ins_staging.get("premium") is not None:
        try:
            premium = float(str(ins_staging.get("premium")).replace(",", "").strip())
        except (TypeError, ValueError):
            premium = None
    field_sources["premium"] = (
        _FINAL_SCRAPE_SRC
        if prem_pv is not None
        else ("staging_payload.insurance (numeric parse)" if premium is not None else "none")
    )

    idv_pv = _float_or_none(pv.get("idv"))
    idv_f = idv_pv
    if idv_f is None and ins_staging.get("idv") is not None:
        try:
            idv_f = float(str(ins_staging.get("idv")).replace(",", "").strip())
        except (TypeError, ValueError):
            idv_f = None
    field_sources["idv"] = (
        _FINAL_SCRAPE_SRC
        if idv_pv is not None
        else ("staging_payload.insurance (numeric parse)" if idv_f is not None else "none")
    )

    st_broker = _str_or_none(ins_staging.get("policy_broker"), 255)
    if not insurer and st_broker:
        insurer = st_broker
        field_sources["insurer"] = "staging_payload.insurance.policy_broker (fallback)"
        policy_broker = None
        field_sources["policy_broker"] = "none (value moved to insurer)"
    else:
        policy_broker = st_broker
        field_sources["policy_broker"] = (
            "staging_payload.insurance" if policy_broker else "none (not in staging)"
        )

    insurance_year = date.today().year
    field_sources["insurance_year"] = "derived: date.today().year (server calendar year)"
    field_sources["insurance_type"] = f"constant: {INSURANCE_TYPE_MAIN} (Hero/MISP GI)"
    field_sources["customer_id"] = "request parameter"
    field_sources["vehicle_id"] = "request parameter"

    uncertainties: list[str] = [
        "insurance_id: not in INSERT; DB default nextval(insurance_master_insurance_id_seq).",
        "insurance_year uses server calendar year, not policy period from preview unless aligned manually.",
        "policy_broker: INSERT uses staging_payload['insurance'] only (not on preview screen scrape).",
        "Final policy scrape uses regex/heuristics; verify against MISP Final Policy Details layout.",
        "String fields may be truncated to column max length (_str_or_none); verify against DDL varchar limits.",
    ]

    insert_row = {
        "customer_id": int(customer_id),
        "vehicle_id": int(vehicle_id),
        "insurance_year": insurance_year,
        "insurance_type": INSURANCE_TYPE_MAIN,
        "nominee_name": nominee_name,
        "nominee_age": nominee_age,
        "nominee_relationship": nominee_relationship,
        "nominee_gender": nominee_gender,
        "insurer": insurer,
        "policy_num": pnum,
        "policy_from": policy_from_d.isoformat() if policy_from_d else None,
        "policy_to": policy_to_d.isoformat() if policy_to_d else None,
        "premium": premium,
        "idv": idv_f,
        "policy_broker": policy_broker,
    }

    return {
        "insert_row": insert_row,
        "field_sources": field_sources,
        "uncertainties": uncertainties,
        "inputs_echo": {
            "preview_scrape_raw": {k: pv.get(k) for k in ("policy_num", "policy_from", "policy_to", "premium", "idv")},
            "staging_insurance_keys": sorted(ins_staging.keys()),
            "fill_values_keys": sorted(k for k in fill_values.keys() if k != "subfolder")[:120],
        },
    }


def append_insurance_master_commit_plan_to_playwright(
    ocr_output_dir: Path | str | None,
    subfolder: str | None,
    *,
    preview_scrape: dict[str, Any] | None,
    snap: dict[str, Any],
) -> None:
    """Log final-page scrape and planned ``insurance_master`` INSERT row to ``Playwright_insurance.txt``."""
    from app.services.insurance_form_values import append_playwright_insurance_line

    pv = preview_scrape or {}
    scrape_keys = ("policy_num", "policy_from", "policy_to", "premium", "idv")
    scrape_part = {k: pv.get(k) for k in scrape_keys}
    try:
        scrape_json = json.dumps(scrape_part, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        scrape_json = str(scrape_part)
    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"final_policy_scrape (raw): {scrape_json}"[:8_000],
    )
    ir = snap.get("insert_row") or {}
    try:
        row_json = json.dumps(ir, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        row_json = str(ir)
    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"insurance_master INSERT (planned): {row_json}"[:8_000],
    )
    fs = snap.get("field_sources") or {}
    try:
        fs_json = json.dumps(fs, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        fs_json = str(fs)
    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"insurance_master INSERT (field_sources): {fs_json}"[:8_000],
    )


def insert_insurance_master_after_gi(
    customer_id: int,
    vehicle_id: int,
    *,
    fill_values: dict[str, Any],
    staging_payload: dict[str, Any] | None = None,
    preview_scrape: dict[str, Any] | None = None,
    ocr_output_dir: Path | str | None = None,
    subfolder: str | None = None,
    staging_id: str | None = None,
    dealer_id: int | None = None,
) -> None:
    """
    INSERT ``insurance_master`` for the current calendar ``insurance_year`` after Final Policy Details
    scrape on MISP (Hero flow, production **Issue Policy** path). Insurer / nominee fields prefer the MISP
    fill dict; ``policy_num``, ``policy_from``, ``policy_to``, ``premium``, and ``idv`` use
    ``preview_scrape`` when provided, else staging; ``policy_broker`` from staging when present.
    Raises ``ValueError`` if a row already exists for the same customer, vehicle, year, and type Main
    (``uq_insurance_customer_vehicle_year_type``). When ``ocr_output_dir`` and ``subfolder`` are set, logs
    scrape + planned INSERT to ``Playwright_insurance.txt`` before commit.
    When ``staging_id`` and ``dealer_id`` are set, merges ``insurance_id`` into ``add_sales_staging.payload_json``.
    """
    snap = compute_insurance_master_insert_snapshot(
        customer_id,
        vehicle_id,
        fill_values=fill_values,
        staging_payload=staging_payload,
        preview_scrape=preview_scrape,
    )
    append_insurance_master_commit_plan_to_playwright(
        ocr_output_dir,
        subfolder,
        preview_scrape=preview_scrape,
        snap=snap,
    )

    ir = snap["insert_row"]
    insurance_year = int(ir["insurance_year"])
    nominee_name = ir["nominee_name"]
    nominee_age = ir.get("nominee_age")
    nominee_relationship = ir["nominee_relationship"]
    nominee_gender = ir["nominee_gender"]
    insurer = ir["insurer"]
    policy_num = ir["policy_num"]
    policy_from_d: date | None = None
    policy_to_d: date | None = None
    if ir.get("policy_from"):
        try:
            policy_from_d = date.fromisoformat(str(ir["policy_from"]))
        except ValueError:
            policy_from_d = None
    if ir.get("policy_to"):
        try:
            policy_to_d = date.fromisoformat(str(ir["policy_to"]))
        except ValueError:
            policy_to_d = None

    premium = ir.get("premium")
    idv_f = ir.get("idv")
    policy_broker = ir["policy_broker"]

    from app.db import get_connection
    from app.repositories.add_sales_staging import merge_staging_payload_on_cursor

    with get_connection() as conn:
        with conn.cursor() as cur:
            insurance_id: int | None = None
            try:
                cur.execute(
                    """
                    INSERT INTO insurance_master (
                        customer_id, vehicle_id, insurance_year, insurance_type,
                        nominee_name, nominee_age, nominee_relationship, nominee_gender,
                        insurer, policy_num, policy_from, policy_to, premium,
                        idv, policy_broker
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING insurance_id
                    """,
                    (
                        int(customer_id),
                        int(vehicle_id),
                        insurance_year,
                        INSURANCE_TYPE_MAIN,
                        nominee_name,
                        nominee_age,
                        nominee_relationship,
                        nominee_gender,
                        insurer,
                        policy_num,
                        policy_from_d,
                        policy_to_d,
                        premium,
                        idv_f,
                        policy_broker,
                    ),
                )
                row_ins = cur.fetchone()
                if row_ins:
                    insurance_id = int(row_ins["insurance_id"])
            except IntegrityError as exc:
                cname = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
                if (
                    cname == INSURANCE_MASTER_UNIQUE_CONSTRAINT
                    or "uq_insurance_customer_vehicle_year" in str(exc)
                ):
                    raise ValueError(
                        "Hero insurance (Main) already recorded for this customer, vehicle, and calendar year; "
                        "duplicate insurance_master row is not allowed."
                    ) from exc
                raise
            sid_merge = (staging_id or "").strip()
            if sid_merge and dealer_id is not None:
                patch: dict[str, Any] = {}
                if insurance_id is not None:
                    patch["insurance_id"] = insurance_id
                ins_patch = _staging_insurance_patch_main_from_insert_row(ir)
                if ins_patch:
                    patch = {**patch, **ins_patch}
                if patch:
                    merge_staging_payload_on_cursor(cur, sid_merge, int(dealer_id), patch)
        conn.commit()
    from app.services.insurance_form_values import append_playwright_insurance_line

    append_playwright_insurance_line(
        ocr_output_dir,
        subfolder,
        "NOTE",
        f"insurance_master INSERT ok: customer_id={customer_id} vehicle_id={vehicle_id} "
        f"year={insurance_year} policy_num={policy_num!r} premium={premium}",
    )
    logger.info(
        "insurance_master insert after GI: customer_id=%s vehicle_id=%s year=%s policy_num=%r premium=%s",
        customer_id,
        vehicle_id,
        insurance_year,
        policy_num,
        premium,
    )


def update_insurance_master_policy_after_issue(
    customer_id: int,
    vehicle_id: int,
    *,
    scrape: dict[str, Any] | None = None,
    staging_id: str | None = None,
    dealer_id: int | None = None,
) -> None:
    """UPDATE policy fields for the current ``insurance_year`` from post–Issue Policy preview scrape."""
    if not scrape:
        return
    insurance_year = date.today().year
    sets: list[str] = []
    params: list[Any] = []

    if scrape.get("policy_num") is not None:
        pn = _str_or_none(scrape.get("policy_num"), 24)
        if pn is not None:
            sets.append("policy_num = %s")
            params.append(pn)

    pf = _parse_date_loose(_str_or_none(scrape.get("policy_from"), 80))
    if pf is not None:
        sets.append("policy_from = %s")
        params.append(pf)

    pt = _parse_date_loose(_str_or_none(scrape.get("policy_to"), 80))
    if pt is not None:
        sets.append("policy_to = %s")
        params.append(pt)

    pr = _float_or_none(scrape.get("premium"))
    if pr is not None:
        sets.append("premium = %s")
        params.append(pr)

    idv_u = _float_or_none(scrape.get("idv"))
    if idv_u is not None:
        sets.append("idv = %s")
        params.append(idv_u)

    if not sets:
        return
    params.extend([int(customer_id), int(vehicle_id), insurance_year, INSURANCE_TYPE_MAIN])

    from app.db import get_connection
    from app.repositories.add_sales_staging import merge_staging_payload_on_cursor

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE insurance_master SET {', '.join(sets)} "
                "WHERE customer_id = %s AND vehicle_id = %s AND insurance_year = %s "
                "AND insurance_type = %s",
                tuple(params),
            )
            if cur.rowcount == 0:
                logger.warning(
                    "insurance_master policy update after issue: no row for customer_id=%s vehicle_id=%s year=%s",
                    customer_id,
                    vehicle_id,
                    insurance_year,
                )
            sid_merge = (staging_id or "").strip()
            if sid_merge and dealer_id is not None:
                ins_patch = _staging_insurance_patch_main_from_scrape(scrape)
                if ins_patch:
                    merge_staging_payload_on_cursor(cur, sid_merge, int(dealer_id), ins_patch)
        conn.commit()
    logger.info(
        "insurance_master update after Issue Policy: customer_id=%s vehicle_id=%s year=%s sets=%s",
        customer_id,
        vehicle_id,
        insurance_year,
        sets,
    )


def _alliance_cpa_plan_premium() -> float:
    import os

    raw = (os.getenv("ALLIANCE_CPA_PLAN_PREMIUM") or "").strip()
    if raw:
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            pass
    return ALLIANCE_CPA_PLAN_PREMIUM_DEFAULT


def compute_cpa_insurance_master_insert_snapshot(
    customer_id: int,
    vehicle_id: int,
    *,
    certificate_number: str,
    staging_payload: dict[str, Any] | None = None,
    cpa_insurer: str | None = None,
) -> dict[str, Any]:
    """
    Planned INSERT for ``insurance_type = CPA``: certificate # from Alliance scrape, premium from
  PLAN348 RGI (default 348), nominee/insurer from staging overlay (same rules as Main GI insert).
    """
    cert = _str_or_none(certificate_number, 24)
    if not cert:
        raise ValueError("certificate_number is required for CPA insurance_master row.")
    prem = _alliance_cpa_plan_premium()
    preview = {"policy_num": cert, "premium": prem}
    ins_staging: dict[str, Any] = {}
    if staging_payload and isinstance(staging_payload.get("insurance"), dict):
        ins_staging = dict(staging_payload["insurance"])
    fill_values: dict[str, Any] = {}
    if cpa_insurer:
        fill_values["insurer"] = cpa_insurer
    for key in ("nominee_name", "nominee_age", "nominee_relationship", "nominee_gender"):
        if ins_staging.get(key) is not None:
            fill_values[key] = ins_staging.get(key)
    snap = compute_insurance_master_insert_snapshot(
        customer_id,
        vehicle_id,
        fill_values=fill_values,
        staging_payload=staging_payload,
        preview_scrape=preview,
    )
    ir = dict(snap.get("insert_row") or {})
    ir["insurance_type"] = INSURANCE_TYPE_CPA
    if cpa_insurer:
        ir["insurer"] = _str_or_none(cpa_insurer, 255) or ir.get("insurer")
    elif not ir.get("insurer"):
        ir["insurer"] = "Alliance CPA"
    ir["policy_num"] = cert
    ir["premium"] = prem
    fs = dict(snap.get("field_sources") or {})
    fs["insurance_type"] = f"constant: {INSURANCE_TYPE_CPA}"
    fs["policy_num"] = "Alliance Print Certificate scrape (Certificate Number)"
    fs["premium"] = f"PLAN348 RGI default ({prem}) or ALLIANCE_CPA_PLAN_PREMIUM env"
    return {"insert_row": ir, "field_sources": fs}


def commit_cpa_alliance_certificate(
    customer_id: int,
    vehicle_id: int,
    *,
    certificate_number: str,
    staging_id: str | None = None,
    dealer_id: int | None = None,
    cpa_insurer: str | None = None,
    staging_payload: dict[str, Any] | None = None,
) -> None:
    """
    Persist Alliance CPA as its own ``insurance_master`` row (``insurance_type = CPA``).

    Hero GI remains ``insurance_type = Main`` on a separate row for the same sale/year.
    UI reads ``staging.insurance.cpa_policy_num``; ``form_cpa_insurance_view`` exposes
    ``cpa_policy_num`` from the CPA row after commit.
    """
    cert = _str_or_none(certificate_number, 24)
    if not cert:
        return
    insurance_year = date.today().year

    from app.db import get_connection
    from app.repositories.add_sales_staging import fetch_staging_payload, merge_staging_payload_on_cursor

    payload = staging_payload
    if payload is None and staging_id and dealer_id:
        payload = fetch_staging_payload(staging_id, int(dealer_id))

    snap = compute_cpa_insurance_master_insert_snapshot(
        int(customer_id),
        int(vehicle_id),
        certificate_number=cert,
        staging_payload=payload,
        cpa_insurer=cpa_insurer,
    )
    ir = snap["insert_row"]
    policy_from_d: date | None = None
    policy_to_d: date | None = None
    if ir.get("policy_from"):
        try:
            policy_from_d = date.fromisoformat(str(ir["policy_from"]))
        except ValueError:
            policy_from_d = None
    if ir.get("policy_to"):
        try:
            policy_to_d = date.fromisoformat(str(ir["policy_to"]))
        except ValueError:
            policy_to_d = None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT insurance_id
                FROM insurance_master
                WHERE customer_id = %s AND vehicle_id = %s AND insurance_year = %s
                  AND insurance_type = %s
                LIMIT 1
                """,
                (int(customer_id), int(vehicle_id), insurance_year, INSURANCE_TYPE_CPA),
            )
            existing = cur.fetchone()
            cpa_insurance_id: int | None = None
            if existing:
                cpa_insurance_id = int(existing["insurance_id"])
                cur.execute(
                    """
                    UPDATE insurance_master SET
                        nominee_name = %s, nominee_age = %s, nominee_relationship = %s,
                        nominee_gender = %s, insurer = %s, policy_num = %s,
                        policy_from = %s, policy_to = %s, premium = %s, idv = %s, policy_broker = %s
                    WHERE insurance_id = %s
                    """,
                    (
                        ir.get("nominee_name"),
                        ir.get("nominee_age"),
                        ir.get("nominee_relationship"),
                        ir.get("nominee_gender"),
                        ir.get("insurer"),
                        ir.get("policy_num"),
                        policy_from_d,
                        policy_to_d,
                        ir.get("premium"),
                        ir.get("idv"),
                        ir.get("policy_broker"),
                        cpa_insurance_id,
                    ),
                )
            else:
                try:
                    cur.execute(
                        """
                        INSERT INTO insurance_master (
                            customer_id, vehicle_id, insurance_year, insurance_type,
                            nominee_name, nominee_age, nominee_relationship, nominee_gender,
                            insurer, policy_num, policy_from, policy_to, premium,
                            idv, policy_broker
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING insurance_id
                        """,
                        (
                            int(customer_id),
                            int(vehicle_id),
                            insurance_year,
                            INSURANCE_TYPE_CPA,
                            ir.get("nominee_name"),
                            ir.get("nominee_age"),
                            ir.get("nominee_relationship"),
                            ir.get("nominee_gender"),
                            ir.get("insurer"),
                            ir.get("policy_num"),
                            policy_from_d,
                            policy_to_d,
                            ir.get("premium"),
                            ir.get("idv"),
                            ir.get("policy_broker"),
                        ),
                    )
                    row_ins = cur.fetchone()
                    if row_ins:
                        cpa_insurance_id = int(row_ins["insurance_id"])
                except IntegrityError as exc:
                    cname = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
                    if (
                        cname == INSURANCE_MASTER_UNIQUE_CONSTRAINT
                        or "uq_insurance_customer_vehicle_year" in str(exc)
                    ):
                        raise ValueError(
                            "CPA insurance already recorded for this customer, vehicle, and year."
                        ) from exc
                    raise
            sid_merge = (staging_id or "").strip()
            patch: dict[str, Any] = {"insurance": {"cpa_policy_num": cert}}
            if cpa_insurance_id is not None:
                patch["cpa_insurance_id"] = cpa_insurance_id
            if sid_merge and dealer_id is not None:
                merge_staging_payload_on_cursor(cur, sid_merge, int(dealer_id), patch)
        conn.commit()
    logger.info(
        "CPA insurance_master committed: customer_id=%s vehicle_id=%s year=%s type=%s cert=%r premium=%s",
        customer_id,
        vehicle_id,
        insurance_year,
        INSURANCE_TYPE_CPA,
        cert,
        ir.get("premium"),
    )
