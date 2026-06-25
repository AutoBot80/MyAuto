"""
Canonical Hero Connect / Siebel URLs and tuning for real DMS automation.

These are static for Hero DMS; configure overrides in code here rather than duplicating
long URLs in backend/.env. Optional env vars in ``app.config`` may still override
some values for tests or non-standard deployments.
"""

# Login / shell entry (Hero Connect edealerHMCL).
HERO_DMS_BASE_URL = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=Login&SWECM=S&SWEHo=connect.heromotocorp.biz"
)

HERO_DMS_REAL_URL_CONTACT = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=GotoView&SWEView=eAuto+Contact+Opportunity+Buyer/CoBuyer+View+(SDW)"
    "&SWERF=1&SWEHo=&SWEBU=1"
)

HERO_DMS_REAL_URL_VEHICLE = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=GotoView&SWEView=eAuto+All+Vehicle+View&SWERF=1&SWEHo=&SWEBU=1"
    "&SWEApplet0=Auto+Vehicle+List+Applet&SWERowId0=1-100-1676"
)
# In-transit receipt: Vehicles Receipt / HMCL - In Transit first+second level view bars (no separate GotoView).

HERO_DMS_REAL_URL_PDI = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=GotoView&SWEView=HMCL+Auto+Vehicle+PDIPre+Assessment+View&SWERF=1"
    "&SWEHo=&SWEBU=1&SWEApplet0=Auto+Vehicle+Entry+Applet&SWERowId0=1-100-1676"
    "&SWEApplet1=HMCL+PDI+Precheck+List+Applet&SWERowId1=2-WRJ1PFP"
)

HERO_DMS_REAL_URL_ENQUIRY = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=GotoView&SWEView=eAuto+Contact+Opportunity+Buyer/CoBuyer+View+(SDW)"
    "&SWERF=1&SWEHo=&SWEBU=1"
)

HERO_DMS_REAL_URL_LINE_ITEMS = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=Login&SWEPL=1&SRN=&SWETS=1774205157677"
)

HERO_DMS_REAL_URL_REPORTS = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=GotoView&SWEView=eAuto+All+Vehicle+View&SWERF=1&SWEHo=&SWEBU=1"
    "&SWEApplet0=Auto+Vehicle+List+Applet&SWERowId0=1-100-1676"
)

# Extra ms after Siebel navigation before other waits (stability for heavy applets).
HERO_DMS_SIEBEL_INTER_ACTION_DELAY_MS = 200

HMCL_SIEBEL_APP = "edealerHMCL"
ASC_SIEBEL_APP = "edealerasc"


def siebel_app_folder_for_portal(portal: str | None) -> str:
    """``ASC`` → edealerasc; ``HMCL`` / NULL / empty → edealerHMCL."""
    if (portal or "").strip().upper() == "ASC":
        return ASC_SIEBEL_APP
    return HMCL_SIEBEL_APP


def _substitute_siebel_app(url: str, app_folder: str) -> str:
    """Replace HMCL/ASC app folder segment in a canonical Hero Connect URL."""
    out = (url or "").replace(HMCL_SIEBEL_APP, app_folder)
    if app_folder == HMCL_SIEBEL_APP:
        out = out.replace(ASC_SIEBEL_APP, HMCL_SIEBEL_APP)
    elif app_folder == ASC_SIEBEL_APP:
        out = out.replace(HMCL_SIEBEL_APP, ASC_SIEBEL_APP)
    return out


def hero_dms_short_entry_url_for_portal(portal: str | None) -> str:
    app = siebel_app_folder_for_portal(portal)
    if app == ASC_SIEBEL_APP:
        return "https://connect.heromotocorp.biz/edealerasc_enu"
    return "https://connect.heromotocorp.biz/edealerHMCL_enu"


def hero_dms_urls_for_portal(portal: str | None) -> tuple[str, "SiebelDmsUrls"]:
    """
  Build DMS base URL and GotoView URLs for a dealer portal (``dealer_ref.dms_siebel_portal``).

  Returns ``(dms_base_url, SiebelDmsUrls)`` matching fill DMS usage.
  """
    from app.services.hero_dms_shared_utilities import SiebelDmsUrls

    app = siebel_app_folder_for_portal(portal)
    base = _substitute_siebel_app(HERO_DMS_BASE_URL, app).strip().rstrip("/")
    urls = SiebelDmsUrls(
        contact=_substitute_siebel_app(HERO_DMS_REAL_URL_CONTACT, app),
        vehicles="",
        precheck="",
        pdi=_substitute_siebel_app(HERO_DMS_REAL_URL_PDI, app),
        vehicle=_substitute_siebel_app(HERO_DMS_REAL_URL_VEHICLE, app),
        enquiry="",
        line_items="",
        reports="",
    )
    return base, urls
