"""Dealer Saathi dashboard: RTO queue depth, IST 7-day sales/challan matrices (principal drill-downs)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.db import get_connection

_IST = ZoneInfo("Asia/Kolkata")

_CHALLAN_BUCKET_DATE_ONLY = """
CASE
    WHEN trim(BOTH FROM coalesce(cm.challan_date, '')) ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'
        THEN to_date(trim(BOTH FROM cm.challan_date), 'DD/MM/YYYY')
    WHEN trim(BOTH FROM coalesce(cm.challan_date, '')) ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{2}$'
        THEN to_date(trim(BOTH FROM cm.challan_date), 'DD/MM/YY')
    ELSE NULL
END
"""

_CHALLAN_BUCKET_WITH_CREATED = """
COALESCE(
    (cm.created_at AT TIME ZONE 'Asia/Kolkata')::date,
    CASE
        WHEN trim(BOTH FROM coalesce(cm.challan_date, '')) ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'
            THEN to_date(trim(BOTH FROM cm.challan_date), 'DD/MM/YYYY')
        WHEN trim(BOTH FROM coalesce(cm.challan_date, '')) ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{2}$'
            THEN to_date(trim(BOTH FROM cm.challan_date), 'DD/MM/YY')
        ELSE NULL
    END
)
"""


def ist_last_7_days() -> tuple[list[date], list[str], str, str]:
    """Seven IST calendar dates oldest→newest, plus ``start``/``end`` ISO for SQL bounds."""
    today = datetime.now(_IST).date()
    start = today - timedelta(days=6)
    days = [start + timedelta(days=i) for i in range(7)]
    return days, [d.isoformat() for d in days], start.isoformat(), today.isoformat()


def ist_calendar_window_last_n_days(n: int) -> tuple[date, date]:
    """Inclusive IST calendar window ending today: ``n`` days (``n`` >= 1)."""
    nn = max(1, int(n))
    end = datetime.now(_IST).date()
    start = end - timedelta(days=nn - 1)
    return start, end


def _as_date(val: Any) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val)[:10])


def pivot_bucket_counts(raw_rows: list[dict], day_sequence: list[date]) -> list[int]:
    """Map ``(bucket, cnt)`` rows into seven ints aligned with *day_sequence*."""
    day_index = {d: i for i, d in enumerate(day_sequence)}
    counts = [0] * 7
    for r in raw_rows or []:
        b = _as_date(r["bucket"])
        di = day_index.get(b)
        if di is None:
            continue
        counts[di] += int(r["cnt"])
    return counts


def pivot_subdealer_sales_rows(
    raw_rows: list[dict],
    day_sequence: list[date],
) -> list[dict[str, Any]]:
    """
    Pivot ``(dealer_id, dealer_name, bucket, cnt)`` into rows with ``counts`` length 7.
    Drops dealers whose sum over the window is zero.
    """
    day_index = {d: i for i, d in enumerate(day_sequence)}
    by_dealer: dict[int, dict[str, Any]] = {}
    for r in raw_rows or []:
        did = int(r["dealer_id"])
        name = str(r["dealer_name"])
        b = _as_date(r["bucket"])
        cnt = int(r["cnt"])
        if did not in by_dealer:
            by_dealer[did] = {"dealer_id": did, "dealer_name": name, "counts": [0] * 7}
        di = day_index.get(b)
        if di is None:
            continue
        by_dealer[did]["counts"][di] += cnt
    out: list[dict[str, Any]] = []
    for _did, row in sorted(by_dealer.items(), key=lambda kv: str(kv[1]["dealer_name"]).casefold()):
        if sum(row["counts"]) == 0:
            continue
        out.append(row)
    return out


def _challan_bucket_expr(cur) -> str:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'challan_master' AND column_name = 'created_at'
        ) AS has_created_at
        """
    )
    row = cur.fetchone()
    has_created = bool(row["has_created_at"]) if row else False
    return _CHALLAN_BUCKET_WITH_CREATED if has_created else _CHALLAN_BUCKET_DATE_ONLY


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat") and callable(getattr(v, "isoformat")) and not isinstance(v, (str, bytes)):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return v


def count_rto_queued_for_dealer(dealer_id: int) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)::int AS cnt
                FROM rto_queue
                WHERE dealer_id = %s AND status = 'Queued'
                """,
                (int(dealer_id),),
            )
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def _sales_buckets_for_dealer_filter(
    dealer_filter_sql: str,
    params: tuple[Any, ...],
    start_s: str,
    end_s: str,
) -> list[dict]:
    """``dealer_filter_sql`` is a WHERE fragment after ``sm.`` (e.g. ``dealer_id = %s``)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date AS bucket,
                       COUNT(*)::int AS cnt
                FROM sales_master sm
                WHERE {dealer_filter_sql}
                  AND (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date >= %s::date
                  AND (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date <= %s::date
                GROUP BY (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date
                """,
                (*params, start_s, end_s),
            )
            return [dict(r) for r in cur.fetchall() or []]
    finally:
        conn.close()


def counter_sales_buckets(dealer_id: int, start_s: str, end_s: str) -> list[dict]:
    return _sales_buckets_for_dealer_filter("sm.dealer_id = %s", (int(dealer_id),), start_s, end_s)


def subdealer_sales_total_buckets(parent_dealer_id: int, start_s: str, end_s: str) -> list[dict]:
    return _sales_buckets_for_dealer_filter(
        """
        sm.dealer_id IN (SELECT d.dealer_id FROM dealer_ref d WHERE d.parent_id = %s)
        """,
        (int(parent_dealer_id),),
        start_s,
        end_s,
    )


def subdealer_sales_by_child_buckets(parent_dealer_id: int, start_s: str, end_s: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sm.dealer_id,
                       COALESCE(TRIM(dr.dealer_name), '') AS dealer_name,
                       (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date AS bucket,
                       COUNT(*)::int AS cnt
                FROM sales_master sm
                INNER JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                WHERE dr.parent_id = %s
                  AND (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date >= %s::date
                  AND (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date <= %s::date
                GROUP BY sm.dealer_id, COALESCE(TRIM(dr.dealer_name), ''),
                         (sm.billing_date AT TIME ZONE 'Asia/Kolkata')::date
                ORDER BY dealer_name, bucket
                """,
                (int(parent_dealer_id), start_s, end_s),
            )
            return [dict(r) for r in cur.fetchall() or []]
    finally:
        conn.close()


def subdealer_challan_buckets(dealer_from_id: int, start_s: str, end_s: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            bucket_expr = _challan_bucket_expr(cur)
            cur.execute(
                f"""
                SELECT x.bucket::date AS bucket, COUNT(*)::int AS cnt
                FROM (
                    SELECT ({bucket_expr}) AS bucket
                    FROM challan_master cm
                    WHERE cm.dealer_from = %s
                ) x
                WHERE x.bucket IS NOT NULL
                  AND x.bucket >= %s::date
                  AND x.bucket <= %s::date
                GROUP BY x.bucket::date
                """,
                (int(dealer_from_id), start_s, end_s),
            )
            return [dict(r) for r in cur.fetchall() or []]
    finally:
        conn.close()


def list_challan_masters_for_dealer_ist_day(dealer_from_id: int, ist_day: date) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            bucket_expr = _challan_bucket_expr(cur)
            cur.execute(
                f"""
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
                    cm.add_transport_cost,
                    cm.transport_cost_per_vehicle,
                    cm.created_at,
                    COALESCE(TRIM(dr.dealer_name), '') AS to_dealer_name
                FROM challan_master cm
                LEFT JOIN dealer_ref dr ON dr.dealer_id = cm.dealer_to
                WHERE cm.dealer_from = %s
                  AND ({bucket_expr})::date = %s::date
                ORDER BY cm.challan_id DESC
                """,
                (int(dealer_from_id), ist_day.isoformat()),
            )
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for r in rows:
                d = {k: _jsonable(v) for k, v in dict(r).items()}
                out.append(d)
            return out
    finally:
        conn.close()


def list_challan_masters_for_dealer_window(
    dealer_from_id: int,
    ist_start: date,
    ist_end: date,
    dealer_to_id: int | None = None,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Committed challan headers for ``dealer_from`` in ``[ist_start, ist_end]`` (IST bucket date)."""
    lim = max(1, min(int(limit), 1000))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            bucket_expr = _challan_bucket_expr(cur)
            to_clause = ""
            params: list[Any] = [int(dealer_from_id)]
            if dealer_to_id is not None:
                to_clause = " AND cm.dealer_to = %s "
                params.append(int(dealer_to_id))
            params.extend([ist_start.isoformat(), ist_end.isoformat(), lim])
            cur.execute(
                f"""
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
                    cm.add_transport_cost,
                    cm.transport_cost_per_vehicle,
                    cm.created_at,
                    COALESCE(TRIM(dr.dealer_name), '') AS to_dealer_name
                FROM challan_master cm
                LEFT JOIN dealer_ref dr ON dr.dealer_id = cm.dealer_to
                WHERE cm.dealer_from = %s
                {to_clause}
                  AND ({bucket_expr})::date IS NOT NULL
                  AND ({bucket_expr})::date >= %s::date
                  AND ({bucket_expr})::date <= %s::date
                ORDER BY cm.created_at DESC NULLS LAST, cm.challan_id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for r in rows:
                d = {k: _jsonable(v) for k, v in dict(r).items()}
                out.append(d)
            return out
    finally:
        conn.close()


def list_recent_challan_masters_for_dealer(dealer_from_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
    """Latest committed challan headers for ``dealer_from`` (newest first)."""
    lim = max(1, min(int(limit), 50))
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
                    cm.add_transport_cost,
                    cm.transport_cost_per_vehicle,
                    cm.created_at,
                    COALESCE(TRIM(dr.dealer_name), '') AS to_dealer_name
                FROM challan_master cm
                LEFT JOIN dealer_ref dr ON dr.dealer_id = cm.dealer_to
                WHERE cm.dealer_from = %s
                ORDER BY cm.created_at DESC NULLS LAST, cm.challan_id DESC
                LIMIT %s
                """,
                (int(dealer_from_id), lim),
            )
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for r in rows:
                d = {k: _jsonable(v) for k, v in dict(r).items()}
                out.append(d)
            return out
    finally:
        conn.close()
