"""Committed ``challan_master`` / ``challan_details`` (post–DMS invoice) for POS history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db import get_connection


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat") and callable(getattr(v, "isoformat")):
        return v.isoformat()
    return v


def _row_jsonable(r: dict[str, Any]) -> dict[str, Any]:
    return {k: _jsonable(v) for k, v in r.items()}


def list_committed_masters_for_dealer(
    dealer_from_id: int,
    *,
    days: int = 365,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Latest-first committed challans where ``dealer_from`` matches (parent dealer).
    ``days`` filters on ``challan_master.created_at``; rows with NULL ``created_at`` are included.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cm.challan_id,
                    cm.challan_date,
                    cm.challan_book_num,
                    cm.dealer_from,
                    cm.dealer_to,
                    cm.num_vehicles,
                    cm.order_number,
                    cm.invoice_number,
                    cm.total_ex_showroom_price,
                    cm.total_discount,
                    cm.created_at,
                    COALESCE(TRIM(dr.dealer_name), '') AS to_dealer_name
                FROM challan_master cm
                LEFT JOIN dealer_ref dr ON dr.dealer_id = cm.dealer_to
                WHERE cm.dealer_from = %s
                  AND (
                      cm.created_at IS NULL
                      OR cm.created_at >= NOW() - (%s * INTERVAL '1 day')
                  )
                ORDER BY cm.created_at DESC NULLS LAST, cm.challan_id DESC
                LIMIT %s
                """,
                (int(dealer_from_id), int(days), int(limit)),
            )
            rows = cur.fetchall() or []
            return [_row_jsonable(dict(r)) for r in rows]
    finally:
        conn.close()


def list_committed_details_for_challan(
    challan_id: int,
    dealer_from_id: int,
) -> list[dict[str, Any]] | None:
    """
    Per-vehicle lines for one committed challan (inventory join).
    Returns ``None`` if the challan does not exist or ``dealer_from`` does not match.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM challan_master
                WHERE challan_id = %s AND dealer_from = %s
                """,
                (int(challan_id), int(dealer_from_id)),
            )
            if cur.fetchone() is None:
                return None
            cur.execute(
                """
                SELECT
                    vim.inventory_line_id,
                    vim.chassis_no,
                    vim.engine_no,
                    vim.model,
                    vim.variant,
                    vim.color,
                    vim.ex_showroom_price,
                    vim.discount
                FROM challan_details cd
                INNER JOIN vehicle_inventory_master vim
                    ON vim.inventory_line_id = cd.inventory_line_id
                WHERE cd.challan_id = %s
                ORDER BY vim.chassis_no NULLS LAST, vim.engine_no NULLS LAST, vim.inventory_line_id
                """,
                (int(challan_id),),
            )
            rows = cur.fetchall() or []
            return [_row_jsonable(dict(r)) for r in rows]
    finally:
        conn.close()
