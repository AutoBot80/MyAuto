"""Process AI reader queue with AWS Textract (forms mode). Details sheet only."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository


def _safe_output_basename(subfolder: str, filename: str) -> str:
    """Build a safe filename for output: subfolder_filename.txt (no path chars)."""
    safe_sub = re.sub(r"[^\w\-]", "_", subfolder)
    safe_name = Path(filename).stem
    safe_name = re.sub(r"[^\w\-.]", "_", safe_name)
    return f"{safe_sub}_{safe_name}.txt"


def _json_output_path(output_dir: Path, subfolder: str, filename: str) -> Path:
    """Path for structured JSON output (same base as .txt but .json)."""
    base = _safe_output_basename(subfolder, filename)
    return output_dir / (Path(base).stem + ".json")


# Only process queue items whose filename contains this (Details sheet).
DETAILS_FILENAME_CONTAINS = "details"

# Map Textract form keys (normalized) to our vehicle detail fields.
_VEHICLE_KEY_ALIASES = {
    "frame_no": ["frame no", "frame no.", "frame number", "chassis", "chassis no", "chassis no."],
    "engine_no": ["engine no", "engine no.", "engine number", "engine"],
    "model_colour": ["model & colour", "model and colour", "model/colour", "model", "colour", "color"],
    "key_no": ["key no", "key no.", "key number", "key"],
    "battery_no": ["battery no", "battery no.", "battery number", "battery"],
}


def _map_key_value_pairs_to_vehicle(pairs: list[dict]) -> dict[str, str]:
    """Map key_value_pairs from Textract to structured vehicle fields (frame_no, engine_no, model_colour, key_no, battery_no)."""
    out: dict[str, str] = {}
    key_lower_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k:
            continue
        key_lower_to_value[k.lower()] = v

    for field, aliases in _VEHICLE_KEY_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            if alias in key_lower_to_value:
                out[field] = key_lower_to_value[alias]
                break

    # Combine Model and Colour into model_colour if we have them separately
    model_val = key_lower_to_value.get("model", "").strip() or next(
        (v for k, v in key_lower_to_value.items() if "model" in k and "colour" not in k and "color" not in k), ""
    )
    colour_val = key_lower_to_value.get("colour", "").strip() or key_lower_to_value.get("color", "").strip()
    if model_val or colour_val:
        combined = ", ".join(filter(None, [model_val, colour_val]))
        if combined:
            out["model_colour"] = combined

    return out


class OcrService:
    """Process AI reader queue with Textract (forms mode); write results to flat files. Details sheet only."""

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
        """Path where extracted text for this queue item is or will be written."""
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
        Process the oldest queued Details sheet: run Textract (forms), write key-value output, update status.
        Only picks items where filename contains "details". Returns result dict or None if none queued.
        """
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            row = AiReaderQueueRepository.get_oldest_queued(conn, filename_contains=DETAILS_FILENAME_CONTAINS)
            if not row:
                return None

            qid = row["id"]
            subfolder = row["subfolder"]
            filename = row["filename"]

            AiReaderQueueRepository.update_status(conn, qid, "processing")
            AiReaderQueueRepository.update_classification(conn, qid, "Details sheet", 1.0)
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
            from app.services.textract_service import extract_forms_from_bytes

            document_bytes = input_path.read_bytes()
            result = extract_forms_from_bytes(document_bytes)
            if result.get("error"):
                raise RuntimeError(result["error"])

            key_value_pairs = result.get("key_value_pairs") or []
            lines = []
            lines.append("Document: Details sheet (Textract Forms)\n")
            for kv in key_value_pairs:
                lines.append(f"{kv.get('key', '')}: {kv.get('value', '')}")
            if result.get("full_text"):
                lines.append("\n--- Full text ---\n")
                lines.append(result["full_text"])
            text = "\n".join(lines)

            self._ensure_ocr_output_dir()
            output_path = self.get_output_path(subfolder, filename)
            output_path.write_text(text, encoding="utf-8")

            # Write structured JSON for client (vehicle details from form keys); preserve existing customer if any
            vehicle = _map_key_value_pairs_to_vehicle(key_value_pairs)
            json_path = _json_output_path(self.ocr_output_dir, subfolder, filename)
            customer = {}
            if json_path.exists():
                try:
                    existing = json.loads(json_path.read_text(encoding="utf-8"))
                    customer = existing.get("customer") or {}
                    if not isinstance(customer, dict):
                        customer = {}
                except Exception:
                    pass
            details_json = {"vehicle": vehicle, "customer": customer}
            json_path.write_text(json.dumps(details_json, indent=2), encoding="utf-8")

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
                "document_type": "Details sheet",
                "classification_confidence": 1.0,
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

    def get_extracted_details(self, subfolder: str) -> dict | None:
        """
        Return structured extracted details (vehicle, customer) for a subfolder.
        Loads from JSON if present. If customer is missing/empty and Aadhar.jpg exists in the subfolder,
        runs Aadhar reading (OpenAI Vision), merges customer, persists JSON, and returns.
        """
        self._ensure_ocr_output_dir()
        json_path = _json_output_path(self.ocr_output_dir, subfolder, "Details.jpg")
        data = {"vehicle": {}, "customer": {}}
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(data.get("vehicle"), dict):
                    data["vehicle"] = {}
                if not isinstance(data.get("customer"), dict):
                    data["customer"] = {}
            except Exception:
                pass

        customer = data.get("customer") or {}
        has_customer = any(customer.get(k) for k in ("name", "address", "city", "state", "pin"))
        aadhar_path = self.uploads_dir / subfolder / "Aadhar.jpg"
        if not has_customer and aadhar_path.exists():
            from app.services.vision_service import extract_aadhar_customer_fields

            cust = extract_aadhar_customer_fields(image_path=aadhar_path)
            cust.pop("error", None)
            data["customer"] = {k: (cust.get(k) or "") for k in ("name", "address", "city", "state", "pin")}
            try:
                json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass
        return data

    def list_extractions(self, limit: int = 200) -> list[dict]:
        """List queue items (oldest first) with extracted text from flat files."""
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            rows = AiReaderQueueRepository.list_all(conn, limit=limit)

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
