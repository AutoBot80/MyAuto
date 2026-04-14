"""Typed reference values from ``master_ref`` (insurers, financiers, etc.)."""

from __future__ import annotations

from typing import Any

# Must match ``ref_type`` values used in DDL/seed scripts.
REF_TYPE_INSURER = "INSURER"
REF_TYPE_FINANCER = "FINANCER"


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
