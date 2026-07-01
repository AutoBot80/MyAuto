"""MISP add-on preset rows from ``insurance_addon_ref``."""

from __future__ import annotations

from typing import Any

from app.db import get_connection


def _list_active_by_insurer_exact(conn: Any, insurer: str) -> list[dict[str, Any]]:
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


def resolve_addon_catalog_insurer_key(conn: Any, insurer: str) -> str:
    """
    Map ``dealer_ref.prefer_insurer`` (or sale insurer) to the ``insurance_addon_ref.insurer``
    label — exact match first, then fuzzy match to portal / preset insurer names.
    """
    ins = (insurer or "").strip()
    if not ins:
        return ""
    if _list_active_by_insurer_exact(conn, ins):
        return ins
    from app.repositories.master_ref import list_portal_insurers
    from app.services.utility_functions import fuzzy_best_master_ref_value

    portal = list_portal_insurers(conn)
    canon = fuzzy_best_master_ref_value(ins, portal, min_score=0.5)
    if canon and _list_active_by_insurer_exact(conn, canon):
        return str(canon).strip()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT insurer
            FROM insurance_addon_ref
            WHERE active_flag = 'Y'
            ORDER BY insurer
            """
        )
        preset_insurers = [
            str(r["insurer"] if isinstance(r, dict) else r[0]).strip()
            for r in (cur.fetchall() or [])
            if r
        ]
    canon2 = fuzzy_best_master_ref_value(ins, preset_insurers, min_score=0.5)
    if canon2 and _list_active_by_insurer_exact(conn, canon2):
        return str(canon2).strip()
    return ins


def list_active_by_insurer(conn: Any, insurer: str) -> list[dict[str, Any]]:
    """Active presets for one portal insurer label, ordered for dropdown display."""
    key = resolve_addon_catalog_insurer_key(conn, insurer)
    if not key:
        return []
    return _list_active_by_insurer_exact(conn, key)


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
    prefer_raw = fetch_dealer_prefer_insurer_on_cursor(cur, dealer_id=dealer_id)
    if not prefer_raw:
        return None
    conn = getattr(cur, "connection", None)
    prefer = (
        resolve_addon_catalog_insurer_key(conn, prefer_raw)
        if conn is not None
        else prefer_raw
    )
    dealer_fk = fetch_dealer_insurance_addon_on_cursor(cur, dealer_id=dealer_id)
    if dealer_fk is not None and conn is not None:
        row = get_by_id(conn, int(dealer_fk))
        if row and str(row.get("active_flag") or "").strip().upper() == "Y":
            row_ins = str(row.get("insurer") or "").strip()
            if row_ins == prefer or resolve_addon_catalog_insurer_key(conn, row_ins) == prefer:
                return int(dealer_fk)
    elif dealer_fk is not None:
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


def validate_dealer_insurance_addon_config(
    conn: Any,
    dealer_id: int,
    *,
    staging_id: str | None = None,
) -> list[str]:
    """
    Human-readable config issues for dealer/staging add-on FKs and ``insurance_addon_ref`` catalog.
    Empty list means no blocking issues detected for ``prefer_insurer``.
    """
    issues: list[str] = []
    did = int(dealer_id)
    sid = (staging_id or "").strip() or None
    with conn.cursor() as cur:
        prefer = fetch_dealer_prefer_insurer_on_cursor(cur, dealer_id=did)
        if not prefer:
            return issues
        presets = list_active_by_insurer(conn, prefer)
        if not presets:
            issues.append(
                f"no active insurance add-on presets for prefer_insurer {prefer!r} "
                "(apply DDL/seed_insurance_addon_ref.sql)"
            )
        dealer_fk = fetch_dealer_insurance_addon_on_cursor(cur, dealer_id=did)
        if dealer_fk is not None:
            row = get_by_id(conn, int(dealer_fk))
            if not row or str(row.get("active_flag") or "").strip().upper() != "Y":
                issues.append(f"dealer_ref.insurance_addon={dealer_fk} not found or inactive")
            else:
                row_ins = str(row.get("insurer") or "").strip()
                canon_prefer = resolve_addon_catalog_insurer_key(conn, prefer)
                canon_row = resolve_addon_catalog_insurer_key(conn, row_ins)
                if canon_row != canon_prefer and row_ins != prefer:
                    issues.append(
                        f"dealer_ref.insurance_addon={dealer_fk} insurer mismatch "
                        f"(preset {row_ins!r} vs prefer_insurer {prefer!r})"
                    )
        if sid:
            staging_fk = fetch_staging_insurance_addon_on_cursor(
                cur, staging_id=sid, dealer_id=did
            )
            if staging_fk is not None:
                row = get_by_id(conn, int(staging_fk))
                if not row or str(row.get("active_flag") or "").strip().upper() != "Y":
                    issues.append(f"staging insurance_addon={staging_fk} not found or inactive")
                elif str(row.get("insurer") or "").strip() != prefer:
                    issues.append(
                        f"staging insurance_addon={staging_fk} insurer mismatch "
                        f"for prefer_insurer {prefer!r}"
                    )
        if presets and resolve_effective_insurance_addon_row(
            staging_id=sid, dealer_id=did, conn=conn
        ) is None:
            issues.append(f"could not resolve effective add-on preset for dealer {did}")
    return issues


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
            prefer_raw = fetch_dealer_prefer_insurer_on_cursor(cur, dealer_id=did)
            if not prefer_raw:
                return None
            prefer = resolve_addon_catalog_insurer_key(conn, prefer_raw)
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
                row = get_by_id(conn, int(fk))
                if row and str(row.get("active_flag") or "").strip().upper() == "Y":
                    row_insurer = str(row.get("insurer") or "").strip()
                    if row_insurer == prefer or row_insurer == prefer_raw:
                        return row
                    canon = resolve_addon_catalog_insurer_key(conn, row_insurer)
                    if canon == prefer:
                        return row
            first_rows = _list_active_by_insurer_exact(conn, prefer)
            return first_rows[0] if first_rows else None
    finally:
        if own_conn and conn is not None:
            conn.close()
