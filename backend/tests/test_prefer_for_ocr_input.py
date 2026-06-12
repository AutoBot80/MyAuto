"""Resolve Aadhaar and other scan paths under for_OCR/ after pre-OCR split."""

from pathlib import Path

from app.services.page_classifier import FILENAME_AADHAR_FRONT, LEGACY_AADHAR_FRONT_JPG
from app.services.sales_ocr_service import (
    _load_aadhar_scan_bytes,
    _prefer_for_ocr_input,
)


def test_prefer_for_ocr_input_finds_aadhar_jpegs_under_for_ocr(tmp_path: Path) -> None:
    sale = tmp_path / "7240275304_120626"
    for_ocr = sale / "for_OCR"
    for_ocr.mkdir(parents=True)
    (for_ocr / FILENAME_AADHAR_FRONT).write_bytes(b"front")
    (for_ocr / "Aadhar_back.jpg").write_bytes(b"back")

    front = _prefer_for_ocr_input(sale, "Aadhar.pdf", FILENAME_AADHAR_FRONT, LEGACY_AADHAR_FRONT_JPG)
    back = _prefer_for_ocr_input(sale, "Aadhar_back.pdf", "Aadhar_back.jpg")

    assert front == for_ocr / FILENAME_AADHAR_FRONT
    assert back == for_ocr / "Aadhar_back.jpg"
    assert front.is_file()
    assert back.is_file()


def test_load_aadhar_scan_bytes_reads_for_ocr_jpegs(tmp_path: Path) -> None:
    sale = tmp_path / "7240275304_120626"
    for_ocr = sale / "for_OCR"
    for_ocr.mkdir(parents=True)
    (for_ocr / FILENAME_AADHAR_FRONT).write_bytes(b"front-bytes")
    (for_ocr / "Aadhar_back.jpg").write_bytes(b"back-bytes")

    bundle = _load_aadhar_scan_bytes(sale)

    assert bundle["front_bytes"] == b"front-bytes"
    assert bundle["back_bytes"] == b"back-bytes"
    assert bundle["front_src"] == "for_OCR/Aadhar_front.jpg"
    assert bundle["back_src"] == "for_OCR/Aadhar_back.jpg"


def test_prefer_for_ocr_input_prefers_root_legacy_when_for_ocr_missing(tmp_path: Path) -> None:
    sale = tmp_path / "legacy_sale"
    sale.mkdir()
    (sale / LEGACY_AADHAR_FRONT_JPG).write_bytes(b"legacy")

    front = _prefer_for_ocr_input(sale, "Aadhar.pdf", FILENAME_AADHAR_FRONT, LEGACY_AADHAR_FRONT_JPG)

    assert front == sale / LEGACY_AADHAR_FRONT_JPG
