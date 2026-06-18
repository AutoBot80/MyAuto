"""Admin Cancel Invoice service and endpoint."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.admin_staging_cancel_invoice_service import (
    CancelStagingInvoiceError,
    cancel_staging_invoice,
)


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_cancel_requires_matching_confirmation() -> None:
    with patch("app.services.admin_staging_cancel_invoice_service.get_connection") as mock_conn:
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        with pytest.raises(CancelStagingInvoiceError, match="Confirmation must match"):
            cancel_staging_invoice(
                staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                dealer_id=100001,
                confirmation="wrong",
                expected_confirmation="Ravi Kumar",
            )


def test_cancel_endpoint_denied_out_of_scope(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ):
        r = admin_client.post(
            "/admin/staging/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/cancel-invoice",
            params={"dealer_id": 100003},
            json={"confirmation": "Test"},
        )
    assert r.status_code == 403


def test_cancel_endpoint_success(admin_client: TestClient) -> None:
    detail = {
        "staging_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "dealer_id": 100001,
        "payload_json": {"customer": {"name": "Ravi Kumar", "mobile_number": "9876543210"}},
    }
    summary = {
        "staging_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "dealer_id": 100001,
        "sales_id": 42,
        "masters_deleted": {"sales_master": 1},
        "staging_reset": True,
    }
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ), patch(
        "app.repositories.add_sales_staging.fetch_staging_admin_detail",
        return_value=detail,
    ), patch(
        "app.services.admin_staging_cancel_invoice_service.cancel_staging_invoice",
        return_value=summary,
    ) as mock_cancel:
        r = admin_client.post(
            "/admin/staging/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/cancel-invoice",
            params={"dealer_id": 100001},
            json={"confirmation": "Ravi Kumar"},
        )
    assert r.status_code == 200
    assert r.json()["staging_reset"] is True
    mock_cancel.assert_called_once()
    assert mock_cancel.call_args.kwargs["expected_confirmation"] == "Ravi Kumar"
