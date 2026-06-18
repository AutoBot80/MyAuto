"""Admin Cancel Invoice: roll back Saathi masters for a staging row and reset staging for re-run."""

from __future__ import annotations

import json
from typing import Any

from app.db import get_connection
from app.repositories.add_sales_staging import (
    _load_payload_json_for_update_on_cursor,
    sales_id_from_staging_payload,
)


class CancelStagingInvoiceError(ValueError):
    """Business rule violation for cancel invoice."""


def _int_from_payload(val: Any) -> int | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    if isinstance(val, float) and val.is_integer():
        n = int(val)
        return n if n > 0 else None
    s = str(val).strip()
    return int(s) if s.isdigit() else None


def _delete_sales_children_on_cursor(cur, sales_id: int) -> dict[str, int]:
    """Delete rows keyed by sales_id; return row counts per table."""
    counts: dict[str, int] = {
        "rc_status_sms_queue": 0,
        "service_reminders_queue": 0,
        "rto_payment_details": 0,
        "rto_queue": 0,
        "process_failure_log_cleared": 0,
    }
    sid = int(sales_id)

    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'process_failure_log'
        """
    )
    if cur.fetchone():
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'rto_queue'
            """
        )
        if cur.fetchone():
            cur.execute(
                """
                UPDATE process_failure_log pfl
                SET rto_queue_id = NULL
                WHERE pfl.rto_queue_id IN (
                    SELECT rq.rto_queue_id FROM rto_queue rq WHERE rq.sales_id = %s
                )
                """,
                (sid,),
            )
            counts["process_failure_log_cleared"] = int(cur.rowcount or 0)

    for table in ("rc_status_sms_queue", "service_reminders_queue", "rto_payment_details", "rto_queue"):
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        if not cur.fetchone():
            continue
        cur.execute(f"DELETE FROM {table} WHERE sales_id = %s", (sid,))
        counts[table] = int(cur.rowcount or 0)

    return counts


def _delete_masters_for_sales_on_cursor(
    cur,
    *,
    sales_id: int,
    customer_id: int,
    vehicle_id: int,
    clear_inventory_sold: bool = True,
) -> dict[str, int]:
    """Adapt revert_wrong_create_invoice_by_sales_id.sql logic."""
    counts: dict[str, int] = {
        "insurance_master": 0,
        "sales_master": 0,
        "customer_master": 0,
        "vehicle_master": 0,
        "vehicle_inventory_sold_cleared": 0,
    }
    sid = int(sales_id)
    cid = int(customer_id)
    vid = int(vehicle_id)

    child_counts = _delete_sales_children_on_cursor(cur, sid)
    counts.update(child_counts)

    cur.execute(
        "DELETE FROM insurance_master WHERE customer_id = %s AND vehicle_id = %s",
        (cid, vid),
    )
    counts["insurance_master"] = int(cur.rowcount or 0)

    cur.execute("DELETE FROM sales_master WHERE sales_id = %s", (sid,))
    if cur.rowcount != 1:
        raise CancelStagingInvoiceError(f"sales_master delete failed for sales_id={sid}")
    counts["sales_master"] = 1

    if clear_inventory_sold:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'vehicle_inventory_master'
            """
        )
        if cur.fetchone():
            cur.execute(
                """
                UPDATE vehicle_inventory_master vim
                SET sold_date = NULL
                FROM vehicle_master vm
                WHERE vm.vehicle_id = %s
                  AND TRIM(COALESCE(vim.chassis_no, '')) = TRIM(COALESCE(vm.chassis, ''))
                  AND TRIM(COALESCE(vim.engine_no, '')) = TRIM(COALESCE(vm.engine, ''))
                  AND TRIM(COALESCE(vm.chassis, '')) <> ''
                  AND TRIM(COALESCE(vm.engine, '')) <> ''
                """,
                (vid,),
            )
            counts["vehicle_inventory_sold_cleared"] = int(cur.rowcount or 0)

    cur.execute(
        """
        DELETE FROM customer_master cm
        WHERE cm.customer_id = %s
          AND NOT EXISTS (SELECT 1 FROM sales_master sm WHERE sm.customer_id = cm.customer_id)
          AND NOT EXISTS (SELECT 1 FROM insurance_master im WHERE im.customer_id = cm.customer_id)
        """,
        (cid,),
    )
    counts["customer_master"] = int(cur.rowcount or 0)

    cur.execute(
        """
        DELETE FROM vehicle_master vm
        WHERE vm.vehicle_id = %s
          AND NOT EXISTS (SELECT 1 FROM sales_master sm WHERE sm.vehicle_id = vm.vehicle_id)
          AND NOT EXISTS (SELECT 1 FROM insurance_master im WHERE im.vehicle_id = vm.vehicle_id)
        """,
        (vid,),
    )
    counts["vehicle_master"] = int(cur.rowcount or 0)

    return counts


def _delete_orphan_masters_on_cursor(
    cur,
    *,
    customer_id: int | None,
    vehicle_id: int | None,
) -> dict[str, int]:
    """Remove customer/vehicle masters when unreferenced (partial DMS prep, no sales row)."""
    counts = {"customer_master": 0, "vehicle_master": 0}
    if customer_id is not None and customer_id > 0:
        cur.execute(
            """
            DELETE FROM customer_master cm
            WHERE cm.customer_id = %s
              AND NOT EXISTS (SELECT 1 FROM sales_master sm WHERE sm.customer_id = cm.customer_id)
              AND NOT EXISTS (SELECT 1 FROM insurance_master im WHERE im.customer_id = cm.customer_id)
            """,
            (int(customer_id),),
        )
        counts["customer_master"] = int(cur.rowcount or 0)
    if vehicle_id is not None and vehicle_id > 0:
        cur.execute(
            """
            DELETE FROM vehicle_master vm
            WHERE vm.vehicle_id = %s
              AND NOT EXISTS (SELECT 1 FROM sales_master sm WHERE sm.vehicle_id = vm.vehicle_id)
              AND NOT EXISTS (SELECT 1 FROM insurance_master im WHERE im.vehicle_id = vm.vehicle_id)
            """,
            (int(vehicle_id),),
        )
        counts["vehicle_master"] = int(cur.rowcount or 0)
    return counts


def _reset_staging_on_cursor(cur, *, staging_id: str, dealer_id: int) -> None:
    """Set staging to draft with processing states cleared and master ids stripped from payload."""
    sid = (staging_id or "").strip()
    did = int(dealer_id)
    existing = _load_payload_json_for_update_on_cursor(cur, staging_id=sid, dealer_id=did)
    if existing is None:
        raise CancelStagingInvoiceError(f"Staging row not found: {sid}")
    cleaned = dict(existing)
    for key in (
        "customer_id",
        "vehicle_id",
        "sales_id",
        "insurance_id",
        "dms_vehicle_scrape",
        "dms_customer_collated",
    ):
        cleaned.pop(key, None)
    frag = json.dumps(cleaned, default=str)
    cur.execute(
        """
        UPDATE add_sales_staging
        SET status = 'draft',
            dms_state = 0,
            insurance_state = 0,
            updated_at = now(),
            payload_json = %s::jsonb
        WHERE staging_id = %s::uuid AND dealer_id = %s
        """,
        (frag, sid, did),
    )
    if cur.rowcount != 1:
        raise CancelStagingInvoiceError(f"Staging reset updated {cur.rowcount} rows (expected 1)")


def cancel_staging_invoice(
    *,
    staging_id: str,
    dealer_id: int,
    confirmation: str,
    expected_confirmation: str,
) -> dict[str, Any]:
    """
    Roll back Saathi DB masters for the staging sale and reset staging for In-process re-run.
    Does not cancel Siebel/DMS invoice.
    """
    sid = (staging_id or "").strip()
    did = int(dealer_id)
    conf = (confirmation or "").strip()
    expected = (expected_confirmation or "").strip()
    if not conf or conf.casefold() != expected.casefold():
        raise CancelStagingInvoiceError(
            f"Confirmation must match exactly: {expected!r}"
        )

    conn = get_connection()
    summary: dict[str, Any] = {
        "staging_id": sid,
        "dealer_id": did,
        "sales_id": None,
        "masters_deleted": {},
        "staging_reset": False,
    }
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT payload_json
                    FROM add_sales_staging
                    WHERE staging_id = %s::uuid AND dealer_id = %s
                    FOR UPDATE
                    """,
                    (sid, did),
                )
                row = cur.fetchone()
                if not row:
                    raise CancelStagingInvoiceError("Staging row not found")
                raw = row["payload_json"] if isinstance(row, dict) else row[0]
                if raw is None:
                    payload: dict[str, Any] = {}
                elif isinstance(raw, dict):
                    payload = dict(raw)
                else:
                    payload = json.loads(raw)

                sales_id = sales_id_from_staging_payload(payload)
                customer_id = _int_from_payload(payload.get("customer_id"))
                vehicle_id = _int_from_payload(payload.get("vehicle_id"))

                if sales_id is not None:
                    cur.execute(
                        """
                        SELECT customer_id, vehicle_id
                        FROM sales_master
                        WHERE sales_id = %s
                        """,
                        (sales_id,),
                    )
                    sm = cur.fetchone()
                    if sm:
                        customer_id = int(sm["customer_id"] if isinstance(sm, dict) else sm[0])
                        vehicle_id = int(sm["vehicle_id"] if isinstance(sm, dict) else sm[1])
                        summary["sales_id"] = sales_id
                        summary["masters_deleted"] = _delete_masters_for_sales_on_cursor(
                            cur,
                            sales_id=sales_id,
                            customer_id=customer_id,
                            vehicle_id=vehicle_id,
                        )
                    elif customer_id and vehicle_id:
                        summary["masters_deleted"] = _delete_orphan_masters_on_cursor(
                            cur, customer_id=customer_id, vehicle_id=vehicle_id
                        )
                elif customer_id or vehicle_id:
                    summary["masters_deleted"] = _delete_orphan_masters_on_cursor(
                        cur, customer_id=customer_id, vehicle_id=vehicle_id
                    )

                _reset_staging_on_cursor(cur, staging_id=sid, dealer_id=did)
                summary["staging_reset"] = True
        return summary
    finally:
        conn.close()
