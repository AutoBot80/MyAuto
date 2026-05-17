"""Sidecar / gate pass: optional DB for template data enrichment."""

import pytest


def test_get_vehicle_from_db_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("app.config.DATABASE_URL", "", raising=False)

    from app.services import form20_service as f20

    assert f20._get_vehicle_from_db(999) == {}


def test_get_dealer_from_db_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("app.config.DATABASE_URL", "", raising=False)

    from app.services import form20_service as f20

    assert f20._get_dealer_from_db(100001) == {}
