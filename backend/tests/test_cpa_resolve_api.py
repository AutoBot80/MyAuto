"""Sidecar CPA resolve: DB-backed prepare on API host for Electron sidecar."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_cpa_resolve_returns_alliance_kwargs(client: TestClient) -> None:
    alliance = {"customer_name": "A B", "mobile": "9988776655", "frame_no": "CH1"}
    full = {"customer_id": 10, "vehicle_id": 20, "customer_name": "A B", "mobile_number": "9988776655"}
    with patch(
        "app.services.cpa_form_values.prepare_cpa_alliance_fill",
        return_value=(alliance, full, "sale-folder-1"),
    ):
        r = client.post(
            "/sidecar/cpa/resolve",
            json={
                "dealer_id": 100001,
                "staging_id": "15e4b58b-54c2-44a0-ae94-6a80cf6a0085",
                "subfolder": "sale-folder-1",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["alliance_kwargs"]["mobile"] == "9988776655"
    assert body["full_values"]["customer_id"] == 10
    assert body["subfolder"] == "sale-folder-1"
    assert body["customer_id"] == 10
    assert body["vehicle_id"] == 20
