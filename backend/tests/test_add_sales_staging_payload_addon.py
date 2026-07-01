"""Regression: staging payload add-on context must not raise NameError on import use."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.routers.add_sales import _resolve_insurance_addon_context


@patch("app.routers.add_sales.validate_dealer_insurance_addon_config", return_value=[])
@patch("app.routers.add_sales.resolve_effective_insurance_addon_row", return_value=None)
@patch("app.routers.add_sales.list_active_by_insurer", return_value=[])
@patch("app.routers.add_sales.fetch_staging_insurance_addon_on_cursor", return_value=None)
@patch("app.routers.add_sales.fetch_dealer_insurance_addon_on_cursor", return_value=None)
@patch("app.routers.add_sales.fetch_dealer_prefer_insurer_on_cursor", return_value="Bajaj Allianz")
@patch("app.routers.add_sales.get_connection")
def test_resolve_insurance_addon_context_uses_staging_fetch(
    mock_get_conn: MagicMock,
    *_mocks: object,
) -> None:
    conn = MagicMock()
    mock_get_conn.return_value = conn
    ctx = conn.cursor.return_value.__enter__.return_value
    ctx.fetchone.return_value = None

    result = _resolve_insurance_addon_context(100001, "2fad336a-9ec9-4020-be7e-c40f02a70fc1")

    assert result["prefer_insurer"] == "Bajaj Allianz"
    assert result["insurance_addons"] == []
