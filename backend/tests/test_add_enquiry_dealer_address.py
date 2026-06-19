"""Add Enquiry opportunity form — dealer_ref address defaults."""

from unittest.mock import MagicMock, patch

from app.repositories import form_dms as form_dms_repo
from app.services.hero_dms_playwright_customer import (
    _add_enquiry_landline_to_fill,
    _dms_values_dealer_id,
    _enquiry_dealer_address_defaults,
    _resolve_add_enquiry_address_fields,
)


def test_dms_values_dealer_id_from_top_level() -> None:
    assert _dms_values_dealer_id({"dealer_id": 100003}) == 100003


def test_dms_values_dealer_id_from_row() -> None:
    assert _dms_values_dealer_id({"row": {"dealer_id": 100001}}) == 100001


def test_dms_values_dealer_id_invalid() -> None:
    assert _dms_values_dealer_id({"dealer_id": "x"}) is None
    assert _dms_values_dealer_id({}) is None


def test_resolve_add_enquiry_address_arora_subdealer() -> None:
    """100003: KAMAN tehsil/city, Bharatpur district, RAJASTHAN state."""
    dms = {
        "state": "Rajasthan",
        "district": "",
        "tehsil": "",
        "city": "Bharatpur",
    }
    dealer = {
        "city": "KAMAN",
        "state": "RAJASTHAN",
        "district": "Bharatpur",
    }
    state, dist, tehsil, city = _resolve_add_enquiry_address_fields(dms, dealer)
    assert state == "RAJASTHAN"
    assert dist == "Bharatpur"
    assert tehsil == "KAMAN"
    assert city == "KAMAN"


def test_resolve_add_enquiry_address_arya_parent() -> None:
    """100001: all dealer defaults when customer city is also Bharatpur."""
    dms = {
        "state": "Rajasthan",
        "district": "",
        "tehsil": "",
        "city": "Bharatpur",
    }
    dealer = {
        "city": "Bharatpur",
        "state": "RAJASTHAN",
        "district": "Bharatpur",
    }
    state, dist, tehsil, city = _resolve_add_enquiry_address_fields(dms, dealer)
    assert state == "RAJASTHAN"
    assert dist == "Bharatpur"
    assert tehsil == "Bharatpur"
    assert city == "Bharatpur"


def test_resolve_add_enquiry_address_customer_fallback_when_dealer_blank() -> None:
    dms = {
        "state": "RAJASTHAN",
        "district": "Jaipur",
        "tehsil": "Amber",
        "city": "Jaipur",
    }
    dealer = {"city": "", "state": "", "district": ""}
    state, dist, tehsil, city = _resolve_add_enquiry_address_fields(dms, dealer)
    assert state == "RAJASTHAN"
    assert dist == "Jaipur"
    assert tehsil == "Amber"
    assert city == "Jaipur"


def test_resolve_add_enquiry_district_falls_back_to_customer_city() -> None:
    dms = {"state": "RAJASTHAN", "district": "", "tehsil": "", "city": "Bharatpur"}
    dealer = {"city": "KAMAN", "state": "RAJASTHAN", "district": ""}
    _, dist, _, _ = _resolve_add_enquiry_address_fields(dms, dealer)
    assert dist == "Bharatpur"


@patch("app.repositories.form_dms.get_connection")
def test_lookup_dealer_enquiry_address_parses_rto_district(mock_get_conn: MagicMock) -> None:
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {
        "city": "KAMAN",
        "state": "Rajasthan",
        "rto_name": "RTO-Bharatpur",
    }
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_conn.return_value = mock_conn

    out = form_dms_repo.lookup_dealer_enquiry_address(100003)
    assert out == {
        "city": "KAMAN",
        "state": "RAJASTHAN",
        "district": "Bharatpur",
    }


def test_enquiry_dealer_address_defaults_reads_embedded_payload() -> None:
    dms = {
        "dealer_enquiry_address": {
            "city": "KAMAN",
            "state": "RAJASTHAN",
            "district": "Bharatpur",
        },
    }
    out = _enquiry_dealer_address_defaults(dms)
    assert out == {"city": "KAMAN", "state": "RAJASTHAN", "district": "Bharatpur"}


def test_enquiry_dealer_address_defaults_empty_when_missing() -> None:
    assert _enquiry_dealer_address_defaults({}) == {
        "city": "",
        "state": "",
        "district": "",
    }
    assert _enquiry_dealer_address_defaults({"dealer_enquiry_address": "bad"}) == {
        "city": "",
        "state": "",
        "district": "",
    }


@patch("app.services.fill_hero_dms_service.form_dms_repo.lookup_dealer_enquiry_address")
@patch("app.services.fill_hero_dms_service.form_dms_repo.build_dms_fill_row_from_staging_payload")
def test_build_dms_fill_values_embeds_dealer_enquiry_address(
    mock_build_row: MagicMock,
    mock_lookup: MagicMock,
) -> None:
    from app.services.fill_hero_dms_service import _build_dms_fill_values

    mock_build_row.return_value = {
        "dealer_id": 100003,
        "Contact First Name": "Test",
        "Contact Last Name": "User",
        "Mobile Phone #": "9414687819",
        "State": "RAJASTHAN",
        "Address Line 1": "Kanjoli, Bharatpur",
        "Pin Code": "321026",
        "Key num (partial)": "13601234",
        "Frame / Chassis num (partial)": "MBLHAW488T5B",
        "Engine num (partial)": "HA11F7T5B514",
        "DMS Contact Path": "new_enquiry",
        "Finance Required": "N",
    }
    mock_lookup.return_value = {"city": "KAMAN", "state": "RAJASTHAN", "district": "Bharatpur"}
    staging = {
        "dealer_id": 100003,
        "customer": {
            "name": "Test User",
            "mobile_number": "9414687819",
            "state": "RAJASTHAN",
            "address": "Kanjoli, Bharatpur",
            "city": "Bharatpur",
            "pin": "321026",
            "aadhar_id": "1234",
            "gender": "Male",
            "dms_contact_path": "new_enquiry",
        },
        "vehicle": {
            "key_no": "13601234",
            "frame_no": "MBLHAW488T5B83631",
            "engine_no": "HA11F7T5B51406",
        },
    }
    values = _build_dms_fill_values(None, None, staging_payload=staging)
    mock_lookup.assert_called_once_with(100003)
    assert values["dealer_enquiry_address"] == {
        "city": "KAMAN",
        "state": "RAJASTHAN",
        "district": "Bharatpur",
    }


def test_add_enquiry_landline_skip_blank_alternate() -> None:
    value, required = _add_enquiry_landline_to_fill("9414687819", "")
    assert value == ""
    assert required is False


def test_add_enquiry_landline_skip_same_as_mobile() -> None:
    value, required = _add_enquiry_landline_to_fill("9414687819", "9414687819")
    assert value == ""
    assert required is False


def test_add_enquiry_landline_fill_different_alternate() -> None:
    value, required = _add_enquiry_landline_to_fill("9414687819", "9568564536")
    assert value == "9568564536"
    assert required is True


def test_add_enquiry_landline_normalizes_formatted_alternate() -> None:
    value, required = _add_enquiry_landline_to_fill("9414687819", "+91 95685 64536")
    assert value == "9568564536"
    assert required is True
