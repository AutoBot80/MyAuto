"""Insurance sidecar must fail before Playwright when add-on preset is unresolved."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from unittest.mock import patch


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_addon_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fill_insurance_sidecar_fails_before_playwright_on_missing_addon() -> None:
    resolve_ctx = {
        "insurance_fill_values": {
            "insurer": "BAJAJ GENERAL INSURANCE LIMITED",
            "insurance_addon_id": None,
            "insurance_addon_label": "",
            "insurance_addon_flags": {"nd_cover": True, "rti": False, "rim_safeguard": False, "rsa": False},
            "hero_cpi": "N",
        },
        "customer_id": 10,
        "vehicle_id": 20,
        "subfolder": "9876543210_200626",
        "insurance_base_url": "http://example.com/insurance",
        "staging_payload": None,
        "staging_id": None,
        "insurance_state": 0,
        "dealer_id": 100001,
    }

    jr = _job_runner_module()
    with (
        patch.object(jr, "_api_post", return_value=resolve_ctx),
        patch.object(jr, "_require_api_credentials", return_value=("http://api", "jwt")),
    ):
        out = jr._dispatch_fill_insurance_impl(
            {
                "api_url": "http://api",
                "jwt": "jwt",
                "staging_id": "00000000-0000-0000-0000-000000000001",
            }
        )
    assert out.get("success") is False
    assert "add-on preset not resolved" in str(out.get("error") or "").lower()
