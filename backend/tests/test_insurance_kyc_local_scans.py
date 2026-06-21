"""KYC upload must fail when local Aadhaar scans are missing (no placeholder PNGs)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.fill_hero_insurance_service import _kyc_proceed_or_upload


def test_kyc_proceed_or_upload_errors_when_local_scans_missing() -> None:
    page = MagicMock()
    page.evaluate.return_value = "please upload aadhaar"

    files = MagicMock()
    files.count.return_value = 3
    page.locator.return_value = files

    with (
        patch(
            "app.services.fill_hero_insurance_service._kyc_preferred_kyc_frame",
            return_value=page,
        ),
        patch(
            "app.services.fill_hero_insurance_service._kyc_locator_file_inputs_best",
            return_value=files,
        ),
        patch(
            "app.services.fill_hero_insurance_service._kyc_scrape_file_inputs_metadata",
            return_value=[],
        ),
        patch(
            "app.services.fill_hero_insurance_service._kyc_resolve_upload_nth_order",
            return_value=([0, 1, 2], "test"),
        ),
        patch(
            "app.services.fill_hero_insurance_service._kyc_note_file_inputs_scrape",
        ),
    ):
        err = _kyc_proceed_or_upload(
            page,
            timeout_ms=1000,
            kyc_local_scan_paths=None,
        )

    assert err is not None
    assert "Aadhar_front.jpg" in err
    assert "Aadhar_back.jpg" in err
