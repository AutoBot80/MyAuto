from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.config import (
    UPLOAD_MAX_IMAGE_BYTES,
    UPLOAD_MAX_LEGACY_FILE_BYTES,
    UPLOAD_MAX_PDF_BYTES,
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
