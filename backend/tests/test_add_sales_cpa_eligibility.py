"""CPA Insurance button eligibility via create-invoice-eligibility."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

from app.routers.add_sales import (
    _cpa_alliance_insurance_eligibility,
    _eligibility_by_customer_vehicle_ids,
    _has_cpa_insurance_master_row,
)


def test_cpa_alliance_insurance_eligibility_no_ids() -> None:
    out = _cpa_alliance_insurance_eligibility(has_cpa_row=False, ids_resolved=False)
    assert out["cpa_alliance_insurance_enabled"] is False
    assert "IDs are required" in str(out["cpa_alliance_insurance_reason"])


def test_cpa_alliance_insurance_eligibility_no_row() -> None:
    out = _cpa_alliance_insurance_eligibility(has_cpa_row=False, ids_resolved=True)
    assert out["cpa_alliance_insurance_enabled"] is True
    assert out["cpa_alliance_insurance_reason"] is None


def test_cpa_alliance_insurance_eligibility_row_exists() -> None:
    out = _cpa_alliance_insurance_eligibility(has_cpa_row=True, ids_resolved=True)
    assert out["cpa_alliance_insurance_enabled"] is False
    assert "already recorded" in str(out["cpa_alliance_insurance_reason"])


def test_has_cpa_insurance_master_row_true() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = {"insurance_id": 99}
    assert _has_cpa_insurance_master_row(cur, 1, 2) is True
    cur.execute.assert_called_once()
    _sql, params = cur.execute.call_args[0]
    assert "insurance_type = 'CPA'" in _sql
    assert params == (1, 2, date.today().year)


def test_has_cpa_insurance_master_row_false() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = None
    assert _has_cpa_insurance_master_row(cur, 3, 4) is False


@contextmanager
def _fake_connection(fetchone_results: list):
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchone_results)
    cm = MagicMock()
    cm.__enter__.return_value = cur
    cm.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cm
    yield conn


def test_eligibility_by_ids_cpa_row_disables_cpa() -> None:
    with patch("app.routers.add_sales.get_connection") as gc:
        with _fake_connection([None, {"insurance_id": 10}]) as conn:
            gc.return_value = conn
            out = _eligibility_by_customer_vehicle_ids(100, 200)
    assert out["cpa_alliance_insurance_enabled"] is False
    assert "already recorded" in str(out["cpa_alliance_insurance_reason"])


def test_eligibility_by_ids_no_cpa_row_enables_cpa() -> None:
    with patch("app.routers.add_sales.get_connection") as gc:
        with _fake_connection(
            [
                {"invoice_number": "INV-1"},
                None,
                None,
            ]
        ) as conn:
            gc.return_value = conn
            out = _eligibility_by_customer_vehicle_ids(100, 200)
    assert out["cpa_alliance_insurance_enabled"] is True
    assert out["generate_insurance_enabled"] is True
