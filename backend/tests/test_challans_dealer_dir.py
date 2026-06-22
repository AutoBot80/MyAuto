"""Dealer-scoped Challans directory layout."""

from __future__ import annotations

import importlib

from app.config import get_challan_artifacts_dir, get_challans_dir


def test_get_challans_dir_includes_dealer_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SAATHI_BASE_DIR", str(tmp_path / "saathi"))
    import app.config

    importlib.reload(app.config)

    root = app.config.get_challans_dir(100003)
    assert root == tmp_path / "saathi" / "Challans" / "100003"
    leaf_dir = app.config.get_challan_artifacts_dir(100003, "CH1_21062026")
    assert leaf_dir == root / "CH1_21062026"
    assert get_challans_dir(100001) != get_challans_dir(100003)
