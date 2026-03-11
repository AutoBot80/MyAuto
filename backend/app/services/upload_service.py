from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.config import UPLOADS_DIR
from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository


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
        queued: list[dict] = []

        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            for f in files:
                filename = Path(f.filename or "scan").name
                target = self._unique_path(subdir, filename)
                content = await f.read()
                target.write_bytes(content)
                saved.append(target.name)
                row = AiReaderQueueRepository.insert(
                    conn, subdir_name, target.name, status="queued"
                )
                queued.append(row)
            conn.commit()

        return {
            "saved_count": len(saved),
            "saved_files": saved,
            "saved_to": str(subdir),
            "queued_items": queued,
        }
