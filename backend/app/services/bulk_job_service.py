"""Bulk job ingestion and worker execution."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import (
    BULK_JOB_MAX_ATTEMPTS,
    BULK_SQS_VISIBILITY_TIMEOUT_SEC,
    get_bulk_input_scans_dir,
    get_bulk_processing_dir,
    get_bulk_queue_dir,
    get_bulk_upload_dir,
    get_ocr_output_dir,
    get_uploads_dir,
)
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository
from app.services.bulk_queue_service import BulkQueueService
from app.services.ocr_extraction_log import append_ocr_extraction_log
from app.services.bulk_upload_service import process_bulk_pdf
from app.services.pre_ocr_service import (
    move_multi_customer_to_success_or_error,
    move_processing_to_success_or_error,
    move_to_rejected,
    run_pre_ocr_and_prepare,
)

logger = logging.getLogger(__name__)

_REJECTION_MESSAGES = {
    "mobile number": "10-digit mobile number",
    "Aadhar front": "Aadhar card (front side)",
    "Aadhar back": "Aadhar card (back side)",
    "sales details form (vehicle & customer info)": "sales details form (with Frame No, Chassis, Engine No, etc.)",
}


def _format_rejection_error(missing: list[str]) -> str:
    items = [_REJECTION_MESSAGES.get(m, m) for m in missing]
    return (
        f"Could not identify required pages: {', '.join(items)}. "
        "Please ensure your scan includes all pages clearly visible and not blurry, "
        "with Aadhar (front & back) and the sales details form (vehicle & customer info)."
    )


def _move_to_error_on_exception(
    original_filename_stem: str,
    original_scan_path: Path | None = None,
    bulk_upload_dir: Path | None = None,
) -> str:
    base = bulk_upload_dir
    if base is None:
        raise RuntimeError("bulk_upload_dir is required")
    error_dir = base / "Error"
    proc_dir = base / "Processing"
    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    dest_subdir = f"{original_filename_stem}_{ddmmyyyy}"
    dest_dir = error_dir / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    result_folder = f"Error/{dest_subdir}"

    if original_scan_path and original_scan_path.exists():
        shutil.move(str(original_scan_path), str(dest_dir / original_scan_path.name))

    pdf_file = proc_dir / f"{original_filename_stem}.pdf"
    if pdf_file.exists():
        shutil.move(str(pdf_file), str(dest_dir / pdf_file.name))

    classified_dir = proc_dir / f"classified_{original_filename_stem}"
    if classified_dir.is_dir():
        for f in classified_dir.iterdir():
            if f.is_file():
                shutil.move(str(f), str(dest_dir / f.name))
            elif f.is_dir():
                target = dest_dir / f.name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.move(str(f), str(target))
        shutil.rmtree(classified_dir, ignore_errors=True)

    for f in proc_dir.glob(f"{original_filename_stem}_*_pre_ocr.txt"):
        if f.is_file():
            shutil.move(str(f), str(dest_dir / f.name))

    return result_folder


def _cleanup_retry_scratch(scans_pdf: Path, dealer_id: int, subfolder: str | None = None) -> None:
    proc_dir = get_bulk_processing_dir(dealer_id)
    stem = scans_pdf.stem
    shutil.rmtree(proc_dir / f"classified_{stem}", ignore_errors=True)
    (proc_dir / f"{stem}.pdf").unlink(missing_ok=True)
    for txt in proc_dir.glob(f"{stem}_*_pre_ocr.txt"):
        txt.unlink(missing_ok=True)
    if subfolder:
        shutil.rmtree(get_uploads_dir(dealer_id) / subfolder, ignore_errors=True)
        shutil.rmtree(get_ocr_output_dir(dealer_id) / subfolder, ignore_errors=True)


def _initial_subfolder(scans_pdf: Path, dealer_id: int) -> str:
    scans_dir = get_bulk_input_scans_dir(dealer_id)
    return scans_pdf.stem if scans_pdf.parent == scans_dir else scans_pdf.parent.name


def build_source_token(scans_pdf: Path, dealer_id: int) -> str:
    stat = scans_pdf.stat()
    try:
        rel = scans_pdf.relative_to(get_bulk_upload_dir(dealer_id))
    except ValueError:
        rel = scans_pdf
    return f"{rel.as_posix()}|{stat.st_size}|{stat.st_mtime_ns}"


def discover_input_pdfs(dealer_id: int) -> list[Path]:
    scans_dir = get_bulk_input_scans_dir(dealer_id)
    if not scans_dir.is_dir():
        return []

    found: list[Path] = []
    subdirs = [d for d in scans_dir.iterdir() if d.is_dir()]
    for subdir in sorted(subdirs, key=lambda p: p.stat().st_mtime):
        pdf_path = subdir / "Scans.pdf"
        if pdf_path.is_file():
            found.append(pdf_path)

    direct_pdfs = [
        f for f in scans_dir.iterdir()
        if f.is_file() and f.suffix.lower() == ".pdf" and "scan" in f.stem.lower()
    ]
    found.extend(sorted(direct_pdfs, key=lambda p: p.stat().st_mtime))
    return found


def ingest_scan_file(scans_pdf: Path, dealer_id: int, queue_service: BulkQueueService) -> dict | None:
    if not scans_pdf.is_file():
        return None

    initial_subfolder = _initial_subfolder(scans_pdf, dealer_id)
    proposed_job_id = uuid4().hex
    source_token = build_source_token(scans_pdf, dealer_id)
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        row = BulkLoadsRepository.create_job(
            conn,
            job_id=proposed_job_id,
            subfolder=initial_subfolder,
            file_name=scans_pdf.name,
            folder_path=initial_subfolder,
            source_path=str(scans_pdf),
            source_token=source_token,
            status="Queued",
            job_status="received",
            processing_stage="INGEST",
            dealer_id=dealer_id,
        )
        conn.commit()
    finally:
        conn.close()

    if row["job_id"] != proposed_job_id:
        return row

    queue_dir = get_bulk_queue_dir(dealer_id) / row["job_id"]
    queue_dir.mkdir(parents=True, exist_ok=True)
    queued_path = queue_dir / scans_pdf.name
    shutil.move(str(scans_pdf), str(queued_path))

    conn = get_connection()
    try:
        BulkLoadsRepository.update_source_path(
            conn,
            row["job_id"],
            str(queued_path),
            folder_path=str(queued_path.relative_to(get_bulk_upload_dir(dealer_id)).as_posix()),
        )
        conn.commit()
    finally:
        conn.close()

    publish_job(row["job_id"], dealer_id, queue_service)
    row["source_path"] = str(queued_path)
    return row


def publish_job(job_id: str, dealer_id: int, queue_service: BulkQueueService) -> bool:
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        try:
            queue_service.send_job(job_id, dealer_id)
            BulkLoadsRepository.mark_queued(conn, job_id)
            conn.commit()
            return True
        except Exception as exc:
            logger.exception("bulk_jobs: enqueue failed for %s", job_id)
            BulkLoadsRepository.mark_retry_pending(conn, job_id, error_message=f"Queue publish failed: {exc}", error_code="QUEUE_PUBLISH")
            conn.commit()
            return False
    finally:
        conn.close()


def publish_ready_jobs(dealer_id: int, queue_service: BulkQueueService, limit: int = 100) -> int:
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        jobs = BulkLoadsRepository.list_publishable_jobs(conn, dealer_id=dealer_id, limit=limit)
    finally:
        conn.close()

    published = 0
    for job in jobs:
        if publish_job(job["job_id"], dealer_id, queue_service):
            published += 1
    return published


def ingest_pending_jobs(dealer_id: int, queue_service: BulkQueueService, limit: int = 200) -> int:
    created = 0
    for scans_pdf in discover_input_pdfs(dealer_id)[:limit]:
        if ingest_scan_file(scans_pdf, dealer_id, queue_service):
            created += 1
    created += publish_ready_jobs(dealer_id, queue_service, limit=limit)
    return created


def _finalize_retry_or_error(
    *,
    row: dict,
    dealer_id: int,
    scans_pdf: Path,
    error_message: str,
    error_code: str,
    retryable: bool,
    result_folder: str | None = None,
    retry_subfolder: str | None = None,
) -> str:
    if retryable and int(row.get("attempt_count") or 0) < BULK_JOB_MAX_ATTEMPTS:
        _cleanup_retry_scratch(scans_pdf, dealer_id, retry_subfolder)
        conn = get_connection()
        try:
            BulkLoadsRepository.mark_retry_pending(conn, row["job_id"], error_message=error_message, error_code=error_code)
            conn.commit()
        finally:
            conn.close()
        return "retry"

    if result_folder is None:
        result_folder = _move_to_error_on_exception(
            scans_pdf.stem,
            original_scan_path=scans_pdf,
            bulk_upload_dir=get_bulk_upload_dir(dealer_id),
        )
    conn = get_connection()
    try:
        BulkLoadsRepository.complete_job(
            conn,
            job_id=row["job_id"],
            status="Error",
            job_status="error",
            processing_stage="ERROR",
            error_message=error_message,
            error_code=error_code,
            result_folder=result_folder,
        )
        conn.commit()
    finally:
        conn.close()
    return "error"


def _handle_single_customer(row: dict, dealer_id: int, scans_pdf: Path, bundles: list[tuple[Path, str, str | None]], ocr_path: Path) -> str:
    sale_dir, subfolder, mobile = bundles[0]
    conn = get_connection()
    try:
        BulkLoadsRepository.update_status(
            conn,
            row["id"],
            "Processing",
            mobile=mobile,
            folder_path=subfolder,
            subfolder=subfolder,
            job_status="processing",
            processing_stage="PROCESSING",
        )
        conn.commit()
    finally:
        conn.close()

    result = process_bulk_pdf(sale_dir, dealer_id=dealer_id, subfolder_override=subfolder)
    if result.get("ok"):
        result_folder = move_processing_to_success_or_error(
            None,
            ocr_path,
            scans_pdf.stem,
            mobile,
            success=True,
            original_scan_path=scans_pdf,
            bulk_upload_dir=get_bulk_upload_dir(dealer_id),
        )
        conn = get_connection()
        try:
            BulkLoadsRepository.complete_job(
                conn,
                job_id=row["job_id"],
                status="Success",
                job_status="success",
                processing_stage="COMPLETE",
                error_message=None,
                mobile=result.get("mobile"),
                name=result.get("name"),
                folder_path=subfolder,
                subfolder=subfolder,
                result_folder=result_folder,
            )
            conn.commit()
        finally:
            conn.close()
        return "success"

    if result.get("retryable"):
        return _finalize_retry_or_error(
            row=row,
            dealer_id=dealer_id,
            scans_pdf=scans_pdf,
            error_message=result.get("error") or "Bulk processing failed",
            error_code="PROCESSING_RETRYABLE",
            retryable=True,
            retry_subfolder=result.get("subfolder"),
        )

    result_folder = move_processing_to_success_or_error(
        None,
        ocr_path,
        scans_pdf.stem,
        mobile,
        success=False,
        original_scan_path=scans_pdf,
        bulk_upload_dir=get_bulk_upload_dir(dealer_id),
    )
    return _finalize_retry_or_error(
        row=row,
        dealer_id=dealer_id,
        scans_pdf=scans_pdf,
        error_message=result.get("error") or "Bulk processing failed",
        error_code="PROCESSING_FAILED",
        retryable=False,
        result_folder=result_folder,
    )


def _handle_multi_customer(row: dict, dealer_id: int, scans_pdf: Path, bundles: list[tuple[Path, str, str | None]], ocr_path: Path) -> str:
    child_rows = [row]
    parent_job_id = row["job_id"]
    conn = get_connection()
    try:
        for index in range(1, len(bundles)):
            child = BulkLoadsRepository.create_job(
                conn,
                parent_job_id=parent_job_id,
                subfolder=f"{scans_pdf.stem}_Customer{index + 1}",
                file_name=row.get("file_name"),
                folder_path=f"{scans_pdf.stem}_Customer{index + 1}",
                source_path=row.get("source_path"),
                status="Processing",
                job_status="processing",
                processing_stage="PROCESSING",
                dealer_id=dealer_id,
            )
            child_rows.append(child)
        BulkLoadsRepository.update_status(
            conn,
            row["id"],
            "Processing",
            subfolder=f"{scans_pdf.stem}_Customer1",
            folder_path=f"{scans_pdf.stem}_Customer1",
            job_status="processing",
            processing_stage="PROCESSING",
        )
        conn.commit()
    finally:
        conn.close()

    results: list[bool] = []
    for index, (sale_dir, subfolder, mobile) in enumerate(bundles):
        target_row = child_rows[index]
        conn = get_connection()
        try:
            BulkLoadsRepository.update_status(
                conn,
                target_row["id"],
                "Processing",
                mobile=mobile,
                folder_path=subfolder,
                subfolder=subfolder,
                job_status="processing",
                processing_stage="PROCESSING",
            )
            conn.commit()
        finally:
            conn.close()

        result = process_bulk_pdf(sale_dir, dealer_id=dealer_id, subfolder_override=subfolder)
        results.append(bool(result.get("ok")))
        conn = get_connection()
        try:
            BulkLoadsRepository.complete_job(
                conn,
                job_id=target_row["job_id"],
                status="Success" if result.get("ok") else "Error",
                job_status="success" if result.get("ok") else "error",
                processing_stage="COMPLETE" if result.get("ok") else "ERROR",
                error_message=None if result.get("ok") else result.get("error"),
                error_code=None if result.get("ok") else ("PROCESSING_RETRYABLE" if result.get("retryable") else "PROCESSING_FAILED"),
                mobile=result.get("mobile") or mobile,
                name=result.get("name"),
                folder_path=subfolder,
                subfolder=subfolder,
            )
            conn.commit()
        finally:
            conn.close()

    result_folders = move_multi_customer_to_success_or_error(
        bundles,
        ocr_path,
        scans_pdf.stem,
        results,
        original_scan_path=scans_pdf,
        bulk_upload_dir=get_bulk_upload_dir(dealer_id),
    )
    conn = get_connection()
    try:
        for index, result_folder in enumerate(result_folders):
            BulkLoadsRepository.update_result_folder(conn, child_rows[index]["id"], result_folder)
        conn.commit()
    finally:
        conn.close()
    return "success" if all(results) else "mixed"


def process_job(job_id: str, dealer_id: int, worker_id: str) -> str:
    conn = get_connection()
    try:
        BulkLoadsRepository.ensure_table(conn)
        row = BulkLoadsRepository.lease_job(conn, job_id, worker_id, BULK_SQS_VISIBILITY_TIMEOUT_SEC)
        conn.commit()
    finally:
        conn.close()
    if not row:
        return "skipped"

    scans_pdf = Path(row.get("source_path") or "")
    if not scans_pdf.is_file():
        return _finalize_retry_or_error(
            row=row,
            dealer_id=dealer_id,
            scans_pdf=scans_pdf,
            error_message=f"Queued source file is missing: {scans_pdf}",
            error_code="SOURCE_MISSING",
            retryable=True,
        )

    proc_dir = get_bulk_processing_dir(dealer_id)
    proc_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        BulkLoadsRepository.update_stage(conn, row["job_id"], "PRE_OCR")
        conn.commit()
    finally:
        conn.close()

    try:
        bundles, subfolder_stem, mobile, ocr_path, missing, _page_imgs, _dest_pdf, _rejected_extras, _ddt_prefetch = run_pre_ocr_and_prepare(
            scans_pdf,
            processing_dir=proc_dir,
            dealer_id=dealer_id,
        )
    except Exception as exc:
        logger.exception("bulk_jobs: pre-OCR failed for %s", scans_pdf)
        return _finalize_retry_or_error(
            row=row,
            dealer_id=dealer_id,
            scans_pdf=scans_pdf,
            error_message=f"Pre-OCR failed: {exc}",
            error_code="PRE_OCR_FAILED",
            retryable=True,
        )

    if bundles:
        _pre_sf, _pre_mob = bundles[0][1], bundles[0][2]
        append_ocr_extraction_log(
            get_ocr_output_dir(dealer_id),
            _pre_sf,
            "pre",
            (
                f"Bulk pre-OCR complete: mobile={_pre_mob!r} scan_stem={subfolder_stem!r} "
                f"ocr_text={ocr_path.name if ocr_path else 'none'}"
            ),
        )

    if missing:
        error_msg = _format_rejection_error(missing)
        result_folder = move_to_rejected(
            proc_dir / f"{scans_pdf.stem}.pdf",
            ocr_path,
            scans_pdf.stem,
            original_scan_path=scans_pdf,
            bulk_upload_dir=get_bulk_upload_dir(dealer_id),
        )
        conn = get_connection()
        try:
            BulkLoadsRepository.complete_job(
                conn,
                job_id=row["job_id"],
                status="Rejected",
                job_status="rejected",
                processing_stage="REJECTED",
                error_message=error_msg,
                error_code="PRE_OCR_REJECTED",
                mobile=mobile,
                folder_path=result_folder,
                subfolder=subfolder_stem,
                result_folder=result_folder,
            )
            conn.commit()
        finally:
            conn.close()
        return "rejected"

    conn = get_connection()
    try:
        BulkLoadsRepository.update_status(
            conn,
            row["id"],
            "Processing",
            mobile=mobile,
            subfolder=subfolder_stem,
            folder_path=subfolder_stem,
            job_status="processing",
            processing_stage="PROCESSING",
        )
        conn.commit()
    finally:
        conn.close()

    if len(bundles) > 1:
        return _handle_multi_customer(row, dealer_id, scans_pdf, bundles, ocr_path)
    return _handle_single_customer(row, dealer_id, scans_pdf, bundles, ocr_path)
