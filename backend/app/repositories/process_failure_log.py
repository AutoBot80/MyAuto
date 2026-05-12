"""Upsert and list rows in ``process_failure_log`` (terminal client-visible failures)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.db import get_connection

logger = logging.getLogger(__name__)

_MAX_ERR = 4000


def _clip_error(text: str) -> str:
    t = (text or "").strip()
    if len(t) <= _MAX_ERR:
        return t
    return t[: _MAX_ERR - 3] + "..."


def upsert_process_failure(
    *,
    dealer_id: int,
    process_label: str,
    entity_dedupe_key: str,
    error_text: str,
    customer_mobile: str | None = None,
    challan_book_num: str | None = None,
    challan_date: str | None = None,
    challan_batch_id: uuid.UUID | str | None = None,
    rto_queue_id: int | None = None,
) -> None:
    """INSERT … ON CONFLICT DO UPDATE. Swallows DB errors so callers are never blocked."""
    err = _clip_error(error_text)
    if not err:
        return
    pl = (process_label or "").strip()
    ek = (entity_dedupe_key or "").strip()
    if not pl or not ek:
        return
    batch = None
    if challan_batch_id is not None:
        if isinstance(challan_batch_id, uuid.UUID):
            batch = challan_batch_id
        else:
            s = str(challan_batch_id).strip()
            if s:
                try:
                    batch = uuid.UUID(s)
                except ValueError:
                    batch = None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO process_failure_log (
                    dealer_id, occurred_at, process_label,
                    customer_mobile, challan_book_num, challan_date, challan_batch_id,
                    rto_queue_id, error_text, entity_dedupe_key
                ) VALUES (
                    %s, NOW(), %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (dealer_id, process_label, entity_dedupe_key) DO UPDATE SET
                    occurred_at = EXCLUDED.occurred_at,
                    error_text = EXCLUDED.error_text,
                    customer_mobile = EXCLUDED.customer_mobile,
                    challan_book_num = EXCLUDED.challan_book_num,
                    challan_date = EXCLUDED.challan_date,
                    challan_batch_id = EXCLUDED.challan_batch_id,
                    rto_queue_id = EXCLUDED.rto_queue_id
                """,
                (
                    int(dealer_id),
                    pl,
                    (customer_mobile or "").strip() or None,
                    (challan_book_num or "").strip() or None,
                    (challan_date or "").strip() or None,
                    batch,
                    int(rto_queue_id) if rto_queue_id is not None else None,
                    err,
                    ek,
                ),
            )
        conn.commit()
    except Exception:
        logger.exception(
            "process_failure_log upsert failed dealer_id=%s process=%s key=%s",
            dealer_id,
            pl,
            ek,
        )
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def list_recent_for_admin(*, limit: int = 200) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 1000))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    f.id,
                    f.dealer_id,
                    COALESCE(d.dealer_name, '') AS dealer_name,
                    f.occurred_at,
                    f.process_label,
                    f.customer_mobile,
                    f.challan_book_num,
                    f.challan_date,
                    f.challan_batch_id::text AS challan_batch_id,
                    f.rto_queue_id,
                    f.error_text,
                    f.entity_dedupe_key
                FROM process_failure_log f
                LEFT JOIN dealer_ref d ON d.dealer_id = f.dealer_id
                ORDER BY f.occurred_at DESC
                LIMIT %s
                """,
                (lim,),
            )
            rows = cur.fetchall() or []
            return [dict(r) for r in rows]
    finally:
        conn.close()
