"""Upload response saved_files includes pending for_OCR documents."""

from pathlib import Path

from app.services.upload_service import list_sale_saved_filenames


def test_list_sale_saved_filenames_includes_pending_for_ocr(tmp_path: Path) -> None:
    sale = tmp_path / "7240275304_120626"
    sale.mkdir()
    for_ocr = sale / "for_OCR"
    for_ocr.mkdir()
    (sale / "Aadhar_front.jpg").write_bytes(b"j")
    (sale / "Sales_Detail_Sheet.pdf").write_bytes(b"p")
    (for_ocr / "Form_20_Cover_Page.jpg").write_bytes(b"f")

    names = list_sale_saved_filenames(sale)

    assert "Aadhar_front.jpg" in names
    assert "Sales_Detail_Sheet.pdf" in names
    assert "Form_20_Cover_Page.jpg" in names
    assert all("/" not in n for n in names)


def test_list_sale_saved_filenames_skips_for_ocr_when_already_at_root(tmp_path: Path) -> None:
    sale = tmp_path / "7240275304_120626"
    for_ocr = sale / "for_OCR"
    for_ocr.mkdir(parents=True)
    (sale / "Form_20_Cover_Page.jpg").write_bytes(b"f")
    (for_ocr / "Form_20_Cover_Page.jpg").write_bytes(b"g")

    names = list_sale_saved_filenames(sale)

    assert names.count("Form_20_Cover_Page.jpg") == 1
