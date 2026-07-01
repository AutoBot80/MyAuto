"""Tests for insurance add-on preset resolution (prefer_insurer driver)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.repositories.insurance_addon_ref import (
    build_addon_flags_from_preset,
    resolve_effective_insurance_addon_row,
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

    def execute_side_effect(sql, params=None):
        q = " ".join(str(sql).split())
        if "SELECT prefer_insurer" in q:
            cur.fetchone.return_value = {"prefer_insurer": "BAJAJ GENERAL INSURANCE LIMITED"}
        elif "FROM add_sales_staging" in q and "insurance_addon" in q:
            cur.fetchone.return_value = {"insurance_addon": 2}
        elif "FROM dealer_ref" in q and "insurance_addon" in q:
            cur.fetchone.return_value = {"insurance_addon": 1}
        elif "insurance_addon_id = %s AND insurer = %s" in q and params == (2, "BAJAJ GENERAL INSURANCE LIMITED"):
            cur.fetchone.return_value = {
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
        return None

    cur.execute.side_effect = execute_side_effect

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

    row = resolve_effective_insurance_addon_row(
        staging_id="00000000-0000-0000-0000-000000000001",
        dealer_id=100001,
        conn=conn,
    )
    assert row is not None
    assert row["insurance_addon_id"] == 1
