"""Insurance sidecar must use resolve dealer_id for local paths, not env DEALER_ID."""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_insurance_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fill_insurance_sidecar_uses_resolve_dealer_id_not_env_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When resolve returns dealer_id=100003, local ocr/uploads paths must not use DEALER_ID=100001."""
    saathi = tmp_path / "saathi"
    saathi.mkdir()
    monkeypatch.setenv("SAATHI_BASE_DIR", str(saathi))
    os.environ["SAATHI_BASE_DIR"] = str(saathi)
    monkeypatch.setenv("DEALER_ID", "100001")

    import app.config

    importlib.reload(app.config)

    subfolder = "9876543210_200626"
    captured: dict = {}

    resolve_ctx = {
        "insurance_fill_values": {"insurer": "Test"},
        "customer_id": 10,
        "vehicle_id": 20,
        "subfolder": subfolder,
        "insurance_base_url": "http://example.com/insurance",
        "staging_payload": None,
        "staging_id": None,
        "insurance_state": 0,
        "dealer_id": 100003,
    }

    def fake_api_post(_url, _jwt, path, _body=None):
        if path == "/sidecar/insurance/resolve":
            return resolve_ctx
        return {}

    def fake_pre_process(**kwargs):
        captured["pre_dealer_id"] = kwargs.get("dealer_id")
        captured["ocr_output_dir"] = kwargs.get("ocr_output_dir")
        return {"success": False, "error": "stop early for test"}

    def fake_main_process(**_kwargs):
        return {"success": False}

    def fake_post_process(**_kwargs):
        return {"success": False, "error": "stop early for test"}

    jr = _job_runner_module()
    with (
        patch.object(jr, "_api_post", side_effect=fake_api_post),
        patch.object(jr, "_require_api_credentials", return_value=("http://api", "jwt")),
        patch.object(jr, "_record_process_failure_via_api"),
        patch(
            "app.services.fill_hero_insurance_service.pre_process",
            side_effect=fake_pre_process,
        ),
        patch(
            "app.services.fill_hero_insurance_service.main_process",
            side_effect=fake_main_process,
        ),
        patch(
            "app.services.fill_hero_insurance_service.post_process",
            side_effect=fake_post_process,
        ),
    ):
        jr._dispatch_fill_insurance_impl(
            {
                "api_url": "http://api",
                "jwt": "jwt",
                "staging_id": "00000000-0000-0000-0000-000000000001",
            }
        )

    assert captured.get("pre_dealer_id") == 100003
    ocr_dir = captured.get("ocr_output_dir")
    assert ocr_dir is not None
    assert "100003" in str(ocr_dir)
    assert "100001" not in str(ocr_dir)
