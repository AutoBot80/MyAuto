"""Persist challan_master + challan_details after successful DMS order (single transaction)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db import get_connection
from app.repositories.vehicle_inventory import update_discount_and_ex_showroom

logger = logging.getLogger(__name__)


def commit_challan_masters(
    *,
    challan_date: str | None,
    challan_book_num: str | None,
    dealer_from: int,
    dealer_to: int,
    inventory_line_ids: list[int],
    order_number: str | None,
    invoice_number: str | None,
    add_transport_cost: bool = False,
    transport_cost_per_vehicle: float | None = None,
) -> int:
    """
    Insert challan_master (with totals from vehicle_inventory_master) and challan_details links.
    Returns challan_id.
    """
    if not inventory_line_ids:
        raise ValueError("inventory_line_ids required")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(ex_showroom_price), 0), COALESCE(SUM(discount), 0)
                FROM vehicle_inventory_master
                WHERE inventory_line_id = ANY(%s)
                """,
                (inventory_line_ids,),
            )
            sum_row = cur.fetchone()
            total_ex = sum_row[0] if sum_row else Decimal("0")
            total_disc = sum_row[1] if sum_row else Decimal("0")
            if isinstance(total_ex, Decimal):
                total_ex_f = float(total_ex)
            else:
                total_ex_f = float(total_ex or 0)
            if isinstance(total_disc, Decimal):
                total_disc_f = float(total_disc)
            else:
                total_disc_f = float(total_disc or 0)

            created = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO challan_master (
                    challan_date, challan_book_num, dealer_from, dealer_to,
                    num_vehicles, order_number, invoice_number,
                    total_ex_showroom_price, total_discount,
                    add_transport_cost, transport_cost_per_vehicle,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING challan_id
                """,
                (
                    challan_date,
                    challan_book_num,
                    int(dealer_from),
                    int(dealer_to),
                    len(inventory_line_ids),
                    (order_number or "").strip() or None,
                    (invoice_number or "").strip() or None,
                    total_ex_f,
                    total_disc_f,
                    bool(add_transport_cost),
                    None if not add_transport_cost else transport_cost_per_vehicle,
                    created,
                ),
            )
            cid_row = cur.fetchone()
            challan_id = int(cid_row["challan_id"] if isinstance(cid_row, dict) else cid_row[0])

            for iid in inventory_line_ids:
                cur.execute(
                    """
                    INSERT INTO challan_details (challan_id, inventory_line_id)
                    VALUES (%s, %s)
                    """,
                    (challan_id, int(iid)),
                )
        conn.commit()
        return challan_id
    finally:
        conn.close()


def _coerce_ex_showroom_scalar(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _ex_showroom_from_line_item_dict(item: dict[str, Any]) -> float | None:
    """Prefer subdealer challan key ``vehicle_ex_showroom_cost``, then legacy scrape keys."""
    for key in ("vehicle_ex_showroom_cost", "ex_showroom", "price", "total"):
        px = _coerce_ex_showroom_scalar(item.get(key))
        if px is not None:
            return px
    return None


def update_inventory_ex_showroom_from_order_scrape(
    inventory_line_ids: list[int],
    vehicle_out: dict[str, Any],
) -> None:
    """
    Best-effort: map per-line ex-showroom from ``order_line_ex_showroom`` when present on scrape.
    Never raises: failures are logged and skipped.
    """
    try:
        raw = vehicle_out.get("order_line_ex_showroom")
        if raw is None:
            return
        # Minimal: single total on vehicle_price updates all lines equally (fallback).
        vp = vehicle_out.get("vehicle_price") or vehicle_out.get("total_amount")
        if vp and not isinstance(raw, (list, dict)):
            try:
                px = float(str(vp).replace(",", "").strip())
            except (TypeError, ValueError):
                return
            for iid in inventory_line_ids:
                try:
                    update_discount_and_ex_showroom(int(iid), ex_showroom_price=px)
                except Exception as exc:
                    logger.warning(
                        "update_inventory_ex_showroom_from_order_scrape: line update failed iid=%s: %s",
                        iid,
                        exc,
                    )
            return

        if isinstance(raw, list):
            for i, iid in enumerate(inventory_line_ids):
                if i >= len(raw):
                    break
                item = raw[i]
                pr: float | None = None
                if isinstance(item, dict):
                    pr = _ex_showroom_from_line_item_dict(item)
                else:
                    pr = _coerce_ex_showroom_scalar(item)
                if pr is None:
                    continue
                try:
                    update_discount_and_ex_showroom(int(iid), ex_showroom_price=float(pr))
                except Exception as exc:
                    logger.warning(
                        "update_inventory_ex_showroom_from_order_scrape: line update failed iid=%s: %s",
                        iid,
                        exc,
                    )
    except Exception as exc:
        logger.warning("update_inventory_ex_showroom_from_order_scrape: skipped: %s", exc)
