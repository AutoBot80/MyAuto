"""DMS fill source row: same projection as the former ``form_dms_view`` (inline query, no DB view).

**Legacy:** ``sales_master`` + ``customer_master`` + ``vehicle_master`` join (after Submit Info).

**Target Create Invoice path:** ``build_dms_fill_row_from_staging_payload`` — OCR merge in
``add_sales_staging.payload_json`` only; optional ``dealer_ref.dealer_name`` lookup (reference table).
"""

from typing import Any

from app.db import get_connection
from app.services.dms_relation_prefix import compute_dms_relation_prefix


def _st(val: object | None) -> str:
    if val is None:
        return ""
    return str(val).strip()


def lookup_dealer_name(dealer_id: int | None) -> str:
    """Dealer display name from ``dealer_ref`` (not customer/vehicle/sales masters)."""
    if dealer_id is None:
        return ""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(TRIM(dealer_name::text), '') AS n FROM dealer_ref WHERE dealer_id = %s",
                (int(dealer_id),),
            )
            row = cur.fetchone()
            if not row:
                return ""
            if isinstance(row, dict):
                return _st(row.get("n"))
            return _st(row[0])
    finally:
        conn.close()


def build_dms_fill_row_from_staging_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Build the label-aligned DMS fill dict from submit-info-shaped staging JSON (OCR + operator edits).
    Does **not** read ``customer_master``, ``vehicle_master``, or ``sales_master``.
    """
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
    dealer_raw = payload.get("dealer_id")
    try:
        did = int(dealer_raw) if dealer_raw is not None else None
    except (TypeError, ValueError):
        did = None
    file_loc = _st(payload.get("file_location")) or _st(customer.get("file_location"))
    name = _st(customer.get("name"))
    parts = name.split(None, 1)
    first = parts[0] if parts else ""
    last = parts[1].strip() if len(parts) > 1 else ""
    gender = _st(customer.get("gender")).lower()
    mr_ms = "Ms." if gender in ("f", "female") else "Mr."
    mob_raw = customer.get("mobile_number")
    mob_s = str(mob_raw).strip() if mob_raw is not None else ""
    financier = _st(customer.get("financier"))
    rel_pref = _st(customer.get("dms_relation_prefix"))
    if not rel_pref:
        rel_pref = compute_dms_relation_prefix(_st(customer.get("address")), customer.get("gender"))
    care = _st(customer.get("care_of"))
    father_h = care
    path = (_st(customer.get("dms_contact_path")) or "found").lower()
    if path not in ("found", "new_enquiry", "skip_find"):
        path = "found"
    raw_key = _st(vehicle.get("key_no"))
    raw_f = _st(vehicle.get("frame_no"))
    raw_e = _st(vehicle.get("engine_no"))
    chassis = _st(vehicle.get("chassis"))
    eng_full = _st(vehicle.get("engine"))
    key_left = raw_key[:8] if raw_key else ""
    frame_src = raw_f or chassis
    engine_src = raw_e or eng_full
    frame_left = frame_src[:12] if frame_src else ""
    engine_left = engine_src[:12] if engine_src else ""
    battery = _st(vehicle.get("battery_no"))
    pin = (_st(customer.get("pin")) or "")[:6]
    return {
        "sales_id": None,
        "customer_id": None,
        "vehicle_id": None,
        "dealer_id": did,
        "subfolder": file_loc or None,
        "dealer_name": lookup_dealer_name(did) or None,
        "oem_name": _st(vehicle.get("oem_name")) or None,
        "Mr/Ms": mr_ms,
        "Contact First Name": first or None,
        "Contact Last Name": last or None,
        "Mobile Phone #": mob_s or None,
        "Landline #": _st(customer.get("alt_phone_num")) or None,
        "State": _st(customer.get("state")).upper() or None,
        "Address Line 1": _st(customer.get("address")) or None,
        "City": _st(customer.get("city")) or None,
        "Pin Code": pin or None,
        "Key num (partial)": key_left or None,
        "Battery No": battery,
        "Frame / Chassis num (partial)": frame_left or None,
        "Engine num (partial)": engine_left or None,
        "Relation (S/O or W/o)": rel_pref,
        "Father or Husband Name": father_h or None,
        "Financier Name": financier,
        "Finance Required": "Y" if financier else "N",
        "DMS Contact Path": path,
    }

_DMS_FILL_ROW_SQL = """
SELECT
    sm.sales_id,
    sm.customer_id,
    sm.vehicle_id,
    sm.dealer_id,
    COALESCE(sm.file_location, cm.file_location) AS subfolder,
    dr.dealer_name,
    vm.oem_name,
    CASE
        WHEN LOWER(COALESCE(cm.gender, '')) IN ('f', 'female') THEN 'Ms.'
        ELSE 'Mr.'
    END AS "Mr/Ms",
    SPLIT_PART(TRIM(COALESCE(cm.name, '')), ' ', 1) AS "Contact First Name",
    NULLIF(
        BTRIM(
            SUBSTRING(
                TRIM(COALESCE(cm.name, ''))
                FROM LENGTH(SPLIT_PART(TRIM(COALESCE(cm.name, '')), ' ', 1)) + 1
            )
        ),
        ''
    ) AS "Contact Last Name",
    cm.mobile_number::text AS "Mobile Phone #",
    cm.alt_phone_num AS "Landline #",
    UPPER(COALESCE(cm.state, '')) AS "State",
    cm.address AS "Address Line 1",
    cm.city AS "City",
    cm.pin AS "Pin Code",
    LEFT(COALESCE(vm.raw_key_num, vm.key_num, ''), 8) AS "Key num (partial)",
    COALESCE(vm.battery, '') AS "Battery No",
    LEFT(COALESCE(vm.raw_frame_num, vm.chassis, ''), 12) AS "Frame / Chassis num (partial)",
    LEFT(COALESCE(vm.raw_engine_num, vm.engine, ''), 12) AS "Engine num (partial)",
    COALESCE(
        NULLIF(BTRIM(cm.dms_relation_prefix), ''),
        CASE
            WHEN LENGTH(BTRIM(COALESCE(cm.address, ''))) >= 3 THEN LEFT(BTRIM(cm.address), 3)
            ELSE CASE
                WHEN LOWER(COALESCE(cm.gender, '')) IN ('f', 'female') THEN 'D/o'
                ELSE 'S/o'
            END
        END
    ) AS "Relation (S/O or W/o)",
    BTRIM(COALESCE(cm.care_of, '')) AS "Father or Husband Name",
    COALESCE(BTRIM(cm.financier), '') AS "Financier Name",
    CASE WHEN COALESCE(BTRIM(cm.financier), '') <> '' THEN 'Y' ELSE 'N' END AS "Finance Required",
    COALESCE(NULLIF(BTRIM(cm.dms_contact_path), ''), 'found') AS "DMS Contact Path"
FROM sales_master sm
JOIN customer_master cm ON cm.customer_id = sm.customer_id
JOIN vehicle_master vm ON vm.vehicle_id = sm.vehicle_id
LEFT JOIN dealer_ref dr ON dr.dealer_id = sm.dealer_id
WHERE sm.customer_id = %s AND sm.vehicle_id = %s
ORDER BY sm.sales_id DESC
LIMIT 1
"""


def get_by_customer_vehicle(customer_id: int, vehicle_id: int) -> dict | None:
    """Return the DMS fill row for one submitted sale (latest sales_id)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_DMS_FILL_ROW_SQL, (customer_id, vehicle_id))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()
