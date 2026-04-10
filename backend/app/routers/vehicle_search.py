"""Vehicle search by chassis and/or engine (partial / wildcard) — no dealer scoping on match keys."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

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


def _alnum_loose_pattern(raw: str) -> str | None:
    """Strip non-alphanumeric from user text; if 4+ chars remain, build %%…%% ILIKE for noisy DB values."""
    s = re.sub(r"[^0-9A-Za-z]", "", (raw or "").strip())
    if len(s) < 4:
        return None
    return f"%{s}%"


def _inventory_patterns_for_sql(chassis_pat: str, engine_pat: str, chassis_q: str, engine_q: str) -> tuple[list[str], list]:
    """ILIKE on vim columns OR stripped-alphanumeric ILIKE."""
    frags: list[str] = []
    params: list = []
    if chassis_pat:
        loose = _alnum_loose_pattern(chassis_q)
        if loose and loose != chassis_pat:
            frags.append(
                "("
                "COALESCE(vim.chassis_no, '') ILIKE %s OR "
                "REGEXP_REPLACE(COALESCE(vim.chassis_no, ''), '[^0-9A-Za-z]', '', 'g') ILIKE %s"
                ")"
            )
            params.extend([chassis_pat, loose])
        else:
            frags.append("COALESCE(vim.chassis_no, '') ILIKE %s")
            params.append(chassis_pat)
    if engine_pat:
        loose_e = _alnum_loose_pattern(engine_q)
        if loose_e and loose_e != engine_pat:
            frags.append(
                "("
                "COALESCE(vim.engine_no, '') ILIKE %s OR "
                "REGEXP_REPLACE(COALESCE(vim.engine_no, ''), '[^0-9A-Za-z]', '', 'g') ILIKE %s"
                ")"
            )
            params.extend([engine_pat, loose_e])
        else:
            frags.append("COALESCE(vim.engine_no, '') ILIKE %s")
            params.append(engine_pat)
    if not frags:
        return ["FALSE"], []
    joined = " AND ".join(frags) if len(frags) > 1 else frags[0]
    return [f"({joined})"], params


def _detail_raw_pattern_sql(
    chassis_pat: str, engine_pat: str, chassis_q: str, engine_q: str
) -> tuple[str, list]:
    """Pattern match on challan_details_staging raw_chassis / raw_engine."""
    frags: list[str] = []
    params: list = []
    if chassis_pat:
        loose = _alnum_loose_pattern(chassis_q)
        if loose and loose != chassis_pat:
            frags.append(
                "("
                "COALESCE(d.raw_chassis, '') ILIKE %s OR "
                "REGEXP_REPLACE(COALESCE(d.raw_chassis, ''), '[^0-9A-Za-z]', '', 'g') ILIKE %s"
                ")"
            )
            params.extend([chassis_pat, loose])
        else:
            frags.append("COALESCE(d.raw_chassis, '') ILIKE %s")
            params.append(chassis_pat)
    if engine_pat:
        loose_e = _alnum_loose_pattern(engine_q)
        if loose_e and loose_e != engine_pat:
            frags.append(
                "("
                "COALESCE(d.raw_engine, '') ILIKE %s OR "
                "REGEXP_REPLACE(COALESCE(d.raw_engine, ''), '[^0-9A-Za-z]', '', 'g') ILIKE %s"
                ")"
            )
            params.extend([engine_pat, loose_e])
        else:
            frags.append("COALESCE(d.raw_engine, '') ILIKE %s")
            params.append(engine_pat)
    if not frags:
        return "FALSE", []
    return "(" + " AND ".join(frags) + ")", params


def _fetch_staging_detail_rows_global(
    cur,
    chassis_pat: str,
    engine_pat: str,
    chassis_q: str,
    engine_q: str,
    limit: int,
) -> list[dict]:
    raw_sql, raw_params = _detail_raw_pattern_sql(chassis_pat, engine_pat, chassis_q, engine_q)
    cur.execute(
        f"""
        SELECT d.challan_detail_staging_id, d.challan_batch_id, d.raw_chassis, d.raw_engine,
               d.status, d.inventory_line_id, d.last_error, d.created_at,
               s.challan_book_num, s.challan_date, s.from_dealer_id, s.to_dealer_id, s.invoice_status,
               s.num_vehicles
        FROM challan_details_staging d
        INNER JOIN challan_master_staging s ON s.challan_batch_id = d.challan_batch_id
        WHERE {raw_sql}
        ORDER BY d.created_at DESC NULLS LAST
        LIMIT %s
        """,
        (*raw_params, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def _staging_inventory_hits_as_vim_shape(
    cur,
    chassis_pat: str,
    engine_pat: str,
    chassis_q: str,
    engine_q: str,
) -> list[dict]:
    """Use challan staging lines and prefer linked inventory rows."""
    rows = _fetch_staging_detail_rows_global(
        cur, chassis_pat, engine_pat, chassis_q, engine_q, limit=50
    )
    out: list[dict] = []
    seen_vim: set[int] = set()
    seen_stg: set[int] = set()
    for r in rows:
        sid = r.get("challan_detail_staging_id")
        if isinstance(sid, int) and sid in seen_stg:
            continue
        if isinstance(sid, int):
            seen_stg.add(sid)
        iid = r.get("inventory_line_id")
        if iid:
            iid_int = int(iid) if isinstance(iid, (int, float)) else None
            if iid_int is not None and iid_int in seen_vim:
                continue
            cur.execute(
                "SELECT * FROM vehicle_inventory_master WHERE inventory_line_id = %s",
                (iid,),
            )
            vim_row = cur.fetchone()
            if vim_row:
                vd = dict(vim_row)
                vid = vd.get("inventory_line_id")
                if isinstance(vid, int):
                    seen_vim.add(vid)
                out.append(vd)
                continue
        out.append(
            {
                "inventory_line_id": iid,
                "chassis_no": r.get("raw_chassis"),
                "engine_no": r.get("raw_engine"),
                "_from_staging": True,
                "challan_detail_staging_id": r.get("challan_detail_staging_id"),
            }
        )
    return out


def _fetch_inventory_lines(
    cur,
    chassis_pat: str,
    engine_pat: str,
    chassis_q: str,
    engine_q: str,
    limit: int,
) -> list[dict]:
    """All yard rows matching chassis/engine patterns (any dealer)."""
    inv_frags, inv_params = _inventory_patterns_for_sql(chassis_pat, engine_pat, chassis_q, engine_q)
    cur.execute(
        f"""
        SELECT vim.*
        FROM vehicle_inventory_master vim
        WHERE {inv_frags[0]}
        ORDER BY vim.inventory_line_id DESC
        LIMIT %s
        """,
        (*inv_params, limit),
    )
    return [dict(r) for r in cur.fetchall()]


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


def _billing_date_ddmmyyyy(val) -> str | None:
    """Format `sales_master.billing_date` for API as dd/mm/yyyy (calendar date only)."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%d/%m/%Y")
    if isinstance(val, date):
        return val.strftime("%d/%m/%Y")
    s = str(val).strip()
    return s if s else None


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
        "_match_note": "No vehicle_master row found; matched from yard inventory or staging only.",
    }


def _inventory_sql_filters(
    vc: str,
    ve: str,
    chassis_pat: str,
    engine_pat: str,
) -> tuple[list[str], list]:
    """Match inventory lines to a vehicle row or user ILIKE patterns."""
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
    chassis_pat: str,
    engine_pat: str,
) -> dict:
    """Sales, inventory (all dealers), committed challans for one vehicle_master row."""
    sales_row = None
    raw_vid = vm.get("vehicle_id")
    try:
        vid_i = int(raw_vid) if raw_vid is not None else None
    except (TypeError, ValueError):
        vid_i = None
    if vid_i is not None:
        # Avoid ``SELECT sm.*`` with JOINs: duplicate column names (e.g. customer_id / dealer_id)
        # collapse in RealDictCursor so joined enrichments were lost. Use explicit ``sm`` columns
        # and scalar subqueries; dealer_name also falls back to yard inventory when ``sm.dealer_id`` is null.
        cur.execute(
            """
            SELECT
                sm.sales_id,
                sm.customer_id,
                sm.vehicle_id,
                sm.billing_date,
                sm.dealer_id,
                sm.vahan_application_id,
                sm.rto_charges,
                sm.file_location,
                sm.order_number,
                sm.invoice_number,
                sm.enquiry_number,
                (SELECT cm.name FROM customer_master cm WHERE cm.customer_id = sm.customer_id LIMIT 1)
                    AS customer_name,
                (
                    SELECT dr.dealer_name FROM dealer_ref dr
                    WHERE dr.dealer_id = COALESCE(
                        sm.dealer_id,
                        (
                            SELECT vim.dealer_id FROM vehicle_inventory_master vim
                            WHERE TRIM(UPPER(COALESCE(vim.chassis_no, ''))) = (
                                SELECT TRIM(UPPER(COALESCE(
                                    NULLIF(BTRIM(vmc.chassis), ''),
                                    NULLIF(BTRIM(vmc.raw_frame_num), '')
                                )))
                                FROM vehicle_master vmc WHERE vmc.vehicle_id = sm.vehicle_id
                            )
                            ORDER BY vim.inventory_line_id DESC
                            LIMIT 1
                        )
                    )
                    LIMIT 1
                ) AS dealer_name,
                (SELECT cm.address FROM customer_master cm WHERE cm.customer_id = sm.customer_id LIMIT 1)
                    AS customer_address,
                (SELECT cm.mobile_number FROM customer_master cm WHERE cm.customer_id = sm.customer_id LIMIT 1)
                    AS customer_mobile,
                (SELECT cm.alt_phone_num FROM customer_master cm WHERE cm.customer_id = sm.customer_id LIMIT 1)
                    AS alt_phone_num,
                (SELECT cm.financier FROM customer_master cm WHERE cm.customer_id = sm.customer_id LIMIT 1)
                    AS financier_name
            FROM sales_master sm
            WHERE sm.vehicle_id = %s
            ORDER BY sm.billing_date DESC NULLS LAST
            LIMIT 1
            """,
            (vid_i,),
        )
        sm_row = cur.fetchone()
        if sm_row:
            smd = dict(sm_row)
            smd["billing_date"] = _billing_date_ddmmyyyy(smd.get("billing_date"))
            sales_row = _json_safe_row(smd)

    vc = (vm.get("chassis") or "").strip()
    ve = (vm.get("engine") or "").strip()

    inv_base: list[str] = []
    inv_p: list = []
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

    sub_where: list[str] = []
    sub_params: list = []
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

    return {
        "vehicle_master": _json_safe_row(vm),
        "vehicle_inventory": inventory_rows,
        "sales_master": sales_row,
        "challans": challans,
    }


@router.get("/search")
def search_vehicles(
    chassis: str | None = Query(None, description="Chassis / VIN fragment; * as wildcard; 4–6 digits = suffix match"),
    engine: str | None = Query(None, description="Engine number fragment; same rules as chassis"),
    dealer_id: int | None = Query(None, description="Ignored; matching uses chassis/engine only."),
) -> dict:
    """
    Match **vehicle_master**, yard inventory, and challans by **chassis** and **engine** patterns only
    (no dealer filter; internal fallback may use challan staging to discover inventory rows).
    """
    del dealer_id  # optional query param kept for backward compatibility

    chassis_q = (chassis or "").strip()
    engine_q = (engine or "").strip()
    if not chassis_q and not engine_q:
        raise HTTPException(status_code=400, detail="Provide chassis and/or engine search text.")

    chassis_pat = _search_pattern(chassis_q) if chassis_q else ""
    engine_pat = _search_pattern(engine_q) if engine_q else ""

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            wheres: list[str] = []
            params: list = []
            if chassis_pat:
                wheres.append(f"{_vm_chassis_expr()} ILIKE %s")
                params.append(chassis_pat)
            if engine_pat:
                wheres.append(
                    "("
                    f"{_vm_engine_expr()} ILIKE %s OR ( "
                    f"{_vm_chassis_expr()} <> '' AND EXISTS ( "
                    "SELECT 1 FROM vehicle_inventory_master vim_eng "
                    "WHERE TRIM(UPPER(COALESCE(vim_eng.chassis_no, ''))) = "
                    f"TRIM(UPPER({_vm_chassis_expr()})) "
                    "AND COALESCE(vim_eng.engine_no, '') ILIKE %s "
                    ") )"
                    ")"
                )
                params.extend([engine_pat, engine_pat])

            sql = f"""
                SELECT DISTINCT ON (vm.vehicle_id) vm.*
                FROM vehicle_master vm
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
                vim_list = _fetch_inventory_lines(
                    cur, chassis_pat, engine_pat, chassis_q, engine_q, limit=100
                )
                if not vim_list:
                    vim_list = _staging_inventory_hits_as_vim_shape(
                        cur, chassis_pat, engine_pat, chassis_q, engine_q
                    )

                for vim in vim_list:
                    ch = (vim.get("chassis_no") or "").strip()
                    eng = (vim.get("engine_no") or "").strip()
                    cur.execute(
                        """
                        SELECT vm.*
                        FROM vehicle_master vm
                        WHERE TRIM(UPPER(COALESCE(NULLIF(BTRIM(vm.chassis), ''), NULLIF(BTRIM(vm.raw_frame_num), ''))))
                              = TRIM(UPPER(COALESCE(%s, '')))
                          AND TRIM(UPPER(COALESCE(NULLIF(BTRIM(vm.engine), ''), NULLIF(BTRIM(vm.raw_engine_num), ''))))
                              = TRIM(UPPER(COALESCE(%s, '')))
                        LIMIT 1
                        """,
                        (ch, eng),
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
                        cw, cp = _inventory_sql_filters(
                            (vim.get("chassis_no") or "").strip(),
                            (vim.get("engine_no") or "").strip(),
                            chassis_pat,
                            engine_pat,
                        )
                        sub_where = list(cw)
                        sub_params = list(cp)
                        if not sub_where:
                            sub_where = ["FALSE"]
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
                            }
                        )
                    return {"found": True, "matches": matches}

        if not vm_rows:
            return {
                "found": False,
                "matches": [],
                "message": "No match for these chassis/engine patterns in vehicle master or yard inventory.",
            }

        matches: list[dict] = []
        with conn.cursor() as cur:
            for vm in vm_rows:
                matches.append(_fetch_match_bundle(cur, vm, chassis_pat, engine_pat))

        return {"found": True, "matches": matches}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("vehicle_search failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        conn.close()
