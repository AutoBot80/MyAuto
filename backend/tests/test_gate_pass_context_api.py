"""Sidecar gate-pass-context: vehicle_master + dealer OEM for local Gate Pass."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_gate_pass_context_merges_vehicle_and_dealer(client: TestClient) -> None:
    with (
        patch(
            "app.repositories.add_sales_staging.fetch_staging_payload",
            return_value={
                "customer": {"name": "Test User"},
                "vehicle": {"model": "DRUM", "colour": "Black"},
            },
        ),
        patch(
            "app.services.form20_service._get_vehicle_from_db",
            return_value={
                "model": "DRUM BS6",
                "colour": "Pearl Igneous Black",
                "oem_name": "Hero MotoCorp",
                "key_num": "K123",
                "chassis": "CHASSIS1",
            },
        ),
        patch(
            "app.services.form20_service._get_dealer_from_db",
            return_value={"oem_name": "Hero MotoCorp Ltd", "dealer_name": "Test Dealer"},
        ),
    ):
        r = client.get(
            "/sidecar/gate-pass-context",
            params={
                "dealer_id": 100001,
                "vehicle_id": 5,
                "staging_id": "15e4b58b-54c2-44a0-ae94-6a80cf6a0085",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["vehicle"]["model"] == "DRUM BS6"
    assert body["vehicle"]["colour"] == "Pearl Igneous Black"
    assert body["vehicle"]["oem_name"] == "Hero MotoCorp"
    assert body["dealer"]["oem_name"] == "Hero MotoCorp Ltd"
    assert body["customer"]["name"] == "Test User"
