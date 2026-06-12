"""Insurer field bleed from financier OCR and portal-only canonical rules."""

from app.services.sales_ocr_service import _apply_initcap_on_read
from app.services.utility_functions import (
    insurer_looks_like_financier,
    sanitize_details_sheet_insurer_value,
)

_SAMPLE_FINANCERS = [
    "HDFC Bank",
    "Bajaj Auto Finance",
    "Bajaj Finance",
    "Shriram Finance Ltd.",
    "Shriram Finance Limited",
]

_PORTAL_INSURERS = [
    "National Insurance Co. Ltd.",
    "The New India Assurance Co. Ltd.",
    "BAJAJ GENERAL INSURANCE LIMITED",
]

_ALL_INSURERS = _PORTAL_INSURERS + [
    "HDFC ERGO General Insurance",
    "Shriram General Insurance",
]


def test_dajaj_finate_rejected_as_financier_bleed():
    assert (
        sanitize_details_sheet_insurer_value(
            "Dajaj Finate", financier_candidates=_SAMPLE_FINANCERS
        )
        is None
    )


def test_insurer_looks_like_financier_for_ocr_garbage():
    assert insurer_looks_like_financier("Dajaj Finate", _SAMPLE_FINANCERS)


def test_valid_portal_insurer_not_financier_bleed():
    val = sanitize_details_sheet_insurer_value(
        "The New India Assurance Co. Ltd.", financier_candidates=_SAMPLE_FINANCERS
    )
    assert val is not None
    assert val.startswith("The New India Assurance Co. Ltd")


def test_apply_initcap_drops_non_portal_insurer():
    data = {"insurance": {"insurer": "HDFC ERGO General Insurance"}}
    _apply_initcap_on_read(
        data,
        master_insurers=_ALL_INSURERS,
        master_financers=_SAMPLE_FINANCERS,
        portal_insurers=_PORTAL_INSURERS,
    )
    assert "insurer" not in data["insurance"]


def test_apply_initcap_keeps_portal_insurer():
    data = {"insurance": {"insurer": "The New India Assurance Co. Ltd."}}
    _apply_initcap_on_read(
        data,
        master_insurers=_ALL_INSURERS,
        master_financers=_SAMPLE_FINANCERS,
        portal_insurers=_PORTAL_INSURERS,
    )
    assert data["insurance"]["insurer"] == "The New India Assurance Co. Ltd."


def test_apply_initcap_drops_financier_bleed_insurer():
    data = {"insurance": {"insurer": "Dajaj Finate"}}
    _apply_initcap_on_read(
        data,
        master_insurers=_ALL_INSURERS,
        master_financers=_SAMPLE_FINANCERS,
        portal_insurers=_PORTAL_INSURERS,
    )
    assert "insurer" not in data["insurance"]


def test_apply_initcap_canonicalizes_financier():
    data = {"insurance": {"financier": "Shriram Finance Ltd."}}
    _apply_initcap_on_read(
        data,
        master_insurers=_ALL_INSURERS,
        master_financers=_SAMPLE_FINANCERS,
        portal_insurers=_PORTAL_INSURERS,
    )
    assert data["insurance"]["financier"] == "Shriram Finance Ltd."
