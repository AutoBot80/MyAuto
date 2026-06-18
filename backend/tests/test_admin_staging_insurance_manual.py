"""Admin Ins. Manually Filled service and endpoint."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.services.admin_staging_insurance_manual_service import (
    InsuranceManuallyFilledError,
    mark_insurance_manually_filled,
)


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_manual_fill_rejects_unknown_insurer() -> None:
    with patch(
        "app.services.admin_staging_insurance_manual_service.get_connection"
    ) as mock_conn:
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_conn.return_value.cursor.return_value.__exit__ = lambda *a: None
        with patch(
            "app.services.admin_staging_insurance_manual_service.list_portal_insurers",
            return_value=["HDFC ERGO"],
        ):
            with pytest.raises(InsuranceManuallyFilledError, match="portal insurers"):
                mark_insurance_manually_filled(
                    staging_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    dealer_id=100001,
                    insurer="Unknown Insurer",
                )
        mock_cur.assert_not_called()


def test_manual_fill_endpoint_policy_conflict_409(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ), patch(
        "app.services.admin_staging_insurance_manual_service.mark_insurance_manually_filled",
        side_effect=InsuranceManuallyFilledError(
            "A policy number is already stored; use Cancel Invoice or manual SQL to revert"
        ),
    ):
        r = admin_client.post(
            "/admin/staging/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/insurance-manually-filled",
            params={"dealer_id": 100001},
            json={"insurer": "HDFC ERGO"},
        )
    assert r.status_code == 409


def test_manual_fill_endpoint_success(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ), patch(
        "app.services.admin_staging_insurance_manual_service.mark_insurance_manually_filled",
        return_value={
            "staging_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "dealer_id": 100001,
            "insurer": "HDFC ERGO",
            "insurance_state": 2,
            "insurance_master_deleted": 0,
        },
    ):
        r = admin_client.post(
            "/admin/staging/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/insurance-manually-filled",
            params={"dealer_id": 100001},
            json={"insurer": "HDFC ERGO"},
        )
    assert r.status_code == 200
    assert r.json()["insurance_state"] == 2
