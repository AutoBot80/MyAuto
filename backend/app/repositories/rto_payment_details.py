"""Data access for rto_queue table (redesigned schema: rto_queue_id serial PK)."""

import re
from datetime import date, datetime, timezone

from app.db import get_connection
from app.services.customer_address_infer import canonical_states_differ

RTO_STATUS_NEEDS_TRC = "Needs TRC"


def _normalize_indian_mobile_10(mobile: str) -> str | None:
    """Return last 10 digits when valid Indian mobile (6–9 start); else None."""
    d = re.sub(r"\D", "", str(mobile or "").strip())
    if len(d) < 10:
        return None
    d10 = d[-10:]
    if not re.match(r"^[6-9]\d{9}$", d10):
        return None
    return d10

SELECT_COLUMNS = """
rq.rto_queue_id,
rq.sales_id,
rq.staging_id,
rq.dealer_id,
rq.insurance_id,
rq.customer_mobile,
rq.rto_application_id,
rq.rto_application_date,
rq.rto_payment_id,
rq.rto_payment_amount,
rq.rto_status,
rq.status,
rq.in_queue,
rq.processing_session_id,
rq.worker_id,
rq.leased_until,
rq.locked_by_login_id,
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
im.policy_to,
im.idv,
im.nominee_name,
im.nominee_relationship,
lr.name AS locked_by_name
""".strip()

JOIN_CLAUSE = """
FROM rto_queue rq
JOIN sales_master sm ON sm.sales_id = rq.sales_id
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
LEFT JOIN insurance_master im ON im.insurance_id = rq.insurance_id
LEFT JOIN login_ref lr ON lr.login_id = rq.locked_by_login_id
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


def get_dealer_id_for_sales(sales_id: int) -> int | None:
    """Return dealer_id from sales_master for this sale."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dealer_id FROM sales_master WHERE sales_id = %s",
                (sales_id,),
            )
            row = cur.fetchone()
            if not row or row.get("dealer_id") is None:
                return None
            return int(row["dealer_id"])
    finally:
        conn.close()


def get_customer_and_dealer_states_for_sales(
    sales_id: int,
    dealer_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(customer_state, dealer_state)`` for interstate Needs TRC detection."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(TRIM(cm.state::text), '') AS customer_state,
                    COALESCE(TRIM(dr.state::text), '') AS dealer_state
                FROM sales_master sm
                JOIN customer_master cm ON cm.customer_id = sm.customer_id
                LEFT JOIN dealer_ref dr ON dr.dealer_id = COALESCE(%s, sm.dealer_id)
                WHERE sm.sales_id = %s
                """,
                (dealer_id, sales_id),
            )
            row = cur.fetchone()
            if not row:
                return None, None
            cust = (row.get("customer_state") or "").strip() or None
            deal = (row.get("dealer_state") or "").strip() or None
            return cust, deal
    finally:
        conn.close()


def insert(
    sales_id: int,
    insurance_id: int | None = None,
    customer_mobile: str | None = None,
    rto_application_date: date | None = None,
    rto_payment_amount: float | None = None,
    status: str = "Queued",
    staging_id: str | None = None,
    dealer_id: int | None = None,
    in_queue: bool | None = None,
) -> int:
    """Insert a queue row; returns rto_queue_id. Upserts on sales_id conflict.

    When customer and dealer states are both known Indian states/UTs and differ,
    forces ``status=Needs TRC`` and ``in_queue=false`` (out-of-state / TRC path).
    """
    status_use = (status or "Queued").strip() or "Queued"
    in_queue_use = True if in_queue is None else bool(in_queue)
    cust_state, deal_state = get_customer_and_dealer_states_for_sales(sales_id, dealer_id)
    if canonical_states_differ(cust_state, deal_state):
        status_use = RTO_STATUS_NEEDS_TRC
        in_queue_use = False

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rto_queue
                (
                    sales_id,
                    staging_id,
                    dealer_id,
                    insurance_id,
                    customer_mobile,
                    rto_application_date,
                    rto_payment_amount,
                    status,
                    in_queue,
                    updated_at
                )
                VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (sales_id) DO UPDATE SET
                  staging_id = COALESCE(EXCLUDED.staging_id, rto_queue.staging_id),
                  dealer_id = COALESCE(EXCLUDED.dealer_id, rto_queue.dealer_id),
                  insurance_id = COALESCE(EXCLUDED.insurance_id, rto_queue.insurance_id),
                  customer_mobile = COALESCE(EXCLUDED.customer_mobile, rto_queue.customer_mobile),
                  rto_application_date = COALESCE(EXCLUDED.rto_application_date, rto_queue.rto_application_date),
                  rto_payment_amount = COALESCE(EXCLUDED.rto_payment_amount, rto_queue.rto_payment_amount),
                  status = EXCLUDED.status,
                  in_queue = EXCLUDED.in_queue,
                  last_error = NULL,
                  leased_until = NULL,
                  processing_session_id = NULL,
                  worker_id = NULL,
                  updated_at = NOW()
                RETURNING rto_queue_id
                """,
                (
                    sales_id,
                    (staging_id or "").strip() or None,
                    dealer_id,
                    insurance_id,
                    (customer_mobile or "").strip() or None,
                    rto_application_date,
                    rto_payment_amount,
                    status_use,
                    in_queue_use,
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
    lease_seconds: int = 900,
    locked_by_login_id: str | None = None,
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
                    locked_by_login_id = NULL,
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
                      AND rq.in_queue = true
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
                    locked_by_login_id = %s,
                    attempt_count = COALESCE(q.attempt_count, 0) + 1,
                    last_error = NULL,
                    updated_at = NOW()
                FROM candidates
                WHERE q.rto_queue_id = candidates.rto_queue_id
                RETURNING q.rto_queue_id
                """,
                (dealer_id, limit, processing_session_id, worker_id, lease_seconds, locked_by_login_id),
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
                    leased_until = NOW() + make_interval(secs => 900),
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


def update_application_progress(
    rto_queue_id: int,
    *,
    rto_status: int,
    rto_application_id: str | None = None,
) -> bool:
    """Persist Vahan scrape progress: optional application id + integer ``rto_status`` (1=Screen 2, 2=Screen 3d)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            app = (rto_application_id or "").strip() or None
            if app:
                cur.execute(
                    """
                    UPDATE rto_queue
                    SET rto_application_id = %s,
                        rto_status = %s,
                        updated_at = NOW()
                    WHERE rto_queue_id = %s
                    """,
                    (app, int(rto_status), int(rto_queue_id)),
                )
            else:
                cur.execute(
                    """
                    UPDATE rto_queue
                    SET rto_status = %s,
                        updated_at = NOW()
                    WHERE rto_queue_id = %s
                    """,
                    (int(rto_status), int(rto_queue_id)),
                )
            conn.commit()
            return cur.rowcount > 0
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
                    rto_application_id = COALESCE(%s, rto_application_id),
                    rto_payment_amount = %s,
                    uploaded_at = NOW(),
                    finished_at = NOW(),
                    leased_until = NULL,
                    locked_by_login_id = NULL,
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
                SET rto_charges = %s
                WHERE sales_id = %s
                """,
                (rto_payment_amount, sales_id),
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
                    locked_by_login_id = NULL,
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
                    locked_by_login_id = NULL,
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
                    locked_by_login_id = NULL,
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


def update_customer_mobile(rto_queue_id: int, mobile: str) -> bool:
    """Persist operator-chosen Vahan OTP mobile on the queue row (per-sale override)."""
    d10 = _normalize_indian_mobile_10(mobile)
    if not d10:
        return False
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET customer_mobile = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (d10, int(rto_queue_id)),
            )
            conn.commit()
            return cur.rowcount > 0
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
                    locked_by_login_id = NULL,
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


def mark_batch_row_forms_missing(
    rto_queue_id: int,
    processing_session_id: str,
    worker_id: str,
    reason: str,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Forms Missing',
                    leased_until = NULL,
                    locked_by_login_id = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                """,
                (((reason or "").strip() or "Missing Vahan upload documents")[:4000], rto_queue_id),
            )
            conn.commit()
    finally:
        conn.close()


def mark_forms_ready(rto_queue_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Queued',
                    last_error = NULL,
                    leased_until = NULL,
                    locked_by_login_id = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                  AND status = 'Forms Missing'
                """,
                (rto_queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def mark_manually_completed(rto_queue_id: int) -> bool:
    """Mark Done — Queued/Pending/Failed only (excludes Needs TRC and in-flight statuses)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Manually Completed',
                    finished_at = NOW(),
                    leased_until = NULL,
                    locked_by_login_id = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                  AND status IN ('Queued', 'Pending', 'Failed')
                """,
                (rto_queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def requeue_completed_row(rto_queue_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Queued',
                    in_queue = true,
                    rto_application_id = NULL,
                    rto_status = NULL,
                    leased_until = NULL,
                    locked_by_login_id = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                  AND status IN ('Completed', 'Manually Completed')
                """,
                (rto_queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def release_in_progress_row(rto_queue_id: int) -> bool:
    """Operator release: In Progress / Pending lock back to Queued."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET status = 'Queued',
                    leased_until = NULL,
                    locked_by_login_id = NULL,
                    processing_session_id = NULL,
                    worker_id = NULL,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                  AND status IN ('In Progress', 'Pending')
                """,
                (rto_queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def set_in_queue(rto_queue_id: int, in_queue: bool) -> bool:
    """Toggle In Queue for Queued/Pending/Failed only (Needs TRC cannot opt into batch)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rto_queue
                SET in_queue = %s,
                    updated_at = NOW()
                WHERE rto_queue_id = %s
                  AND status = ANY(%s)
                """,
                (bool(in_queue), rto_queue_id, ["Queued", "Pending", "Failed"]),
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
