"""Compute missing OCR fields and record admin diagnostics without blocking OCR."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.process_failure_log_service import digits_only_mobile
from app.services.sales_ocr_service import AADHAR_15_FIELDS

logger = logging.getLogger(__name__)

_VEHICLE_FIELDS: list[tuple[str, str]] = [
    ("frame_no", "Frame no"),
    ("engine_no", "Engine no"),
    ("model_colour", "Model colour"),
    ("key_no", "Key no"),
    ("battery_no", "Battery no"),
]

_INSURANCE_FIELDS: list[tuple[str, str]] = [
    ("profession", "Profession"),
    ("financier", "Financier"),
    ("insurer", "Insurer"),
    ("marital_status", "Marital status"),
    ("nominee_gender", "Nominee gender"),
    ("nominee_name", "Nominee name"),
    ("nominee_age", "Nominee age"),
    ("nominee_relationship", "Nominee relationship"),
    ("payment_mode", "Payment mode"),
]

_SYNTHETIC_FAILURE_LABELS: tuple[tuple[str, str], ...] = (
    ("extraction_error", "Aadhaar identity"),
    ("name_mismatch_error", "Name match"),
)


def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    return not str(val).strip()


def _mobile_from_details_or_subfolder(details: dict[str, Any], subfolder: str) -> str | None:
    customer = details.get("customer")
    if isinstance(customer, dict):
        for key in ("mobile_number", "mobile"):
            md = digits_only_mobile(str(customer.get(key) or ""))
            if md:
                return md
    m = re.match(r"^(\d{10})", (subfolder or "").strip())
    return m.group(1) if m else None


def compute_missing_ocr_fields(details: dict[str, Any] | None) -> list[str]:
    """Return human-readable missing field labels sorted A→Z (case-insensitive)."""
    if not details or not isinstance(details, dict):
        return []

    missing: set[str] = set()
    vehicle = details.get("vehicle") if isinstance(details.get("vehicle"), dict) else {}
    customer = details.get("customer") if isinstance(details.get("customer"), dict) else {}
    insurance = details.get("insurance") if isinstance(details.get("insurance"), dict) else {}

    for key, label in _VEHICLE_FIELDS:
        if _is_empty((vehicle or {}).get(key)):
            missing.add(label)

    for key, label in AADHAR_15_FIELDS:
        if _is_empty((customer or {}).get(key)):
            missing.add(label)

    if _is_empty((customer or {}).get("address")):
        missing.add("Address")

    if _is_empty(details.get("details_customer_name")):
        missing.add("Details customer name")

    if _is_empty((customer or {}).get("alt_phone_num")):
        missing.add("Alt phone num")

    for key, label in _INSURANCE_FIELDS:
        if _is_empty((insurance or {}).get(key)):
            missing.add(label)

    for json_key, label in _SYNTHETIC_FAILURE_LABELS:
        if details.get(json_key):
            missing.add(label)

    return sorted(missing, key=str.casefold)


def record_safe(*, dealer_id: int, subfolder: str, details: dict[str, Any] | None) -> None:
    """Insert an OCR log row when at least one tracked field is missing."""
    try:
        labels = compute_missing_ocr_fields(details)
        if not labels:
            return
        from app.repositories.ocr_run_log import insert_ocr_run_log

        insert_ocr_run_log(
            dealer_id=int(dealer_id),
            customer_mobile=_mobile_from_details_or_subfolder(details or {}, subfolder),
            sale_subfolder=subfolder,
            ocr_failures=", ".join(labels),
        )
    except Exception:
        logger.exception("ocr_run_log_service.record_safe failed dealer_id=%s subfolder=%s", dealer_id, subfolder)
