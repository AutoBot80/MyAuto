"""Hail-mary DOB from ``pre_ocr_ddt_text.txt`` when Textract front misses it."""

from pathlib import Path

from app.services.sales_ocr_service import (
    _extract_dob_hail_mary_from_pre_ocr_ddt_blob,
    _maybe_fill_dob_from_pre_ocr_ddt_file,
)


def test_extract_dob_bridob_garbage_prefix():
    blob = "noise - BRIDOB: 12/12/1999 tail"
    assert _extract_dob_hail_mary_from_pre_ocr_ddt_blob(blob) == "12/12/1999"


def test_extract_dob_clean_dob_label():
    blob = "DOB: 01-05-2001"
    assert _extract_dob_hail_mary_from_pre_ocr_ddt_blob(blob) == "01/05/2001"


def test_maybe_fill_reads_file(tmp_path: Path):
    sub = "8302588348_140526"
    base = tmp_path / sub
    base.mkdir(parents=True)
    (base / "pre_ocr_ddt_text.txt").write_text("x DOB: 15/08/1996\n", encoding="utf-8")
    cust: dict = {}
    _maybe_fill_dob_from_pre_ocr_ddt_file(tmp_path, sub, cust)
    assert cust.get("date_of_birth") == "15/08/1996"
    assert cust.get("year_of_birth") == "1996"


def test_maybe_fill_skips_when_dob_present(tmp_path: Path):
    sub = "8302588348_140526"
    base = tmp_path / sub
    base.mkdir(parents=True)
    (base / "pre_ocr_ddt_text.txt").write_text("DOB: 15/08/1996\n", encoding="utf-8")
    cust = {"date_of_birth": "01/01/2000"}
    _maybe_fill_dob_from_pre_ocr_ddt_file(tmp_path, sub, cust)
    assert cust["date_of_birth"] == "01/01/2000"
