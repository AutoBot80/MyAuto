"""Admin Saathi dealer scope via admin_dealer_access_ref."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from app.main import app

    return TestClient(app)


def test_list_dealers_returns_only_scoped_rows(admin_client: TestClient) -> None:
    scoped = [
        {"dealer_id": 100001, "dealer_name": "Arya Agencies"},
        {"dealer_id": 100003, "dealer_name": "Test Dealer 100003"},
    ]
    with patch(
        "app.repositories.admin_dealer_access.list_dealers_for_admin_login",
        return_value=scoped,
    ):
        r = admin_client.get("/admin/dealers")
    assert r.status_code == 200
    body = r.json()
    assert [row["dealer_id"] for row in body] == [100001, 100003]


def test_usage_matrix_sales_only_dealers_with_activity(admin_client: TestClient) -> None:
    scoped_ids = [100001, 100003]
    names = {100001: "Arya Agencies", 100003: "Test Dealer 100003"}
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=scoped_ids,
    ), patch(
        "app.repositories.admin_dealer_access.dealer_names_for_ids",
        return_value=names,
    ), patch(
        "app.repositories.admin_dealer_access.parent_dealer_ids_in_scope",
        return_value=[100001],
    ), patch("app.routers.admin.get_connection") as mock_conn:
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.return_value = {"has_created_at": True}
        mock_cur.fetchall.side_effect = [
            [{"dealer_id": 100001, "dealer_name": "Arya Agencies", "bucket": "2026-06-12", "cnt": 2}],
            [{"dealer_id": 100001, "dealer_name": "Arya Agencies", "bucket": "2026-06-12", "cnt": 1}],
        ]
        r = admin_client.get("/admin/usage-dealer-matrix")
    assert r.status_code == 200
    body = r.json()
    sales_ids = [row["dealer_id"] for row in body["sales"]]
    assert sales_ids == [100001]
    assert 100003 not in sales_ids
    chall_ids = [row["dealer_id"] for row in body["challans"]]
    assert chall_ids == [100001]
    assert 100003 not in chall_ids


def test_get_dealer_detail_denied_out_of_scope(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001],
    ):
        r = admin_client.get("/admin/dealers/100003")
    assert r.status_code == 403


def test_get_dealer_detail_allowed_in_scope(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100001, 100003],
    ), patch("app.routers.admin.get_connection") as mock_conn:
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.return_value = {
            "dealer_name": "Test Dealer 100003",
            "parent_id": 100000,
            "parent_name": "Hero Motors",
            "oem_name": "Hero Motors",
            "prefer_insurer": None,
            "hero_cpi": "N",
        }
        r = admin_client.get("/admin/dealers/100003")
    assert r.status_code == 200
    assert r.json()["dealer_name"] == "Test Dealer 100003"


def test_list_portal_insurers_for_admin(admin_client: TestClient) -> None:
    portal = ["National Insurance Co. Ltd.", "BAJAJ GENERAL INSURANCE LIMITED"]
    with patch("app.routers.admin.list_portal_insurers", return_value=portal), patch(
        "app.routers.admin.get_connection"
    ):
        r = admin_client.get("/admin/portal-insurers")
    assert r.status_code == 200
    assert r.json()["insurers"] == portal


def test_patch_prefer_insurer_rejects_non_portal_label(admin_client: TestClient) -> None:
    portal = ["National Insurance Co. Ltd."]
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100003],
    ), patch("app.routers.admin.list_portal_insurers", return_value=portal), patch(
        "app.routers.admin.get_connection"
    ) as mock_conn:
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.return_value = (1,)
        r = admin_client.patch(
            "/admin/dealers/100003",
            json={
                "prefer_insurer": "National Insurance Co. Ltd",
                "hero_cpi": "N",
                "cpi_reqd": "N",
                "insurance_pay": "APD",
                "dms_siebel_portal": "HMCL",
                "insurance_addon": None,
            },
        )
    assert r.status_code == 400


def test_patch_dms_siebel_portal_round_trip(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100003],
    ), patch("app.routers.admin.list_portal_insurers", return_value=[]), patch(
        "app.routers.admin.get_connection"
    ) as mock_conn:
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.side_effect = [
            (1,),
            {
                "dealer_name": "Test Dealer 100003",
                "parent_id": None,
                "parent_name": None,
                "oem_name": "Hero Motors",
                "prefer_insurer": None,
                "hero_cpi": "N",
                "cpi_reqd": "N",
                "insurance_pay": "APD",
                "dms_siebel_portal": "ASC",
            },
        ]
        r = admin_client.patch(
            "/admin/dealers/100003",
            json={
                "prefer_insurer": None,
                "hero_cpi": "N",
                "cpi_reqd": "N",
                "insurance_pay": "APD",
                "dms_siebel_portal": "ASC",
                "insurance_addon": None,
            },
        )
    assert r.status_code == 200
    assert r.json()["dms_siebel_portal"] == "ASC"
    update_call = mock_cur.execute.call_args_list[1]
    assert "dms_siebel_portal" in update_call.args[0]
    assert update_call.args[1][4] == "ASC"


def test_patch_dms_siebel_portal_hmcl_stores_null(admin_client: TestClient) -> None:
    with patch(
        "app.repositories.admin_dealer_access.list_dealer_ids_for_admin_login",
        return_value=[100003],
    ), patch("app.routers.admin.list_portal_insurers", return_value=[]), patch(
        "app.routers.admin.get_connection"
    ) as mock_conn:
        mock_cur = mock_conn.return_value.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.side_effect = [
            (1,),
            {
                "dealer_name": "Test Dealer 100003",
                "parent_id": None,
                "parent_name": None,
                "oem_name": "Hero Motors",
                "prefer_insurer": None,
                "hero_cpi": "N",
                "cpi_reqd": "N",
                "insurance_pay": "APD",
                "dms_siebel_portal": None,
            },
        ]
        r = admin_client.patch(
            "/admin/dealers/100003",
            json={
                "prefer_insurer": None,
                "hero_cpi": "N",
                "cpi_reqd": "N",
                "insurance_pay": "APD",
                "dms_siebel_portal": "HMCL",
                "insurance_addon": None,
            },
        )
    assert r.status_code == 200
    update_call = mock_cur.execute.call_args_list[1]
    assert update_call.args[1][4] is None
