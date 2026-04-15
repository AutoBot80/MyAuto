"""Load Add Sales staging rows for Create Invoice (DMS) without reading customer/vehicle/sales_master."""

import json
import uuid
from typing import Any

from app.db import get_connection


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
    INSERT a new draft row or UPDATE ``payload_json`` when ``staging_id_existing`` matches a draft row
    for the same ``dealer_id``. Sets ``login_id`` when the column exists and a value is provided.
    Returns the staging UUID string (existing or new).
    """
    payload_str = json.dumps(payload, default=str)
    sid = (staging_id_existing or "").strip()
    lid = (login_id or "").strip() or None
    sf = (payload.get("file_location") or "").strip() or None
    if sid:
        cur.execute(
            """
            UPDATE add_sales_staging
            SET payload_json = %s::jsonb,
                updated_at = now(),
                login_id = COALESCE(%s, login_id),
                subfolder = COALESCE(%s, subfolder)
            WHERE staging_id = %s::uuid AND dealer_id = %s AND status = 'draft'
            """,
            (payload_str, lid, sf, sid, dealer_id),
        )
        if cur.rowcount:
            return sid
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
    """Set status to committed and merge ``patch_json_fragment`` into ``payload_json`` (ids, optional ``customer.financier``)."""
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
