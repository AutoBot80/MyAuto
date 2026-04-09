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


def _vm_chassis_expr() -> str:
    return "COALESCE(NULLIF(BTRIM(vm.chassis), ''), NULLIF(BTRIM(vm.raw_frame_num), ''))"


def _vm_engine_expr() -> str:
    return "COALESCE(NULLIF(BTRIM(vm.engine), ''), NULLIF(BTRIM(vm.raw_engine_num), ''))"


def _synthetic_vehicle_master_from_vim(vim: dict) -> dict:
    return {
        "vehicle_id": None,
        "chassis": vim.get("chassis_no"),
        "engine": vim.get("engine_no"),
        "model": vim.get("model"),
        "colour": vim.get("color"),
        "_match_note": "No vehicle_master row with a sale in this OEM; matched from yard inventory only.",
    }


def _fetch_staging_lines(
    cur,
    did: int,
    chassis_pat: str,
    engine_pat: str,
    inventory_line_ids: list[int],
) -> list[dict]:
    stg_wheres = ["(s.from_dealer_id = %s OR s.to_dealer_id = %s)"]
    stg_params: list = [did, did]
    or_parts = []
    if chassis_pat and engine_pat:
        or_parts.append("(COALESCE(d.raw_chassis, '') ILIKE %s AND COALESCE(d.raw_engine, '') ILIKE %s)")
        stg_params.extend([chassis_pat, engine_pat])
    elif chassis_pat:
        or_parts.append("COALESCE(d.raw_chassis, '') ILIKE %s")
        stg_params.append(chassis_pat)
    elif engine_pat:
        or_parts.append("COALESCE(d.raw_engine, '') ILIKE %s")
        stg_params.append(engine_pat)
    if inventory_line_ids:
        or_parts.append("d.inventory_line_id = ANY(%s)")
        stg_params.append(inventory_line_ids)
    if not or_parts:
        return []
    stg_wheres.append(f"({' OR '.join(or_parts)})")
    cur.execute(
        f"""
        SELECT d.challan_detail_staging_id, d.challan_batch_id, d.raw_chassis, d.raw_engine,
               d.status, d.inventory_line_id, d.last_error, d.created_at,
               s.challan_book_num, s.challan_date, s.from_dealer_id, s.to_dealer_id, s.invoice_status,
               s.num_vehicles
        FROM challan_details_staging d
        INNER JOIN challan_master_staging s ON s.challan_batch_id = d.challan_batch_id
        WHERE {' AND '.join(stg_wheres)}
        ORDER BY d.created_at DESC NULLS LAST
        LIMIT 200
        """,
        stg_params,
    )
    return [_json_safe_row(dict(r)) for r in cur.fetchall()]


def _inventory_sql_filters(
    vc: str,
    ve: str,
    chassis_pat: str,
    engine_pat: str,
) -> tuple[list[str], list]:
    """Match inventory lines to a vehicle row (exact vm strings) or to user ILIKE patterns."""
    inv_where: list[str] = []
    inv_params: list = []
    if vc:
        inv_where.append("TRIM(UPPER(COALESCE(vim.chassis_no, ''))) = TRIM(UPPER(%s))")
        inv_params.append(vc)
    elif chassis_pat:
        inv_where.append("COALESCE(vim.chassis_no, '') ILIKE %s")
        inv_params.append(chassis_pat)
    if ve:
        inv_where.append("TRIM(UPPER(COALESCE(vim.engine_no, ''))) = TRIM(UPPER(%s))")
        inv_params.append(ve)
    elif engine_pat:
        inv_where.append("COALESCE(vim.engine_no, '') ILIKE %s")
        inv_params.append(engine_pat)
    return inv_where, inv_params


def _fetch_match_bundle(
    cur,
    vm: dict,
    did: int,
    oem_id: int,
    chassis_pat: str,
    engine_pat: str,
) -> dict:
    """Sales, inventory, committed challans, staging lines for one vehicle_master row."""
    sales_row = None
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
        smd["billing_date"] = _billing_ts(smd.get("billing_date"))
        sales_row = _json_safe_row(smd)

    vc = (vm.get("chassis") or "").strip()
    ve = (vm.get("engine") or "").strip()

    inv_base = ["vim.dealer_id = %s"]
    inv_p: list = [did]
    ew, ep = _inventory_sql_filters(vc, ve, chassis_pat, engine_pat)
    if ew:
        inv_base.extend(ew)
        inv_p.extend(ep)
    else:
        inv_base.append("FALSE")

    cur.execute(
        f"""
        SELECT vim.*
        FROM vehicle_inventory_master vim
        WHERE {' AND '.join(inv_base)}
        ORDER BY vim.inventory_line_id DESC
        """,
        inv_p,
    )
    inventory_rows = [_json_safe_row(dict(r)) for r in cur.fetchall()]

    sub_where = ["(cm.dealer_from = %s OR cm.dealer_to = %s)"]
    sub_params: list = [did, did]
    if ew:
        sub_where.extend(ew)
        sub_params.extend(ep)
    else:
        sub_where.append("FALSE")

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
    challans = [_json_safe_row(dict(r)) for r in cur.fetchall()]

    inv_ids = [r["inventory_line_id"] for r in inventory_rows if r.get("inventory_line_id") is not None]
    staging = _fetch_staging_lines(cur, did, chassis_pat, engine_pat, inv_ids)

    return {
        "vehicle_master": _json_safe_row(vm),
        "vehicle_inventory": inventory_rows,
        "sales_master": sales_row,
        "challans": challans,
        "challan_details_staging": staging,
    }


@router.get("/search")
def search_vehicles(
    chassis: str | None = Query(None, description="Chassis / VIN fragment; * as wildcard; 4–6 digits = suffix match"),
    engine: str | None = Query(None, description="Engine number fragment; same rules as chassis"),
    dealer_id: int | None = Query(None, description="Dealer ID; OEM scope from this dealer"),
) -> dict:
    """
    Find vehicles: **vehicle_master** + **sales_master** in OEM, with engine match satisfied by
    **vehicle_inventory_master** when master engine fields are blank. If none, fall back to
    **vehicle_inventory_master** (this dealer) by ILIKE, then resolve **vehicle_master** or return
    inventory-only synthetic master. Includes **challan_details_staging** lines matching patterns
    or linked **inventory_line_id**.
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
                wheres.append(f"{_vm_chassis_expr()} ILIKE %s")
                params.append(chassis_pat)
            if engine_pat:
                wheres.append(
                    "("
                    f"{_vm_engine_expr()} ILIKE %s OR ( "
                    f"{_vm_chassis_expr()} <> '' AND EXISTS ( "
                    "SELECT 1 FROM vehicle_inventory_master vim_eng "
                    "WHERE vim_eng.dealer_id = %s "
                    "AND TRIM(UPPER(COALESCE(vim_eng.chassis_no, ''))) = "
                    f"TRIM(UPPER({_vm_chassis_expr()})) "
                    "AND COALESCE(vim_eng.engine_no, '') ILIKE %s "
                    ") )"
                    ")"
                )
                params.extend([engine_pat, did, engine_pat])

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

            seen_vehicle_ids: set[int] = set()
            for r in vm_rows:
                vid = r.get("vehicle_id")
                if isinstance(vid, int):
                    seen_vehicle_ids.add(vid)

            if not vm_rows:
                vim_wheres = ["vim.dealer_id = %s"]
                vim_params: list = [did]
                if chassis_pat:
                    vim_wheres.append("COALESCE(vim.chassis_no, '') ILIKE %s")
                    vim_params.append(chassis_pat)
                if engine_pat:
                    vim_wheres.append("COALESCE(vim.engine_no, '') ILIKE %s")
                    vim_params.append(engine_pat)
                cur.execute(
                    f"""
                    SELECT vim.*
                    FROM vehicle_inventory_master vim
                    WHERE {' AND '.join(vim_wheres)}
                    ORDER BY vim.inventory_line_id DESC
                    LIMIT 100
                    """,
                    vim_params,
                )
                vim_list = [dict(r) for r in cur.fetchall()]

                for vim in vim_list:
                    ch = (vim.get("chassis_no") or "").strip()
                    eng = (vim.get("engine_no") or "").strip()
                    cur.execute(
                        """
                        SELECT vm.*
                        FROM vehicle_master vm
                        INNER JOIN sales_master sm ON sm.vehicle_id = vm.vehicle_id
                        INNER JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id AND dr.oem_id = %s
                        WHERE TRIM(UPPER(COALESCE(NULLIF(BTRIM(vm.chassis), ''), NULLIF(BTRIM(vm.raw_frame_num), ''))))
                              = TRIM(UPPER(COALESCE(%s, '')))
                          AND TRIM(UPPER(COALESCE(NULLIF(BTRIM(vm.engine), ''), NULLIF(BTRIM(vm.raw_engine_num), ''))))
                              = TRIM(UPPER(COALESCE(%s, '')))
                        LIMIT 1
                        """,
                        (oem_id, ch, eng),
                    )
                    row = cur.fetchone()
                    if row:
                        vmd = dict(row)
                        vid = vmd.get("vehicle_id")
                        if isinstance(vid, int) and vid not in seen_vehicle_ids:
                            vm_rows.append(vmd)
                            seen_vehicle_ids.add(vid)

                if not vm_rows and vim_list:
                    matches = []
                    for vim in vim_list:
                        vim_safe = _json_safe_row(vim)
                        inv_ids = [vim_safe["inventory_line_id"]] if vim_safe.get("inventory_line_id") else []
                        staging = _fetch_staging_lines(cur, did, chassis_pat, engine_pat, inv_ids)
                        sub_where = ["(cm.dealer_from = %s OR cm.dealer_to = %s)"]
                        sub_params: list = [did, did]
                        cw, cp = _inventory_sql_filters(
                            (vim.get("chassis_no") or "").strip(),
                            (vim.get("engine_no") or "").strip(),
                            chassis_pat,
                            engine_pat,
                        )
                        sub_where.extend(cw)
                        sub_params.extend(cp)
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
                        challans = [_json_safe_row(dict(r)) for r in cur.fetchall()]
                        matches.append(
                            {
                                "vehicle_master": _synthetic_vehicle_master_from_vim(vim),
                                "vehicle_inventory": [vim_safe],
                                "sales_master": None,
                                "challans": challans,
                                "challan_details_staging": staging,
                            }
                        )
                    return {"found": True, "matches": matches}

        if not vm_rows:
            return {
                "found": False,
                "matches": [],
                "message": "No vehicle found for the given chassis/engine criteria (including yard inventory).",
            }

        matches: list[dict] = []
        with conn.cursor() as cur:
            for vm in vm_rows:
                matches.append(_fetch_match_bundle(cur, vm, did, oem_id, chassis_pat, engine_pat))

        return {"found": True, "matches": matches}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("vehicle_search failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        conn.close()
