"""Dealer signature on Form 20 page 3 (right mid), not page 1."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

from app.services.dealer_sign_overlay import (
    collect_pdfs_to_stamp,
    overlay_signature_bottom_right_all_pages,
)


def _form20_three_pages(path: Path) -> None:
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Form 20 page {i + 1}")
    doc.save(str(path))
    doc.close()


def test_collect_pdfs_to_stamp_form20_page3_mid(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_010126"
    sale.mkdir()
    _form20_three_pages(sale / "9876543210_Form_20.pdf")
    items = collect_pdfs_to_stamp(sale, sale.name, "9876543210")
    assert len(items) == 1
    assert items[0][1] == [2]
    assert items[0][2] == "mid"


def test_overlay_signature_on_form20_page3_only(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_010126"
    sale.mkdir()
    form20 = sale / "9876543210_Form_20.pdf"
    sig = sale / "100001_sign.jpg"
    out = sale / "9876543210_Form_20_signed.pdf"
    _form20_three_pages(form20)
    Image.new("RGB", (80, 40), color=(0, 0, 0)).save(sig, "JPEG")

    overlay_signature_bottom_right_all_pages(
        form20, sig, out, page_indices=[2], vertical_anchor="mid"
    )
    assert out.is_file()
    doc = fitz.open(str(out))
    try:
        assert len(doc) == 3
        assert len(doc[0].get_images()) == 0
        assert len(doc[1].get_images()) == 0
        assert len(doc[2].get_images()) >= 1
    finally:
        doc.close()
