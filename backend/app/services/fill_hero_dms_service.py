"""
Fill DMS flow using Playwright: Hero Connect / Siebel (``DMS_MODE=real`` and ``DMS_REAL_URL_*``).
Vaahan Playwright against static training HTML was removed; ``run_fill_vahan_only`` / batch RTO helpers
raise until a production VAHAN automation is implemented.
Uses Chromium (faster launch). Requires: pip install playwright && playwright install chromium.
Uses headed browser by default (set DMS_PLAYWRIGHT_HEADED=false for headless).
Writes pulled data to ocr_output/subfolder/Data from DMS.txt for consistency with other OCR outputs.

**Browser lifetime:** This module never calls ``Browser.close()`` or ``Playwright.stop()`` for operator
sessions (including on API process exit and thread switches). Edge/Chrome stays open for the operator;
stale handles are moved to a retain list so GC does not implicitly close windows.

**JS dialogs:** ``run_fill_dms_only`` installs a per-tab ``dialog`` listener so short-lived Siebel
``alert``/``confirm`` dialogs do not crash the Playwright Node driver (CDP race *No dialog is showing*).
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.config import (
    DEALER_ID,
    DMS_PLAYWRIGHT_HEADED,
    DMS_REAL_URL_CONTACT,
    DMS_REAL_URL_ENQUIRY,
    DMS_REAL_URL_LINE_ITEMS,
    DMS_REAL_URL_PDI,
    DMS_REAL_URL_PRECHECK,
    DMS_REAL_URL_REPORTS,
    DMS_REAL_URL_VEHICLE,
    DMS_REAL_URL_VEHICLES,
    DMS_SIEBEL_ACTION_TIMEOUT_MS,
    DMS_SIEBEL_CONTENT_FRAME_SELECTOR,
    DMS_SIEBEL_MOBILE_ARIA_HINTS,
    DMS_SIEBEL_NAV_TIMEOUT_MS,
    OCR_OUTPUT_DIR,
    dms_automation_is_real_siebel,
)
from app.services.siebel_dms_playwright import SiebelDmsUrls, Playwright_Hero_DMS_fill
from app.repositories import form_dms as form_dms_repo
from app.repositories import form_vahan as form_vahan_repo
from app.db import get_connection
from app.services.customer_address_infer import enrich_customer_address_from_freeform
from app.services.add_sales_commit_service import commit_staging_masters_and_finalize_row
from app.services.handle_browser_opening import get_or_open_site_page
from app.services.utility_functions import (
    clean_text as _clean_text,
    require_customer_vehicle_ids as _require_customer_vehicle_ids,
    safe_subfolder_name as _safe_subfolder_name,
)

logger = logging.getLogger(__name__)


def _dms_scrape_has_vehicle_row(scraped: dict) -> bool:
    """True when DMS scrape returned at least one key vehicle identifier (mirrors fill_dms router helper)."""
    key_num = str(scraped.get("key_num") or "").strip()
    frame_num = str(scraped.get("frame_num") or "").strip()
    engine_num = str(scraped.get("engine_num") or "").strip()
    full_chassis = str(scraped.get("full_chassis") or "").strip()
    full_engine = str(scraped.get("full_engine") or "").strip()
    return bool(key_num or frame_num or engine_num or full_chassis or full_engine)


HERO_SUPPORTED_OEM_ID = "1"
HERO_OEM_ONLY_ERROR = "Currently only Hero MotoCorp Limited is  configured as OEM"


def _ensure_hero_oem_for_fill_dms(dealer_id: int | None) -> None:
    """
    Guard Fill DMS execution by dealer OEM.
    Only Hero oem_id matching ``HERO_SUPPORTED_OEM_ID`` (see ``dealer_ref.oem_id``) is supported.
    """
    did = int(dealer_id if dealer_id is not None else DEALER_ID)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(TRIM(oem_id::text), '') AS oem_id
                FROM dealer_ref
                WHERE dealer_id = %s
                LIMIT 1
                """,
                (did,),
            )
            row = cur.fetchone() or {}
            oem_id = str((row.get("oem_id") or "")).strip()
            if oem_id != HERO_SUPPORTED_OEM_ID:
                raise ValueError(HERO_OEM_ONLY_ERROR)
    finally:
        conn.close()

# ``id(page)`` for tabs that already have ``page.on("dialog", ...)`` installed.
_PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES: set[int] = set()


def _install_playwright_js_dialog_handler(page) -> None:
    """
    Siebel / Hero Connect sometimes shows ``alert``/``confirm`` that disappear immediately. Playwright's
    built-in auto-dismiss then races Chromium (``Page.handleJavaScriptDialog``: *No dialog is showing*),
    which can crash the Node driver with an uncaught ``ProtocolError``. A single explicit listener that
    calls ``accept()`` inside try/except avoids that race for this tab.
    """
    pid = id(page)
    if pid in _PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES:
        return
    _PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES.add(pid)

    def _on_dialog(dialog):
        try:
            dialog.accept()
        except Exception as exc:
            logger.debug("fill_dms_service: JS dialog accept skipped (already closed?): %s", exc)

    try:
        page.on("dialog", _on_dialog)
    except Exception as exc:
        _PLAYWRIGHT_JS_DIALOG_HANDLER_PAGES.discard(pid)
        logger.warning("fill_dms_service: could not attach JS dialog handler: %s", exc)


def _requires_operator_create_invoice(page) -> bool:
    """Detect whether the current DMS page is asking operator to click Create Invoice."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"create\s*invoice", re.IGNORECASE))
        if btn.count() > 0 and btn.first.is_visible():
            return True
        line_btn = page.locator("#dms-line-create-invoice")
        if line_btn.count() > 0 and line_btn.first.is_visible():
            return True
    except Exception:
        return False
    return False


def _fill_vahan_and_scrape(
    page,
    vahan_base_url: str,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
) -> tuple[str | None, float]:
    """
    Static Vaahan training HTML was removed. Production VAHAN Playwright is not implemented here.
    """
    del page, vahan_base_url, rto_dealer_id, customer_name, chassis_no
    del vehicle_model, vehicle_colour, fuel_type, year_of_mfg, vehicle_price
    raise NotImplementedError(
        "Vaahan Playwright for the old static training site was removed. "
        "Point VAHAN_BASE_URL at the production VAHAN portal and add selectors, or complete RTO steps manually."
    )


def _complete_vahan_upload_step(page) -> bool:
    del page
    raise NotImplementedError(
        "Vaahan cart/upload automation for the static training site was removed."
    )


def _split_name(full_name: str | None) -> tuple[str, str]:
    if not full_name or not full_name.strip():
        return "", ""
    parts = full_name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0], "")


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name (one segment) for ocr_output and uploads."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _write_data_from_dms(ocr_output_dir: Path, subfolder: str, customer: dict, vehicle: dict) -> None:
    """Write all pulled DMS data to ocr_output/subfolder/Data from DMS.txt (subfolder = mobile_ddmmyy)."""
    safe_name = _safe_subfolder_name(subfolder)
    base = Path(ocr_output_dir).resolve()
    dir_path = base / safe_name
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / "Data from DMS.txt"
    lines = ["Data from DMS", ""]

    lines.append("--- Customer (filled on enquiry) ---")
    for label, key in [
        ("Name", "name"),
        ("Address", "address"),
        ("State", "state"),
        ("Pin code", "pin_code"),
    ]:
        val = customer.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")
    mobile = customer.get("mobile_number") or customer.get("mobile")
    lines.append(f"Mobile: {mobile or '—'}")
    rel = customer.get("relation_prefix") or customer.get("dms_relation_prefix")
    if rel:
        lines.append(f"Relation (S/O or W/o): {rel}")
    fath = customer.get("care_of")
    if fath:
        lines.append(f"Care of / Father–Husband (Aadhaar QR): {fath}")

    lines.append("")
    lines.append("--- Vehicle (from DMS search result) ---")
    for label, key in [
        ("Key num", "key_num"),
        ("Frame / Chassis num", "frame_num"),
        ("Full chassis (VIN from detail)", "full_chassis"),
        ("Engine num", "engine_num"),
        ("Full engine (from detail)", "full_engine"),
        ("Model", "model"),
        ("Color", "color"),
        ("Cubic capacity", "cubic_capacity"),
        ("Seating capacity", "seating_capacity"),
        ("Body type", "body_type"),
        ("Vehicle type", "vehicle_type"),
        ("Num cylinders", "num_cylinders"),
        ("Ex-showroom Price (Order Value)", "vehicle_price"),
        ("Year of Mfg", "year_of_mfg"),
        ("Order # (DMS)", "order_number"),
        ("Invoice # (DMS)", "invoice_number"),
    ]:
        val = vehicle.get(key)
        lines.append(f"{label}: {(val or '').strip() or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


# Default customer budget written to DMS_Form_Values.txt trace (Siebel fills booking amount in-portal).
DMS_TRACE_DEFAULT_CUSTOMER_BUDGET = "89000"

# UI checklist order (Add Sales banner). Labels must match exactly for sorting.
DMS_MILESTONE_ORDER: tuple[str, ...] = (
    "Customer found",
    "All Enquiries opened",
    "Care of filled",
    "Enquiry created",
    "Booking generated",
    "Vehicle received",
    "Pre check completed",
    "Vehicle inspection done",
    "Vehicle allocated",
    "Allotment view opened",
    "Invoice created",
)


def _dms_milestone(result: dict, label: str) -> None:
    m = result.setdefault("dms_milestones", [])
    if label not in m:
        m.append(label)


def _sort_dms_milestones(result: dict) -> None:
    m = list(result.get("dms_milestones") or [])
    order = {k: i for i, k in enumerate(DMS_MILESTONE_ORDER)}
    result["dms_milestones"] = sorted(m, key=lambda x: order.get(x, 99))


def _parse_vehicle_price(vehicle: dict) -> float:
    """Parse vehicle_price from vehicle (e.g. '72000' or '72,000') for Vahan automation."""
    raw = vehicle.get("vehicle_price")
    if raw is None:
        raw = vehicle.get("total_amount")
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coalesce_vehicle_master_vin(chassis: str, raw_frame_num: str) -> tuple[str, str]:
    """
    Full frame/VIN for Siebel: prefer ``vehicle_master.chassis`` when it looks like a full frame
    (length ≥ 11). If ``chassis`` is only a short tail, use the longer of the two columns so the
    complete VIN stored in ``raw_frame_num`` is not ignored.
    Returns ``(vin, source)`` where source is ``chassis``, ``raw_frame_num``, or ``empty``.
    """
    c = _clean_text(chassis)
    r = _clean_text(raw_frame_num)
    if not c and not r:
        return "", "empty"
    if not c:
        return r, "raw_frame_num"
    if not r:
        return c, "chassis"
    if len(c) >= 11:
        return c, "chassis"
    if len(r) > len(c):
        return r, "raw_frame_num"
    return c, "chassis"


def _coalesce_vehicle_master_engine(engine: str, raw_engine_num: str) -> tuple[str, str]:
    """Prefer full engine number when ``engine`` is long enough; else take the longer column."""
    e = _clean_text(engine)
    re_ = _clean_text(raw_engine_num)
    if not e and not re_:
        return "", "empty"
    if not e:
        return re_, "raw_engine_num"
    if not re_:
        return e, "engine"
    if len(e) >= 8:
        return e, "engine"
    if len(re_) > len(e):
        return re_, "raw_engine_num"
    return e, "engine"


def _vehicle_identity_from_ocr_vehicle(vehicle: dict) -> dict[str, str]:
    """Full chassis/engine/model/colour from OCR staging ``vehicle`` dict (no ``vehicle_master`` read)."""
    if not vehicle:
        return {}
    ch = _clean_text(vehicle.get("chassis"))
    rf = _clean_text(vehicle.get("frame_no"))
    eng = _clean_text(vehicle.get("engine"))
    re_ = _clean_text(vehicle.get("engine_no"))
    vin, _ = _coalesce_vehicle_master_vin(ch, rf)
    eng_m, _ = _coalesce_vehicle_master_engine(eng, re_)
    return {
        "chassis": vin,
        "engine": eng_m,
        "model": _clean_text(vehicle.get("model")),
        "colour": _clean_text(vehicle.get("colour") or vehicle.get("color")),
    }


def _aadhar_last4_from_customer(customer: dict) -> str:
    raw = customer.get("aadhar_id") or ""
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) >= 4:
        return digits[-4:]
    return digits


def _format_amount(value: object | None) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value).strip()


def _parse_float_or_zero(value: object | None) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _require_customer_vehicle_ids(customer_id: int | None, vehicle_id: int | None, view_name: str) -> tuple[int, int]:
    if customer_id is None or vehicle_id is None:
        raise ValueError(f"customer_id and vehicle_id are required because automation now reads from {view_name} only")
    return customer_id, vehicle_id


def _load_form_vahan_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    if customer_id is None or vehicle_id is None:
        return {}
    try:
        return form_vahan_repo.get_by_customer_vehicle(customer_id, vehicle_id) or {}
    except Exception as exc:
        logger.warning(
            "fill_dms_service: form_vahan_view lookup failed customer_id=%s vehicle_id=%s: %s",
            customer_id,
            vehicle_id,
            exc,
        )
        return {}


def _load_required_form_vahan_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "form_vahan_view")
    row = _load_form_vahan_row(cid, vid)
    if not row:
        raise ValueError(f"No form_vahan_view row found for customer_id={cid} vehicle_id={vid}")
    return row


def _load_form_dms_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    if customer_id is None or vehicle_id is None:
        return {}
    try:
        return form_dms_repo.get_by_customer_vehicle(customer_id, vehicle_id) or {}
    except Exception as exc:
        logger.warning(
            "fill_dms_service: DMS fill row lookup failed customer_id=%s vehicle_id=%s: %s",
            customer_id,
            vehicle_id,
            exc,
        )
        return {}


def _load_required_form_dms_row(customer_id: int | None, vehicle_id: int | None) -> dict:
    cid, vid = _require_customer_vehicle_ids(customer_id, vehicle_id, "DMS fill row")
    row = _load_form_dms_row(cid, vid)
    if not row:
        raise ValueError(
            f"No sales row found for customer_id={cid} vehicle_id={vid} (cannot build DMS fill values)"
        )
    return row


def _load_vehicle_master_identity(vehicle_id: int | None) -> dict[str, str]:
    """
    Full VIN / engine / model / colour from ``vehicle_master`` for Siebel (create_order, attach, etc.).
    Reads ``chassis`` and ``raw_frame_num`` (and engine / ``raw_engine_num``); merges so the longer
    complete value wins when one column still holds only a short tail (e.g. last five digits).
    """
    if vehicle_id is None:
        return {}
    try:
        from app.db import get_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TRIM(COALESCE(chassis::text, '')) AS chassis,
                           TRIM(COALESCE(raw_frame_num::text, '')) AS raw_frame_num,
                           TRIM(COALESCE(engine::text, '')) AS engine,
                           TRIM(COALESCE(raw_engine_num::text, '')) AS raw_engine_num,
                           TRIM(COALESCE(model::text, '')) AS model,
                           TRIM(COALESCE(colour::text, '')) AS colour
                    FROM vehicle_master
                    WHERE vehicle_id = %s
                    """,
                    (int(vehicle_id),),
                )
                row = cur.fetchone()
                if not row:
                    return {}
                _vin, _vin_src = _coalesce_vehicle_master_vin(
                    row.get("chassis"), row.get("raw_frame_num")
                )
                _eng, _eng_src = _coalesce_vehicle_master_engine(
                    row.get("engine"), row.get("raw_engine_num")
                )
                # #region agent log
                try:
                    _dbg = Path(__file__).resolve().parents[3] / "debug-08e634.log"
                    with open(_dbg, "a", encoding="utf-8") as _lf:
                        _lf.write(
                            json.dumps(
                                {
                                    "sessionId": "08e634",
                                    "hypothesisId": "H_vm_vin",
                                    "location": "fill_hero_dms_service.py:_load_vehicle_master_identity",
                                    "message": "vehicle_master chassis/raw merge",
                                    "data": {
                                        "vehicle_id": int(vehicle_id),
                                        "len_chassis_col": len(_clean_text(row.get("chassis"))),
                                        "len_raw_frame_col": len(_clean_text(row.get("raw_frame_num"))),
                                        "vin_source": _vin_src,
                                        "len_merged_vin": len(_vin),
                                        "len_engine_col": len(_clean_text(row.get("engine"))),
                                        "len_raw_engine_col": len(_clean_text(row.get("raw_engine_num"))),
                                        "engine_source": _eng_src,
                                        "len_merged_engine": len(_eng),
                                    },
                                    "timestamp": int(time.time() * 1000),
                                }
                            )
                            + "\n"
                        )
                except Exception:
                    pass
                # #endregion
                return {
                    "chassis": _vin,
                    "engine": _eng,
                    "model": _clean_text(row.get("model")),
                    "colour": _clean_text(row.get("colour")),
                }
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "fill_dms_service: vehicle_master identity lookup failed vehicle_id=%s: %s",
            vehicle_id,
            exc,
        )
        return {}


def _load_customer_aadhar_last4(customer_id: int | None) -> str:
    """Last 4 digits stored in ``customer_master.aadhar`` (UIDAI compliance). Used for Siebel UIN No."""
    if customer_id is None:
        return ""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(TRIM(aadhar::text), '') AS aad FROM customer_master WHERE customer_id = %s",
                    (customer_id,),
                )
                row = cur.fetchone()
                if not row:
                    return ""
                if isinstance(row, dict):
                    return _clean_text(row.get("aad"))
                try:
                    return _clean_text(row[0])
                except Exception:
                    return ""
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "fill_dms_service: customer_master aadhar lookup failed customer_id=%s: %s",
            customer_id,
            exc,
        )
        return ""


def _load_customer_gender_from_master(customer_id: int | None) -> str:
    """
    Preferred gender source for DMS relation-name derivation:
    customer_master.gender (Aadhaar-derived and persisted via submit flow).
    """
    if customer_id is None:
        return ""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(gender, '') AS gender_val FROM customer_master WHERE customer_id = %s",
                    (customer_id,),
                )
                row = cur.fetchone()
                if not row:
                    return ""
                # RealDictCursor returns dict-like rows; keep tuple fallback for safety.
                if isinstance(row, dict):
                    return _clean_text(row.get("gender_val"))
                try:
                    return _clean_text(row[0])
                except Exception:
                    return ""
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("fill_dms_service: customer_master gender lookup failed customer_id=%s: %s", customer_id, exc)
        return ""


def _load_customer_dob_from_master(customer_id: int | None) -> str:
    """DOB source for add-enquiry age derivation (customer_master.date_of_birth)."""
    if customer_id is None:
        return ""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(TRIM(date_of_birth::text), '') AS dob_val FROM customer_master WHERE customer_id = %s",
                    (customer_id,),
                )
                row = cur.fetchone()
                if not row:
                    return ""
                if isinstance(row, dict):
                    return _clean_text(row.get("dob_val"))
                try:
                    return _clean_text(row[0])
                except Exception:
                    return ""
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("fill_dms_service: customer_master DOB lookup failed customer_id=%s: %s", customer_id, exc)
        return ""


def _derive_gender_from_care_of_text(care_of_text: str) -> str:
    """
    Fallback only when DB gender is missing.
    Infer coarse gender from relation marker in care_of/father text.
    """
    t = _clean_text(care_of_text).upper().replace(" ", "")
    if not t:
        return ""
    if t.startswith("D/O") or t.startswith("DO"):
        return "Female"
    if t.startswith("W/O") or t.startswith("WO"):
        return "Female"
    if t.startswith("S/O") or t.startswith("SO"):
        return "Male"
    return ""


def _normalize_dms_relation_prefix(raw: object | None) -> str:
    s = _clean_text(raw).upper().replace(" ", "")
    if s in ("S/O", "SO"):
        return "S/O"
    if s in ("D/O", "DO", "D/O."):
        return "D/o"
    if s in ("W/O", "WO", "W/O."):
        return "W/o"
    if "W" in s and "O" in s:
        return "W/o"
    if "S" in s and "O" in s:
        return "S/O"
    return _clean_text(raw)


def _build_dms_fill_values(
    customer_id: int | None,
    vehicle_id: int | None,
    subfolder: str | None = None,
    *,
    staging_payload: dict | None = None,
) -> dict:
    """
    Build Playwright DMS values. **Staging path:** ``staging_payload`` (OCR merge in ``add_sales_staging``)
    — no reads of ``customer_master`` / ``vehicle_master`` / ``sales_master``. **Legacy path:** master join
    via ``form_dms_repo.get_by_customer_vehicle``.
    """
    if staging_payload is not None:
        row = form_dms_repo.build_dms_fill_row_from_staging_payload(staging_payload)
        cust = staging_payload.get("customer") if isinstance(staging_payload.get("customer"), dict) else {}
        gender_master = _clean_text(cust.get("gender"))
        dob_src = _clean_text(cust.get("date_of_birth"))
        aadhar_src = _aadhar_last4_from_customer(cust)
    else:
        row = _load_required_form_dms_row(customer_id, vehicle_id)
        gender_master = _load_customer_gender_from_master(customer_id)
        try:
            cid_for_aadhar = int(row.get("customer_id")) if row.get("customer_id") is not None else None
        except (TypeError, ValueError):
            cid_for_aadhar = None
        dob_src = _load_customer_dob_from_master(cid_for_aadhar)
        aadhar_src = _load_customer_aadhar_last4(cid_for_aadhar)

    addr_full = _clean_text(row.get("Address Line 1"))
    pin_raw = _clean_text(row.get("Pin Code"))[:6]
    state_raw = _clean_text(row.get("State"))
    father_raw = _clean_text(row.get("Father or Husband Name"))
    inferred_addr = enrich_customer_address_from_freeform(
        {
            "address": addr_full,
            "pin": pin_raw,
            "state": state_raw,
            "care_of": father_raw,
        }
    )
    pin_e = _clean_text(inferred_addr.get("pin"))[:6] or pin_raw
    state_e = _clean_text(inferred_addr.get("state")) or state_raw
    addr_line = _clean_text(inferred_addr.get("address"))[:80] or addr_full[:80]
    father_e = _clean_text(inferred_addr.get("care_of"))[:255] or father_raw[:255]
    care_of_e = father_e
    first_name = _clean_text(row.get("Contact First Name"))
    last_name = _clean_text(row.get("Contact Last Name"))
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    effective_subfolder = _clean_text(row.get("subfolder")) or _clean_text(subfolder)
    relation_raw = row.get("Relation (S/O or W/o)")
    relation_prefix = _normalize_dms_relation_prefix(relation_raw) if _clean_text(relation_raw) else ""
    contact_path = (_clean_text(row.get("DMS Contact Path")) or "found").lower()
    if contact_path not in ("found", "new_enquiry", "skip_find"):
        contact_path = "found"
    finance_required = (_clean_text(row.get("Finance Required")) or "N").upper()
    if finance_required not in ("Y", "N"):
        finance_required = "N"
    gender_row = _clean_text(row.get("Gender")) or _clean_text(row.get("gender"))
    gender_effective = gender_master or gender_row or _derive_gender_from_care_of_text(care_of_e)

    values = {
        "row": row,
        "subfolder": effective_subfolder,
        "customer_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "mobile_phone": _clean_text(row.get("Mobile Phone #"))[:10],
        "landline": _clean_text(row.get("Landline #"))[:16],
        "address_line_1": addr_line,
        "city": _clean_text(row.get("City"))[:80],
        "district": _clean_text(row.get("District"))[:80],
        "tehsil": _clean_text(row.get("Tehsil"))[:80],
        "age": _clean_text(row.get("Age"))[:8],
        "state": state_e,
        "pin_code": pin_e,
        "key_partial": _clean_text(row.get("Key num (partial)"))[:8],
        "battery_partial": "" if _clean_text(row.get("Battery No") or "").upper() in ("EMPTY", "BLANK", "NA", "N/A", "NIL", "NONE", "-") else _clean_text(row.get("Battery No") or "")[:12],
        "frame_partial": _clean_text(row.get("Frame / Chassis num (partial)"))[:12],
        "engine_partial": _clean_text(row.get("Engine num (partial)"))[:12],
        "relation_prefix": relation_prefix,
        "care_of": care_of_e,
        "financier_name": _clean_text(row.get("Financier Name"))[:255],
        "finance_required": finance_required,
        "dms_contact_path": contact_path,
        "gender": gender_effective,
        "date_of_birth": dob_src,
        "aadhar_id": aadhar_src,
        "customer_export": {
            "name": full_name,
            "address": _clean_text(inferred_addr.get("address")) or addr_full,
            "state": state_e,
            "pin_code": pin_e,
            "mobile_number": _clean_text(row.get("Mobile Phone #")),
            "alt_phone_num": _clean_text(row.get("Landline #")),
            "relation_prefix": relation_prefix,
            "care_of": father_e,
            "finance_required": finance_required,
            "financier_name": _clean_text(row.get("Financier Name")),
        },
    }
    required_keys = [
        ("DMS fill.Contact First Name", values["first_name"]),
        ("DMS fill.Mobile Phone #", values["mobile_phone"]),
        ("DMS fill.State", values["state"]),
        ("DMS fill.Address Line 1", values["address_line_1"]),
        ("DMS fill.Pin Code", values["pin_code"]),
        ("DMS fill.Key num (partial)", values["key_partial"]),
        ("DMS fill.Frame / Chassis num (partial)", values["frame_partial"]),
        ("DMS fill.Engine num (partial)", values["engine_partial"]),
    ]
    missing = [label for label, val in required_keys if not val]
    if missing:
        src = "staging OCR payload" if staging_payload is not None else "database"
        raise ValueError(f"Missing required DMS fields ({src}): " + ", ".join(missing))

    if staging_payload is not None:
        vm = _vehicle_identity_from_ocr_vehicle(
            staging_payload.get("vehicle") if isinstance(staging_payload.get("vehicle"), dict) else {}
        )
    else:
        vm = _load_vehicle_master_identity(vehicle_id)
    _ch = (vm.get("chassis") or "").strip()
    _eng = (vm.get("engine") or "").strip()
    _mod = (vm.get("model") or "").strip()
    _col = (vm.get("colour") or "").strip()
    if _ch:
        values["full_chassis"] = _ch
        values["frame_num"] = _ch
    if _eng:
        values["full_engine"] = _eng
        values["engine_num"] = _eng
    if _mod:
        values["model"] = _mod
    if _col:
        values["color"] = _col
        values["colour"] = _col

    return values


def _build_vahan_fill_values(customer_id: int | None, vehicle_id: int | None, subfolder: str | None = None) -> dict:
    row = _load_required_form_vahan_row(customer_id, vehicle_id)
    vehicle_price = _parse_float_or_zero(row.get("vehicle_price"))
    if vehicle_price <= 0:
        raise ValueError(
            f"form_vahan_view.vehicle_price is empty for customer_id={customer_id} vehicle_id={vehicle_id}; "
            "run DMS first so vehicle_ex_showroom_price is stored on vehicle_master"
        )
    effective_subfolder = _clean_text(row.get("subfolder")) or _clean_text(subfolder)
    values = {
        "row": row,
        "subfolder": effective_subfolder,
        "rto_dealer_id": _clean_text(row.get("rto_dealer_id")),
        "customer_name": _clean_text(row.get("Owner Name *")),
        "chassis_no": _clean_text(row.get("Chassis No *")),
        "vehicle_model": _clean_text(row.get("vehicle_model")),
        "vehicle_colour": _clean_text(row.get("vehicle_colour")),
        "fuel_type": _clean_text(row.get("fuel_type")),
        "year_of_mfg": _clean_text(row.get("year_of_mfg")),
        "vehicle_price": vehicle_price,
    }
    required_keys = [
        ("form_vahan_view.rto_dealer_id", values["rto_dealer_id"]),
        ("form_vahan_view.Owner Name *", values["customer_name"]),
        ("form_vahan_view.Chassis No *", values["chassis_no"]),
        ("form_vahan_view.vehicle_model", values["vehicle_model"]),
        ("form_vahan_view.vehicle_colour", values["vehicle_colour"]),
        ("form_vahan_view.fuel_type", values["fuel_type"]),
        ("form_vahan_view.year_of_mfg", values["year_of_mfg"]),
    ]
    missing = [label for label, val in required_keys if not val]
    if missing:
        raise ValueError("Missing required Vahan DB values: " + ", ".join(missing))
    return values


def _write_dms_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    dms_fill_row: dict | None = None,
    customer_name: str,
    mobile_number: str,
    alt_phone_num: str,
    address: str,
    state: str,
    pin_code: str,
    key_no: str,
    frame_no: str,
    engine_no: str,
    relation_prefix: str = "",
    care_of: str = "",
    customer_budget: str = "",
    finance_required: str = "",
    financier_name: str = "",
    dms_contact_path: str = "",
) -> None:
    if not subfolder or not str(subfolder).strip():
        return

    row = dms_fill_row if dms_fill_row is not None else _load_form_dms_row(customer_id, vehicle_id)
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "DMS_Form_Values.txt"

    row_first_name = _clean_text(row.get("Contact First Name"))
    row_last_name = _clean_text(row.get("Contact Last Name"))
    first_name, last_name = _split_name(customer_name or "")
    effective_first_name = _clean_text(first_name) or row_first_name
    effective_last_name = _clean_text(last_name) or row_last_name
    effective_mobile = _clean_text(mobile_number)[:10] or _clean_text(row.get("Mobile Phone #"))[:10]
    effective_landline = _clean_text(alt_phone_num)[:16] or _clean_text(row.get("Landline #"))[:16]
    effective_address = _clean_text(address)[:80] or _clean_text(row.get("Address Line 1"))[:80]
    effective_state = _clean_text(state) or _clean_text(row.get("State"))
    effective_pin = _clean_text(pin_code)[:6] or _clean_text(row.get("Pin Code"))[:6]
    effective_key = _clean_text(key_no)[:8] or _clean_text(row.get("Key num (partial)"))
    effective_frame = _clean_text(frame_no)[:12] or _clean_text(row.get("Frame / Chassis num (partial)"))
    effective_engine = _clean_text(engine_no)[:12] or _clean_text(row.get("Engine num (partial)"))
    effective_relation = _clean_text(relation_prefix) or _clean_text(row.get("Relation (S/O or W/o)"))
    effective_father = _clean_text(care_of) or _clean_text(row.get("Father or Husband Name"))
    effective_budget = _clean_text(customer_budget)
    effective_fin_req = _clean_text(finance_required) or _clean_text(row.get("Finance Required")) or "N"
    effective_financier = _clean_text(financier_name) or _clean_text(row.get("Financier Name"))
    effective_path = _clean_text(dms_contact_path) or _clean_text(row.get("DMS Contact Path")) or "found"

    label_values: list[tuple[str, str]] = [
        ("Mr/Ms", _clean_text(row.get("Mr/Ms")) or "Mr."),
        ("Contact First Name", effective_first_name),
        ("Contact Last Name", effective_last_name),
        ("Mobile Phone #", effective_mobile),
        ("Landline #", effective_landline),
        ("State", effective_state),
        ("Address Line 1", effective_address),
        ("Pin Code", effective_pin),
        ("Relation (S/O or W/o)", effective_relation),
        ("Father or Husband Name", effective_father),
        ("Customer Budget (trace default)", effective_budget),
        ("Finance Required", effective_fin_req),
        ("Financier Name", effective_financier),
        ("DMS Contact Path", effective_path),
        ("Key num (partial)", effective_key),
        ("Frame / Chassis num (partial)", effective_frame),
        ("Engine num (partial)", effective_engine),
    ]

    runtime_values: list[tuple[str, str]] = [
        ("sales_id", _clean_text(row.get("sales_id"))),
        ("customer_id", _clean_text(customer_id or row.get("customer_id"))),
        ("vehicle_id", _clean_text(vehicle_id or row.get("vehicle_id"))),
        ("dealer_id", _clean_text(row.get("dealer_id"))),
        ("subfolder", safe_subfolder),
        ("dealer_name", _clean_text(row.get("dealer_name"))),
        ("oem_name", _clean_text(row.get("oem_name"))),
        ("source_customer_name", _clean_text(customer_name)),
        ("source_mobile_number", _clean_text(mobile_number)),
        ("source_alt_phone_num", _clean_text(alt_phone_num)),
        ("source_address", _clean_text(address)),
        ("source_state", _clean_text(state)),
        ("source_pin_code", _clean_text(pin_code)),
        ("source_key_no", _clean_text(key_no)),
        ("source_frame_no", _clean_text(frame_no)),
        ("source_engine_no", _clean_text(engine_no)),
        ("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]

    lines = ["DMS Form Values", "", "--- Values sent to DMS labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")

    lines.extend(["", "--- Runtime values used by Playwright ---"])
    for label, value in runtime_values:
        lines.append(f"{label}: {value or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_vahan_form_values(
    ocr_output_dir: Path,
    subfolder: str | None,
    customer_id: int | None,
    vehicle_id: int | None,
    *,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
    application_id: str | None,
    rto_fees: float | None,
) -> None:
    if not subfolder or not str(subfolder).strip():
        return

    row = _load_form_vahan_row(customer_id, vehicle_id)
    safe_subfolder = _safe_subfolder_name(subfolder)
    subfolder_path = Path(ocr_output_dir).resolve() / safe_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)
    path = subfolder_path / "Vahan_Form_Values.txt"

    effective_rto_dealer_id = _clean_text(rto_dealer_id) or _clean_text(row.get("rto_dealer_id"))
    effective_customer_name = _clean_text(customer_name) or _clean_text(row.get("Owner Name *"))
    effective_chassis_no = _clean_text(chassis_no) or _clean_text(row.get("Chassis No *"))
    effective_vehicle_model = _clean_text(vehicle_model) or _clean_text(row.get("vehicle_model"))
    effective_vehicle_colour = _clean_text(vehicle_colour) or _clean_text(row.get("vehicle_colour"))
    effective_fuel_type = _clean_text(fuel_type) or _clean_text(row.get("fuel_type"))
    effective_year_of_mfg = _clean_text(year_of_mfg) or _clean_text(row.get("year_of_mfg"))
    effective_vehicle_price = float(vehicle_price or 0)
    if effective_vehicle_price <= 0:
        raise ValueError("vehicle_price must be positive for Vahan form values export")

    label_values: list[tuple[str, str]] = [
        ("Registration Type *", _clean_text(row.get("Registration Type *"))),
        ("Chassis No *", effective_chassis_no),
        ("Engine/Motor No (Last 5 Chars)", effective_chassis_no[-5:] if effective_chassis_no else ""),
        ("Purchase Delivery Date", _clean_text(row.get("Purchase Delivery Date"))),
        ("Do You want to Opt Choice Number / Fancy Number / Retention Number", _clean_text(row.get("Do You want to Opt Choice Number / Fancy Number / Retention Number"))),
        ("Owner Name *", effective_customer_name),
        ("Owner Type", _clean_text(row.get("Owner Type"))),
        ("Son/Wife/Daughter of", _clean_text(row.get("Son/Wife/Daughter of"))),
        ("Ownership Serial", _clean_text(row.get("Ownership Serial"))),
        ("Aadhaar Mode", _clean_text(row.get("Aadhaar Mode"))),
        ("Category *", _clean_text(row.get("Category *"))),
        ("Mobile No", _clean_text(row.get("Mobile No"))),
        ("PAN Card", _clean_text(row.get("PAN Card"))),
        ("Voter ID", _clean_text(row.get("Voter ID"))),
        ("Aadhaar No", _clean_text(row.get("Aadhaar No"))),
        ("Permanent Address", _clean_text(row.get("Permanent Address"))),
        ("House No & Street Name", _clean_text(row.get("House No & Street Name"))),
        ("Village/Town/City", _clean_text(row.get("Village/Town/City"))),
        ("Insurance Type", _clean_text(row.get("Insurance Type"))),
        ("Insurer", _clean_text(row.get("Insurer"))),
        ("Policy No", _clean_text(row.get("Policy No"))),
        ("Insurance From (DD-MMM-YYYY)", _clean_text(row.get("Insurance From (DD-MMM-YYYY)"))),
        ("Insurance Upto (DD-MMM-YYYY)", _clean_text(row.get("Insurance Upto (DD-MMM-YYYY)"))),
        ("Insured Declared Value", _clean_text(row.get("Insured Declared Value"))),
        ("Please Select Series Type", _clean_text(row.get("Please Select Series Type"))),
        ("Financier / Bank", _clean_text(row.get("Financier / Bank"))),
        ("Application No", _clean_text(application_id) or _clean_text(row.get("Application No"))),
        ("Assigned Office & Action", _clean_text(row.get("Assigned Office & Action")) or effective_rto_dealer_id),
        ("Registration No", _clean_text(row.get("Registration No"))),
        ("Amount", _format_amount(rto_fees) or _clean_text(row.get("Amount"))),
    ]

    runtime_values: list[tuple[str, str]] = [
        ("sales_id", _clean_text(row.get("sales_id"))),
        ("customer_id", _clean_text(customer_id or row.get("customer_id"))),
        ("vehicle_id", _clean_text(vehicle_id or row.get("vehicle_id"))),
        ("dealer_id", _clean_text(row.get("dealer_id"))),
        ("subfolder", safe_subfolder),
        ("rto_dealer_id", effective_rto_dealer_id),
        ("vehicle_model", effective_vehicle_model),
        ("vehicle_colour", effective_vehicle_colour),
        ("fuel_type", effective_fuel_type),
        ("year_of_mfg", effective_year_of_mfg),
        ("vehicle_price", _format_amount(effective_vehicle_price)),
        ("generated_at", datetime.now().strftime("%d-%m-%Y %H:%M:%S")),
    ]

    lines = ["Vahan Form Values", "", "--- Values sent to Vahan labels ---"]
    for label, value in label_values:
        lines.append(f"{label}: {value or '—'}")

    lines.extend(["", "--- Runtime values used by Playwright ---"])
    for label, value in runtime_values:
        lines.append(f"{label}: {value or '—'}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _run_vahan_in_context(
    context,
    vahan_base_url: str,
    *,
    customer_id: int | None,
    vehicle_id: int | None,
    subfolder: str | None,
    ocr_output_dir: Path | None,
    complete_upload_step: bool,
) -> dict:
    """Run Vahan using an existing browser context so batches can reuse one session."""
    vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
    effective_subfolder = vahan_values.get("subfolder") or subfolder
    page = context.new_page()
    page.set_default_timeout(15_000)
    try:
        app_id, fees = _fill_vahan_and_scrape(
            page,
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
        )
        added_to_cart = False
        if complete_upload_step:
            added_to_cart = _complete_vahan_upload_step(page)
        if ocr_output_dir is not None and effective_subfolder:
            _write_vahan_form_values(
                ocr_output_dir=ocr_output_dir,
                subfolder=effective_subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                rto_dealer_id=vahan_values["rto_dealer_id"],
                customer_name=vahan_values["customer_name"],
                chassis_no=vahan_values["chassis_no"],
                vehicle_model=vahan_values["vehicle_model"],
                vehicle_colour=vahan_values["vehicle_colour"],
                fuel_type=vahan_values["fuel_type"],
                year_of_mfg=vahan_values["year_of_mfg"],
                vehicle_price=vahan_values["vehicle_price"],
                application_id=app_id,
                rto_fees=fees,
            )
        return {
            "application_id": app_id,
            "rto_fees": fees,
            "added_to_cart": added_to_cart,
            "subfolder": effective_subfolder,
        }
    finally:
        page.close()


def run_fill_vahan_batch_row(
    context,
    vahan_base_url: str,
    *,
    customer_id: int,
    vehicle_id: int,
    subfolder: str | None,
    ocr_output_dir: Path | None,
) -> dict:
    """Batch-safe Vahan helper that reuses one browser/context and stops after cart upload."""
    del context  # Existing open tab mode does not create/reuse server-owned contexts.
    page, open_error = get_or_open_site_page(vahan_base_url, "Vahan", require_login_on_open=False)
    if page is None:
        raise ValueError(open_error or "Vahan site not open. Please open Vahan site and keep it logged in.")
    vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
    app_id, fees = _fill_vahan_and_scrape(
        page,
        vahan_base_url=vahan_base_url.strip(),
        rto_dealer_id=vahan_values["rto_dealer_id"],
        customer_name=vahan_values["customer_name"],
        chassis_no=vahan_values["chassis_no"],
        vehicle_model=vahan_values["vehicle_model"],
        vehicle_colour=vahan_values["vehicle_colour"],
        fuel_type=vahan_values["fuel_type"],
        year_of_mfg=vahan_values["year_of_mfg"],
        vehicle_price=vahan_values["vehicle_price"],
    )
    added_to_cart = _complete_vahan_upload_step(page)
    if ocr_output_dir is not None and (vahan_values.get("subfolder") or subfolder):
        _write_vahan_form_values(
            ocr_output_dir=ocr_output_dir,
            subfolder=vahan_values.get("subfolder") or subfolder,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
            application_id=app_id,
            rto_fees=fees,
        )
    return {
        "application_id": app_id,
        "rto_fees": fees,
        "added_to_cart": added_to_cart,
        "subfolder": vahan_values.get("subfolder") or subfolder,
    }


def _parse_vehicle_year_int_for_db(raw) -> int | None:
    """Match Siebel-style year strings (``2009``, ``2,009``) to an integer yyyy for ``vehicle_master``."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    compact = re.sub(r"[\s,\u00a0\u202f'ʼ`]", "", s)
    m = re.search(r"(19\d{2}|20\d{2})", compact)
    if not m:
        return None
    try:
        y = int(m.group(1), 10)
        if 1900 <= y <= 2099:
            return y
    except ValueError:
        pass
    return None


def _normalize_vehicle_type_for_db(raw: object | None) -> str | None:
    """Store DMS ``vehicle_type`` in ALL CAPS (Siebel may send mixed case)."""
    s = (str(raw) if raw is not None else "").strip().upper()
    return s or None


def _is_two_wheeler_vehicle_type(vehicle_type_upper: str | None) -> bool:
    if not vehicle_type_upper:
        return False
    u = re.sub(r"\s+", "", vehicle_type_upper.upper())
    return "MOTORCYCLE" in u or "SCOOTER" in u


def _parse_cubic_cc_numeric_for_db(raw: object) -> float | None:
    """First numeric token from cc text (e.g. ``125 CC`` → ``125.0``)."""
    s = str(raw or "").strip().replace(",", "")
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def update_vehicle_master_from_dms(vehicle_id: int, scraped: dict) -> None:
    """
    Merge DMS / Siebel scrape into ``vehicle_master`` (non-null scraped values win via ``COALESCE``).

    Maps scrape keys → columns: **full_chassis** / **frame_num** → ``chassis``; **full_engine** /
    **engine_num** → ``engine``; **key_num** or **raw_key_num** → ``key_num``; **model** → ``model``;
    **color** / **colour** → ``colour``; **variant** → ``variant``; **vehicle_price** (after Price All /
    Allocate All in booking attach) / **vehicle_ex_showroom_cost** →
    ``vehicle_ex_showroom_price``; **year_of_mfg** (or **dispatch_year**) → ``year_of_mfg``.

    ``vehicle_type`` is normalized to **ALL CAPS**. When it indicates a **motorcycle** or **scooter**
    (substring, spaces ignored), sets ``seating_capacity`` = 2, ``body_type`` = ``Open``,
    ``num_cylinders`` = 1.

    **cubic_capacity** is stored as the first numeric token from scrape text (e.g. ``125 CC`` → ``125``).

    ``place_of_registeration`` and ``oem_name`` are filled from the latest ``sales_master`` row’s
    ``dealer_ref.rto_name`` and ``oem_ref.oem_name`` when available.

    Does **not** update ``raw_frame_num`` / ``raw_engine_num``.
    """
    from app.db import get_connection

    def _strip_or_none(k: str) -> str | None:
        v = (scraped.get(k) or "").strip()
        return v or None

    chassis = _strip_or_none("full_chassis") or _strip_or_none("frame_num") or _strip_or_none("chassis")
    engine = _strip_or_none("full_engine") or _strip_or_none("engine_num") or _strip_or_none("engine")
    key_num = _strip_or_none("key_num") or _strip_or_none("raw_key_num")
    model = _strip_or_none("model")
    colour = _strip_or_none("color") or _strip_or_none("colour")
    variant_raw = _strip_or_none("variant")
    variant = (variant_raw[:64] if variant_raw else None)
    cubic_capacity = scraped.get("cubic_capacity")
    seating_capacity = scraped.get("seating_capacity")
    body_type = (scraped.get("body_type") or "").strip() or None
    vehicle_type = _normalize_vehicle_type_for_db(scraped.get("vehicle_type"))
    num_cylinders = scraped.get("num_cylinders")
    year_of_mfg = _parse_vehicle_year_int_for_db(scraped.get("year_of_mfg"))
    if year_of_mfg is None:
        year_of_mfg = _parse_vehicle_year_int_for_db(scraped.get("dispatch_year"))
    ex_showroom = scraped.get("vehicle_price")
    if ex_showroom is None:
        ex_showroom = scraped.get("vehicle_ex_showroom_cost")
    if ex_showroom is None:
        ex_showroom = scraped.get("total_amount")
    cubic_capacity = _parse_cubic_cc_numeric_for_db(cubic_capacity)
    if seating_capacity:
        try:
            seating_capacity = int(str(seating_capacity).strip())
        except (ValueError, TypeError):
            seating_capacity = None
    if num_cylinders:
        try:
            num_cylinders = int(str(num_cylinders).strip())
        except (ValueError, TypeError):
            num_cylinders = None
    if ex_showroom:
        try:
            ex_showroom = float(str(ex_showroom).replace(",", ""))
        except (ValueError, TypeError):
            ex_showroom = None

    if _is_two_wheeler_vehicle_type(vehicle_type):
        seating_capacity = 2
        body_type = "Open"
        num_cylinders = 1

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dr.rto_name, o.oem_name
                FROM sales_master sm
                JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
                LEFT JOIN oem_ref o ON o.oem_id = dr.oem_id
                WHERE sm.vehicle_id = %s
                ORDER BY sm.sales_id DESC NULLS LAST
                LIMIT 1
                """,
                (vehicle_id,),
            )
            drow = cur.fetchone()
            place_reg: str | None = None
            oem_n: str | None = None
            if drow:
                if isinstance(drow, dict):
                    r = str(drow.get("rto_name") or "").strip()
                    o = str(drow.get("oem_name") or "").strip()
                else:
                    r = str(drow[0] or "").strip()
                    o = str(drow[1] or "").strip()
                if r:
                    place_reg = r[:128]
                if o:
                    oem_n = o[:64]

            sql = """
                UPDATE vehicle_master SET
                    chassis = COALESCE(%s, chassis),
                    engine = COALESCE(%s, engine),
                    key_num = COALESCE(%s, key_num),
                    model = COALESCE(%s, model),
                    colour = COALESCE(%s, colour),
                    variant = COALESCE(%s, variant),
                    cubic_capacity = COALESCE(%s, cubic_capacity),
                    seating_capacity = COALESCE(%s, seating_capacity),
                    body_type = COALESCE(%s, body_type),
                    vehicle_type = COALESCE(%s, vehicle_type),
                    num_cylinders = COALESCE(%s, num_cylinders),
                    year_of_mfg = COALESCE(%s, year_of_mfg),
                    vehicle_ex_showroom_price = COALESCE(%s, vehicle_ex_showroom_price),
                    place_of_registeration = COALESCE(%s, place_of_registeration),
                    oem_name = COALESCE(%s, oem_name)
                WHERE vehicle_id = %s
                """
            params = (
                chassis,
                engine,
                key_num,
                model,
                colour,
                variant,
                cubic_capacity,
                seating_capacity,
                body_type,
                vehicle_type,
                num_cylinders,
                year_of_mfg,
                ex_showroom,
                place_reg,
                oem_n,
                vehicle_id,
            )
            try:
                cur.execute(sql, params)
            except Exception as exc:
                msg = str(exc).lower()
                if "variant" in msg and ("column" in msg or "undefined" in msg):
                    sql_no_var = """
                UPDATE vehicle_master SET
                    chassis = COALESCE(%s, chassis),
                    engine = COALESCE(%s, engine),
                    key_num = COALESCE(%s, key_num),
                    model = COALESCE(%s, model),
                    colour = COALESCE(%s, colour),
                    cubic_capacity = COALESCE(%s, cubic_capacity),
                    seating_capacity = COALESCE(%s, seating_capacity),
                    body_type = COALESCE(%s, body_type),
                    vehicle_type = COALESCE(%s, vehicle_type),
                    num_cylinders = COALESCE(%s, num_cylinders),
                    year_of_mfg = COALESCE(%s, year_of_mfg),
                    vehicle_ex_showroom_price = COALESCE(%s, vehicle_ex_showroom_price),
                    place_of_registeration = COALESCE(%s, place_of_registeration),
                    oem_name = COALESCE(%s, oem_name)
                WHERE vehicle_id = %s
                """
                    cur.execute(
                        sql_no_var,
                        (
                            chassis,
                            engine,
                            key_num,
                            model,
                            colour,
                            cubic_capacity,
                            seating_capacity,
                            body_type,
                            vehicle_type,
                            num_cylinders,
                            year_of_mfg,
                            ex_showroom,
                            place_reg,
                            oem_n,
                            vehicle_id,
                        ),
                    )
                    logger.info(
                        "fill_dms: vehicle_master update without variant column (run DDL/alter/15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql)"
                    )
                else:
                    raise
            conn.commit()
            if cur.rowcount > 0:
                logger.info("fill_dms: updated vehicle_master vehicle_id=%s with DMS data", vehicle_id)
    finally:
        conn.close()


def update_sales_master_from_dms_scrape(customer_id: int, vehicle_id: int, vehicle_dict: dict) -> None:
    """
    Persist DMS-scraped **Order#**, **Invoice#**, and **Enquiry#** onto ``sales_master`` as each
    becomes available across **different Siebel stages** (enquiry / order / invoice — see **BRD §6.1d**).
    Uses ``COALESCE`` so non-null scraped values fill empty cells only when provided.
    Does **not** set ``vahan_application_id`` or ``rto_charges`` (Vahan / RTO queue).
    """
    order_n = (vehicle_dict.get("order_number") or "").strip() or None
    inv_n = (vehicle_dict.get("invoice_number") or "").strip() or None
    enq_n = (vehicle_dict.get("enquiry_number") or "").strip() or None
    if not order_n and not inv_n and not enq_n:
        return
    from app.db import get_connection

    sql = """
        UPDATE sales_master SET
            order_number = COALESCE(%s, order_number),
            invoice_number = COALESCE(%s, invoice_number),
            enquiry_number = COALESCE(%s, enquiry_number)
        WHERE customer_id = %s AND vehicle_id = %s
        """
    sql_no_enq = """
        UPDATE sales_master SET
            order_number = COALESCE(%s, order_number),
            invoice_number = COALESCE(%s, invoice_number)
        WHERE customer_id = %s AND vehicle_id = %s
        """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, (order_n, inv_n, enq_n, customer_id, vehicle_id))
            except Exception as exc:
                msg = str(exc).lower()
                if "enquiry_number" in msg and ("column" in msg or "undefined" in msg):
                    cur.execute(sql_no_enq, (order_n, inv_n, customer_id, vehicle_id))
                    logger.info("fill_dms: enquiry_number column missing; saved order/invoice only")
                else:
                    raise
            conn.commit()
            if cur.rowcount > 0:
                logger.info(
                    "fill_dms: updated sales_master order/invoice/enquiry customer_id=%s vehicle_id=%s",
                    customer_id,
                    vehicle_id,
                )
            else:
                logger.warning(
                    "fill_dms: sales_master row not found for customer_id=%s vehicle_id=%s (order/invoice/enquiry not saved)",
                    customer_id,
                    vehicle_id,
                )
    finally:
        conn.close()


def _run_fill_dms_real_siebel_playwright(
    page,
    dms_values: dict,
    effective_subfolder: str,
    ocr_dir: Path,
    customer_id: int | None,
    vehicle_id: int | None,
    result: dict,
) -> None:
    """
    Hero Connect / Siebel Open UI: ``Playwright_Hero_DMS_fill``. When
    ``siebel_dms_playwright.SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`` is True, only the **Find Contact →
    All Enquiries** path runs, then stops. Otherwise (BRD §6.1a) **always** Contact Find (mobile + Go)
    first; vehicle list scrape + ``in_transit`` branch (receipt/PDI vs booking/allotment).

    ``dms_contact_path=skip_find`` in DB is **ignored** for real Siebel (operators still need Find so the
    correct contact context is loaded even when the customer already exists).

    Writes ``DMS_Form_Values`` trace for operators.
    """
    if not (DMS_REAL_URL_CONTACT or "").strip():
        result["error"] = (
            "DMS_MODE is real/siebel but DMS_REAL_URL_CONTACT is not set. "
            "Set the full GotoView URL (e.g. Buyer/CoBuyer) in backend/.env."
        )
        return

    mobile_phone = dms_values["mobile_phone"]
    landline = dms_values.get("landline") or ""
    addr = dms_values["address_line_1"]
    state = dms_values["state"]
    pin = dms_values["pin_code"]
    key_partial = dms_values["key_partial"]
    frame_partial = dms_values["frame_partial"]
    engine_partial = dms_values["engine_partial"]

    _write_dms_form_values(
        ocr_output_dir=ocr_dir,
        subfolder=effective_subfolder,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        dms_fill_row=dms_values.get("row"),
        customer_name=dms_values["customer_name"],
        mobile_number=mobile_phone,
        alt_phone_num=landline,
        address=addr,
        state=state,
        pin_code=pin,
        key_no=key_partial,
        frame_no=frame_partial,
        engine_no=engine_partial,
        relation_prefix=dms_values.get("relation_prefix") or "",
        care_of=dms_values.get("care_of") or "",
        customer_budget=DMS_TRACE_DEFAULT_CUSTOMER_BUDGET,
        finance_required=dms_values.get("finance_required") or "",
        financier_name=dms_values.get("financier_name") or "",
        dms_contact_path=dms_values.get("dms_contact_path") or "",
    )

    playwright_dms_log = Path(ocr_dir).resolve() / _safe_subfolder_name(effective_subfolder) / "Playwright_DMS.txt"

    urls = SiebelDmsUrls(
        contact=DMS_REAL_URL_CONTACT,
        vehicles=DMS_REAL_URL_VEHICLES,
        precheck=DMS_REAL_URL_PRECHECK,
        pdi=DMS_REAL_URL_PDI,
        vehicle=DMS_REAL_URL_VEHICLE,
        enquiry=DMS_REAL_URL_ENQUIRY,
        line_items=DMS_REAL_URL_LINE_ITEMS,
        reports=DMS_REAL_URL_REPORTS,
    )
    frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
    frag = Playwright_Hero_DMS_fill(
        page,
        dms_values,
        urls,
        action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
        nav_timeout_ms=DMS_SIEBEL_NAV_TIMEOUT_MS,
        content_frame_selector=frame_sel,
        mobile_aria_hints=list(DMS_SIEBEL_MOBILE_ARIA_HINTS),
        skip_contact_find=False,
        execution_log_path=playwright_dms_log,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
    )

    result["vehicle"] = frag.get("vehicle") or {}
    result["error"] = frag.get("error")
    result["dms_siebel_forms_filled"] = bool(frag.get("dms_siebel_forms_filled"))
    result["dms_siebel_notes"] = frag.get("dms_siebel_notes") or []
    result["dms_milestones"] = list(frag.get("dms_milestones") or [])
    result["dms_step_messages"] = list(frag.get("dms_step_messages") or [])
    _sort_dms_milestones(result)
    result["dms_automation_mode"] = "real"
    if result.get("error"):
        result["dms_real_note"] = None
    else:
        notes = "; ".join(result["dms_siebel_notes"]) if result.get("dms_siebel_notes") else ""
        result["dms_real_note"] = notes or "Siebel contact + vehicle automation finished."
    if vehicle_id and result.get("vehicle"):
        try:
            update_vehicle_master_from_dms(vehicle_id, result.get("vehicle") or {})
        except Exception as exc:
            logger.warning(
                "fill_dms_service: vehicle_master update failed (real Siebel) vehicle_id=%s: %s",
                vehicle_id,
                exc,
            )
    if customer_id and vehicle_id and result.get("vehicle") and not result.get("error"):
        try:
            update_sales_master_from_dms_scrape(customer_id, vehicle_id, result["vehicle"])
        except Exception as exc:
            logger.warning(
                "fill_dms_service: sales_master update failed (real Siebel) customer_id=%s vehicle_id=%s: %s",
                customer_id,
                vehicle_id,
                exc,
            )
    logger.info(
        "fill_dms_service: real Siebel flow done error=%s forms_filled=%s vehicle_keys=%s",
        bool(result.get("error")),
        result.get("dms_siebel_forms_filled"),
        list((result.get("vehicle") or {}).keys())[:8],
    )


def warm_dms_browser_session(dms_base_url: str) -> dict:
    """
    Pre-open or attach to the DMS browser (same path as Create Invoice: CDP reuse, launch, login wait)
    without running fill automation. Leaves the tab on the post-open screen so **Create Invoice** can
    start closer to ready when the operator clicks it later.
    """
    out: dict = {"success": False, "error": None}
    u = (dms_base_url or "").strip()
    if not u:
        out["error"] = "DMS_BASE_URL not set"
        return out
    if not dms_automation_is_real_siebel():
        out["error"] = (
            "DMS_MODE must be real, siebel, live, production, or hero "
            "(warm-browser applies to Siebel / Hero Connect only)."
        )
        return out
    try:
        page, open_error = get_or_open_site_page(
            u,
            "DMS",
            require_login_on_open=False,
        )
        if page is None:
            out["error"] = open_error or "Could not open DMS browser"
            return out
        _install_playwright_js_dialog_handler(page)
        out["success"] = True
    except PlaywrightTimeout as e:
        out["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: warm_dms_browser_session PlaywrightTimeout %s", e)
    except Exception as e:
        out["error"] = str(e)
        logger.warning("fill_dms_service: warm_dms_browser_session %s", e)
    return out


def run_fill_dms_only(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    dealer_id: int | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    staging_payload: dict | None = None,
    staging_id: str | None = None,
) -> dict:
    """
    Run DMS steps via Hero Connect / Siebel Open UI (``DMS_MODE`` real/siebel/live/production/hero and
    ``DMS_REAL_URL_*`` in ``backend/.env``). Static training HTML is not supported.

    Separate Playwright session. Returns vehicle, pdfs_saved, error.
    """
    result: dict = {
        "vehicle": {},
        "pdfs_saved": [],
        "error": None,
        "dms_milestones": [],
        "dms_step_messages": [],
    }
    try:
        _ensure_hero_oem_for_fill_dms(dealer_id)
    except Exception as e:
        result["error"] = str(e)
        return result
    if not dms_base_url:
        result["error"] = "DMS_BASE_URL not set"
        return result
    ocr_dir = Path(ocr_output_dir or OCR_OUTPUT_DIR).resolve()
    try:
        dms_values = _build_dms_fill_values(
            customer_id,
            vehicle_id,
            subfolder,
            staging_payload=staging_payload,
        )
    except Exception as e:
        result["error"] = str(e)
        return result
    effective_subfolder = dms_values.get("subfolder") or subfolder
    subfolder_path = uploads_dir / effective_subfolder
    subfolder_path.mkdir(parents=True, exist_ok=True)

    if not dms_automation_is_real_siebel():
        result["error"] = (
            "DMS_MODE must be real, siebel, live, production, or hero (static training DMS was removed). "
            "Set DMS_MODE=real and DMS_REAL_URL_CONTACT in backend/.env."
        )
        return result

    try:
        logger.info("fill_dms_service: run_fill_dms_only starting mode=real dms=%s", dms_base_url[:50])
        page, open_error = get_or_open_site_page(
            dms_base_url,
            "DMS",
            require_login_on_open=True,
        )
        if page is None:
            result["error"] = open_error
            return result

        _install_playwright_js_dialog_handler(page)

        # Operator-controlled step: Playwright must not click "Create Invoice".
        # If that action is pending on screen, instruct operator and stop.
        if _requires_operator_create_invoice(page):
            result["error"] = "Please click Create Invoice manually in DMS, then press Fill DMS again."
            return result

        _run_fill_dms_real_siebel_playwright(
            page,
            dms_values,
            effective_subfolder,
            ocr_dir,
            customer_id,
            vehicle_id,
            result,
        )
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: run_fill_dms_only PlaywrightTimeout %s", e)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("fill_dms_service: run_fill_dms_only exception %s", e)

    try:
        _write_data_from_dms(ocr_dir, effective_subfolder, dms_values.get("customer_export") or {}, result.get("vehicle") or {})
    except Exception as e:
        result["error"] = (result.get("error") or "") + f"; DMS file write: {e!s}"

    scraped_final = result.get("vehicle") or {}
    has_v = _dms_scrape_has_vehicle_row(scraped_final)
    skip_nv = result.get("dms_automation_mode") == "real" and not result.get("dms_siebel_forms_filled")

    if (
        staging_payload is not None
        and (staging_id or "").strip()
        and not result.get("error")
        and (has_v or skip_nv)
    ):
        try:
            cid_c, vid_c = commit_staging_masters_and_finalize_row(
                staging_id=staging_id or "",
                merged_payload=staging_payload,
            )
            update_vehicle_master_from_dms(vid_c, scraped_final)
            update_sales_master_from_dms_scrape(cid_c, vid_c, scraped_final)
            result["committed_customer_id"] = cid_c
            result["committed_vehicle_id"] = vid_c
        except Exception as commit_exc:
            logger.warning("fill_dms_service: staging master commit failed: %s", commit_exc)
            result["error"] = f"Database commit after DMS failed: {commit_exc!s}"

    if customer_id and vehicle_id and result.get("vehicle") and not result.get("error"):
        try:
            update_sales_master_from_dms_scrape(customer_id, vehicle_id, result["vehicle"])
        except Exception as exc:
            logger.warning(
                "fill_dms_service: sales_master order/invoice update failed customer_id=%s vehicle_id=%s: %s",
                customer_id,
                vehicle_id,
                exc,
            )
    return result


def run_fill_vahan_only(
    vahan_base_url: str,
    rto_dealer_id: str,
    customer_name: str,
    chassis_no: str,
    vehicle_model: str,
    vehicle_colour: str,
    fuel_type: str,
    year_of_mfg: str,
    vehicle_price: float,
    ocr_output_dir: Path | None = None,
    subfolder: str | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
) -> dict:
    """
    Run only Vahan step: fill registration form, submit, scrape application_id and rto_fees.
    Separate Playwright process (new browser). Returns application_id, rto_fees, error.
    """
    result: dict = {"application_id": None, "rto_fees": None, "added_to_cart": False, "error": None}
    if not vahan_base_url or not vahan_base_url.strip():
        result["error"] = "vahan_base_url required"
        return result
    try:
        logger.info("fill_dms_service: run_fill_vahan_only starting")
        page, open_error = get_or_open_site_page(vahan_base_url, "Vahan", require_login_on_open=False)
        if page is None:
            result["error"] = open_error
            return result
        vahan_values = _build_vahan_fill_values(customer_id, vehicle_id, subfolder)
        app_id, fees = _fill_vahan_and_scrape(
            page,
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=vahan_values["rto_dealer_id"],
            customer_name=vahan_values["customer_name"],
            chassis_no=vahan_values["chassis_no"],
            vehicle_model=vahan_values["vehicle_model"],
            vehicle_colour=vahan_values["vehicle_colour"],
            fuel_type=vahan_values["fuel_type"],
            year_of_mfg=vahan_values["year_of_mfg"],
            vehicle_price=vahan_values["vehicle_price"],
        )
        result.update(
            {
                "application_id": app_id,
                "rto_fees": fees,
                "added_to_cart": False,
                "subfolder": vahan_values.get("subfolder") or subfolder,
            }
        )
        if ocr_output_dir is not None and (vahan_values.get("subfolder") or subfolder):
            _write_vahan_form_values(
                ocr_output_dir=ocr_output_dir,
                subfolder=vahan_values.get("subfolder") or subfolder,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                rto_dealer_id=vahan_values["rto_dealer_id"],
                customer_name=vahan_values["customer_name"],
                chassis_no=vahan_values["chassis_no"],
                vehicle_model=vahan_values["vehicle_model"],
                vehicle_colour=vahan_values["vehicle_colour"],
                fuel_type=vahan_values["fuel_type"],
                year_of_mfg=vahan_values["year_of_mfg"],
                vehicle_price=vahan_values["vehicle_price"],
                application_id=app_id,
                rto_fees=fees,
            )
    except PlaywrightTimeout as e:
        result["error"] = f"Timeout: {e!s}"
        logger.warning("fill_dms_service: run_fill_vahan_only PlaywrightTimeout %s", e)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("fill_dms_service: run_fill_vahan_only exception %s", e)
    return result


def run_fill_dms(
    dms_base_url: str,
    subfolder: str,
    customer: dict,
    vehicle: dict,
    login_user: str,
    login_password: str,
    uploads_dir: Path,
    ocr_output_dir: Path | None = None,
    vahan_base_url: str | None = None,
    rto_dealer_id: str | None = None,
    dealer_id: int | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    headless: bool | None = None,
    staging_payload: dict | None = None,
    staging_id: str | None = None,
) -> dict:
    """
    Run Playwright: same DMS flow as `run_fill_dms_only` (Siebel). Optional ``vahan_base_url`` triggers
    ``run_fill_vahan_only``, which is not implemented for production VAHAN (returns ``NotImplementedError``).
    Writes pulled data to ocr_output_dir/subfolder/Data from DMS.txt.
    Returns dict with vehicle details (key_num, frame_num, vehicle_price / ex-showroom, order_number, invoice_number, …),
    optional application_id, rto_fees, and any error. When ``staging_payload`` is set, DMS fill avoids
    master reads; scrape persistence to ``vehicle_master`` / ``sales_master`` runs only when IDs are set.
    """
    result = run_fill_dms_only(
        dms_base_url=dms_base_url,
        subfolder=subfolder,
        customer=customer,
        vehicle=vehicle,
        login_user=login_user,
        login_password=login_password,
        uploads_dir=uploads_dir,
        ocr_output_dir=ocr_output_dir,
        dealer_id=dealer_id,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        staging_payload=staging_payload,
        staging_id=staging_id,
    )
    cid_eff = customer_id if customer_id is not None else result.get("committed_customer_id")
    vid_eff = vehicle_id if vehicle_id is not None else result.get("committed_vehicle_id")
    dms_mode = result.get("dms_automation_mode")
    siebel_ok = result.get("dms_siebel_forms_filled")
    milestones = list(result.get("dms_milestones") or [])
    step_msgs = list(result.get("dms_step_messages") or [])

    if result.get("error"):
        return {
            "vehicle": result.get("vehicle") or {},
            "pdfs_saved": result.get("pdfs_saved") or [],
            "application_id": None,
            "rto_fees": None,
            "error": result.get("error"),
            "dms_automation_mode": dms_mode,
            "dms_siebel_forms_filled": siebel_ok,
            "dms_milestones": milestones,
            "dms_step_messages": step_msgs,
            "committed_customer_id": result.get("committed_customer_id"),
            "committed_vehicle_id": result.get("committed_vehicle_id"),
        }

    if vahan_base_url and vahan_base_url.strip():
        vahan_result = run_fill_vahan_only(
            vahan_base_url=vahan_base_url.strip(),
            rto_dealer_id=rto_dealer_id or "",
            customer_name=str((customer or {}).get("name") or ""),
            chassis_no=str((result.get("vehicle") or {}).get("frame_num") or (vehicle or {}).get("frame_no") or ""),
            vehicle_model=str((result.get("vehicle") or {}).get("model") or ""),
            vehicle_colour=str((result.get("vehicle") or {}).get("color") or ""),
            fuel_type=str((result.get("vehicle") or {}).get("fuel_type") or ""),
            year_of_mfg=str((result.get("vehicle") or {}).get("year_of_mfg") or ""),
            vehicle_price=_parse_vehicle_price(result.get("vehicle") or {}),
            ocr_output_dir=ocr_output_dir,
            subfolder=subfolder,
            customer_id=cid_eff,
            vehicle_id=vid_eff,
        )
        if vahan_result.get("error"):
            return {
                "vehicle": result.get("vehicle") or {},
                "pdfs_saved": result.get("pdfs_saved") or [],
                "application_id": None,
                "rto_fees": None,
                "error": vahan_result.get("error"),
                "dms_automation_mode": dms_mode,
                "dms_siebel_forms_filled": siebel_ok,
                "dms_milestones": milestones,
                "dms_step_messages": step_msgs,
                "committed_customer_id": result.get("committed_customer_id"),
                "committed_vehicle_id": result.get("committed_vehicle_id"),
            }
        return {
            "vehicle": result.get("vehicle") or {},
            "pdfs_saved": result.get("pdfs_saved") or [],
            "application_id": vahan_result.get("application_id"),
            "rto_fees": vahan_result.get("rto_fees"),
            "error": None,
            "dms_automation_mode": dms_mode,
            "dms_siebel_forms_filled": siebel_ok,
            "dms_milestones": milestones,
            "dms_step_messages": step_msgs,
            "committed_customer_id": result.get("committed_customer_id"),
            "committed_vehicle_id": result.get("committed_vehicle_id"),
        }

    return {
        "vehicle": result.get("vehicle") or {},
        "pdfs_saved": result.get("pdfs_saved") or [],
        "application_id": None,
        "rto_fees": None,
        "error": None,
        "dms_automation_mode": dms_mode,
        "dms_siebel_forms_filled": siebel_ok,
        "dms_milestones": milestones,
        "dms_step_messages": step_msgs,
        "committed_customer_id": result.get("committed_customer_id"),
        "committed_vehicle_id": result.get("committed_vehicle_id"),
    }
