"""push-sale-bundle: single ZIP POST into uploads tree."""

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def _zip_bytes(subfolder: str, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(f"{subfolder}/{name}", data)
    return buf.getvalue()


def test_push_sale_bundle_writes_files(client: TestClient, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    uploads.mkdir(parents=True)
    sub = "9057397169_210526"
    body = _zip_bytes(sub, {"a.pdf": b"%PDF-1.4", "b.pdf": b"%PDF-1.4 b"})

    with (
        patch("app.routers.sidecar_proxy.get_uploads_dir", return_value=uploads),
        patch("app.services.dealer_storage.sync_uploads_file_to_s3", return_value=(True, None)),
    ):
        r = client.post(
            "/sidecar/push-sale-bundle",
            params={"dealer_id": 100001, "subfolder": sub},
            content=body,
            headers={"Content-Type": "application/zip"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["files_written"] == 2
    assert (uploads / sub / "a.pdf").is_file()
    assert (uploads / sub / "b.pdf").is_file()


def test_push_sale_bundle_rejects_empty(client: TestClient) -> None:
    r = client.post(
        "/sidecar/push-sale-bundle",
        params={"dealer_id": 100001, "subfolder": "mob_010126"},
        content=b"",
        headers={"Content-Type": "application/zip"},
    )
    assert r.status_code == 400
