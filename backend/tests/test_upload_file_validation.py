"""Magic-byte and filename helpers for uploads."""

import pytest

from app.services.upload_file_validation import (
    detect_image_or_pdf_kind,
    sanitize_legacy_upload_filename,
    validate_magic_jpeg_or_png,
    validate_magic_jpeg_png_pdf_legacy,
)


def test_detect_jpeg() -> None:
    assert detect_image_or_pdf_kind(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "jpeg"


def test_detect_png() -> None:
    assert detect_image_or_pdf_kind(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR") == "png"


def test_detect_pdf() -> None:
    assert detect_image_or_pdf_kind(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n") == "pdf"


def test_validate_jpeg_or_png_rejects_pdf() -> None:
    with pytest.raises(ValueError, match="magic-byte"):
        validate_magic_jpeg_or_png(b"%PDF-1.4\n", label="x")


def test_legacy_allows_pdf() -> None:
    validate_magic_jpeg_png_pdf_legacy(b"%PDF-1.4\n", label="doc.pdf")


def test_sanitize_blocks_exe() -> None:
    with pytest.raises(ValueError, match="blocked"):
        sanitize_legacy_upload_filename("report.exe")


def test_sanitize_ok() -> None:
    assert sanitize_legacy_upload_filename("scan_1.jpg") == "scan_1.jpg"
