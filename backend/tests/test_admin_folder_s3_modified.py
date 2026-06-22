"""Tests for S3 admin folder listing: per-dir LastModified and newest-first sort."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import dealer_storage, s3_storage


def test_aggregate_child_last_modified_picks_newest_per_child() -> None:
    parent = "uploaded-scans/100001/"
    older = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 16, 8, 0, 0, tzinfo=timezone.utc)
    newest = datetime(2026, 5, 16, 9, 0, 0, tzinfo=timezone.utc)

    pages = [
        {
            "Contents": [
                {"Key": f"{parent}folder_a/scan1.jpg", "LastModified": older},
                {"Key": f"{parent}folder_a/scan2.jpg", "LastModified": newest},
                {"Key": f"{parent}folder_b/log.json", "LastModified": newer},
            ]
        }
    ]
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = pages
    mock_client = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator

    with patch.object(s3_storage, "S3_DATA_BUCKET", "test-bucket"), patch.object(s3_storage, "_s3", return_value=mock_client):
        out = s3_storage.aggregate_child_last_modified(parent, ["folder_a", "folder_b"])

    assert out["folder_a"] == newest
    assert out["folder_b"] == newer


def test_list_admin_folder_s3_dirs_have_distinct_modified_and_newest_first() -> None:
    t_old = datetime(2026, 5, 10, 10, 0, 0, tzinfo=timezone.utc)
    t_mid = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)
    t_new = datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc)
    t_file = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)

    with (
        patch.object(dealer_storage.s3_storage, "list_one_level_prefix", return_value=(["older_sale", "newer_sale"], [])),
        patch.object(
            dealer_storage.s3_storage,
            "aggregate_child_last_modified",
            return_value={"older_sale": t_old, "newer_sale": t_new},
        ),
        patch.object(dealer_storage, "S3_DATA_BUCKET", "test-bucket"),
        patch.object(dealer_storage, "S3_UPLOADS_PREFIX", "uploaded-scans"),
    ):
        _display, items = dealer_storage.list_admin_folder_s3("upload_scans", 100001, "")

    assert len(items) == 2
    assert items[0]["name"] == "newer_sale"
    assert items[1]["name"] == "older_sale"
    assert items[0]["modified_at"] != items[1]["modified_at"]
    assert items[0]["modified_at"] == t_new.isoformat()


def test_list_admin_folder_s3_files_use_last_modified_and_sort_with_dirs() -> None:
    t_dir = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    t_file_old = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_file_new = datetime(2026, 5, 16, 11, 0, 0, tzinfo=timezone.utc)  # between dir and oldest file

    files = [
        {"name": "readme.txt", "Key": "uploaded-scans/1/readme.txt", "Size": 10, "LastModified": t_file_old},
        {"name": "latest.pdf", "Key": "uploaded-scans/1/latest.pdf", "Size": 20, "LastModified": t_file_new},
    ]

    with (
        patch.object(dealer_storage.s3_storage, "list_one_level_prefix", return_value=(["sale_a"], files)),
        patch.object(dealer_storage.s3_storage, "aggregate_child_last_modified", return_value={"sale_a": t_dir}),
        patch.object(dealer_storage, "S3_DATA_BUCKET", "test-bucket"),
        patch.object(dealer_storage, "S3_UPLOADS_PREFIX", "uploaded-scans"),
    ):
        _display, items = dealer_storage.list_admin_folder_s3("upload_scans", 1, "")

    names = [x["name"] for x in items]
    assert names == ["sale_a", "latest.pdf", "readme.txt"]


def test_list_admin_folder_s3_challans_root_uses_dealer_prefix() -> None:
    with (
        patch.object(dealer_storage.s3_storage, "list_one_level_prefix", return_value=(["batch_1"], [])) as mock_list,
        patch.object(dealer_storage.s3_storage, "aggregate_child_last_modified", return_value={}) as mock_agg,
        patch.object(dealer_storage, "S3_DATA_BUCKET", "test-bucket"),
        patch.object(dealer_storage, "S3_CHALLANS_PREFIX", "challans"),
    ):
        dealer_storage.list_admin_folder_s3("challans", 100001, "")

    mock_list.assert_called_once_with("challans/100001/")
    mock_agg.assert_called_once_with("challans/100001/", ["batch_1"])
