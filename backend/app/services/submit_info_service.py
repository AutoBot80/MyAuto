"""
Submit Info: upsert customer_master, vehicle_master, sales_master, insurance_master
in one transaction. Identity: customer by (aadhar last 4, mobile_number); vehicle by
(raw_frame_num, raw_engine_num, raw_key_num); sales by (customer_id, vehicle_id);
insurance by (customer_id, vehicle_id, insurance_year).
"""

from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.db import get_connection


def _parse_date(s: str | None) -> date | None:
    """Parse dd-mm-yyyy, dd/mm/yyyy, or yyyy-mm-dd to date."""
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _last4(aadhar_id: str | None) -> str | None:
    if not aadhar_id or not str(aadhar_id).strip():
        return None
    digits = "".join(c for c in str(aadhar_id) if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits or None


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


def submit_info(
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    insurance: dict[str, Any],
    dealer_id: int | None,
    file_location: str | None = None,
) -> dict[str, Any]:
    """
    Upsert customer, vehicle, sales, insurance in one transaction.
    Returns { "customer_id", "vehicle_id", "ok": True } or raises.
    """
    aadhar_last4 = _last4(customer.get("aadhar_id"))
    mobile = _int_or_none(customer.get("mobile_number"))
    if not aadhar_last4 or mobile is None:
        raise ValueError("Customer aadhar (last 4) and mobile_number are required")

    loc = _str_or_none(file_location) or _str_or_none(customer.get("file_location"))
    if loc:
        from app.config import DEALER_ID, get_ocr_output_dir
        from app.services.ocr_service import validate_name_match_for_subfolder

        ocr_dir = get_ocr_output_dir(dealer_id if dealer_id is not None else DEALER_ID)
        name_err = validate_name_match_for_subfolder(Path(ocr_dir).resolve(), loc)
        if name_err:
            raise ValueError(name_err)

    name = _str_or_none(customer.get("name")) or ""
    address = _str_or_none(customer.get("address"))
    pin = _str_or_none(customer.get("pin"), 6)
    city = _str_or_none(customer.get("city"))
    state = _str_or_none(customer.get("state"))
    gender = _str_or_none(customer.get("gender"), 8)
    date_of_birth = _str_or_none(customer.get("date_of_birth"), 20)
    profession = _str_or_none(customer.get("profession"), 16)

    frame_no = _str_or_none(vehicle.get("frame_no"), 64)
    engine_no = _str_or_none(vehicle.get("engine_no"), 64)
    key_no = _str_or_none(vehicle.get("key_no"), 32)
    battery_no = _str_or_none(vehicle.get("battery_no"), 64)

    raw_f = frame_no or None
    raw_e = engine_no or None
    raw_k = key_no or None

    nominee_name = _str_or_none(insurance.get("nominee_name"))
    nominee_age_raw = insurance.get("nominee_age")
    nominee_age = _int_or_none(nominee_age_raw)
    if nominee_age_raw is not None and str(nominee_age_raw).strip() and nominee_age is None:
        raise ValueError("Nominee Age must be a number (1–150)")
    if nominee_age is not None and (nominee_age < 1 or nominee_age > 150):
        raise ValueError("Nominee Age must be between 1 and 150")
    nominee_relationship = _str_or_none(insurance.get("nominee_relationship"), 64)
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

    policy_from = _parse_date(policy_from_str)
    policy_to = _parse_date(policy_to_str)

    insurance_year = date.today().year

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1) Customer upsert by (aadhar, mobile_number)
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
                        gender = %s, date_of_birth = %s, profession = %s, file_location = %s
                    WHERE customer_id = %s
                    """,
                    (name, address, pin, city, state, gender, date_of_birth, profession, loc, customer_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO customer_master (aadhar, name, address, pin, city, state, mobile_number, profession, file_location, gender, date_of_birth)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING customer_id
                    """,
                    (aadhar_last4, name, address, pin, city, state, mobile, profession, loc, gender, date_of_birth),
                )
                customer_id = cur.fetchone()["customer_id"]

            # 2) Vehicle upsert by (raw_frame_num, raw_engine_num, raw_key_num)
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
                    (frame_no, engine_no, key_no, battery_no, raw_f, raw_e, raw_k, vehicle_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO vehicle_master (chassis, engine, key_num, battery, raw_frame_num, raw_engine_num, raw_key_num)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING vehicle_id
                    """,
                    (frame_no, engine_no, key_no, battery_no, raw_f, raw_e, raw_k),
                )
                vehicle_id = cur.fetchone()["vehicle_id"]

            # 3) Sales upsert: keep original billing_date on conflict
            cur.execute(
                """
                ALTER TABLE sales_master
                ADD COLUMN IF NOT EXISTS file_location VARCHAR(128)
                """
            )
            cur.execute(
                """
                INSERT INTO sales_master (customer_id, vehicle_id, dealer_id, file_location)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (customer_id, vehicle_id) DO UPDATE SET
                    dealer_id = EXCLUDED.dealer_id,
                    file_location = COALESCE(EXCLUDED.file_location, sales_master.file_location)
                """,
                (customer_id, vehicle_id, dealer_id, loc),
            )

            # 4) Insurance upsert by (customer_id, vehicle_id, insurance_year)
            cur.execute(
                """
                INSERT INTO insurance_master (customer_id, vehicle_id, insurance_year, nominee_name, nominee_age, nominee_relationship, insurer, policy_num, policy_from, policy_to, premium)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id, vehicle_id, insurance_year)
                DO UPDATE SET
                    nominee_name = EXCLUDED.nominee_name,
                    nominee_age = EXCLUDED.nominee_age,
                    nominee_relationship = EXCLUDED.nominee_relationship,
                    insurer = COALESCE(EXCLUDED.insurer, insurance_master.insurer),
                    policy_num = COALESCE(EXCLUDED.policy_num, insurance_master.policy_num),
                    policy_from = COALESCE(EXCLUDED.policy_from, insurance_master.policy_from),
                    policy_to = COALESCE(EXCLUDED.policy_to, insurance_master.policy_to),
                    premium = COALESCE(EXCLUDED.premium, insurance_master.premium)
                """,
                (customer_id, vehicle_id, insurance_year, nominee_name, nominee_age, nominee_relationship, insurer, policy_num, policy_from, policy_to, premium),
            )

        conn.commit()

    return {"ok": True, "customer_id": customer_id, "vehicle_id": vehicle_id}
