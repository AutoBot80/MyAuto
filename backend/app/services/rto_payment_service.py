"""
RTO Payment flow: navigate to Vahan search, fill Application ID and Chassis No.,
take screenshot, click Pay, capture TC number, update DB.
"""
import logging
import re
import threading
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.config import DMS_PLAYWRIGHT_HEADED, UPLOADS_DIR, get_ocr_output_dir
from app.db import get_connection
from app.repositories import rto_payment_details as repo
from app.services.fill_dms_service import run_fill_vahan_batch_row

logger = logging.getLogger(__name__)
_BATCH_LOCK = threading.Lock()
_BATCH_STATUS_BY_DEALER: dict[int, dict] = {}


def _safe_subfolder(name: str | None) -> str:
    """Safe directory name (one segment)."""
    if not name or not str(name).strip():
        return "rto_default"
    return re.sub(r"[^\w\-]", "_", str(name).strip()) or "rto_default"


def _is_session_expiry_error(error: Exception) -> bool:
    message = str(error or "").lower()
    markers = (
        "session expired",
        "target page, context or browser has been closed",
        "browser has been closed",
        "target closed",
        "execution context was destroyed",
        "timeout",
    )
    return isinstance(error, PlaywrightTimeout) or any(marker in message for marker in markers)


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


def get_dealer_batch_status(dealer_id: int) -> dict:
    return _read_batch_status(dealer_id) or {
        "dealer_id": int(dealer_id),
        "session_id": None,
        "state": "idle",
        "message": "No active batch",
        "started_at": None,
        "completed_at": None,
        "current_queue_id": None,
        "current_customer_name": None,
        "current_vahan_application_id": None,
        "total_count": 0,
        "processed_count": 0,
        "cart_count": 0,
        "failed_count": 0,
        "last_error": None,
        "rows": [],
    }


def start_dealer_rto_batch(
    *,
    dealer_id: int,
    operator_id: str | None,
    vahan_base_url: str,
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
            "current_queue_id": None,
            "current_customer_name": None,
            "current_vahan_application_id": None,
            "total_count": 0,
            "processed_count": 0,
            "cart_count": 0,
            "failed_count": 0,
            "last_error": None,
            "rows": [],
        }
    thread = threading.Thread(
        target=_run_dealer_rto_batch,
        kwargs={
            "dealer_id": dealer_id,
            "operator_id": operator_id,
            "session_id": session_id,
            "vahan_base_url": vahan_base_url,
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
    operator_id: str | None,
    session_id: str,
    vahan_base_url: str,
    limit: int,
) -> None:
    worker_id = f"dealer-{dealer_id}:{session_id}"
    advisory_conn = None
    acquired_lock = False
    claimed_rows: list[dict] = []
    current_queue_id: str | None = None
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
                message="Another RTO browser session is already running for this dealer",
                last_error="Dealer lock is already held",
            )
            return

        _write_batch_status(dealer_id, state="running", message="Claiming queued rows")
        claimed_rows = repo.claim_oldest_batch(
            dealer_id=dealer_id,
            processing_session_id=session_id,
            worker_id=worker_id,
            operator_id=operator_id,
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

        ocr_output_dir = get_ocr_output_dir(dealer_id)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=not DMS_PLAYWRIGHT_HEADED)
            context = browser.new_context()
            for index, row in enumerate(claimed_rows, start=1):
                current_queue_id = row.get("application_id")
                current_sales_id = int(row["sales_id"])
                _write_batch_status(
                    dealer_id,
                    message=f"Processing {index} of {total_count}",
                    current_queue_id=current_queue_id,
                    current_customer_name=row.get("name"),
                    current_vahan_application_id=None,
                )
                try:
                    repo.mark_batch_row_in_progress(current_queue_id or "", session_id, worker_id)
                    batch_result = run_fill_vahan_batch_row(
                        context,
                        vahan_base_url=vahan_base_url,
                        customer_id=int(row["customer_id"]),
                        vehicle_id=int(row["vehicle_id"]),
                        subfolder=row.get("subfolder"),
                        ocr_output_dir=Path(ocr_output_dir),
                    )
                    vahan_application_id = (batch_result.get("application_id") or "").strip() or None
                    added_to_cart = bool(batch_result.get("added_to_cart"))
                    if not added_to_cart:
                        raise RuntimeError("Vahan upload/cart checkpoint was not reached")
                    repo.mark_batch_row_cart_added(
                        current_queue_id or "",
                        current_sales_id,
                        session_id,
                        worker_id,
                        vahan_application_id=vahan_application_id,
                        rto_fees=batch_result.get("rto_fees"),
                    )
                    updated = _read_batch_status(dealer_id) or {}
                    processed_count = int(updated.get("processed_count") or 0) + 1
                    cart_count = int(updated.get("cart_count") or 0) + 1
                    _write_batch_status(
                        dealer_id,
                        processed_count=processed_count,
                        cart_count=cart_count,
                        current_vahan_application_id=vahan_application_id,
                        message=f"Added {processed_count} of {total_count} rows to RTO Cart",
                        last_error=None,
                    )
                    _append_row_result(
                        dealer_id,
                        {
                            "queue_id": current_queue_id,
                            "customer_name": row.get("name"),
                            "status": "Added To Cart",
                            "vahan_application_id": vahan_application_id,
                            "rto_fees": batch_result.get("rto_fees"),
                            "error": None,
                        },
                    )
                    current_queue_id = None
                    current_sales_id = None
                except Exception as exc:
                    logger.warning("rto_batch: failed queue_id=%s: %s", current_queue_id, exc)
                    if _is_session_expiry_error(exc):
                        repo.mark_batch_row_pending(current_queue_id or "", session_id, worker_id, str(exc))
                    else:
                        repo.mark_batch_row_failed(current_queue_id or "", session_id, worker_id, str(exc))
                    updated = _read_batch_status(dealer_id) or {}
                    processed_count = int(updated.get("processed_count") or 0) + 1
                    failed_count = int(updated.get("failed_count") or 0) + (0 if _is_session_expiry_error(exc) else 1)
                    _write_batch_status(
                        dealer_id,
                        processed_count=processed_count,
                        failed_count=failed_count,
                        last_error=str(exc),
                        message=(
                            f"Session expired; returned row {processed_count} of {total_count} to Pending"
                            if _is_session_expiry_error(exc)
                            else f"Failed row {processed_count} of {total_count}"
                        ),
                    )
                    _append_row_result(
                        dealer_id,
                        {
                            "queue_id": current_queue_id,
                            "customer_name": row.get("name"),
                            "status": "Pending" if _is_session_expiry_error(exc) else "Failed",
                            "vahan_application_id": None,
                            "rto_fees": row.get("rto_fees"),
                            "error": str(exc),
                        },
                    )
                    if _is_session_expiry_error(exc):
                        raise
                    current_queue_id = None
                    current_sales_id = None
            browser.close()
        final_status = _read_batch_status(dealer_id) or {}
        _write_batch_status(
            dealer_id,
            state="completed",
            completed_at=_utc_now(),
            current_queue_id=None,
            current_customer_name=None,
            message=(
                f"Batch finished: {final_status.get('cart_count', 0)} added to cart, "
                f"{final_status.get('failed_count', 0)} failed"
            ),
        )
    except Exception as exc:
        logger.exception("rto_batch: fatal dealer batch error dealer_id=%s", dealer_id)
        if current_queue_id:
            try:
                if _is_session_expiry_error(exc):
                    repo.mark_batch_row_pending(current_queue_id, session_id, worker_id, str(exc))
                else:
                    repo.mark_batch_row_failed(current_queue_id, session_id, worker_id, str(exc))
            except Exception:
                logger.exception("rto_batch: could not mark fatal row failed")
        _write_batch_status(
            dealer_id,
            state="failed",
            completed_at=_utc_now(),
            last_error=str(exc),
            message="Dealer batch session expired; rows returned to Pending" if _is_session_expiry_error(exc) else "Dealer batch failed",
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


def run_rto_pay(
    application_id: str,
    chassis_num: str | None,
    subfolder: str | None,
    vahan_base_url: str,
    rto_dealer_id: str = "RTO100001",
    customer_name: str | None = None,
    rto_fees: float = 200.0,
    uploads_dir: Path | None = None,
) -> dict:
    """
    Open dummy Vahan worklist, step through payment gateway and bank confirmation,
    then capture the generated TC number.
    Returns { success, pay_txn_id, screenshot_path, error }.
    """
    result: dict = {"success": False, "pay_txn_id": None, "screenshot_path": None, "error": None}
    base = vahan_base_url.rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        result["error"] = "vahan_base_url must be absolute (http/https)"
        return result

    safe_sub = _safe_subfolder(subfolder) if subfolder else f"rto_{_safe_subfolder(application_id)}"
    uploads_path = Path(uploads_dir or UPLOADS_DIR).resolve()
    subfolder_path = uploads_path / safe_sub
    subfolder_path.mkdir(parents=True, exist_ok=True)
    screenshot_path = subfolder_path / "RTO Payment Proof.png"

    params = {"application_id": application_id}
    if chassis_num and str(chassis_num).strip():
        params["chassis_no"] = str(chassis_num).strip()
    if rto_dealer_id:
        params["rto_dealer_id"] = str(rto_dealer_id).strip()
    if customer_name and str(customer_name).strip():
        params["customer_name"] = str(customer_name).strip()[:100]
    if rto_fees:
        params["rto_fees"] = str(rto_fees)

    query = urllib.parse.urlencode(params)
    url = f"{base}/search.html?{query}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=not DMS_PLAYWRIGHT_HEADED,
            )
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(15_000)

            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#vahan-result-section:not(.hidden)", timeout=8000)
            page.screenshot(path=str(screenshot_path))
            result["screenshot_path"] = str(screenshot_path)

            page.click("#vahan-payment-btn")
            page.wait_for_url("**/payment.html*", timeout=10000)
            page.check("#vahan-accept-terms")
            page.click("#vahan-payment-continue")
            page.wait_for_url("**/bank-login.html*", timeout=10000)
            page.click("#vahan-bank-login")
            page.wait_for_url("**/bank-confirm.html*", timeout=10000)
            page.click("#vahan-confirm-payment")
            page.wait_for_selector("#vahan-confirm-tc-number:not(.hidden)", timeout=5000)
            el = page.locator("#vahan-confirm-tc-number")
            tc = el.get_attribute("data-tc") if el.count() > 0 else None
            if tc and str(tc).strip():
                result["pay_txn_id"] = str(tc).strip()
                result["success"] = True
            else:
                result["error"] = "Could not capture TC number after Pay"

            browser.close()
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
    except Exception as e:
        result["error"] = str(e)

    return result
