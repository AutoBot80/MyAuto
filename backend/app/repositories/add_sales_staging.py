"""Load Add Sales staging rows for Create Invoice (DMS) without reading customer/vehicle/sales_master."""

import json
import re
import uuid
from typing import Any

from app.db import get_connection


def normalize_staging_natural_key_mobile(mobile: Any) -> str | None:
    """Last up to 10 digits as string for dedupe; None if no digits."""
    if mobile is None:
        return None
    digits = "".join(c for c in str(mobile) if c.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    if len(digits) > 0:
        return digits
    return None


def normalize_staging_natural_key_text(val: Any) -> str | None:
    """Trim, collapse whitespace, lower — None if empty after normalize."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def _sql_no_rto_correspondence(alias: str = "s") -> str:
    return f"""
    NOT EXISTS (
        SELECT 1 FROM rto_queue rq
        WHERE (
            rq.staging_id IS NOT NULL AND rq.staging_id::text = {alias}.staging_id::text
        ) OR (
            NULLIF(trim({alias}.payload_json->>'sales_id'), '') IS NOT NULL
            AND (NULLIF(trim({alias}.payload_json->>'sales_id'), '') ~ '^[0-9]+$')
            AND rq.sales_id = (NULLIF(trim({alias}.payload_json->>'sales_id'), ''))::bigint
        )
    )
    """


def ist_window_start_timestamptz(days: int) -> str:
    """SQL fragment: start of IST calendar day ``days`` days before current IST date (inclusive window)."""
    # (ist_today - days) at 00:00 IST as timestamptz
    return f"((timezone('Asia/Kolkata', now()))::date - {int(days)})::timestamp AT TIME ZONE 'Asia/Kolkata'"


def list_in_process_staging_rows(*, dealer_id: int, days: int = 7) -> list[dict[str, Any]]:
    """
    Staging rows for In-process list: draft/committed, updated in last ``days`` IST calendar days,
    no corresponding rto_queue row.
    """
    did = int(dealer_id)
    d = max(1, min(int(days), 365))
    ist_start = ist_window_start_timestamptz(d)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    s.staging_id::text AS staging_id,
                    s.updated_at,
                    s.status,
                    NULLIF(trim(s.payload_json->'customer'->>'name'), '') AS customer_name,
                    NULLIF(trim(s.payload_json->'customer'->>'mobile_number'), '') AS mobile,
                    NULLIF(trim(s.payload_json->'vehicle'->>'frame_no'), '') AS chassis,
                    NULLIF(trim(s.payload_json->'vehicle'->>'engine_no'), '') AS engine,
                    NULLIF(trim(s.payload_json->'vehicle'->>'order_number'), '') AS order_number,
                    NULLIF(trim(s.payload_json->>'sales_id'), '') AS sales_id_text,
                    NULLIF(trim(s.payload_json->>'customer_id'), '') AS customer_id_text,
                    NULLIF(trim(s.payload_json->>'vehicle_id'), '') AS vehicle_id_text,
                    NULLIF(trim(s.payload_json->>'file_location'), '') AS file_location,
                    NULLIF(trim(s.subfolder), '') AS subfolder
                FROM add_sales_staging s
                WHERE s.dealer_id = %s
                  AND s.status IN ('draft', 'committed')
                  AND s.updated_at >= {ist_start}
                  AND {_sql_no_rto_correspondence('s')}
                ORDER BY s.updated_at DESC
                """,
                (did,),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []
    finally:
        conn.close()


def find_open_staging_by_natural_keys_on_cursor(
    cur,
    *,
    dealer_id: int,
    mobile_key: str,
    chassis_key: str,
    engine_key: str,
    prefer_staging_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Lock first matching open staging row (no rto) for dealer + normalized natural keys.
    Returns ``staging_id`` (str), ``status``, ``payload_json`` dict or None.

    When ``prefer_staging_id`` is set, prefer that row if it matches the same keys (stable client handle).
    """
    did = int(dealer_id)
    boost = (prefer_staging_id or "").strip() or ""
    cur.execute(
        f"""
        SELECT s.staging_id::text AS staging_id, s.status, s.payload_json
        FROM add_sales_staging s
        WHERE s.dealer_id = %s
          AND s.status IN ('draft', 'committed')
          AND {_sql_no_rto_correspondence('s')}
          AND lower(regexp_replace(trim(COALESCE(s.payload_json->'vehicle'->>'frame_no', '')), '\\s+', ' ', 'g'))
              = %s
          AND lower(regexp_replace(trim(COALESCE(s.payload_json->'vehicle'->>'engine_no', '')), '\\s+', ' ', 'g'))
              = %s
          AND right(
                regexp_replace(trim(COALESCE(s.payload_json->'customer'->>'mobile_number', '')), '[^0-9]', '', 'g'),
                10
              ) = %s
        ORDER BY CASE WHEN %s <> '' AND s.staging_id::text = %s THEN 0 ELSE 1 END, s.updated_at DESC
        LIMIT 1
        FOR UPDATE OF s
        """,
        (did, chassis_key, engine_key, mobile_key, boost, boost),
    )
    row = cur.fetchone()
    if not row:
        return None
    raw = row["payload_json"] if isinstance(row, dict) else row[2]
    if raw is None:
        pl: dict[str, Any] = {}
    elif isinstance(raw, dict):
        pl = dict(raw)
    else:
        pl = json.loads(raw)
    sid = row["staging_id"] if isinstance(row, dict) else row[0]
    st = row["status"] if isinstance(row, dict) else row[1]
    return {"staging_id": str(sid), "status": str(st), "payload_json": pl}


def fetch_staging_id_status_on_cursor(cur, *, staging_id: str, dealer_id: int) -> str | None:
    """Return ``draft`` / ``committed`` / ``abandoned`` or None if missing."""
    sid = (staging_id or "").strip()
    if not sid:
        return None
    try:
        uuid.UUID(sid)
    except ValueError:
        return None
    cur.execute(
        """
        SELECT status::text
        FROM add_sales_staging
        WHERE staging_id::text = %s AND dealer_id = %s
        FOR UPDATE
        """,
        (sid, int(dealer_id)),
    )
    row = cur.fetchone()
    if not row:
        return None
    v = row["status"] if isinstance(row, dict) else row[0]
    return str(v).strip() if v is not None else None


def fetch_staging_payload(staging_id: str, dealer_id: int) -> dict[str, Any] | None:
    """
    Return ``payload_json`` when ``staging_id`` and ``dealer_id`` match and ``status`` is **draft** or **committed**.
    Used by Generate Insurance to merge OCR/Submit snapshot fields (e.g. nominee, insurer) not yet on ``insurance_master``.
    """
    sid = (staging_id or "").strip()
    if not sid:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload_json
                FROM add_sales_staging
                WHERE staging_id::text = %s
                  AND dealer_id = %s
                  AND status IN ('draft', 'committed')
                """,
                (sid, int(dealer_id)),
            )
            row = cur.fetchone()
            if not row:
                return None
            raw = row["payload_json"] if isinstance(row, dict) else row[0]
            if raw is None:
                return None
            if isinstance(raw, dict):
                return dict(raw)
            return json.loads(raw)
    finally:
        conn.close()


def fetch_staging_subfolder(staging_id: str, dealer_id: int) -> str | None:
    """Return upload subfolder from staging (``payload_json.file_location``; ``subfolder`` column synced on persist)."""
    p = fetch_staging_payload(staging_id, dealer_id)
    if not p:
        return None
    s = (p.get("file_location") or "").strip()
    return s or None


def fetch_draft_payload(staging_id: str, dealer_id: int) -> dict[str, Any] | None:
    """
    Return ``payload_json`` for a **draft** staging row when ``staging_id`` and ``dealer_id`` match.
    Used by Fill DMS so automation reads OCR merge only from staging + Siebel scrape.
    """
    sid = (staging_id or "").strip()
    if not sid:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload_json
                FROM add_sales_staging
                WHERE staging_id::text = %s
                  AND dealer_id = %s
                  AND status = 'draft'
                """,
                (sid, int(dealer_id)),
            )
            row = cur.fetchone()
            if not row:
                return None
            raw = row["payload_json"] if isinstance(row, dict) else row[0]
            if raw is None:
                return None
            if isinstance(raw, dict):
                return dict(raw)
            return json.loads(raw)
    finally:
        conn.close()


def persist_staging_for_submit(
    cur,
    *,
    dealer_id: int,
    payload: dict[str, Any],
    staging_id_existing: str | None,
    login_id: str | None = None,
) -> str:
    """
    Upsert **draft** ``add_sales_staging`` for Submit Info: update by client ``staging_id`` when draft;
    else match open row by natural keys (mobile + chassis + engine); **never** merge into **committed**
    (raises ``ValueError`` with ``SUBMIT_INFO_COMMITTED_SALE_MSG``). Otherwise INSERT new UUID.
    """
    from app.constants.add_sales_submit import SUBMIT_INFO_COMMITTED_SALE_MSG

    payload_str = json.dumps(payload, default=str)
    lid = (login_id or "").strip() or None
    sf = (payload.get("file_location") or "").strip() or None

    sid_in = (staging_id_existing or "").strip()
    if sid_in:
        try:
            uuid.UUID(sid_in)
        except ValueError:
            sid_in = ""

    if sid_in:
        st = fetch_staging_id_status_on_cursor(cur, staging_id=sid_in, dealer_id=dealer_id)
        if st == "committed":
            raise ValueError(SUBMIT_INFO_COMMITTED_SALE_MSG)
        if st == "draft":
            cur.execute(
                """
                UPDATE add_sales_staging
                SET payload_json = %s::jsonb,
                    updated_at = now(),
                    login_id = COALESCE(%s, login_id),
                    subfolder = COALESCE(%s, subfolder)
                WHERE staging_id = %s::uuid AND dealer_id = %s AND status = 'draft'
                """,
                (payload_str, lid, sf, sid_in, dealer_id),
            )
            if cur.rowcount:
                return sid_in

    cust = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    veh = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
    mob_key = normalize_staging_natural_key_mobile(cust.get("mobile_number"))
    ch_key = normalize_staging_natural_key_text(veh.get("frame_no"))
    eng_key = normalize_staging_natural_key_text(veh.get("engine_no"))
    if mob_key and ch_key and eng_key:
        hit = find_open_staging_by_natural_keys_on_cursor(
            cur,
            dealer_id=dealer_id,
            mobile_key=mob_key,
            chassis_key=ch_key,
            engine_key=eng_key,
            prefer_staging_id=sid_in or None,
        )
        if hit:
            if str(hit.get("status") or "").strip() == "committed":
                raise ValueError(SUBMIT_INFO_COMMITTED_SALE_MSG)
            upd_id = str(hit["staging_id"]).strip()
            cur.execute(
                """
                UPDATE add_sales_staging
                SET payload_json = %s::jsonb,
                    updated_at = now(),
                    login_id = COALESCE(%s, login_id),
                    subfolder = COALESCE(%s, subfolder)
                WHERE staging_id = %s::uuid AND dealer_id = %s AND status = 'draft'
                """,
                (payload_str, lid, sf, upd_id, dealer_id),
            )
            if cur.rowcount:
                return upd_id

    new_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO add_sales_staging (staging_id, dealer_id, payload_json, status, login_id, subfolder)
        VALUES (%s::uuid, %s, %s::jsonb, 'draft', %s, %s)
        """,
        (new_id, dealer_id, payload_str, lid, sf),
    )
    return new_id


def merge_staging_payload_on_cursor(
    cur,
    staging_id: str,
    dealer_id: int,
    patch: dict[str, Any],
) -> None:
    """
    Merge ``patch`` into ``payload_json`` without changing ``status`` (e.g. ``insurance_id`` after GI).
    Only updates rows where ``staging_id`` / ``dealer_id`` match and ``status`` is draft or committed.
    """
    sid = (staging_id or "").strip()
    if not sid or not patch:
        return
    frag = json.dumps(patch, default=str)
    cur.execute(
        """
        UPDATE add_sales_staging
        SET updated_at = now(),
            payload_json = payload_json || %s::jsonb
        WHERE staging_id = %s::uuid AND dealer_id = %s
          AND status IN ('draft', 'committed')
        """,
        (frag, sid, int(dealer_id)),
    )


def mark_staging_committed_on_cursor(cur, staging_id: str, dealer_id: int, *, patch_json_fragment: str) -> None:
    """Set status to committed and merge ``patch_json_fragment`` into ``payload_json`` (ids, optional ``customer.financier``, ``vehicle`` DMS numbers)."""
    sid = (staging_id or "").strip()
    cur.execute(
        """
        UPDATE add_sales_staging
        SET status = 'committed',
            updated_at = now(),
            payload_json = payload_json || %s::jsonb
        WHERE staging_id = %s::uuid AND dealer_id = %s
        """,
        (patch_json_fragment, sid, int(dealer_id)),
    )
