"""Hero Connect Siebel portal (HMCL vs ASC) URL substitution."""

from unittest.mock import MagicMock, patch

from app.hero_dms_defaults import (
    ASC_SIEBEL_APP,
    HMCL_SIEBEL_APP,
    hero_dms_short_entry_url_for_portal,
    hero_dms_urls_for_portal,
    portal_from_dms_base_url,
    siebel_app_folder_for_portal,
    siebel_urls_from_resolve_payload,
)
from app.services.fill_hero_dms_service import _resolve_fill_dms_siebel_urls
from app.services.hero_dms_portal_service import (
    dms_siebel_portal_for_dealer,
    hero_dms_base_url_for_dealer,
    hero_dms_siebel_urls_for_dealer,
)


def test_siebel_app_folder_for_portal():
    assert siebel_app_folder_for_portal(None) == HMCL_SIEBEL_APP
    assert siebel_app_folder_for_portal("") == HMCL_SIEBEL_APP
    assert siebel_app_folder_for_portal("HMCL") == HMCL_SIEBEL_APP
    assert siebel_app_folder_for_portal("asc") == ASC_SIEBEL_APP


def test_hero_dms_short_entry_url_for_portal():
    assert "edealerHMCL" in hero_dms_short_entry_url_for_portal(None)
    assert "edealerasc" in hero_dms_short_entry_url_for_portal("ASC")


def test_hero_dms_urls_for_portal_hmcl_default():
    base, urls = hero_dms_urls_for_portal(None)
    assert HMCL_SIEBEL_APP in base
    assert HMCL_SIEBEL_APP in urls.contact
    assert HMCL_SIEBEL_APP in urls.vehicle
    assert ASC_SIEBEL_APP not in urls.contact


def test_hero_dms_urls_for_portal_asc():
    base, urls = hero_dms_urls_for_portal("ASC")
    assert ASC_SIEBEL_APP in base
    assert ASC_SIEBEL_APP in urls.contact
    assert ASC_SIEBEL_APP in urls.vehicle
    assert HMCL_SIEBEL_APP not in urls.contact


@patch("app.services.hero_dms_portal_service.get_connection")
def test_dms_siebel_portal_for_dealer_reads_db(mock_get_conn):
    cur = MagicMock()
    cur.fetchone.return_value = {"dms_siebel_portal": "ASC"}
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    mock_get_conn.return_value = conn

    assert dms_siebel_portal_for_dealer(100003) == "ASC"
    cur.execute.assert_called_once()


@patch("app.services.hero_dms_portal_service.dms_siebel_portal_for_dealer", return_value="ASC")
def test_hero_dms_base_url_for_dealer_uses_portal(mock_portal):
    base = hero_dms_base_url_for_dealer(100003)
    assert ASC_SIEBEL_APP in base
    mock_portal.assert_called_once_with(100003)


@patch("app.services.hero_dms_portal_service.dms_siebel_portal_for_dealer", return_value="ASC")
def test_hero_dms_siebel_urls_for_dealer_uses_portal(mock_portal):
    urls = hero_dms_siebel_urls_for_dealer(100003)
    assert ASC_SIEBEL_APP in urls.contact
    mock_portal.assert_called_once_with(100003)


def test_portal_from_dms_base_url():
    assert portal_from_dms_base_url(
        "https://connect.heromotocorp.biz/siebel/app/edealerasc/enu/?SWECmd=Login"
    ) == "ASC"
    assert portal_from_dms_base_url(
        "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/?SWECmd=Login"
    ) == "HMCL"
    assert portal_from_dms_base_url(None) is None


def test_siebel_urls_from_resolve_payload_explicit_contact():
    urls = siebel_urls_from_resolve_payload(
        {
            "dms_real_url_contact": "https://example.test/edealerasc/contact",
            "dms_real_url_vehicle": "https://example.test/edealerasc/vehicle",
            "dms_real_url_pdi": "https://example.test/edealerasc/pdi",
        }
    )
    assert urls is not None
    assert urls.contact.endswith("/contact")


def test_siebel_urls_from_resolve_payload_infers_from_base_url():
    urls = siebel_urls_from_resolve_payload(
        {
            "dms_base_url": (
                "https://connect.heromotocorp.biz/siebel/app/edealerasc/enu/"
                "?SWECmd=Login&SWECM=S"
            ),
        }
    )
    assert urls is not None
    assert ASC_SIEBEL_APP in urls.contact
    assert ASC_SIEBEL_APP in urls.vehicle


def test_resolve_fill_dms_siebel_urls_uses_override_without_db():
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls

    override = SiebelDmsUrls(
        contact="https://connect.heromotocorp.biz/siebel/app/edealerasc/enu/contact",
        vehicles="",
        precheck="",
        pdi="",
        vehicle="",
        enquiry="",
        line_items="",
        reports="",
    )
    urls = _resolve_fill_dms_siebel_urls(100003, override=override)
    assert urls.contact == override.contact

def test_resolve_fill_dms_siebel_urls_uses_local_base_without_db(monkeypatch):
    monkeypatch.setattr("app.config.DATABASE_URL", "")
    monkeypatch.setattr("app.config.DMS_BASE_URL", "")
    urls = _resolve_fill_dms_siebel_urls(
        100003,
        dms_base_url="https://connect.heromotocorp.biz/siebel/app/edealerasc/enu/?SWECmd=Login",
    )
    assert ASC_SIEBEL_APP in urls.contact
