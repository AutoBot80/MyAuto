"""
Commit customer_master, vehicle_master, sales_master from Add Sales staging payload
after successful Create Invoice (DMS). ``insert_insurance_master_after_gi`` runs after
successful Generate Insurance (plain INSERT; duplicate ``(customer_id, vehicle_id, insurance_year)``
fails). ``update_insurance_master_policy_after_issue`` refreshes policy fields from the post–**Issue Policy**
preview scrape (same shape as pre-insert scrape).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from psycopg2 import IntegrityError

from app.config import DEALER_ID
from app.services.dms_relation_prefix import compute_dms_relation_prefix

logger = logging.getLogger(__name__)


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


def upsert_customer_vehicle_sales(cur, payload: dict[str, Any]) -> tuple[int, int]:
    """
    Customer by (aadhar last 4, mobile_number), vehicle by raw triple upsert; **sales** is a plain
    ``INSERT``. If ``sales_master`` already has a row for the resulting ``(customer_id, vehicle_id)``,
    raises ``ValueError`` (unique ``uq_sales_customer_vehicle``).
    """
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
    dms_relation_prefix = _str_or_none(compute_dms_relation_prefix(address or "", gender), 8)
    care_of = _str_or_none(customer.get("care_of"), 255)
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
    enq_n = _str_or_none(vehicle.get("enquiry_number"), 128)

    raw_f = frame_no or None
    raw_e = engine_no or None
    raw_k = key_no or None

    sale_dealer_id = _int_or_none(payload.get("dealer_id"))
    if sale_dealer_id is None:
        sale_dealer_id = int(DEALER_ID)

    cur.execute(
        """
        SELECT customer_id FROM customer_master
        WHERE aadhar = %s AND mobile_number = %s
        """,
        (aadhar_last4, mobile),
    )
    row = cur.fetchone()
    if row:
        customer_id = row["customer_id"]
        cur.execute(
            """
            UPDATE customer_master SET
                name = %s, address = %s, pin = %s, city = %s, state = %s,
                alt_phone_num = %s,
                gender = %s, date_of_birth = %s, profession = %s,
                financier = %s, marital_status = %s,
                dms_relation_prefix = %s, dms_contact_path = %s, care_of = %s,
                file_location = %s
            WHERE customer_id = %s
            """,
            (
                name,
                address,
                pin,
                city,
                state,
                alt_phone_num,
                gender,
                date_of_birth,
                profession,
                financier,
                marital_status,
                dms_relation_prefix,
                dms_contact_path,
                care_of,
                loc,
                customer_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO customer_master (
                aadhar, name, address, pin, city, state, mobile_number,
                alt_phone_num,
                profession, financier, marital_status,
                dms_relation_prefix, dms_contact_path, care_of,
                file_location, gender, date_of_birth
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING customer_id
            """,
            (
                aadhar_last4,
                name,
                address,
                pin,
                city,
                state,
                mobile,
                alt_phone_num,
                profession,
                financier,
                marital_status,
                dms_relation_prefix,
                dms_contact_path,
                care_of,
                loc,
                gender,
                date_of_birth,
            ),
        )
        customer_id = cur.fetchone()["customer_id"]

    cur.execute(
        """
        SELECT vehicle_id FROM vehicle_master
        WHERE (raw_frame_num IS NOT DISTINCT FROM %s)
          AND (raw_engine_num IS NOT DISTINCT FROM %s)
          AND (raw_key_num IS NOT DISTINCT FROM %s)
        """,
        (raw_f, raw_e, raw_k),
    )
    vrow = cur.fetchone()
    if vrow:
        vehicle_id = vrow["vehicle_id"]
        cur.execute(
            """
            UPDATE vehicle_master SET
                chassis = %s, engine = %s, key_num = %s, battery = %s,
                raw_frame_num = %s, raw_engine_num = %s, raw_key_num = %s
            WHERE vehicle_id = %s
            """,
            (frame_no, engine_no, raw_k, battery_no, raw_f, raw_e, raw_k, vehicle_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO vehicle_master (chassis, engine, key_num, battery, raw_frame_num, raw_engine_num, raw_key_num)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING vehicle_id
            """,
            (frame_no, engine_no, raw_k, battery_no, raw_f, raw_e, raw_k),
        )
        vehicle_id = cur.fetchone()["vehicle_id"]

    try:
        cur.execute(
            """
            INSERT INTO sales_master (
                customer_id, vehicle_id, dealer_id, file_location,
                order_number, invoice_number, enquiry_number
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (customer_id, vehicle_id, sale_dealer_id, loc, order_n, inv_n, enq_n),
        )
    except IntegrityError as exc:
        cname = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
        if cname == "uq_sales_customer_vehicle" or "uq_sales_customer_vehicle" in str(exc):
            raise ValueError(
                "A sale already exists for this customer and vehicle; duplicate sales_master row is not allowed."
            ) from exc
        raise

    return int(customer_id), int(vehicle_id)


def commit_staging_masters_and_finalize_row(
    *,
    staging_id: str,
    merged_payload: dict[str, Any],
) -> tuple[int, int]:
    """
    Single transaction: upsert masters, mark ``add_sales_staging`` committed, patch payload with ids.
    Called only after **Create Invoice** (Invoice# present in merged scrape); order/invoice/enquiry are
    stored on ``sales_master`` in the same INSERT — no follow-up UPDATE.
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
            cid, vid = upsert_customer_vehicle_sales(cur, merged_payload)
            patch = json.dumps({"customer_id": cid, "vehicle_id": vid}, default=str)
            mark_staging_committed_on_cursor(cur, sid, dealer_id, patch_json_fragment=patch)
        conn.commit()
    return cid, vid


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

    fv_ins = _str_or_none(fill_values.get("insurer"), 255)
    st_ins = _str_or_none(ins_staging.get("insurer"), 255)
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

    pnum = _str_or_none(pv.get("policy_num"), 24) or _str_or_none(ins_staging.get("policy_num"), 24)
    if _str_or_none(pv.get("policy_num"), 24):
        field_sources["policy_num"] = "proposal preview scrape (pre–Issue Policy)"
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
        "proposal preview scrape (pre–Issue Policy)"
        if pf_pv
        else ("staging_payload.insurance (parsed)" if pf_st else "none")
    )
    field_sources["policy_to"] = (
        "proposal preview scrape (pre–Issue Policy)"
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
        "proposal preview scrape (pre–Issue Policy)"
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
        "proposal preview scrape (pre–Issue Policy)"
        if idv_pv is not None
        else ("staging_payload.insurance (numeric parse)" if idv_f is not None else "none")
    )

    policy_broker = _str_or_none(ins_staging.get("policy_broker"), 255)
    field_sources["policy_broker"] = (
        "staging_payload.insurance" if policy_broker else "none (not in staging)"
    )

    insurance_year = date.today().year
    field_sources["insurance_year"] = "derived: date.today().year (server calendar year)"
    field_sources["customer_id"] = "request parameter"
    field_sources["vehicle_id"] = "request parameter"

    uncertainties: list[str] = [
        "insurance_id: not in INSERT; DB default nextval(insurance_master_insurance_id_seq).",
        "insurance_year uses server calendar year, not policy period from preview unless aligned manually.",
        "policy_broker: INSERT uses staging_payload['insurance'] only (not on preview screen scrape).",
        "Preview scrape uses regex/heuristics; verify against MISP layout for your insurer.",
        "policy_num, policy_from, policy_to, premium, idv may be refreshed by update_insurance_master_policy_after_issue after Issue Policy.",
        "String fields may be truncated to column max length (_str_or_none); verify against DDL varchar limits.",
    ]

    insert_row = {
        "customer_id": int(customer_id),
        "vehicle_id": int(vehicle_id),
        "insurance_year": insurance_year,
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


def insert_insurance_master_after_gi(
    customer_id: int,
    vehicle_id: int,
    *,
    fill_values: dict[str, Any],
    staging_payload: dict[str, Any] | None = None,
    preview_scrape: dict[str, Any] | None = None,
    ocr_output_dir: Path | str | None = None,
    subfolder: str | None = None,
) -> None:
    """
    INSERT ``insurance_master`` for the current calendar ``insurance_year`` after proposal review.
    Insurer / nominee fields prefer the MISP fill dict; ``policy_num``, ``policy_from``, ``policy_to``,
    ``premium``, and ``idv`` prefer the preview scrape dict; ``policy_broker`` from staging when present.
    Raises ``ValueError`` if a row already exists for the same customer, vehicle, and year
    (``uq_insurance_customer_vehicle_year``). After **Issue Policy**, call
    ``update_insurance_master_policy_after_issue`` with the post-issue preview scrape dict.
    Does not write pre-commit INSERT snapshot lines to ``Playwright_insurance.txt`` (see **LLD** **6.215**).
    """
    snap = compute_insurance_master_insert_snapshot(
        customer_id,
        vehicle_id,
        fill_values=fill_values,
        staging_payload=staging_payload,
        preview_scrape=preview_scrape,
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

    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO insurance_master (
                        customer_id, vehicle_id, insurance_year,
                        nominee_name, nominee_age, nominee_relationship, nominee_gender,
                        insurer, policy_num, policy_from, policy_to, premium,
                        idv, policy_broker
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(customer_id),
                        int(vehicle_id),
                        insurance_year,
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
            except IntegrityError as exc:
                cname = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
                if cname == "uq_insurance_customer_vehicle_year" or "uq_insurance_customer_vehicle_year" in str(
                    exc
                ):
                    raise ValueError(
                        "Insurance already recorded for this customer, vehicle, and calendar year; "
                        "duplicate insurance_master row is not allowed."
                    ) from exc
                raise
        conn.commit()
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
    params.extend([int(customer_id), int(vehicle_id), insurance_year])

    from app.db import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE insurance_master SET {', '.join(sets)} "
                "WHERE customer_id = %s AND vehicle_id = %s AND insurance_year = %s",
                tuple(params),
            )
            if cur.rowcount == 0:
                logger.warning(
                    "insurance_master policy update after issue: no row for customer_id=%s vehicle_id=%s year=%s",
                    customer_id,
                    vehicle_id,
                    insurance_year,
                )
        conn.commit()
    logger.info(
        "insurance_master update after Issue Policy: customer_id=%s vehicle_id=%s year=%s sets=%s",
        customer_id,
        vehicle_id,
        insurance_year,
        sets,
    )
