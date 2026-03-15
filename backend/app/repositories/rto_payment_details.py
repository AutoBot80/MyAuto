"""Data access for rto_payment_details table."""

from datetime import date

from app.db import get_connection


def insert(
    customer_id: int,
    name: str | None,
    mobile: str | None,
    chassis_num: str | None,
    application_num: str,
    submission_date: date,
    rto_payment_due: float,
    status: str = "Pending",
    pos_mgr_id: str | None = None,
    txn_id: str | None = None,
    payment_date: date | None = None,
) -> int:
    """Insert a row; returns id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rto_payment_details
                (customer_id, name, mobile, chassis_num, application_num, submission_date,
                 rto_payment_due, status, pos_mgr_id, txn_id, payment_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    customer_id,
                    (name or "").strip() or None,
                    (mobile or "").strip() or None,
                    (chassis_num or "").strip() or None,
                    (application_num or "").strip(),
                    submission_date,
                    rto_payment_due,
                    (status or "Pending").strip(),
                    (pos_mgr_id or "").strip() or None,
                    (txn_id or "").strip() or None,
                    payment_date,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row["id"]
    finally:
        conn.close()


def list_all() -> list[dict]:
    """Return all rows for RTO Payments Pending table, newest first."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, customer_id, name, mobile, chassis_num, application_num,
                       submission_date, rto_payment_due, status, pos_mgr_id, txn_id, payment_date, created_at
                FROM rto_payment_details
                ORDER BY created_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
