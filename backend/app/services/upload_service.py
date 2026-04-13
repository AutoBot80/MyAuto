import asyncio
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

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
from app.services.upload_file_validation import (
    read_upload_capped,
    sanitize_legacy_upload_filename,
    validate_magic_jpeg_or_png,
    validate_magic_jpeg_png_or_pdf,
    validate_magic_jpeg_png_pdf_legacy,
)

# ai_reader_queue disabled; extraction runs directly after upload (Option 1)


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
    ) -> dict:
        """Subfolder = mobile_ddmmyy; save as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg."""
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

        err_msg = await save_image_field(aadhar_scan, "Aadhar.jpg", "Aadhar front")
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
                normalize_aadhar_upload_files,
                orient_common_sale_jpegs,
                try_write_pencil_mark_from_details_jpeg_file,
            )

            sale_path = uploads_dir / subdir_name
            orient_common_sale_jpegs(sale_path)
            normalize_aadhar_upload_files(sale_path)
            try_write_pencil_mark_from_details_jpeg_file(sale_path, sale_path / "Details.jpg")

            from app.services.sales_ocr_service import OcrService

            ocr = OcrService(
                uploads_dir=uploads_dir,
                ocr_output_dir=get_ocr_output_dir(dealer_id),
            )
            extraction_result = ocr.process_uploaded_subfolder(subdir_name)
            # Include full extracted details so client can populate immediately (no polling)
            details = ocr.get_extracted_details(subdir_name)
            if details:
                extraction_result["details"] = details
        except Exception as e:
            extraction_result = {"error": str(e), "processed": []}

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

    def _process_consolidated_pdf_sync(self, dest_pdf: Path, proc_dir: Path, dealer_id: int) -> dict:
        """CPU-bound pre-OCR + Textract; must run in a worker thread (see ``save_and_queue_v2_consolidated``)."""
        # Lazy import: ``pre_ocr_service`` references ``UploadService`` to avoid import cycles.
        from app.services.pre_ocr_service import run_pre_ocr_and_prepare

        try:
            bundles, _stem, _mobile_ocr, _ocr_path, missing = run_pre_ocr_and_prepare(
                dest_pdf,
                processing_dir=proc_dir,
                dealer_id=dealer_id,
            )
        except Exception as e:
            return {"error": f"Pre-OCR failed: {e}"}

        if missing:
            return {"error": self._pre_ocr_rejection_message(missing)}
        if not bundles:
            return {"error": "Pre-OCR did not produce a sale folder."}
        if len(bundles) > 1:
            return {
                "error": "Multiple customers detected in this PDF. Use Bulk Upload for multi-customer consolidated scans.",
            }

        _sale_dir, subfolder_name, _mobile_str = bundles[0]
        uploads_dir = self.uploads_dir or get_uploads_dir(dealer_id)
        subdir_name = subfolder_name

        extraction_result: dict = {}
        try:
            from app.services.pre_ocr_service import (
                normalize_aadhar_upload_files,
                orient_common_sale_jpegs,
                try_write_pencil_mark_from_details_jpeg_file,
            )

            sale_path = uploads_dir / subdir_name
            orient_common_sale_jpegs(sale_path)
            normalize_aadhar_upload_files(sale_path)
            try_write_pencil_mark_from_details_jpeg_file(sale_path, sale_path / "Details.jpg")

            from app.services.sales_ocr_service import OcrService

            ocr = OcrService(
                uploads_dir=uploads_dir,
                ocr_output_dir=get_ocr_output_dir(dealer_id),
            )
            extraction_result = ocr.process_uploaded_subfolder(subdir_name)
            details = ocr.get_extracted_details(subdir_name)
            if details:
                extraction_result["details"] = details
        except Exception as e:
            extraction_result = {"error": str(e), "processed": []}

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

    async def save_and_queue_v2_consolidated(
        self,
        consolidated_pdf: UploadFile,
        dealer_id: int = 100001,
    ) -> dict:
        """
        Single multi-page PDF (Aadhaar + sales detail in one file): run bulk pre-OCR pipeline
        (``run_pre_ocr_and_prepare``), then the same orient / normalize / pencil / Textract path as
        ``save_and_queue_v2``. Mobile and subfolder come from pre-OCR text, not the form.

        Heavy work runs in a thread pool so the asyncio loop keeps servicing the socket while the
        client uploads the multipart body (avoids ``ECONNRESET`` on the Vite dev proxy).

        **Not** the bulk load queue: no ``bulk_loads`` row, no worker lease — pre-OCR runs in this request only.
        """
        try:
            content = await read_upload_capped(consolidated_pdf, UPLOAD_MAX_CONSOLIDATED_PDF_BYTES)
            validate_magic_jpeg_png_or_pdf(content, label="Consolidated scan")
        except ValueError as e:
            return {"error": str(e)}

        # Add Sales only: same ``run_pre_ocr_and_prepare`` as bulk, but no ``bulk_loads`` / SQS — not Bulk Upload/Processing.
        proc_dir = get_add_sales_pre_ocr_work_dir(dealer_id)
        proc_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(f"{(consolidated_pdf.filename or 'consolidated').strip() or 'consolidated'}").stem
        safe_stem = stem[:80] if stem else "consolidated"
        dest_pdf = proc_dir / f"add_sales_{safe_stem}_{uuid4().hex[:12]}.pdf"
        dest_pdf.write_bytes(content)

        try:
            return await asyncio.to_thread(self._process_consolidated_pdf_sync, dest_pdf, proc_dir, dealer_id)
        except Exception as e:
            return {"error": f"Consolidated processing failed: {e}"}
