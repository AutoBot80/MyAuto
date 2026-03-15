"""Data access for rto_payment_details table."""

from datetime import date

from app.db import get_connection


def insert(
    application_id: str,
    customer_id: int,
    vehicle_id: int,
    dealer_id: int | None,
    name: str | None,
    mobile: str | None,
    chassis_num: str | None,
    register_date: date,
    rto_fees: float,
    status: str = "Pending",
    pay_txn_id: str | None = None,
    operator_id: str | None = None,
    payment_date: date | None = None,
    rto_status: str = "Registered",
) -> str:
    """Insert a row; returns application_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rto_payment_details
                (application_id, customer_id, vehicle_id, dealer_id, name, mobile, chassis_num,
                 register_date, rto_fees, status, pay_txn_id, operator_id, payment_date, rto_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (application_id) DO UPDATE SET
                  name = EXCLUDED.name,
                  mobile = EXCLUDED.mobile,
                  chassis_num = EXCLUDED.chassis_num,
                  register_date = EXCLUDED.register_date,
                  rto_fees = EXCLUDED.rto_fees,
                  status = EXCLUDED.status
                RETURNING application_id
                """,
                (
                    (application_id or "").strip(),
                    customer_id,
                    vehicle_id,
                    dealer_id,
                    (name or "").strip() or None,
                    (mobile or "").strip() or None,
                    (chassis_num or "").strip() or None,
                    register_date,
                    rto_fees,
                    (status or "Pending").strip(),
                    (pay_txn_id or "").strip() or None,
                    (operator_id or "").strip() or None,
                    payment_date,
                    (rto_status or "Registered").strip(),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row["application_id"]
    finally:
        conn.close()


def list_all() -> list[dict]:
    """Return all rows for RTO Payments Pending table, newest first."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT application_id, customer_id, vehicle_id, dealer_id, name, mobile, chassis_num,
                       register_date, rto_fees, status, pay_txn_id, operator_id, payment_date, rto_status, created_at
                FROM rto_payment_details
                ORDER BY created_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
