"""Staging insurance patch for Hero GI policy display after commit."""

from app.services.add_sales_commit_service import (
    _build_staging_insurance_patch_main,
    _staging_insurance_patch_main_from_insert_row,
    _staging_insurance_patch_main_from_scrape,
)


def test_staging_patch_main_from_insert_row() -> None:
    patch = _staging_insurance_patch_main_from_insert_row(
        {
            "policy_num": "GI-123",
            "policy_from": "2026-01-01",
            "policy_to": "2027-01-01",
            "premium": 5400.0,
        }
    )
    assert patch["insurance"]["policy_num"] == "GI-123"
    assert patch["insurance"]["premium"] == 5400.0


def test_staging_patch_main_from_scrape() -> None:
    patch = _staging_insurance_patch_main_from_scrape(
        {"policy_num": "GI-999", "premium": "1200", "idv": 61677.0}
    )
    assert patch == {
        "insurance": {"policy_num": "GI-999", "premium": 1200.0, "idv": 61677.0}
    }


def test_staging_patch_main_includes_idv() -> None:
    patch = _build_staging_insurance_patch_main(policy_num="P1", idv=50000, premium=1000)
    assert patch["insurance"]["idv"] == 50000.0
    assert patch["insurance"]["premium"] == 1000.0


def test_staging_patch_main_empty() -> None:
    assert _build_staging_insurance_patch_main() == {}
