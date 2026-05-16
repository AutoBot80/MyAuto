"""Tests for staging-path vehicle_master scrape persist on Create Invoice commit."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.add_sales_commit_service import upsert_customer_vehicle_sales


def _minimal_payload() -> dict:
    return {
        "dealer_id": 100001,
        "file_location": "mob_160526",
        "customer": {
            "aadhar_id": "123456789012",
            "name": "Test Customer",
            "mobile_number": 9876543210,
            "gender": "Male",
            "address": "Some Address",
            "care_of": "S/O Father Name",
        },
        "vehicle": {
            "frame_no": "MBLHAW478THD09377",
            "engine_no": "HA11F6THD09898",
            "key_no": "1404",
            "battery_no": "M7CSQ67387",
            "order_number": "ORD-1",
            "invoice_number": "INV-1",
            "enquiry_number": "ENQ-1",
        },
    }


@patch("app.services.fill_hero_dms_service._vehicle_master_update_from_scrape_on_cursor")
def test_upsert_calls_vehicle_scrape_update_after_sales_insert(mock_vm_update):
    cur = MagicMock()
    cur.fetchone.side_effect = [
        None,  # customer SELECT — insert
        {"customer_id": 10},
        None,  # vehicle SELECT — insert
        {"vehicle_id": 20},
        {"sales_id": 30},
    ]
    scrape = {
        "full_chassis": "MBLHAW478THD09377",
        "full_engine": "HA11F6THD09898",
        "model": "SPLENDOR +",
        "color": "MAG",
        "variant": "HSPUNIRSCFIMAG",
        "year_of_mfg": "2026",
        "vehicle_type": "MOTORCYCLE WITH GEAR",
    }
    cid, vid, sid = upsert_customer_vehicle_sales(
        cur, _minimal_payload(), scraped_vehicle=scrape
    )
    assert (cid, vid, sid) == (10, 20, 30)
    mock_vm_update.assert_called_once()
    assert mock_vm_update.call_args[0][1] == 20
    assert mock_vm_update.call_args[0][2]["model"] == "SPLENDOR +"


@patch("app.services.fill_hero_dms_service._vehicle_master_update_from_scrape_on_cursor")
def test_upsert_skips_scrape_update_when_scrape_empty(mock_vm_update):
    cur = MagicMock()
    cur.fetchone.side_effect = [
        {"customer_id": 1},
        {"vehicle_id": 2},
        {"sales_id": 3},
    ]
    upsert_customer_vehicle_sales(cur, _minimal_payload(), scraped_vehicle={})
    mock_vm_update.assert_not_called()
