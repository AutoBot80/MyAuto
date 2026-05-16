"""Nominee gender derived from relationship (Add Sales OCR)."""
from __future__ import annotations

from app.services.sales_ocr_service import (
    _apply_nominee_relationship_gender_to_mapping,
    _refine_nominee_relationship_with_gender,
    _sync_nominee_relation_with_gender_across_fragments,
)
from app.services.utility_functions import derive_nominee_gender_from_relationship


def test_derive_gender_from_specific_relationship():
    assert derive_nominee_gender_from_relationship("Wife") == "Female"
    assert derive_nominee_gender_from_relationship("wife.") == "Female"
    assert derive_nominee_gender_from_relationship("Husband") == "Male"
    assert derive_nominee_gender_from_relationship("Sister") == "Female"
    assert derive_nominee_gender_from_relationship("Brother") == "Male"
    assert derive_nominee_gender_from_relationship("Daughter") == "Female"
    assert derive_nominee_gender_from_relationship("Son") == "Male"
    assert derive_nominee_gender_from_relationship("Wife/Husband") is None


def test_refine_slash_relationship_from_gender():
    assert _refine_nominee_relationship_with_gender("Wife/Husband", "Female") == "Wife"
    assert _refine_nominee_relationship_with_gender("Wife/Husband", "Male") == "Husband"


def test_wife_overrides_wrong_male_gender():
    out = {"nominee_relationship": "Wife", "nominee_gender": "Male"}
    _apply_nominee_relationship_gender_to_mapping(out)
    assert out["nominee_relationship"] == "Wife"
    assert out["nominee_gender"] == "Female"


def test_husband_overrides_wrong_female_gender():
    out = {"nominee_relationship": "Husband", "nominee_gender": "Female"}
    _apply_nominee_relationship_gender_to_mapping(out)
    assert out["nominee_relationship"] == "Husband"
    assert out["nominee_gender"] == "Male"


def test_wife_fills_missing_gender():
    out = {"nominee_relationship": "Wife"}
    _apply_nominee_relationship_gender_to_mapping(out)
    assert out["nominee_gender"] == "Female"


def test_sync_across_insurance_and_details_fragments():
    insurance = {"nominee_relationship": "Sister"}
    details = {"nominee_gender": "Male"}
    _sync_nominee_relation_with_gender_across_fragments(insurance, details)
    assert insurance["nominee_relationship"] == "Sister"
    assert insurance["nominee_gender"] == "Female"
    assert details["nominee_gender"] == "Female"
