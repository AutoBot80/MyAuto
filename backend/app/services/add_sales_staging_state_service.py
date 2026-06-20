"""Update ``add_sales_staging`` processing-state columns from automation."""

from __future__ import annotations

import logging

from app.db import get_connection
from app.repositories.add_sales_staging import (
    merge_staging_payload_on_cursor,
    update_staging_processing_state,
)

logger = logging.getLogger(__name__)


def persist_staging_insurance_main_fields(
    staging_id: str,
    dealer_id: int,
    *,
    policy_num: str | None = None,
    policy_from: str | None = None,
    policy_to: str | None = None,
    premium: object | None = None,
    idv: object | None = None,
) -> bool:
    """
    Merge non-empty Main insurance fields into ``payload_json.insurance`` (pre-INSERT / re-run cache).
    """
    from app.services.add_sales_commit_service import _build_staging_insurance_patch_main

    sid = (staging_id or "").strip()
    if not sid:
        return False
    patch = _build_staging_insurance_patch_main(
        policy_num=policy_num,
        policy_from=policy_from,
        policy_to=policy_to,
        premium=premium,
        idv=idv,
    )
    if not patch:
        return False
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                n = merge_staging_payload_on_cursor(cur, sid, int(dealer_id), patch)
            conn.commit()
        return n > 0
    except Exception as exc:
        logger.warning(
            "persist_staging_insurance_main_fields failed staging_id=%s dealer_id=%s: %s",
            sid,
            dealer_id,
            exc,
        )
        return False


def persist_staging_issued_policy_num(
    staging_id: str,
    dealer_id: int,
    policy_num: str,
) -> bool:
    """
    Merge issued **Main** policy number into ``payload_json.insurance`` (pre-``insurance_master`` INSERT).
    Used after post-Submit cert scrape and admin portal-only manual issue.
    """
    return persist_staging_insurance_main_fields(
        staging_id,
        dealer_id,
        policy_num=policy_num,
    )


def mark_staging_insurance_state(staging_id: str, dealer_id: int, state: int) -> None:
    """
    Set ``insurance_state`` on the staging row. Logs a warning when no row matches;
    does not raise (Playwright flow must continue).
    """
    sid = (staging_id or "").strip()
    if not sid:
        return
    try:
        updated = update_staging_processing_state(
            sid,
            int(dealer_id),
            insurance_state=int(state),
        )
    except Exception as exc:
        logger.warning(
            "mark_staging_insurance_state failed staging_id=%s dealer_id=%s state=%s: %s",
            sid,
            dealer_id,
            state,
            exc,
        )
        return
    if not updated:
        logger.warning(
            "mark_staging_insurance_state: no row updated staging_id=%s dealer_id=%s state=%s",
            sid,
            dealer_id,
            state,
        )


def resolved_staging_dms_state(
    *,
    staging_id: str | None,
    dealer_id: int | None,
    dms_state_hint: int | None,
) -> int | None:
    if dms_state_hint is not None:
        return int(dms_state_hint)
    sid = (staging_id or "").strip()
    if sid and dealer_id is not None:
        from app.repositories.add_sales_staging import fetch_staging_dms_state

        return fetch_staging_dms_state(sid, int(dealer_id))
    return None


def mark_staging_dms_state(staging_id: str, dealer_id: int, state: int) -> None:
    """Set ``dms_state`` on the staging row (reserved for future DMS milestones)."""
    sid = (staging_id or "").strip()
    if not sid:
        return
    try:
        updated = update_staging_processing_state(
            sid,
            int(dealer_id),
            dms_state=int(state),
        )
    except Exception as exc:
        logger.warning(
            "mark_staging_dms_state failed staging_id=%s dealer_id=%s state=%s: %s",
            sid,
            dealer_id,
            state,
            exc,
        )
        return
    if not updated:
        logger.warning(
            "mark_staging_dms_state: no row updated staging_id=%s dealer_id=%s state=%s",
            sid,
            dealer_id,
            state,
        )
