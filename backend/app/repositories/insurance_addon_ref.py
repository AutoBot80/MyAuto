"""MISP add-on preset rows from ``insurance_addon_ref``."""

from __future__ import annotations

from typing import Any

from app.db import get_connection


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if row is None:
        return {}
    cols = (
        "insurance_addon_id",
        "insurer",
        "display_label",
        "nd_cover",
        "rti",
        "rim_safeguard",
        "rsa",
        "sort_order",
        "active_flag",
    )
    return {cols[i]: row[i] for i in range(min(len(cols), len(row)))}


def list_active_by_insurer(conn: Any, insurer: str) -> list[dict[str, Any]]:
    """Active presets for one portal insurer label, ordered for dropdown display."""
    ins = (insurer or "").strip()
    if not ins:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT insurance_addon_id, insurer, display_label,
                   nd_cover, rti, rim_safeguard, rsa, sort_order, active_flag
            FROM insurance_addon_ref
            WHERE insurer = %s AND active_flag = 'Y'
            ORDER BY sort_order, insurance_addon_id
            """,
            (ins,),
        )
        rows = cur.fetchall() or []
    return [_row_to_dict(r) for r in rows]


def list_all_active(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT insurance_addon_id, insurer, display_label,
                   nd_cover, rti, rim_safeguard, rsa, sort_order, active_flag
            FROM insurance_addon_ref
            WHERE active_flag = 'Y'
            ORDER BY insurer, sort_order, insurance_addon_id
            """
        )
        rows = cur.fetchall() or []
    return [_row_to_dict(r) for r in rows]


def get_by_id(conn: Any, insurance_addon_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT insurance_addon_id, insurer, display_label,
                   nd_cover, rti, rim_safeguard, rsa, sort_order, active_flag
            FROM insurance_addon_ref
            WHERE insurance_addon_id = %s
            LIMIT 1
            """,
            (int(insurance_addon_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def first_active_for_insurer(conn: Any, insurer: str) -> dict[str, Any] | None:
    rows = list_active_by_insurer(conn, insurer)
    return rows[0] if rows else None


def fetch_dealer_prefer_insurer_on_cursor(cur, *, dealer_id: int) -> str:
    cur.execute(
        """
        SELECT prefer_insurer
        FROM dealer_ref
        WHERE dealer_id = %s
        LIMIT 1
        """,
        (int(dealer_id),),
    )
    row = cur.fetchone()
    if not row:
        return ""
    raw = row["prefer_insurer"] if isinstance(row, dict) else row[0]
    return str(raw or "").strip()


def fetch_dealer_insurance_addon_on_cursor(cur, *, dealer_id: int) -> int | None:
    cur.execute(
        """
        SELECT insurance_addon
        FROM dealer_ref
        WHERE dealer_id = %s
        LIMIT 1
        """,
        (int(dealer_id),),
    )
    row = cur.fetchone()
    if not row:
        return None
    raw = row["insurance_addon"] if isinstance(row, dict) else row[0]
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def fetch_staging_insurance_addon_on_cursor(
    cur, *, staging_id: str, dealer_id: int
) -> int | None:
    sid = (staging_id or "").strip()
    if not sid:
        return None
    cur.execute(
        """
        SELECT insurance_addon
        FROM add_sales_staging
        WHERE staging_id = %s::uuid AND dealer_id = %s
        LIMIT 1
        """,
        (sid, int(dealer_id)),
    )
    row = cur.fetchone()
    if not row:
        return None
    raw = row["insurance_addon"] if isinstance(row, dict) else row[0]
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_dealer_insurance_addon_for_insert_on_cursor(cur, *, dealer_id: int) -> int | None:
    """Preset id for new staging row: dealer FK when valid for prefer_insurer, else first preset."""
    prefer = fetch_dealer_prefer_insurer_on_cursor(cur, dealer_id=dealer_id)
    if not prefer:
        return None
    dealer_fk = fetch_dealer_insurance_addon_on_cursor(cur, dealer_id=dealer_id)
    if dealer_fk is not None:
        cur.execute(
            """
            SELECT insurance_addon_id
            FROM insurance_addon_ref
            WHERE insurance_addon_id = %s AND insurer = %s AND active_flag = 'Y'
            LIMIT 1
            """,
            (dealer_fk, prefer),
        )
        if cur.fetchone():
            return dealer_fk
    cur.execute(
        """
        SELECT insurance_addon_id
        FROM insurance_addon_ref
        WHERE insurer = %s AND active_flag = 'Y'
        ORDER BY sort_order, insurance_addon_id
        LIMIT 1
        """,
        (prefer,),
    )
    row = cur.fetchone()
    if not row:
        return None
    raw = row["insurance_addon_id"] if isinstance(row, dict) else row[0]
    return int(raw) if raw is not None else None


def build_addon_flags_from_preset(row: dict[str, Any] | None) -> dict[str, bool]:
    if not row:
        return {"nd_cover": True, "rti": False, "rim_safeguard": False, "rsa": False}

    def _yn(key: str, default: bool = False) -> bool:
        return str(row.get(key) or "").strip().upper() == "Y" if row.get(key) is not None else default

    return {
        "nd_cover": _yn("nd_cover", default=True),
        "rti": _yn("rti"),
        "rim_safeguard": _yn("rim_safeguard"),
        "rsa": _yn("rsa"),
    }


def resolve_effective_insurance_addon_row(
    *,
    staging_id: str | None,
    dealer_id: int,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    """
    Resolve preset using ``dealer_ref.prefer_insurer`` as insurer key:
    staging FK → dealer FK → first active preset for prefer_insurer.
    """
    did = int(dealer_id)
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            prefer = fetch_dealer_prefer_insurer_on_cursor(cur, dealer_id=did)
            if not prefer:
                return None
            sid = (staging_id or "").strip()
            candidates: list[int] = []
            if sid:
                staging_fk = fetch_staging_insurance_addon_on_cursor(
                    cur, staging_id=sid, dealer_id=did
                )
                if staging_fk is not None:
                    candidates.append(staging_fk)
            dealer_fk = fetch_dealer_insurance_addon_on_cursor(cur, dealer_id=did)
            if dealer_fk is not None and dealer_fk not in candidates:
                candidates.append(dealer_fk)
            for fk in candidates:
                cur.execute(
                    """
                    SELECT insurance_addon_id, insurer, display_label,
                           nd_cover, rti, rim_safeguard, rsa, sort_order, active_flag
                    FROM insurance_addon_ref
                    WHERE insurance_addon_id = %s AND insurer = %s AND active_flag = 'Y'
                    LIMIT 1
                    """,
                    (fk, prefer),
                )
                row = cur.fetchone()
                if row:
                    return _row_to_dict(row)
            cur.execute(
                """
                SELECT insurance_addon_id, insurer, display_label,
                       nd_cover, rti, rim_safeguard, rsa, sort_order, active_flag
                FROM insurance_addon_ref
                WHERE insurer = %s AND active_flag = 'Y'
                ORDER BY sort_order, insurance_addon_id
                LIMIT 1
                """,
                (prefer,),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None
    finally:
        if own_conn and conn is not None:
            conn.close()
