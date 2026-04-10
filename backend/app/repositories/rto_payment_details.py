"""Data access for rto_queue table (redesigned schema: rto_queue_id serial PK)."""

from datetime import date, datetime, timezone

from app.db import get_connection

SELECT_COLUMNS = """
rq.rto_queue_id,
rq.sales_id,
rq.insurance_id,
rq.customer_mobile,
rq.rto_application_id,
rq.rto_application_date,
rq.rto_payment_id,
rq.rto_payment_amount,
rq.status,
rq.processing_session_id,
rq.worker_id,
rq.leased_until,
rq.attempt_count,
rq.last_error,
rq.started_at,
rq.uploaded_at,
rq.finished_at,
rq.created_at,
rq.updated_at
""".strip()

JOINED_COLUMNS = f"""
{SELECT_COLUMNS},
sm.customer_id,
sm.vehicle_id,
sm.dealer_id,
sm.billing_date,
cm.name AS customer_name,
cm.mobile_number AS mobile,
cm.care_of,
cm.address,
cm.city,
cm.state,
cm.pin,
cm.financier,
COALESCE(vm.chassis, vm.raw_frame_num) AS chassis_num,
RIGHT(COALESCE(vm.engine, vm.raw_engine_num, ''), 5) AS engine_short,
COALESCE(sm.file_location, cm.file_location) AS subfolder,
COALESCE(dr.rto_name, 'RTO' || sm.dealer_id::text) AS dealer_rto,
im.insurer,
im.policy_num,
im.policy_from,
im.idv
""".strip()

JOIN_CLAUSE = """
FROM rto_queue rq
JOIN sales_master sm ON sm.sales_id = rq.sales_id
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
LEFT JOIN insurance_master im ON im.insurance_id = rq.insurance_id
""".strip()


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
    sales_id: int,
    insurance_id: int | None = None,
    customer_mobile: str | None = None,
    rto_application_date: date | None = None,
    rto_payment_amount: float | None = None,
    status: str = "Queued",
) -> int:
    """Insert a queue row; returns rto_queue_id. Upserts on sales_id conflict."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rto_queue
                (
                    sales_id,
                    insurance_id,
                    customer_mobile,
                    rto_application_date,
                    rto_payment_amount,
                    status,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (sales_id) DO UPDATE SET
                  insurance_id = COALESCE(EXCLUDED.insurance_id, rto_queue.insurance_id),
                  customer_mobile = COALESCE(EXCLUDED.customer_mobile, rto_queue.customer_mobile),
                  rto_application_date = COALESCE(EXCLUDED.rto_application_date, rto_queue.rto_application_date),
                  rto_payment_amount = COALESCE(EXCLUDED.rto_payment_amount, rto_queue.rto_payment_amount),
                  status = EXCLUDED.status,
                  last_error = NULL,
                  leased_until = NULL,
                  processing_session_id = NULL,
                  worker_id = NULL,
                  updated_at = NOW()
                RETURNING rto_queue_id
                """,
                (
                    sales_id,
                    insurance_id,
                    (customer_mobile or "").strip() or None,
                    rto_application_date,
                    rto_payment_amount,
                    (status or "Queued").strip(),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row["rto_queue_id"]
    finally:
        conn.close()


def get_by_customer_vehicle(customer_id: int, vehicle_id: int) -> dict | None:
    """Get one row by (customer_id, vehicle_id) via sales_master join."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {JOINED_COLUMNS}
                {JOIN_CLAUSE}
                WHERE sm.customer_id = %s AND sm.vehicle_id = %s
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_by_queue_id(rto_queue_id: int) -> dict | None:
    """Get one row by rto_queue_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {JOINED_COLUMNS}
                {JOIN_CLAUSE}
                WHERE rq.rto_queue_id = %s
                """,
                (rto_queue_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_by_sales_id(sales_id: int) -> dict | None:
    """Get one row by sales_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {JOINED_COLUMNS}
                {JOIN_CLAUSE}
                WHERE rq.sales_id = %s
                """,
                (sales_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_payment(
    rto_queue_id: int,
    rto_payment_id: str,
    rto_payment_amount: float | None = None,
    status: str = "Paid",
) -> bool:
    """Update payment fields after Pay completes."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET rto_payment_id = %s,
                    rto_payment_amount = COALESCE(%s, rto_payment_amount),
                    status = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (
                    (rto_payment_id or "").strip(),
                    rto_payment_amount,
                    (status or "Paid").strip(),
                    rto_queue_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def claim_oldest_batch(
    dealer_id: int,
    processing_session_id: str,
    worker_id: str,
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
                    leased_until = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = COALESCE(last_error, 'Session expired or lease timed out'),
                    updated_at = NOW()
                WHERE rto_queue_id IN (
                    SELECT rq.rto_queue_id
                    FROM rto_queue rq
                    JOIN sales_master sm ON sm.sales_id = rq.sales_id
                    WHERE sm.dealer_id = %s
                      AND rq.status = 'In Progress'
                      AND rq.leased_until IS NOT NULL
                      AND rq.leased_until < NOW()
                )
                """,
                (dealer_id,),
            )
            cur.execute(
                """
                WITH candidates AS (
                    SELECT rq.rto_queue_id
                    FROM rto_queue rq
                    JOIN sales_master sm ON sm.sales_id = rq.sales_id
                    WHERE sm.dealer_id = %s
                      AND rq.status IN ('Queued', 'Pending')
                      AND rq.rto_payment_id IS NULL
                      AND (rq.leased_until IS NULL OR rq.leased_until < NOW())
                    ORDER BY rq.created_at ASC
                    LIMIT %s
                    FOR UPDATE OF rq SKIP LOCKED
                )
                UPDATE rto_queue q
                SET processing_session_id = %s,
                    worker_id = %s,
                    leased_until = NOW() + make_interval(secs => %s),
                    attempt_count = COALESCE(q.attempt_count, 0) + 1,
                    last_error = NULL,
                    updated_at = NOW()
                FROM candidates
                WHERE q.rto_queue_id = candidates.rto_queue_id
                RETURNING q.rto_queue_id
                """,
                (dealer_id, limit, processing_session_id, worker_id, lease_seconds),
            )
            claimed_ids = [row["rto_queue_id"] for row in cur.fetchall()]
            if not claimed_ids:
                conn.commit()
                return []
            cur.execute(
                f"""
                SELECT {JOINED_COLUMNS}
                {JOIN_CLAUSE}
                WHERE rq.processing_session_id = %s
                  AND rq.rto_queue_id = ANY(%s)
                ORDER BY rq.created_at ASC
                """,
                (processing_session_id, claimed_ids),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
            return rows
    finally:
        conn.close()


def mark_batch_row_in_progress(rto_queue_id: int, processing_session_id: str, worker_id: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'In Progress',
                    started_at = COALESCE(started_at, NOW()),
                    leased_until = NOW() + make_interval(secs => 1800),
                    processing_session_id = %s,
                    worker_id = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (processing_session_id, worker_id, rto_queue_id),
            )
            conn.commit()
    finally:
        conn.close()


def mark_batch_row_completed(
    rto_queue_id: int,
    sales_id: int,
    processing_session_id: str,
    worker_id: str,
    *,
    rto_application_id: str | None,
    rto_payment_amount: float | None,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Completed',
                    rto_application_id = %s,
                    rto_payment_amount = %s,
                    uploaded_at = NOW(),
                    finished_at = NOW(),
                    leased_until = NULL,
                    processing_session_id = %s,
                    worker_id = %s,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (rto_application_id, rto_payment_amount, processing_session_id, worker_id, rto_queue_id),
            )
            cur.execute(
                """
                UPDATE sales_master
                SET vahan_application_id = %s,
                    rto_charges = %s
                WHERE sales_id = %s
                """,
                (rto_application_id, rto_payment_amount, sales_id),
            )
            conn.commit()
    finally:
        conn.close()


def mark_batch_row_failed(rto_queue_id: int, processing_session_id: str, worker_id: str, error_message: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Failed',
                    finished_at = NOW(),
                    leased_until = NULL,
                    processing_session_id = %s,
                    worker_id = %s,
                    last_error = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (processing_session_id, worker_id, (error_message or "").strip()[:4000], rto_queue_id),
            )
            conn.commit()
    finally:
        conn.close()


def mark_batch_row_pending(
    rto_queue_id: int,
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
                    leased_until = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (((error_message or "").strip() or None), rto_queue_id),
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


def retry_failed_row(rto_queue_id: int) -> bool:
    """Set one failed queue row back to Queued for operator retry."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Queued',
                    leased_until = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                  AND status = 'Failed'
                """,
                (rto_queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def list_all(dealer_id: int | None = None) -> list[dict]:
    """Return rows for RTO Queue, newest first. Filter by dealer_id via sales_master join."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if dealer_id is not None:
                cur.execute(
                    f"""
                    SELECT {JOINED_COLUMNS}
                    {JOIN_CLAUSE}
                    WHERE sm.dealer_id = %s
                    ORDER BY rq.created_at DESC
                    """,
                    (dealer_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {JOINED_COLUMNS}
                    {JOIN_CLAUSE}
                    ORDER BY rq.created_at DESC
                    """
                )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
