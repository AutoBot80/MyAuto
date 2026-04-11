"""RTO queue batch processing. Batch loop claims rows, delegates per-row fill to fill_rto_service."""

import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

from app.db import get_connection
from app.repositories import rto_payment_details as repo
from app.services.fill_rto_service import fill_rto_row
from app.services.playwright_executor import run_playwright_callable_sync

logger = logging.getLogger(__name__)
_BATCH_LOCK = threading.Lock()
_BATCH_STATUS_BY_DEALER: dict[int, dict] = {}


def _batch_lock_key(dealer_id: int) -> int:
    return 9_200_000 + int(dealer_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_batch_status(dealer_id: int) -> dict | None:
    with _BATCH_LOCK:
        state = _BATCH_STATUS_BY_DEALER.get(int(dealer_id))
        return dict(state) if state else None


def _write_batch_status(dealer_id: int, **changes) -> dict:
    with _BATCH_LOCK:
        dealer_key = int(dealer_id)
        current = dict(_BATCH_STATUS_BY_DEALER.get(dealer_key) or {})
        current.update(changes)
        _BATCH_STATUS_BY_DEALER[dealer_key] = current
        return dict(current)


def _append_row_result(dealer_id: int, row_result: dict) -> None:
    with _BATCH_LOCK:
        dealer_key = int(dealer_id)
        current = dict(_BATCH_STATUS_BY_DEALER.get(dealer_key) or {})
        rows = list(current.get("rows") or [])
        rows.append(row_result)
        current["rows"] = rows
        _BATCH_STATUS_BY_DEALER[dealer_key] = current


def _is_retryable_error(error: Exception) -> bool:
    """Errors that should return the row to Pending so operator can retry."""
    message = str(error or "").lower()
    markers = (
        "session expired",
        "target page, context or browser has been closed",
        "browser has been closed",
        "target closed",
        "execution context was destroyed",
        "timeout",
        "opened. please login",
        "site not open",
        "please open vahan site",
        "not yet implemented",
        "no otp submitted",
        "operatorotptimeout",
    )
    return any(marker in message for marker in markers)


def get_dealer_batch_status(dealer_id: int) -> dict:
    defaults = {
        "dealer_id": int(dealer_id),
        "session_id": None,
        "state": "idle",
        "message": "No active batch",
        "started_at": None,
        "completed_at": None,
        "current_rto_queue_id": None,
        "current_customer_name": None,
        "total_count": 0,
        "processed_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "last_error": None,
        "rows": [],
        "otp_pending": False,
        "otp_rto_queue_id": None,
        "otp_customer_mobile": None,
        "otp_prompt": None,
    }
    cur = _read_batch_status(dealer_id)
    if not cur:
        return dict(defaults)
    merged = {**defaults, **cur}
    return merged


def start_dealer_rto_batch(
    *,
    dealer_id: int,
    limit: int = 7,
) -> dict:
    dealer_id = int(dealer_id)
    with _BATCH_LOCK:
        existing = _BATCH_STATUS_BY_DEALER.get(dealer_id)
        if existing and existing.get("state") in {"starting", "running"}:
            return {
                "started": False,
                "session_id": existing.get("session_id"),
                "message": existing.get("message") or "A batch is already running for this dealer",
            }
        session_id = f"rto-batch-{uuid4().hex}"
        _BATCH_STATUS_BY_DEALER[dealer_id] = {
            "dealer_id": dealer_id,
            "session_id": session_id,
            "state": "starting",
            "message": "Starting dealer batch",
            "started_at": _utc_now(),
            "completed_at": None,
            "current_rto_queue_id": None,
            "current_customer_name": None,
            "total_count": 0,
            "processed_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "last_error": None,
            "rows": [],
        }
    thread = threading.Thread(
        target=_run_dealer_rto_batch,
        kwargs={
            "dealer_id": dealer_id,
            "session_id": session_id,
            "limit": limit,
        },
        daemon=True,
        name=f"rto-batch-{dealer_id}",
    )
    thread.start()
    return {"started": True, "session_id": session_id, "message": "Dealer batch started"}


def _run_dealer_rto_batch(
    *,
    dealer_id: int,
    session_id: str,
    limit: int,
) -> None:
    worker_id = f"dealer-{dealer_id}:{session_id}"
    advisory_conn = None
    acquired_lock = False
    current_queue_id: int | None = None
    current_sales_id: int | None = None
    try:
        advisory_conn = get_connection()
        advisory_conn.autocommit = True
        with advisory_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (_batch_lock_key(dealer_id),))
            row = cur.fetchone()
            acquired_lock = bool(row and row.get("locked"))
        if not acquired_lock:
            _write_batch_status(
                dealer_id,
                state="failed",
                completed_at=_utc_now(),
                message="Another RTO session is already running for this dealer",
                last_error="Dealer lock is already held",
            )
            return

        _write_batch_status(dealer_id, state="running", message="Claiming queued rows")
        claimed_rows = repo.claim_oldest_batch(
            dealer_id=dealer_id,
            processing_session_id=session_id,
            worker_id=worker_id,
            limit=max(1, min(int(limit or 7), 7)),
        )
        total_count = len(claimed_rows)
        _write_batch_status(
            dealer_id,
            total_count=total_count,
            message="No queued rows found" if total_count == 0 else f"Claimed {total_count} queued row(s)",
        )
        if total_count == 0:
            _write_batch_status(dealer_id, state="completed", completed_at=_utc_now())
            return

        for index, row in enumerate(claimed_rows, start=1):
            current_queue_id = row.get("rto_queue_id")
            current_sales_id = int(row["sales_id"])
            _write_batch_status(
                dealer_id,
                message=f"Processing {index} of {total_count}",
                current_rto_queue_id=current_queue_id,
                current_customer_name=row.get("customer_name"),
            )
            try:
                repo.mark_batch_row_in_progress(current_queue_id, session_id, worker_id)

                # Run on the Playwright worker thread (same as warm-browser / Fill DMS). If fill_rto_row ran on
                # this daemon batch thread, handle_browser_opening would start a second sync_playwright and
                # discard the first driver — which can close the Vahan browser window.
                batch_result = run_playwright_callable_sync(lambda: fill_rto_row(row))

                rto_application_id = (batch_result.get("rto_application_id") or "").strip() or None
                completed = bool(batch_result.get("completed"))
                if not completed:
                    raise RuntimeError("Vahan fill did not reach the target checkpoint")
                repo.mark_batch_row_completed(
                    current_queue_id,
                    current_sales_id,
                    session_id,
                    worker_id,
                    rto_application_id=rto_application_id,
                    rto_payment_amount=batch_result.get("rto_payment_amount"),
                )
                updated = _read_batch_status(dealer_id) or {}
                processed_count = int(updated.get("processed_count") or 0) + 1
                completed_count = int(updated.get("completed_count") or 0) + 1
                _write_batch_status(
                    dealer_id,
                    processed_count=processed_count,
                    completed_count=completed_count,
                    message=f"Completed {processed_count} of {total_count} rows",
                    last_error=None,
                )
                _append_row_result(
                    dealer_id,
                    {
                        "rto_queue_id": current_queue_id,
                        "customer_name": row.get("customer_name"),
                        "status": "Completed",
                        "rto_application_id": rto_application_id,
                        "rto_payment_amount": batch_result.get("rto_payment_amount"),
                        "error": None,
                    },
                )
                current_queue_id = None
                current_sales_id = None
            except Exception as exc:
                logger.warning("rto_batch: failed rto_queue_id=%s: %s", current_queue_id, exc)
                should_return_pending = _is_retryable_error(exc)
                if should_return_pending:
                    repo.mark_batch_row_pending(current_queue_id, session_id, worker_id, str(exc))
                else:
                    repo.mark_batch_row_failed(current_queue_id, session_id, worker_id, str(exc))
                updated = _read_batch_status(dealer_id) or {}
                processed_count = int(updated.get("processed_count") or 0) + 1
                failed_count = int(updated.get("failed_count") or 0) + (0 if should_return_pending else 1)
                _write_batch_status(
                    dealer_id,
                    processed_count=processed_count,
                    failed_count=failed_count,
                    last_error=str(exc),
                    message=(
                        f"Returned row {processed_count} of {total_count} to Pending (retryable)"
                        if should_return_pending
                        else f"Failed row {processed_count} of {total_count}"
                    ),
                )
                _append_row_result(
                    dealer_id,
                    {
                        "rto_queue_id": current_queue_id,
                        "customer_name": row.get("customer_name"),
                        "status": "Pending" if should_return_pending else "Failed",
                        "rto_application_id": None,
                        "rto_payment_amount": row.get("rto_payment_amount"),
                        "error": str(exc),
                    },
                )
                if should_return_pending:
                    raise
                current_queue_id = None
                current_sales_id = None
        final_status = _read_batch_status(dealer_id) or {}
        _write_batch_status(
            dealer_id,
            state="completed",
            completed_at=_utc_now(),
            current_rto_queue_id=None,
            current_customer_name=None,
            message=(
                f"Batch finished: {final_status.get('completed_count', 0)} completed, "
                f"{final_status.get('failed_count', 0)} failed"
            ),
        )
    except Exception as exc:
        logger.exception("rto_batch: fatal dealer batch error dealer_id=%s", dealer_id)
        if current_queue_id:
            try:
                if _is_retryable_error(exc):
                    repo.mark_batch_row_pending(current_queue_id, session_id, worker_id, str(exc))
                else:
                    repo.mark_batch_row_failed(current_queue_id, session_id, worker_id, str(exc))
            except Exception:
                logger.exception("rto_batch: could not mark fatal row failed")
        pending_fatal = _is_retryable_error(exc)
        _write_batch_status(
            dealer_id,
            state="failed",
            completed_at=_utc_now(),
            last_error=str(exc),
            message="Dealer batch paused; rows returned to Pending" if pending_fatal else "Dealer batch failed",
        )
    finally:
        try:
            repo.release_batch_claims(session_id)
        except Exception:
            logger.exception("rto_batch: failed to release queued claims session_id=%s", session_id)
        if advisory_conn is not None:
            try:
                if acquired_lock:
                    with advisory_conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(%s)", (_batch_lock_key(dealer_id),))
            finally:
                advisory_conn.close()
