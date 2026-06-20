"""Sidecar dealer_sign_overlay job dispatches apply_dealer_signatures_to_sale_folder."""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path

import fitz
from PIL import Image


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_overlay_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _minimal_pdf(path: Path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_dispatch_dealer_sign_overlay_stamps_sale_certificate(tmp_path: Path, monkeypatch) -> None:
    saathi = tmp_path / "saathi"
    saathi.mkdir()
    dealer_id = 100001
    subfolder = "9876543210_200626"

    monkeypatch.setenv("SAATHI_BASE_DIR", str(saathi))
    os.environ["SAATHI_BASE_DIR"] = str(saathi)
    import app.config

    importlib.reload(app.config)

    sale = app.config.get_uploads_dir(dealer_id) / subfolder
    sale.mkdir(parents=True)
    _minimal_pdf(sale / "Form_21_Sale_Certificate.pdf", pages=1)

    sig = saathi / f"{dealer_id}_sign.jpg"
    Image.new("RGB", (80, 40), color=(0, 0, 0)).save(sig, "JPEG")

    jr = _job_runner_module()
    out = jr._dispatch_dealer_sign_overlay_impl(
        {"dealer_id": dealer_id, "subfolder": subfolder},
    )

    assert out["success"] is True
    assert "Form_21_Sale_Certificate.pdf" in (out.get("stamped") or [])
