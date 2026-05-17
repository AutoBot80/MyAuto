"""Print/Queue RTO push: only RTO bundle PDFs are uploaded."""

from __future__ import annotations

from pathlib import Path

from app.services.fill_rto_service import resolve_print_rto_push_upload_paths


def test_resolve_print_rto_push_skips_scans_and_gate_pass(tmp_path: Path) -> None:
    sale = tmp_path / "8278671032_160526"
    sale.mkdir()
    for name in (
        "8278671032_Form_20.pdf",
        "8278671032_Form_20_1.pdf",
        "8278671032_Form22.pdf",
        "8278671032_Sale_Certificate.pdf",
        "8278671032_Insurance_16052026.pdf",
        "8278671032_GST_Retail_Invoice.pdf",
        "Aadhar_front.jpg",
        "Gate Pass.pdf",
        "pencil_mark.jpeg",
        "Sales_Detail_Sheet.pdf",
    ):
        (sale / name).write_bytes(b"%PDF-1.4\n")

    paths = resolve_print_rto_push_upload_paths(sale, subfolder=sale.name, mobile="8278671032")
    names = {p.name for p in paths}
    assert names == {
        "8278671032_Form_20.pdf",
        "8278671032_Form22.pdf",
        "8278671032_Sale_Certificate.pdf",
        "8278671032_GST_Retail_Invoice.pdf",
        "8278671032_Insurance_16052026.pdf",
    }


def test_resolve_print_rto_push_includes_optional_cpa(tmp_path: Path) -> None:
    sale = tmp_path / "8278671032_160526"
    sale.mkdir()
    (sale / "8278671032_Form_20.pdf").write_bytes(b"x")
    (sale / "8278671032_Form22.pdf").write_bytes(b"x")
    (sale / "8278671032_Sale_Certificate.pdf").write_bytes(b"x")
    (sale / "8278671032_Insurance_16052026.pdf").write_bytes(b"x")
    (sale / "8278671032_CPA_16052026.pdf").write_bytes(b"x")

    paths = resolve_print_rto_push_upload_paths(sale, subfolder=sale.name, mobile="8278671032")
    assert any(p.name.endswith("_CPA_16052026.pdf") for p in paths)
