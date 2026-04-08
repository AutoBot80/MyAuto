"""vehicle_inventory_master upsert helpers for subdealer challan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_connection


def today_dd_mm_yyyy() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def upsert_from_prepare_vehicle_scrape(
    *,
    to_dealer_id: int,
    vehicle: dict[str, Any],
) -> int:
    """
    Match on trimmed chassis_no / engine_no (frame_num, engine_num, full_chassis, full_engine from scrape).
    On insert set from_company_date = today (dd/mm/yyyy). Do not touch sold_date, yard_id on update.
    Sets dealer_id = to_dealer_id.
    """
    def _t(x: object | None) -> str:
        return (str(x) if x is not None else "").strip()

    chassis = _t(vehicle.get("full_chassis") or vehicle.get("frame_num") or vehicle.get("chassis"))
    engine = _t(vehicle.get("full_engine") or vehicle.get("engine_num") or vehicle.get("engine"))
    if not chassis and not engine:
        raise ValueError("vehicle scrape has no chassis/engine for inventory upsert")

    model = _t(vehicle.get("model"))[:64] or None
    variant = _t(vehicle.get("variant"))[:64] or None
    color = _t(vehicle.get("color") or vehicle.get("colour"))[:64] or None
    cc_raw = vehicle.get("cubic_capacity")
    cc: float | None = None
    if cc_raw is not None and str(cc_raw).strip():
        try:
            cc = float(str(cc_raw).replace(",", "").strip())
        except ValueError:
            cc = None
    vtype = _t(vehicle.get("vehicle_type"))[:32] or None
    batt = _t(vehicle.get("battery") or vehicle.get("battery_num"))[:64] or None
    keyv = _t(vehicle.get("key_num") or vehicle.get("key"))[:64] or None

    today = today_dd_mm_yyyy()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT inventory_line_id
                FROM vehicle_inventory_master
                WHERE TRIM(COALESCE(chassis_no, '')) = %s
                  AND TRIM(COALESCE(engine_no, '')) = %s
                LIMIT 1
                """,
                (chassis, engine),
            )
            ex = cur.fetchone()
            if ex:
                iid = int(ex["inventory_line_id"] if isinstance(ex, dict) else ex[0])
                cur.execute(
                    """
                    UPDATE vehicle_inventory_master SET
                        dealer_id = %s,
                        model = COALESCE(%s, model),
                        variant = COALESCE(%s, variant),
                        color = COALESCE(%s, color),
                        cubic_capacity = COALESCE(%s, cubic_capacity),
                        vehicle_type = COALESCE(%s, vehicle_type),
                        battery = COALESCE(%s, battery),
                        "key" = COALESCE(%s, "key")
                    WHERE inventory_line_id = %s
                    """,
                    (int(to_dealer_id), model, variant, color, cc, vtype, batt, keyv, iid),
                )
                conn.commit()
                return iid

            cur.execute(
                """
                INSERT INTO vehicle_inventory_master (
                    from_company_date, dealer_id, chassis_no, engine_no,
                    model, variant, color, cubic_capacity, vehicle_type, battery, "key"
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING inventory_line_id
                """,
                (today, int(to_dealer_id), chassis, engine, model, variant, color, cc, vtype, batt, keyv),
            )
            row = cur.fetchone()
            iid = int(row["inventory_line_id"] if isinstance(row, dict) else row[0])
            conn.commit()
            return iid
    finally:
        conn.close()


def update_discount_and_ex_showroom(
    inventory_line_id: int,
    *,
    discount: float | None = None,
    ex_showroom_price: float | None = None,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_inventory_master
                SET discount = COALESCE(%s, discount),
                    ex_showroom_price = COALESCE(%s, ex_showroom_price)
                WHERE inventory_line_id = %s
                """,
                (discount, ex_showroom_price, int(inventory_line_id)),
            )
        conn.commit()
    finally:
        conn.close()


def get_discount_for_model(from_dealer_id: int, model: str) -> float | None:
    m = (model or "").strip()[:64]
    if not m:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT discount
                FROM subdealer_discount_master
                WHERE dealer_id = %s
                  AND TRIM(model) = %s
                  AND valid_flag = 'Y'
                ORDER BY subdealer_discount_id DESC
                LIMIT 1
                """,
                (int(from_dealer_id), m),
            )
            r = cur.fetchone()
            if not r:
                return None
            d = r["discount"] if isinstance(r, dict) else r[0]
            if d is None:
                return None
            try:
                return float(d)
            except (TypeError, ValueError):
                return None
    finally:
        conn.close()


def get_by_id(inventory_line_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT inventory_line_id, chassis_no, engine_no, model, ex_showroom_price, discount,
                       dealer_id, variant, color, cubic_capacity, vehicle_type
                FROM vehicle_inventory_master
                WHERE inventory_line_id = %s
                """,
                (int(inventory_line_id),),
            )
            r = cur.fetchone()
            return dict(r) if r else None
    finally:
        conn.close()


def fetch_lines_for_batch_inventory(
    inventory_line_ids: list[int],
) -> list[dict[str, Any]]:
    if not inventory_line_ids:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT inventory_line_id, chassis_no, engine_no, ex_showroom_price, discount, model
                FROM vehicle_inventory_master
                WHERE inventory_line_id = ANY(%s)
                """,
                (inventory_line_ids,),
            )
            return [dict(r) for r in cur.fetchall() or []]
    finally:
        conn.close()
