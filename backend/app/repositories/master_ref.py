"""Typed reference values from ``master_ref`` (insurers, financiers, etc.)."""

from __future__ import annotations

from typing import Any

# Must match ``ref_type`` values used in DDL/seed scripts.
REF_TYPE_INSURER = "INSURER"
REF_TYPE_FINANCER = "FINANCER"
REF_TYPE_CPA = "CPA"


def list_ref_values(conn: Any, ref_type: str) -> list[str]:
    """
    Return all ``ref_value`` rows for ``ref_type``, sorted for stable fuzzy matching.
    """
    rt = (ref_type or "").strip()
    if not rt:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ref_value
            FROM master_ref
            WHERE ref_type = %s
            ORDER BY ref_value
            """,
            (rt,),
        )
        rows = cur.fetchall()
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            v = row.get("ref_value")
        else:
            v = row[0] if row else None
        if v and str(v).strip():
            out.append(str(v).strip())
    return out


def list_portal_insurers(conn: Any) -> list[str]:
    """
    Insurer labels allowed on the Add Sales portal dropdown: ``ref_type = INSURER`` and
    ``comments`` marks eligibility (``Y``). CPA rows use ``ref_type = CPA`` and ``comments`` as URL.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ref_value
            FROM master_ref
            WHERE ref_type = %s
              AND UPPER(TRIM(COALESCE(comments, ''))) = 'Y'
            ORDER BY ref_value
            """,
            (REF_TYPE_INSURER,),
        )
        rows = cur.fetchall()
    out: list[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            v = row.get("ref_value")
        else:
            v = row[0] if row else None
        if v and str(v).strip():
            out.append(str(v).strip())
    return out


def list_cpa_portals(conn: Any) -> list[dict[str, str]]:
    """
    CPA third-party portal rows: ``ref_value`` (display name) and ``comments`` (login URL).

    Only rows with non-empty ``comments`` are returned (URL required for automation).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ref_value, TRIM(COALESCE(comments, '')) AS login_url
            FROM master_ref
            WHERE ref_type = %s
              AND TRIM(COALESCE(comments, '')) <> ''
              AND TRIM(COALESCE(comments, '')) LIKE 'http%%'
            ORDER BY ref_value
            """,
            (REF_TYPE_CPA,),
        )
        rows = cur.fetchall()
    out: list[dict[str, str]] = []
    for row in rows or []:
        if isinstance(row, dict):
            rv = row.get("ref_value")
            lu = row.get("login_url")
        else:
            rv = row[0] if row else None
            lu = row[1] if row and len(row) > 1 else None
        rvs = str(rv or "").strip()
        lus = str(lu or "").strip()
        if rvs and lus:
            out.append({"ref_value": rvs, "login_url": lus})
    return out
