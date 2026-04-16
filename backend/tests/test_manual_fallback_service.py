"""Tests for manual OCR fallback split and apply."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.manual_fallback_service import (
    ROLE_AADHAR_BACK,
    ROLE_AADHAR_FRONT,
    ROLE_DETAILS,
    ROLE_UNUSED,
    apply_manual_session,
    read_details_forms_cache,
    validate_session_id,
    write_details_forms_cache,
    write_manual_session_jpegs,
)


def _rgb_pdf_with_pages(tmp_path: Path, n: int) -> Path:
    import fitz

    doc = fitz.open()
    for _ in range(n):
        page = doc.new_page(width=200, height=280)
        page.draw_rect(fitz.Rect(10, 10, 190, 270), color=(0.2, 0.3, 0.5), fill=(0.9, 0.9, 0.95))
    pdf = tmp_path / "t.pdf"
    doc.save(str(pdf))
    doc.close()
    return pdf


@patch("app.services.manual_fallback_service.get_add_sales_pre_ocr_work_dir")
def test_details_forms_cache_roundtrip(mock_work, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mock_work.return_value = work
    pdf = _rgb_pdf_with_pages(tmp_path, 3)
    session_id, _ = write_manual_session_jpegs(100001, pdf, {})
    cache = {"full_text": "hello", "key_value_pairs": [], "tables": [], "error": None}
    write_details_forms_cache(100001, session_id, cache)
    loaded = read_details_forms_cache(100001, session_id)
    assert loaded == cache


def test_validate_session_id() -> None:
    assert validate_session_id("a" * 32)
    assert not validate_session_id("short")
    assert not validate_session_id("")


@patch("app.services.manual_fallback_service.get_add_sales_pre_ocr_work_dir")
def test_write_and_apply_manual_session(mock_work, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mock_work.return_value = work

    pdf = _rgb_pdf_with_pages(tmp_path, 4)
    session_id, page_count = write_manual_session_jpegs(100001, pdf, {})
    assert page_count == 4
    assert validate_session_id(session_id)

    session_dir = work / "manual_sessions" / session_id
    assert session_dir.is_dir()
    for i in range(1, 5):
        p = session_dir / f"page_{i:02d}.jpg"
        assert p.is_file()
        assert p.stat().st_size <= 210 * 1024

    uploads = tmp_path / "uploads"
    uploads.mkdir()

    ocr_out = tmp_path / "ocr_output"
    ocr_out.mkdir()

    with patch("app.services.manual_fallback_service.get_uploads_dir", return_value=uploads):
        sub, saved = apply_manual_session(
            100001,
            session_id,
            "7014512345",
            {
                "0": ROLE_AADHAR_FRONT,
                "1": ROLE_AADHAR_BACK,
                "2": ROLE_DETAILS,
                "3": ROLE_UNUSED,
            },
            ocr_output_dir=ocr_out,
        )

    assert sub
    assert "for_OCR/Aadhar_front.jpg" in saved or any("Aadhar_front" in s for s in saved)
    sale = uploads / sub
    assert (sale / "for_OCR" / "Aadhar_front.jpg").is_file()
    assert (sale / "for_OCR" / "Aadhar_back.jpg").is_file()
    assert (sale / "for_OCR" / "Sales_Detail_Sheet.pdf").is_file()
    assert (sale / "unused.pdf").is_file()
    assert not session_dir.exists()


@patch("app.services.manual_fallback_service.get_add_sales_pre_ocr_work_dir")
def test_apply_rejects_placeholder_mobile(mock_work, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mock_work.return_value = work
    pdf = _rgb_pdf_with_pages(tmp_path, 3)
    session_id, _ = write_manual_session_jpegs(100001, pdf, {})
    with pytest.raises(ValueError, match="placeholder"):
        apply_manual_session(
            100001,
            session_id,
            "9876543210",
            {"0": ROLE_AADHAR_FRONT, "1": ROLE_AADHAR_BACK, "2": ROLE_DETAILS},
        )


@patch("app.services.manual_fallback_service.get_add_sales_pre_ocr_work_dir")
def test_apply_rejects_bad_role_count(mock_work, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    mock_work.return_value = work
    pdf = _rgb_pdf_with_pages(tmp_path, 3)
    session_id, _ = write_manual_session_jpegs(100001, pdf, {})

    with pytest.raises(ValueError, match="Assign exactly one"):
        apply_manual_session(
            100001,
            session_id,
            "7014512345",
            {"0": ROLE_AADHAR_FRONT, "1": ROLE_AADHAR_BACK, "2": ROLE_UNUSED},
        )
