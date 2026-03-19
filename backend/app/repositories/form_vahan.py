"""Read helper for the form_vahan_view database view."""

from app.db import get_connection


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
