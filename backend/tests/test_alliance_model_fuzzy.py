"""Alliance model fuzzy: SPL+ ↔ Splendor+, best match not first in list."""

from app.services.utility_functions import (
    alliance_model_match_score,
    fuzzy_best_alliance_model_label,
    normalize_for_alliance_model_match,
)


def test_normalize_spl_plus_to_splendor_plus():
    assert normalize_for_alliance_model_match("SPL + DRS CCR") == normalize_for_alliance_model_match(
        "Splendor + DRS CCR"
    )


def test_best_model_prefers_splendor_plus_variant_over_nxg():
    options = [
        "SPLENDOR NXG",
        "SPL + DRS CCR (NEW E20)",
        "SPLENDOR + X TEC 2.0 E20 PHASE 2",
    ]
    pick = fuzzy_best_alliance_model_label("Splendor + XTEC 2.0", options, min_score=0.70)
    assert pick == "SPLENDOR + X TEC 2.0 E20 PHASE 2"
    assert alliance_model_match_score("Splendor + XTEC 2.0", pick) >= 0.70


def test_spl_abbreviation_matches_splendor_plus_option():
    options = ["DESTINI 125", "SPL + DRS CCR E20 PHASE 2", "GLAMOUR DRS"]
    pick = fuzzy_best_alliance_model_label("Splendor + DRS", options, min_score=0.70)
    assert pick == "SPL + DRS CCR E20 PHASE 2"
