"""Alliance CPA plan preset helper."""

import os

from app.services.add_alliance_cpa_insurance import (
    ALLIANCE_CPA_PLAN_DEFAULT,
    _resolve_alliance_cpa_plan_name,
)


def test_resolve_alliance_cpa_plan_default(monkeypatch):
    monkeypatch.delenv("ALLIANCE_CPA_PLAN", raising=False)
    assert _resolve_alliance_cpa_plan_name() == ALLIANCE_CPA_PLAN_DEFAULT


def test_resolve_alliance_cpa_plan_env_override(monkeypatch):
    monkeypatch.setenv("ALLIANCE_CPA_PLAN", "Flexible-RGI")
    assert _resolve_alliance_cpa_plan_name() == "Flexible-RGI"
