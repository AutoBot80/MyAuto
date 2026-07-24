"""Screen 3c insurance helpers — upto date and integer IDV."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.fill_rto_service import (
    _SCREEN3_INSURANCE_UPTO_INPUT,
    _SCREEN3_NOMINATION_DATE_INPUT,
    _insurance_upto_from_from_date,
    _normalize_idv_for_vahan,
    _resolve_policy_upto_str,
    _screen_3_fail_3c_required_field,
    _screen_3_insurer_option_matches,
    _vahan_nominee_relation_portal_label,
)


def test_insurance_upto_selector_prefers_live_vahan_id() -> None:
    assert "ins_upto_input" in _SCREEN3_INSURANCE_UPTO_INPUT[0]


def test_nomination_date_selector_includes_live_id() -> None:
    assert any("nominationdate" in s.lower() for s in _SCREEN3_NOMINATION_DATE_INPUT)


def test_bajaj_general_does_not_match_auto_fin() -> None:
    target = "Bajaj General Insurance Co. Ltd."
    assert _screen_3_insurer_option_matches(target, "Bajaj General Insurance Co. Ltd.")
    assert _screen_3_insurer_option_matches(target, "Bajaj General Insurance Co Ltd")
    assert not _screen_3_insurer_option_matches(target, "BAJAJ AUTO FIN LTD")
    assert not _screen_3_insurer_option_matches(target, "BAJAJ INSURANCE")
    assert not _screen_3_insurer_option_matches("Bajaj Allianz", "BAJAJ AUTO FIN LTD")
    assert _screen_3_insurer_option_matches("Bajaj Allianz General", "Bajaj General Insurance Co. Ltd.")


def test_screen_3_fail_3c_required_field_raises() -> None:
    class _Page:
        url = "https://example.test/"

        def evaluate(self, _script: str):
            return {"activeSubtab": "", "fields": [], "selectCount": 0}

        @property
        def frames(self):
            return [self]

    with pytest.raises(RuntimeError, match="Insurance Upto"):
        _screen_3_fail_3c_required_field(_Page(), "Insurance Upto", "expected '20-Jul-2031'")


def test_insurance_upto_five_year_minus_one_day() -> None:
    assert _insurance_upto_from_from_date(date(2026, 7, 21)) == "20-Jul-2031"
    assert _insurance_upto_from_from_date(date(2026, 7, 20)) == "19-Jul-2031"


def test_insurance_upto_leap_day() -> None:
    # Feb 29 + 5 calendar years → Feb 28 (non-leap), then − 1 day.
    assert _insurance_upto_from_from_date(date(2024, 2, 29)) == "27-Feb-2029"


def test_resolve_policy_upto_prefers_explicit_to_str() -> None:
    got = _resolve_policy_upto_str(
        {"policy_to_str": "18-Jul-2031", "policy_from_str": "20-Jul-2026"}
    )
    assert got == "18-Jul-2031"


def test_resolve_policy_upto_computed_from_policy_from_str() -> None:
    got = _resolve_policy_upto_str({"policy_from_str": "20-Jul-2026"})
    assert got == "19-Jul-2031"


def test_normalize_idv_strips_decimals_and_commas() -> None:
    assert _normalize_idv_for_vahan(Decimal("90000.00")) == "90000"
    assert _normalize_idv_for_vahan(90000.0) == "90000"
    assert _normalize_idv_for_vahan("90,000.00") == "90000"


def test_vahan_nominee_relation_wife_husband_map_to_spouse() -> None:
    assert _vahan_nominee_relation_portal_label("Wife") == "Spouse"
    assert _vahan_nominee_relation_portal_label("Husband") == "Spouse"
    assert _vahan_nominee_relation_portal_label("Mother") == "Mother"
