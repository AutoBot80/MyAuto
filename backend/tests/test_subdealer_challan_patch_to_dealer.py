"""Unit tests for in-process To Dealer correction on challan staging."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.add_subdealer_challan_service import patch_staging_master_to_dealer

_BATCH = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _master(**overrides: object) -> dict:
    base = {
        "from_dealer_id": 100000,
        "to_dealer_id": 100001,
        "invoice_complete": False,
        "dms_order_number": None,
    }
    base.update(overrides)
    return base


@patch("app.services.add_subdealer_challan_service.update_dealer_id_for_inventory_line_ids")
@patch("app.services.add_subdealer_challan_service.DealerRefRepository.get_by_id")
@patch("app.services.add_subdealer_challan_service.master_repo.update_master_to_dealer")
@patch("app.services.add_subdealer_challan_service.detail_repo.fetch_batch_rows")
@patch("app.services.add_subdealer_challan_service.master_repo.fetch_master")
@patch("app.services.add_subdealer_challan_service.get_connection")
def test_patch_to_dealer_success_updates_inventory(
    mock_conn: MagicMock,
    mock_fetch_master: MagicMock,
    mock_fetch_rows: MagicMock,
    mock_update_master: MagicMock,
    mock_get_dealer: MagicMock,
    mock_update_inv: MagicMock,
) -> None:
    mock_fetch_master.return_value = _master()
    mock_fetch_rows.return_value = [{"inventory_line_id": 42}, {"inventory_line_id": None}]
    mock_update_master.return_value = True
    mock_get_dealer.return_value = {"dealer_id": 100002, "parent_id": 100000}
    mock_conn.return_value = MagicMock()

    out = patch_staging_master_to_dealer(
        challan_batch_id=_BATCH,
        dealer_id=100000,
        to_dealer_id=100002,
    )

    assert out == {"ok": True, "error": None, "to_dealer_id": 100002}
    mock_update_master.assert_called_once_with(_BATCH, 100002)
    mock_update_inv.assert_called_once_with([42], 100002)


@patch("app.services.add_subdealer_challan_service.master_repo.fetch_master")
def test_patch_to_dealer_rejects_dms_order_number(mock_fetch_master: MagicMock) -> None:
    mock_fetch_master.return_value = _master(dms_order_number="ORD-123")

    out = patch_staging_master_to_dealer(
        challan_batch_id=_BATCH,
        dealer_id=100000,
        to_dealer_id=100002,
    )

    assert out["ok"] is False
    assert "DMS Order#" in str(out.get("error") or "")


@patch("app.services.add_subdealer_challan_service.DealerRefRepository.get_by_id")
@patch("app.services.add_subdealer_challan_service.get_connection")
@patch("app.services.add_subdealer_challan_service.master_repo.fetch_master")
def test_patch_to_dealer_rejects_invalid_child(
    mock_fetch_master: MagicMock,
    mock_conn: MagicMock,
    mock_get_dealer: MagicMock,
) -> None:
    mock_fetch_master.return_value = _master()
    mock_conn.return_value = MagicMock()
    mock_get_dealer.return_value = {"dealer_id": 100002, "parent_id": 999999}

    out = patch_staging_master_to_dealer(
        challan_batch_id=_BATCH,
        dealer_id=100000,
        to_dealer_id=100002,
    )

    assert out["ok"] is False
    assert "subdealer" in str(out.get("error") or "").lower()


@patch("app.services.add_subdealer_challan_service.master_repo.fetch_master")
def test_patch_to_dealer_noop_same_id(mock_fetch_master: MagicMock) -> None:
    mock_fetch_master.return_value = _master(to_dealer_id=100001)

    out = patch_staging_master_to_dealer(
        challan_batch_id=_BATCH,
        dealer_id=100000,
        to_dealer_id=100001,
    )

    assert out == {"ok": True, "error": None, "to_dealer_id": 100001}
