"""Tests for in-process mobile/alt PATCH and sale folder rename helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.schemas.add_sales_staging_patch import PatchAddSalesStagingCustomer, PatchAddSalesStagingPayloadRequest
from app.services.add_sales_staging_patch_service import (
    _build_patch_from_request,
    patch_add_sales_staging_payload,
)
from app.services.sale_folder_rename_service import compute_new_subfolder_leaf


def test_compute_new_subfolder_leaf_preserves_date_suffix() -> None:
    assert compute_new_subfolder_leaf("8278671032_160526", "9057397169") == "9057397169_160526"


def test_build_patch_includes_mobile_and_alt() -> None:
    req = PatchAddSalesStagingPayloadRequest(
        customer=PatchAddSalesStagingCustomer(
            mobile_number=9876543210,
            alt_phone_num="9123456789",
        ),
    )
    patch = _build_patch_from_request(req)
    assert patch["customer"]["mobile_number"] == 9876543210
    assert patch["customer"]["alt_phone_num"] == "9123456789"


def test_patch_rejects_mobile_when_dms_state_ge_2() -> None:
    cur = MagicMock()
    cur.fetchone.side_effect = [
        {"dms_state": 2},
    ]
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    req = PatchAddSalesStagingPayloadRequest(
        customer=PatchAddSalesStagingCustomer(mobile_number=9876543210),
    )
    with patch("app.services.add_sales_staging_patch_service.get_connection", return_value=conn):
        with patch(
            "app.services.add_sales_staging_patch_service._load_payload_json_for_update_on_cursor",
            return_value={
                "customer": {"mobile_number": 1111111111},
                "file_location": "1111111111_160526",
            },
        ):
            with pytest.raises(ValueError, match="customer has been saved in DMS"):
                patch_add_sales_staging_payload(
                    staging_id="00000000-0000-0000-0000-000000000001",
                    dealer_id=1,
                    req=req,
                )


def test_patch_alt_only_does_not_rename_folder() -> None:
    cur = MagicMock()
    cur.fetchone.side_effect = [
        {"dms_state": 0},
        {"updated_at": None},
    ]
    cur.rowcount = 1
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    req = PatchAddSalesStagingPayloadRequest(
        customer=PatchAddSalesStagingCustomer(alt_phone_num="9123456789"),
    )
    with patch("app.services.add_sales_staging_patch_service.get_connection", return_value=conn):
        with patch(
            "app.services.add_sales_staging_patch_service._load_payload_json_for_update_on_cursor",
            return_value={
                "customer": {"mobile_number": 9876543210, "alt_phone_num": ""},
                "file_location": "9876543210_160526",
            },
        ):
            with patch(
                "app.services.add_sales_staging_patch_service.merge_staging_payload_on_cursor",
                return_value=1,
            ) as merge:
                with patch(
                    "app.services.add_sales_staging_patch_service.rename_sale_folders_for_mobile_change"
                ) as rename:
                    with patch(
                        "app.services.add_sales_staging_patch_service.patch_ocr_to_be_used_json"
                    ):
                        result = patch_add_sales_staging_payload(
                            staging_id="00000000-0000-0000-0000-000000000001",
                            dealer_id=1,
                            req=req,
                        )
                        rename.assert_not_called()
                        merge.assert_called_once()
                        assert result["ok"] is True
                        assert result.get("file_location") is None
