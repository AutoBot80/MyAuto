"""Tests for Add Sales staging natural keys and Submit Info upsert rules."""

from unittest.mock import MagicMock, patch

import pytest

from app.constants.add_sales_submit import SUBMIT_INFO_COMMITTED_SALE_MSG
from app.repositories import add_sales_staging as repo
from app.services.add_sales_commit_service import build_staging_commit_patch


def test_normalize_mobile_last_10():
    assert repo.normalize_staging_natural_key_mobile(919876543210) == "9876543210"
    assert repo.normalize_staging_natural_key_mobile("0919876543210") == "9876543210"
    assert repo.normalize_staging_natural_key_mobile(None) is None


def test_normalize_text_casefold_collapse_space():
    assert repo.normalize_staging_natural_key_text("  AB  cd  ") == "ab cd"
    assert repo.normalize_staging_natural_key_text("") is None


def test_normalize_cpi_reqd_flag():
    assert repo._normalize_cpi_reqd_flag("Y") == "Y"
    assert repo._normalize_cpi_reqd_flag("n") == "N"
    assert repo._normalize_cpi_reqd_flag(None) == "N"


def test_fetch_dealer_cpi_reqd_on_cursor():
    cur = MagicMock()
    cur.fetchone.return_value = {"cpi_reqd": "Y"}
    assert repo.fetch_dealer_cpi_reqd_on_cursor(cur, dealer_id=100001) == "Y"
    cur.fetchone.return_value = None
    assert repo.fetch_dealer_cpi_reqd_on_cursor(cur, dealer_id=999) == "N"


def test_persist_submit_updates_cpi_reqd_on_draft():
    cur = MagicMock()
    cur.fetchone.return_value = {"status": "draft"}
    cur.rowcount = 1
    payload = {
        "dealer_id": 1,
        "file_location": None,
        "customer": {"mobile_number": 9876543210, "name": "X"},
        "vehicle": {"frame_no": "CH1", "engine_no": "EN1"},
        "insurance": {},
    }
    with patch(
        "app.repositories.add_sales_staging.resolve_dealer_insurance_addon_for_insert_on_cursor",
        return_value=None,
    ):
        sid = repo.persist_staging_for_submit(
            cur,
            dealer_id=1,
            payload=payload,
            staging_id_existing="11111111-1111-1111-1111-111111111111",
            login_id=None,
            cpi_reqd="Y",
        )
    assert sid == "11111111-1111-1111-1111-111111111111"
    sql = cur.execute.call_args_list[-1][0][0]
    assert "cpi_reqd = %s" in sql


def test_persist_submit_defaults_cpi_reqd_to_dealer_when_omitted():
    cur = MagicMock()
    cur.fetchone.side_effect = [{"status": "draft"}, {"cpi_reqd": "Y"}]
    cur.rowcount = 1
    payload = {
        "dealer_id": 100001,
        "file_location": None,
        "customer": {"mobile_number": 9876543210, "name": "X"},
        "vehicle": {"frame_no": "CH1", "engine_no": "EN1"},
        "insurance": {},
    }
    with patch(
        "app.repositories.add_sales_staging.resolve_dealer_insurance_addon_for_insert_on_cursor",
        return_value=None,
    ):
        sid = repo.persist_staging_for_submit(
            cur,
            dealer_id=100001,
            payload=payload,
            staging_id_existing="11111111-1111-1111-1111-111111111111",
            login_id=None,
            cpi_reqd=None,
        )
    assert sid == "11111111-1111-1111-1111-111111111111"
    update_call = cur.execute.call_args_list[-1]
    assert update_call[0][1][3] == "Y"


def test_persist_submit_raises_when_staging_id_is_committed():
    cur = MagicMock()
    cur.fetchone.return_value = {"status": "committed"}
    payload = {
        "dealer_id": 1,
        "file_location": None,
        "customer": {"mobile_number": 9876543210, "name": "X"},
        "vehicle": {"frame_no": "CH1", "engine_no": "EN1"},
        "insurance": {},
    }
    with pytest.raises(ValueError) as ei:
        repo.persist_staging_for_submit(
            cur,
            dealer_id=1,
            payload=payload,
            staging_id_existing="11111111-1111-1111-1111-111111111111",
            login_id=None,
        )
    assert str(ei.value) == SUBMIT_INFO_COMMITTED_SALE_MSG


def test_build_staging_commit_patch_ids_only():
    patch = build_staging_commit_patch(
        {"customer": {}, "vehicle": {}},
        customer_id=10,
        vehicle_id=20,
        sales_id=30,
    )
    assert patch == {"customer_id": 10, "vehicle_id": 20, "sales_id": 30}


def test_build_staging_commit_patch_financier_and_vehicle_numbers():
    patch = build_staging_commit_patch(
        {
            "customer": {"financier": "  Hinduja  "},
            "vehicle": {
                "order_number": "ORD-1",
                "invoice_number": "INV-9",
                "enquiry_number": "ENQ-2",
            },
        },
        customer_id=1,
        vehicle_id=2,
    )
    assert patch["customer_id"] == 1
    assert patch["vehicle_id"] == 2
    assert patch["customer"] == {"financier": "Hinduja"}
    assert patch["vehicle"] == {
        "order_number": "ORD-1",
        "invoice_number": "INV-9",
        "enquiry_number": "ENQ-2",
    }


def test_build_staging_commit_patch_skips_blank_vehicle_fields():
    patch = build_staging_commit_patch(
        {"customer": {"financier": ""}, "vehicle": {"order_number": "  ", "invoice_number": None}},
        customer_id=1,
        vehicle_id=2,
    )
    assert "customer" not in patch
    assert "vehicle" not in patch
