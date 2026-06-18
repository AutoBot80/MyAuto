"""Admin staging search endpoint."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_search_denied_out_of_scope(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ):
        r = admin_client.get(
            "/admin/staging/search",
            params={"dealer_id": 100003, "mobile": "9876543210"},
        )
    assert r.status_code == 403


def test_search_requires_ten_digit_mobile(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ):
        r = admin_client.get(
            "/admin/staging/search",
            params={"dealer_id": 100001, "mobile": "123"},
        )
    assert r.status_code == 422


def test_search_returns_scoped_rows(admin_client: TestClient) -> None:
    sample = [
        {
            "staging_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "dealer_id": 100001,
            "updated_at": "2026-06-18T10:00:00+05:30",
            "status": "committed",
            "customer_name": "Test User",
            "mobile": "9876543210",
            "chassis": "CH1",
            "engine": "EN1",
            "order_number": "ORD1",
            "dms_state": 2,
            "insurance_state": 0,
            "has_rto_queue": True,
        }
    ]
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ), patch(
        "app.repositories.add_sales_staging.list_staging_rows_for_admin",
        return_value=sample,
    ) as mock_list:
        r = admin_client.get(
            "/admin/staging/search",
            params={"dealer_id": 100001, "mobile": "+91 98765 43210"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["dealer_id"] == 100001
    assert body["mobile"] == "9876543210"
    assert len(body["rows"]) == 1
    assert body["rows"][0]["has_rto_queue"] is True
    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["mobile_digits"].endswith("9876543210")
