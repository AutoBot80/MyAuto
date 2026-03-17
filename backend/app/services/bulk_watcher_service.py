"""
File watcher for Bulk Upload/Scans. When Scans.pdf appears, process it and record in bulk_loads.
"""

import logging
import threading
import time
from pathlib import Path

from app.config import BULK_UPLOAD_DIR
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository
from app.services.bulk_upload_service import process_new_scans_and_record

logger = logging.getLogger(__name__)

DEALER_ID = 100001
POLL_INTERVAL_SEC = 5
_watcher_thread: threading.Thread | None = None
_watcher_stop = threading.Event()


def _process_one(scans_pdf: Path) -> bool:
    """Process one Scans.pdf and mark folder with .processed. Returns True if processed."""
    subfolder = scans_pdf.parent.name
    marker = scans_pdf.parent / ".processed"
    if marker.exists():
        return False

    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        row = BulkLoadsRepository.insert(
            conn,
            subfolder=subfolder,
            status="pending",
            folder_path=str(scans_pdf.parent),
        )
        conn.commit()
        bulk_load_id = row["id"]
    finally:
        conn.close()

    try:
        process_new_scans_and_record(bulk_load_id, scans_pdf, dealer_id=DEALER_ID)
        marker.touch()
        return True
    except Exception as e:
        logger.exception("bulk_watcher: process failed for %s", scans_pdf)
        from app.db import get_connection
        conn = get_connection()
        try:
            BulkLoadsRepository.update_status(conn, bulk_load_id, "Error", error_message=str(e))
            conn.commit()
        finally:
            conn.close()
        return True  # Consider it processed so we don't retry


def _watcher_loop() -> None:
    while not _watcher_stop.is_set():
        try:
            scans_dir = BULK_UPLOAD_DIR / "Scans"
            if scans_dir.is_dir():
                for subdir in scans_dir.iterdir():
                    if not subdir.is_dir():
                        continue
                    pdf_path = subdir / "Scans.pdf"
                    if pdf_path.exists() and not (subdir / ".processed").exists():
                        _process_one(pdf_path)
                        break  # Process one at a time
        except Exception as e:
            logger.exception("bulk_watcher: loop error: %s", e)
        _watcher_stop.wait(timeout=POLL_INTERVAL_SEC)


def start_watcher() -> None:
    """Start background watcher thread."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return
    _watcher_stop.clear()
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True)
    _watcher_thread.start()
    logger.info("bulk_watcher: started")


def stop_watcher() -> None:
    """Stop background watcher thread."""
    global _watcher_thread
    _watcher_stop.set()
    if _watcher_thread:
        _watcher_thread.join(timeout=POLL_INTERVAL_SEC * 2)
        _watcher_thread = None
    logger.info("bulk_watcher: stopped")
