"""OCR address line cleanup: colons, duplicate PINs, junk between state and PIN."""

from app.services.customer_address_infer import (
    enrich_customer_address_from_freeform,
    normalize_address_freeform,
    resolve_indian_state_name,
    strip_junk_between_last_indian_state_and_pin,
    normalize_operator_freeform_address,
    validate_operator_freeform_address,
    _strip_leading_care_of_duplicate_from_work,
)
from app.services.sales_ocr_service import _clean_aadhar_back_cross_column_noise


def test_strip_junk_between_last_state_and_pin_removes_tail_noise():
    raw = "W/O Rakesh, Bani, Kumher, Rajasthan, mage, 3\u02b3\u1d48\u02b3, 321602"
    got = strip_junk_between_last_indian_state_and_pin(raw)
    assert "mage" not in got
    assert "321602" in got
    assert got.rstrip().endswith("Rajasthan, 321602")


def test_clean_aadhar_back_colon_comma_split_and_full_address():
    """Colon becomes comma so clauses split; duplicate PIN and state→PIN junk removed."""
    raw = (
        "W/O Rakesh, 9AI: Bani, Bhatawali, Bharatpur, Kumher, Rajasthan, mage, "
        "3\u02b3\u1d48\u02b3, 321602 321602"
    )
    got = _clean_aadhar_back_cross_column_noise(raw)
    assert ":" not in got
    # ``9AI`` may be dropped as short all-caps OCR noise; locality chain should survive.
    assert "Bani" in got
    assert "Bhatawali" in got
    assert "W/O Rakesh" in got
    assert got.count("321602") == 1
    assert got.rstrip().endswith("Rajasthan, 321602")


def test_clean_dedupes_comma_separated_duplicate_pin():
    raw = "Line one, Rajasthan, 321602, 321602"
    got = _clean_aadhar_back_cross_column_noise(raw)
    assert got.count("321602") == 1


def test_resolve_state_typo_and_two_letter():
    assert resolve_indian_state_name("Rajashan", allow_la_ladakh=True) == "Rajasthan"
    assert resolve_indian_state_name("RJ", allow_la_ladakh=False) == "Rajasthan"
    assert resolve_indian_state_name("hr", allow_la_ladakh=False) == "Haryana"
    assert resolve_indian_state_name("Raj.", allow_la_ladakh=True) == "Rajasthan"


def test_resolve_la_ladakh_only_when_allowed():
    assert resolve_indian_state_name("LA", allow_la_ladakh=False) is None
    assert resolve_indian_state_name("LA", allow_la_ladakh=True) == "Ladakh"


def test_normalize_freeform_strips_gen_male_and_fixes_state():
    raw = (
        "S/O Aur Singh, dhanota, Diabita, gen/ MALE DIST, Bharatpur, Rajashan- 321303"
    )
    got = normalize_address_freeform(raw)
    assert got.get("state") == "Rajasthan"
    assert got.get("pin") == "321303"
    addr = (got.get("address") or "").lower()
    assert "gen/" not in addr
    assert "male" not in addr


def test_enrich_fixes_untrustworthy_state_field():
    out = enrich_customer_address_from_freeform(
        {"address": "Bharatpur, RJ - 321303", "state": "Rajashan"}
    )
    assert out.get("state") == "Rajasthan"


def test_strip_junk_misread_state_before_pin():
    raw = "W/O Rakesh, Bani, Kumher, Bharatpur, Rajashan, mage, 321602"
    got = strip_junk_between_last_indian_state_and_pin(raw)
    assert "mage" not in got
    assert "321602" in got
    assert "Rajasthan" in got


def test_dedupe_repeated_relation_clause_in_freeform():
    raw = "S/O Same Name, Village X, S/O Same Name, Village Y, RJ - 302001"
    got = normalize_address_freeform(raw)
    addr = (got.get("address") or "").lower()
    assert got.get("care_of")
    assert "s/o same name" not in addr


def test_strip_leading_care_of_ignores_s_o_vs_so_casing():
    assert (
        _strip_leading_care_of_duplicate_from_work(
            "S/o Aur Singh, dhanota, Bharatpur",
            "S/O Aur Singh",
        )
        == "dhanota, Bharatpur"
    )


def test_normalize_address_no_duplicate_care_of_in_composed_line():
    raw = (
        "S/o Aur Singh, dhanota, Diabita, DIST, Bharatpur, RJ - 321303"
    )
    got = normalize_address_freeform(raw)
    assert got.get("care_of") == "S/O Aur Singh"
    addr = (got.get("address") or "").lower()
    assert not addr.startswith("s/o ")
    assert "aur singh" not in addr


def test_validate_operator_freeform_address_rejects_pin_only_garbage():
    assert validate_operator_freeform_address("sss 123456") is not None
    assert validate_operator_freeform_address("sss, 123456") is not None


def test_validate_operator_freeform_address_accepts_canonical_tail():
    assert validate_operator_freeform_address("house, Bharatpur, Rajasthan, 321001") is None
    assert validate_operator_freeform_address("house, Bharatpur, Rajashan, 321001") is None


def test_validate_operator_freeform_address_accepts_dash_before_pin():
    assert validate_operator_freeform_address("house, Bharatpur, Rajasthan - 321001") is None
    assert (
        validate_operator_freeform_address(
            "Bharatpur, Rajasthan - 321001", min_comma_segments=2
        )
        is None
    )


def test_normalize_operator_address_title_case_and_canonical_state():
    got = normalize_operator_freeform_address("Locality, bharatpur, RJ - 321001")
    assert got is not None
    assert got["address"] == "Locality, Bharatpur, Rajasthan - 321001"
    assert got["city"] == "Bharatpur"
    assert got["state"] == "Rajasthan"
    assert got["pin"] == "321001"


def test_validate_operator_freeform_address_rejects_unknown_state():
    err = validate_operator_freeform_address("house, Bharatpur, Foobaristan, 321001")
    assert err is not None
    assert "Foobaristan" in err
