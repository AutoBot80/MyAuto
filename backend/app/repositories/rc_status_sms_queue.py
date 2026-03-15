"""Data access for rc_status_sms_queue table."""

from app.db import get_connection


def insert(
    sales_id: int,
    dealer_id: int | None,
    vehicle_id: int,
    customer_id: int,
    customer_mobile: str | None,
    message_type: str,
    sms_status: str = "Pending",
) -> int:
    """Insert a row; returns id. sales_id and dealer_id validated via sales_master."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rc_status_sms_queue (sales_id, dealer_id, vehicle_id, customer_id, customer_mobile, message_type, sms_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    sales_id,
                    dealer_id,
                    vehicle_id,
                    customer_id,
                    (customer_mobile or "").strip() or None,
                    (message_type or "").strip(),
                    (sms_status or "Pending").strip(),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row["id"]
    finally:
        conn.close()
