"""Admin Saathi dealer scope via ``admin_dealer_access_ref``."""

from __future__ import annotations

from fastapi import HTTPException

from app.db import get_connection


def list_dealer_ids_for_admin_login(login_id: str) -> list[int]:
    """Distinct dealer_ids mapped to this login for Admin Saathi visibility."""
    lid = (login_id or "").strip()
    if not lid:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ada.dealer_id
                FROM admin_dealer_access_ref ada
                INNER JOIN dealer_ref dr ON dr.dealer_id = ada.dealer_id
                WHERE ada.login_id = %s
                ORDER BY ada.dealer_id
                """,
                (lid,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [int(r["dealer_id"]) for r in rows if r.get("dealer_id") is not None]


def list_dealers_for_admin_login(login_id: str) -> list[dict]:
    """``dealer_id``, ``dealer_name`` for scoped dealers (Admin dropdowns)."""
    lid = (login_id or "").strip()
    if not lid:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dr.dealer_id, dr.dealer_name
                FROM admin_dealer_access_ref ada
                INNER JOIN dealer_ref dr ON dr.dealer_id = ada.dealer_id
                WHERE ada.login_id = %s
                ORDER BY dr.dealer_name ASC
                """,
                (lid,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def dealer_names_for_ids(dealer_ids: list[int]) -> dict[int, str]:
    if not dealer_ids:
        return {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dealer_id, dealer_name
                FROM dealer_ref
                WHERE dealer_id = ANY(%s)
                """,
                (dealer_ids,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return {int(r["dealer_id"]): str(r["dealer_name"]) for r in rows}


def parent_dealer_ids_in_scope(dealer_ids: list[int]) -> list[int]:
    """Scoped dealers that are parent rows (``dealer_ref.parent_id`` IS NULL) — for Challans usage matrix."""
    if not dealer_ids:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dealer_id
                FROM dealer_ref
                WHERE dealer_id = ANY(%s)
                  AND parent_id IS NULL
                ORDER BY dealer_id
                """,
                (dealer_ids,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [int(r["dealer_id"]) for r in rows if r.get("dealer_id") is not None]


def assert_dealer_in_admin_scope(login_id: str, dealer_id: int) -> None:
    allowed = list_dealer_ids_for_admin_login(login_id)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="No dealers assigned for Admin Saathi access. Ask an administrator to add admin_dealer_access_ref rows.",
        )
    did = int(dealer_id)
    if did not in allowed:
        raise HTTPException(status_code=403, detail="Access denied for this dealer in Admin Saathi.")


def resolve_admin_scoped_dealer_id(login_id: str, session_dealer_id: int, dealer_id: int | None) -> int:
    """Pick dealer for admin folder APIs: query param, else session if scoped, else first mapped."""
    allowed = list_dealer_ids_for_admin_login(login_id)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="No dealers assigned for Admin Saathi access. Ask an administrator to add admin_dealer_access_ref rows.",
        )
    if dealer_id is not None:
        did = int(dealer_id)
        if did not in allowed:
            raise HTTPException(status_code=403, detail="Access denied for this dealer in Admin Saathi.")
        return did
    sid = int(session_dealer_id)
    if sid in allowed:
        return sid
    return allowed[0]
