"""Process AI reader queue with AWS Textract (forms mode). Details sheet only."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name under ocr_output (one segment, no path separators)."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _safe_output_basename(subfolder: str, filename: str) -> str:
    """Build a safe filename for output: subfolder_filename.txt (no path chars)."""
    safe_sub = _safe_subfolder_name(subfolder)
    safe_name = Path(filename).stem
    safe_name = re.sub(r"[^\w\-.]", "_", safe_name)
    return f"{safe_sub}_{safe_name}.txt"


def _ocr_subfolder_path(output_dir: Path, subfolder: str) -> Path:
    """Path to per-customer subfolder under ocr_output: ocr_output/mobile_ddmmyyyy/."""
    return output_dir / _safe_subfolder_name(subfolder)


def _json_output_path(output_dir: Path, subfolder: str, filename: str) -> Path:
    """Path for structured JSON output under subfolder: ocr_output/mobile_ddmmyy/Details.json."""
    stem = Path(filename).stem
    subfolder_name = _safe_subfolder_name(subfolder)
    return output_dir / subfolder_name / f"{stem}.json"


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

# Map Textract form keys to insurance/nominee fields (from Details sheet).
_INSURANCE_KEY_ALIASES = {
    "profession": ["profession", "profession:", "occupation", "customer profession"],
    "nominee_name": ["nominee name", "nominee name:", "nominee"],
    "nominee_age": ["nominee age", "nominee age:", "age of nominee"],
    "nominee_relationship": ["nominee relationship", "nominee relationship:", "relationship", "relation"],
}


def _normalize_key_for_match(key: str) -> str:
    """Lowercase and collapse spaces for key matching."""
    return re.sub(r"\s+", " ", (key or "").lower().strip())


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


def _map_key_value_pairs_to_insurance(pairs: list[dict]) -> dict[str, str]:
    """Map key_value_pairs from Textract to insurance/nominee fields."""
    out: dict[str, str] = {}
    key_lower_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k:
            continue
        key_norm = _normalize_key_for_match(k)
        key_lower_to_value[key_norm] = v
        # Also store with colon stripped for "Nominee Name:" style
        if ":" in key_norm:
            key_lower_to_value[key_norm.replace(":", "").strip()] = v

    for field, aliases in _INSURANCE_KEY_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            anorm = _normalize_key_for_match(alias)
            if anorm in key_lower_to_value:
                out[field] = key_lower_to_value[anorm]
                break
            # Match if key contains alias (e.g. "nominee name" in "Nominee Name")
            for k, v in key_lower_to_value.items():
                if anorm in k or k in anorm:
                    out[field] = v
                    break
            if field in out:
                break
    return out


def _parse_insurance_from_full_text(full_text: str) -> dict[str, str]:
    """Try to extract profession, nominee name, age, relationship from full text (fallback when not in key-value pairs)."""
    out: dict[str, str] = {}
    if not full_text or not isinstance(full_text, str):
        return out
    text = full_text.strip()
    # Patterns: "Label" or "Label:" followed by value on same or next line
    patterns = [
        ("profession", "profession"),
        ("nominee name", "nominee_name"),
        ("nominee age", "nominee_age"),
        ("nominee relationship", "nominee_relationship"),
        ("occupation", "profession"),
        ("relation", "nominee_relationship"),
    ]
    for label, key in patterns:
        if key in out:
            continue
        pat = re.compile(
            rf"{re.escape(label)}\s*:?\s*\n?\s*([^\n]+)",
            re.IGNORECASE,
        )
        m = pat.search(text)
        if m:
            val = m.group(1).strip()
            if val and len(val) < 200:
                out[key] = val
    return out


class OcrService:
    """Process AI reader queue with Textract (forms mode); write results to flat files. Details sheet only."""

    def __init__(
        self,
        uploads_dir: Path | None = None,
        ocr_output_dir: Path | None = None,
    ):
        from app.config import OCR_OUTPUT_DIR, UPLOADS_DIR

        self.uploads_dir = Path(uploads_dir or UPLOADS_DIR).resolve()
        self.ocr_output_dir = Path(ocr_output_dir or OCR_OUTPUT_DIR).resolve()

    def _ensure_ocr_output_dir(self) -> None:
        self.ocr_output_dir.mkdir(parents=True, exist_ok=True)

    def get_output_path(self, subfolder: str, filename: str) -> Path:
        """Path where extracted text is written: ocr_output/mobile_ddmmyy/filename_stem.txt."""
        self._ensure_ocr_output_dir()
        stem = Path(filename).stem
        safe_stem = re.sub(r"[^\w\-.]", "_", stem)
        # Always use a subfolder (mobile_ddmmyy); never write at ocr_output root
        subfolder_name = _safe_subfolder_name(subfolder)
        subfolder_path = self.ocr_output_dir / subfolder_name
        subfolder_path.mkdir(parents=True, exist_ok=True)
        return subfolder_path / f"{safe_stem}.txt"

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

    def process_uploaded_subfolder(self, subfolder: str) -> dict:
        """
        Run extraction directly on uploaded files (no queue).
        Processes Details.jpg first (vehicle + insurance), then Aadhar.jpg (customer).
        Returns summary of what was processed.
        """
        subdir = self.uploads_dir / subfolder
        if not subdir.exists() or not subdir.is_dir():
            return {"error": f"Subfolder not found: {subfolder}", "processed": []}

        processed: list[str] = []
        errors: list[str] = []

        # 1. Details.jpg first (creates vehicle + insurance JSON)
        details_path = subdir / "Details.jpg"
        if details_path.exists():
            try:
                self._process_details_sheet(None, subfolder, "Details.jpg", details_path)
                processed.append("Details.jpg")
            except Exception as e:
                errors.append(f"Details.jpg: {e}")

        # 2. Aadhar.jpg second (adds customer to JSON)
        aadhar_path = subdir / "Aadhar.jpg"
        if aadhar_path.exists():
            try:
                self._process_aadhar(None, subfolder, "Aadhar.jpg", aadhar_path)
                processed.append("Aadhar.jpg")
            except Exception as e:
                errors.append(f"Aadhar.jpg: {e}")

        result: dict = {"processed": processed}
        if errors:
            result["errors"] = errors
        return result

    def _process_details_sheet(self, qid: int | None, subfolder: str, filename: str, input_path: Path) -> dict:
        """Run Textract (forms) on Details sheet; write text + JSON; optionally update queue."""
        if qid is not None:
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
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8")

            vehicle = _map_key_value_pairs_to_vehicle(key_value_pairs)
            insurance = _map_key_value_pairs_to_insurance(key_value_pairs)
            if result.get("full_text"):
                from_full = _parse_insurance_from_full_text(result["full_text"])
                for k, v in from_full.items():
                    if v and not insurance.get(k):
                        insurance[k] = v
            json_path = _json_output_path(self.ocr_output_dir, subfolder, filename)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            customer = {}
            existing_insurance: dict = {}
            if json_path.exists():
                try:
                    existing = json.loads(json_path.read_text(encoding="utf-8"))
                    customer = existing.get("customer") or {}
                    if not isinstance(customer, dict):
                        customer = {}
                    existing_insurance = existing.get("insurance") or {}
                    if not isinstance(existing_insurance, dict):
                        existing_insurance = {}
                except Exception:
                    pass
            # Preserve existing insurance fields (e.g. profession) and overlay nominee from Details sheet
            insurance_merged = {**existing_insurance, **{k: v for k, v in insurance.items() if v}}
            details_json = {"vehicle": vehicle, "customer": customer, "insurance": insurance_merged}
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(details_json, indent=2), encoding="utf-8")

            if qid is not None:
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
            if qid is not None:
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
            raise

    def _process_aadhar(self, qid: int | None, subfolder: str, filename: str, input_path: Path) -> dict:
        """Run Aadhar extraction: try QR decode first, then fall back to OpenAI Vision; merge customer into subfolder JSON."""
        if qid is not None:
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
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            # Write a file in ocr_output/subfolder listing the 15 extracted Aadhar fields (with display labels)
            output_path = self.get_output_path(subfolder, filename)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["Aadhar scan – 15 extracted fields", ""]
            for key, label in AADHAR_15_FIELDS:
                value = customer.get(key)
                if value and str(value).strip():
                    lines.append(f"{label}: {value.strip()}")
            if customer.get("address") and str(customer["address"]).strip():
                lines.append(f"Address (constructed): {customer['address'].strip()}")
            summary = "\n".join(f"{k}: {v}" for k, v in customer.items() if v)
            output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            if qid is not None:
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
            if qid is not None:
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
            raise

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
                if not isinstance(data.get("insurance"), dict):
                    data["insurance"] = {}
            except Exception:
                pass
        if "insurance" not in data:
            data["insurance"] = {}

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
                json_path.parent.mkdir(parents=True, exist_ok=True)
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
