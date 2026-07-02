"""My Orders jqGrid mobile readback helpers (invoiced-row pick)."""

from app.services.hero_dms_playwright_invoice import (
    _classify_my_orders_grid_rows,
    _my_orders_has_invoiced_mobile_false_positive,
    _my_orders_normalize_mobile_digits,
    _my_orders_row_matches_searched_mobile,
    _my_orders_row_mobile_digits,
)


def test_row_mobile_digits_from_cell() -> None:
    row = {"mobile": "9414687819", "invoice": "INV-1", "order": "ORD-1"}
    assert _my_orders_row_mobile_digits(row) == "9414687819"


def test_row_mobile_digits_strips_non_digits() -> None:
    row = {"mobile": "+91 94146-87819"}
    assert _my_orders_row_mobile_digits(row) == "9414687819"


def test_row_mobile_digits_empty_when_no_cell() -> None:
    row = {"raw": "9414687819 stale text", "invoice": "INV-1"}
    assert _my_orders_row_mobile_digits(row) == ""


def test_normalize_mobile_last_ten_when_longer() -> None:
    assert _my_orders_normalize_mobile_digits("919414687819") == "9414687819"


def test_row_matches_searched_mobile() -> None:
    row = {"mobile": "7878953390", "invoice": "INV-1"}
    assert _my_orders_row_matches_searched_mobile(row, "7878953390") is True
    assert _my_orders_row_matches_searched_mobile(row, "9999999999") is False
    assert _my_orders_row_matches_searched_mobile(row, "") is True


def test_has_invoiced_mobile_false_positive() -> None:
    rows = [
        {"invoice": "INV-1", "mobile": "9999999999", "order": "ORD-1"},
        {"invoice": "", "mobile": "7878953390", "order": "ORD-2", "status": "Allocated"},
    ]
    assert _my_orders_has_invoiced_mobile_false_positive(rows, "7878953390") is True
    assert _my_orders_has_invoiced_mobile_false_positive(rows, "9999999999") is False


def test_classify_invoiced_returns_first_row_without_mobile_guard() -> None:
    rows = [
        {
            "order": "ORD-OLD",
            "invoice": "INV-OLD",
            "mobile": "9999999999",
            "status": "Invoiced",
        },
        {
            "order": "ORD-NEW",
            "invoice": "INV-NEW",
            "mobile": "9414687819",
            "status": "Invoiced",
        },
    ]
    oc, po, pi, pm = _classify_my_orders_grid_rows(rows)
    assert oc == "invoiced"
    assert po == "ORD-OLD"
    assert pi == "INV-OLD"
    assert pm == "9999999999"


def test_classify_skips_invoiced_wrong_mobile() -> None:
    rows = [
        {
            "order": "ORD-OLD",
            "invoice": "INV-OLD",
            "mobile": "9999999999",
            "status": "Invoiced",
        },
        {
            "order": "ORD-NEW",
            "invoice": "INV-NEW",
            "mobile": "9414687819",
            "status": "Invoiced",
        },
    ]
    oc, po, pi, pm = _classify_my_orders_grid_rows(rows, searched_mobile_digits="9414687819")
    assert oc == "invoiced"
    assert po == "ORD-NEW"
    assert pi == "INV-NEW"
    assert pm == "9414687819"


def test_classify_skips_only_stale_invoiced_falls_through_allocated() -> None:
    rows = [
        {
            "order": "ORD-1",
            "invoice": "INV-STALE",
            "mobile": "9999999999",
            "status": "Invoiced",
        },
        {
            "order": "ORD-2",
            "invoice": "",
            "mobile": "9414687819",
            "status": "Allocated",
        },
    ]
    oc, po, pi, pm = _classify_my_orders_grid_rows(rows, searched_mobile_digits="9414687819")
    assert oc == "allocated"
    assert po == "ORD-2"
    assert pi == ""
    assert pm == ""


def test_classify_allocated_no_primary_mobile() -> None:
    rows = [{"order": "ORD-1", "invoice": "", "mobile": "9414687819", "status": "Allocated"}]
    oc, po, pi, pm = _classify_my_orders_grid_rows(rows)
    assert oc == "allocated"
    assert pm == ""
