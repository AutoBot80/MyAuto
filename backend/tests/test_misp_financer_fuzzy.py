"""MISP proposal financier phrase containment (full passed value preferred over partial token match)."""

from app.services.fill_hero_insurance_service import (
    _misp_financer_phrase_contains,
    _norm_misp_financer_phrase,
    _pick_misp_financer_option_label,
    _proposal_financer_expected_matches_readback,
)

# Snapshot 2 — filtered list after typing .SHRIRAM FINANCE
SHRIRAM_FILTERED_CANDIDATES = [
    "shri ram city union finance limited",
    "SHRI RAM CITY UNION FINANCE LTD",
    "SHRI RAM CITY UNION FINANCE LTD",
    "SHRI RAM CITY UNION FINANCE LTD.",
    "SHRI RAM FINANCE LTD",
    "SHRI RAM FINANCE PVT.CORP.LTD,PANDARIA",
    "SHRI RAM FINENCE CORPORATION PVT LTD",
    "SHRIRAM CITY UNION FINANCE LTD",
    "Shriram City Union Finance Ltd.",
    "SHRIRAM FINANCE LIMITED",
    "SHRIRAM FINANCE LTD .",
    "SHRIRAM FINANCE LTD..",
    "SHRIRAM FINANCE LTD...",
    "State Bank Of Bikaner And Jaipur",
    "State Bank Of India",
]

# Snapshot 1 — top of full dropdown
SHRIRAM_FULL_DROPDOWN_HEAD = [
    "--Select--",
    ".AXIS BANK LTD.",
    ".SHRIRAM FINANCE LIMITED.",
    "AMRIT MALWA CAPITAL FINANCE",
    "AXIS BANK",
    "AXIS BANK LTD",
]


def test_norm_misp_financer_phrase_keeps_finance():
    assert _norm_misp_financer_phrase("Shriram Finance Ltd.") == "shriram finance"
    assert _norm_misp_financer_phrase(".SHRIRAM FINANCE") == "shriram finance"
    assert _norm_misp_financer_phrase(".SHRIRAM FINANCE LIMITED.") == "shriram finance"
    assert _norm_misp_financer_phrase("SHRI RAM CITY UNION FINANCE LTD") == "shri ram city union finance"


def test_phrase_contains_shriram_finance_not_city_union():
    qp = _norm_misp_financer_phrase("Shriram Finance Ltd.")
    assert _misp_financer_phrase_contains("SHRIRAM FINANCE LTD", qp)
    assert _misp_financer_phrase_contains("SHRI RAM FINANCE LTD", qp)
    assert _misp_financer_phrase_contains(".SHRIRAM FINANCE LIMITED.", qp)
    assert not _misp_financer_phrase_contains("SHRI RAM CITY UNION FINANCE LTD", qp)
    assert not _misp_financer_phrase_contains("SHRIRAM CITY UNION FINANCE LTD", qp)


def test_pick_prefers_shriram_finance_over_city_union_filtered_list():
    pick = _pick_misp_financer_option_label("Shriram Finance Ltd.", SHRIRAM_FILTERED_CANDIDATES)
    assert pick is not None
    assert "CITY" not in pick.upper() and "UNION" not in pick.upper()
    assert "FINANCE" in pick.upper()
    assert pick == "SHRI RAM FINANCE LTD"


def test_pick_dot_shriram_finance_query():
    pick = _pick_misp_financer_option_label(".SHRIRAM FINANCE", SHRIRAM_FILTERED_CANDIDATES)
    assert pick is not None
    assert "CITY" not in pick.upper()
    assert "FINANCE" in pick.upper()
    assert "RAM" in pick.upper()


def test_pick_shriram_from_full_dropdown_not_axis():
    pick = _pick_misp_financer_option_label("Shriram Finance Ltd.", SHRIRAM_FULL_DROPDOWN_HEAD)
    assert pick == ".SHRIRAM FINANCE LIMITED."


def test_readback_rejects_city_union_for_shriram_finance():
    assert not _proposal_financer_expected_matches_readback(
        "Shriram Finance Ltd.",
        "SHRI RAM CITY UNION FINANCE LTD",
    )
    assert _proposal_financer_expected_matches_readback(
        "Shriram Finance Ltd.",
        "SHRIRAM FINANCE LTD",
    )


def test_axis_bank_still_picks_axis():
    pick = _pick_misp_financer_option_label(".AXIS BANK LTD.", SHRIRAM_FULL_DROPDOWN_HEAD)
    assert pick is not None
    assert "AXIS" in pick.upper()
