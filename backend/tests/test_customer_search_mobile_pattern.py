"""Mobile wildcard / prefix patterns for View Customer search."""

from app.routers.customer_search import _mobile_exact_int, _mobile_ilike_pattern


def test_mobile_ilike_pattern_nine_star():
    assert _mobile_ilike_pattern("9*") == "9%"


def test_mobile_ilike_pattern_full_ten_digit_uses_exact():
    assert _mobile_ilike_pattern("9876543210") is None
    assert _mobile_exact_int("9876543210") == 9876543210


def test_mobile_ilike_pattern_partial_prefix():
    assert _mobile_ilike_pattern("98765") == "98765%"


def test_mobile_ilike_pattern_no_digits():
    assert _mobile_ilike_pattern("***") is None
