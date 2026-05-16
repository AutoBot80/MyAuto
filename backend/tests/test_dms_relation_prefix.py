"""Tests for dms_relation_prefix derivation (care_of before address)."""

from app.services.dms_relation_prefix import compute_dms_relation_prefix


def test_prefix_from_care_of():
    assert compute_dms_relation_prefix(care_of="S/O Raman Singh", address="Jatoli Thoon") == "S/O"


def test_prefix_from_address_when_care_of_short():
    assert compute_dms_relation_prefix(care_of="SO", address="Jatoli Thoon") == "Jat"


def test_prefix_gender_fallback():
    assert compute_dms_relation_prefix(care_of="", address="", gender="Male") == "S/o"
    assert compute_dms_relation_prefix(care_of=None, address=None, gender="female") == "D/o"
