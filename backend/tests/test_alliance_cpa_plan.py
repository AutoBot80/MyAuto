"""Alliance CPA plan preset helper."""

import os

from app.services.add_alliance_cpa_insurance import (
    ALLIANCE_CPA_FLEXIBLE_PLAN_TOTAL_AMOUNT_DEFAULT,
    ALLIANCE_CPA_PLAN_DEFAULT,
    _resolve_alliance_cpa_flexible_plan_total_amount,
    _resolve_alliance_cpa_plan_name,
)


def test_resolve_alliance_cpa_plan_default(monkeypatch):
    monkeypatch.delenv("ALLIANCE_CPA_PLAN", raising=False)
    assert _resolve_alliance_cpa_plan_name() == ALLIANCE_CPA_PLAN_DEFAULT


def test_resolve_alliance_cpa_plan_env_override(monkeypatch):
    monkeypatch.setenv("ALLIANCE_CPA_PLAN", "PLAN348 RGI")
    assert _resolve_alliance_cpa_plan_name() == "PLAN348 RGI"


def test_resolve_alliance_cpa_flexible_plan_total_amount_default(monkeypatch):
    monkeypatch.delenv("ALLIANCE_CPA_PLAN_TOTAL_AMOUNT", raising=False)
    assert _resolve_alliance_cpa_flexible_plan_total_amount() == ALLIANCE_CPA_FLEXIBLE_PLAN_TOTAL_AMOUNT_DEFAULT
    assert ALLIANCE_CPA_FLEXIBLE_PLAN_TOTAL_AMOUNT_DEFAULT == "1100"


def test_resolve_alliance_cpa_flexible_plan_total_amount_env(monkeypatch):
    monkeypatch.setenv("ALLIANCE_CPA_PLAN_TOTAL_AMOUNT", "1200")
    assert _resolve_alliance_cpa_flexible_plan_total_amount() == "1200"
