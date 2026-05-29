"""Unit tests for final policy page (PrintPolicy.aspx) helpers."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.fill_hero_insurance_service import (
    _hero_misp_on_final_policy_cert_page,
    _normalize_policy_num_for_db,
    _policy_num_from_print_policy_cert_body,
)


class TestNormalizePolicyNumForDb:
    def test_bajaj_slashy_id_fits_varchar_24(self) -> None:
        raw = "MD/BAGIC/29052026/01008141"
        out = _normalize_policy_num_for_db(raw)
        assert out is not None
        assert len(out) <= 24
        assert out == "BAGIC/29052026/01008141"

    def test_numeric_id_unchanged_when_short(self) -> None:
        assert _normalize_policy_num_for_db("9000123456789012345678") == "9000123456789012345678"


class TestPolicyNumFromPrintPolicyCertBody:
    def test_bajaj_policy_type_column(self) -> None:
        body = (
            "Print Policy Certificates\n"
            "BAJAJ GENERAL INSURANCE LIMITED #MD/BAGIC/29052026/01008141\n"
            "Premium Amount 4945"
        )
        pn = _policy_num_from_print_policy_cert_body(body)
        assert pn == "BAGIC/29052026/01008141"

    def test_numeric_hash_policy(self) -> None:
        body = "Some Insurer #9000123456789012 Premium 1000"
        pn = _policy_num_from_print_policy_cert_body(body)
        assert pn == "9000123456789012"


class TestFinalPolicyCertPageDetection:
    def test_printpolicy_aspx_is_final_cert_page(self) -> None:
        page = SimpleNamespace(
            url="https://misp.heroinsurance.com/prod/apps/V1/2W/Policy/PrintPolicy.aspx?PID=abc&Mob="
        )
        assert _hero_misp_on_final_policy_cert_page(page) is True

    def test_printpolicydetails_is_not_cert_page(self) -> None:
        page = SimpleNamespace(
            url="https://misp.heroinsurance.com/prod/apps/V1/2W/Policy/PrintPolicyDetails.aspx"
        )
        assert _hero_misp_on_final_policy_cert_page(page) is False

    def test_allprintpolicy_is_not_cert_page(self) -> None:
        page = SimpleNamespace(
            url="https://misp.heroinsurance.com/prod/apps/V1/2W/Policy/AllPrintPolicy.aspx"
        )
        assert _hero_misp_on_final_policy_cert_page(page) is False
