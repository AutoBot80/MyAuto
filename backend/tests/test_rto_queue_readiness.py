"""RTO queue Vahan document readiness and Form 20 with cover gate."""

from pathlib import Path

import fitz

from app.services.fill_rto_service import resolve_vahan_upload_readiness
from app.services.form20_pencil_overlay import build_form20_with_cover_pdf


def _write_min_pdf(path: Path, text: str = "x") -> None:
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    doc[0].insert_text((20, 20), text)
    doc.save(str(path))
    doc.close()


def test_readiness_requires_merged_form20_with_cover(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_01012026"
    sale.mkdir()
    _write_min_pdf(sale / "9876543210_Form_20.pdf")
    ready, missing = resolve_vahan_upload_readiness(
        sale, subfolder=sale.name, mobile="9876543210"
    )
    assert not ready
    assert any("Form 20 with cover" in m for m in missing)


def test_readiness_passes_when_merged_form20_exists(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_01012026"
    sale.mkdir()
    _write_min_pdf(sale / "9876543210_Form_20.pdf")
    cover = sale / "Form_20_Cover_Page.jpg"
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
    pix.save(str(cover))
    pix = None
    merged = build_form20_with_cover_pdf(sale, "9876543210")
    assert merged is not None
    assert merged.name.endswith("_Form_20_with_cover.pdf")

    _write_min_pdf(sale / "9876543210_Sale_Certificate.pdf")
    _write_min_pdf(sale / "9876543210_Form22.pdf")
    _write_min_pdf(sale / "9876543210_Insurance_01012026.pdf")
    _write_min_pdf(sale / "9876543210_GST_Retail_Invoice.pdf")
    (sale / "Aadhar_front.jpg").write_bytes(cover.read_bytes())
    (sale / "Aadhar_back.jpg").write_bytes(cover.read_bytes())
    _write_min_pdf(sale / "Sales_Detail_Sheet.pdf")

    ready, missing = resolve_vahan_upload_readiness(
        sale, subfolder=sale.name, mobile="9876543210"
    )
    assert ready, missing


def test_build_form20_with_cover_includes_front_back_and_dms(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_01012026"
    sale.mkdir()
    _write_min_pdf(sale / "9876543210_Form_20.pdf", "dms")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
    cover = sale / "Form_20_Cover_Page.jpg"
    cover_back = sale / "Form_20_Cover_Back_Page.jpg"
    pix.save(str(cover))
    pix.save(str(cover_back))
    pix = None

    merged = build_form20_with_cover_pdf(sale, "9876543210")
    assert merged is not None
    doc = fitz.open(str(merged))
    try:
        assert doc.page_count == 3
    finally:
        doc.close()
