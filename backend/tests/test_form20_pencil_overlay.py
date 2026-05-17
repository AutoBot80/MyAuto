"""Form 20 pencil mark composite (JPEG stamp must not use show_pdf_page)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PIL import Image

from app.services.form20_pencil_overlay import (
    composite_form20_first_page_with_stamp,
    form20_pencil_overlay_write_only,
    prepare_details_pencil_and_form20_overlay,
)


def _minimal_form20(path: Path) -> None:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Form 20")
    doc.save(str(path))
    doc.close()


def _minimal_pencil_jpeg(path: Path) -> None:
    Image.new("RGB", (120, 60), color=(30, 30, 30)).save(path, "JPEG")


def test_composite_jpeg_pencil_onto_form20(tmp_path: Path) -> None:
    sale = tmp_path / "8278671032_160526"
    sale.mkdir()
    form20 = sale / "8278671032_Form_20.pdf"
    pencil = sale / "pencil_mark.jpeg"
    _minimal_form20(form20)
    _minimal_pencil_jpeg(pencil)
    out = sale / "8278671032_Form_20_with_pencil_mark.pdf"
    composite_form20_first_page_with_stamp(form20, pencil, out)
    assert out.is_file()
    assert out.stat().st_size > 500


def test_form20_pencil_inplace_updates_same_pdf(tmp_path: Path) -> None:
    sale = tmp_path / "8278671032_160526"
    sale.mkdir()
    form20 = sale / "8278671032_Form_20.pdf"
    pencil = sale / "pencil_mark.jpeg"
    _minimal_form20(form20)
    _minimal_pencil_jpeg(pencil)
    before = form20.stat().st_size
    stamped, note = form20_pencil_overlay_write_only(sale, "8278671032", inplace=True)
    assert note is None
    assert stamped == form20
    assert form20.is_file()
    assert form20.stat().st_size >= before


def test_prepare_overlay_uses_existing_pencil_mark(tmp_path: Path) -> None:
    sale = tmp_path / "8278671032_160526"
    sale.mkdir()
    _minimal_form20(sale / "8278671032_Form_20.pdf")
    _minimal_pencil_jpeg(sale / "pencil_mark.jpeg")
    result = prepare_details_pencil_and_form20_overlay(sale)
    assert result["pencil_crop_written"] is True
    assert result["form20_stamped_path"] is not None
    assert "used_existing_pencil_mark" in str(result.get("note") or "")
