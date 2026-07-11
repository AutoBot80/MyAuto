"""My Orders jqGrid mobile readback helpers (invoiced-row pick)."""

from datetime import date

from app.services.hero_dms_playwright_invoice import (
    _classify_my_orders_grid_rows,
    _my_orders_has_invoiced_guard_retry_candidate,
    _my_orders_has_invoiced_mobile_false_positive,
    _my_orders_invoice_date_within_last_days,
    _my_orders_norm_first_name_key,
    _my_orders_normalize_mobile_digits,
    _my_orders_parse_invoice_date,
    _my_orders_row_contact_first_name,
    _my_orders_row_eligible_invoiced_candidate,
    _my_orders_row_invoice_date,
    _my_orders_row_matches_expected_first_name,
    _my_orders_row_matches_searched_mobile,
    _my_orders_row_mobile_digits,
)


def test_row_mobile_digits_from_cell() -> None:
    row = {"mobile": "9414687819", "invoice": "INV-1", "order": "ORD-1"}
    assert _my_orders_row_mobile_digits(row) == "9414687819"


def test_row_contact_first_name_and_invoice_date() -> None:
    row = {
        "contact_first_name": "SHRUTI",
        "invoice_date": "02/07/2026 02:15:12 PM",
        "invoice": "INV-1",
    }
    assert _my_orders_row_contact_first_name(row) == "SHRUTI"
    assert _my_orders_row_invoice_date(row) == "02/07/2026 02:15:12 PM"


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


def test_norm_first_name_and_match() -> None:
    assert _my_orders_norm_first_name_key("  SHRUTI  ") == "shruti"
    row = {"contact_first_name": "SHRUTI"}
    assert _my_orders_row_matches_expected_first_name(row, "shruti") is True
    assert _my_orders_row_matches_expected_first_name(row, "HEMANT") is False
    assert _my_orders_row_matches_expected_first_name(row, "") is True


def test_parse_invoice_date_am_pm() -> None:
    dt = _my_orders_parse_invoice_date("02/07/2026 02:15:12 PM")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 2


def test_invoice_date_within_last_seven_days() -> None:
    as_of = date(2026, 7, 2)
    assert _my_orders_invoice_date_within_last_days(
        "02/07/2026 02:15:12 PM", as_of=as_of
    ) is True
    assert _my_orders_invoice_date_within_last_days(
        "26/06/2026 02:15:12 PM", as_of=as_of
    ) is True
    assert _my_orders_invoice_date_within_last_days(
        "25/06/2026 02:15:12 PM", as_of=as_of
    ) is False
    assert _my_orders_invoice_date_within_last_days(
        "09/08/2025 02:06:16 PM", as_of=as_of
    ) is False


def test_row_eligible_invoiced_candidate_shruti_not_hemant() -> None:
    as_of = date(2026, 7, 2)
    shruti = {
        "order": "ORD-NEW",
        "invoice": "INV-NEW",
        "contact_first_name": "SHRUTI",
        "invoice_date": "02/07/2026 02:15:12 PM",
    }
    hemant = {
        "order": "ORD-OLD",
        "invoice": "INV-OLD",
        "contact_first_name": "HEMANT",
        "invoice_date": "09/08/2025 02:06:16 PM",
    }
    assert _my_orders_row_eligible_invoiced_candidate(
        shruti, expected_first_name="SHRUTI", as_of=as_of
    ) is True
    assert _my_orders_row_eligible_invoiced_candidate(
        hemant, expected_first_name="SHRUTI", as_of=as_of
    ) is False


def test_has_invoiced_guard_retry_candidate() -> None:
    as_of = date(2026, 7, 2)
    rows = [
        {
            "invoice": "INV-OLD",
            "contact_first_name": "HEMANT",
            "invoice_date": "09/08/2025 02:06:16 PM",
        },
        {
            "invoice": "INV-NEW",
            "contact_first_name": "SHRUTI",
            "invoice_date": "02/07/2026 02:15:12 PM",
        },
    ]
    assert _my_orders_has_invoiced_guard_retry_candidate(rows, "SHRUTI", as_of=as_of) is False
    assert _my_orders_has_invoiced_guard_retry_candidate(rows, "OTHER", as_of=as_of) is True


def test_has_invoiced_mobile_false_positive() -> None:
    rows = [
        {"invoice": "INV-1", "mobile": "9999999999", "order": "ORD-1"},
        {"invoice": "", "mobile": "7878953390", "order": "ORD-2", "status": "Allocated"},
    ]
    assert _my_orders_has_invoiced_mobile_false_positive(rows, "7878953390") is True
    assert _my_orders_has_invoiced_mobile_false_positive(rows, "9999999999") is False


def test_classify_invoiced_returns_first_row_without_guards() -> None:
    rows = [
        {
            "order": "ORD-OLD",
            "invoice": "INV-OLD",
            "mobile": "9999999999",
            "contact_first_name": "HEMANT",
            "invoice_date": "09/08/2025 02:06:16 PM",
            "status": "Invoiced",
        },
        {
            "order": "ORD-NEW",
            "invoice": "INV-NEW",
            "mobile": "9414687819",
            "contact_first_name": "SHRUTI",
            "invoice_date": "02/07/2026 02:15:12 PM",
            "status": "Invoiced",
        },
    ]
    oc, po, pi, pm, pfn, pidt = _classify_my_orders_grid_rows(rows)
    assert oc == "invoiced"
    assert po == "ORD-OLD"
    assert pi == "INV-OLD"
    assert pm == "9999999999"
    assert pfn == "HEMANT"
    assert pidt == "09/08/2025 02:06:16 PM"


def test_classify_picks_shruti_by_name_and_date_when_hemant_first() -> None:
    as_of = date(2026, 7, 2)
    rows = [
        {
            "order": "ORD-OLD",
            "invoice": "INV-OLD",
            "mobile": "9351244099",
            "contact_first_name": "HEMANT",
            "invoice_date": "09/08/2025 02:06:16 PM",
            "status": "Invoiced",
        },
        {
            "order": "ORD-NEW",
            "invoice": "INV-NEW",
            "mobile": "9351244099",
            "contact_first_name": "SHRUTI",
            "invoice_date": "02/07/2026 02:15:12 PM",
            "status": "Invoiced",
        },
    ]
    oc, po, pi, pm, pfn, pidt = _classify_my_orders_grid_rows(
        rows,
        searched_mobile_digits="9351244099",
        expected_contact_first_name="SHRUTI",
        as_of=as_of,
    )
    assert oc == "invoiced"
    assert po == "ORD-NEW"
    assert pi == "INV-NEW"
    assert pfn == "SHRUTI"
    assert pidt == "02/07/2026 02:15:12 PM"


def test_classify_name_guard_skips_stale_invoiced_falls_through_allocated() -> None:
    rows = [
        {
            "order": "ORD-1",
            "invoice": "INV-STALE",
            "mobile": "9351244099",
            "contact_first_name": "HEMANT",
            "invoice_date": "09/08/2025 02:06:16 PM",
            "status": "Invoiced",
        },
        {
            "order": "ORD-2",
            "invoice": "",
            "mobile": "9351244099",
            "status": "Allocated",
        },
    ]
    oc, po, pi, pm, pfn, pidt = _classify_my_orders_grid_rows(
        rows,
        searched_mobile_digits="9351244099",
        expected_contact_first_name="SHRUTI",
    )
    assert oc == "allocated"
    assert po == "ORD-2"
    assert pi == ""


def test_classify_skips_invoiced_wrong_mobile_legacy() -> None:
    rows = [
        {
            "order": "ORD-OLD",
            "invoice": "INV-OLD",
            "mobile": "9999999999",
            "contact_first_name": "HEMANT",
            "status": "Invoiced",
        },
        {
            "order": "ORD-NEW",
            "invoice": "INV-NEW",
            "mobile": "9414687819",
            "contact_first_name": "SHRUTI",
            "status": "Invoiced",
        },
    ]
    oc, po, pi, pm, pfn, pidt = _classify_my_orders_grid_rows(rows, searched_mobile_digits="9414687819")
    assert oc == "invoiced"
    assert po == "ORD-NEW"
    assert pi == "INV-NEW"
    assert pm == "9414687819"
    assert pfn == "SHRUTI"


def test_classify_allocated_no_primary_mobile() -> None:
    rows = [{"order": "ORD-1", "invoice": "", "mobile": "9414687819", "status": "Allocated"}]
    oc, po, pi, pm, pfn, pidt = _classify_my_orders_grid_rows(rows)
    assert oc == "allocated"
    assert pm == ""
