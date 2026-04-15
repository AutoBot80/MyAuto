"""Resolve ``customer_id`` / ``vehicle_id`` from the same natural keys as ``/add-sales/create-invoice-eligibility``."""

from __future__ import annotations

from typing import Any

from app.db import get_connection


def _digits_mobile(mobile: str) -> int | None:
    digits = "".join(c for c in (mobile or "") if c.isdigit())
    if len(digits) >= 10:
        return int(digits[-10:])
    if len(digits) > 0:
        return int(digits)
    return None


def resolve_customer_vehicle_ids_by_natural_keys(
    chassis_num: str,
    engine_num: str,
    mobile: str,
) -> tuple[int | None, int | None]:
    """
    Match ``vehicle_master`` by trimmed frame/engine (same LIKE rules as eligibility) and
    ``customer_master`` by 10-digit mobile; return ``(customer_id, vehicle_id)`` when both exist.
    """
    ch = (chassis_num or "").strip()
    eng = (engine_num or "").strip()
    mob_i = _digits_mobile(mobile)
    if not ch or not eng or mob_i is None:
        return None, None

    conn = get_connection()
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
    finally:
        conn.close()

    if not vrow or not crow:
        return None, None
    return int(crow["customer_id"]), int(vrow["vehicle_id"])


def natural_keys_from_staging_payload(payload: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """
    Extract chassis, engine, and mobile string from ``add_sales_staging.payload_json``-shaped dict.
    Returns ``None`` if any part is missing after trim.
    """
    if not payload or not isinstance(payload, dict):
        return None
    cust = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    veh = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
    mobile_raw = cust.get("mobile_number") if cust.get("mobile_number") is not None else cust.get("mobile")
    mob = str(mobile_raw or "").strip()
    ch = str(
        veh.get("frame_no")
        or veh.get("full_chassis")
        or veh.get("frame_num")
        or veh.get("full_frame")
        or ""
    ).strip()
    eng = str(
        veh.get("engine_no")
        or veh.get("full_engine")
        or veh.get("engine_num")
        or ""
    ).strip()
    if not mob or not ch or not eng:
        return None
    return ch, eng, mob
