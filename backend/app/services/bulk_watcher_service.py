"""
File watcher for Bulk Upload/Input Scans. When Scans.pdf appears, copy to Processing,
run pre-OCR (Tesseract/Textract) to extract mobile, rename to mobile.pdf if found,
then process and record in bulk_loads.
"""

import logging
import threading
import time
from pathlib import Path

from app.config import BULK_UPLOAD_DIR, BULK_PRE_OCR_USE_TEXTRACT
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository
from app.services.bulk_upload_service import process_new_scans_and_record
from app.services.pre_ocr_service import (
    run_pre_ocr_and_prepare,
    move_processing_to_success_or_error,
    move_to_rejected,
    PROCESSING_DIR,
)

logger = logging.getLogger(__name__)

DEALER_ID = 100001
POLL_INTERVAL_SEC = 5
_watcher_thread: threading.Thread | None = None
_watcher_stop = threading.Event()


def _move_to_error_on_exception(original_filename_stem: str, original_scan_path: Path | None = None) -> str:
    """When pre-OCR raises, move original from Scans and Processing PDF/pre-OCR to Error. Returns result_folder path."""
    from app.services.pre_ocr_service import ERROR_DIR
    from datetime import datetime
    import shutil

    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    dest_subdir = f"{original_filename_stem}_{ddmmyyyy}"
    dest_dir = ERROR_DIR / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    result_folder = f"Error/{dest_subdir}"

    # Move original scan from Scans folder first
    if original_scan_path and original_scan_path.exists():
        shutil.move(str(original_scan_path), str(dest_dir / original_scan_path.name))
        logger.info("Moved original scan %s -> %s", original_scan_path.name, dest_dir)

    # Move PDF and classified dir (if any) and pre-OCR from Processing
    pdf_file = PROCESSING_DIR / f"{original_filename_stem}.pdf"
    if pdf_file.exists():
        shutil.move(str(pdf_file), str(dest_dir / pdf_file.name))
        logger.info("Moved %s -> %s", pdf_file.name, dest_dir)
    classified_dir = PROCESSING_DIR / f"classified_{original_filename_stem}"
    if classified_dir.is_dir():
        for f in classified_dir.iterdir():
            if f.is_file():
                shutil.move(str(f), str(dest_dir / f.name))
                logger.info("Moved %s -> %s", f.name, dest_dir)
        try:
            classified_dir.rmdir()
        except OSError:
            pass
    for f in PROCESSING_DIR.glob(f"{original_filename_stem}_*_pre_ocr.txt"):
        if f.is_file():
            shutil.move(str(f), str(dest_dir / f.name))
            logger.info("Moved %s -> %s", f.name, dest_dir)

    return result_folder


def _process_one(scans_pdf: Path, marker: Path | None = None) -> bool:
    """Process one Scans.pdf and mark with .processed. Returns True if processed."""
    if marker is None:
        marker = scans_pdf.parent / ".processed"
    if marker.exists():
        return False
    scans_dir = BULK_UPLOAD_DIR / "Input Scans"
    initial_subfolder = scans_pdf.stem if scans_pdf.parent == scans_dir else scans_pdf.parent.name

    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        row = BulkLoadsRepository.insert(
            conn,
            subfolder=initial_subfolder,
            file_name=scans_pdf.name,
            status="Processing",
            folder_path=initial_subfolder,
        )
        conn.commit()
        bulk_load_id = row["id"]
    finally:
        conn.close()

    # Mark as in-progress immediately to prevent duplicate processing
    marker.touch()

    try:
        # Pre-OCR: copy to Processing, run OCR, save as filename_ddmmyyyy_pre_ocr.txt,
        # extract mobile. Mobile is required for Add Customer - do not proceed without it.
        PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
        try:
            classified_dir, subfolder, mobile, ocr_path, missing = run_pre_ocr_and_prepare(
                scans_pdf,
                processing_dir=PROCESSING_DIR,
                use_textract=BULK_PRE_OCR_USE_TEXTRACT,
            )
        except Exception as e:
            logger.exception("bulk_watcher: pre-OCR failed for %s", scans_pdf)
            conn = get_connection()
            try:
                BulkLoadsRepository.update_status(conn, bulk_load_id, "Error", error_message=f"Pre-OCR failed: {e}")
                conn.commit()
            finally:
                conn.close()
            # Move to Error: Processing PDF/pre-OCR and original from Input Scans
            result_folder = _move_to_error_on_exception(scans_pdf.stem, original_scan_path=scans_pdf)
            conn = get_connection()
            try:
                BulkLoadsRepository.update_status(conn, bulk_load_id, "Error", result_folder=result_folder)
                conn.commit()
            finally:
                conn.close()
            if marker.exists():
                marker.unlink()
            return True

        # Validation failed (classification could not identify mobile + 3 critical pages) — move to Rejected, no split
        if missing:
            error_msg = f"Rejected: missing {', '.join(missing)}"
            logger.warning("bulk_watcher: %s for %s", error_msg, scans_pdf)
            pdf_in_proc = PROCESSING_DIR / f"{scans_pdf.stem}.pdf"
            result_folder = move_to_rejected(
                pdf_in_proc, ocr_path, scans_pdf.stem,
                original_scan_path=scans_pdf,
            )
            conn = get_connection()
            try:
                BulkLoadsRepository.update_status(
                    conn, bulk_load_id, "Rejected",
                    error_message=error_msg,
                    folder_path=result_folder,
                    subfolder=subfolder,
                )
                BulkLoadsRepository.update_result_folder(conn, bulk_load_id, result_folder)
                conn.commit()
            finally:
                conn.close()
            if marker.exists():
                marker.unlink()
            return True

        # Update bulk_loads with final subfolder (mobile_ddmmyy)
        conn = get_connection()
        try:
            BulkLoadsRepository.update_status(conn, bulk_load_id, "Processing", folder_path=subfolder, subfolder=subfolder, mobile=mobile)
            conn.commit()
        finally:
            conn.close()

        ok = process_new_scans_and_record(
            bulk_load_id,
            classified_dir,
            dealer_id=DEALER_ID,
            subfolder_override=subfolder,
        )
        result_folder = move_processing_to_success_or_error(
            classified_dir, ocr_path, scans_pdf.stem, mobile, success=ok,
            original_scan_path=scans_pdf,
        )
        conn = get_connection()
        try:
            BulkLoadsRepository.update_result_folder(conn, bulk_load_id, result_folder)
            conn.commit()
        finally:
            conn.close()
        if marker.exists():
            marker.unlink()
        return True
    except Exception as e:
        logger.exception("bulk_watcher: process failed for %s", scans_pdf)
        conn = get_connection()
        try:
            BulkLoadsRepository.update_status(conn, bulk_load_id, "Error", error_message=str(e))
            conn.commit()
        finally:
            conn.close()
        # Move to Error (original from Input Scans, classified dir or PDF, pre-OCR)
        try:
            classified_dir = PROCESSING_DIR / f"classified_{scans_pdf.stem}"
            processing_path = classified_dir if classified_dir.is_dir() else next((f for f in PROCESSING_DIR.glob("*.pdf") if f.is_file()), None)
            ocr_in_proc = next(PROCESSING_DIR.glob(f"{scans_pdf.stem}_*_pre_ocr.txt"), None)
            if processing_path or ocr_in_proc or scans_pdf.exists():
                result_folder = move_processing_to_success_or_error(
                    processing_path,
                    ocr_in_proc,
                    scans_pdf.stem,
                    None,
                    success=False,
                    original_scan_path=scans_pdf,
                )
                conn = get_connection()
                try:
                    BulkLoadsRepository.update_status(conn, bulk_load_id, "Error", result_folder=result_folder)
                    conn.commit()
                finally:
                    conn.close()
        except Exception as move_err:
            logger.warning("Failed to move to Error: %s", move_err)
        if marker.exists():
            marker.unlink()
        return True  # Consider it processed so we don't retry


def _find_pending_pdf(scans_dir: Path) -> Path | None:
    """Find a Scans.pdf or Scan*.pdf to process. Oldest first. Checks subfolders first, then files directly in Input Scans."""
    # 1) Subfolders: Input Scans/<subfolder>/Scans.pdf — sort by mtime (oldest first)
    subdirs = [d for d in scans_dir.iterdir() if d.is_dir()]
    for subdir in sorted(subdirs, key=lambda p: p.stat().st_mtime):
        pdf_path = subdir / "Scans.pdf"
        if pdf_path.exists() and not (subdir / ".processed").exists():
            return pdf_path
    # 2) Files directly in Input Scans: Input Scans/Scan1.pdf, etc. — sort by mtime (oldest first)
    pdf_files = [
        f for f in scans_dir.iterdir()
        if f.is_file() and f.suffix.lower() == ".pdf" and f.stem.lower().startswith("scan")
    ]
    for f in sorted(pdf_files, key=lambda p: p.stat().st_mtime):
        marker = scans_dir / f".processed_{f.name}"
        if not marker.exists():
            return f
    return None


# Consider Processing stale after this many seconds (allows recovery from crashed runs)
PROCESSING_STALE_SEC = 900  # 15 minutes


def _has_processing_in_progress() -> bool:
    """Return True if any bulk_loads row has status Processing and is not stale (don't start another)."""
    conn = get_connection()
    try:
        rows = BulkLoadsRepository.list_all(conn, limit=10, status_filter="Processing")
        if not rows:
            return False
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for r in rows:
            updated = r.get("updated_at")
            if updated:
                if isinstance(updated, str):
                    try:
                        updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_sec = (now - updated).total_seconds()
                if age_sec < PROCESSING_STALE_SEC:
                    return True  # Recent Processing, wait
        return False  # All stale or no updated_at
    except Exception:
        return True  # On error, assume in progress to be safe
    finally:
        conn.close()


def _watcher_loop() -> None:
    while not _watcher_stop.is_set():
        try:
            # Don't start new work if something is already in Processing (prevents queue buildup)
            if _has_processing_in_progress():
                _watcher_stop.wait(timeout=POLL_INTERVAL_SEC)
                continue
            scans_dir = BULK_UPLOAD_DIR / "Input Scans"
            if scans_dir.is_dir():
                pdf_path = _find_pending_pdf(scans_dir)
                if pdf_path:
                    marker = (scans_dir / f".processed_{pdf_path.name}") if pdf_path.parent == scans_dir else (pdf_path.parent / ".processed")
                    _process_one(pdf_path, marker=marker)
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
