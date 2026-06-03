from pathlib import Path

import fitz

from app.services.fill_rto_service import build_view_customer_sale_files_print_jobs


def _minimal_pdf(path: Path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_build_view_customer_sale_files_print_jobs_merged(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    _minimal_pdf(sale / "9876543210_Form_20.pdf", pages=2)
    _minimal_pdf(sale / "9876543210_Form22.pdf", pages=1)
    _minimal_pdf(sale / "9876543210_GST_Retail_Invoice.pdf", pages=3)
    jobs, missing = build_view_customer_sale_files_print_jobs(
        sale,
        subfolder=sale.name,
        mobile="9876543210",
    )
    assert not missing
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "rto_print_bundle"
    merged = Path(jobs[0]["presigned_url"])
    assert merged.is_file()
    doc = fitz.open(str(merged))
    try:
        assert doc.page_count == 6
    finally:
        doc.close()
        merged.unlink(missing_ok=True)


def test_build_view_customer_sale_files_print_jobs_missing_form22(tmp_path: Path) -> None:
    sale = tmp_path / "9876543210_160526"
    sale.mkdir()
    _minimal_pdf(sale / "Form_20.pdf", pages=1)
    _minimal_pdf(sale / "GST_Retail_Invoice.pdf", pages=1)
    jobs, missing = build_view_customer_sale_files_print_jobs(
        sale,
        subfolder=sale.name,
        mobile="9876543210",
    )
    assert not jobs
    assert any("Form 22" in m for m in missing)
