"""challan_staging table: OCR/import rows for subdealer challan DMS flow."""

from __future__ import annotations

import uuid
from typing import Any

from app.db import get_connection


def insert_staging_rows(
    *,
    challan_batch_id: uuid.UUID,
    challan_date: str | None,
    challan_book_num: str | None,
    from_dealer_id: int,
    to_dealer_id: int,
    lines: list[dict[str, Any]],
) -> list[int]:
    """
    Insert one row per line with status Queued. Each line: raw_engine, raw_chassis (optional strings).
    Returns list of challan_staging_id values.
    """
    ids: list[int] = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for ln in lines:
                rc = (ln.get("raw_chassis") or ln.get("chassis") or "").strip() or None
                re_ = (ln.get("raw_engine") or ln.get("engine") or "").strip() or None
                cur.execute(
                    """
                    INSERT INTO challan_staging (
                        challan_date, challan_book_num, from_dealer_id, to_dealer_id,
                        raw_chassis, raw_engine, status, challan_batch_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING challan_staging_id
                    """,
                    (
                        challan_date,
                        challan_book_num,
                        int(from_dealer_id),
                        int(to_dealer_id),
                        rc,
                        re_,
                        "Queued",
                        challan_batch_id,
                    ),
                )
                row = cur.fetchone()
                if row:
                    ids.append(int(row["challan_staging_id"] if isinstance(row, dict) else row[0]))
        conn.commit()
    finally:
        conn.close()
    return ids


def fetch_batch_rows(challan_batch_id: uuid.UUID) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT challan_staging_id, challan_date, challan_book_num, from_dealer_id, to_dealer_id,
                       raw_chassis, raw_engine, status, last_error, inventory_line_id, challan_batch_id
                FROM challan_staging
                WHERE challan_batch_id = %s::uuid
                ORDER BY challan_staging_id
                """,
                (str(challan_batch_id),),
            )
            return [dict(r) for r in cur.fetchall() or []]
    finally:
        conn.close()


def update_staging_status(
    challan_staging_id: int,
    *,
    status: str,
    last_error: str | None = None,
    inventory_line_id: int | None = None,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE challan_staging
                SET status = %s,
                    last_error = %s,
                    inventory_line_id = COALESCE(%s, inventory_line_id)
                WHERE challan_staging_id = %s
                """,
                (status, last_error, inventory_line_id, int(challan_staging_id)),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_staging_row(challan_staging_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT challan_staging_id, challan_date, challan_book_num, from_dealer_id, to_dealer_id,
                       raw_chassis, raw_engine, status, last_error, inventory_line_id, challan_batch_id
                FROM challan_staging
                WHERE challan_staging_id = %s
                """,
                (int(challan_staging_id),),
            )
            r = cur.fetchone()
            return dict(r) if r else None
    finally:
        conn.close()
