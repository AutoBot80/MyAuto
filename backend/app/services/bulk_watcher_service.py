"""Bulk ingest watcher and worker pool."""

from __future__ import annotations

import logging
import threading

from app.config import (
    BULK_INGEST_ENABLED,
    BULK_INGEST_POLL_SEC,
    BULK_WORKER_ENABLED,
    BULK_WORKER_THREADS,
    DEALER_ID,
)
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository
from app.services.bulk_job_service import ingest_pending_jobs, process_job
from app.services.bulk_queue_service import BulkQueueService

logger = logging.getLogger(__name__)

_watcher_stop = threading.Event()
_ingest_thread: threading.Thread | None = None
_worker_threads: list[threading.Thread] = []
_queue_service = BulkQueueService()


def _ingest_loop() -> None:
    while not _watcher_stop.is_set():
        try:
            ingest_pending_jobs(DEALER_ID, _queue_service)
        except Exception as exc:
            logger.exception("bulk_watcher: ingest loop failed: %s", exc)
        _watcher_stop.wait(timeout=BULK_INGEST_POLL_SEC)


def _worker_loop(worker_index: int) -> None:
    worker_id = f"bulk-worker-{worker_index}"
    while not _watcher_stop.is_set():
        handled = False
        try:
            messages = _queue_service.receive_messages(max_messages=1, wait_time_sec=1)
            if messages:
                handled = True
            for message in messages:
                try:
                    process_job(message.job_id, message.dealer_id, worker_id)
                finally:
                    _queue_service.ack_message(message)

            if not handled:
                conn = get_connection()
                try:
                    BulkLoadsRepository.ensure_table(conn)
                    fallback_jobs = BulkLoadsRepository.list_runnable_jobs(conn, dealer_id=DEALER_ID, limit=1)
                    conn.commit()
                finally:
                    conn.close()
                for row in fallback_jobs:
                    handled = True
                    process_job(row["job_id"], int(row.get("dealer_id") or DEALER_ID), worker_id)
        except Exception as exc:
            logger.exception("bulk_watcher: worker loop failed: %s", exc)
        if not handled:
            _watcher_stop.wait(timeout=1)


def start_watcher() -> None:
    global _ingest_thread, _worker_threads
    if (_ingest_thread and _ingest_thread.is_alive()) or any(t.is_alive() for t in _worker_threads):
        return

    _watcher_stop.clear()
    _worker_threads = []

    if BULK_INGEST_ENABLED:
        _ingest_thread = threading.Thread(target=_ingest_loop, daemon=True, name="bulk-ingest")
        _ingest_thread.start()

    if BULK_WORKER_ENABLED:
        for index in range(max(1, BULK_WORKER_THREADS)):
            thread = threading.Thread(target=_worker_loop, args=(index + 1,), daemon=True, name=f"bulk-worker-{index + 1}")
            thread.start()
            _worker_threads.append(thread)

    logger.info(
        "bulk_watcher: started ingest=%s workers=%d",
        BULK_INGEST_ENABLED,
        len(_worker_threads),
    )


def stop_watcher() -> None:
    global _ingest_thread, _worker_threads
    _watcher_stop.set()
    if _ingest_thread:
        _ingest_thread.join(timeout=max(2, BULK_INGEST_POLL_SEC * 2))
        _ingest_thread = None
    for thread in _worker_threads:
        thread.join(timeout=4)
    _worker_threads = []
    logger.info("bulk_watcher: stopped")
