"""Production guard for Admin Delete All Data."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

CONFIRMATION = {"confirmation": "DELETE ALL DATA"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


@contextmanager
def _mock_reset_db(*, table_names: list[str], preserved_names: list[str]):
    mock_cur = MagicMock()
    mock_cur.fetchall.side_effect = [
        [{"table_name": name} for name in table_names],
        [{"table_name": name} for name in preserved_names],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch("app.routers.admin.get_connection", return_value=mock_conn):
        yield mock_conn


def test_reset_all_data_forbidden_in_production(client: TestClient) -> None:
    with patch("app.routers.admin.ENVIRONMENT_IS_PRODUCTION", True):
        r = client.post("/admin/reset-all-data", json=CONFIRMATION)
    assert r.status_code == 403
    assert "disabled in production" in r.json()["detail"].lower()


def test_reset_all_data_allowed_in_non_production(client: TestClient) -> None:
    with patch("app.routers.admin.ENVIRONMENT_IS_PRODUCTION", False):
        with _mock_reset_db(table_names=["sales_master"], preserved_names=["dealer_ref"]):
            r = client.post("/admin/reset-all-data", json=CONFIRMATION)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["truncated_count"] == 1
    assert body["truncated_tables"] == ["sales_master"]
    assert body["preserved_tables"] == ["dealer_ref"]


def test_site_urls_exposes_environment_is_production(client: TestClient) -> None:
    with patch("app.routers.settings.ENVIRONMENT_IS_PRODUCTION", True):
        r = client.get("/settings/site-urls")
    assert r.status_code == 200
    assert r.json()["environment_is_production"] is True
