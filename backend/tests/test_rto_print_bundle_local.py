from pathlib import Path

from app.services.fill_rto_service import (
    build_local_rto_print_jobs,
    resolve_cpa_certificate_pdf,
)


def test_resolve_cpa_canonical_name(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    (sale / "9876543210_CPA_160526.pdf").write_bytes(b"%PDF-1.4")
    p = resolve_cpa_certificate_pdf(sale, subfolder=sale.name, mobile="9876543210")
    assert p is not None
    assert p.name == "9876543210_CPA_160526.pdf"


def test_build_local_rto_print_jobs_includes_optional_cpa(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    (sale / "Form_21_Sale_Certificate.pdf").write_bytes(b"%PDF")
    (sale / "9876543210_Insurance_160526.pdf").write_bytes(b"%PDF")
    (sale / "9876543210_CPA_160526.pdf").write_bytes(b"%PDF")
    gate = sale / "Gate Pass.pdf"
    gate.write_bytes(b"%PDF")
    jobs, missing = build_local_rto_print_jobs(
        sale,
        subfolder=sale.name,
        mobile="9876543210",
        gate_pass_pdf=gate,
    )
    assert not missing
    kinds = [j["kind"] for j in jobs]
    assert kinds == ["sale_certificate", "insurance", "cpa", "gate_pass"]
