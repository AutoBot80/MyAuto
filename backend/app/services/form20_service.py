"""
Generate Form 20 (front and back) filled with customer and vehicle data.
Saves Form 20 Front.pdf and Form 20 Back.pdf to Uploaded scans/subfolder.
"""
import logging
from pathlib import Path
from typing import Any

from app.config import UPLOADS_DIR

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def _get_vehicle_from_db(vehicle_id: int) -> dict[str, Any]:
    """Fetch vehicle from vehicle_master by vehicle_id."""
    from app.db import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT vehicle_id, chassis, engine, key_num, model, colour,
                       year_of_mfg, cubic_capacity, body_type, seating_capacity,
                       oem_name, vehicle_type, num_cylinders, horse_power, length_mm, fuel_type
                FROM vehicle_master WHERE vehicle_id = %s
                """,
                (vehicle_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return dict(row)
    except Exception:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT vehicle_id, chassis, engine, key_num, model, colour,
                           year_of_mfg, cubic_capacity, body_type, seating_capacity
                    FROM vehicle_master WHERE vehicle_id = %s
                    """,
                    (vehicle_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else {}
        except Exception:
            return {}
    finally:
        conn.close()


def _get_dealer_from_db(dealer_id: int) -> dict[str, Any]:
    """Fetch dealer from dealer_ref by dealer_id."""
    from app.db import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dealer_id, dealer_name, address, pin, city, state
                FROM dealer_ref WHERE dealer_id = %s
                """,
                (dealer_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    except Exception:
        return {}
    finally:
        conn.close()


def _build_form20_data(
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    vehicle_id: int | None = None,
    dealer_id: int | None = None,
) -> dict[str, str]:
    """Build Form 20 field values from customer and vehicle. Merge with DB vehicle if vehicle_id given."""
    data: dict[str, str] = {}

    # Merge vehicle from DB if we have vehicle_id
    db_vehicle: dict[str, Any] = {}
    if vehicle_id:
        db_vehicle = _get_vehicle_from_db(vehicle_id)

    v = {**vehicle, **db_vehicle}
    c = customer or {}

    def _str(val: Any) -> str:
        if val is None:
            return ""
        s = str(val).strip()
        return s if s else ""

    # Field 1: Name and Care of
    name = _str(c.get("name"))
    care_of = _str(c.get("care_of"))
    if name and care_of:
        data["field_1_name_care_of"] = f"{name} S/O {care_of}"
    else:
        data["field_1_name_care_of"] = name or care_of or ""

    # Field 3: Address
    addr_parts = [
        _str(c.get("address")),
        _str(c.get("city")),
        _str(c.get("state")),
        _str(c.get("pin_code") or c.get("pin")),
    ]
    data["field_3_address"] = ", ".join(p for p in addr_parts if p)

    # Field 10: Dealer name and address (from dealer_ref)
    dealer: dict[str, Any] = {}
    if dealer_id:
        dealer = _get_dealer_from_db(dealer_id)
    dealer_name = _str(dealer.get("dealer_name"))
    dealer_addr_parts = [
        _str(dealer.get("address")),
        _str(dealer.get("city")),
        _str(dealer.get("state")),
        _str(dealer.get("pin") or dealer.get("pin_code")),
    ]
    dealer_address = ", ".join(p for p in dealer_addr_parts if p)
    if dealer_name and dealer_address:
        data["field_10_dealer_name_address"] = f"{dealer_name}, {dealer_address}"
    else:
        data["field_10_dealer_name_address"] = dealer_name or dealer_address or ""

    # Field 14: Body type
    data["field_14_body_type"] = _str(v.get("body_type"))

    # Field 15: Vehicle type
    data["field_15_vehicle_type"] = _str(v.get("vehicle_type"))

    # Field 16: OEM / Make
    data["field_16_oem_name"] = _str(v.get("oem_name") or v.get("model"))

    # Field 17: Year of mfg
    data["field_17_year_of_mfg"] = _str(v.get("year_of_mfg"))

    # Field 18: No. of cylinders
    data["field_18_num_cylinders"] = _str(v.get("num_cylinders"))

    # Field 19: Horse power
    data["field_19_horse_power"] = _str(v.get("horse_power"))

    # Field 21: Length (mm)
    data["field_21_length"] = _str(v.get("length_mm"))

    # Field 22: Chassis no.
    data["field_22_chassis_no"] = _str(v.get("chassis") or v.get("frame_num") or v.get("frame_no"))

    # Field 24: Seating capacity
    data["field_24_seating_capacity"] = _str(v.get("seating_capacity"))

    # Field 25: Fuel type
    data["field_25_fuel_type"] = _str(v.get("fuel_type")) or "Petrol"

    # Field 28: Colour
    data["field_28_colour"] = _str(v.get("colour") or v.get("color"))

    # Footer: Dealer Saathi© Vehicle ID <vehicle_id> (left, 10pt)
    vid = vehicle_id if vehicle_id else v.get("vehicle_id")
    data["footer_text"] = f"Dealer Saathi<sup>©</sup> Vehicle ID {vid if vid else '—'}"

    return data


def generate_form20_pdfs(
    subfolder: str,
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    vehicle_id: int | None = None,
    dealer_id: int | None = None,
    uploads_dir: Path | None = None,
) -> list[str]:
    """
    Generate Form 20 Front.pdf and Form 20 Back.pdf, save to Uploaded scans/subfolder.
    Returns list of saved filenames.
    """
    import re

    uploads_path = Path(uploads_dir or UPLOADS_DIR).resolve()
    safe_sub = re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"
    subfolder_path = uploads_path / safe_sub
    subfolder_path.mkdir(parents=True, exist_ok=True)

    data = _build_form20_data(customer, vehicle, vehicle_id, dealer_id)

    front_html = (TEMPLATES_DIR / "form20_front.html").read_text(encoding="utf-8")
    back_html = (TEMPLATES_DIR / "form20_back.html").read_text(encoding="utf-8")

    for key, val in data.items():
        front_html = front_html.replace("{{" + key + "}}", val or "—")
        back_html = back_html.replace("{{" + key + "}}", val or "—")

    # Clear any remaining placeholders
    import re as re_mod
    front_html = re_mod.sub(r"\{\{[^}]+\}\}", "—", front_html)
    back_html = re_mod.sub(r"\{\{[^}]+\}\}", "—", back_html)

    saved: list[str] = []
    try:
        from xhtml2pdf import pisa

        for html, out_name in [
            (front_html, "Form 20 Front.pdf"),
            (back_html, "Form 20 Back.pdf"),
        ]:
            out_path = subfolder_path / out_name
            with open(out_path, "w+b") as dest:
                status = pisa.CreatePDF(html, dest=dest, encoding="utf-8")
            if status.err:
                raise RuntimeError(f"xhtml2pdf error: {status.err}")
            saved.append(out_name)
            logger.info("form20: saved %s", out_path)
    except Exception as e:
        logger.warning("form20: PDF generation failed: %s", e)
        raise

    return saved
