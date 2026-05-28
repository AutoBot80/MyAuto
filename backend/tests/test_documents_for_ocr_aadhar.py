"""Documents API: serve Aadhaar JPEGs under for_OCR before post-OCR promotion."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    uploads = tmp_path / "uploads" / "100001"
    sale = uploads / "9057397169_210526"
    for_ocr = sale / "for_OCR"
    for_ocr.mkdir(parents=True)
    (for_ocr / "Aadhar_front.jpg").write_bytes(b"front-jpeg")
    (for_ocr / "Aadhar_back.jpg").write_bytes(b"back-jpeg")

    def _uploads_dir(dealer_id: int) -> Path:
        return uploads

    from app.main import app
    from app.routers import documents as documents_router
    monkeypatch.setattr(documents_router, "get_uploads_dir", _uploads_dir)

    return TestClient(app)


def test_get_document_for_ocr_serves_aadhar_jpeg(client: TestClient) -> None:
    r = client.get(
        "/documents/9057397169_210526/for_ocr/Aadhar_front.jpg",
        params={"dealer_id": 100001},
    )
    assert r.status_code == 200
    assert r.content == b"front-jpeg"


def test_get_document_for_ocr_rejects_non_aadhar_filename(client: TestClient) -> None:
    r = client.get(
        "/documents/9057397169_210526/for_ocr/Sales_Detail_Sheet.pdf",
        params={"dealer_id": 100001},
    )
    assert r.status_code == 404


def test_get_document_for_ocr_missing_file(client: TestClient) -> None:
    r = client.get(
        "/documents/9057397169_210526/for_ocr/Aadhar.jpg",
        params={"dealer_id": 100001},
    )
    assert r.status_code == 404
