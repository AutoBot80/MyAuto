"""Mixed PDF + JPEG consolidated upload page loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.services.pre_ocr_service import _paths_to_page_images, pre_ocr_pdf
from app.services.manual_fallback_service import write_manual_session_jpegs


def _rgb_jpeg(tmp_path: Path, name: str = "page.jpg") -> Path:
    p = tmp_path / name
    Image.new("RGB", (120, 160), color=(200, 210, 220)).save(p, "JPEG")
    return p


def _rgb_pdf_with_pages(tmp_path: Path, n: int, name: str = "sheet.pdf") -> Path:
    import fitz

    doc = fitz.open()
    for _ in range(n):
        page = doc.new_page(width=200, height=280)
        page.draw_rect(fitz.Rect(10, 10, 190, 270), color=(0.2, 0.3, 0.5), fill=(0.9, 0.9, 0.95))
    pdf = tmp_path / name
    doc.save(str(pdf))
    doc.close()
    return pdf


def test_paths_to_page_images_jpeg_then_pdf(tmp_path: Path) -> None:
    jpeg = _rgb_jpeg(tmp_path, "aadhar_back.jpg")
    pdf = _rgb_pdf_with_pages(tmp_path, 2, "sales_detail.pdf")

    pages, osd, _timings = _paths_to_page_images([jpeg, pdf], fix_orientation=False)

    assert len(pages) == 3
    assert [idx for idx, _ in pages] == [0, 1, 2]
    assert len(osd) == 3


def test_paths_to_page_images_pdf_then_jpeg(tmp_path: Path) -> None:
    pdf = _rgb_pdf_with_pages(tmp_path, 1, "sales_detail.pdf")
    jpeg_a = _rgb_jpeg(tmp_path, "aadhar_front.jpg")
    jpeg_b = _rgb_jpeg(tmp_path, "aadhar_back.jpg")

    pages, _, _ = _paths_to_page_images([pdf, jpeg_a, jpeg_b], fix_orientation=False)

    assert len(pages) == 3


@patch("app.services.sales_textract_service.extract_text_from_bytes")
def test_pre_ocr_pdf_mixed_jpeg_primary_and_pdf_extra(mock_ddt, tmp_path: Path) -> None:
    mock_ddt.return_value = {"full_text": "Sales Detail Sheet", "blocks": [], "error": None}

    jpeg = _rgb_jpeg(tmp_path, "aadhar_back.jpg")
    pdf = _rgb_pdf_with_pages(tmp_path, 1, "sales_detail.pdf")
    proc = tmp_path / "proc"
    proc.mkdir()

    full_text, ocr_path, _mobile, steps, page_images, _osd, ddt = pre_ocr_pdf(
        jpeg,
        processing_dir=proc,
        extra_image_paths=[pdf],
    )

    assert "pre_ocr_error" not in [s[0] for s in steps]
    assert len(page_images) == 2
    assert ocr_path is not None and ocr_path.is_file()
    assert "Page 1" in full_text
    assert len(ddt) == 2


@patch("app.services.manual_fallback_service.get_add_sales_pre_ocr_work_dir")
def test_manual_session_jpegs_mixed_paths(mock_work, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mock_work.return_value = work

    jpeg = _rgb_jpeg(tmp_path, "aadhar_back.jpg")
    pdf = _rgb_pdf_with_pages(tmp_path, 3, "sales_detail.pdf")

    session_id, page_count = write_manual_session_jpegs(
        100001,
        jpeg,
        {},
        extra_image_paths=[pdf],
    )

    assert page_count == 4
    session_dir = work / "manual_sessions" / session_id
    assert (session_dir / "page_04.jpg").is_file()
