"""vehicle_inventory_master upsert helpers for subdealer challan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_connection


def today_dd_mm_yyyy() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def update_sold_date_by_chassis_engine_on_cursor(
    cur: Any,
    *,
    chassis: str,
    engine: str,
    sold_date: str | None = None,
) -> int:
    """
    Set ``sold_date`` (dd/mm/yyyy) on ``vehicle_inventory_master`` rows that match trimmed
    ``chassis_no`` and ``engine_no`` (same matching as ``upsert_from_prepare_vehicle_scrape``).

    Skips when chassis or engine is empty after strip. Returns cursor rowcount.
    """
    c = (chassis or "").strip()
    e = (engine or "").strip()
    if not c or not e:
        return 0
    sd = sold_date if sold_date is not None else today_dd_mm_yyyy()
    cur.execute(
        """
        UPDATE vehicle_inventory_master
        SET sold_date = %s
        WHERE TRIM(COALESCE(chassis_no, '')) = %s
          AND TRIM(COALESCE(engine_no, '')) = %s
        """,
        (sd, c, e),
    )
    return int(getattr(cur, "rowcount", -1) or 0)


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


# Subdealer challan: used when `subdealer_discount_master_ref` + `dealer_ref.subdealer_type` (to) do not match.
FALLBACK_SUBDEALER_CHALLAN_DISCOUNT: float = 1500.0


def get_subdealer_challan_discount(
    from_dealer_id: int,
    to_dealer_id: int,
    model: str,
) -> float:
    """
    Resolve discount for a challan line: ``from_dealer_id``/``to_dealer_id``/DMS model string.

    Reads ``dealer_ref.subdealer_type`` for ``to_dealer_id`` and looks up
    ``subdealer_discount_master_ref`` for ``dealer_id = from_dealer_id``,
    matching ``subdealer_type`` and ``valid_flag = 'Y'``. **Model** match is **prefix**:
    the reference row's ``model`` is the start of the DMS value (DMS may have extra trailing
    characters). If several rows match, the **longest** reference ``model`` wins. If no row
    matches or discount is null, returns ``FALLBACK_SUBDEALER_CHALLAN_DISCOUNT`` (1500).
    """
    m = (model or "").strip()[:64]
    if not m:
        return FALLBACK_SUBDEALER_CHALLAN_DISCOUNT

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.discount
                FROM dealer_ref d
                INNER JOIN subdealer_discount_master_ref s
                  ON s.dealer_id = %s
                 AND TRIM(s.subdealer_type) = TRIM(COALESCE(d.subdealer_type, ''))
                 AND s.valid_flag = 'Y'
                 AND BTRIM(s.model) <> ''
                 AND starts_with(BTRIM(%s), BTRIM(s.model))
                WHERE d.dealer_id = %s
                  AND d.subdealer_type IS NOT NULL
                  AND TRIM(COALESCE(d.subdealer_type, '')) <> ''
                ORDER BY LENGTH(BTRIM(s.model)) DESC
                LIMIT 1
                """,
                (int(from_dealer_id), m, int(to_dealer_id)),
            )
            r = cur.fetchone()
            if not r:
                return FALLBACK_SUBDEALER_CHALLAN_DISCOUNT
            d = r["discount"] if isinstance(r, dict) else r[0]
            if d is None:
                return FALLBACK_SUBDEALER_CHALLAN_DISCOUNT
            try:
                return float(d)
            except (TypeError, ValueError):
                return FALLBACK_SUBDEALER_CHALLAN_DISCOUNT
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
