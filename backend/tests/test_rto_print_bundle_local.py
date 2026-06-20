from pathlib import Path

import fitz

from app.services.fill_rto_service import (
    build_local_rto_print_jobs,
    resolve_cpa_certificate_pdf,
)


def _minimal_pdf(path: Path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_resolve_cpa_canonical_name(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    _minimal_pdf(sale / "9876543210_CPA_160526.pdf")
    p = resolve_cpa_certificate_pdf(sale, subfolder=sale.name, mobile="9876543210")
    assert p is not None
    assert p.name == "9876543210_CPA_160526.pdf"


def test_build_local_rto_print_jobs_merged_bundle_with_cpa(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    _minimal_pdf(sale / "Form_21_Sale_Certificate.pdf", pages=2)
    _minimal_pdf(sale / "9876543210_Insurance_160526.pdf", pages=1)
    _minimal_pdf(sale / "9876543210_CPA_160526.pdf", pages=1)
    gate = sale / "Gate Pass.pdf"
    _minimal_pdf(gate, pages=1)
    jobs, missing = build_local_rto_print_jobs(
        sale,
        subfolder=sale.name,
        mobile="9876543210",
        gate_pass_pdf=gate,
    )
    assert not missing
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "rto_print_bundle"
    merged = Path(jobs[0]["presigned_url"])
    assert merged.is_file()
    assert merged.name.startswith("saathi-rto-print-")
    doc = fitz.open(str(merged))
    try:
        # Gate Pass.pdf required for job build but excluded from print bundle (4 = sale+ins+cpa).
        assert doc.page_count == 4
    finally:
        doc.close()
        merged.unlink(missing_ok=True)


def test_build_local_rto_print_jobs_merged_bundle_without_cpa(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    _minimal_pdf(sale / "Form_21_Sale_Certificate.pdf", pages=1)
    _minimal_pdf(sale / "9876543210_Insurance_160526.pdf", pages=1)
    gate = sale / "Gate Pass.pdf"
    _minimal_pdf(gate, pages=1)
    jobs, missing = build_local_rto_print_jobs(
        sale,
        subfolder=sale.name,
        mobile="9876543210",
        gate_pass_pdf=gate,
    )
    assert not missing
    assert len(jobs) == 1
    merged = Path(jobs[0]["presigned_url"])
    doc = fitz.open(str(merged))
    try:
        # Gate Pass.pdf required for job build but excluded from print bundle (2 = sale+ins).
        assert doc.page_count == 2
    finally:
        doc.close()
        merged.unlink(missing_ok=True)
