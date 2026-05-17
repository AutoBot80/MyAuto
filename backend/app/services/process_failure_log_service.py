"""Helpers to build dedupe keys and record failures without breaking main flows."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def digits_only_mobile(raw: str | None) -> str | None:
    if raw is None:
        return None
    d = re.sub(r"\D", "", str(raw).strip())
    return d[-10:] if len(d) >= 10 else (d if d else None)


def entity_key_fill_dms(
    *,
    staging_id: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    mobile_digits: str | None,
) -> str:
    if mobile_digits:
        return f"m:{mobile_digits}"
    sid = (staging_id or "").strip()
    if sid:
        return f"staging:{sid}"
    if customer_id is not None and vehicle_id is not None:
        return f"cv:{int(customer_id)}_{int(vehicle_id)}"
    if customer_id is not None:
        return f"cust:{int(customer_id)}"
    return "fill_dms:unknown"


def entity_key_print_forms(*, subfolder: str, mobile_digits: str | None, suffix: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"
    mob = mobile_digits or "nomobile"
    return f"{suffix}:{safe}:{mob}"


PROCESS_LABEL_PRINT_QUEUE_RTO = "Print / Queue RTO"


def mobile_digits_for_print_rto_subfolder(
    subfolder: str,
    customer: dict | None = None,
) -> str | None:
    """10-digit mobile from customer dict or sale subfolder prefix ``{mobile}_…``."""
    if isinstance(customer, dict):
        raw = customer.get("mobile_number")
        if raw is None:
            raw = customer.get("mobile")
        md = digits_only_mobile(str(raw).strip() if raw is not None else "")
        if md:
            return md
    m = re.match(r"^(\d{10})", (subfolder or "").strip())
    return m.group(1) if m else None


def entity_key_print_queue_rto(*, subfolder: str, mobile_digits: str | None) -> str:
    return entity_key_print_forms(subfolder=subfolder, mobile_digits=mobile_digits, suffix="pqtrto")


def entity_key_challan(*, challan_book_num: str | None, challan_date: str | None, batch_id: uuid.UUID) -> str:
    cb = (challan_book_num or "").strip()
    cd = (challan_date or "").strip()
    if cb and cd:
        return f"c:{cb}|{cd}"
    return f"b:{batch_id}"


def record_safe(**kwargs: Any) -> None:
    try:
        from app.repositories.process_failure_log import upsert_process_failure

        upsert_process_failure(**kwargs)
    except Exception:
        logger.exception("process_failure_log_service.record_safe failed")
