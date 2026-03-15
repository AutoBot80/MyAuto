"""Data access for rto_payment_details table."""

from datetime import date

from app.db import get_connection


def _get_sales_id(customer_id: int, vehicle_id: int) -> int | None:
    """Resolve sales_id from sales_master for (customer_id, vehicle_id)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sales_id FROM sales_master WHERE customer_id = %s AND vehicle_id = %s",
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return row["sales_id"] if row else None
    finally:
        conn.close()


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
    subfolder: str | None = None,
) -> str:
    """Insert a row; returns application_id. Resolves sales_id from sales_master."""
    sales_id = _get_sales_id(customer_id, vehicle_id)
    if sales_id is None:
        raise ValueError(f"No sale found for customer_id={customer_id} vehicle_id={vehicle_id}")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rto_payment_details
                (application_id, sales_id, customer_id, vehicle_id, dealer_id, name, mobile, chassis_num,
                 register_date, rto_fees, status, pay_txn_id, operator_id, payment_date, rto_status, subfolder)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sales_id) DO UPDATE SET
                  application_id = EXCLUDED.application_id,
                  dealer_id = EXCLUDED.dealer_id,
                  name = EXCLUDED.name,
                  mobile = EXCLUDED.mobile,
                  chassis_num = EXCLUDED.chassis_num,
                  register_date = EXCLUDED.register_date,
                  rto_fees = EXCLUDED.rto_fees,
                  status = EXCLUDED.status,
                  subfolder = COALESCE(EXCLUDED.subfolder, rto_payment_details.subfolder)
                RETURNING application_id
                """,
                (
                    (application_id or "").strip(),
                    sales_id,
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
                    (subfolder or "").strip() or None,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row["application_id"]
    finally:
        conn.close()


def get_by_customer_vehicle(customer_id: int, vehicle_id: int) -> dict | None:
    """Get one row by (customer_id, vehicle_id)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT application_id, sales_id, customer_id, vehicle_id, dealer_id, name, mobile, chassis_num,
                       register_date, rto_fees, status, pay_txn_id, operator_id, payment_date, rto_status, subfolder, created_at
                FROM rto_payment_details
                WHERE customer_id = %s AND vehicle_id = %s
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_by_application_id(application_id: str) -> dict | None:
    """Get one row by application_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT application_id, sales_id, customer_id, vehicle_id, dealer_id, name, mobile, chassis_num,
                       register_date, rto_fees, status, pay_txn_id, operator_id, payment_date, rto_status, subfolder, created_at
                FROM rto_payment_details
                WHERE application_id = %s
                """,
                ((application_id or "").strip(),),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_payment(
    application_id: str,
    pay_txn_id: str,
    payment_date: date,
    status: str = "Paid",
) -> bool:
    """Update payment fields after Pay completes."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_payment_details
                SET pay_txn_id = %s, payment_date = %s, status = %s
                WHERE application_id = %s
                """,
                ((pay_txn_id or "").strip(), payment_date, (status or "Paid").strip(), (application_id or "").strip()),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def list_all() -> list[dict]:
    """Return all rows for RTO Payments Pending table, newest first."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT application_id, sales_id, customer_id, vehicle_id, dealer_id, name, mobile, chassis_num,
                       register_date, rto_fees, status, pay_txn_id, operator_id, payment_date, rto_status, subfolder, created_at
                FROM rto_payment_details
                ORDER BY created_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
