"""Sidecar script-sync: git commit id matching (ALB bundle validation)."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _job_runner_module():
    path = Path(__file__).resolve().parents[2] / "electron" / "sidecar" / "job_runner.py"
    spec = importlib.util.spec_from_file_location("job_runner_git_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_git_commits_match_short_and_full() -> None:
    jr = _job_runner_module()
    assert jr._git_commits_match("ecd5000", "ecd5000")
    assert jr._git_commits_match("ecd50000d9014879a7ea3c0df0fb4e407ff3eed0", "ecd5000")
    assert not jr._git_commits_match("ecd5000", "bc40d48")
    assert not jr._git_commits_match("", "ecd5000")
