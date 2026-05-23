"""Read helper for the form_vahan_view database view."""

from datetime import date, datetime

from app.db import get_connection

VAHAN_DISPLAY_KEYS = (
    "dealer_name",
    "rto",
    "billing_date",
    "model",
    "chassis",
    "engine",
)


def _json_safe_value(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return val


def get_by_customer_vehicle(customer_id: int, vehicle_id: int) -> dict | None:
    """Return the Vahan form row for one submitted sale."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM form_vahan_view
                WHERE customer_id = %s AND vehicle_id = %s
                ORDER BY sales_id DESC
                LIMIT 1
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_by_sales_id(sales_id: int) -> dict | None:
    """Return the Vahan form row by sales_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM form_vahan_view
                WHERE sales_id = %s
                """,
                (sales_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_display_row_by_customer_vehicle(customer_id: int, vehicle_id: int) -> dict | None:
    """Six-column View Customer Vahan row (dealer, RTO, billing, model, chassis, engine)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(dr.dealer_name, '') AS dealer_name,
                    COALESCE(dr.rto_name, 'RTO' || sm.dealer_id::text) AS rto,
                    sm.billing_date,
                    COALESCE(
                        NULLIF(BTRIM(vm.model), ''),
                        NULLIF(BTRIM(vm.vehicle_type), ''),
                        ''
                    ) AS model,
                    COALESCE(vm.chassis, vm.raw_frame_num) AS chassis,
                    COALESCE(vm.engine, vm.raw_engine_num) AS engine
                FROM rto_queue rq
                JOIN sales_master sm ON sm.sales_id = rq.sales_id
                LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
                WHERE sm.customer_id = %s AND sm.vehicle_id = %s
                ORDER BY rq.sales_id DESC
                LIMIT 1
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            raw = dict(row)
            return {k: _json_safe_value(raw.get(k)) for k in VAHAN_DISPLAY_KEYS}
    finally:
        conn.close()
