"""Ensure required env vars exist before ``app`` imports (``validate_external_site_urls``)."""

import os

os.environ.setdefault("DMS_BASE_URL", "https://example.com/dms")
os.environ.setdefault("INSURANCE_BASE_URL", "https://example.com/ins")
os.environ.setdefault("AUTH_DISABLED", "true")
