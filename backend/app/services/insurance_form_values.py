"""Load and normalize insurance/MISP fill values from ``form_insurance_view`` plus ``add_sales_staging.payload_json`` (when ``staging_id`` is used) and OCR JSON (insurer fallback).

Add Sales passes the same ``staging_id`` as Create Invoice (DMS) so the view (committed masters) and staging (full OCR merge) jointly supply the insurance flow — see BR-20.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_IST_TZ = ZoneInfo("Asia/Kolkata")


def _insurance_log_ts_ist() -> str:
    return datetime.now(_IST_TZ).isoformat(timespec="milliseconds")

from app.db import get_connection
from app.services.utility_functions import clean_text, require_customer_vehicle_ids, safe_subfolder_name

logger = logging.getLogger(__name__)


def read_insurance_insurer_from_ocr_json(ocr_output_dir: Path | None, subfolder: str | None) -> str:
    """Fallback insurer from Details sheet in OCR_To_be_Used.json when ``insurance_master.insurer`` is empty."""
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        return ""
    safe = safe_subfolder_name(subfolder)
    path = Path(ocr_output_dir).resolve() / safe / "OCR_To_be_Used.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ins = data.get("insurance") if isinstance(data.get("insurance"), dict) else {}
        return clean_text((ins or {}).get("insurer"))
    except Exception as exc:
        logger.debug("Insurance: could not read insurer from %s: %s", path, exc)
        return ""


def _apply_staging_insurance_overlay(values: dict, staging_payload: dict | None) -> None:
    """Fill empty insurer / nominee / nominee_gender from staging ``insurance`` blob."""
    if not staging_payload or not isinstance(staging_payload, dict):
        return
    ins = staging_payload.get("insurance") if isinstance(staging_payload.get("insurance"), dict) else {}

    def take_if_empty(key: str, raw: object) -> None:
        if values.get(key):
            return
        if raw is None:
            return
        t = clean_text(raw) if isinstance(raw, str) else clean_text(str(raw))
        if t:
            values[key] = t

    take_if_empty("insurer", ins.get("insurer"))
    take_if_empty("nominee_name", ins.get("nominee_name"))
    take_if_empty("nominee_relationship", ins.get("nominee_relationship"))
    if not values.get("nominee_age") and ins.get("nominee_age") is not None:
        t = clean_text(str(ins.get("nominee_age")).strip())
        if t:
            values["nominee_age"] = t
    take_if_empty("nominee_gender", ins.get("nominee_gender"))


def load_latest_insurance_values(customer_id: int, vehicle_id: int) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM form_insurance_view
                WHERE customer_id = %s AND vehicle_id = %s
                LIMIT 1
                """,
                (customer_id, vehicle_id),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def build_insurance_fill_values(
    customer_id: int | None,
    vehicle_id: int | None,
    subfolder: str | None = None,
    ocr_output_dir: Path | None = None,
    *,
    staging_payload: dict | None = None,
) -> dict:
    """
    Build MISP fill dict from ``form_insurance_view`` (chassis, customer, vehicle, dealer context).
    When ``staging_payload`` is set (``add_sales_staging.payload_json``), fills empty **insurer** / **nominee**
    fields from the embedded **insurance** object so OCR merge is available before
    ``insurance_master`` is populated after a **successful** Generate Insurance run (UPSERT). Insurer may still fall back to **OCR_To_be_Used.json** when view
    and staging lack it.
    Proposal-only controls (email default, add-ons, CPA, payment mode, registration date) are **not**
    sourced here — Playwright uses hardcoded defaults for those until persisted columns exist.
    """
    cid, vid = require_customer_vehicle_ids(customer_id, vehicle_id, "form_insurance_view")
    row = load_latest_insurance_values(cid, vid)
    if not row:
        raise ValueError(
            f"No row in form_insurance_view for customer_id={cid} vehicle_id={vid} "
            "(requires a matching sales_master row)."
        )
    insurer_db = clean_text(row.get("insurer"))
    fn = clean_text(row.get("frame_no"))
    values = {
        "subfolder": clean_text(subfolder),
        "insurer": insurer_db,
        "mobile_number": clean_text(row.get("mobile_number"))[:10],
        "alt_phone_num": clean_text(row.get("alt_phone_num"))[:16],
        "customer_name": clean_text(row.get("customer_name")),
        "gender": clean_text(row.get("gender")),
        "dob": clean_text(row.get("dob")),
        "marital_status": clean_text(row.get("marital_status")),
        "profession": clean_text(row.get("profession")),
        "state": clean_text(row.get("state")),
        "city": clean_text(row.get("city")),
        "pin_code": clean_text(row.get("pin_code"))[:6],
        "address": clean_text(row.get("address")),
        "frame_no": fn,
        "full_chassis": fn,
        "engine_no": clean_text(row.get("engine_no")),
        "model_name": clean_text(row.get("model_name")),
        "fuel_type": clean_text(row.get("fuel_type")),
        "year_of_mfg": clean_text(row.get("year_of_mfg")),
        "vehicle_price": clean_text(row.get("vehicle_price")),
        "oem_name": clean_text(row.get("oem_name")),
        "rto_name": clean_text(row.get("rto_name")),
        "nominee_name": clean_text(row.get("nominee_name")),
        "nominee_age": clean_text(row.get("nominee_age")),
        "nominee_relationship": clean_text(row.get("nominee_relationship")),
        "nominee_gender": clean_text(row.get("nominee_gender")),
        "financer_name": clean_text(row.get("financer_name")),
    }
    _apply_staging_insurance_overlay(values, staging_payload)
    insurer_json = read_insurance_insurer_from_ocr_json(ocr_output_dir, subfolder)
    if not values.get("insurer"):
        values["insurer"] = insurer_json
    required = [
        ("insurance_master.insurer (or staging / OCR details insurer)", values["insurer"]),
        ("customer_master.mobile_number", values["mobile_number"]),
        ("customer_master.name", values["customer_name"]),
        ("vehicle_master.chassis / frame", values["frame_no"]),
        ("vehicle_master.engine", values["engine_no"]),
    ]
    missing = [label for label, val in required if not val]
    if missing:
        raise ValueError("Missing required Insurance DB values: " + ", ".join(missing))
    if insurer_json and not insurer_db and values.get("insurer") == insurer_json:
        logger.info(
            "Insurance: using insurer from OCR JSON (%r); view/staging had no insurer",
            insurer_json[:80],
        )
    return values


def write_insurance_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    values: dict,
) -> None:
    if not subfolder or not str(subfolder).strip():
        return
    safe_subfolder = safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "Insurance_Form_Values.txt"
    label_values: list[tuple[str, str]] = [
        ("Insurance Company (fuzzy-matched to details insurer)", clean_text(values.get("insurer"))),
        ("Manufacturer / OEM (vehicle_master.oem_name or dealer oem_ref)", clean_text(values.get("oem_name"))),
        ("Mobile No.", clean_text(values.get("mobile_number"))),
        ("Alternate / Landline No.", clean_text(values.get("alt_phone_num"))),
        ("Proposer Name", clean_text(values.get("customer_name"))),
        ("Gender", clean_text(values.get("gender"))),
        ("Date of Birth", clean_text(values.get("dob"))),
        ("Marital Status", clean_text(values.get("marital_status"))),
        ("Occupation Type", clean_text(values.get("profession"))),
        ("Proposer State", clean_text(values.get("state"))),
        ("Proposer City", clean_text(values.get("city"))),
        ("Pin Code", clean_text(values.get("pin_code"))),
        ("Address", clean_text(values.get("address"))),
        ("VIN / Frame No. (Chassis)", clean_text(values.get("frame_no"))),
        ("Engine No.", clean_text(values.get("engine_no"))),
        ("Model Name", clean_text(values.get("model_name"))),
        ("Fuel Type", clean_text(values.get("fuel_type"))),
        ("Year of Manufacture", clean_text(values.get("year_of_mfg"))),
        ("Ex-Showroom (DMS cost)", clean_text(values.get("vehicle_price"))),
        ("RTO", clean_text(values.get("rto_name"))),
        ("Nominee Name", clean_text(values.get("nominee_name"))),
        ("Nominee Age", clean_text(values.get("nominee_age"))),
        ("Relation", clean_text(values.get("nominee_relationship"))),
        ("Nominee Gender", clean_text(values.get("nominee_gender"))),
        ("Financer Name", clean_text(values.get("financer_name"))),
    ]
    lines = ["Insurance Form Values", "", "--- Values sent to Insurance labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")
    lines.extend(
        [
            "",
            "--- Runtime values used by Playwright ---",
            f"customer_id: {customer_id or '—'}",
            f"vehicle_id: {vehicle_id or '—'}",
            f"subfolder: {safe_subfolder}",
            f"generated_at: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def reset_playwright_insurance_log(ocr_output_dir: Path | None, subfolder: str | None) -> None:
    """Start a fresh ``Playwright_insurance.txt`` for this run (same folder convention as DMS traces)."""
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        return
    safe = safe_subfolder_name(subfolder)
    path = Path(ocr_output_dir).resolve() / safe / "Playwright_insurance.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("Playwright Insurance — execution log (IST / Asia/Kolkata timestamps)\n\n")
            fp.write(f"started_ist={_insurance_log_ts_ist()}\n")
            fp.write(f"subfolder={safe!r}\n\n--- trace ---\n")
    except OSError as exc:
        logger.warning("Insurance: could not reset Playwright_insurance.txt: %s", exc)


def append_playwright_insurance_line(
    ocr_output_dir: Path | None,
    subfolder: str | None,
    prefix: str,
    message: str,
) -> None:
    """Append one IST timestamped line to ``ocr_output/<dealer>/<subfolder>/Playwright_insurance.txt``."""
    if not ocr_output_dir or not subfolder or not str(subfolder).strip():
        return
    if not (message or "").strip():
        return
    safe = safe_subfolder_name(subfolder)
    path = Path(ocr_output_dir).resolve() / safe / "Playwright_insurance.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.is_file()
        with open(path, "a", encoding="utf-8") as fp:
            if is_new:
                fp.write("Playwright Insurance — execution log (IST / Asia/Kolkata timestamps)\n\n")
                fp.write(f"started_ist={_insurance_log_ts_ist()}\n")
                fp.write(f"subfolder={safe!r}\n\n--- trace ---\n")
            fp.write(f"{_insurance_log_ts_ist()} [{prefix}] {message}\n")
    except OSError as exc:
        logger.warning("Insurance: could not write Playwright_insurance.txt: %s", exc)
