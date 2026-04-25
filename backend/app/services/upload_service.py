import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, UploadFile

from app.config import (
    UPLOAD_MAX_CONSOLIDATED_PDF_BYTES,
    UPLOAD_MAX_IMAGE_BYTES,
    UPLOAD_MAX_LEGACY_FILE_BYTES,
    UPLOAD_MAX_PDF_BYTES,
    get_add_sales_pre_ocr_work_dir,
    get_ocr_output_dir,
    get_uploaded_scans_sale_subfolder_leaf,
    get_uploads_dir,
)
from app.services.ocr_extraction_log import append_ocr_extraction_log, append_pre_ocr_step_lines
from app.services.ocr_sale_artifacts import (
    consolidate_peer_pre_ocr_folder_into_mobile,
    initial_artifact_leaf,
    remove_if_empty_initial_artifact_dir,
    safe_file_stem,
)

# Merged into ``details_forms_cache.json`` for manual-apply so we can remove stale pre-OCR ocr_output dirs
PRE_OCR_ARTIFACT_LEAF_CACHE_KEY = "_pre_ocr_artifact_leaf"
from app.services.page_classifier import FILENAME_AADHAR_FRONT
from app.services.upload_file_validation import (
    detect_image_or_pdf_kind,
    read_upload_capped,
    sanitize_legacy_upload_filename,
    validate_magic_jpeg_or_png,
    validate_magic_jpeg_png_or_pdf,
    validate_magic_jpeg_png_pdf_legacy,
)
from app.services.dealer_storage import sync_ocr_subfolder_to_s3, sync_uploads_subfolder_to_s3
from app.services.post_ocr_service import run_deferred_post_ocr_for_sale

# ai_reader_queue disabled; extraction runs directly after upload (Option 1)

logger = logging.getLogger(__name__)


class UploadService:
    """Business logic for scan uploads and queueing. Stateless, testable."""

    def __init__(self, uploads_dir: Path | None = None):
        self.uploads_dir = uploads_dir

    def validate_aadhar_last4(self, aadhar_last4: str) -> tuple[bool, str | None]:
        digits = "".join(c for c in aadhar_last4 if c.isdigit())
        if len(digits) != 4:
            return False, "Invalid aadhar. Expected last 4 digits."
        return True, None

    def get_subdir_name(self, aadhar_last4: str) -> str:
        digits = "".join(c for c in aadhar_last4 if c.isdigit())
        ddmm = datetime.now().strftime("%d%m")
        return f"{digits}_{ddmm}"

    def validate_mobile(self, mobile: str) -> tuple[bool, str | None]:
        digits = "".join(c for c in mobile if c.isdigit())
        if len(digits) != 10:
            return False, "Invalid mobile. Expected 10 digits."
        return True, None

    def get_subdir_name_mobile(self, mobile: str) -> str:
        # Same leaf as get_uploaded_scans_sale_folder / DMS report downloads (ddmmyy, last 10 digits).
        return get_uploaded_scans_sale_subfolder_leaf(mobile)

    def _unique_path(self, base_dir: Path, filename: str) -> Path:
        target = base_dir / Path(filename).name
        if not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        i = 1
        while True:
            candidate = base_dir / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    async def save_and_queue(
        self, aadhar_last4: str, files: list[UploadFile], dealer_id: int = 100001
    ) -> dict:
        ok, err = self.validate_aadhar_last4(aadhar_last4)
        if not ok:
            return {"error": err}

        uploads_dir = self.uploads_dir or get_uploads_dir(dealer_id)
        subdir_name = self.get_subdir_name(aadhar_last4)
        subdir = uploads_dir / subdir_name
        subdir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []

        for f in files:
            try:
                safe_name = sanitize_legacy_upload_filename(f.filename, default="scan.jpg")
                content = await read_upload_capped(f, UPLOAD_MAX_LEGACY_FILE_BYTES)
                validate_magic_jpeg_png_pdf_legacy(content, label=safe_name)
            except ValueError as e:
                return {"error": str(e)}
            target = self._unique_path(subdir, safe_name)
            target.write_bytes(content)
            saved.append(target.name)

        sync_uploads_subfolder_to_s3(dealer_id, subdir_name)
        return {
            "saved_count": len(saved),
            "saved_files": saved,
            "saved_to": subdir_name,
            "queued_items": [],
        }

    async def save_and_queue_v2(
        self,
        mobile: str,
        aadhar_scan: UploadFile,
        aadhar_back: UploadFile,
        sales_detail: UploadFile,
        insurance_sheet: UploadFile | None = None,
        financing_doc: UploadFile | None = None,
        dealer_id: int = 100001,
        *,
        background_tasks: BackgroundTasks | None = None,
    ) -> dict:
        """Subfolder = mobile_ddmmyy; save as Aadhar_front.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg."""
        ok, err = self.validate_mobile(mobile)
        if not ok:
            return {"error": err}

        uploads_dir = self.uploads_dir or get_uploads_dir(dealer_id)
        subdir_name = self.get_subdir_name_mobile(mobile)
        subdir = uploads_dir / subdir_name
        subdir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []

        async def save_image_field(upload: UploadFile, save_name: str, label: str) -> str | None:
            try:
                content = await read_upload_capped(upload, UPLOAD_MAX_IMAGE_BYTES)
                validate_magic_jpeg_or_png(content, label=label)
            except ValueError as e:
                return str(e)
            target = subdir / save_name
            target.write_bytes(content)
            saved.append(save_name)
            return None

        err_msg = await save_image_field(aadhar_scan, FILENAME_AADHAR_FRONT, "Aadhar front")
        if err_msg:
            return {"error": err_msg}
        err_msg = await save_image_field(aadhar_back, "Aadhar_back.jpg", "Aadhar back")
        if err_msg:
            return {"error": err_msg}
        err_msg = await save_image_field(sales_detail, "Details.jpg", "Sales details")
        if err_msg:
            return {"error": err_msg}

        if insurance_sheet and insurance_sheet.filename:
            try:
                content = await read_upload_capped(insurance_sheet, UPLOAD_MAX_IMAGE_BYTES)
                validate_magic_jpeg_or_png(content, label="Insurance sheet")
            except ValueError as e:
                return {"error": str(e)}
            (subdir / "Insurance.jpg").write_bytes(content)
            saved.append("Insurance.jpg")

        if financing_doc and financing_doc.filename:
            try:
                content = await read_upload_capped(financing_doc, UPLOAD_MAX_PDF_BYTES)
                validate_magic_jpeg_png_or_pdf(content, label="Financing document")
            except ValueError as e:
                return {"error": str(e)}
            # OCR pipeline expects this exact name (bytes may be PDF or image).
            (subdir / "Financing.jpg").write_bytes(content)
            saved.append("Financing.jpg")

        # Run extraction directly after upload (Option 1: no queue)
        extraction_result: dict = {}
        try:
            from app.services.pre_ocr_service import (
                orient_and_normalize_sale_documents,
                sale_folder_has_details_for_pencil_crop,
                try_write_pencil_mark_for_sale_folder,
            )

            sale_path = uploads_dir / subdir_name
            orient_and_normalize_sale_documents(sale_path)
            pencil_ok = try_write_pencil_mark_for_sale_folder(sale_path)
            pencil_warnings: list[str] = []
            if not pencil_ok and sale_folder_has_details_for_pencil_crop(sale_path):
                pencil_warnings.append(
                    "Chassis pencil mark image was not saved (optional). "
                    "OCR and DMS can still supply frame/chassis; verify manually if needed."
                )
            append_ocr_extraction_log(
                get_ocr_output_dir(dealer_id),
                subdir_name,
                "pre",
                "Manual multi-file upload: orient/normalize/pencil complete (no consolidated PDF pre-OCR).",
            )

            from app.services.sales_ocr_service import OcrService

            ocr = OcrService(
                uploads_dir=uploads_dir,
                ocr_output_dir=get_ocr_output_dir(dealer_id),
            )
            extraction_result = ocr.process_uploaded_subfolder(
                subdir_name,
                defer_post_ocr=background_tasks is not None,
            )
            if pencil_warnings:
                extraction_result = {**extraction_result, "warnings": pencil_warnings}
            # Include full extracted details so client can populate immediately (no polling)
            details = ocr.get_extracted_details(subdir_name)
            if details:
                extraction_result["details"] = details
        except Exception as e:
            extraction_result = {"error": str(e), "processed": []}

        udir = self.uploads_dir or get_uploads_dir(dealer_id)
        ocr_dir = get_ocr_output_dir(dealer_id)
        if background_tasks and (extraction_result.get("post_ocr") or {}).get("deferred"):
            background_tasks.add_task(
                run_deferred_post_ocr_for_sale,
                udir,
                ocr_dir,
                subdir_name,
                dealer_id,
            )
        else:
            sync_uploads_subfolder_to_s3(dealer_id, subdir_name)
        return {
            "saved_count": len(saved),
            "saved_files": saved,
            "saved_to": subdir_name,
            "queued_items": [],
            "extraction": extraction_result,
        }

    def _pre_ocr_rejection_message(self, missing: list[str]) -> str:
        return (
            f"Could not identify required pages: {', '.join(missing)}. "
            "Please ensure your scan includes all pages clearly visible and not blurry, "
            "with Aadhar (front & back) and the sales details form (vehicle & customer info)."
        )

    def _process_consolidated_pdf_sync(
        self,
        dest_pdf: Path,
        proc_dir: Path,
        dealer_id: int,
        *,
        mobile_hint: str | None = None,
        on_extraction_event: Any | None = None,
        extra_image_paths: list[Path] | None = None,
        defer_post_ocr: bool = False,
    ) -> dict:
        """CPU-bound pre-OCR + Textract; must run in a worker thread (see ``save_and_queue_v2_consolidated``).

        ``on_extraction_event(event_name, payload)`` is called from the worker when Aadhaar or Details
        fragment merge is persisted (``event_name`` == ``\"partial\"``) so the client can stream updates.
        """
        # Lazy import: ``pre_ocr_service`` references ``UploadService`` to avoid import cycles.
        from app.services.pre_ocr_service import run_pre_ocr_and_prepare

        try:
            bundles, _stem, _mobile_ocr, _ocr_path, missing, page_images, _, rejected_extras, ddt_prefetch, details_forms_prefetch = run_pre_ocr_and_prepare(
                dest_pdf,
                processing_dir=proc_dir,
                dealer_id=dealer_id,
                mobile_hint=mobile_hint,
                extra_image_paths=extra_image_paths,
            )
        except Exception as e:
            return {"error": f"Pre-OCR failed: {e}"}

        if missing:
            from app.services.manual_fallback_service import (
                write_details_forms_cache,
                write_manual_session_jpegs,
            )

            log_leaf = initial_artifact_leaf(safe_file_stem(dest_pdf.stem))
            ocr_out_dir = get_ocr_output_dir(dealer_id)
            _t0_wall: float = (rejected_extras or {}).get("_t0_wall") or time.perf_counter()

            def _post_off() -> int:
                return int((time.perf_counter() - _t0_wall) * 1000)

            post_pre_steps: list[tuple[str, int | None, str, int]] = []

            try:
                t_split0 = time.perf_counter()
                session_id, page_count = write_manual_session_jpegs(dealer_id, dest_pdf, page_images or {})
                split_ms = int((time.perf_counter() - t_split0) * 1000)
                post_pre_steps.append(
                    (
                        "manual_session_jpeg_split",
                        split_ms,
                        f"pages={page_count} session={session_id[:8]}…",
                        _post_off(),
                    ),
                )
            except Exception as e:
                logger.exception("manual fallback split failed")
                return {"error": f"{self._pre_ocr_rejection_message(missing)} (Could not prepare manual split: {e})"}

            if rejected_extras and rejected_extras.get("details_forms_cache"):
                t_cache0 = time.perf_counter()
                try:
                    _cache = {**rejected_extras["details_forms_cache"]}
                    _cache[PRE_OCR_ARTIFACT_LEAF_CACHE_KEY] = initial_artifact_leaf(dest_pdf.stem)
                    write_details_forms_cache(dealer_id, session_id, _cache)
                    cache_ms = int((time.perf_counter() - t_cache0) * 1000)
                    post_pre_steps.append(
                        ("details_forms_cache_json_write", cache_ms, "details_forms_cache.json for manual-apply reuse", _post_off()),
                    )
                except Exception:
                    logger.exception("Could not write details_forms cache for session %s", session_id)

            append_pre_ocr_step_lines(ocr_out_dir, log_leaf, post_pre_steps)

            try:
                sync_ocr_subfolder_to_s3(dealer_id, log_leaf)
            except Exception:
                logger.exception("Failed to sync rejected OCR artifacts to S3 for leaf=%s", log_leaf)

            mf: dict[str, Any] = {
                "session_id": session_id,
                "page_count": page_count,
                "missing_reasons": missing,
            }
            if rejected_extras:
                sr = rejected_extras.get("suggested_roles")
                if isinstance(sr, list) and sr:
                    mf["suggested_roles"] = sr
                li = rejected_extras.get("locked_details_index")
                if li is not None:
                    mf["locked_details_index"] = li

            extraction: dict[str, Any] = {"manual_only": True, "pending": True}
            ed = rejected_extras.get("extraction_details") if rejected_extras else None
            if ed:
                extraction["details"] = ed

            return {
                "saved_count": 0,
                "saved_files": [],
                "saved_to": "",
                "queued_items": [],
                "warning": self._pre_ocr_rejection_message(missing),
                "manual_fallback": mf,
                "extraction": extraction,
            }
        if not bundles:
            return {"error": "Pre-OCR did not produce a sale folder."}
        if len(bundles) > 1:
            return {
                "error": "Multiple customers detected in this PDF. Use Bulk Upload for multi-customer consolidated scans.",
            }

        _sale_dir, subfolder_name, _mobile_str = bundles[0]
        uploads_dir = self.uploads_dir or get_uploads_dir(dealer_id)
        subdir_name = subfolder_name
        pre_ocr_artifact_leaf = initial_artifact_leaf(dest_pdf.stem)
        append_ocr_extraction_log(
            get_ocr_output_dir(dealer_id),
            subfolder_name,
            "pre",
            f"Consolidated PDF pre-OCR complete: mobile={_mobile_str!r}.",
        )

        extraction_result: dict = {}
        try:
            from app.services.pre_ocr_service import (
                orient_and_normalize_sale_documents,
                sale_folder_has_details_for_pencil_crop,
                try_write_pencil_mark_for_sale_folder,
            )

            sale_path = uploads_dir / subdir_name
            orient_and_normalize_sale_documents(sale_path)
            pencil_ok = try_write_pencil_mark_for_sale_folder(sale_path)
            pencil_warnings: list[str] = []
            if not pencil_ok and sale_folder_has_details_for_pencil_crop(sale_path):
                pencil_warnings.append(
                    "Chassis pencil mark image was not saved (optional). "
                    "OCR and DMS can still supply frame/chassis; verify manually if needed."
                )

            from app.services.sales_ocr_service import OcrService

            ocr = OcrService(
                uploads_dir=uploads_dir,
                ocr_output_dir=get_ocr_output_dir(dealer_id),
            )
            extraction_result = ocr.process_uploaded_subfolder(
                subdir_name,
                on_extraction_event=on_extraction_event,
                details_forms_prefetch=details_forms_prefetch or None,
                ddt_prefetch=ddt_prefetch or None,
                defer_post_ocr=defer_post_ocr,
            )
            if pencil_warnings:
                extraction_result = {**extraction_result, "warnings": pencil_warnings}
            details = ocr.get_extracted_details(subdir_name)
            if details:
                extraction_result["details"] = details
        except Exception as e:
            extraction_result = {"error": str(e), "processed": []}
        finally:
            ocr_dealer = get_ocr_output_dir(dealer_id)
            try:
                consolidate_peer_pre_ocr_folder_into_mobile(ocr_dealer, subdir_name)
            except Exception:
                logger.exception(
                    "consolidate_peer_pre_ocr_folder_into_mobile failed subfolder=%s",
                    subdir_name,
                )
            try:
                remove_if_empty_initial_artifact_dir(ocr_dealer, subdir_name, pre_ocr_artifact_leaf)
            except Exception:
                logger.exception("remove_if_empty_initial_artifact_dir failed subfolder=%s", subdir_name)

        if not defer_post_ocr:
            sync_uploads_subfolder_to_s3(dealer_id, subdir_name)
            sync_ocr_subfolder_to_s3(dealer_id, subdir_name)

        saved: list[str] = []
        final_sale = uploads_dir / subdir_name
        if final_sale.is_dir():
            for p in sorted(final_sale.iterdir()):
                if p.is_file():
                    saved.append(p.name)

        return {
            "saved_count": len(saved),
            "saved_files": saved,
            "saved_to": subdir_name,
            "queued_items": [],
            "extraction": extraction_result,
        }

    def _apply_consolidated_manual_fallback_sync(
        self,
        session_id: str,
        mobile: str,
        assignments_json: str,
        dealer_id: int,
        *,
        defer_post_ocr: bool = False,
    ) -> dict:
        from app.services.manual_fallback_service import apply_manual_session, read_details_forms_cache

        try:
            assignments = json.loads(assignments_json)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid assignments JSON: {e}"}
        if not isinstance(assignments, dict):
            return {"error": "assignments must be a JSON object"}
        str_map = {str(k): str(v) for k, v in assignments.items()}

        details_forms_prefetch = read_details_forms_cache(dealer_id, session_id)
        pre_ocr_artifact_for_cleanup: str | None = None
        if details_forms_prefetch and isinstance(details_forms_prefetch, dict):
            _leaf = details_forms_prefetch.get(PRE_OCR_ARTIFACT_LEAF_CACHE_KEY)
            if isinstance(_leaf, str) and _leaf.strip():
                pre_ocr_artifact_for_cleanup = _leaf.strip()
        try:
            subfolder, _saved_paths = apply_manual_session(dealer_id, session_id, mobile, str_map)
        except ValueError as e:
            return {"error": str(e)}

        uploads_dir = self.uploads_dir or get_uploads_dir(dealer_id)
        extraction_result: dict[str, Any] = {}
        try:
            from app.services.pre_ocr_service import (
                orient_and_normalize_sale_documents,
                sale_folder_has_details_for_pencil_crop,
                try_write_pencil_mark_for_sale_folder,
            )
            from app.services.sales_ocr_service import OcrService

            sale_path = uploads_dir / subfolder
            orient_and_normalize_sale_documents(sale_path)
            pencil_ok = try_write_pencil_mark_for_sale_folder(sale_path)
            pencil_warnings: list[str] = []
            if not pencil_ok and sale_folder_has_details_for_pencil_crop(sale_path):
                pencil_warnings.append(
                    "Chassis pencil mark image was not saved (optional). "
                    "OCR and DMS can still supply frame/chassis; verify manually if needed."
                )

            prefetch = details_forms_prefetch if details_forms_prefetch and not details_forms_prefetch.get("error") else None
            if prefetch and isinstance(prefetch, dict) and PRE_OCR_ARTIFACT_LEAF_CACHE_KEY in prefetch:
                prefetch = {k: v for k, v in prefetch.items() if k != PRE_OCR_ARTIFACT_LEAF_CACHE_KEY}
            ocr = OcrService(
                uploads_dir=uploads_dir,
                ocr_output_dir=get_ocr_output_dir(dealer_id),
            )
            extraction_result = ocr.process_uploaded_subfolder(
                subfolder,
                details_forms_prefetch=prefetch,
                defer_post_ocr=defer_post_ocr,
            )
            if pencil_warnings:
                extraction_result = {**extraction_result, "warnings": pencil_warnings}
            details = ocr.get_extracted_details(subfolder)
            if details:
                extraction_result["details"] = details
        except Exception as e:
            logger.exception("manual-apply OCR failed subfolder=%s", subfolder)
            extraction_result = {"error": str(e), "manual_only": True}
        finally:
            ocr_d = get_ocr_output_dir(dealer_id)
            try:
                consolidate_peer_pre_ocr_folder_into_mobile(ocr_d, subfolder)
            except Exception:
                logger.exception("consolidate_peer_pre_ocr_folder_into_mobile failed subfolder=%s", subfolder)
            if pre_ocr_artifact_for_cleanup:
                try:
                    remove_if_empty_initial_artifact_dir(ocr_d, subfolder, pre_ocr_artifact_for_cleanup)
                except Exception:
                    logger.exception("remove_if_empty_initial_artifact_dir failed subfolder=%s", subfolder)

        final_sale = uploads_dir / subfolder
        saved_names: list[str] = []
        if final_sale.is_dir():
            for p in sorted(final_sale.iterdir()):
                if p.is_file():
                    saved_names.append(p.name)
            fo = final_sale / "for_OCR"
            if fo.is_dir():
                for p in sorted(fo.iterdir()):
                    if p.is_file():
                        saved_names.append(f"for_OCR/{p.name}")
        return {
            "saved_count": len(saved_names),
            "saved_files": saved_names,
            "saved_to": subfolder,
            "queued_items": [],
            "extraction": extraction_result,
        }

    async def apply_consolidated_manual_fallback(
        self,
        session_id: str,
        mobile: str,
        assignments_json: str,
        dealer_id: int = 100001,
        *,
        background_tasks: BackgroundTasks | None = None,
    ) -> dict:
        result = await asyncio.to_thread(
            self._apply_consolidated_manual_fallback_sync,
            session_id,
            mobile,
            assignments_json,
            dealer_id,
            defer_post_ocr=background_tasks is not None,
        )
        if background_tasks and result.get("saved_to"):
            ex = result.get("extraction") or {}
            if (ex.get("post_ocr") or {}).get("deferred"):
                udir = self.uploads_dir or get_uploads_dir(dealer_id)
                background_tasks.add_task(
                    run_deferred_post_ocr_for_sale,
                    udir,
                    get_ocr_output_dir(dealer_id),
                    result["saved_to"],
                    dealer_id,
                )
        return result

    async def save_and_queue_v2_consolidated(
        self,
        consolidated_pdf: list[UploadFile],
        dealer_id: int = 100001,
        *,
        form_mobile: str | None = None,
        background_tasks: BackgroundTasks | None = None,
    ) -> dict:
        """
        One multi-page PDF *or* multiple JPEG/PNG pages: run bulk pre-OCR pipeline
        (``run_pre_ocr_and_prepare``), then the same orient / normalize / pencil / Textract path as
        ``save_and_queue_v2``. Subfolder mobile comes from OCR when readable; optional ``form_mobile``
        (Add Sales **Customer Mobile**) is used when the Details mobile row is missing or garbled in Tesseract.

        Heavy work runs in a thread pool so the asyncio loop keeps servicing the socket while the
        client uploads the multipart body (avoids ``ECONNRESET`` on the Vite dev proxy).

        **Not** the bulk load queue: no ``bulk_loads`` row, no worker lease — pre-OCR runs in this request only.
        """
        proc_dir = get_add_sales_pre_ocr_work_dir(dealer_id)
        proc_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        for uf in consolidated_pdf:
            try:
                content = await read_upload_capped(uf, UPLOAD_MAX_CONSOLIDATED_PDF_BYTES)
                validate_magic_jpeg_png_or_pdf(content, label="Consolidated scan")
            except ValueError as e:
                return {"error": str(e)}
            stem = Path(f"{(uf.filename or 'consolidated').strip() or 'consolidated'}").stem
            safe_stem = stem[:80] if stem else "consolidated"
            kind = detect_image_or_pdf_kind(content)
            ext = {  "jpeg": ".jpg", "png": ".png" }.get(kind or "", ".pdf")
            dest = proc_dir / f"add_sales_{safe_stem}_{uuid4().hex[:12]}{ext}"
            dest.write_bytes(content)
            saved_paths.append(dest)

        dest_pdf = saved_paths[0]
        extra_image_paths = saved_paths[1:] if len(saved_paths) > 1 else None

        try:
            result = await asyncio.to_thread(
                self._process_consolidated_pdf_sync,
                dest_pdf,
                proc_dir,
                dealer_id,
                mobile_hint=form_mobile,
                extra_image_paths=extra_image_paths,
                defer_post_ocr=background_tasks is not None,
            )
        except Exception as e:
            return {"error": f"Consolidated processing failed: {e}"}
        if background_tasks and result.get("saved_to"):
            ex = result.get("extraction") or {}
            if (ex.get("post_ocr") or {}).get("deferred"):
                udir = self.uploads_dir or get_uploads_dir(dealer_id)
                background_tasks.add_task(
                    run_deferred_post_ocr_for_sale,
                    udir,
                    get_ocr_output_dir(dealer_id),
                    result["saved_to"],
                    dealer_id,
                )
        return result

    async def save_and_queue_v2_consolidated_stream(
        self,
        consolidated_pdf: list[UploadFile],
        dealer_id: int = 100001,
        *,
        form_mobile: str | None = None,
        background_tasks: BackgroundTasks | None = None,
    ):
        """
        Same as ``save_and_queue_v2_consolidated`` but yields **SSE** lines (``text/event-stream``):
        ``partial`` when Aadhaar or Details merge is written, ``complete`` with the final JSON payload.
        """
        proc_dir = get_add_sales_pre_ocr_work_dir(dealer_id)
        proc_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        for uf in consolidated_pdf:
            try:
                content = await read_upload_capped(uf, UPLOAD_MAX_CONSOLIDATED_PDF_BYTES)
                validate_magic_jpeg_png_or_pdf(content, label="Consolidated scan")
            except ValueError as e:
                yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"
                return
            stem = Path(f"{(uf.filename or 'consolidated').strip() or 'consolidated'}").stem
            safe_stem = stem[:80] if stem else "consolidated"
            kind = detect_image_or_pdf_kind(content)
            ext = {  "jpeg": ".jpg", "png": ".png" }.get(kind or "", ".pdf")
            dest = proc_dir / f"add_sales_{safe_stem}_{uuid4().hex[:12]}{ext}"
            dest.write_bytes(content)
            saved_paths.append(dest)

        dest_pdf = saved_paths[0]
        extra_image_paths = saved_paths[1:] if len(saved_paths) > 1 else None
        defer_post_ocr = background_tasks is not None

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def worker() -> None:
            try:

                def bridge(event_name: str, payload: dict) -> None:
                    loop.call_soon_threadsafe(queue.put_nowait, ("msg", event_name, payload))

                result = self._process_consolidated_pdf_sync(
                    dest_pdf,
                    proc_dir,
                    dealer_id,
                    mobile_hint=form_mobile,
                    on_extraction_event=bridge,
                    extra_image_paths=extra_image_paths,
                    defer_post_ocr=defer_post_ocr,
                )
                loop.call_soon_threadsafe(queue.put_nowait, ("done", result))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item[0] == "msg":
                _, event_name, payload = item
                body: dict = {"event": event_name, **payload}
                yield f"data: {json.dumps(body)}\n\n"
            elif item[0] == "done":
                result = item[1]
                if background_tasks and result.get("saved_to"):
                    ex = result.get("extraction") or {}
                    if (ex.get("post_ocr") or {}).get("deferred"):
                        udir = self.uploads_dir or get_uploads_dir(dealer_id)
                        background_tasks.add_task(
                            run_deferred_post_ocr_for_sale,
                            udir,
                            get_ocr_output_dir(dealer_id),
                            result["saved_to"],
                            dealer_id,
                        )
                yield f"data: {json.dumps({'event': 'complete', 'result': result})}\n\n"
                return
            elif item[0] == "error":
                yield f"data: {json.dumps({'event': 'error', 'message': item[1]})}\n\n"
                return
