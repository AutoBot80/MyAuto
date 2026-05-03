"""System-level operational endpoints used by the SPA (e.g. teardown of local Playwright browsers).

The dev SPA at ``localhost:5173`` calls this on tab close (``pagehide``) and at the start of
Subdealer Challan Retry, so a previously-killed Edge/Chrome and a wedged debug-port owner do not
block the next Playwright launch. In Electron production builds, equivalent teardown already runs
at app quit via the sidecar ``teardown_local_browsers`` job, so this endpoint is a near no-op there.
"""

from fastapi import APIRouter, Depends

from app.security.deps import get_principal
from app.security.principal import Principal
from app.services.handle_browser_opening import teardown_local_automation_browsers

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/teardown-local-browsers")
def teardown_local_browsers_endpoint(
    _principal: Principal = Depends(get_principal),
) -> dict:
    """
    Kill the managed Chromium debug-port process and disconnect cached Playwright handles.

    Idempotent and safe to call repeatedly. The OS-level port kill is issued first, so even a
    wedged Playwright executor cannot prevent cleanup. On a server with no managed Chromium
    running (e.g. cloud EC2), this is effectively a no-op aside from clearing in-memory caches.
    """
    return teardown_local_automation_browsers()
