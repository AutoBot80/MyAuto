"""Vehicle search by chassis and/or engine (partial / wildcard) with master + challan context."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from app.config import DEALER_ID
from app.db import get_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vehicle-search", tags=["vehicle-search"])


def _search_pattern(raw: str) -> str:
    """Build a PostgreSQL ILIKE pattern: * → %; short digit-only → suffix match; else substring."""
    s = raw.strip()
    if not s:
        return ""
    p = s.replace("*", "%")
    if "%" in p:
        return p
    if s.isdigit() and 4 <= len(s) <= 6:
        return f"%{s}"
    return f"%{p}%"


def _json_safe_row(row: dict) -> dict:
    out: dict = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, datetime):
            out[k] = v.strftime("%d-%m-%Y %H:%M:%S")
        elif isinstance(v, date):
            out[k] = v.strftime("%d-%m-%Y")
        elif isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, memoryview):
            out[k] = v.tobytes().decode("utf-8", errors="replace")
        else:
            out[k] = v
    return out


def _billing_ts(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%d-%m-%Y %H:%M:%S")
    return str(val)


@router.get("/search")
def search_vehicles(
    chassis: str | None = Query(None, description="Chassis / VIN fragment; * as wildcard; 4–6 digits = suffix match"),
    engine: str | None = Query(None, description="Engine number fragment; same rules as chassis"),
    dealer_id: int | None = Query(None, description="Dealer ID; OEM scope from this dealer"),
) -> dict:
    """
    Find vehicles tied to sales in the dealer's OEM: match **vehicle_master** chassis/engine
    (falls back to raw_frame_num / raw_engine_num when primary columns are blank).

    For each match returns **vehicle_master**, **vehicle_inventory_master** rows (same dealer;
    chassis/engine aligned with the vehicle), **sales_master**, and **challan** rows where
    **challan_details** + **vehicle_inventory_master** align with the vehicle's chassis/engine.
    """
    chassis_q = (chassis or "").strip()
    engine_q = (engine or "").strip()
    if not chassis_q and not engine_q:
        raise HTTPException(status_code=400, detail="Provide chassis and/or engine search text.")

    chassis_pat = _search_pattern(chassis_q) if chassis_q else ""
    engine_pat = _search_pattern(engine_q) if engine_q else ""

    did = dealer_id if dealer_id is not None else DEALER_ID

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT oem_id FROM dealer_ref WHERE dealer_id = %s", (did,))
            dealer_row = cur.fetchone()
            if not dealer_row:
                raise HTTPException(status_code=404, detail="Dealer not found.")
            oem_id = dealer_row.get("oem_id")
            if oem_id is None:
                raise HTTPException(status_code=400, detail="Dealer has no OEM configured.")

            wheres = ["dr.oem_id = %s"]
            params: list = [oem_id]
            if chassis_pat:
                wheres.append(
                    "COALESCE(NULLIF(BTRIM(vm.chassis), ''), NULLIF(BTRIM(vm.raw_frame_num), '')) ILIKE %s"
                )
                params.append(chassis_pat)
            if engine_pat:
                wheres.append(
                    "COALESCE(NULLIF(BTRIM(vm.engine), ''), NULLIF(BTRIM(vm.raw_engine_num), '')) ILIKE %s"
                )
                params.append(engine_pat)

            sql = f"""
                SELECT DISTINCT ON (vm.vehicle_id) vm.*
                FROM vehicle_master vm
                INNER JOIN sales_master sm ON sm.vehicle_id = vm.vehicle_id
                INNER JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                WHERE {' AND '.join(wheres)}
                ORDER BY vm.vehicle_id
            """
            cur.execute(sql, params)
            vm_rows = [dict(r) for r in cur.fetchall()]

        if not vm_rows:
            return {
                "found": False,
                "matches": [],
                "message": "No vehicle found for the given chassis/engine criteria in this OEM.",
            }

        matches: list[dict] = []
        for vm in vm_rows:
            vm_safe = _json_safe_row(vm)
            sales_row = None
            challans: list[dict] = []
            inventory_rows: list[dict] = []

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT sm.*
                    FROM sales_master sm
                    INNER JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                    WHERE sm.vehicle_id = %s AND dr.oem_id = %s
                    ORDER BY sm.billing_date DESC NULLS LAST
                    LIMIT 1
                    """,
                    (vm["vehicle_id"], oem_id),
                )
                sm_row = cur.fetchone()
                if sm_row:
                    smd = dict(sm_row)
                    bd = smd.get("billing_date")
                    smd["billing_date"] = _billing_ts(bd)
                    sales_row = _json_safe_row(smd)

                vc = (vm.get("chassis") or "").strip()
                ve = (vm.get("engine") or "").strip()
                if vc or ve:
                    inv_where = ["vim.dealer_id = %s"]
                    inv_params: list = [did]
                    if vc:
                        inv_where.append(
                            "TRIM(UPPER(COALESCE(vim.chassis_no, ''))) = TRIM(UPPER(%s))"
                        )
                        inv_params.append(vc)
                    if ve:
                        inv_where.append(
                            "TRIM(UPPER(COALESCE(vim.engine_no, ''))) = TRIM(UPPER(%s))"
                        )
                        inv_params.append(ve)
                    cur.execute(
                        f"""
                        SELECT vim.*
                        FROM vehicle_inventory_master vim
                        WHERE {' AND '.join(inv_where)}
                        ORDER BY vim.inventory_line_id DESC
                        """,
                        inv_params,
                    )
                    for r in cur.fetchall():
                        inventory_rows.append(_json_safe_row(dict(r)))

                    sub_where = ["(cm.dealer_from = %s OR cm.dealer_to = %s)"]
                    sub_params: list = [did, did]
                    if vc:
                        sub_where.append(
                            "TRIM(UPPER(COALESCE(vim.chassis_no, ''))) = TRIM(UPPER(%s))"
                        )
                        sub_params.append(vc)
                    if ve:
                        sub_where.append(
                            "TRIM(UPPER(COALESCE(vim.engine_no, ''))) = TRIM(UPPER(%s))"
                        )
                        sub_params.append(ve)

                    cur.execute(
                        f"""
                        SELECT cm.challan_id, cm.challan_date, cm.challan_book_num,
                               cm.dealer_from, cm.dealer_to, cm.num_vehicles,
                               cm.order_number, cm.invoice_number,
                               cm.total_ex_showroom_price, cm.total_discount,
                               cd.inventory_line_id,
                               vim.chassis_no, vim.engine_no, vim.model AS inv_model,
                               vim.variant AS inv_variant, vim.color AS inv_color,
                               vim.ex_showroom_price AS inv_ex_showroom,
                               vim.yard_id AS inv_yard_id
                        FROM challan_master cm
                        INNER JOIN challan_details cd ON cd.challan_id = cm.challan_id
                        INNER JOIN vehicle_inventory_master vim ON vim.inventory_line_id = cd.inventory_line_id
                        WHERE {' AND '.join(sub_where)}
                        ORDER BY cm.challan_id DESC
                        """,
                        sub_params,
                    )
                    for r in cur.fetchall():
                        challans.append(_json_safe_row(dict(r)))

            matches.append(
                {
                    "vehicle_master": vm_safe,
                    "vehicle_inventory": inventory_rows,
                    "sales_master": sales_row,
                    "challans": challans,
                }
            )

        return {"found": True, "matches": matches}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("vehicle_search failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        conn.close()
