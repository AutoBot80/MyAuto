"""challan_master_staging: one row per subdealer challan batch (header + lifecycle)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.db import get_connection


def _row_jsonable(r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in r.items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "isoformat") and callable(getattr(v, "isoformat")):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def find_existing_batch_for_dealer_book_date(
    *,
    from_dealer_id: int,
    challan_book_num: str,
    challan_date: str,
) -> uuid.UUID | None:
    """
    Return an existing ``challan_batch_id`` when the same dealer already has a master row
    with the same trimmed book number and date (duplicate challan upload).
    Caller must pass non-empty *challan_book_num* and *challan_date*.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT challan_batch_id
                FROM challan_master_staging
                WHERE from_dealer_id = %s
                  AND TRIM(COALESCE(challan_book_num, '')) = %s
                  AND TRIM(COALESCE(challan_date, '')) = %s
                LIMIT 1
                """,
                (int(from_dealer_id), challan_book_num.strip(), challan_date.strip()),
            )
            row = cur.fetchone()
            if not row:
                return None
            raw = row["challan_batch_id"] if isinstance(row, dict) else row[0]
            return uuid.UUID(str(raw))
    finally:
        conn.close()


def insert_master(
    *,
    challan_batch_id: uuid.UUID,
    from_dealer_id: int,
    to_dealer_id: int,
    challan_date: str | None,
    challan_book_num: str | None,
    num_vehicles: int,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO challan_master_staging (
                    challan_batch_id, from_dealer_id, to_dealer_id,
                    challan_date, challan_book_num, num_vehicles,
                    num_vehicles_prepared, invoice_complete, invoice_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 0, FALSE, 'Pending')
                """,
                (
                    str(challan_batch_id),
                    int(from_dealer_id),
                    int(to_dealer_id),
                    challan_date,
                    challan_book_num,
                    int(num_vehicles),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_master(challan_batch_id: uuid.UUID) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT challan_batch_id, from_dealer_id, to_dealer_id, challan_date, challan_book_num,
                       num_vehicles, num_vehicles_prepared, invoice_complete, invoice_status, created_at,
                       last_run_at
                FROM challan_master_staging
                WHERE challan_batch_id = %s::uuid
                """,
                (str(challan_batch_id),),
            )
            r = cur.fetchone()
            return _row_jsonable(dict(r)) if r else None
    finally:
        conn.close()


def refresh_prepared_count(challan_batch_id: uuid.UUID) -> None:
    """Recompute num_vehicles_prepared from detail rows (Ready or Committed)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE challan_master_staging m
                SET num_vehicles_prepared = COALESCE((
                    SELECT COUNT(*)::integer
                    FROM challan_details_staging d
                    WHERE d.challan_batch_id = m.challan_batch_id
                      AND LOWER(TRIM(COALESCE(d.status, ''))) IN ('ready', 'committed')
                ), 0)
                WHERE m.challan_batch_id = %s::uuid
                """,
                (str(challan_batch_id),),
            )
        conn.commit()
    finally:
        conn.close()


def touch_last_run_at(challan_batch_id: uuid.UUID) -> None:
    """Record that a process/retry attempt finished (UI **Latest run**)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE challan_master_staging
                SET last_run_at = CURRENT_TIMESTAMP
                WHERE challan_batch_id = %s::uuid
                """,
                (str(challan_batch_id),),
            )
        conn.commit()
    finally:
        conn.close()


def set_invoice_state(
    challan_batch_id: uuid.UUID,
    *,
    invoice_complete: bool | None = None,
    invoice_status: str | None = None,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sets: list[str] = []
            params: list[Any] = []
            if invoice_complete is not None:
                sets.append("invoice_complete = %s")
                params.append(invoice_complete)
            if invoice_status is not None:
                sets.append("invoice_status = %s")
                params.append(invoice_status)
            if not sets:
                return
            params.append(str(challan_batch_id))
            cur.execute(
                f"""
                UPDATE challan_master_staging
                SET {", ".join(sets)}
                WHERE challan_batch_id = %s::uuid
                """,
                params,
            )
        conn.commit()
    finally:
        conn.close()


_MASTER_LIST_SELECT = """
                SELECT m.challan_batch_id, m.from_dealer_id, m.to_dealer_id, m.challan_date, m.challan_book_num,
                       m.num_vehicles, m.num_vehicles_prepared, m.invoice_complete, m.invoice_status, m.created_at,
                       m.last_run_at,
                       df.dealer_name AS from_dealer_name,
                       dt.dealer_name AS to_dealer_name,
                       COALESCE((
                           SELECT COUNT(*)::integer FROM challan_details_staging d
                           WHERE d.challan_batch_id = m.challan_batch_id
                             AND LOWER(TRIM(COALESCE(d.status, ''))) = 'ready'
                       ), 0) AS ready_line_count,
                       COALESCE((
                           SELECT COUNT(*)::integer FROM challan_details_staging d
                           WHERE d.challan_batch_id = m.challan_batch_id
                             AND LOWER(TRIM(COALESCE(d.status, ''))) = 'failed'
                       ), 0) AS failed_line_count
                FROM challan_master_staging m
                LEFT JOIN dealer_ref df ON df.dealer_id = m.from_dealer_id
                LEFT JOIN dealer_ref dt ON dt.dealer_id = m.to_dealer_id
"""

# Default Processed list (15-day window): batches that need user action.
# Include any master with at least one **Queued** line — not only when ``last_run_at`` is set. Electron sidecar
# can fail on the first API call (e.g. SSL) before ``touch_last_run_at`` runs; staging rows then stay Queued
# with ``last_run_at`` NULL, would not match the old filter, and duplicate staging was blocked with no UI row.
_DEFAULT_PROCESSED_ATTENTION_SQL = """
(
  EXISTS (
    SELECT 1 FROM challan_details_staging d
    WHERE d.challan_batch_id = m.challan_batch_id
      AND LOWER(TRIM(COALESCE(d.status, ''))) = 'failed'
  )
  OR LOWER(TRIM(COALESCE(m.invoice_status, ''))) = 'failed'
  OR EXISTS (
    SELECT 1 FROM challan_details_staging d2
    WHERE d2.challan_batch_id = m.challan_batch_id
      AND LOWER(TRIM(COALESCE(d2.status, ''))) = 'queued'
  )
)
"""


def list_masters_recent(
    from_dealer_id: int,
    *,
    days: int = 15,
    challan_book_num: str | None = None,
) -> list[dict[str, Any]]:
    """
    Processed tab list.

    * No ``challan_book_num``: masters from the last *days* that need attention: Failed line(s), failed invoice,
      **or** at least one **Queued** detail line (includes never-started sidecar runs where ``last_run_at`` is still null).
    * With non-empty ``challan_book_num`` (trimmed): masters for this dealer whose ``challan_book_num`` matches
      (case-insensitive, trimmed); **no** date window — used to open older challans by book number.
    """
    book = (challan_book_num or "").strip()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if book:
                cur.execute(
                    _MASTER_LIST_SELECT
                    + """
                WHERE m.from_dealer_id = %s
                  AND LOWER(TRIM(COALESCE(m.challan_book_num, ''))) = LOWER(%s)
                ORDER BY m.created_at DESC, m.challan_batch_id DESC
                """,
                    (int(from_dealer_id), book),
                )
            else:
                cur.execute(
                    _MASTER_LIST_SELECT
                    + """
                WHERE m.from_dealer_id = %s
                  AND m.created_at >= CURRENT_TIMESTAMP - (%s::integer * INTERVAL '1 day')
                  AND """ + _DEFAULT_PROCESSED_ATTENTION_SQL + """
                ORDER BY m.created_at DESC, m.challan_batch_id DESC
                """,
                    (int(from_dealer_id), int(days)),
                )
            return [_row_jsonable(dict(r)) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def count_masters_needing_attention_recent(from_dealer_id: int, *, days: int = 15) -> int:
    """Badge: number of ``challan_master_staging`` rows in the default Processed list (same filter as ``list_masters_recent`` without book search)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)::integer AS c
                FROM challan_master_staging m
                WHERE m.from_dealer_id = %s
                  AND m.created_at >= CURRENT_TIMESTAMP - (%s::integer * INTERVAL '1 day')
                  AND """ + _DEFAULT_PROCESSED_ATTENTION_SQL + """
                """,
                (int(from_dealer_id), int(days)),
            )
            row = cur.fetchone()
            if not row:
                return 0
            return int(row["c"] if isinstance(row, dict) else row[0])
    finally:
        conn.close()
