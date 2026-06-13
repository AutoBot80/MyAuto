"""Vahan district resolution — dealer RTO place, not village/city."""

from app.services.fill_rto_service import (
    _district_from_dealer_rto,
    _resolve_vahan_district,
)


def test_district_from_dealer_rto_rto_prefix() -> None:
    assert _district_from_dealer_rto("RTO-Bharatpur") == "Bharatpur"
    assert _district_from_dealer_rto("RTO-BHARATPUR") == "Bharatpur"


def test_district_from_dealer_rto_suffix() -> None:
    assert _district_from_dealer_rto("BHARATPUR RTO") == "Bharatpur"


def test_resolve_vahan_district_uses_dealer_rto_not_city() -> None:
    data = {
        "city": "Barakhur",
        "district": "",
        "dealer_rto": "RTO-Bharatpur",
    }
    assert _resolve_vahan_district(data) == "Bharatpur"


def test_resolve_vahan_district_prefers_explicit() -> None:
    data = {
        "city": "Barakhur",
        "district": "Jaipur",
        "dealer_rto": "RTO-Bharatpur",
    }
    assert _resolve_vahan_district(data) == "Jaipur"
