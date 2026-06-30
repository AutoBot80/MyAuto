"""Vehicle chassis/engine scrape validation (reject invoice datetimes in identity fields)."""

from app.services.add_sales_commit_service import (
    _dms_values_vehicle_from_staging,
    _vehicle_scrape_for_commit,
)
from app.services.fill_hero_dms_service import (
    _merge_staging_payload_with_scrape_for_commit,
    _vehicle_chassis_engine_from_scrape_dict,
)
from app.services.hero_dms_shared_utilities import (
    _coerce_vehicle_engine_for_db,
    _looks_like_vehicle_engine_number,
    _strip_invalid_vehicle_identity_from_scrape,
)


def test_datetime_rejected_as_engine():
    bad = "29/06/2026 03:50:33 PM"
    assert not _looks_like_vehicle_engine_number(bad)
    assert _coerce_vehicle_engine_for_db(bad) is None


def test_valid_hero_engine_accepted():
    good = "HA11F6THE45403"
    assert _looks_like_vehicle_engine_number(good)
    assert _coerce_vehicle_engine_for_db(good) == good


def test_strip_invalid_vehicle_identity_from_scrape():
    scraped = {
        "full_chassis": "MBLHAW473THE45433",
        "full_engine": "29/06/2026 03:50:33 PM",
        "engine_num": "29/06/2026 03:50:33 PM",
        "frame_num": "45433",
        "key_num": "2166",
    }
    cleaned = _strip_invalid_vehicle_identity_from_scrape(scraped)
    assert cleaned.get("full_chassis") == "MBLHAW473THE45433"
    assert "full_engine" not in cleaned
    assert "engine_num" not in cleaned
    assert "frame_num" not in cleaned
    assert cleaned.get("key_num") == "2166"


def test_vehicle_chassis_engine_from_scrape_dict_ignores_datetime_engine():
    chassis, engine = _vehicle_chassis_engine_from_scrape_dict(
        {
            "full_chassis": "MBLHAW473THE45433",
            "full_engine": "29/06/2026 03:50:33 PM",
            "engine_num": "45403",
        }
    )
    assert chassis == "MBLHAW473THE45433"
    assert engine is None


def test_vehicle_scrape_for_commit_strips_datetime_engine():
    out = _vehicle_scrape_for_commit(
        {"frame_no": "MBLHAW473THE45433", "engine_no": "HA11F6THE45403", "key_no": "2166"},
        {
            "full_chassis": "MBLHAW473THE45433",
            "full_engine": "29/06/2026 03:50:33 PM",
            "frame_num": "45433",
            "engine_num": "45403",
        },
    )
    assert out.get("full_engine") is None
    assert out.get("engine_num") == "HA11F6THE45403"


def test_merge_staging_does_not_write_datetime_engine_no():
    merged = _merge_staging_payload_with_scrape_for_commit(
        {"vehicle": {"frame_no": "45433", "engine_no": "45403", "key_no": "2166"}},
        {
            "full_chassis": "MBLHAW473THE45433",
            "full_engine": "29/06/2026 03:50:33 PM",
            "invoice_number": "11870BF26S688",
        },
    )
    veh = merged["vehicle"]
    assert veh["frame_no"] == "MBLHAW473THE45433"
    assert veh.get("engine_no") != "29/06/2026 03:50:33 PM"
    assert veh.get("engine_no") in (None, "45403")


def test_dms_values_partial_after_full_merge_with_datetime_engine_in_scrape():
    vehicle = {
        "frame_no": "MBLHAW473THE45433",
        "engine_no": "29/06/2026 03:50:33 PM",
        "key_no": "2166",
    }
    scrape = {
        "full_chassis": "MBLHAW473THE45433",
        "full_engine": "29/06/2026 03:50:33 PM",
        "frame_num": "45433",
        "engine_num": "45403",
    }
    partials = _dms_values_vehicle_from_staging(vehicle, scrape)
    assert partials["frame_partial"] == "45433"
    assert partials["engine_partial"] == "45403"
