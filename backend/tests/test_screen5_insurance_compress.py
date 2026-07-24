"""Screen 5 insurance PDF compression for Vahan upload size limit."""

import fitz
from pathlib import Path

from app.services.fill_rto_service import VAHAN_UPLOAD_MAX_BYTES, _screen_5_prepare_upload_file
from app.services.post_ocr_service import compress_pdf_for_upload


def _make_oversized_color_pdf(path: Path, *, pages: int = 3) -> None:
    """Build a PDF large enough to exceed Vahan upload limit (for compression tests)."""
    import fitz

    doc = fitz.open()
    try:
        for i in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), f"Insurance certificate page {i + 1}", fontsize=24)
            page.draw_rect(fitz.Rect(50, 50, 545, 792), color=(1, 0, 0), fill=(0.2, 0.4, 0.9))
            # Large inline pixmap inflates file size beyond VAHAN_UPLOAD_MAX_BYTES.
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 800, 1100), 0)
            pix.clear_with(180 + (i * 10))
            page.insert_image(fitz.Rect(60, 100, 535, 780), pixmap=pix)
            pix = None
        doc.save(str(path), garbage=0, deflate=False)
    finally:
        doc.close()


def test_compress_pdf_for_upload_under_vahan_limit(tmp_path: Path) -> None:
    src = tmp_path / "big_insurance.pdf"
    _make_oversized_color_pdf(src, pages=4)
    assert src.stat().st_size > VAHAN_UPLOAD_MAX_BYTES

    out = compress_pdf_for_upload(src, VAHAN_UPLOAD_MAX_BYTES, grayscale=True)
    assert len(out) <= VAHAN_UPLOAD_MAX_BYTES

    doc = fitz.open(stream=out, filetype="pdf")
    try:
        assert doc.page_count == 1
    finally:
        doc.close()


def test_screen5_prepare_upload_file_insurance(tmp_path: Path) -> None:
    src = tmp_path / "9650693610_Insurance_21072026.pdf"
    _make_oversized_color_pdf(src, pages=3)
    prepared = _screen_5_prepare_upload_file("INSURANCE CERTIFICATE", src)
    assert prepared.name.endswith("_vahan_upload.pdf")
    assert prepared.stat().st_size <= VAHAN_UPLOAD_MAX_BYTES


def test_screen5_prepare_upload_file_skips_non_insurance(tmp_path: Path) -> None:
    src = tmp_path / "Form_20.pdf"
    _make_oversized_color_pdf(src, pages=1)
    assert _screen_5_prepare_upload_file("FORM 20", src) is src
