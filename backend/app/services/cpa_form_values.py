"""Load CPA Alliance fill values from ``form_cpa_insurance_view`` plus ``add_sales_staging.payload_json``.

Same joint pattern as ``insurance_form_values.build_insurance_fill_values`` (BR-20): committed
masters via the view after Create Invoice, OCR/operator merge from staging when ``staging_id`` is set.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import UUID

from app.db import get_connection
from app.repositories.add_sales_staging import (
    fetch_staging_payload,
    fetch_staging_subfolder,
    merge_staging_payload_on_cursor,
)
from app.services.add_sales_natural_key_resolve import (
    natural_keys_from_staging_payload,
    resolve_customer_vehicle_ids_by_natural_keys,
)
from app.services.utility_functions import (
    clean_text,
    normalize_address_dedupe_repetition,
    normalize_dob_for_misp,
    normalize_nominee_relationship_value,
    require_customer_vehicle_ids,
    safe_subfolder_name,
)

logger = logging.getLogger(__name__)

CPA_PLAN_TOTAL_AMOUNT_DEFAULT = "380"


def load_latest_cpa_insurance_values(customer_id: int, vehicle_id: int) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM form_cpa_insurance_view
                WHERE customer_id = %s AND vehicle_id = %s
                LIMIT 1
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def _apply_staging_cpa_overlay(values: dict, staging_payload: dict | None) -> None:
    """Fill empty view fields from staging ``customer`` / ``vehicle`` / ``insurance`` blobs."""
    if not staging_payload or not isinstance(staging_payload, dict):
        return
    ins = staging_payload.get("insurance") if isinstance(staging_payload.get("insurance"), dict) else {}
    cust = staging_payload.get("customer") if isinstance(staging_payload.get("customer"), dict) else {}
    veh = staging_payload.get("vehicle") if isinstance(staging_payload.get("vehicle"), dict) else {}

    def take_if_empty(key: str, raw: object) -> None:
        if values.get(key):
            return
        if raw is None:
            return
        t = clean_text(raw) if isinstance(raw, str) else clean_text(str(raw))
        if t:
            values[key] = t

    take_if_empty("customer_name", cust.get("name"))
    take_if_empty("gender", cust.get("gender"))
    take_if_empty("date_of_birth", cust.get("date_of_birth") or cust.get("dob"))
    take_if_empty("mobile_number", cust.get("mobile_number") or cust.get("mobile"))
    take_if_empty("address", cust.get("address"))
    take_if_empty("state", cust.get("state"))
    take_if_empty("city", cust.get("city"))
    take_if_empty("pin_code", cust.get("pin_code") or cust.get("pin"))

    frame = clean_text(veh.get("full_chassis") or veh.get("frame_no"))
    engine = clean_text(veh.get("full_engine") or veh.get("engine_no"))
    if frame:
        if not values.get("full_chassis"):
            values["full_chassis"] = frame
        if not values.get("frame_no"):
            values["frame_no"] = frame
    if engine:
        if not values.get("full_engine"):
            values["full_engine"] = engine
        if not values.get("engine_no"):
            values["engine_no"] = engine

    take_if_empty("model", veh.get("model"))
    if not values.get("model"):
        mc = clean_text(veh.get("model_colour"))
        if mc and "/" in mc:
            values["model"] = mc.split("/", 1)[0].strip()
        elif mc:
            values["model"] = mc

    take_if_empty("year_of_mfg", veh.get("year_of_mfg"))
    take_if_empty("make", veh.get("make") or veh.get("oem") or veh.get("manufacturer"))

    take_if_empty("nominee_name", ins.get("nominee_name"))
    take_if_empty(
        "nominee_relationship",
        ins.get("nominee_relationship") or ins.get("nominee_relation"),
    )
    take_if_empty("nominee_gender", ins.get("nominee_gender"))
    if not values.get("nominee_age") and ins.get("nominee_age") is not None:
        t = clean_text(str(ins.get("nominee_age")).strip())
        if t:
            values["nominee_age"] = t


def build_cpa_fill_values(
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    staging_payload: dict | None = None,
    subfolder: str | None = None,
    ocr_output_dir: Path | None = None,
) -> dict:
    """
    Build Alliance CPA fill dict from ``form_cpa_insurance_view`` merged with staging ``payload_json``.
    Requires a ``sales_master`` row for the customer/vehicle pair (post–Create Invoice).
    """
    _ = ocr_output_dir, subfolder  # reserved for future OCR fallbacks
    cid, vid = require_customer_vehicle_ids(customer_id, vehicle_id, "form_cpa_insurance_view")
    row = load_latest_cpa_insurance_values(cid, vid)
    if not row:
        raise ValueError(
            f"No row in form_cpa_insurance_view for customer_id={cid} vehicle_id={vid} "
            "(requires a matching sales_master row — run Create Invoice first)."
        )

    chassis = clean_text(row.get("full_chassis") or row.get("frame_no"))
    engine = clean_text(row.get("full_engine") or row.get("engine_no"))
    values = {
        "customer_id": cid,
        "vehicle_id": vid,
        "sales_id": row.get("sales_id"),
        "subfolder": clean_text(subfolder),
        "customer_name": clean_text(row.get("customer_name")),
        "gender": clean_text(row.get("gender")),
        "date_of_birth": clean_text(row.get("date_of_birth")),
        "mobile_number": clean_text(row.get("mobile_number"))[:10],
        "address": clean_text(row.get("address")),
        "state": clean_text(row.get("state")),
        "city": clean_text(row.get("city")),
        "pin_code": clean_text(row.get("pin_code"))[:6],
        "frame_no": chassis,
        "full_chassis": chassis,
        "engine_no": engine,
        "full_engine": engine,
        "model": clean_text(row.get("model")),
        "year_of_mfg": clean_text(row.get("year_of_mfg")),
        "vehicle_type": "New",
        "client_type": "Individual",
        "plan_total_amount": CPA_PLAN_TOTAL_AMOUNT_DEFAULT,
        "nominee_name": clean_text(row.get("nominee_name")),
        "nominee_age": clean_text(row.get("nominee_age")),
        "nominee_relationship": clean_text(row.get("nominee_relationship")),
        "nominee_gender": clean_text(row.get("nominee_gender")),
        "make": "",
    }

    _apply_staging_cpa_overlay(values, staging_payload)

    if not clean_text(values.get("make")):
        values["make"] = (os.getenv("ALLIANCE_CPA_DEFAULT_MAKE") or "Hero MotoCorp Limited").strip()

    dob_raw = clean_text(values.get("date_of_birth"))
    values["date_of_birth"] = normalize_dob_for_misp(dob_raw) if dob_raw else ""
    values["nominee_relationship"] = normalize_nominee_relationship_value(values.get("nominee_relationship"))
    values["address"] = normalize_address_dedupe_repetition(values.get("address"))

    required = [
        ("customer_master.name", values["customer_name"]),
        ("customer_master.mobile_number", values["mobile_number"]),
        ("vehicle_master.chassis", values["full_chassis"] or values["frame_no"]),
        ("vehicle_master.engine", values["full_engine"] or values["engine_no"]),
    ]
    missing = [label for label, val in required if not val]
    if missing:
        raise ValueError("Missing required CPA DB values: " + ", ".join(missing))

    return values


def cpa_fill_values_to_alliance_payload(values: dict) -> dict:
    """Map ``build_cpa_fill_values`` output to ``add_alliance_cpa_insurance`` keyword args."""
    return {
        "customer_name": values.get("customer_name"),
        "mobile": values.get("mobile_number"),
        "frame_no": values.get("full_chassis") or values.get("frame_no"),
        "engine_no": values.get("full_engine") or values.get("engine_no"),
        "full_chassis": values.get("full_chassis"),
        "full_engine": values.get("full_engine"),
        "model": values.get("model"),
        "year_of_mfg": values.get("year_of_mfg"),
        "make": values.get("make"),
        "vehicle_type": values.get("vehicle_type") or "New",
        "client_type": values.get("client_type") or "Individual",
        "gender": values.get("gender"),
        "date_of_birth": values.get("date_of_birth"),
        "address": values.get("address"),
        "state": values.get("state"),
        "city": values.get("city"),
        "plan_total_amount": values.get("plan_total_amount") or CPA_PLAN_TOTAL_AMOUNT_DEFAULT,
        "nominee_name": values.get("nominee_name"),
        "nominee_relationship": values.get("nominee_relationship"),
        "nominee_gender": values.get("nominee_gender"),
        "nominee_age": values.get("nominee_age"),
    }


def _resolve_subfolder_for_cpa(
    req_subfolder: str | None,
    staging_id: str | None,
    dealer_id: int,
    staging_payload: dict | None,
) -> str:
    s = clean_text(req_subfolder)
    if s.lower() == "default":
        s = ""
    if s:
        return s
    sid = clean_text(staging_id)
    if sid:
        fs = fetch_staging_subfolder(sid, dealer_id)
        if fs and clean_text(fs):
            return clean_text(fs)
    if staging_payload and isinstance(staging_payload, dict):
        fl = clean_text(staging_payload.get("file_location"))
        if fl:
            return fl
    return ""


def prepare_cpa_alliance_fill(
    *,
    dealer_id: int,
    subfolder: str | None,
    staging_id: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    ocr_output_dir: Path,
) -> tuple[dict, dict, str]:
    """
    Resolve ``staging_id`` / master ids, load ``form_cpa_insurance_view``, merge staging overlay,
    and map to ``add_alliance_cpa_insurance`` keyword arguments.

    Returns ``(alliance_kwargs, full_values, subfolder_resolved)``.
    """
    staging_payload = None
    sid = clean_text(staging_id)
    if sid:
        try:
            UUID(sid)
        except ValueError as exc:
            raise ValueError("staging_id must be a valid UUID") from exc
        staging_payload = fetch_staging_payload(sid, dealer_id)
        if not staging_payload:
            if customer_id is not None and vehicle_id is not None:
                logger.info(
                    "prepare_cpa_alliance_fill: staging_id=%s not loaded; using customer_id=%s vehicle_id=%s",
                    sid,
                    customer_id,
                    vehicle_id,
                )
                staging_payload = None
            else:
                raise ValueError(
                    "Staging not found, abandoned, or dealer_id does not match, "
                    "or row is not in draft/committed status. "
                    "Pass customer_id and vehicle_id to continue without staging snapshot."
                )

    cid = customer_id
    vid = vehicle_id
    if staging_payload is not None:
        if cid is None:
            try:
                raw_c = staging_payload.get("customer_id")
                cid = int(raw_c) if raw_c is not None else None
            except (TypeError, ValueError):
                cid = None
        if vid is None:
            try:
                raw_v = staging_payload.get("vehicle_id")
                vid = int(raw_v) if raw_v is not None else None
            except (TypeError, ValueError):
                vid = None

    if staging_payload is not None and (cid is None or vid is None):
        keys = natural_keys_from_staging_payload(staging_payload)
        if keys:
            ch, eng, mob = keys
            rk_cid, rk_vid = resolve_customer_vehicle_ids_by_natural_keys(ch, eng, mob)
            if rk_cid is not None and rk_vid is not None:
                cid, vid = rk_cid, rk_vid
                if sid:
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                merge_staging_payload_on_cursor(
                                    cur,
                                    sid,
                                    dealer_id,
                                    {"customer_id": int(cid), "vehicle_id": int(vid)},
                                )
                            conn.commit()
                    except Exception as exc:
                        logger.warning(
                            "prepare_cpa_alliance_fill: persist resolved ids to staging failed: %s",
                            exc,
                        )

    if cid is None or vid is None:
        raise ValueError(
            "customer_id and vehicle_id are required, or staging must include them or resolvable "
            "chassis, engine, and mobile in the staging snapshot."
        )

    subfolder_resolved = _resolve_subfolder_for_cpa(subfolder, sid or None, dealer_id, staging_payload)
    if not subfolder_resolved:
        raise ValueError(
            "subfolder is required unless staging has file_location (Submit Info) or pass subfolder explicitly."
        )

    full_values = build_cpa_fill_values(
        int(cid),
        int(vid),
        staging_payload=staging_payload,
        subfolder=subfolder_resolved,
        ocr_output_dir=ocr_output_dir,
    )
    write_cpa_form_values_snapshot(ocr_output_dir, subfolder_resolved, full_values)
    alliance_kwargs = cpa_fill_values_to_alliance_payload(full_values)
    return alliance_kwargs, full_values, subfolder_resolved


def write_cpa_form_values_snapshot(ocr_output_dir: Path, subfolder: str | None, values: dict) -> None:
    """Optional trace file beside ``playwright_cpa_*.txt`` (same folder as insurance ``Insurance_Form_Values.txt``)."""
    if not subfolder or not str(subfolder).strip():
        return
    safe = safe_subfolder_name(subfolder)
    path = Path(ocr_output_dir).resolve() / safe / "CPA_Form_Values.txt"
    lines = ["CPA Alliance Form Values", ""]
    for key in sorted(values.keys()):
        lines.append(f"{key}: {values.get(key) or '—'}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("CPA: could not write CPA_Form_Values.txt: %s", exc)
