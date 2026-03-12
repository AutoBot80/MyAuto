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


# Filename patterns for queue processing.
DETAILS_FILENAME_CONTAINS = "details"
AADHAR_FILENAME_CONTAINS = "aadhar"

# 15 Aadhar fields (display order and labels for ocr_output file).
AADHAR_15_FIELDS: list[tuple[str, str]] = [
    ("aadhar_id", "Aadhar ID"),
    ("name", "Name"),
    ("gender", "Gender"),
    ("year_of_birth", "Year of birth"),
    ("date_of_birth", "Date of birth"),
    ("care_of", "Care of"),
    ("house", "House"),
    ("street", "Street"),
    ("location", "Location"),
    ("city", "City"),
    ("post_office", "Post Office"),
    ("district", "District"),
    ("sub_district", "Sub District"),
    ("state", "State"),
    ("pin_code", "Pin Code"),
]

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
        Process the oldest queued item: Details sheet (Textract) or Aadhar (Vision).
        Picks any queued item; branches on filename (details vs aadhar). Returns result dict or None if none queued.
        """
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            row = AiReaderQueueRepository.get_oldest_queued(conn)  # any queued item
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

        fn_lower = filename.lower()
        if DETAILS_FILENAME_CONTAINS in fn_lower:
            return self._process_details_sheet(qid, subfolder, filename, input_path)
        if AADHAR_FILENAME_CONTAINS in fn_lower:
            return self._process_aadhar(qid, subfolder, filename, input_path)
        # Unknown type: mark done so queue advances
        with get_connection() as conn:
            AiReaderQueueRepository.update_classification(conn, qid, "Other", 0.0)
            AiReaderQueueRepository.update_status(conn, qid, "done")
            conn.commit()
        return {
            "id": qid,
            "subfolder": subfolder,
            "filename": filename,
            "status": "done",
            "error": None,
            "extracted_text": None,
            "output_path": None,
            "document_type": "Other",
            "classification_confidence": 0.0,
        }

    def _process_details_sheet(self, qid: int, subfolder: str, filename: str, input_path: Path) -> dict:
        """Run Textract (forms) on Details sheet; write text + JSON; update queue."""
        with get_connection() as conn:
            AiReaderQueueRepository.update_classification(conn, qid, "Details sheet", 1.0)
            conn.commit()
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

    def _process_aadhar(self, qid: int, subfolder: str, filename: str, input_path: Path) -> dict:
        """Run Aadhar extraction: try QR decode first, then fall back to OpenAI Vision; merge customer into subfolder JSON."""
        with get_connection() as conn:
            AiReaderQueueRepository.update_classification(conn, qid, "Aadhar", 1.0)
            conn.commit()
        try:
            customer: dict[str, str] = {}
            raw_bytes = input_path.read_bytes()
            try:
                from app.services.qr_decode_service import decode_qr_from_image_bytes

                qr_result = decode_qr_from_image_bytes(raw_bytes)
                if qr_result.get("decoded") and qr_result["decoded"][0].get("fields"):
                    fields = qr_result["decoded"][0]["fields"]
                    for k in (
                        "aadhar_id", "name", "gender", "year_of_birth", "date_of_birth",
                        "care_of", "house", "street", "location", "city", "post_office",
                        "district", "sub_district", "state", "pin_code",
                    ):
                        v = fields.get(k)
                        if v and str(v).strip():
                            customer[k] = str(v).strip()
                    parts = [
                        customer.get("care_of"), customer.get("house"), customer.get("street"),
                        customer.get("location"), customer.get("state"), customer.get("pin_code"),
                    ]
                    address = ", ".join(p for p in parts if p)
                    if address:
                        customer["address"] = address
                else:
                    raise ValueError("No QR fields")
            except Exception:
                from app.services.vision_service import extract_aadhar_customer_fields

                cust = extract_aadhar_customer_fields(image_path=input_path)
                cust.pop("error", None)
                customer = {k: (cust.get(k) or "") for k in ("name", "address", "city", "state", "pin")}
                if customer.get("pin"):
                    customer["pin_code"] = customer["pin"]

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
            data["customer"] = customer
            json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            # Write a file in ocr_output listing the 15 extracted Aadhar fields (with display labels)
            output_path = self.get_output_path(subfolder, filename)
            lines = ["Aadhar scan – 15 extracted fields", ""]
            for key, label in AADHAR_15_FIELDS:
                value = customer.get(key)
                if value and str(value).strip():
                    lines.append(f"{label}: {value.strip()}")
            if customer.get("address") and str(customer["address"]).strip():
                lines.append(f"Address (constructed): {customer['address'].strip()}")
            summary = "\n".join(f"{k}: {v}" for k, v in customer.items() if v)
            output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with get_connection() as conn:
                AiReaderQueueRepository.update_status(conn, qid, "done")
                conn.commit()

            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "done",
                "error": None,
                "extracted_text": summary,
                "output_path": str(output_path),
                "document_type": "Aadhar",
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
        has_customer = any(customer.get(k) for k in ("name", "address", "aadhar_id", "city", "state", "pin", "pin_code"))
        aadhar_path = self.uploads_dir / subfolder / "Aadhar.jpg"
        if not has_customer and aadhar_path.exists():
            raw_bytes = aadhar_path.read_bytes()
            try:
                from app.services.qr_decode_service import decode_qr_from_image_bytes

                qr_result = decode_qr_from_image_bytes(raw_bytes)
                if qr_result.get("decoded") and qr_result["decoded"][0].get("fields"):
                    fields = qr_result["decoded"][0]["fields"]
                    for k in (
                        "aadhar_id", "name", "gender", "year_of_birth", "date_of_birth",
                        "care_of", "house", "street", "location", "city", "post_office",
                        "district", "sub_district", "state", "pin_code",
                    ):
                        v = fields.get(k)
                        if v and str(v).strip():
                            customer[k] = str(v).strip()
                    parts = [
                        customer.get("care_of"), customer.get("house"), customer.get("street"),
                        customer.get("location"), customer.get("state"), customer.get("pin_code"),
                    ]
                    address = ", ".join(p for p in parts if p)
                    if address:
                        customer["address"] = address
                    data["customer"] = customer
                else:
                    raise ValueError("No QR fields")
            except Exception:
                from app.services.vision_service import extract_aadhar_customer_fields

                cust = extract_aadhar_customer_fields(image_path=aadhar_path)
                cust.pop("error", None)
                data["customer"] = {k: (cust.get(k) or "") for k in ("name", "address", "city", "state", "pin")}
                if data["customer"].get("pin"):
                    data["customer"]["pin_code"] = data["customer"]["pin"]
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
