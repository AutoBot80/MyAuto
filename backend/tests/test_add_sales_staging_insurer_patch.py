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


"""Staging PATCH insurer and GI overlay precedence."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.schemas.add_sales_staging_patch import (
    PatchAddSalesStagingInsurance,
    PatchAddSalesStagingPayloadRequest,
)
from app.services.add_sales_staging_patch_service import (
    _build_patch_from_request,
    _fetch_insurance_state_on_cursor,
    patch_add_sales_staging_payload,
)
from app.services.insurance_form_values import _apply_staging_insurance_overlay


def test_build_patch_includes_sanitized_insurer() -> None:
    req = PatchAddSalesStagingPayloadRequest(
        insurance=PatchAddSalesStagingInsurance(insurer="Bajaj Allianz General Insurance"),
    )
    patch = _build_patch_from_request(req)
    assert patch["insurance"]["insurer"] == "Bajaj Allianz General Insurance"


def test_build_patch_request_accepts_cpi_reqd() -> None:
    req = PatchAddSalesStagingPayloadRequest(cpi_reqd="Y")
    assert req.cpi_reqd == "Y"


def test_fetch_insurance_state_on_cursor() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = {"insurance_state": 2}
    assert _fetch_insurance_state_on_cursor(cur, staging_id="uuid", dealer_id=1) == 2


def test_patch_rejects_insurer_when_insurance_state_not_zero() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = {"insurance_state": 2}
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    req = PatchAddSalesStagingPayloadRequest(
        insurance=PatchAddSalesStagingInsurance(insurer="Bajaj Allianz General Insurance"),
    )
    with patch("app.services.add_sales_staging_patch_service.get_connection", return_value=conn):
        with patch(
            "app.services.add_sales_staging_patch_service._load_payload_json_for_update_on_cursor",
            return_value={"customer": {}, "vehicle": {}, "insurance": {}},
        ):
            try:
                patch_add_sales_staging_payload(
                    staging_id="00000000-0000-0000-0000-000000000001",
                    dealer_id=1,
                    req=req,
                )
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "insurance processing" in str(e).lower()


def test_patch_cpi_reqd_only_updates_column() -> None:
    cur = MagicMock()
    cur.rowcount = 1
    cur.fetchone.return_value = {"updated_at": datetime(2026, 1, 1, 12, 0, 0)}
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    req = PatchAddSalesStagingPayloadRequest(cpi_reqd="N")
    with patch("app.services.add_sales_staging_patch_service.get_connection", return_value=conn):
        with patch(
            "app.services.add_sales_staging_patch_service._load_payload_json_for_update_on_cursor",
            return_value={"customer": {}, "vehicle": {}, "insurance": {}},
        ):
            with patch(
                "app.services.add_sales_staging_patch_service.merge_staging_payload_on_cursor"
            ) as merge:
                result = patch_add_sales_staging_payload(
                    staging_id="00000000-0000-0000-0000-000000000001",
                    dealer_id=1,
                    req=req,
                )
                merge.assert_not_called()
                cpi_sql = cur.execute.call_args_list[0][0][0]
                assert "cpi_reqd" in cpi_sql
                assert result["ok"] is True


def test_staging_insurer_overrides_view_insurer() -> None:
    values = {"insurer": "The New India Assurance Co. Ltd."}
    staging = {"insurance": {"insurer": "Bajaj Allianz General Insurance"}}
    _apply_staging_insurance_overlay(values, staging)
    assert values["insurer"] == "Bajaj Allianz General Insurance"
