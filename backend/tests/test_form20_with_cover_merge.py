"""Merge scanned Form 20 cover with DMS Form 20 PDF."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

from app.services.form20_pencil_overlay import build_form20_with_cover_pdf


def _minimal_pdf(path: Path, pages: int = 2) -> None:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"DMS page {i + 1}")
    doc.save(str(path))
    doc.close()


def test_build_form20_with_cover_pdf(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_120625"
    sale.mkdir()
    cover = sale / "Form_20_Cover_Page.jpg"
    Image.new("RGB", (200, 280), color=(240, 240, 240)).save(cover, "JPEG")
    dms = sale / "9876543210_Form_20.pdf"
    _minimal_pdf(dms, pages=3)

    merged = build_form20_with_cover_pdf(sale, "9876543210")
    assert merged is not None
    assert merged.name == "9876543210_Form_20_with_cover.pdf"
    doc = fitz.open(str(merged))
    try:
        assert doc.page_count == 4
    finally:
        doc.close()


def test_build_form20_without_cover_returns_dms_only(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_120625"
    sale.mkdir()
    dms = sale / "9876543210_Form_20.pdf"
    _minimal_pdf(dms, pages=2)

    out = build_form20_with_cover_pdf(sale, "9876543210")
    assert out == dms
