"""Admin folder zip export for Usage tab folder browsers."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.services import dealer_storage
from app.services.dealer_storage import (
    AdminFolderEmptyError,
    AdminFolderTooLargeError,
    build_admin_folder_zip_bytes,
)


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_build_admin_folder_zip_local_nested_files(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    sale = uploads / "9876543210"
    nested = sale / "docs"
    nested.mkdir(parents=True)
    (sale / "scan.pdf").write_bytes(b"pdf-bytes")
    (nested / "note.txt").write_bytes(b"hello")

    with patch.object(dealer_storage, "STORAGE_USE_S3", False), patch.object(
        dealer_storage, "get_uploads_dir", return_value=uploads
    ):
        zip_bytes, stem = build_admin_folder_zip_bytes("upload_scans", 100001, "9876543210")

    assert stem == "9876543210"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(zf.namelist())
        assert names == ["docs/note.txt", "scan.pdf"]
        assert zf.read("scan.pdf") == b"pdf-bytes"


def test_build_admin_folder_zip_local_empty_raises(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    empty = uploads / "empty_sale"
    empty.mkdir(parents=True)

    with patch.object(dealer_storage, "STORAGE_USE_S3", False), patch.object(
        dealer_storage, "get_uploads_dir", return_value=uploads
    ):
        with pytest.raises(AdminFolderEmptyError):
            build_admin_folder_zip_bytes("upload_scans", 100001, "empty_sale")


def test_build_admin_folder_zip_local_rejects_traversal(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    uploads.mkdir(parents=True)

    with patch.object(dealer_storage, "STORAGE_USE_S3", False), patch.object(
        dealer_storage, "get_uploads_dir", return_value=uploads
    ):
        with pytest.raises(ValueError, match="Invalid path"):
            build_admin_folder_zip_bytes("upload_scans", 100001, "../secret")


def test_build_admin_folder_zip_s3_happy_path() -> None:
    prefix = "uploaded-scans/1/sale_a/"
    objects = [
        {"Key": f"{prefix}scan.jpg", "Size": 100},
        {"Key": f"{prefix}nested/log.json", "Size": 50},
        {"Key": f"{prefix}", "Size": 0},
    ]

    with (
        patch.object(dealer_storage, "STORAGE_USE_S3", True),
        patch.object(dealer_storage, "S3_UPLOADS_PREFIX", "uploaded-scans"),
        patch.object(dealer_storage.s3_storage, "list_objects_with_prefix", return_value=objects),
        patch.object(dealer_storage.s3_storage, "download_bytes", side_effect=lambda key: key.encode()),
    ):
        zip_bytes, stem = build_admin_folder_zip_bytes("upload_scans", 1, "sale_a")

    assert stem == "sale_a"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert sorted(zf.namelist()) == ["nested/log.json", "scan.jpg"]


def test_build_admin_folder_zip_s3_too_many_files() -> None:
    prefix = "uploaded-scans/1/big/"
    objects = [{"Key": f"{prefix}f{i}.txt", "Size": 1} for i in range(dealer_storage.ADMIN_FOLDER_ZIP_MAX_FILES + 1)]

    with (
        patch.object(dealer_storage, "STORAGE_USE_S3", True),
        patch.object(dealer_storage, "S3_UPLOADS_PREFIX", "uploaded-scans"),
        patch.object(dealer_storage.s3_storage, "list_objects_with_prefix", return_value=objects),
    ):
        with pytest.raises(AdminFolderTooLargeError):
            build_admin_folder_zip_bytes("upload_scans", 1, "big")


def test_folder_zip_endpoint_requires_rel_path(admin_client: TestClient) -> None:
    res = admin_client.get(
        "/admin/folder-zip",
        params={"dealer_id": 100001, "root": "upload_scans", "rel_path": ""},
    )
    assert res.status_code == 400


def test_folder_zip_endpoint_rejects_traversal(admin_client: TestClient, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    uploads.mkdir(parents=True)

    with (
        patch.object(dealer_storage, "STORAGE_USE_S3", False),
        patch.object(dealer_storage, "get_uploads_dir", return_value=uploads),
    ):
        res = admin_client.get(
            "/admin/folder-zip",
            params={"dealer_id": 100001, "root": "upload_scans", "rel_path": "../escape"},
        )
    assert res.status_code == 400


def test_folder_zip_endpoint_returns_zip(admin_client: TestClient, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads" / "100001"
    sale = uploads / "mobile123"
    sale.mkdir(parents=True)
    (sale / "a.txt").write_text("x", encoding="utf-8")

    with (
        patch.object(dealer_storage, "STORAGE_USE_S3", False),
        patch.object(dealer_storage, "get_uploads_dir", return_value=uploads),
    ):
        res = admin_client.get(
            "/admin/folder-zip",
            params={"dealer_id": 100001, "root": "upload_scans", "rel_path": "mobile123"},
        )

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "mobile123.zip" in res.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert zf.read("a.txt") == b"x"
