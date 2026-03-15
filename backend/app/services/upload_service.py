from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.config import UPLOADS_DIR

# ai_reader_queue disabled; extraction runs directly after upload (Option 1)


class UploadService:
    """Business logic for scan uploads and queueing. Stateless, testable."""

    def __init__(self, uploads_dir: Path | None = None):
        self.uploads_dir = uploads_dir or UPLOADS_DIR

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
        digits = "".join(c for c in mobile if c.isdigit())
        ddmmyy = datetime.now().strftime("%d%m%y")
        return f"{digits}_{ddmmyy}"

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
        self, aadhar_last4: str, files: list[UploadFile]
    ) -> dict:
        ok, err = self.validate_aadhar_last4(aadhar_last4)
        if not ok:
            return {"error": err}

        subdir_name = self.get_subdir_name(aadhar_last4)
        subdir = self.uploads_dir / subdir_name
        subdir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []

        for f in files:
            filename = Path(f.filename or "scan").name
            target = self._unique_path(subdir, filename)
            content = await f.read()
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
    ) -> dict:
        """Subfolder = mobile_ddmmyy; save as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg."""
        ok, err = self.validate_mobile(mobile)
        if not ok:
            return {"error": err}

        subdir_name = self.get_subdir_name_mobile(mobile)
        subdir = self.uploads_dir / subdir_name
        subdir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []

        file_defs: list[tuple[UploadFile, str]] = [
            (aadhar_scan, "Aadhar.jpg"),
            (aadhar_back, "Aadhar_back.jpg"),
            (sales_detail, "Details.jpg"),
        ]
        if insurance_sheet and insurance_sheet.filename:
            file_defs.append((insurance_sheet, "Insurance.jpg"))
        if financing_doc and financing_doc.filename:
            file_defs.append((financing_doc, "Financing.jpg"))
        for role, save_name in file_defs:
            content = await role.read()
            target = subdir / save_name
            target.write_bytes(content)
            saved.append(save_name)

        # Run extraction directly after upload (Option 1: no queue)
        extraction_result: dict = {}
        try:
            from app.services.ocr_service import OcrService

            ocr = OcrService()
            extraction_result = ocr.process_uploaded_subfolder(subdir_name)
        except Exception as e:
            extraction_result = {"error": str(e), "processed": []}

        return {
            "saved_count": len(saved),
            "saved_files": saved,
            "saved_to": subdir_name,
            "queued_items": [],
            "extraction": extraction_result,
        }
