"""Tests for insurance add-on preset resolution (prefer_insurer driver)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.repositories.insurance_addon_ref import (
    build_addon_flags_from_preset,
    list_active_by_insurer,
    resolve_addon_catalog_insurer_key,
    resolve_effective_insurance_addon_row,
    validate_dealer_insurance_addon_config,
)


def test_build_addon_flags_from_preset_bajaj_full():
    row = {
        "nd_cover": "Y",
        "rti": "N",
        "rim_safeguard": "Y",
        "rsa": "Y",
    }
    flags = build_addon_flags_from_preset(row)
    assert flags == {
        "nd_cover": True,
        "rti": False,
        "rim_safeguard": True,
        "rsa": True,
    }


def test_resolve_prefers_staging_when_insurer_matches_prefer():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    staging_row = {
        "insurance_addon_id": 2,
        "insurer": "BAJAJ GENERAL INSURANCE LIMITED",
        "display_label": "ND Cover, Rim Safeguard",
        "nd_cover": "Y",
        "rti": "N",
        "rim_safeguard": "Y",
        "rsa": "N",
        "sort_order": 20,
        "active_flag": "Y",
    }

    def execute_side_effect(sql, params=None):
        q = " ".join(str(sql).split())
        if "SELECT prefer_insurer" in q:
            cur.fetchone.return_value = {"prefer_insurer": "BAJAJ GENERAL INSURANCE LIMITED"}
        elif "FROM add_sales_staging" in q and "insurance_addon" in q:
            cur.fetchone.return_value = {"insurance_addon": 2}
        elif "FROM dealer_ref" in q and "insurance_addon" in q:
            cur.fetchone.return_value = {"insurance_addon": 1}
        return None

    cur.execute.side_effect = execute_side_effect

    with patch(
        "app.repositories.insurance_addon_ref.resolve_addon_catalog_insurer_key",
        side_effect=lambda _c, ins: ins,
    ), patch(
        "app.repositories.insurance_addon_ref.get_by_id",
        side_effect=lambda _c, fk: staging_row if fk == 2 else None,
    ):
        row = resolve_effective_insurance_addon_row(
            staging_id="00000000-0000-0000-0000-000000000001",
            dealer_id=100003,
            conn=conn,
        )
    assert row is not None
    assert row["insurance_addon_id"] == 2
    assert row["display_label"] == "ND Cover, Rim Safeguard"
    assert row["rsa"] == "N"


def test_resolve_ignores_staging_when_insurer_mismatch():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    dealer_row = {
        "insurance_addon_id": 1,
        "insurer": "BAJAJ GENERAL INSURANCE LIMITED",
        "display_label": "ND Cover, Rim Safeguard, RSA",
        "nd_cover": "Y",
        "rti": "N",
        "rim_safeguard": "Y",
        "rsa": "Y",
        "sort_order": 10,
        "active_flag": "Y",
    }

    def execute_side_effect(sql, params=None):
        q = " ".join(str(sql).split())
        if "SELECT prefer_insurer" in q:
            cur.fetchone.return_value = {"prefer_insurer": "BAJAJ GENERAL INSURANCE LIMITED"}
        elif "FROM add_sales_staging" in q:
            cur.fetchone.return_value = {"insurance_addon": 99}
        elif "FROM dealer_ref" in q and "insurance_addon" in q:
            cur.fetchone.return_value = {"insurance_addon": 1}
        elif params == (99, "BAJAJ GENERAL INSURANCE LIMITED"):
            cur.fetchone.return_value = None
        elif params == (1, "BAJAJ GENERAL INSURANCE LIMITED"):
            cur.fetchone.return_value = dealer_row
        return None

    cur.execute.side_effect = execute_side_effect

    with patch(
        "app.repositories.insurance_addon_ref.resolve_addon_catalog_insurer_key",
        side_effect=lambda _c, ins: ins,
    ), patch(
        "app.repositories.insurance_addon_ref.get_by_id",
        side_effect=lambda _c, fk: dealer_row if fk == 1 else None,
    ), patch(
        "app.repositories.insurance_addon_ref._list_active_by_insurer_exact",
        return_value=[],
    ):
        row = resolve_effective_insurance_addon_row(
            staging_id="00000000-0000-0000-0000-000000000001",
            dealer_id=100001,
            conn=conn,
        )
    assert row is not None
    assert row["insurance_addon_id"] == 1


def test_validate_reports_missing_presets_for_prefer_insurer():
    conn = MagicMock()
    with (
        patch(
            "app.repositories.insurance_addon_ref.fetch_dealer_prefer_insurer_on_cursor",
            return_value="BAJAJ GENERAL INSURANCE LIMITED",
        ),
        patch(
            "app.repositories.insurance_addon_ref.resolve_addon_catalog_insurer_key",
            side_effect=lambda _c, ins: ins,
        ),
        patch(
            "app.repositories.insurance_addon_ref.list_active_by_insurer",
            return_value=[],
        ),
        patch(
            "app.repositories.insurance_addon_ref.fetch_dealer_insurance_addon_on_cursor",
            return_value=None,
        ),
    ):
        issues = validate_dealer_insurance_addon_config(conn, 100001)
    assert issues
    assert "no active insurance add-on presets" in issues[0]


def test_resolve_addon_catalog_insurer_key_fuzzy_nic():
    conn = MagicMock()
    nic_presets = [
        {
            "insurance_addon_id": 4,
            "insurer": "National Insurance Co. Ltd.",
            "display_label": "ND Plus Cover",
            "nd_cover": "Y",
            "rti": "N",
            "rim_safeguard": "N",
            "rsa": "N",
            "sort_order": 10,
            "active_flag": "Y",
        }
    ]

    def exact_side_effect(_conn, insurer):
        if insurer == "National Insurance Co. Ltd.":
            return nic_presets
        return []

    with patch(
        "app.repositories.insurance_addon_ref._list_active_by_insurer_exact",
        side_effect=exact_side_effect,
    ), patch(
        "app.repositories.master_ref.list_portal_insurers",
        return_value=["National Insurance Co. Ltd.", "BAJAJ GENERAL INSURANCE LIMITED"],
    ), patch(
        "app.services.utility_functions.fuzzy_best_master_ref_value",
        return_value="National Insurance Co. Ltd.",
    ):
        key = resolve_addon_catalog_insurer_key(conn, "National Insurance Company")
    assert key == "National Insurance Co. Ltd."
    with patch(
        "app.repositories.insurance_addon_ref.resolve_addon_catalog_insurer_key",
        return_value="National Insurance Co. Ltd.",
    ), patch(
        "app.repositories.insurance_addon_ref._list_active_by_insurer_exact",
        return_value=nic_presets,
    ):
        rows = list_active_by_insurer(conn, "National Insurance Company")
    assert len(rows) == 1
    assert rows[0]["display_label"] == "ND Plus Cover"
