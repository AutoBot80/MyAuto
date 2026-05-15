"""First-threshold fuzzy option pick (Alliance model/year dropdowns)."""

from app.services.utility_functions import fuzzy_first_option_label_at_or_above, fuzzy_option_match_score


def test_fuzzy_first_option_picks_first_above_threshold_not_global_best():
    options = [
        "DESTINI 125 ZX+ PHASE 2",
        "SPLENDOR + X TEC 2.0 E20 PHASE 2",
        "SPLENDOR + X TEC DSS CCR E20 PHASE 2",
        "GLAMOUR DRS CCR",
    ]
    pick = fuzzy_first_option_label_at_or_above("Splendor", options, min_score=0.70)
    assert pick == "SPLENDOR + X TEC 2.0 E20 PHASE 2"
    assert fuzzy_option_match_score("Splendor", pick) >= 0.70


def test_fuzzy_first_option_returns_none_when_nothing_meets_threshold():
    options = ["DESTINI 125 ZX+ PHASE 2", "GLAMOUR DRS CCR"]
    assert fuzzy_first_option_label_at_or_above("Splendor", options, min_score=0.70) is None
