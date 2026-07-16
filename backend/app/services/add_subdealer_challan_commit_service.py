"""Persist challan_master + challan_details after successful DMS order (single transaction)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db import get_connection
from app.repositories.vehicle_inventory import (
    fetch_lines_for_batch_inventory,
    update_discount_and_ex_showroom,
)

logger = logging.getLogger(__name__)


def _norm_chassis_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (s or "").upper())


def scraped_total_ex_showroom_from_vehicle_out(vehicle_out: dict[str, Any]) -> float | None:
    """DMS scrape aggregate (``vehicle_ex_showroom_cost`` / ``vehicle_price``) for challan master total."""
    return _coerce_ex_showroom_scalar(
        vehicle_out.get("vehicle_ex_showroom_cost") or vehicle_out.get("vehicle_price")
    )


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
    reduce_discount_by_percent: float | None = None,
    total_ex_showroom_price: float | None = None,
) -> int:
    """
    Insert or update challan_master (totals from vehicle_inventory_master) and challan_details links.

    When ``total_ex_showroom_price`` is set (DMS ``Total_Cost`` scrape), it is written to
    ``challan_master.total_ex_showroom_price`` instead of summing inventory lines. Discount still
    comes from inventory.

    When ``order_number`` is non-empty, rows matching ``dealer_from`` + that order number are treated
    as one challan: keep the **oldest** ``challan_id``, ``UPDATE`` it with new totals / invoice / book,
    delete duplicate headers for the same order, then replace ``challan_details``. Otherwise insert
    a new header (legacy behaviour when order number is unknown).
    """
    if not inventory_line_ids:
        raise ValueError("inventory_line_ids required")

    ord_key = (order_number or "").strip() or None
    inv_key = (invoice_number or "").strip() or None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(ex_showroom_price), 0) AS total_ex_showroom,
                       COALESCE(SUM(discount), 0) AS total_discount
                FROM vehicle_inventory_master
                WHERE inventory_line_id = ANY(%s)
                """,
                (inventory_line_ids,),
            )
            sum_row = cur.fetchone()
            if not sum_row:
                total_ex = Decimal("0")
                total_disc = Decimal("0")
            elif isinstance(sum_row, dict):
                total_ex = sum_row.get("total_ex_showroom") or 0
                total_disc = sum_row.get("total_discount") or 0
            else:
                total_ex = sum_row[0]
                total_disc = sum_row[1]
            if isinstance(total_ex, Decimal):
                total_ex_f = float(total_ex)
            else:
                total_ex_f = float(total_ex or 0)
            if isinstance(total_disc, Decimal):
                total_disc_f = float(total_disc)
            else:
                total_disc_f = float(total_disc or 0)

            if total_ex_showroom_price is not None:
                try:
                    total_ex_f = float(total_ex_showroom_price)
                except (TypeError, ValueError):
                    pass

            created = datetime.now(timezone.utc)
            challan_id: int

            if ord_key:
                cur.execute(
                    """
                    SELECT challan_id
                    FROM challan_master
                    WHERE dealer_from = %s
                      AND TRIM(COALESCE(order_number, '')) = %s
                    ORDER BY challan_id ASC
                    """,
                    (int(dealer_from), ord_key),
                )
                existing = cur.fetchall()
                if existing:
                    first_row = existing[0]
                    challan_id = int(
                        first_row["challan_id"]
                        if isinstance(first_row, dict)
                        else first_row[0]
                    )
                    if len(existing) > 1:
                        cur.execute(
                            """
                            DELETE FROM challan_master
                            WHERE dealer_from = %s
                              AND TRIM(COALESCE(order_number, '')) = %s
                              AND challan_id <> %s
                            """,
                            (int(dealer_from), ord_key, challan_id),
                        )
                    cur.execute(
                        "DELETE FROM challan_details WHERE challan_id = %s",
                        (challan_id,),
                    )
                    cur.execute(
                        """
                        UPDATE challan_master SET
                            challan_date = %s,
                            challan_book_num = %s,
                            dealer_to = %s,
                            num_vehicles = %s,
                            order_number = %s,
                            invoice_number = COALESCE(NULLIF(TRIM(%s), ''), invoice_number),
                            total_ex_showroom_price = %s,
                            total_discount = %s,
                            add_transport_cost = %s,
                            transport_cost_per_vehicle = %s,
                            reduce_discount_by_percent = %s
                        WHERE challan_id = %s
                        """,
                        (
                            challan_date,
                            challan_book_num,
                            int(dealer_to),
                            len(inventory_line_ids),
                            ord_key,
                            inv_key or "",
                            total_ex_f,
                            total_disc_f,
                            bool(add_transport_cost),
                            None if not add_transport_cost else transport_cost_per_vehicle,
                            None if not add_transport_cost else reduce_discount_by_percent,
                            challan_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO challan_master (
                            challan_date, challan_book_num, dealer_from, dealer_to,
                            num_vehicles, order_number, invoice_number,
                            total_ex_showroom_price, total_discount,
                            add_transport_cost, transport_cost_per_vehicle,
                            reduce_discount_by_percent,
                            created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING challan_id
                        """,
                        (
                            challan_date,
                            challan_book_num,
                            int(dealer_from),
                            int(dealer_to),
                            len(inventory_line_ids),
                            ord_key,
                            inv_key,
                            total_ex_f,
                            total_disc_f,
                            bool(add_transport_cost),
                            None if not add_transport_cost else transport_cost_per_vehicle,
                            None if not add_transport_cost else reduce_discount_by_percent,
                            created,
                        ),
                    )
                    cid_row = cur.fetchone()
                    challan_id = int(
                        cid_row["challan_id"] if isinstance(cid_row, dict) else cid_row[0]
                    )
            else:
                cur.execute(
                    """
                    INSERT INTO challan_master (
                        challan_date, challan_book_num, dealer_from, dealer_to,
                        num_vehicles, order_number, invoice_number,
                        total_ex_showroom_price, total_discount,
                        add_transport_cost, transport_cost_per_vehicle,
                        reduce_discount_by_percent,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING challan_id
                    """,
                    (
                        challan_date,
                        challan_book_num,
                        int(dealer_from),
                        int(dealer_to),
                        len(inventory_line_ids),
                        None,
                        inv_key,
                        total_ex_f,
                        total_disc_f,
                        bool(add_transport_cost),
                        None if not add_transport_cost else transport_cost_per_vehicle,
                        None if not add_transport_cost else reduce_discount_by_percent,
                        created,
                    ),
                )
                cid_row = cur.fetchone()
                challan_id = int(
                    cid_row["challan_id"] if isinstance(cid_row, dict) else cid_row[0]
                )

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
    s = str(val).strip()
    if not s:
        return None
    s = re.sub(r"^(?:Rs\.?|INR)\s*", "", s, flags=re.I)
    s = s.replace(",", "")
    cleaned = re.sub(r"[^\d.]", "", s)
    if not cleaned:
        return None
    try:
        return float(cleaned)
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
            if len(inventory_line_ids) == 1:
                px = _coerce_ex_showroom_scalar(
                    vehicle_out.get("vehicle_ex_showroom_cost") or vehicle_out.get("vehicle_price")
                )
                if px is not None:
                    try:
                        update_discount_and_ex_showroom(int(inventory_line_ids[0]), ex_showroom_price=px)
                    except Exception as exc:
                        logger.warning(
                            "update_inventory_ex_showroom_from_order_scrape: single-line update failed "
                            "iid=%s: %s",
                            inventory_line_ids[0],
                            exc,
                        )
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
            # Prefer chassis/VIN match when scrape rows carry full_chassis/vin (partial attach / resume).
            by_chassis: dict[str, int] = {}
            try:
                for _ln in fetch_lines_for_batch_inventory(list(inventory_line_ids)):
                    _ck = _norm_chassis_key(str(_ln.get("chassis_no") or ""))
                    if _ck and int(_ln.get("inventory_line_id") or 0):
                        by_chassis[_ck] = int(_ln["inventory_line_id"])
            except Exception as _fetch_exc:
                logger.warning(
                    "update_inventory_ex_showroom_from_order_scrape: chassis lookup failed: %s",
                    _fetch_exc,
                )
            chassis_matched = False
            if by_chassis:
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    ch = (
                        item.get("full_chassis")
                        or item.get("vin")
                        or item.get("chassis")
                        or item.get("chassis_no")
                        or ""
                    )
                    ck = _norm_chassis_key(str(ch))
                    iid_m = by_chassis.get(ck) if ck else None
                    pr = _ex_showroom_from_line_item_dict(item)
                    if iid_m is None or pr is None:
                        continue
                    chassis_matched = True
                    try:
                        update_discount_and_ex_showroom(int(iid_m), ex_showroom_price=float(pr))
                    except Exception as exc:
                        logger.warning(
                            "update_inventory_ex_showroom_from_order_scrape: chassis-line update failed "
                            "iid=%s: %s",
                            iid_m,
                            exc,
                        )
            if chassis_matched:
                return
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
