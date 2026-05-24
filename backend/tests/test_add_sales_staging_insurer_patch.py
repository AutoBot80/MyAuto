"""Staging PATCH insurer and GI overlay precedence."""

from app.schemas.add_sales_staging_patch import (
    PatchAddSalesStagingInsurance,
    PatchAddSalesStagingPayloadRequest,
)
from app.services.add_sales_staging_patch_service import _build_patch_from_request
from app.services.insurance_form_values import _apply_staging_insurance_overlay


def test_build_patch_includes_sanitized_insurer() -> None:
    req = PatchAddSalesStagingPayloadRequest(
        insurance=PatchAddSalesStagingInsurance(insurer="Bajaj Allianz General Insurance"),
    )
    patch = _build_patch_from_request(req)
    assert patch["insurance"]["insurer"] == "Bajaj Allianz General Insurance"


def test_staging_insurer_overrides_view_insurer() -> None:
    values = {"insurer": "The New India Assurance Co. Ltd."}
    staging = {"insurance": {"insurer": "Bajaj Allianz General Insurance"}}
    _apply_staging_insurance_overlay(values, staging)
    assert values["insurer"] == "Bajaj Allianz General Insurance"
