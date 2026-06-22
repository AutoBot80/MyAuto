"""CPA sidecar must use resolve dealer_id for local paths, not env DEALER_ID."""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_cpa_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fill_cpa_sidecar_uses_resolve_dealer_id_not_env_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    saathi = tmp_path / "saathi"
    saathi.mkdir()
    monkeypatch.setenv("SAATHI_BASE_DIR", str(saathi))
    os.environ["SAATHI_BASE_DIR"] = str(saathi)
    monkeypatch.setenv("DEALER_ID", "100001")

    import app.config

    importlib.reload(app.config)

    captured: dict = {}
    resolve_ctx = {
        "alliance_kwargs": {"cpa_insurer": "Test"},
        "full_values": {"customer_id": 1, "vehicle_id": 2, "mobile_number": "9876543210"},
        "subfolder": "9876543210_200626",
        "customer_id": 1,
        "vehicle_id": 2,
        "dealer_id": 100003,
    }

    def fake_add_alliance_cpa_insurance(*, dealer_id, subfolder, portal_url, **kwargs):
        captured["playwright_dealer_id"] = dealer_id
        captured["subfolder"] = subfolder
        return {"success": False, "error": "stop for test"}

    jr = _job_runner_module()
    with (
        patch.object(jr, "_api_post", return_value=resolve_ctx),
        patch.object(jr, "_require_api_credentials", return_value=("http://api", "jwt")),
        patch.object(jr, "_record_process_failure_via_api"),
        patch(
            "app.services.add_alliance_cpa_insurance.add_alliance_cpa_insurance",
            side_effect=fake_add_alliance_cpa_insurance,
        ),
        patch("app.services.cpa_form_values.write_cpa_form_values_snapshot"),
    ):
        jr._dispatch_fill_cpa_alliance_insurance_impl(
            {
                "api_url": "http://api",
                "jwt": "jwt",
                "portal_url": "http://cpa.example",
                "staging_id": "00000000-0000-0000-0000-000000000001",
            }
        )

    assert captured.get("playwright_dealer_id") == 100003
