"""Unit tests for MISP proposal RTO dropdown query building and option picking."""

from app.services.fill_hero_insurance_service import (
    _misp_rto_city_from_query,
    _misp_rto_fuzzy_query,
    _pick_misp_rto_option_label,
    _proposal_model_portal_auto_populated,
    _proposal_select_readback_is_placeholder,
)


def test_misp_rto_fuzzy_query_rajasthan_bharatpur() -> None:
    assert _misp_rto_fuzzy_query(city="Bharatpur", state="Rajasthan") == "RJ - Bharatpur"
    assert _misp_rto_fuzzy_query(city="Bharatpur", state="RAJASTHAN") == "RJ - Bharatpur"


def test_misp_rto_fuzzy_query_unknown_state_falls_back_to_city() -> None:
    assert _misp_rto_fuzzy_query(city="Bharatpur", state="Foobaristan") == "Bharatpur"
    assert _misp_rto_fuzzy_query(city="Bharatpur", state="") == "Bharatpur"


def test_pick_misp_rto_prefers_bharatpur_over_bhartpur_typo() -> None:
    cands = ["RJ - BHARTPUR", "RJ - Bharatpur", "RJ - Barmer"]
    pick = _pick_misp_rto_option_label("Bharatpur", cands, state_prefix="RJ")
    assert pick == "RJ - Bharatpur"


def test_pick_misp_rto_full_query_prefers_correct_spelling() -> None:
    cands = ["RJ - BHARTPUR", "RJ - Bharatpur", "RJ - Barmer"]
    pick = _pick_misp_rto_option_label("RJ - Bharatpur", cands, state_prefix="RJ")
    assert pick == "RJ - Bharatpur"


def test_pick_misp_rto_only_typo_available() -> None:
    cands = ["RJ - BHARTPUR", "RJ - Barmer"]
    pick = _pick_misp_rto_option_label("Bharatpur", cands, state_prefix="RJ")
    assert pick == "RJ - BHARTPUR"


def test_misp_rto_city_from_query() -> None:
    assert _misp_rto_city_from_query("RJ - Bharatpur") == "Bharatpur"
    assert _misp_rto_city_from_query("Bharatpur") == "Bharatpur"


def test_proposal_model_portal_auto_populated() -> None:
    assert _proposal_select_readback_is_placeholder("--Select--")
    assert _proposal_select_readback_is_placeholder("")
    assert _proposal_model_portal_auto_populated(
        "model_name", "HF DLX 24 BS6 DRS CS FI"
    )
    assert not _proposal_model_portal_auto_populated("model_name", "--Select--")
    assert not _proposal_model_portal_auto_populated("rto", "RJ - Bharatpur")
