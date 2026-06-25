"""Resolve Hero Connect Siebel portal (HMCL vs ASC) per dealer from ``dealer_ref``."""

from __future__ import annotations

from app.db import get_connection
from app.hero_dms_defaults import hero_dms_urls_for_portal
from app.services.hero_dms_shared_utilities import SiebelDmsUrls


def dms_siebel_portal_for_dealer(dealer_id: int | None) -> str | None:
    """
    Return ``dealer_ref.dms_siebel_portal`` (``ASC`` | ``HMCL`` | NULL).

    Missing dealer row → ``None`` (HMCL default).
    """
    if dealer_id is None:
        return None
    try:
        did = int(dealer_id)
    except (TypeError, ValueError):
        return None
    if did <= 0:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dms_siebel_portal
                FROM dealer_ref
                WHERE dealer_id = %s
                LIMIT 1
                """,
                (did,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw = row.get("dms_siebel_portal")
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("ASC", "HMCL") else None


def hero_dms_base_url_for_dealer(dealer_id: int | None) -> str:
    portal = dms_siebel_portal_for_dealer(dealer_id)
    base, _ = hero_dms_urls_for_portal(portal)
    return base


def hero_dms_siebel_urls_for_dealer(dealer_id: int | None) -> SiebelDmsUrls:
    portal = dms_siebel_portal_for_dealer(dealer_id)
    _, urls = hero_dms_urls_for_portal(portal)
    return urls
