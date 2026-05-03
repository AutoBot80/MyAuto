"""Tests for removing empty pre-OCR artifact dirs when the mobile ocr_output folder is canonical."""

from __future__ import annotations

from pathlib import Path

from app.services.ocr_sale_artifacts import remove_if_empty_initial_artifact_dir


def test_remove_if_empty_drops_stale_artifact_dir(tmp_path: Path) -> None:
    root = tmp_path / "ocr_output" / "100001"
    mobile = root / "9784542030_250426"
    init = root / "add_sales_PDF3_250426"
    mobile.mkdir(parents=True, exist_ok=True)
    init.mkdir(parents=True, exist_ok=True)
    (mobile / "OCR_To_be_Used.json").write_text("{}", encoding="utf-8")

    remove_if_empty_initial_artifact_dir(root, "9784542030_250426", "add_sales_PDF3_250426")

    assert not init.exists()
    assert mobile.is_dir()


def test_remove_if_empty_noop_when_artifact_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "ocr_output" / "100001"
    mobile = root / "9784542030_250426"
    init = root / "add_sales_PDF3_250426"
    mobile.mkdir(parents=True, exist_ok=True)
    init.mkdir(parents=True, exist_ok=True)
    (mobile / "OCR_To_be_Used.json").write_text("{}", encoding="utf-8")
    (init / "pre_ocr_ddt_text.txt").write_text("x", encoding="utf-8")

    remove_if_empty_initial_artifact_dir(root, "9784542030_250426", "add_sales_PDF3_250426")

    assert init.is_dir()
    assert (init / "pre_ocr_ddt_text.txt").is_file()


def test_remove_if_empty_noop_when_names_equal(tmp_path: Path) -> None:
    root = tmp_path / "ocr_output" / "100001"
    one = root / "9784542030_250426"
    one.mkdir(parents=True, exist_ok=True)

    remove_if_empty_initial_artifact_dir(root, "9784542030_250426", "9784542030_250426")

    assert one.is_dir()
