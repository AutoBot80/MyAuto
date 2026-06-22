"""Vahan batch sidecar must use claim-batch dealer_id for local paths."""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_vahan_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fill_vahan_batch_sidecar_uses_claim_dealer_id_not_env_default(
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
    claim_resp = {
        "rows": [],
        "session_id": "sess-1",
        "worker_id": "worker-1",
        "dealer_id": 100003,
    }

    def fake_api_post(_url, _jwt, path, _body=None):
        if path == "/sidecar/vahan/claim-batch":
            return claim_resp
        return {}

    jr = _job_runner_module()
    with (
        patch.object(jr, "_api_post", side_effect=fake_api_post),
        patch.object(jr, "_require_api_credentials", return_value=("http://api", "jwt")),
    ):
        out = jr._dispatch_fill_vahan_batch_impl({"api_url": "http://api", "jwt": "jwt"})

    assert out.get("success") is True
    ocr_dir = app.config.get_ocr_output_dir(100003)
    ocr_wrong = app.config.get_ocr_output_dir(100001)
    assert ocr_dir != ocr_wrong
    assert "100003" in str(ocr_dir.resolve())
