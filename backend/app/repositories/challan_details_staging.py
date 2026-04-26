"""challan_details_staging: per-line vehicle rows for a subdealer challan batch."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.db import get_connection


def _norm_vehicle_key(raw_engine: str | None, raw_chassis: str | None) -> tuple[str, str]:
    return ((raw_engine or "").strip().upper(), (raw_chassis or "").strip().upper())


def fetch_existing_vehicle_keys_for_dealer_book_date(
    from_dealer_id: int,
    challan_book_num: str,
    challan_date: str,
) -> set[tuple[str, str]]:
    """
    Normalised (engine, chassis) pairs for **every** detail line on any staging batch with the same
    dealer, book number, and challan date — **any** status (Queued, Failed, Ready, Committed).
    Used to avoid duplicate ``challan_details_staging`` rows for the same vehicle when re-uploading.
    """
    conn = get_connection()
    out: set[tuple[str, str]] = set()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.raw_engine, d.raw_chassis
                FROM challan_details_staging d
                INNER JOIN challan_master_staging m ON m.challan_batch_id = d.challan_batch_id
                WHERE m.from_dealer_id = %s
                  AND TRIM(COALESCE(m.challan_book_num, '')) = %s
                  AND TRIM(COALESCE(m.challan_date, '')) = %s
                """,
                (int(from_dealer_id), challan_book_num.strip(), challan_date.strip()),
            )
            for r in cur.fetchall() or []:
                dct = dict(r) if not isinstance(r, dict) else r
                re_ = dct.get("raw_engine")
                rc = dct.get("raw_chassis")
                key = _norm_vehicle_key(
                    str(re_) if re_ is not None else None,
                    str(rc) if rc is not None else None,
                )
                if key[0] or key[1]:
                    out.add(key)
    finally:
        conn.close()
    return out


def _row_jsonable(r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in r.items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def insert_detail_rows(
    *,
    challan_batch_id: uuid.UUID,
    lines: list[dict[str, Any]],
) -> list[int]:
    """Insert one row per line (Queued). Returns challan_detail_staging_id values."""
    ids: list[int] = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for ln in lines:
                rc = (ln.get("raw_chassis") or ln.get("chassis") or "").strip() or None
                re_ = (ln.get("raw_engine") or ln.get("engine") or "").strip() or None
                cur.execute(
                    """
                    INSERT INTO challan_details_staging (
                        challan_batch_id, raw_chassis, raw_engine, status
                    )
                    VALUES (%s::uuid, %s, %s, %s)
                    RETURNING challan_detail_staging_id
                    """,
                    (str(challan_batch_id), rc, re_, "Queued"),
                )
                row = cur.fetchone()
                if row:
                    ids.append(int(row["challan_detail_staging_id"] if isinstance(row, dict) else row[0]))
        conn.commit()
    finally:
        conn.close()
    return ids


def fetch_batch_rows(challan_batch_id: uuid.UUID) -> list[dict[str, Any]]:
    """Detail rows joined with master header fields (compat with orchestrator)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.challan_detail_staging_id AS challan_staging_id,
                       m.challan_date, m.challan_book_num, m.from_dealer_id, m.to_dealer_id,
                       d.raw_chassis, d.raw_engine, d.status, d.last_error, d.inventory_line_id,
                       d.challan_batch_id
                FROM challan_details_staging d
                JOIN challan_master_staging m ON m.challan_batch_id = d.challan_batch_id
                WHERE d.challan_batch_id = %s::uuid
                ORDER BY d.challan_detail_staging_id
                """,
                (str(challan_batch_id),),
            )
            return [_row_jsonable(dict(r)) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def other_line_has_same_vehicle_key(
    *,
    exclude_challan_detail_staging_id: int,
    from_dealer_id: int,
    challan_book_num: str | None,
    challan_date: str | None,
    challan_batch_id: uuid.UUID,
    raw_engine: str | None,
    raw_chassis: str | None,
) -> bool:
    """
    True if another detail row would duplicate the (engine, chassis) identity:
    - always checks other rows in the same ``challan_batch_id``;
    - when both book number and date are set on the master, also checks any other batch
      for the same dealer + book + date (mirrors create_staging dedupe).
    """
    e = (raw_engine or "").strip().upper()
    c = (raw_chassis or "").strip().upper()
    if not e and not c:
        return False
    book = (challan_book_num or "").strip()
    d = (challan_date or "").strip()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM challan_details_staging
                WHERE challan_batch_id = %s::uuid
                  AND challan_detail_staging_id <> %s
                  AND UPPER(TRIM(COALESCE(raw_engine, ''))) = %s
                  AND UPPER(TRIM(COALESCE(raw_chassis, ''))) = %s
                LIMIT 1
                """,
                (str(challan_batch_id), int(exclude_challan_detail_staging_id), e, c),
            )
            if cur.fetchone() is not None:
                return True
            if book and d:
                cur.execute(
                    """
                    SELECT 1
                    FROM challan_details_staging d
                    INNER JOIN challan_master_staging m ON m.challan_batch_id = d.challan_batch_id
                    WHERE m.from_dealer_id = %s
                      AND TRIM(COALESCE(m.challan_book_num, '')) = %s
                      AND TRIM(COALESCE(m.challan_date, '')) = %s
                      AND d.challan_detail_staging_id <> %s
                      AND UPPER(TRIM(COALESCE(d.raw_engine, ''))) = %s
                      AND UPPER(TRIM(COALESCE(d.raw_chassis, ''))) = %s
                    LIMIT 1
                    """,
                    (int(from_dealer_id), book, d, int(exclude_challan_detail_staging_id), e, c),
                )
                return cur.fetchone() is not None
    finally:
        conn.close()
    return False


def update_detail_raw_fields(
    challan_detail_staging_id: int,
    *,
    raw_chassis: str | None,
    raw_engine: str | None,
) -> bool:
    """
    Set ``raw_chassis`` / ``raw_engine`` and clear ``last_error`` and ``inventory_line_id`` for
    a **Failed** line so the user can correct OCR and retry. Returns True if a row was updated.
    """
    rc = (raw_chassis or "").strip() or None
    re_ = (raw_engine or "").strip() or None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE challan_details_staging
                SET raw_chassis = %s,
                    raw_engine = %s,
                    last_error = NULL,
                    inventory_line_id = NULL
                WHERE challan_detail_staging_id = %s
                  AND LOWER(TRIM(COALESCE(status, ''))) = 'failed'
                """,
                (rc, re_, int(challan_detail_staging_id)),
            )
            n = int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return n > 0


def update_detail_status(
    challan_detail_staging_id: int,
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
                UPDATE challan_details_staging
                SET status = %s,
                    last_error = %s,
                    inventory_line_id = COALESCE(%s, inventory_line_id)
                WHERE challan_detail_staging_id = %s
                """,
                (status, last_error, inventory_line_id, int(challan_detail_staging_id)),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_detail_row(challan_detail_staging_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.challan_detail_staging_id AS challan_staging_id,
                       m.challan_date, m.challan_book_num, m.from_dealer_id, m.to_dealer_id,
                       d.raw_chassis, d.raw_engine, d.status, d.last_error, d.inventory_line_id,
                       d.challan_batch_id
                FROM challan_details_staging d
                JOIN challan_master_staging m ON m.challan_batch_id = d.challan_batch_id
                WHERE d.challan_detail_staging_id = %s
                """,
                (int(challan_detail_staging_id),),
            )
            r = cur.fetchone()
            return _row_jsonable(dict(r)) if r else None
    finally:
        conn.close()


def fetch_failed_details_for_batch(challan_batch_id: uuid.UUID) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT challan_detail_staging_id, raw_chassis, raw_engine, last_error, status
                FROM challan_details_staging
                WHERE challan_batch_id = %s::uuid
                  AND LOWER(TRIM(COALESCE(status, ''))) = 'failed'
                ORDER BY challan_detail_staging_id
                """,
                (str(challan_batch_id),),
            )
            return [_row_jsonable(dict(r)) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def fetch_all_detail_lines_for_batch(challan_batch_id: uuid.UUID) -> list[dict[str, Any]]:
    """All detail rows for Processed tab (Queued / Failed / Ready / Committed)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT challan_detail_staging_id, raw_chassis, raw_engine, last_error, status
                FROM challan_details_staging
                WHERE challan_batch_id = %s::uuid
                ORDER BY challan_detail_staging_id
                """,
                (str(challan_batch_id),),
            )
            return [_row_jsonable(dict(r)) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def reset_failed_detail_for_retry(challan_detail_staging_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE challan_details_staging
                SET status = 'Queued',
                    last_error = NULL,
                    inventory_line_id = NULL
                WHERE challan_detail_staging_id = %s
                  AND LOWER(TRIM(COALESCE(status, ''))) = 'failed'
                RETURNING challan_detail_staging_id
                """,
                (int(challan_detail_staging_id),),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def reset_all_failed_details_for_batch(challan_batch_id: uuid.UUID) -> int:
    """
    Set every **Failed** line in the batch to **Queued** and clear error / inventory link so
    ``prepare_vehicle`` will run again (Find→Vehicles, etc.). Used when re-processing a batch
    from ``POST /process/...`` while lines are still Failed.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE challan_details_staging
                SET status = 'Queued',
                    last_error = NULL,
                    inventory_line_id = NULL
                WHERE challan_batch_id = %s::uuid
                  AND LOWER(TRIM(COALESCE(status, ''))) = 'failed'
                """,
                (str(challan_batch_id),),
            )
            n = int(cur.rowcount or 0)
        conn.commit()
        return n
    finally:
        conn.close()


def batch_all_ready_for_order(challan_batch_id: uuid.UUID) -> bool:
    """True if at least one line and every line is Ready (prepare done, order not yet committed)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)::integer AS n,
                       BOOL_AND(LOWER(TRIM(COALESCE(status, ''))) = 'ready') AS all_ready
                FROM challan_details_staging
                WHERE challan_batch_id = %s::uuid
                """,
                (str(challan_batch_id),),
            )
            row = cur.fetchone()
            if not row:
                return False
            d = dict(row)
            n = int(d.get("n") or 0)
            ar = d.get("all_ready")
            return n > 0 and bool(ar)
    finally:
        conn.close()
