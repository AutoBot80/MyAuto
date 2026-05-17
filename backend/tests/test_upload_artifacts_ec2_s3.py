"""upload-artifacts: EC2 write always; S3 sync status surfaced."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_upload_artifacts_ec2_ok_s3_fail_reported(client: TestClient, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    uploads.mkdir(parents=True)

    with (
        patch("app.routers.sidecar_proxy.get_uploads_dir", return_value=uploads),
        patch("app.routers.sidecar_proxy.sync_uploads_file_to_s3", return_value=(False, "AccessDenied")),
    ):
        r = client.post(
            "/sidecar/upload-artifacts",
            data={"dealer_id": "100001", "tree": "uploads", "rel_path": "mob_010126/a.pdf"},
            files={"file": ("a.pdf", b"%PDF-1.4", "application/octet-stream")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["ec2_written"] is True
    assert body["s3_synced"] is False
    assert "AccessDenied" in (body.get("s3_error") or "")
    assert (uploads / "mob_010126" / "a.pdf").is_file()
