"""
Bulk upload processing: classify pages by pre-OCR, split into logical files, run Add Sales flow.

Pages can be in any order. Pre-OCR text classifies each page (Aadhar, Aadhar_back,
Details, Insurance). Unrecognized pages go to unused.pdf. Split files are written
to Uploaded scans/<subfolder>/. The OCR service and submit_info read these
(Aadhar.jpg for QR/customer, Details.jpg for vehicle/insurance, etc.).
"""
import logging
import shutil
from pathlib import Path

from app.config import (
    UPLOADS_DIR,
    BULK_UPLOAD_DIR,
    OCR_OUTPUT_DIR,
    DMS_BASE_URL,
    DMS_LOGIN_USER,
    DMS_LOGIN_PASSWORD,
)
from app.repositories.bulk_loads import BulkLoadsRepository

logger = logging.getLogger(__name__)

# Expected filenames from page classification (order no longer fixed)
CLASSIFIED_FILENAMES = ["Aadhar_back.jpg", "Insurance.jpg", "Aadhar.jpg", "Details.jpg"]


def _copy_classified_to_uploads(classified_dir: Path, uploads_subdir: Path) -> list[Path]:
    """Copy pre-classified images from Processing to uploads subfolder. Returns list of copied paths."""
    copied: list[Path] = []
    for name in CLASSIFIED_FILENAMES:
        src = classified_dir / name
        if src.exists():
            dst = uploads_subdir / name
            shutil.copy2(str(src), str(dst))
            copied.append(dst)
    # Also copy unused.pdf if present (for operator reference)
    unused_src = classified_dir / "unused.pdf"
    if unused_src.exists():
        dst = uploads_subdir / "unused.pdf"
        shutil.copy2(str(unused_src), str(dst))
        copied.append(dst)
    return copied


def _split_pdf_to_images(pdf_path: Path, out_dir: Path) -> list[Path]:
    """Split PDF pages to JPG images. Returns list of saved paths."""
    import fitz
    from PIL import Image
    import io

    doc = fitz.open(str(pdf_path))
    saved: list[Path] = []
    try:
        for i in range(min(doc.page_count, len(CLASSIFIED_FILENAMES))):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            out_path = out_dir / (CLASSIFIED_FILENAMES[i] if i < len(CLASSIFIED_FILENAMES) else f"page_{i}.jpg")
            img.save(out_path, "JPEG", quality=90)
            saved.append(out_path)
    finally:
        doc.close()
    return saved


def _extract_mobile_from_subfolder(subfolder: str) -> str | None:
    """Extract 10-digit mobile from subfolder like 9650693610_170326."""
    parts = subfolder.split("_")
    if parts:
        digits = "".join(c for c in parts[0] if c.isdigit())
        if len(digits) >= 10:
            return digits[:10]
        if len(digits) > 0:
            return digits
    return None


def process_bulk_pdf(
    source_path: Path,
    dealer_id: int = 100001,
    subfolder_override: str | None = None,
) -> dict:
    """
    Process bulk upload: copy classified files or split PDF, run OCR, submit info, fill DMS, print forms.
    source_path: either a directory with pre-classified files (Aadhar.jpg, etc.) or a PDF to split by position.
    Returns {ok, subfolder, mobile, name, error}.
    subfolder_override: if set, use this for uploads dir (e.g. mobile_ddmmyy from pre-OCR).
    """
    subfolder = subfolder_override or (source_path.parent.name if source_path.is_file() else source_path.name)
    mobile = _extract_mobile_from_subfolder(subfolder)
    uploads_subdir = Path(UPLOADS_DIR) / subfolder
    uploads_subdir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Copy classified files or split PDF to images
        if source_path.is_dir():
            saved = _copy_classified_to_uploads(source_path, uploads_subdir)
        else:
            saved = _split_pdf_to_images(source_path, uploads_subdir)
        # Need at least Details.jpg and Aadhar.jpg for Add Customer
        has_details = (uploads_subdir / "Details.jpg").exists()
        has_aadhar = (uploads_subdir / "Aadhar.jpg").exists()
        if not has_details or not has_aadhar:
            missing = []
            if not has_details:
                missing.append("sales details form (vehicle & customer info)")
            if not has_aadhar:
                missing.append("Aadhar card")
            return {
                "ok": False,
                "subfolder": subfolder,
                "mobile": mobile,
                "error": f"Missing required pages: {', '.join(missing)}. Ensure your scan includes the sales details form (with Frame No, Chassis, Engine No) and Aadhar card (front & back).",
            }

        # 2. Run OCR extraction
        from app.services.ocr_service import OcrService
        ocr = OcrService()
        ocr.process_uploaded_subfolder(subfolder)
        details = ocr.get_extracted_details(subfolder)
        if not details:
            return {"ok": False, "subfolder": subfolder, "mobile": mobile, "error": "OCR extraction failed"}

        customer = details.get("customer") or {}
        vehicle = details.get("vehicle") or {}
        insurance = details.get("insurance") or {}

        # 3. Submit info (requires aadhar last 4 + mobile)
        aadhar = customer.get("aadhar_id") or ""
        if not aadhar:
            return {"ok": False, "subfolder": subfolder, "mobile": mobile, "error": "Aadhar not extracted"}
        mobile_val = mobile or str(customer.get("mobile_number") or customer.get("mobile") or "")
        if not mobile_val:
            return {"ok": False, "subfolder": subfolder, "mobile": mobile, "error": "Mobile not in subfolder or extracted"}

        from app.services.submit_info_service import submit_info
        submit_result = submit_info(
            customer={
                "aadhar_id": aadhar,
                "name": customer.get("name"),
                "address": customer.get("address"),
                "city": customer.get("city"),
                "state": customer.get("state"),
                "pin_code": customer.get("pin_code") or customer.get("pin"),
                "mobile_number": int(mobile_val) if mobile_val.isdigit() else mobile_val,
                "gender": customer.get("gender"),
                "date_of_birth": customer.get("date_of_birth"),
                "profession": insurance.get("profession"),
            },
            vehicle={
                "frame_no": vehicle.get("chassis") or vehicle.get("frame_num") or vehicle.get("frame_no"),
                "engine_no": vehicle.get("engine_num") or vehicle.get("engine_no"),
                "key_no": vehicle.get("key_num") or vehicle.get("key_no"),
                "battery_no": vehicle.get("battery_no"),
            },
            insurance={
                "nominee_name": insurance.get("nominee_name"),
                "nominee_age": insurance.get("nominee_age"),
                "nominee_relationship": insurance.get("nominee_relationship"),
                "insurer": insurance.get("insurer"),
                "policy_num": insurance.get("policy_num"),
                "policy_from": insurance.get("policy_from"),
                "policy_to": insurance.get("policy_to"),
                "premium": insurance.get("premium"),
            },
            dealer_id=dealer_id,
            file_location=subfolder,
        )
        customer_id = submit_result.get("customer_id")
        vehicle_id = submit_result.get("vehicle_id")

        # 4. Fill DMS (headless)
        from app.services.fill_dms_service import run_fill_dms
        dms_result = run_fill_dms(
            dms_base_url=DMS_BASE_URL or "http://127.0.0.1:8000/dummy-dms",
            subfolder=subfolder,
            customer={"name": customer.get("name"), "address": customer.get("address"), "city": customer.get("city"), "state": customer.get("state"), "pin_code": customer.get("pin_code") or customer.get("pin"), "mobile_number": mobile_val},
            vehicle={"key_no": vehicle.get("key_num") or vehicle.get("key_no"), "frame_no": vehicle.get("chassis") or vehicle.get("frame_num"), "engine_no": vehicle.get("engine_num") or vehicle.get("engine_no")},
            login_user=DMS_LOGIN_USER or "demo",
            login_password=DMS_LOGIN_PASSWORD or "demo",
            uploads_dir=Path(UPLOADS_DIR),
            ocr_output_dir=Path(OCR_OUTPUT_DIR),
            vahan_base_url=None,
            headless=True,
        )
        if dms_result.get("vehicle") and vehicle_id:
            from app.services.fill_dms_service import update_vehicle_master_from_dms
            try:
                update_vehicle_master_from_dms(vehicle_id, dms_result["vehicle"])
            except Exception as e:
                logger.warning("bulk: vehicle_master update failed: %s", e)

        # 5. Print forms (Form 20 + Gate Pass)
        from app.services.form20_service import generate_form20_pdfs
        generate_form20_pdfs(
            subfolder=subfolder,
            customer=customer,
            vehicle={**vehicle, **dms_result.get("vehicle", {})},
            vehicle_id=vehicle_id,
            dealer_id=dealer_id,
            uploads_dir=Path(UPLOADS_DIR),
        )

        name = customer.get("name") or ""
        return {"ok": True, "subfolder": subfolder, "mobile": mobile or mobile_val, "name": name}
    except Exception as e:
        logger.exception("bulk: process failed for %s", subfolder)
        return {"ok": False, "subfolder": subfolder, "mobile": mobile, "error": str(e)}


def process_new_scans_and_record(
    bulk_load_id: int,
    source_path: Path,
    dealer_id: int = 100001,
    subfolder_override: str | None = None,
) -> bool:
    """Run process_bulk_pdf and update bulk_loads row. source_path: classified dir or PDF. Returns True if success."""
    from app.db import get_connection
    result = process_bulk_pdf(source_path, dealer_id=dealer_id, subfolder_override=subfolder_override)
    conn = get_connection()
    try:
        if result.get("ok"):
            BulkLoadsRepository.update_status(
                conn, bulk_load_id, "Success",
                error_message=None,
                mobile=result.get("mobile"),
                name=result.get("name"),
            )
            conn.commit()
            return True
        else:
            BulkLoadsRepository.update_status(
                conn, bulk_load_id, "Error",
                error_message=result.get("error"),
                mobile=result.get("mobile"),
                name=result.get("name"),
            )
            conn.commit()
            return False
    finally:
        conn.close()


def discover_new_scans() -> list[Path]:
    """Find Scans.pdf files in Bulk Upload/Input Scans that have not been processed (no .processed marker)."""
    scans_dir = BULK_UPLOAD_DIR / "Input Scans"
    if not scans_dir.is_dir():
        return []
    found: list[Path] = []
    for subdir in scans_dir.iterdir():
        if not subdir.is_dir():
            continue
        pdf_path = subdir / "Scans.pdf"
        if pdf_path.exists() and not (subdir / ".processed").exists():
            found.append(pdf_path)
    return found
