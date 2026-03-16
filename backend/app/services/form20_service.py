"""
Generate Form 20 (front and back) by overlaying filled data on blank PDF templates.
Uses Raw Scans/Official FORM-20 english.pdf (page 0=front, page 1=back) or separate PDFs.
If templates are missing, falls back to HTML-based generation.
Saves Form 20 Front.pdf and Form 20 Back.pdf to Uploaded scans/subfolder.
"""
import logging
import re
from pathlib import Path
from typing import Any

from app.config import UPLOADS_DIR, FORM20_TEMPLATE_SINGLE, FORM20_TEMPLATE_FRONT, FORM20_TEMPLATE_BACK

logger = logging.getLogger(__name__)

# Backend dir for HTML templates
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = _BACKEND_DIR / "templates"

# Field positions (x, y) in points for Form 20 front page. Adjust if template layout differs.
# Origin: top-left. y increases downward. Based on typical Form 20 layout.
FORM20_FIELD_POSITIONS = {
    "field_1_name_care_of": (120, 135),
    "field_3_address": (120, 175),
    "field_10_dealer_name_address": (120, 295),
    "field_14_body_type": (120, 365),
    "field_15_vehicle_type": (120, 390),
    "field_16_oem_name": (120, 415),
    "field_17_year_of_mfg": (120, 440),
    "field_18_num_cylinders": (120, 465),
    "field_19_horse_power": (120, 490),
    "field_21_length": (120, 535),
    "field_22_chassis_no": (120, 560),
    "field_24_seating_capacity": (120, 605),
    "field_25_fuel_type": (120, 630),
    "field_28_colour": (120, 675),
}


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
    data["footer_text"] = f"Dealer Saathi\u00a9 Vehicle ID {vid if vid else '\u2014'}"

    return data


def _overlay_on_pdf(
    template_path: Path,
    out_path: Path,
    data: dict[str, str],
    field_positions: dict[str, tuple[float, float]],
    fontsize: float = 10,
    footer_y: float = 820,
    page_index: int = 0,
) -> None:
    """Overlay form data on a PDF template. Adds footer at bottom left."""
    import fitz

    if not template_path.exists():
        raise FileNotFoundError(f"Form 20 template not found: {template_path}")

    doc = fitz.open(template_path)
    if doc.page_count <= page_index:
        doc.close()
        raise ValueError(f"Template has no page {page_index}: {template_path}")

    page = doc[page_index]
    # Overlay text above existing content
    for key, (x, y) in field_positions.items():
        val = data.get(key)
        if val and key != "footer_text":
            # Truncate long text to fit; insert_text doesn't wrap
            text = str(val)[:80] if len(str(val)) > 80 else str(val)
            page.insert_text(
                (x, y),
                text,
                fontsize=fontsize,
                fontname="helv",
                color=(0, 0, 0),
                overlay=True,
            )

    # Footer: left side, 10pt
    footer = data.get("footer_text", "")
    if footer:
        page.insert_text(
            (42, footer_y),
            footer,
            fontsize=10,
            fontname="helv",
            color=(0, 0, 0),
            overlay=True,
        )

    doc.save(str(out_path))
    doc.close()


def generate_form20_pdfs(
    subfolder: str,
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    vehicle_id: int | None = None,
    dealer_id: int | None = None,
    uploads_dir: Path | None = None,
) -> list[str]:
    """
    Generate Form 20 Front.pdf and Form 20 Back.pdf by overlaying data on blank templates.
    Uses Raw Scans/Form 20 Front.pdf and Form 20 back.pdf. Saves to Uploaded scans/subfolder.
    """
    import re

    uploads_path = Path(uploads_dir or UPLOADS_DIR).resolve()
    safe_sub = re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"
    subfolder_path = uploads_path / safe_sub
    subfolder_path.mkdir(parents=True, exist_ok=True)

    data = _build_form20_data(customer, vehicle, vehicle_id, dealer_id)

    # Template paths: config first, then fallback using uploads_dir parent (project root)
    uploads_path = Path(uploads_dir or UPLOADS_DIR).resolve()
    project_root = uploads_path.parent  # Uploaded scans is under project root

    single_template = Path(FORM20_TEMPLATE_SINGLE).resolve()
    front_template = Path(FORM20_TEMPLATE_FRONT).resolve()
    back_template = Path(FORM20_TEMPLATE_BACK).resolve()

    # Fallback: if config paths don't exist, try Raw Scans relative to project root
    if not single_template.exists():
        fallback = project_root / "Raw Scans" / "Official FORM-20 english.pdf"
        if fallback.exists():
            single_template = fallback.resolve()
    if not front_template.exists():
        fallback = project_root / "Raw Scans" / "Form 20 Front.pdf"
        if fallback.exists():
            front_template = fallback.resolve()
    if not back_template.exists():
        fallback = project_root / "Raw Scans" / "Form 20 back.pdf"
        if fallback.exists():
            back_template = fallback.resolve()

    use_single = single_template.exists()
    use_separate = front_template.exists() and back_template.exists()

    logger.info(
        "form20: single=%s exists=%s | front=%s exists=%s | back=%s exists=%s",
        single_template,
        use_single,
        front_template,
        front_template.exists(),
        back_template,
        back_template.exists(),
    )

    if not use_single and not use_separate:
        logger.warning("form20: No PDF templates found at %s or %s/%s. Using HTML fallback.", single_template, front_template, back_template)
        return _generate_form20_via_html(subfolder_path, data)

    try:
        import fitz  # noqa: F401
    except ImportError as e:
        logger.warning("form20: pymupdf not installed (%s). Using HTML fallback. Run: pip install pymupdf", e)
        return _generate_form20_via_html(subfolder_path, data)

    saved: list[str] = []

    if use_single:
        # Official FORM-20: page 0 = front, page 1 = back
        front_out = subfolder_path / "Form 20 Front.pdf"
        try:
            _overlay_on_pdf(
                single_template,
                front_out,
                data,
                FORM20_FIELD_POSITIONS,
                fontsize=10,
                footer_y=820,
                page_index=0,
            )
            saved.append("Form 20 Front.pdf")
            logger.info("form20: saved %s (from Official FORM-20 page 0)", front_out)
        except Exception as e:
            logger.warning("form20: PDF generation failed for front: %s", e)
            raise

        back_out = subfolder_path / "Form 20 Back.pdf"
        try:
            _overlay_on_pdf(
                single_template,
                back_out,
                data,
                {},
                fontsize=10,
                footer_y=820,
                page_index=1,
            )
            saved.append("Form 20 Back.pdf")
            logger.info("form20: saved %s (from Official FORM-20 page 1)", back_out)
        except Exception as e:
            logger.warning("form20: PDF generation failed for back: %s", e)
            raise
    else:
        # Separate front and back templates
        front_out = subfolder_path / "Form 20 Front.pdf"
        _overlay_on_pdf(
            front_template,
            front_out,
            data,
            FORM20_FIELD_POSITIONS,
            fontsize=10,
            footer_y=820,
        )
        saved.append("Form 20 Front.pdf")
        logger.info("form20: saved %s", front_out)

        back_out = subfolder_path / "Form 20 Back.pdf"
        _overlay_on_pdf(
            back_template,
            back_out,
            data,
            {},
            fontsize=10,
            footer_y=820,
        )
        saved.append("Form 20 Back.pdf")
        logger.info("form20: saved %s", back_out)

    return saved


def _generate_form20_via_html(
    subfolder_path: Path,
    data: dict[str, str],
) -> list[str]:
    """Fallback: generate Form 20 using HTML templates when PDF templates are missing."""
    front_html = (TEMPLATES_DIR / "form20_front.html").read_text(encoding="utf-8")
    back_html = (TEMPLATES_DIR / "form20_back.html").read_text(encoding="utf-8")
    for key, val in data.items():
        front_html = front_html.replace("{{" + key + "}}", val or "\u2014")
        back_html = back_html.replace("{{" + key + "}}", val or "\u2014")
    front_html = re.sub(r"\{\{[^}]+\}\}", "\u2014", front_html)
    back_html = re.sub(r"\{\{[^}]+\}\}", "\u2014", back_html)

    from xhtml2pdf import pisa

    saved = []
    for html, out_name in [(front_html, "Form 20 Front.pdf"), (back_html, "Form 20 Back.pdf")]:
        out_path = subfolder_path / out_name
        with open(out_path, "w+b") as dest:
            status = pisa.CreatePDF(html, dest=dest, encoding="utf-8")
        if status.err:
            raise RuntimeError(f"xhtml2pdf error: {status.err}")
        saved.append(out_name)
        logger.info("form20: saved %s (HTML fallback)", out_path)
    return saved
