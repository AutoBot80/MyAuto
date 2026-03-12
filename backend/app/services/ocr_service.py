"""Tesseract-based OCR: process queue items, write extracted text to flat files."""

import re
from datetime import datetime
from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance

from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Improve text extraction on images with logos/graphics: grayscale and boost contrast."""
    if img.mode != "L":
        img = img.convert("L")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.5)
    return img


def _safe_output_basename(subfolder: str, filename: str) -> str:
    """Build a safe filename for OCR output: subfolder_filename.txt (no path chars)."""
    safe_sub = re.sub(r"[^\w\-]", "_", subfolder)
    safe_name = Path(filename).stem
    safe_name = re.sub(r"[^\w\-.]", "_", safe_name)
    return f"{safe_sub}_{safe_name}.txt"


class OcrService:
    """Process AI reader queue with Tesseract; write results to flat files."""

    def __init__(
        self,
        uploads_dir: Path | None = None,
        ocr_output_dir: Path | None = None,
    ):
        from app.config import OCR_OUTPUT_DIR, UPLOADS_DIR

        self.uploads_dir = uploads_dir or UPLOADS_DIR
        self.ocr_output_dir = ocr_output_dir or OCR_OUTPUT_DIR

    def _ensure_ocr_output_dir(self) -> None:
        self.ocr_output_dir.mkdir(parents=True, exist_ok=True)

    def get_output_path(self, subfolder: str, filename: str) -> Path:
        """Path where OCR text for this queue item is or will be written."""
        self._ensure_ocr_output_dir()
        return self.ocr_output_dir / _safe_output_basename(subfolder, filename)

    def read_extraction_file(self, subfolder: str, filename: str) -> str | None:
        """Read extracted text from the flat file if it exists."""
        path = self.get_output_path(subfolder, filename)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def process_next(self) -> dict | None:
        """
        Process the oldest queued item: run Tesseract, write to flat file, update status.
        Returns result dict or None if queue is empty.
        """
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            row = AiReaderQueueRepository.get_oldest_queued(conn)
            if not row:
                return None

            qid = row["id"]
            subfolder = row["subfolder"]
            filename = row["filename"]

            AiReaderQueueRepository.update_status(conn, qid, "processing")
            conn.commit()

        input_path = self.uploads_dir / subfolder / filename
        if not input_path.exists():
            with get_connection() as conn:
                AiReaderQueueRepository.update_status(conn, qid, "failed")
                conn.commit()
            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "failed",
                "error": "File not found",
                "extracted_text": None,
                "output_path": None,
                "document_type": None,
                "classification_confidence": None,
            }

        try:
            from app.config import OCR_LANG, OCR_PSM, OCR_PREPROCESS, USE_AI_CLASSIFIER
            from app.services.document_classifier import get_document_classifier

            img = Image.open(input_path).copy()
            if img.mode == "RGBA":
                img = img.convert("RGB")

            # Step 1: Classify image (AI model)
            classifier = get_document_classifier(use_ai=USE_AI_CLASSIFIER)
            document_type, classification_confidence = classifier.classify(img)
            with get_connection() as conn:
                AiReaderQueueRepository.update_classification(
                    conn, qid, document_type, classification_confidence
                )
                conn.commit()

            # Step 2: Tesseract OCR
            img_for_ocr = _preprocess_for_ocr(img.copy()) if OCR_PREPROCESS else img
            config = f"--psm {OCR_PSM}"
            text = pytesseract.image_to_string(
                img_for_ocr, lang=OCR_LANG, config=config
            )
            if text is None:
                text = ""
            text = text.strip()

            self._ensure_ocr_output_dir()
            output_path = self.get_output_path(subfolder, filename)
            header = f"Document type: {document_type} (confidence: {classification_confidence:.2f})\n\n"
            output_path.write_text(header + text, encoding="utf-8")

            with get_connection() as conn:
                AiReaderQueueRepository.update_status(conn, qid, "done")
                conn.commit()

            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "done",
                "error": None,
                "extracted_text": text,
                "output_path": str(output_path),
                "document_type": document_type,
                "classification_confidence": classification_confidence,
            }
        except Exception as e:
            with get_connection() as conn:
                AiReaderQueueRepository.update_status(conn, qid, "failed")
                conn.commit()
            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "failed",
                "error": str(e),
                "extracted_text": None,
                "output_path": None,
                "document_type": None,
                "classification_confidence": None,
            }

    def list_extractions(self, limit: int = 200) -> list[dict]:
        """List queue items (oldest first for display) with extracted text from flat files."""
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            rows = AiReaderQueueRepository.list_all(conn, limit=limit)

        # list_all returns newest first; reverse so oldest is first for "reader" view
        rows = list(reversed(rows))

        result = []
        for row in rows:
            r = dict(row)
            for k in ("created_at", "updated_at"):
                if k in r and isinstance(r[k], datetime):
                    r[k] = r[k].isoformat()
            text = self.read_extraction_file(r["subfolder"], r["filename"])
            r["extracted_text"] = text
            r["output_path"] = str(self.get_output_path(r["subfolder"], r["filename"]))
            result.append(r)
        return result
