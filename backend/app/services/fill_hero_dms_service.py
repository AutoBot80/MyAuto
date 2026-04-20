"""
Fill DMS flow using Playwright: Hero Connect / Siebel (``DMS_MODE=real`` and ``DMS_REAL_URL_*``).
Vahan automation was moved to ``fill_rto_service.py`` (called via ``rto_payment_service`` batch loop).
Uses Chromium (faster launch). Requires: pip install playwright && playwright install chromium.
Uses headed browser by default (set DMS_PLAYWRIGHT_HEADED=false for headless).
Writes pulled data to ocr_output/subfolder/Data from DMS.txt for consistency with other OCR outputs.

**Browser lifetime:** This module never calls ``Browser.close()`` or ``Playwright.stop()`` for operator
sessions (including on API process exit and thread switches). Edge/Chrome stays open for the operator;
stale handles are moved to a retain list so GC does not implicitly close windows.

**JS dialogs:** ``run_fill_dms_only`` installs a per-tab ``dialog`` listener so short-lived Siebel
``alert``/``confirm`` dialogs do not crash the Playwright Node driver (CDP race *No dialog is showing*).
"""
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from app.config import (
    DEALER_ID,
    HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE,
    HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER,
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
    get_ocr_output_dir,
    get_uploaded_scans_sale_folder,
    get_uploaded_scans_sale_subfolder_leaf,
)
from app.services.hero_dms_shared_utilities import (
    SiebelDmsUrls,
    _sort_milestone_labels,
    _try_click_siebel_save,
    _ts_ist_iso,
    _write_playwright_vehicle_master_section,
)
from app.services.hero_dms_playwright_vehicle import (
    prepare_vehicle,
)
from app.services.hero_dms_playwright_customer import (
    _validate_contact_find_first_name,
)
from app.services.hero_dms_db_service import persist_masters_after_create_order
from app.services.hero_dms_prepare_customer import prepare_customer
from app.services.hero_dms_playwright_invoice import prepare_order
from app.repositories import form_dms as form_dms_repo
from app.repositories.add_sales_staging import merge_staging_payload_on_cursor
from app.db import get_connection
from app.services.customer_address_infer import enrich_customer_address_from_freeform
from app.services.dms_relation_prefix import compute_dms_relation_prefix
from app.services.hero_dms_db_service import persist_staging_masters_after_invoice
from app.services.hero_dms_reports_service import run_hero_dms_reports
from app.services.handle_browser_opening import get_or_open_site_page
from app.services.utility_functions import (
    clean_text as _clean_text,
    require_customer_vehicle_ids as _require_customer_vehicle_ids,
    safe_subfolder_name as _safe_subfolder_name,
)

logger = logging.getLogger(__name__)

_PLAYWRIGHT_DMS_LOG_TZ = ZoneInfo("Asia/Kolkata")


def playwright_dms_execution_log_filename() -> str:
    """
    Per-run Siebel trace file: ``Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt`` (IST).
    New file each invocation so retries keep a sequence under the same OCR subfolder.
    """
    stamp = datetime.now(_PLAYWRIGHT_DMS_LOG_TZ).strftime("%d%m%Y_%H%M%S")
    return f"Playwright_DMS_{stamp}.txt"


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


def invoice_number_ready_for_master_commit(vehicle_dict: dict | None) -> bool:
    """
    True when Siebel scrape has an **Invoice#**, or when ``ENVIRONMENT`` is not prod/production
    (masters insert uses ``HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER`` — no real Create Invoice in UI).
    """
    vd = vehicle_dict or {}
    if str(vd.get("invoice_number") or "").strip():
        return True
    return not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE


def fetch_three_masters_snapshot_for_log(customer_id: int, vehicle_id: int) -> dict[str, Any]:
    """
    Load **customer_master**, **vehicle_master**, and the **sales_master** row for the pair — for
    appending to ``Playwright_DMS_*.txt`` after a successful master commit.
    """
    out: dict[str, Any] = {"customer_master": None, "vehicle_master": None, "sales_master": None}
    cid = int(customer_id)
    vid = int(vehicle_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customer_master WHERE customer_id = %s", (cid,))
            r = cur.fetchone()
            if r:
                out["customer_master"] = dict(r)
            cur.execute("SELECT * FROM vehicle_master WHERE vehicle_id = %s", (vid,))
            r = cur.fetchone()
            if r:
                out["vehicle_master"] = dict(r)
            cur.execute(
                """
                SELECT * FROM sales_master
                WHERE customer_id = %s AND vehicle_id = %s
                ORDER BY sales_id DESC
                LIMIT 1
                """,
                (cid, vid),
            )
            r = cur.fetchone()
            if r:
                out["sales_master"] = dict(r)
    return out


def append_playwright_dms_masters_committed_log(
    log_path: Path | str | None,
    *,
    customer_id: int,
    vehicle_id: int,
) -> None:
    """Append a JSON snapshot of the three master rows to the per-run Playwright DMS execution log."""
    if not log_path:
        return
    p = Path(str(log_path))
    if not p.is_file():
        logger.warning(
            "fill_dms_service: Playwright DMS log not found for masters snapshot: %s",
            p,
        )
        return
    try:
        snap = fetch_three_masters_snapshot_for_log(customer_id, vehicle_id)
    except Exception as exc:
        logger.warning(
            "fill_dms_service: could not load masters for Playwright log snapshot: %s",
            exc,
        )
        return
    try:
        with p.open("a", encoding="utf-8") as fp:
            fp.write(
                "\n--- masters_committed (customer_master / vehicle_master / sales_master after DB write) ---\n"
            )
            fp.write(json.dumps(snap, indent=2, default=str))
            fp.write("\n")
            fp.flush()
    except OSError as exc:
        logger.warning("fill_dms_service: could not append masters snapshot to %s: %s", p, exc)


def _merge_staging_payload_with_scrape_for_commit(staging_payload: dict, scraped: dict) -> dict:
    """Deep-merge Fill DMS scrape into staging payload for one post-invoice ``upsert_customer_vehicle_sales`` (no follow-up UPDATE)."""
    merged = copy.deepcopy(staging_payload)
    s = dict(scraped or {})
    veh = merged.setdefault("vehicle", {})
    fc = (s.get("full_chassis") or s.get("chassis") or s.get("frame_num") or "").strip()
    if fc:
        veh["frame_no"] = fc[:64]
    fe = (s.get("full_engine") or s.get("engine_num") or s.get("engine") or "").strip()
    if fe:
        veh["engine_no"] = fe[:64]
    fk = (s.get("key_num") or s.get("raw_key_num") or "").strip()
    if fk:
        veh["key_no"] = fk[:32]
    fb = (s.get("battery") or "").strip()
    if fb:
        veh["battery_no"] = fb[:64]
    for k in ("order_number", "invoice_number", "enquiry_number"):
        v = s.get(k)
        if v is not None and str(v).strip():
            veh[k] = str(v).strip()
    if not str(veh.get("invoice_number") or "").strip() and not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
        veh["invoice_number"] = HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER
    fn = (s.get("financier_name") or "").strip()
    if fn:
        cust = merged.setdefault("customer", {})
        if isinstance(cust, dict):
            cust["financier"] = fn[:255]
    return merged


def _split_name(full_name: str | None) -> tuple[str, str]:
    if not full_name or not full_name.strip():
        return "", ""
    parts = full_name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0], "")


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name (one segment) for ocr_output and uploads."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _sales_file_location_varchar128(full_posix: str, dealer_id: int, safe_leaf: str) -> str:
    """``sales_master.file_location`` is varchar(128); shorten with a stable relative form if needed."""
    if len(full_posix) <= 128:
        return full_posix
    rel = (Path("ocr_output") / str(dealer_id) / safe_leaf).as_posix()
    if len(rel) <= 128:
        return rel
    return rel[:128]


def resolve_ocr_sale_folder_paths(
    dealer_id: int,
    dms_values: dict | None,
    *,
    file_location_override: str | None = None,
) -> tuple[str, str]:
    """
    Per-sale OCR folder: ``ocr_output/{dealer_id}/{mobile}_{ddmmyy}`` (same layout as Add Sales / pre-OCR).

    Leaf directory: ``dms_values['subfolder']`` when set, else ``{10-digit mobile}_{today as ddmmyy}`` (see **get_uploaded_scans_sale_subfolder_leaf**).
    Returns ``(customer_master.file_location, sales_master.file_location)`` as posix paths; the latter is ≤128 chars.
    Optional ``file_location_override`` is a full path, or a single segment treated as the leaf under the dealer OCR dir.
    """
    dv = dict(dms_values or {})
    ov = (file_location_override or "").strip()
    did = int(dealer_id)
    if ov:
        p = Path(ov)
        if p.is_absolute() or "/" in ov or "\\" in ov:
            full_path = p.expanduser().resolve().as_posix()
            safe_leaf = _safe_subfolder_name(p.name or "default")
        else:
            safe_leaf = _safe_subfolder_name(ov)
            full_path = (get_ocr_output_dir(did) / safe_leaf).resolve().as_posix()
    else:
        leaf = (dv.get("subfolder") or "").strip()
        if not leaf:
            leaf = get_uploaded_scans_sale_subfolder_leaf(str(dv.get("mobile_phone") or ""))
        safe_leaf = _safe_subfolder_name(leaf)
        full_path = (get_ocr_output_dir(did) / safe_leaf).resolve().as_posix()
    sales_loc = _sales_file_location_varchar128(full_path, did, safe_leaf)
    return full_path, sales_loc


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
        ("Financier (DMS display)", "financier_name"),
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


def _load_customer_profession_from_master(customer_id: int | None) -> str:
    """Profession from customer_master for Siebel Occupation LOV."""
    if customer_id is None:
        return ""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(BTRIM(profession), '') AS prof_val FROM customer_master WHERE customer_id = %s",
                    (customer_id,),
                )
                row = cur.fetchone()
                if not row:
                    return ""
                if isinstance(row, dict):
                    return _clean_text(row.get("prof_val"))
                try:
                    return _clean_text(row[0])
                except Exception:
                    return ""
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("fill_dms_service: customer_master profession lookup failed customer_id=%s: %s", customer_id, exc)
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
        profession_src = _clean_text(cust.get("profession"))[:16]
    else:
        row = _load_required_form_dms_row(customer_id, vehicle_id)
        gender_master = _load_customer_gender_from_master(customer_id)
        try:
            cid_for_aadhar = int(row.get("customer_id")) if row.get("customer_id") is not None else None
        except (TypeError, ValueError):
            cid_for_aadhar = None
        dob_src = _load_customer_dob_from_master(cid_for_aadhar)
        aadhar_src = _load_customer_aadhar_last4(cid_for_aadhar)
        profession_src = _load_customer_profession_from_master(cid_for_aadhar)

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
        "profession": profession_src,
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


def _vehicle_master_update_from_scrape_on_cursor(cur, vehicle_id: int, scraped: dict) -> None:
    """Run ``vehicle_master`` COALESCE update on an open cursor (no commit)."""
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _vehicle_master_update_from_scrape_on_cursor(cur, vehicle_id, scraped)
        conn.commit()
        logger.info("fill_dms: updated vehicle_master vehicle_id=%s with DMS data", vehicle_id)
    finally:
        conn.close()


def _sales_master_update_from_scrape_on_cursor(
    cur, customer_id: int, vehicle_id: int, vehicle_dict: dict
) -> int:
    """``sales_master`` COALESCE update for order/invoice/enquiry. Returns ``cursor.rowcount``."""
    order_n = (vehicle_dict.get("order_number") or "").strip() or None
    inv_n = (vehicle_dict.get("invoice_number") or "").strip() or None
    enq_n = (vehicle_dict.get("enquiry_number") or "").strip() or None
    if not order_n and not inv_n and not enq_n:
        return 0

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
    try:
        cur.execute(sql, (order_n, inv_n, enq_n, customer_id, vehicle_id))
    except Exception as exc:
        msg = str(exc).lower()
        if "enquiry_number" in msg and ("column" in msg or "undefined" in msg):
            cur.execute(sql_no_enq, (order_n, inv_n, customer_id, vehicle_id))
            logger.info("fill_dms: enquiry_number column missing; saved order/invoice only")
        else:
            raise
    return int(getattr(cur, "rowcount", -1) or 0)


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

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            rc = _sales_master_update_from_scrape_on_cursor(cur, customer_id, vehicle_id, vehicle_dict)
            conn.commit()
            if rc > 0:
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


def insert_dms_masters_from_siebel_scrape(
    dms_values: dict,
    vehicle_dict: dict,
    *,
    collated_customer_fields: dict | None = None,
    file_location: str | None = None,
    dealer_id: int | None = None,
) -> tuple[int, int, int]:
    """
    Single transaction: **INSERT** ``customer_master``, ``vehicle_master``, and ``sales_master`` when
    no prior rows exist (Siebel-only / staging-less Fill DMS). Before commit, **UPDATE**
    ``vehicle_inventory_master.sold_date`` to today (dd/mm/yyyy) when trimmed chassis/engine match
    full scraped frame/engine (not ``raw_*``).     Call after Create Invoice in DMS when **Invoice#** is scraped; in non-production, **Invoice#**
    may be ``HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER`` when the UI never issued an invoice
    (see ``invoice_number_ready_for_master_commit``).

    Requires name, mobile, and Aadhar last-4 derivable from ``dms_values`` and/or ``collated_customer_fields``.

    ``customer_master.file_location`` / ``sales_master.file_location`` are set to the per-sale OCR folder
    ``ocr_output/{dealer_id}/{mobile}_{ddmmyy}`` (see ``resolve_ocr_sale_folder_paths``), unless
    ``file_location`` overrides (full path or leaf segment).

    Returns ``(customer_id, vehicle_id, sales_id)``.
    """
    from psycopg2 import IntegrityError

    from app.repositories.vehicle_inventory import (
        today_dd_mm_yyyy,
        update_sold_date_by_chassis_engine_on_cursor,
    )

    dv = dict(dms_values or {})
    cf = dict(collated_customer_fields or {})
    vd = dict(vehicle_dict or {})
    sale_dealer_id = int(dealer_id) if dealer_id is not None else int(DEALER_ID)
    loc_full, loc_sales = resolve_ocr_sale_folder_paths(
        sale_dealer_id,
        dv,
        file_location_override=(file_location or "").strip() or None,
    )

    name = (cf.get("name") or dv.get("customer_name") or "").strip()
    mob_raw = cf.get("mobile_number")
    if mob_raw is None:
        dig_m = re.sub(r"\D", "", str(dv.get("mobile_phone") or ""))
        mob_raw = int(dig_m[-10:]) if len(dig_m) >= 10 else None
    else:
        try:
            mob_raw = int(str(mob_raw).strip().replace(" ", ""))
        except (ValueError, TypeError):
            mob_raw = None

    dig_cf = "".join(c for c in str(cf.get("aadhar") or "") if c.isdigit())
    dig_dv = "".join(c for c in str(dv.get("aadhar_id") or "") if c.isdigit())
    _dig = dig_cf if len(dig_cf) >= 4 else dig_dv
    aad = _dig[-4:] if len(_dig) >= 4 else None

    if not name or mob_raw is None or not aad or len(aad) < 4:
        raise ValueError(
            "Cannot INSERT customer_master: need customer name, 10-digit mobile, and Aadhar last 4 "
            "(from DMS fill values and/or Siebel collate fields)."
        )

    address = (cf.get("address") or dv.get("address_line_1") or "").strip() or None
    pin = (cf.get("pin") or dv.get("pin_code") or "").strip()[:6] or None
    city = (cf.get("city") or dv.get("city") or "").strip() or None
    state = (cf.get("state") or dv.get("state") or "").strip() or None
    alt_phone = (cf.get("alt_phone_num") or dv.get("landline") or "").strip()[:16] or None
    profession = (cf.get("profession") or dv.get("profession") or "").strip()[:16] or None
    financier = (cf.get("financier") or dv.get("financier_name") or "").strip()[:255] or None
    marital_status = (cf.get("marital_status") or dv.get("marital_status") or "").strip()[:32] or None
    care_of = (cf.get("care_of") or dv.get("care_of") or "").strip()[:255] or None
    gender = (cf.get("gender") or dv.get("gender") or "").strip()[:8] or None
    dob = (cf.get("date_of_birth") or dv.get("date_of_birth") or "").strip()[:20] or None
    dcp = (cf.get("dms_contact_path") or dv.get("dms_contact_path") or "found").strip().lower()
    if dcp not in ("found", "new_enquiry", "skip_find"):
        dcp = "found"
    dms_cid = (cf.get("dms_contact_id") or "").strip()[:128] or None

    drp = (cf.get("dms_relation_prefix") or "").strip()[:8] or None
    if not drp:
        drp = compute_dms_relation_prefix(address or "", gender)[:8]

    def _strip_or_none(k: str) -> str | None:
        v = (vd.get(k) or "").strip()
        return v or None

    chassis = _strip_or_none("full_chassis") or _strip_or_none("frame_num") or _strip_or_none("chassis")
    engine = _strip_or_none("full_engine") or _strip_or_none("engine_num") or _strip_or_none("engine")
    key_num = _strip_or_none("key_num") or _strip_or_none("raw_key_num")
    model = _strip_or_none("model")
    colour = _strip_or_none("color") or _strip_or_none("colour")
    variant_raw = _strip_or_none("variant")
    variant = (variant_raw[:64] if variant_raw else None)
    cubic_capacity = vd.get("cubic_capacity")
    seating_capacity = vd.get("seating_capacity")
    body_type = (vd.get("body_type") or "").strip() or None
    vehicle_type = _normalize_vehicle_type_for_db(vd.get("vehicle_type"))
    num_cylinders = vd.get("num_cylinders")
    year_of_mfg = _parse_vehicle_year_int_for_db(vd.get("year_of_mfg"))
    if year_of_mfg is None:
        year_of_mfg = _parse_vehicle_year_int_for_db(vd.get("dispatch_year"))
    ex_showroom = vd.get("vehicle_price")
    if ex_showroom is None:
        ex_showroom = vd.get("vehicle_ex_showroom_cost")
    if ex_showroom is None:
        ex_showroom = vd.get("total_amount")
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

    raw_f = (str(dv.get("frame_partial") or "").strip() or chassis or None)
    raw_e = (str(dv.get("engine_partial") or "").strip() or engine or None)
    raw_k = (str(dv.get("key_partial") or "").strip() or key_num or None)
    battery = _strip_or_none("battery") or (str(dv.get("battery_partial") or "").strip() or None)

    order_n = (vd.get("order_number") or "").strip() or None
    inv_n = (vd.get("invoice_number") or "").strip() or None
    enq_n = (vd.get("enquiry_number") or "").strip() or None
    if not inv_n and not HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
        inv_n = HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dr.rto_name, o.oem_name
                FROM dealer_ref dr
                LEFT JOIN oem_ref o ON o.oem_id = dr.oem_id
                WHERE dr.dealer_id = %s
                LIMIT 1
                """,
                (sale_dealer_id,),
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

            cur.execute(
                """
                INSERT INTO customer_master (
                    aadhar, name, address, pin, city, state, mobile_number,
                    alt_phone_num,
                    profession, financier, marital_status,
                    dms_relation_prefix, dms_contact_path, care_of,
                    file_location, gender, date_of_birth, dms_contact_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (aadhar, mobile_number) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, customer_master.name),
                    address = COALESCE(EXCLUDED.address, customer_master.address),
                    pin = COALESCE(EXCLUDED.pin, customer_master.pin),
                    city = COALESCE(EXCLUDED.city, customer_master.city),
                    state = COALESCE(EXCLUDED.state, customer_master.state),
                    alt_phone_num = COALESCE(EXCLUDED.alt_phone_num, customer_master.alt_phone_num),
                    profession = COALESCE(EXCLUDED.profession, customer_master.profession),
                    financier = COALESCE(EXCLUDED.financier, customer_master.financier),
                    marital_status = COALESCE(EXCLUDED.marital_status, customer_master.marital_status),
                    dms_relation_prefix = COALESCE(EXCLUDED.dms_relation_prefix, customer_master.dms_relation_prefix),
                    dms_contact_path = COALESCE(EXCLUDED.dms_contact_path, customer_master.dms_contact_path),
                    care_of = COALESCE(EXCLUDED.care_of, customer_master.care_of),
                    file_location = COALESCE(EXCLUDED.file_location, customer_master.file_location),
                    gender = COALESCE(EXCLUDED.gender, customer_master.gender),
                    date_of_birth = COALESCE(EXCLUDED.date_of_birth, customer_master.date_of_birth),
                    dms_contact_id = COALESCE(EXCLUDED.dms_contact_id, customer_master.dms_contact_id)
                RETURNING customer_id
                """,
                (
                    aad,
                    name,
                    address,
                    pin,
                    city,
                    state,
                    mob_raw,
                    alt_phone,
                    profession,
                    financier,
                    marital_status,
                    drp,
                    dcp,
                    care_of,
                    loc_full,
                    gender,
                    dob,
                    dms_cid,
                ),
            )
            cid_row = cur.fetchone()
            customer_id = int(cid_row["customer_id"] if isinstance(cid_row, dict) else cid_row[0])

            _vehicle_coalesce_set = """
                    chassis = COALESCE(EXCLUDED.chassis, vehicle_master.chassis),
                    engine = COALESCE(EXCLUDED.engine, vehicle_master.engine),
                    key_num = COALESCE(EXCLUDED.key_num, vehicle_master.key_num),
                    battery = COALESCE(EXCLUDED.battery, vehicle_master.battery),
                    model = COALESCE(EXCLUDED.model, vehicle_master.model),
                    colour = COALESCE(EXCLUDED.colour, vehicle_master.colour),
                    cubic_capacity = COALESCE(EXCLUDED.cubic_capacity, vehicle_master.cubic_capacity),
                    seating_capacity = COALESCE(EXCLUDED.seating_capacity, vehicle_master.seating_capacity),
                    body_type = COALESCE(EXCLUDED.body_type, vehicle_master.body_type),
                    vehicle_type = COALESCE(EXCLUDED.vehicle_type, vehicle_master.vehicle_type),
                    num_cylinders = COALESCE(EXCLUDED.num_cylinders, vehicle_master.num_cylinders),
                    year_of_mfg = COALESCE(EXCLUDED.year_of_mfg, vehicle_master.year_of_mfg),
                    vehicle_ex_showroom_price = COALESCE(EXCLUDED.vehicle_ex_showroom_price, vehicle_master.vehicle_ex_showroom_price),
                    place_of_registeration = COALESCE(EXCLUDED.place_of_registeration, vehicle_master.place_of_registeration),
                    oem_name = COALESCE(EXCLUDED.oem_name, vehicle_master.oem_name)
            """
            sql_v = f"""
                INSERT INTO vehicle_master (
                    chassis, engine, key_num, battery,
                    raw_frame_num, raw_engine_num, raw_key_num,
                    model, colour, variant,
                    cubic_capacity, seating_capacity, body_type, vehicle_type, num_cylinders, year_of_mfg,
                    vehicle_ex_showroom_price, place_of_registeration, oem_name
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (raw_frame_num, raw_engine_num, raw_key_num) DO UPDATE SET
                    {_vehicle_coalesce_set},
                    variant = COALESCE(EXCLUDED.variant, vehicle_master.variant)
                RETURNING vehicle_id
                """
            params_v = (
                chassis,
                engine,
                key_num,
                battery,
                raw_f,
                raw_e,
                raw_k,
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
            )
            try:
                cur.execute(sql_v, params_v)
            except Exception as exc:
                msg = str(exc).lower()
                if "variant" in msg and ("column" in msg or "undefined" in msg):
                    sql_v_nv = f"""
                INSERT INTO vehicle_master (
                    chassis, engine, key_num, battery,
                    raw_frame_num, raw_engine_num, raw_key_num,
                    model, colour,
                    cubic_capacity, seating_capacity, body_type, vehicle_type, num_cylinders, year_of_mfg,
                    vehicle_ex_showroom_price, place_of_registeration, oem_name
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (raw_frame_num, raw_engine_num, raw_key_num) DO UPDATE SET
                    {_vehicle_coalesce_set}
                RETURNING vehicle_id
                """
                    cur.execute(
                        sql_v_nv,
                        (
                            chassis,
                            engine,
                            key_num,
                            battery,
                            raw_f,
                            raw_e,
                            raw_k,
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
                        ),
                    )
                    logger.info(
                        "fill_dms: vehicle_master upsert without variant column (DDL/alter/15a_vehicle_master_variant_vin_unique_drop_dms_sku.sql)"
                    )
                else:
                    raise
            vid_row = cur.fetchone()
            vehicle_id = int(vid_row["vehicle_id"] if isinstance(vid_row, dict) else vid_row[0])

            sql_s = """
                INSERT INTO sales_master (
                    customer_id, vehicle_id, dealer_id, file_location,
                    order_number, invoice_number, enquiry_number
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id, vehicle_id) DO UPDATE SET
                    dealer_id = COALESCE(EXCLUDED.dealer_id, sales_master.dealer_id),
                    file_location = COALESCE(EXCLUDED.file_location, sales_master.file_location),
                    order_number = COALESCE(EXCLUDED.order_number, sales_master.order_number),
                    invoice_number = COALESCE(EXCLUDED.invoice_number, sales_master.invoice_number),
                    enquiry_number = COALESCE(EXCLUDED.enquiry_number, sales_master.enquiry_number)
                RETURNING sales_id
                """
            try:
                cur.execute(
                    sql_s,
                    (customer_id, vehicle_id, sale_dealer_id, loc_sales, order_n, inv_n, enq_n),
                )
            except Exception as exc:
                msg = str(exc).lower()
                if "enquiry_number" in msg and ("column" in msg or "undefined" in msg):
                    cur.execute(
                        """
                        INSERT INTO sales_master (
                            customer_id, vehicle_id, dealer_id, file_location,
                            order_number, invoice_number
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (customer_id, vehicle_id) DO UPDATE SET
                            dealer_id = COALESCE(EXCLUDED.dealer_id, sales_master.dealer_id),
                            file_location = COALESCE(EXCLUDED.file_location, sales_master.file_location),
                            order_number = COALESCE(EXCLUDED.order_number, sales_master.order_number),
                            invoice_number = COALESCE(EXCLUDED.invoice_number, sales_master.invoice_number)
                        RETURNING sales_id
                        """,
                        (customer_id, vehicle_id, sale_dealer_id, loc_sales, order_n, inv_n),
                    )
                    logger.info("fill_dms: sales_master upsert without enquiry_number column")
                else:
                    raise
            sid_row = cur.fetchone()
            sales_id = int(sid_row["sales_id"] if isinstance(sid_row, dict) else sid_row[0])

            _sold_today = today_dd_mm_yyyy()
            _inv_rc = update_sold_date_by_chassis_engine_on_cursor(
                cur,
                chassis=chassis or "",
                engine=engine or "",
                sold_date=_sold_today,
            )
            if _inv_rc:
                logger.info(
                    "fill_dms: vehicle_inventory_master sold_date=%s for chassis/engine match (rows=%s)",
                    _sold_today,
                    _inv_rc,
                )

        conn.commit()
        logger.info(
            "fill_dms: upserted customer_id=%s vehicle_id=%s sales_id=%s (Siebel scrape)",
            customer_id,
            vehicle_id,
            sales_id,
        )
        return customer_id, vehicle_id, sales_id
    except IntegrityError as exc:
        conn.rollback()
        logger.warning("fill_dms: insert_dms_masters_from_siebel_scrape integrity error: %s", exc)
        raise ValueError(
            "Cannot upsert masters: constraint violation (customer/vehicle/sales). "
            f"{exc!s}"
        ) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def collate_customer_master_from_dms_siebel_inputs(
    dms_values: dict | None,
    *,
    contact_id: str | None = None,
) -> dict[str, object]:
    """
    Build a ``customer_master``-shaped payload from Fill DMS ``dms_values`` plus optional Siebel-scraped
    **Contact Id** (video path: after Relation's Name / Contact ID scrape and **Add customer payment**).

    Returns:
        ``fields``: proposed column → value (only keys with a non-empty value).
        ``notes``: product sourcing (detail sheet vs Siebel); ``customer_id`` is never set here (system PK).
        ``mapping_unclear``: optional residual ambiguity (e.g. ``file_location``).

    **``dms_relation_prefix``:** first three characters of trimmed **Address Line 1** when length ≥ 3.

    Does **not** write to the database.
    """
    def _digits_only(s: str) -> str:
        return re.sub(r"\D", "", s or "")

    def _mobile_int_10(mobile: str) -> int | None:
        d = _digits_only(mobile)
        if len(d) < 10:
            return None
        tail = d[-10:]
        try:
            return int(tail)
        except ValueError:
            return None

    dv = dms_values or {}
    fields: dict[str, object] = {}
    unclear: list[str] = []
    notes: dict[str, object] = {
        "customer_id": "System-generated primary key; not supplied by Siebel or this collate.",
        "profession_marital_aadhar": "Captured from the sales detail sheet (Submit / OCR pipeline), not from Siebel UI.",
    }

    cn = (dv.get("customer_name") or "").strip()
    if cn:
        fields["name"] = cn

    addr = (dv.get("address_line_1") or "").strip()
    if addr:
        fields["address"] = addr

    pin_raw = (dv.get("pin_code") or "").strip()[:6]
    if pin_raw:
        fields["pin"] = pin_raw

    city = (dv.get("city") or "").strip()
    if city:
        fields["city"] = city

    st = (dv.get("state") or "").strip()
    if st:
        fields["state"] = st

    mi = _mobile_int_10(str(dv.get("mobile_phone") or ""))
    if mi is not None:
        fields["mobile_number"] = mi

    land = (dv.get("landline") or "").strip()[:16]
    if land:
        fields["alt_phone_num"] = land

    co = (dv.get("care_of") or "").strip()
    if co:
        fields["care_of"] = co

    fn = (dv.get("financier_name") or "").strip()
    if fn:
        fields["financier"] = fn

    dcp = (dv.get("dms_contact_path") or "found").strip().lower()
    if dcp not in ("found", "new_enquiry", "skip_find"):
        dcp = "found"
    fields["dms_contact_path"] = dcp

    cid = (str(contact_id).strip() if contact_id is not None else "")
    if cid:
        fields["dms_contact_id"] = cid

    g = (dv.get("gender") or "").strip()
    if g:
        fields["gender"] = g

    dob = (dv.get("date_of_birth") or "").strip()
    if dob:
        fields["date_of_birth"] = dob

    aad = (dv.get("aadhar_id") or "").strip()
    digits_aad = re.sub(r"\D", "", aad)
    if len(digits_aad) >= 4:
        fields["aadhar"] = digits_aad[-4:]

    addr_trim = (addr or "").strip()
    if len(addr_trim) >= 3:
        fields["dms_relation_prefix"] = addr_trim[:3]

    unclear.append(
        "file_location: per-sale folder is canonical on sales_master; not derived from Siebel automation."
    )

    return {"fields": fields, "notes": notes, "mapping_unclear": unclear}


def _run_fill_dms_real_siebel_playwright(
    page,
    dms_values: dict,
    effective_subfolder: str,
    ocr_dir: Path,
    customer_id: int | None,
    vehicle_id: int | None,
    result: dict,
    *,
    execution_log_client_api_base_url: str | None = None,
    execution_log_http_request_base_url: str | None = None,
) -> None:
    """
    Hero Connect / Siebel Open UI: ``Playwright_Hero_DMS_fill`` — **Find Contact Enquiry** path
    (``prepare_vehicle`` before Contact Find, enquiry sweep / Add Enquiry, Relation's Name, Payments,
    optional hard-stop before booking, **Generate Booking**, ``_create_order``). See **LLD §2.4d**.

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

    playwright_dms_log = (
        Path(ocr_dir).resolve()
        / _safe_subfolder_name(effective_subfolder)
        / playwright_dms_execution_log_filename()
    )
    result["playwright_dms_execution_log_path"] = str(playwright_dms_log)

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
        execution_log_client_api_base_url=execution_log_client_api_base_url,
        execution_log_http_request_base_url=execution_log_http_request_base_url,
    )

    result["vehicle"] = frag.get("vehicle") or {}
    result["error"] = frag.get("error")
    if frag.get("customer_id") is not None:
        result["customer_id"] = frag.get("customer_id")
    if frag.get("vehicle_id") is not None:
        result["vehicle_id"] = frag.get("vehicle_id")
    if frag.get("sales_id") is not None:
        result["sales_id"] = frag.get("sales_id")
    if frag.get("dms_master_persist_committed") is not None:
        result["dms_master_persist_committed"] = frag.get("dms_master_persist_committed")
    if frag.get("hero_dms_form22_print") is not None:
        result["hero_dms_form22_print"] = frag.get("hero_dms_form22_print")
    result["dms_siebel_forms_filled"] = bool(frag.get("dms_siebel_forms_filled"))
    result["dms_siebel_notes"] = frag.get("dms_siebel_notes") or []
    result["dms_milestones"] = list(frag.get("dms_milestones") or [])
    result["dms_step_messages"] = list(frag.get("dms_step_messages") or [])
    if frag.get("ready_for_client_create_invoice") is not None:
        result["ready_for_client_create_invoice"] = bool(frag.get("ready_for_client_create_invoice"))
    if (result.get("vehicle") or {}).get("financier_name"):
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
    _sort_dms_milestones(result)
    result["dms_automation_mode"] = "real"
    if result.get("error"):
        result["dms_real_note"] = None
    else:
        notes = "; ".join(result["dms_siebel_notes"]) if result.get("dms_siebel_notes") else ""
        result["dms_real_note"] = notes or "Siebel contact + vehicle automation finished."
    logger.info(
        "fill_dms_service: real Siebel flow done error=%s forms_filled=%s vehicle_keys=%s",
        bool(result.get("error")),
        result.get("dms_siebel_forms_filled"),
        list((result.get("vehicle") or {}).keys())[:8],
    )


def warm_dms_browser_session(dms_base_url: str) -> dict:
    """
    Pre-open or attach to the DMS browser (CDP reuse or managed launch) without running fill automation.
    Does **not** wait for login — **Create Invoice** runs prefilled login + session gate on reuse.
    On Windows, a **new** managed browser starts **minimized** so the web client keeps focus.
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
            launch_background=True,
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
    *,
    execution_log_client_api_base_url: str | None = None,
    execution_log_http_request_base_url: str | None = None,
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

    page = None
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

        # Operator-controlled step in production: Create Invoice must be clicked manually if still pending.
        # Non-production: continue (dummy Invoice# on ``sales_master`` insert — see ``HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER``).
        if _requires_operator_create_invoice(page):
            if HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE:
                result["error"] = "Please click Create Invoice manually in DMS, then press Fill DMS again."
                return result
            logger.info(
                "fill_dms_service: Create Invoice control visible — continuing (non-production ENVIRONMENT; "
                "master commit uses %s if scrape has no Invoice#).",
                HERO_DMS_NONPROD_DUMMY_INVOICE_NUMBER,
            )

        _run_fill_dms_real_siebel_playwright(
            page,
            dms_values,
            effective_subfolder,
            ocr_dir,
            customer_id,
            vehicle_id,
            result,
            execution_log_client_api_base_url=execution_log_client_api_base_url,
            execution_log_http_request_base_url=execution_log_http_request_base_url,
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
        and invoice_number_ready_for_master_commit(scraped_final)
    ):
        try:
            cid_c, vid_c = persist_staging_masters_after_invoice(
                staging_id=staging_id or "",
                staging_payload=staging_payload,
                scraped_vehicle=scraped_final,
            )
            result["committed_customer_id"] = cid_c
            result["committed_vehicle_id"] = vid_c
            append_playwright_dms_masters_committed_log(
                result.get("playwright_dms_execution_log_path"),
                customer_id=cid_c,
                vehicle_id=vid_c,
            )
            if page is not None:
                try:
                    _frame_sel = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
                    _mob_pf = (dms_values.get("mobile_phone") or "").strip()
                    _dl_dir = get_uploaded_scans_sale_folder(int(DEALER_ID), _mob_pf).resolve()
                    _ok_pf, _err_pf, _paths_pf, _reports_pf = run_hero_dms_reports(
                        page,
                        mobile=_mob_pf,
                        order_number=(scraped_final.get("order_number") or "").strip(),
                        invoice_number=(scraped_final.get("invoice_number") or "").strip(),
                        action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
                        content_frame_selector=_frame_sel,
                        note=lambda m: logger.info("%s", m),
                        downloads_dir=_dl_dir,
                        execution_log_path=result.get("playwright_dms_execution_log_path"),
                    )
                    result["hero_dms_form22_print"] = {
                        "ok": _ok_pf,
                        "error": _err_pf,
                        "paths": _paths_pf,
                        "reports": _reports_pf,
                    }
                except Exception as _pf_exc:
                    logger.warning("fill_dms_service: run_hero_dms_reports exception: %s", _pf_exc)
                    result["hero_dms_form22_print"] = {"ok": False, "error": str(_pf_exc)}
        except Exception as commit_exc:
            logger.warning("fill_dms_service: staging master commit failed: %s", commit_exc)
            result["error"] = f"Database commit after DMS failed: {commit_exc!s}"

    # Siebel path may have INSERTed masters via ``persist_masters_after_create_order`` inside
    # ``Playwright_Hero_DMS_fill`` while the full staging txn above was skipped (e.g. Invoice# not
    # present in scrape when ``HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE`` is true). Sync ids into
    # ``add_sales_staging.payload_json`` so downstream calls need only ``staging_id``.
    sid_clean = (staging_id or "").strip()
    did_eff = int(dealer_id) if dealer_id is not None else int(DEALER_ID)
    if (
        sid_clean
        and staging_payload is not None
        and not result.get("error")
        and result.get("committed_customer_id") is None
        and result.get("committed_vehicle_id") is None
    ):
        cid_s = result.get("customer_id")
        vid_s = result.get("vehicle_id")
        if cid_s is not None and vid_s is not None:
            try:
                patch_ids: dict[str, Any] = {
                    "customer_id": int(cid_s),
                    "vehicle_id": int(vid_s),
                }
                sal = result.get("sales_id")
                if sal is not None:
                    patch_ids["sales_id"] = int(sal)
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        merge_staging_payload_on_cursor(cur, sid_clean, did_eff, patch_ids)
                    conn.commit()
                result["committed_customer_id"] = int(cid_s)
                result["committed_vehicle_id"] = int(vid_s)
            except Exception as patch_exc:
                logger.warning("fill_dms_service: staging payload patch (Siebel master ids) failed: %s", patch_exc)

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
    dealer_id: int | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    headless: bool | None = None,
    staging_payload: dict | None = None,
    staging_id: str | None = None,
    *,
    execution_log_client_api_base_url: str | None = None,
    execution_log_http_request_base_url: str | None = None,
) -> dict:
    """
    Run Playwright: DMS flow (Siebel Create Invoice).
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
        execution_log_client_api_base_url=execution_log_client_api_base_url,
        execution_log_http_request_base_url=execution_log_http_request_base_url,
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
            "hero_dms_form22_print": result.get("hero_dms_form22_print"),
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
        "hero_dms_form22_print": result.get("hero_dms_form22_print"),
    }


def Playwright_Hero_DMS_fill(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    skip_contact_find: bool = False,
    execution_log_path: Path | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    execution_log_client_api_base_url: str | None = None,
    execution_log_http_request_base_url: str | None = None,
) -> dict:
    """
    Hero Connect / Siebel automation — **Find Contact Enquiry** path. Pipeline:
    ``prepare_vehicle`` → ``prepare_customer`` (``hero_dms_prepare_customer``) → ``prepare_order``
    (``hero_dms_playwright_invoice`` — **Generate Booking**, ``_create_order``) →
    ``persist_masters_after_create_order`` (``hero_dms_db_service``; delegates
    ``insert_dms_masters_from_siebel_scrape`` when **Invoice#** and policy allow). When that persist commits
    (``dms_master_persist_committed``), ``run_hero_dms_reports`` runs next (same Run Report PDF flow as
    ``run_fill_dms_only``); report failure does not clear the DB success. Does not run a separate
    post-contact **Auto Vehicle List** stage after enquiry (vehicle prep is up front). Browser is not closed
    by this function.

    ``skip_contact_find=True`` is ignored: real Fill DMS always runs Find (``fill_dms_service`` passes
    ``skip_contact_find=False``). ``dms_contact_path=skip_find`` in DB does not bypass Find.

    ``_attach_vehicle_to_bkg`` clicks **Apply Campaign**; **Create Invoice** only if
    ``HERO_DMS_ATTACH_AUTO_CLICK_CREATE_INVOICE`` is True (``ENVIRONMENT=prod`` in ``.env``). Returns ``vehicle``,
    ``error``, ``dms_siebel_forms_filled``, notes, milestones, ``dms_step_messages``, and on the video SOP path
    after **Add customer payment** ``dms_customer_master_collated``
    (``fill_hero_dms_service.collate_customer_master_from_dms_siebel_inputs`` — ``fields``, ``notes``, ``mapping_unclear``).
    After a successful video ``create_order`` scrape: ``dms_sales_master_prep`` (order/invoice/enquiry + ids),
    ``dms_master_persist_committed`` when **Invoice#** was scraped (**Create Invoice** done) and
    the db facade ran ``insert_dms_masters_from_siebel_scrape``; otherwise DB is untouched and values are log-only.

    If ``execution_log_path`` is set, writes that path (typically a unique per-run filename from
    ``playwright_dms_execution_log_filename``) with an IST (Asia/Kolkata) trace
    (values used, STEP / NOTE / MILESTONE lines, and a final END line with ``error`` if any).
    """
    out: dict = {
        "vehicle": {},
        "error": None,
        "dms_siebel_forms_filled": False,
        "dms_siebel_notes": [],
        "dms_milestones": [],
        "dms_step_messages": [],
    }

    page.set_default_timeout(action_timeout_ms)

    mobile = (dms_values.get("mobile_phone") or "").strip()
    first = (dms_values.get("first_name") or "").strip()
    last = (dms_values.get("last_name") or "").strip()
    addr = (dms_values.get("address_line_1") or "").strip()
    state = (dms_values.get("state") or "").strip()
    pin = (dms_values.get("pin_code") or "").strip()
    landline = (dms_values.get("landline") or "").strip()
    care_of = (dms_values.get("care_of") or "").strip()
    key_p = (dms_values.get("key_partial") or "").strip()
    battery_p = (dms_values.get("battery_partial") or "").strip()
    frame_p = (dms_values.get("frame_partial") or "").strip()
    engine_p = (dms_values.get("engine_partial") or "").strip()
    aadhar_uin = (dms_values.get("aadhar_id") or "").strip()
    dms_path = (dms_values.get("dms_contact_path") or "found").strip().lower()

    run_started_ist = _ts_ist_iso()
    log_fp = None
    _exec_log_path = Path(execution_log_path) if execution_log_path is not None else None
    if execution_log_path is not None:
        lp = Path(execution_log_path)
        lp.parent.mkdir(parents=True, exist_ok=True)
        log_fp = open(lp, "w", encoding="utf-8")
        log_fp.write("Playwright DMS — execution log (this run only; IST / Asia/Kolkata timestamps)\n\n")
        _capi = (execution_log_client_api_base_url or "").strip()
        _hreq = (execution_log_http_request_base_url or "").strip()
        log_fp.write(
            "# Client API base: VITE_API_URL at build (browser/Electron); empty string = relative URLs / Vite proxy.\n"
        )
        log_fp.write(f"client_api_base_url={_capi[:400]!r}\n")
        log_fp.write(
            "# HTTP request base: FastAPI Request.base_url for this fill-forms call (which host the API saw).\n"
        )
        log_fp.write(f"http_request_base_url={_hreq[:400]!r}\n\n")
        log_fp.write(f"started_ist={run_started_ist}\n")
        log_fp.write(f"skip_contact_find={skip_contact_find}\n")
        log_fp.write(f"dms_contact_path={dms_path!r}\n")
        log_fp.write(f"mobile_phone={mobile!r}\n")
        log_fp.write(f"first_name={first!r}\n")
        log_fp.write(f"last_name={last!r}\n")
        log_fp.write(f"address_line_1={addr!r}\n")
        log_fp.write(f"state={state!r}\n")
        log_fp.write(f"pin_code={pin!r}\n")
        log_fp.write(f"landline={landline!r}\n")
        log_fp.write(f"care_of={care_of!r}\n")
        log_fp.write(f"key_partial={key_p!r}\n")
        log_fp.write(f"frame_partial={frame_p!r}\n")
        log_fp.write(f"engine_partial={engine_p!r}\n")
        log_fp.write(f"aadhar_id={aadhar_uin!r}\n")
        log_fp.write(
            "# Siebel: after prepare_vehicle / scrapes, a --- vehicle_master --- block lists merged keys for "
            "traceability (grid + DMS). Masters are persisted only after Invoice# is scraped (Create Invoice).\n"
        )
        cu = (urls.contact or "").strip()
        log_fp.write(f"url_contact_truncated={cu[:200]!r}\n")
        log_fp.write(f"url_enquiry_truncated={(urls.enquiry or '')[:200]!r}\n")
        log_fp.write(f"url_vehicle_truncated={(urls.vehicle or '')[:200]!r}\n")
        log_fp.write("\n--- trace ---\n")
        log_fp.write(
            "Legend: [STEP]/[NOTE]/[MILESTONE] = operator narrative; [FORM] = siebel_step + "
            "Siebel form/screen + action + fields/values being applied on that form.\n\n"
        )
        log_fp.flush()

    def _exec_log(prefix: str, msg: str) -> None:
        if not log_fp or not (msg or "").strip():
            return
        try:
            log_fp.write(f"{_ts_ist_iso()} [{prefix}] {msg}\n")
            log_fp.flush()
        except OSError:
            pass

    def form_trace(siebel_step: str, form_name: str, action: str, **fields: object) -> None:
        """Write one structured [FORM] line: step, screen/applet name, action, and field updates."""
        segments = [f"siebel_step={siebel_step}", f"form={form_name}", f"action={action}"]
        for key in sorted(fields.keys()):
            val = fields[key]
            if val is None:
                continue
            if isinstance(val, bool):
                segments.append(f"{key}={val}")
                continue
            v = str(val).replace("\n", " ").strip()
            if v == "":
                continue
            if len(v) > 500:
                v = v[:497] + "..."
            segments.append(f"{key}={v!r}")
        _exec_log("FORM", " | ".join(segments))

    def ms_done(label: str) -> None:
        m = out["dms_milestones"]
        if label not in m:
            m.append(label)
            _exec_log("MILESTONE", label)

    def step(msg: str) -> None:
        """Ordered user-facing progress (Add Sales banner)."""
        if msg and (not out["dms_step_messages"] or out["dms_step_messages"][-1] != msg):
            out["dms_step_messages"].append(msg)
        _exec_log("STEP", msg)

    def note(msg: str) -> None:
        out["dms_siebel_notes"].append(msg)
        logger.info("siebel_dms: %s", msg)
        _exec_log("NOTE", msg)

    def log_vehicle_snapshot(stage: str) -> None:
        """
        Write current ``out['vehicle']`` key-values immediately after each scrape/merge update.
        Keeps the Playwright DMS execution log aligned with in-memory state evolution.
        """
        veh = out.get("vehicle") or {}
        if not log_fp or not isinstance(veh, dict):
            return
        try:
            log_fp.write(f"\n--- vehicle_snapshot ({stage}) ---\n")
            for k in sorted(veh.keys()):
                v = veh.get(k)
                if v is None:
                    continue
                s = str(v).replace("\n", " ").replace("\r", " ").strip()
                if not s:
                    continue
                if len(s) > 2000:
                    s = s[:1997] + "..."
                log_fp.write(f"{k}={s!r}\n")
            log_fp.flush()
        except OSError:
            pass

    customer_save_clicked = False

    def save_customer_record(msg_clicked: str, msg_missing: str) -> None:
        nonlocal customer_save_clicked
        if _try_click_siebel_save(
            page, timeout_ms=action_timeout_ms, content_frame_selector=content_frame_selector
        ):
            customer_save_clicked = True
            note(msg_clicked)
        else:
            note(msg_missing)

    try:
        step("Started Hero Connect / Siebel DMS automation (Find Contact Enquiry path).")
        _fn_gate_ok, _fn_gate_msg = _validate_contact_find_first_name(first)
        if not _fn_gate_ok:
            step("Stopped: invalid or missing Contact First Name for Siebel automation.")
            out["error"] = _fn_gate_msg
            return out

        if dms_path == "skip_find" and not skip_contact_find:
            note(
                "dms_contact_path=skip_find in form data — real Siebel still runs Stage 1 Contact Find "
                "(mobile + Go) so the existing customer is loaded in the correct Siebel context."
            )

        contact_url = (urls.contact or "").strip()
        in_transit_state = False

        step("Pre-step: preparing vehicle before contact find (video path).")
        _pv_ok, _pv_err, _pv_scraped, in_transit_state, _pv_crit, _pv_info = prepare_vehicle(
            page,
            dms_values,
            urls,
            nav_timeout_ms=nav_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            form_trace=form_trace,
            ms_done=ms_done,
            step=step,
            debug_dump_dir=_exec_log_path.parent if _exec_log_path else None,
        )
        if not _pv_ok:
            out["error"] = _pv_err or "prepare_vehicle failed before contact find."
            return out
        out["vehicle"] = _pv_scraped
        _write_playwright_vehicle_master_section(log_fp, _pv_scraped, _pv_crit, _pv_info)

        if skip_contact_find:
            note(
                "skip_contact_find was True — ignored; using Find → Contact → All Enquiries path."
            )
        if not mobile:
            step("Stopped: mobile_phone is required for Find Contact video path.")
            out["error"] = "Siebel: mobile_phone is empty — cannot run Find by mobile."
            return out
        if not contact_url:
            step("Stopped: DMS_REAL_URL_CONTACT is not configured.")
            out["error"] = (
                "Siebel: set DMS_REAL_URL_CONTACT to the Contact / Find (or Visible Contact List for Find) "
                "GotoView URL so the video SOP can open the Find applet."
            )
            return out
        video_first_name = first.strip()
        if not prepare_customer(
            page,
            dms_values,
            urls,
            out,
            contact_url=contact_url,
            mobile=mobile,
            video_first_name=video_first_name,
            care_of=care_of,
            addr=addr,
            pin=pin,
            action_timeout_ms=action_timeout_ms,
            nav_timeout_ms=nav_timeout_ms,
            content_frame_selector=content_frame_selector,
            mobile_aria_hints=mobile_aria_hints,
            note=note,
            step=step,
            form_trace=form_trace,
            ms_done=ms_done,
            log_fp=log_fp,
            log_vehicle_snapshot=log_vehicle_snapshot,
        ):
            return out
        order_scraped = prepare_order(
            page,
            dms_values,
            urls,
            out,
            mobile=mobile,
            video_first_name=video_first_name,
            action_timeout_ms=action_timeout_ms,
            nav_timeout_ms=nav_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            step=step,
            form_trace=form_trace,
            ms_done=ms_done,
            log_vehicle_snapshot=log_vehicle_snapshot,
        )
        if out.get("error"):
            return out
        persist_masters_after_create_order(
            out,
            dms_values,
            order_scraped=order_scraped,
            preexisting_customer_id=customer_id,
            preexisting_vehicle_id=vehicle_id,
            dealer_id=int(DEALER_ID),
            log_fp=log_fp,
            note=note,
        )
        if out.get("error"):
            return out

        _inv_num_for_report = (str((out.get("vehicle") or {}).get("invoice_number") or "")).strip()
        _should_run_reports = (
            out.get("dms_master_persist_committed") is True
            or bool(_inv_num_for_report)
        )
        if _should_run_reports:
            try:
                _frame_sel_pf = (DMS_SIEBEL_CONTENT_FRAME_SELECTOR or "").strip() or None
                _dl_dir_pf = get_uploaded_scans_sale_folder(int(DEALER_ID), mobile).resolve()
                _ok_pf, _err_pf, _paths_pf, _reports_pf = run_hero_dms_reports(
                    page,
                    mobile=mobile,
                    order_number=(str((out.get("vehicle") or {}).get("order_number") or "")).strip(),
                    invoice_number=_inv_num_for_report,
                    action_timeout_ms=DMS_SIEBEL_ACTION_TIMEOUT_MS,
                    content_frame_selector=_frame_sel_pf,
                    note=note,
                    downloads_dir=_dl_dir_pf,
                    execution_log_path=str(_exec_log_path) if _exec_log_path is not None else None,
                )
                out["hero_dms_form22_print"] = {
                    "ok": _ok_pf,
                    "error": _err_pf,
                    "paths": _paths_pf,
                    "reports": _reports_pf,
                }
            except Exception as _pf_exc:
                logger.warning(
                    "fill_dms_service: run_hero_dms_reports after persist_masters_after_create_order: %s",
                    _pf_exc,
                )
                out["hero_dms_form22_print"] = {"ok": False, "error": str(_pf_exc)}

        out["dms_siebel_forms_filled"] = True
        step(
            "Video SOP complete: customer record opened, payment added, and create_order flow completed. "
            "Automation stops here; browser left open."
        )
        note("Relation's Name/Address/Pincode, payment entry, and create_order flow completed; automation stops now.")
        return out



    except PlaywrightTimeout as e:
        out["error"] = f"Siebel automation timeout: {e!s}"
        logger.warning("siebel_dms: PlaywrightTimeout %s", e)
    except RuntimeError as e:
        out["error"] = str(e)
        logger.warning("siebel_dms: %s", e)
    except Exception as e:
        out["error"] = f"Siebel automation error: {e!s}"
        logger.warning("siebel_dms: exception %s", e, exc_info=True)
    finally:
        out["dms_milestones"] = _sort_milestone_labels(list(out.get("dms_milestones") or []))
        if log_fp is not None:
            try:
                log_fp.write(
                    f"\n{_ts_ist_iso()} [END] "
                    f"error={out.get('error')!s}\n"
                )
            except OSError:
                pass
            try:
                log_fp.close()
            except OSError:
                pass

    return out


def Playwright_Hero_DMS_fill_subdealer_challan_order_only(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    execution_log_path: Path | None = None,
) -> dict:
    """
    After per-vehicle ``prepare_vehicle`` (and DB upserts), run **only** ``prepare_order`` for the challan batch:
    Network customer, dummy mobile, multi-line ``order_line_vehicles`` / ``attach_line_items``.

    Does **not** run ``prepare_vehicle`` or retail ``prepare_customer``. Caller must set ``dms_values`` via
    ``prepare_customer_for_challan`` and ``_challan_last_vehicle`` (last scrape) before calling.

    When ``execution_log_path`` is set (typically the same ``playwright_challan.txt`` as the prepare
    phase), STEP/NOTE lines are **appended** to that file — not a new ``Playwright_DMS_*.txt``.
    """
    out: dict = {
        "vehicle": dict(dms_values.get("_challan_last_vehicle") or {}),
        "error": None,
        "dms_siebel_forms_filled": False,
        "dms_siebel_notes": [],
        "dms_milestones": [],
        "dms_step_messages": [],
    }
    page.set_default_timeout(action_timeout_ms)
    log_fp = None
    _exec_log_path = Path(execution_log_path) if execution_log_path is not None else None
    if execution_log_path is not None:
        lp = Path(execution_log_path)
        lp.parent.mkdir(parents=True, exist_ok=True)
        # Append to the same human trace as prepare_vehicle (e.g. playwright_challan.txt); do not
        # open a separate per-phase Playwright_DMS_*.txt file.
        log_fp = open(lp, "a", encoding="utf-8")
        log_fp.write("\n\n--- subdealer challan order phase (structured STEP/NOTE trace) ---\n")
        log_fp.write(f"started_ist={_ts_ist_iso()}\n")
        log_fp.flush()

    def _exec_log(prefix: str, msg: str) -> None:
        if not log_fp or not (msg or "").strip():
            return
        try:
            log_fp.write(f"{_ts_ist_iso()} [{prefix}] {msg}\n")
            log_fp.flush()
        except OSError:
            pass

    def form_trace(siebel_step: str, form_name: str, action: str, **fields: object) -> None:
        segments = [f"siebel_step={siebel_step}", f"form={form_name}", f"action={action}"]
        for key in sorted(fields.keys()):
            val = fields[key]
            if val is None:
                continue
            v = str(val).replace("\n", " ").strip()
            if v == "":
                continue
            if len(v) > 500:
                v = v[:497] + "..."
            segments.append(f"{key}={v!r}")
        _exec_log("FORM", " | ".join(segments))

    def ms_done(label: str) -> None:
        m = out["dms_milestones"]
        if label not in m:
            m.append(label)
            _exec_log("MILESTONE", label)

    def step(msg: str) -> None:
        if msg and (not out["dms_step_messages"] or out["dms_step_messages"][-1] != msg):
            out["dms_step_messages"].append(msg)
        _exec_log("STEP", msg)

    def note(msg: str) -> None:
        out["dms_siebel_notes"].append(msg)
        logger.info("siebel_dms_challan: %s", msg)
        _exec_log("NOTE", msg)

    def log_vehicle_snapshot(stage: str) -> None:
        veh = out.get("vehicle") or {}
        if not log_fp or not isinstance(veh, dict):
            return
        try:
            log_fp.write(f"\n--- vehicle_snapshot ({stage}) ---\n")
            for k in sorted(veh.keys()):
                v = veh.get(k)
                if v is None:
                    continue
                s = str(v).replace("\n", " ").replace("\r", " ").strip()
                if not s:
                    continue
                if len(s) > 2000:
                    s = s[:1997] + "..."
                log_fp.write(f"{k}={s!r}\n")
            log_fp.flush()
        except OSError:
            pass

    try:
        step("Subdealer challan: Sales Orders / create order (Network, multi-VIN).")
        _flow = str(dms_values.get("hero_dms_flow") or "").strip()
        if _flow != "add_subdealer_challan":
            _fn_ok, _fn_msg = _validate_contact_find_first_name((dms_values.get("first_name") or "").strip())
            if not _fn_ok:
                out["error"] = _fn_msg
                return out
        mobile = (dms_values.get("mobile_phone") or "").strip()
        if not mobile:
            out["error"] = "Siebel: mobile_phone is empty — cannot run Sales Orders mobile search."
            return out
        video_first_name = (dms_values.get("first_name") or "").strip()
        order_scraped = prepare_order(
            page,
            dms_values,
            urls,
            out,
            mobile=mobile,
            video_first_name=video_first_name or "Subdealer",
            action_timeout_ms=action_timeout_ms,
            nav_timeout_ms=nav_timeout_ms,
            content_frame_selector=content_frame_selector,
            note=note,
            step=step,
            form_trace=form_trace,
            ms_done=ms_done,
            log_vehicle_snapshot=log_vehicle_snapshot,
        )
        if out.get("error"):
            return out
        out["vehicle"] = dict(out.get("vehicle") or {})
        if order_scraped:
            veh = dict(out.get("vehicle") or {})
            if order_scraped.get("vehicle_price"):
                veh["vehicle_price"] = order_scraped.get("vehicle_price")
            if order_scraped.get("order_number"):
                veh["order_number"] = order_scraped.get("order_number")
            if order_scraped.get("invoice_number"):
                veh["invoice_number"] = order_scraped.get("invoice_number")
            if order_scraped.get("order_line_ex_showroom"):
                veh["order_line_ex_showroom"] = order_scraped.get("order_line_ex_showroom")
            out["vehicle"] = veh
            log_vehicle_snapshot("challan_prepare_order_merge")
        out["dms_siebel_forms_filled"] = True
        step("Subdealer challan: prepare_order completed.")
        return out
    except PlaywrightTimeout as e:
        out["error"] = f"Siebel automation timeout: {e!s}"
        logger.warning("siebel_dms_challan: PlaywrightTimeout %s", e)
    except Exception as e:
        out["error"] = f"Siebel automation error: {e!s}"
        logger.warning("siebel_dms_challan: %s", e, exc_info=True)
    finally:
        out["dms_milestones"] = _sort_milestone_labels(list(out.get("dms_milestones") or []))
        if log_fp is not None:
            try:
                log_fp.write(f"\n{_ts_ist_iso()} [END] error={out.get('error')!s}\n")
            except OSError:
                pass
            try:
                log_fp.close()
            except OSError:
                pass
    return out


def run_hero_siebel_dms_flow(
    page: Page,
    dms_values: dict,
    urls: SiebelDmsUrls,
    *,
    action_timeout_ms: int,
    nav_timeout_ms: int,
    content_frame_selector: str | None,
    mobile_aria_hints: list[str],
    skip_contact_find: bool = False,
    execution_log_path: Path | None = None,
    customer_id: int | None = None,
    vehicle_id: int | None = None,
    execution_log_client_api_base_url: str | None = None,
    execution_log_http_request_base_url: str | None = None,
) -> dict:
    """
    Backward-compatible alias for older callers.
    Prefer ``Playwright_Hero_DMS_fill`` for new integrations/modules.
    """
    return Playwright_Hero_DMS_fill(
        page,
        dms_values,
        urls,
        action_timeout_ms=action_timeout_ms,
        nav_timeout_ms=nav_timeout_ms,
        content_frame_selector=content_frame_selector,
        mobile_aria_hints=mobile_aria_hints,
        skip_contact_find=skip_contact_find,
        execution_log_path=execution_log_path,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        execution_log_client_api_base_url=execution_log_client_api_base_url,
        execution_log_http_request_base_url=execution_log_http_request_base_url,
    )
