"""Load Add Sales staging rows for Create Invoice (DMS) without reading customer/vehicle/sales_master."""

import json
import re
import uuid
from typing import Any

from app.db import get_connection

_NESTED_STAGING_PAYLOAD_KEYS = frozenset({"customer", "vehicle", "insurance"})


def non_empty_staging_customer_vehicle_patch(
    customer: dict[str, Any] | None,
    vehicle: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a staging ``payload_json`` patch from optional request customer/vehicle dicts."""
    patch: dict[str, Any] = {}
    if customer:
        c = {
            k: v
            for k, v in customer.items()
            if v is not None and str(v).strip() != ""
        }
        if c:
            if "pin_code" in c and "pin" not in c:
                c["pin"] = c["pin_code"]
            elif "pin" in c and "pin_code" not in c:
                c["pin_code"] = c["pin"]
            patch["customer"] = c
    if vehicle:
        v = {
            k: v2
            for k, v2 in vehicle.items()
            if v2 is not None and str(v2).strip() != ""
        }
        if v:
            patch["vehicle"] = v
    return patch


def deep_merge_staging_payload(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """
    Merge ``patch`` into ``base`` for staging JSON.

    Top-level scalars: patch wins. Nested ``customer`` / ``vehicle`` / ``insurance``: field-level
    overlay (PostgreSQL ``||`` on jsonb replaces whole nested objects — do not use that here).
    """
    result = dict(base)
    for key, val in patch.items():
        if key in _NESTED_STAGING_PAYLOAD_KEYS and isinstance(val, dict):
            existing = result.get(key)
            if isinstance(existing, dict):
                merged_nested = dict(existing)
                merged_nested.update(val)
                result[key] = merged_nested
            else:
                result[key] = dict(val)
        else:
            result[key] = val
    return result


def _load_payload_json_for_update_on_cursor(
    cur, *, staging_id: str, dealer_id: int
) -> dict[str, Any] | None:
    """Lock row and return ``payload_json`` dict, or None if missing."""
    cur.execute(
        """
        SELECT payload_json
        FROM add_sales_staging
        WHERE staging_id = %s::uuid AND dealer_id = %s
        FOR UPDATE
        """,
        (staging_id, int(dealer_id)),
    )
    row = cur.fetchone()
    if not row:
        return None
    raw = row["payload_json"] if isinstance(row, dict) else row[0]
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return json.loads(raw)


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


def sales_id_from_staging_payload(p: dict[str, Any]) -> int | None:
    """
    Read ``sales_id`` from a staging ``payload_json`` dict.

    Create Invoice may persist ``sales_id`` as a JSON number or string.
    """
    raw = p.get("sales_id")
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float) and raw.is_integer():
        n = int(raw)
        return n if n > 0 else None
    s = str(raw).strip()
    if s.isdigit():
        return int(s)
    return None


def resolve_sales_id_from_staging(staging_id: str, dealer_id: int) -> int | None:
    """
    Resolve ``sales_master.sales_id`` for a committed/draft staging row.

    Uses ``payload_json.sales_id`` when set (post Create Invoice), else
    ``customer_id`` + ``vehicle_id`` on the payload.
    """
    p = fetch_staging_payload(staging_id, dealer_id)
    if not p:
        return None
    sid = sales_id_from_staging_payload(p)
    if sid is not None:
        return sid
    try:
        cid = int(p.get("customer_id") or 0)
        vid = int(p.get("vehicle_id") or 0)
    except (TypeError, ValueError):
        return None
    if cid < 1 or vid < 1:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sales_id FROM sales_master WHERE customer_id = %s AND vehicle_id = %s",
                (cid, vid),
            )
            row = cur.fetchone()
            return int(row["sales_id"]) if row else None
    finally:
        conn.close()


def staging_customer_mobile_from_payload(payload: dict[str, Any] | None) -> str | None:
    """Best-effort mobile for RTO queue from staging customer block."""
    if not payload:
        return None
    cust = payload.get("customer")
    if not isinstance(cust, dict):
        return None
    for key in ("mobile_number", "mobile", "phone"):
        v = str(cust.get(key) or "").strip()
        if v:
            return v
    return None


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
) -> int:
    """
    Merge ``patch`` into ``payload_json`` without changing ``status`` (e.g. ``insurance_id`` after GI).
    Only updates rows where ``staging_id`` / ``dealer_id`` match and ``status`` is draft or committed.
    Returns ``cur.rowcount`` from the UPDATE (0 if row missing or not draft/committed).
    """
    sid = (staging_id or "").strip()
    if not sid or not patch:
        return 0
    did = int(dealer_id)
    existing = _load_payload_json_for_update_on_cursor(cur, staging_id=sid, dealer_id=did)
    if existing is None:
        return 0
    merged = deep_merge_staging_payload(existing, patch)
    frag = json.dumps(merged, default=str)
    cur.execute(
        """
        UPDATE add_sales_staging
        SET updated_at = now(),
            payload_json = %s::jsonb
        WHERE staging_id = %s::uuid AND dealer_id = %s
          AND status IN ('draft', 'committed')
        """,
        (frag, sid, did),
    )
    return int(cur.rowcount or 0)


def mark_staging_committed_on_cursor(
    cur, staging_id: str, dealer_id: int, *, patch: dict[str, Any]
) -> None:
    """Set status to committed and deep-merge ``patch`` into ``payload_json`` (ids, financier, DMS numbers)."""
    sid = (staging_id or "").strip()
    did = int(dealer_id)
    existing = _load_payload_json_for_update_on_cursor(cur, staging_id=sid, dealer_id=did)
    if existing is None:
        raise ValueError(f"add_sales_staging row not found for staging_id={sid!r} dealer_id={did}")
    merged = deep_merge_staging_payload(existing, patch)
    frag = json.dumps(merged, default=str)
    cur.execute(
        """
        UPDATE add_sales_staging
        SET status = 'committed',
            updated_at = now(),
            payload_json = %s::jsonb
        WHERE staging_id = %s::uuid AND dealer_id = %s
        """,
        (frag, sid, did),
    )
    if cur.rowcount != 1:
        raise ValueError(f"add_sales_staging commit updated {cur.rowcount} rows (expected 1)")


def update_staging_processing_state(
    staging_id: str,
    dealer_id: int,
    *,
    dms_state: int | None = None,
    insurance_state: int | None = None,
) -> bool:
    """
    Set ``dms_state`` and/or ``insurance_state`` on a draft/committed staging row.
    Returns True when exactly one row was updated.
    """
    sid = (staging_id or "").strip()
    if not sid:
        return False
    if dms_state is None and insurance_state is None:
        return False
    try:
        uuid.UUID(sid)
    except ValueError:
        return False

    sets: list[str] = ["updated_at = now()"]
    params: list[Any] = []
    if dms_state is not None:
        sets.append("dms_state = %s")
        params.append(int(dms_state))
    if insurance_state is not None:
        sets.append("insurance_state = %s")
        params.append(int(insurance_state))

    did = int(dealer_id)
    params.extend([sid, did])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE add_sales_staging
                SET {", ".join(sets)}
                WHERE staging_id = %s::uuid AND dealer_id = %s
                  AND status IN ('draft', 'committed')
                """,
                tuple(params),
            )
            updated = int(cur.rowcount or 0) == 1
        conn.commit()
        return updated
    finally:
        conn.close()


def fetch_staging_insurance_state(staging_id: str, dealer_id: int) -> int | None:
    """
    Return ``insurance_state`` for a draft/committed staging row, or None if missing.
    """
    sid = (staging_id or "").strip()
    if not sid:
        return None
    try:
        uuid.UUID(sid)
    except ValueError:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT insurance_state
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
            raw = row["insurance_state"] if isinstance(row, dict) else row[0]
            if raw is None:
                return 0
            return int(raw)
    finally:
        conn.close()
