"""Update ``add_sales_staging`` processing-state columns from automation."""

from __future__ import annotations

import logging

from app.repositories.add_sales_staging import update_staging_processing_state

logger = logging.getLogger(__name__)


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
