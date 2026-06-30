"""Insurance commit scrape merge and staging refetch contract."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.services.add_sales_commit_service import merge_insurance_scrape_for_commit


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_merge_insurance_scrape_grid_premium_wins() -> None:
    merged = merge_insurance_scrape_for_commit(
        {"policy_num": "P1", "premium": 6000.0, "idv": 80000.0},
        {"policy_num": "P2", "premium": 5550.0, "idv": 73800.0},
    )
    assert merged["policy_num"] == "P1"
    assert merged["premium"] == 6000.0
    assert merged["idv"] == 80000.0


def test_merge_insurance_scrape_falls_back_to_proposal_preview() -> None:
    merged = merge_insurance_scrape_for_commit(
        {"policy_num": "BAGIC123"},
        {
            "policy_num": "P388",
            "premium": 5550.0,
            "idv": 73800.0,
            "policy_from": "29/06/2026",
        },
    )
    assert merged["policy_num"] == "BAGIC123"
    assert merged["premium"] == 5550.0
    assert merged["idv"] == 73800.0
    assert merged["policy_from"] == "29/06/2026"


def test_merge_insurance_scrape_empty_grid_uses_proposal() -> None:
    merged = merge_insurance_scrape_for_commit(
        None,
        {"premium": 5550.0, "policy_num": "X"},
    )
    assert merged["premium"] == 5550.0
    assert merged["policy_num"] == "X"


def test_insurance_commit_refetches_staging_and_merges_preview(client: TestClient) -> None:
    fresh_payload = {
        "customer_id": 70,
        "vehicle_id": 94,
        "insurance": {"premium": 5550.0, "idv": 73800.0, "policy_num": "BAGIC123"},
    }

    with (
        patch("app.repositories.add_sales_staging.fetch_staging_payload", return_value=fresh_payload),
        patch(
            "app.services.add_sales_commit_service.insert_insurance_master_after_gi",
        ) as mock_insert,
        patch("app.services.add_sales_staging_state_service.mark_staging_insurance_state") as mock_state,
    ):
        r = client.post(
            "/sidecar/insurance/commit",
            json={
                "customer_id": 70,
                "vehicle_id": 94,
                "fill_values": {"insurer": "BAJAJ"},
                "staging_payload": {"insurance": {"premium": None}},
                "preview_scrape": {"policy_num": "BAGIC123"},
                "proposal_preview_scrape": {"premium": 5550.0, "idv": 73800.0},
                "staging_id": "2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0",
                "dealer_id": 100001,
                "subfolder": "7878793294_290626",
            },
        )

    assert r.status_code == 200
    body = r.json()
    assert body.get("error") is None
    assert body.get("insurance_state_set") is True
    mock_insert.assert_called_once()
    _args, kwargs = mock_insert.call_args
    assert kwargs["staging_payload"] == fresh_payload
    assert kwargs["preview_scrape"]["premium"] == 5550.0
    assert kwargs["preview_scrape"]["policy_num"] == "BAGIC123"
    mock_state.assert_called_once_with(
        "2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0", 100001, 3
    )


def test_staging_insurance_patch_endpoint(client: TestClient) -> None:
    with patch(
        "app.services.add_sales_staging_state_service.persist_staging_insurance_main_fields",
        return_value=True,
    ) as mock_persist:
        r = client.post(
            "/sidecar/staging/insurance-patch",
            json={
                "staging_id": "2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0",
                "dealer_id": 100001,
                "premium": 5550.0,
                "idv": 73800.0,
            },
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None}
    mock_persist.assert_called_once_with(
        "2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0",
        100001,
        policy_num=None,
        policy_from=None,
        policy_to=None,
        premium=5550.0,
        idv=73800.0,
    )
