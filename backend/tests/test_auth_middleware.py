"""Auth middleware and public paths (no live DB required for basic checks)."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    # Import after env so config picks up AUTH_DISABLED
    from app.main import app

    return TestClient(app)


def test_health_public(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_auth_me_with_auth_disabled(client: TestClient) -> None:
    r = client.get("/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["login_id"] == "dev"
    assert "dealer_id" in body
