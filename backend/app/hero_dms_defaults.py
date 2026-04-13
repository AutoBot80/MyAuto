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

HERO_DMS_REAL_URL_VEHICLES = (
    "https://connect.heromotocorp.biz/siebel/app/edealerHMCL/enu/"
    "?SWECmd=Login&SWEPL=1&SRN=&SWETS=1774205157677"
)

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
