"""Subdealer challan sidecar must prefer resolve from_dealer_id for local/API dealer scope."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_challan_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_sidecar_local_dealer_id_prefers_from_dealer_for_challan(
    monkeypatch,
) -> None:
    jr = _job_runner_module()
    monkeypatch.setenv("DEALER_ID", "100003")
    assert (
        jr._resolve_sidecar_local_dealer_id(
            {},
            {"from_dealer_id": 100001, "to_dealer_id": 100003},
            ctx_keys=("from_dealer_id", "dealer_id"),
        )
        == 100001
    )


def test_get_challan_artifacts_dir_scoped_by_dealer(tmp_path, monkeypatch) -> None:
    saathi = tmp_path / "saathi"
    saathi.mkdir()
    monkeypatch.setenv("SAATHI_BASE_DIR", str(saathi))
    import importlib

    import app.config

    importlib.reload(app.config)

    a = app.config.get_challan_artifacts_dir(100001, "CH1_21062026")
    b = app.config.get_challan_artifacts_dir(100003, "CH1_21062026")
    assert "100001" in str(a)
    assert "100003" in str(b)
    assert a != b


def test_resolve_sidecar_local_dealer_id_falls_back_to_params_then_env(
    monkeypatch,
) -> None:
    jr = _job_runner_module()
    monkeypatch.setenv("DEALER_ID", "100001")
    assert jr._resolve_sidecar_local_dealer_id({"dealer_id": 100003}) == 100003
    assert jr._resolve_sidecar_local_dealer_id({}) == 100001
