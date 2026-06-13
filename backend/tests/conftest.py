"""Ensure required env vars exist before ``app`` imports (``validate_external_site_urls``)."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DMS_BASE_URL", "https://example.com/dms")
os.environ.setdefault("INSURANCE_BASE_URL", "https://example.com/ins")
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("DEALER_ID", "100001")


@pytest.fixture(autouse=True)
def _mock_admin_dealer_scope_for_auth_disabled():
    """AUTH_DISABLED dev principal is admin; avoid DATABASE_URL for scope lookups in unit tests."""
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001, 100003],
    ):
        yield
