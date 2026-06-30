"""Tests for staging-path vehicle_master scrape persist on Create Invoice commit."""

from unittest.mock import MagicMock, patch

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


@patch("app.services.fill_hero_dms_service._upsert_vehicle_master_from_scrape_on_cursor", return_value=20)
@patch("app.services.fill_hero_dms_service._upsert_customer_master_from_dms_on_cursor", return_value=10)
def test_upsert_uses_shared_master_upserts_before_sales_insert(mock_cust_upsert, mock_veh_upsert):
    cur = MagicMock()
    cur.fetchone.return_value = {"sales_id": 30}
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
    mock_cust_upsert.assert_called_once()
    mock_veh_upsert.assert_called_once()
    veh_scrape = mock_veh_upsert.call_args[0][2]
    assert veh_scrape["model"] == "SPLENDOR +"
    assert cur.execute.call_count == 1


@patch("app.services.fill_hero_dms_service._upsert_vehicle_master_from_scrape_on_cursor", return_value=2)
@patch("app.services.fill_hero_dms_service._upsert_customer_master_from_dms_on_cursor", return_value=1)
def test_upsert_vehicle_upsert_runs_even_when_scrape_empty(mock_cust_upsert, mock_veh_upsert):
    cur = MagicMock()
    cur.fetchone.return_value = {"sales_id": 3}
    upsert_customer_vehicle_sales(cur, _minimal_payload(), scraped_vehicle={})
    mock_veh_upsert.assert_called_once()
