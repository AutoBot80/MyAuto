"""Recent sales_master rows for Add Sales Invoices sub-tab."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_connection
from app.repositories.add_sales_staging import ist_window_start_timestamptz
from app.repositories.ist_date_ranges import validate_date_range


def _search_pattern(raw: str) -> str:
    """PostgreSQL ILIKE pattern: * → %; 4–6 digit-only → suffix; else substring."""
    s = raw.strip()
    if not s:
        return ""
    p = s.replace("*", "%")
    if "%" in p:
        return p
    if s.isdigit() and 4 <= len(s) <= 6:
        return f"%{s}"
    return f"%{p}%"


def _digits_mobile(mobile: str) -> int | None:
    digits = "".join(c for c in (mobile or "") if c.isdigit())
    if len(digits) >= 10:
        return int(digits[-10:])
    if len(digits) > 0:
        return int(digits)
    return None


def _format_invoice_date(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%d-%m-%Y")
    return str(val)


def _to_float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def _serialize_row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    mob = out.get("mobile_number")
    if mob is not None:
        out["mobile"] = str(mob)
    else:
        out["mobile"] = None
    out.pop("mobile_number", None)
    out["invoice_date"] = _format_invoice_date(out.pop("billing_date", None))
    out["ex_showroom_amount"] = _to_float_or_none(out.get("ex_showroom_amount"))
    out["insurance_premium"] = _to_float_or_none(out.get("insurance_premium"))
    out["cpa_premium"] = _to_float_or_none(out.get("cpa_premium"))
    for k in ("customer_name", "model", "invoice_number", "insurance_policy_num", "cpa_policy_num", "file_location"):
        v = out.get(k)
        if isinstance(v, str):
            out[k] = v.strip() or None
    return out


def list_recent_sales_invoices(
    *,
    dealer_id: int,
    days: int = 7,
    date_from: str | None = None,
    date_to: str | None = None,
    mobile: str | None = None,
    chassis: str | None = None,
    engine: str | None = None,
) -> list[dict[str, Any]]:
    """
    ``sales_master`` rows for dealer in last ``days`` IST calendar days on ``billing_date``,
    or inclusive ``date_from``/``date_to`` (dd-mm-yyyy IST) when both are valid.
    Newest first. Optional filters AND together.
    """
    did = int(dealer_id)

    where: list[str] = ["sm.dealer_id = %s"]
    params: list[Any] = [did]

    bounds = validate_date_range(date_from, date_to)
    if bounds is not None:
        start, end = bounds
        where.append("(sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date >= %s::date")
        where.append("(sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date <= %s::date")
        params.extend([start.isoformat(), end.isoformat()])
    else:
        d = max(1, min(int(days), 365))
        ist_start = ist_window_start_timestamptz(d)
        where.append(f"sm.billing_date >= {ist_start}")

    mobile_clean = (mobile or "").strip()
    if mobile_clean:
        mobile_int = _digits_mobile(mobile_clean)
        if mobile_int is None:
            return []
        where.append("cm.mobile_number = %s")
        params.append(mobile_int)

    chassis_clean = (chassis or "").strip()
    if chassis_clean:
        pat = _search_pattern(chassis_clean)
        if pat:
            where.append(
                "(COALESCE(vm.chassis, '') ILIKE %s OR COALESCE(vm.chassis_no, '') ILIKE %s)"
            )
            params.extend([pat, pat])

    engine_clean = (engine or "").strip()
    if engine_clean:
        pat_e = _search_pattern(engine_clean)
        if pat_e:
            where.append(
                "(COALESCE(vm.engine, '') ILIKE %s OR COALESCE(vm.engine_no, '') ILIKE %s)"
            )
            params.extend([pat_e, pat_e])

    where_sql = " AND ".join(where)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    sm.sales_id,
                    cm.name AS customer_name,
                    cm.mobile_number,
                    vm.model,
                    sm.billing_date,
                    sm.invoice_number,
                    sm.file_location,
                    vm.vehicle_ex_showroom_price AS ex_showroom_amount,
                    main_ins.policy_num AS insurance_policy_num,
                    main_ins.premium AS insurance_premium,
                    cpa_ins.policy_num AS cpa_policy_num,
                    cpa_ins.premium AS cpa_premium
                FROM sales_master sm
                JOIN customer_master cm ON cm.customer_id = sm.customer_id
                JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
                LEFT JOIN LATERAL (
                    SELECT im.policy_num, im.premium
                    FROM insurance_master im
                    WHERE im.customer_id = sm.customer_id
                      AND im.vehicle_id = sm.vehicle_id
                      AND im.insurance_type = 'Main'
                    ORDER BY im.insurance_year DESC NULLS LAST
                    LIMIT 1
                ) main_ins ON true
                LEFT JOIN LATERAL (
                    SELECT im.policy_num, im.premium
                    FROM insurance_master im
                    WHERE im.customer_id = sm.customer_id
                      AND im.vehicle_id = sm.vehicle_id
                      AND im.insurance_type = 'CPA'
                    ORDER BY im.insurance_year DESC NULLS LAST
                    LIMIT 1
                ) cpa_ins ON true
                WHERE {where_sql}
                ORDER BY sm.billing_date DESC NULLS LAST, sm.sales_id DESC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [_serialize_row(dict(r)) for r in rows]
