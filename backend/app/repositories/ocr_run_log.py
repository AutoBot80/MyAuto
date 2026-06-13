"""Insert and list rows in ``ocr_run_log`` (Add Sales OCR missing-field diagnostics)."""

from __future__ import annotations

import logging
from typing import Any

from app.db import get_connection

logger = logging.getLogger(__name__)

_MAX_FAILURES_TEXT = 4000
_DEFAULT_LIST_DAYS = 15


def _clip_failures(text: str) -> str:
    t = (text or "").strip()
    if len(t) <= _MAX_FAILURES_TEXT:
        return t
    return t[: _MAX_FAILURES_TEXT - 3] + "..."


def insert_ocr_run_log(
    *,
    dealer_id: int,
    customer_mobile: str | None,
    sale_subfolder: str,
    ocr_failures: str,
) -> None:
    """Append one OCR run row. Swallows DB errors so OCR is never blocked."""
    failures = _clip_failures(ocr_failures)
    if not failures:
        return
    sub = (sale_subfolder or "").strip()
    if not sub:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ocr_run_log (
                    dealer_id, occurred_at, customer_mobile, sale_subfolder, ocr_failures
                ) VALUES (
                    %s, NOW(), %s, %s, %s
                )
                """,
                (
                    int(dealer_id),
                    (customer_mobile or "").strip() or None,
                    sub,
                    failures,
                ),
            )
        conn.commit()
    except Exception:
        logger.exception(
            "ocr_run_log insert failed dealer_id=%s subfolder=%s",
            dealer_id,
            sale_subfolder,
        )
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def list_recent_for_admin(
    *,
    limit: int = 200,
    days: int = _DEFAULT_LIST_DAYS,
    dealer_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 1000))
    window_days = max(1, min(int(days), 365))
    dealer_filter = ""
    params: list[Any] = [window_days]
    if dealer_ids:
        dealer_filter = "AND o.dealer_id = ANY(%s)"
        params.append(dealer_ids)
    params.append(lim)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    o.id,
                    o.dealer_id,
                    COALESCE(d.dealer_name, '') AS dealer_name,
                    o.occurred_at,
                    o.customer_mobile,
                    o.sale_subfolder,
                    o.ocr_failures
                FROM ocr_run_log o
                LEFT JOIN dealer_ref d ON d.dealer_id = o.dealer_id
                WHERE o.occurred_at >= NOW() - (%s * INTERVAL '1 day')
                {dealer_filter}
                ORDER BY o.occurred_at DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            return [dict(r) for r in rows]
    finally:
        conn.close()
