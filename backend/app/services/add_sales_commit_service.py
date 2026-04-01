"""
Commit customer_master, vehicle_master, sales_master from Add Sales staging payload
after successful Create Invoice (DMS). ``insert_insurance_master_after_gi`` runs after
successful Generate Insurance (plain INSERT; duplicate ``(customer_id, vehicle_id, insurance_year)``
fails). ``update_insurance_master_policy_after_issue`` refreshes ``policy_num`` / ``insurance_cost``
after **Issue Policy** is clicked and values are scraped.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
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


def insert_insurance_master_after_gi(
    customer_id: int,
    vehicle_id: int,
    *,
    fill_values: dict[str, Any],
    staging_payload: dict[str, Any] | None = None,
    preview_policy_num: str | None = None,
    preview_insurance_cost: float | None = None,
) -> None:
    """
    INSERT ``insurance_master`` for the current calendar ``insurance_year`` after proposal review.
    Insurer / nominee fields prefer the MISP fill dict; policy # and ``insurance_cost`` prefer
    preview scrape (before Issue Policy); remaining policy fields from staging ``insurance`` when
    present. Raises ``ValueError`` if a row already exists for the same customer, vehicle, and year
    (``uq_insurance_customer_vehicle_year``). After **Issue Policy**, call
    ``update_insurance_master_policy_after_issue`` with scraped values.
    """
    ins_staging: dict[str, Any] = {}
    if staging_payload and isinstance(staging_payload.get("insurance"), dict):
        ins_staging = dict(staging_payload["insurance"])

    insurer = _str_or_none(fill_values.get("insurer"), 255) or _str_or_none(ins_staging.get("insurer"), 255)
    nominee_name = _str_or_none(fill_values.get("nominee_name")) or _str_or_none(ins_staging.get("nominee_name"))
    nominee_relationship = _str_or_none(fill_values.get("nominee_relationship"), 64) or _str_or_none(
        ins_staging.get("nominee_relationship"), 64
    )
    nominee_raw = fill_values.get("nominee_age")
    if nominee_raw is None or (isinstance(nominee_raw, str) and not str(nominee_raw).strip()):
        nominee_raw = ins_staging.get("nominee_age")
    nominee_age = _int_or_none(nominee_raw)
    nominee_gender = _str_or_none(fill_values.get("nominee_gender"), 16) or _str_or_none(
        ins_staging.get("nominee_gender"), 16
    )

    policy_num = _str_or_none(preview_policy_num, 24) or _str_or_none(ins_staging.get("policy_num"), 24)
    policy_from_d = _parse_date_loose(_str_or_none(ins_staging.get("policy_from"), 20))
    policy_to_d = _parse_date_loose(_str_or_none(ins_staging.get("policy_to"), 20))
    premium = None
    if ins_staging.get("premium") is not None:
        try:
            premium = float(str(ins_staging.get("premium")).replace(",", "").strip())
        except (TypeError, ValueError):
            premium = None
    idv_f = None
    if ins_staging.get("idv") is not None:
        try:
            idv_f = float(str(ins_staging.get("idv")).replace(",", "").strip())
        except (TypeError, ValueError):
            idv_f = None
    policy_broker = _str_or_none(ins_staging.get("policy_broker"), 255)

    insurance_cost_f: float | None = preview_insurance_cost
    if insurance_cost_f is None and ins_staging.get("insurance_cost") is not None:
        try:
            insurance_cost_f = float(str(ins_staging.get("insurance_cost")).replace(",", "").strip())
        except (TypeError, ValueError):
            insurance_cost_f = None

    insurance_year = date.today().year

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
                        idv, policy_broker, insurance_cost
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        insurance_cost_f,
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
        "insurance_master insert after GI: customer_id=%s vehicle_id=%s year=%s policy_num=%r insurance_cost=%s",
        customer_id,
        vehicle_id,
        insurance_year,
        policy_num,
        insurance_cost_f,
    )


def update_insurance_master_policy_after_issue(
    customer_id: int,
    vehicle_id: int,
    *,
    policy_num: str | None,
    insurance_cost: float | None,
) -> None:
    """UPDATE ``policy_num`` / ``insurance_cost`` for the current ``insurance_year`` after Issue Policy."""
    if policy_num is None and insurance_cost is None:
        return
    insurance_year = date.today().year
    sets: list[str] = []
    params: list[Any] = []
    pn = _str_or_none(policy_num, 24) if policy_num is not None else None
    if pn is not None:
        sets.append("policy_num = %s")
        params.append(pn)
    if insurance_cost is not None:
        sets.append("insurance_cost = %s")
        params.append(float(insurance_cost))
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
        "insurance_master update after Issue Policy: customer_id=%s vehicle_id=%s year=%s policy_num=%r insurance_cost=%s",
        customer_id,
        vehicle_id,
        insurance_year,
        policy_num,
        insurance_cost,
    )
