"""Sidecar insurance resolve must reject missing add-on preset catalog."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_sidecar_insurance_resolve_rejects_missing_addon_presets(
    client: TestClient,
) -> None:
    fill_values = {
        "insurer": "BAJAJ GENERAL INSURANCE LIMITED",
        "insurance_addon_id": None,
        "insurance_addon_label": "",
        "insurance_addon_flags": {
            "nd_cover": True,
            "rti": False,
            "rim_safeguard": False,
            "rsa": False,
        },
        "hero_cpi": "N",
    }
    with (
        patch(
            "app.services.insurance_form_values.build_insurance_fill_values",
            return_value=fill_values,
        ),
        patch(
            "app.routers.sidecar_proxy.validate_dealer_insurance_addon_config",
            return_value=[
                "no active insurance add-on presets for prefer_insurer "
                "'BAJAJ GENERAL INSURANCE LIMITED'"
            ],
        ),
        patch("app.routers.sidecar_proxy.get_connection") as mock_conn,
        patch("app.routers.sidecar_proxy.get_ocr_output_dir", return_value=Path("/tmp/ocr")),
    ):
        mock_conn.return_value = MagicMock()
        r = client.post(
            "/sidecar/insurance/resolve",
            json={
                "customer_id": 1,
                "vehicle_id": 2,
                "subfolder": "9999999999_010126",
                "dealer_id": 100001,
            },
        )
    assert r.status_code == 400
    assert "Insurance add-on config invalid" in r.json()["detail"]


def test_sidecar_insurance_resolve_includes_addon_fields_when_valid(
    client: TestClient,
) -> None:
    fill_values = {
        "insurer": "BAJAJ GENERAL INSURANCE LIMITED",
        "insurance_addon_id": 1,
        "insurance_addon_label": "ND Cover, Rim Safeguard, RSA",
        "insurance_addon_flags": {
            "nd_cover": True,
            "rti": False,
            "rim_safeguard": True,
            "rsa": True,
        },
        "hero_cpi": "N",
    }
    with (
        patch(
            "app.services.insurance_form_values.build_insurance_fill_values",
            return_value=fill_values,
        ),
        patch(
            "app.routers.sidecar_proxy.validate_dealer_insurance_addon_config",
            return_value=[],
        ),
        patch("app.routers.sidecar_proxy.get_connection") as mock_conn,
        patch("app.routers.sidecar_proxy.get_ocr_output_dir", return_value=Path("/tmp/ocr")),
        patch("app.routers.sidecar_proxy.INSURANCE_BASE_URL", "http://insurance.example"),
    ):
        mock_conn.return_value = MagicMock()
        r = client.post(
            "/sidecar/insurance/resolve",
            json={
                "customer_id": 1,
                "vehicle_id": 2,
                "subfolder": "9999999999_010126",
                "dealer_id": 100001,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["insurance_fill_values"]["insurance_addon_label"] == "ND Cover, Rim Safeguard, RSA"
    assert body["insurance_fill_values"]["insurance_addon_id"] == 1
