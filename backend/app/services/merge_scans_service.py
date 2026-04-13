"""
Merge raw scans (Aadhar back, Insurance, Aadhar front, Details sheet) into a combined PDF.
Order: Aadhar_back, Insurance, Aadhar front, Sales detail (PDF or image).
Output: Bulk Upload/Input Scans/<subfolder>/Scans.pdf
"""
import logging
from pathlib import Path

from app.services.page_classifier import (
    FILENAME_AADHAR_FRONT,
    FILENAME_SALES_DETAIL_SHEET_PDF,
    LEGACY_AADHAR_FRONT_JPG,
    LEGACY_DETAILS_JPG,
)

logger = logging.getLogger(__name__)

# First existing file in each slot wins (order within slot: preferred name first)
MERGE_ORDER_SLOTS: list[list[str]] = [
    ["Aadhar_back.jpg"],
    ["Insurance.jpg"],
    [FILENAME_AADHAR_FRONT, LEGACY_AADHAR_FRONT_JPG],
    [FILENAME_SALES_DETAIL_SHEET_PDF, LEGACY_DETAILS_JPG],
]


def merge_scans_for_subfolder(
    subfolder_path: Path,
    output_dir: Path,
    subfolder_name: str | None = None,
) -> Path | None:
    """
    Merge Aadhar back, Insurance, Aadhar front, Details into one PDF.
    Returns output path if successful, None if no files found.
    """
    import fitz

    files_to_merge: list[Path] = []
    for slot in MERGE_ORDER_SLOTS:
        found: Path | None = None
        for name in slot:
            p = subfolder_path / name
            if p.exists():
                found = p
                break
        if found is not None:
            files_to_merge.append(found)
        else:
            logger.debug("merge_scans: skip missing slot %s in %s", slot, subfolder_path)

    if not files_to_merge:
        logger.warning("merge_scans: no files to merge in %s", subfolder_path)
        return None

    name = subfolder_name or subfolder_path.name
    out_subdir = output_dir / name
    out_subdir.mkdir(parents=True, exist_ok=True)
    out_path = out_subdir / "Scans.pdf"

    merged = fitz.open()
    try:
        for f in files_to_merge:
            doc = fitz.open(str(f))
            # Images (jpg/png) must be converted to PDF before insert_pdf
            if doc.is_pdf:
                merged.insert_pdf(doc, from_page=0, to_page=-1)
            else:
                pdf_bytes = doc.convert_to_pdf()
                doc.close()
                img_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
                merged.insert_pdf(img_pdf, from_page=0, to_page=-1)
                img_pdf.close()
                continue
            doc.close()
        merged.save(str(out_path))
        logger.info("merge_scans: saved %s (%d pages from %s)", out_path, len(files_to_merge), subfolder_path)
        return out_path
    finally:
        merged.close()


def merge_all_scans(
    uploads_dir: Path,
    output_base: Path,
) -> list[dict]:
    """
    Process all subfolders under uploads_dir; merge scans into output_base/Input Scans/<subfolder>/Scans.pdf.
    Returns list of {subfolder, output_path, pages, error}.
    """
    output_dir = output_base / "Input Scans"
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for subdir in sorted(uploads_dir.iterdir()):
        if not subdir.is_dir():
            continue
        try:
            out = merge_scans_for_subfolder(subdir, output_dir, subfolder_name=subdir.name)
            if out:
                results.append({"subfolder": subdir.name, "output_path": str(out), "ok": True})
            else:
                results.append({"subfolder": subdir.name, "ok": False, "error": "No files to merge"})
        except Exception as e:
            logger.exception("merge_scans: failed for %s", subdir)
            results.append({"subfolder": subdir.name, "ok": False, "error": str(e)})

    return results
