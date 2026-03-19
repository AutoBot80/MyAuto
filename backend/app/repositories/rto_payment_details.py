"""Data access for rto_queue table."""

from datetime import date, datetime, timezone

from app.db import get_connection

SELECT_COLUMNS = """
application_id,
sales_id,
customer_id,
vehicle_id,
dealer_id,
name,
mobile,
chassis_num,
vahan_application_id,
register_date,
rto_fees,
status,
pay_txn_id,
operator_id,
payment_date,
rto_status,
subfolder,
processing_session_id,
worker_id,
leased_until,
attempt_count,
last_error,
started_at,
uploaded_at,
finished_at,
created_at,
updated_at
""".strip()


def _make_queue_id(customer_id: int, vehicle_id: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"RQ-{stamp}-{customer_id}-{vehicle_id}"


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
    application_id: str | None,
    customer_id: int,
    vehicle_id: int,
    dealer_id: int | None,
    name: str | None,
    mobile: str | None,
    chassis_num: str | None,
    register_date: date,
    rto_fees: float,
    status: str = "Queued",
    pay_txn_id: str | None = None,
    operator_id: str | None = None,
    payment_date: date | None = None,
    rto_status: str = "Pending",
    subfolder: str | None = None,
) -> str:
    """Insert a queue row; returns the queue/reference id. Resolves sales_id from sales_master."""
    sales_id = _get_sales_id(customer_id, vehicle_id)
    if sales_id is None:
        raise ValueError(f"No sale found for customer_id={customer_id} vehicle_id={vehicle_id}")
    queue_id = (application_id or "").strip() or _make_queue_id(customer_id, vehicle_id)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rto_queue
                (
                    application_id,
                    sales_id,
                    customer_id,
                    vehicle_id,
                    dealer_id,
                    name,
                    mobile,
                    chassis_num,
                    register_date,
                    rto_fees,
                    status,
                    pay_txn_id,
                    operator_id,
                    payment_date,
                    rto_status,
                    subfolder,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (sales_id) DO UPDATE SET
                  application_id = EXCLUDED.application_id,
                  dealer_id = EXCLUDED.dealer_id,
                  name = EXCLUDED.name,
                  mobile = EXCLUDED.mobile,
                  chassis_num = EXCLUDED.chassis_num,
                  register_date = EXCLUDED.register_date,
                  rto_fees = EXCLUDED.rto_fees,
                  status = EXCLUDED.status,
                  rto_status = EXCLUDED.rto_status,
                  subfolder = COALESCE(EXCLUDED.subfolder, rto_queue.subfolder),
                  last_error = NULL,
                  leased_until = NULL,
                  processing_session_id = NULL,
                  worker_id = NULL,
                  updated_at = NOW()
                RETURNING application_id
                """,
                (
                    queue_id,
                    sales_id,
                    customer_id,
                    vehicle_id,
                    dealer_id,
                    (name or "").strip() or None,
                    (mobile or "").strip() or None,
                    (chassis_num or "").strip() or None,
                    register_date,
                    rto_fees,
                    (status or "Queued").strip(),
                    (pay_txn_id or "").strip() or None,
                    (operator_id or "").strip() or None,
                    payment_date,
                    (rto_status or "Pending").strip(),
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
                f"""
                SELECT {SELECT_COLUMNS}
                FROM rto_queue
                WHERE customer_id = %s AND vehicle_id = %s
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_by_application_id(application_id: str) -> dict | None:
    """Get one row by queue/application id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {SELECT_COLUMNS}
                FROM rto_queue
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
                UPDATE rto_queue
                SET pay_txn_id = %s,
                    payment_date = %s,
                    status = %s,
                    rto_status = 'Paid',
                    updated_at = NOW()
                WHERE application_id = %s
                """,
                ((pay_txn_id or "").strip(), payment_date, (status or "Paid").strip(), (application_id or "").strip()),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def claim_oldest_batch(
    dealer_id: int,
    processing_session_id: str,
    worker_id: str,
    operator_id: str | None,
    *,
    limit: int = 7,
    lease_seconds: int = 1800,
) -> list[dict]:
    """Claim the oldest queued rows for one dealer."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Pending',
                    rto_status = 'Pending',
                    leased_until = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = COALESCE(last_error, 'Session expired or lease timed out'),
                    updated_at = NOW()
                WHERE dealer_id = %s
                  AND status = 'In Progress'
                  AND leased_until IS NOT NULL
                  AND leased_until < NOW()
                """,
                (dealer_id,),
            )
            cur.execute(
                """
                WITH candidates AS (
                    SELECT application_id
                    FROM rto_queue
                    WHERE dealer_id = %s
                      AND status IN ('Queued', 'Pending')
                      AND COALESCE(NULLIF(BTRIM(pay_txn_id), ''), NULL) IS NULL
                      AND payment_date IS NULL
                      AND (leased_until IS NULL OR leased_until < NOW())
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE rto_queue q
                SET processing_session_id = %s,
                    worker_id = %s,
                    operator_id = COALESCE(%s, q.operator_id),
                    leased_until = NOW() + make_interval(secs => %s),
                    attempt_count = COALESCE(q.attempt_count, 0) + 1,
                    last_error = NULL,
                    updated_at = NOW()
                FROM candidates
                WHERE q.application_id = candidates.application_id
                RETURNING q.application_id
                """,
                (dealer_id, limit, processing_session_id, worker_id, (operator_id or "").strip() or None, lease_seconds),
            )
            claimed_ids = [row["application_id"] for row in cur.fetchall()]
            if not claimed_ids:
                conn.commit()
                return []
            cur.execute(
                f"""
                SELECT {SELECT_COLUMNS}
                FROM rto_queue
                WHERE processing_session_id = %s
                  AND application_id = ANY(%s)
                ORDER BY created_at ASC
                """,
                (processing_session_id, claimed_ids),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
            return rows
    finally:
        conn.close()


def mark_batch_row_in_progress(queue_id: str, processing_session_id: str, worker_id: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'In Progress',
                    rto_status = 'Processing',
                    started_at = COALESCE(started_at, NOW()),
                    leased_until = NOW() + make_interval(secs => 1800),
                    processing_session_id = %s,
                    worker_id = %s,
                    updated_at = NOW()
                WHERE application_id = %s
                """,
                (processing_session_id, worker_id, (queue_id or "").strip()),
            )
            conn.commit()
    finally:
        conn.close()


def mark_batch_row_cart_added(
    queue_id: str,
    sales_id: int,
    processing_session_id: str,
    worker_id: str,
    *,
    vahan_application_id: str | None,
    rto_fees: float | None,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Added To Cart',
                    rto_status = 'Files Uploaded',
                    vahan_application_id = %s,
                    rto_fees = %s,
                    uploaded_at = NOW(),
                    finished_at = NOW(),
                    leased_until = NULL,
                    processing_session_id = %s,
                    worker_id = %s,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE application_id = %s
                """,
                (vahan_application_id, rto_fees, processing_session_id, worker_id, (queue_id or "").strip()),
            )
            cur.execute(
                """
                UPDATE sales_master
                SET vahan_application_id = %s,
                    rto_charges = %s
                WHERE sales_id = %s
                """,
                (vahan_application_id, rto_fees, sales_id),
            )
            conn.commit()
    finally:
        conn.close()


def mark_batch_row_failed(queue_id: str, processing_session_id: str, worker_id: str, error_message: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Failed',
                    rto_status = 'Failed',
                    finished_at = NOW(),
                    leased_until = NULL,
                    processing_session_id = %s,
                    worker_id = %s,
                    last_error = %s,
                    updated_at = NOW()
                WHERE application_id = %s
                """,
                (processing_session_id, worker_id, (error_message or "").strip()[:4000], (queue_id or "").strip()),
            )
            conn.commit()
    finally:
        conn.close()


def mark_batch_row_pending(
    queue_id: str,
    processing_session_id: str,
    worker_id: str,
    error_message: str | None = None,
) -> None:
    """Return a row to Pending after a retryable/session-expiry failure."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Pending',
                    rto_status = 'Pending',
                    leased_until = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = %s,
                    updated_at = NOW()
                WHERE application_id = %s
                """,
                (((error_message or "").strip() or None), (queue_id or "").strip()),
            )
            conn.commit()
    finally:
        conn.close()


def release_batch_claims(processing_session_id: str) -> int:
    """Release rows from a dead session back to Pending."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = CASE WHEN status = 'In Progress' THEN 'Pending' ELSE status END,
                    rto_status = CASE WHEN status = 'In Progress' THEN 'Pending' ELSE rto_status END,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    leased_until = NULL,
                    last_error = COALESCE(last_error, 'Session released before completion'),
                    updated_at = NOW()
                WHERE processing_session_id = %s
                """,
                ((processing_session_id or "").strip(),),
            )
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()


def retry_failed_row(application_id: str) -> bool:
    """Set one failed queue row back to Queued for operator retry."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Queued',
                    rto_status = 'Pending',
                    leased_until = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE application_id = %s
                  AND status = 'Failed'
                """,
                ((application_id or "").strip(),),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def list_all(dealer_id: int | None = None) -> list[dict]:
    """Return rows for RTO Queue, newest first. Filter by dealer_id when provided."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    SELECT {SELECT_COLUMNS}
                    FROM rto_queue
                    WHERE dealer_id = %s
                    ORDER BY created_at DESC
                    """,
                    (dealer_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {SELECT_COLUMNS}
                    FROM rto_queue
                    ORDER BY created_at DESC
                    """
                )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
