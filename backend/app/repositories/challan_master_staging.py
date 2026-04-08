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
                       num_vehicles, num_vehicles_prepared, invoice_complete, invoice_status, created_at
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


def list_masters_recent(from_dealer_id: int, *, days: int = 15) -> list[dict[str, Any]]:
    """Recent master rows for Processed tab."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.challan_batch_id, m.from_dealer_id, m.to_dealer_id, m.challan_date, m.challan_book_num,
                       m.num_vehicles, m.num_vehicles_prepared, m.invoice_complete, m.invoice_status, m.created_at,
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
                WHERE m.from_dealer_id = %s
                  AND m.created_at >= CURRENT_TIMESTAMP - (%s::integer * INTERVAL '1 day')
                ORDER BY m.created_at DESC, m.challan_batch_id DESC
                """,
                (int(from_dealer_id), int(days)),
            )
            return [_row_jsonable(dict(r)) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def count_failed_detail_lines_recent(from_dealer_id: int, *, days: int = 15) -> int:
    """Badge: Failed detail lines for this dealer in window (join master created_at)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)::integer AS c
                FROM challan_details_staging d
                JOIN challan_master_staging m ON m.challan_batch_id = d.challan_batch_id
                WHERE m.from_dealer_id = %s
                  AND LOWER(TRIM(COALESCE(d.status, ''))) = 'failed'
                  AND m.created_at >= CURRENT_TIMESTAMP - (%s::integer * INTERVAL '1 day')
                """,
                (int(from_dealer_id), int(days)),
            )
            row = cur.fetchone()
            if not row:
                return 0
            return int(row["c"] if isinstance(row, dict) else row[0])
    finally:
        conn.close()
