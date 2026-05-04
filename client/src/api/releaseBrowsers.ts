import { isElectron } from "../electron";
import { teardownLocalBrowsers } from "./system";

const WORKER_STILL_BUSY_HINT =
  "The managed browser port was cleared, but the API could not finish Playwright cleanup — another request is probably still stuck on the single Playwright worker thread. In the terminal where uvicorn runs: press Ctrl+C, start the API again (for example `python -m uvicorn app.main:app --reload --port 8000` from the `backend` folder), refresh this page, then use Retry.";

/**
 * Best-effort: clear Playwright/CDP state after the automation browser was force-closed or the
 * sidecar queue wedged.
 *
 * - **Electron (dealer prod or dev loading Vite):** `sidecar.releaseBrowsers` — local hard reset
 *   (always the meaningful path for Playwright on the PC). Also POSTs `/system/teardown-local-browsers`
 *   when a co-located API exists.
 * - **Browser-only dev (`localhost:5173`):** POSTs teardown via the Vite proxy (`/system` → :8000).
 *   If ``playwright_disconnect_ok`` is false, Retry can stay stuck until you **restart uvicorn**.
 */
export async function releaseAutomationBrowsers(): Promise<{ ok: boolean; detail?: string }> {
  const sidecar = isElectron() ? window.electronAPI?.sidecar : undefined;
  if (sidecar?.releaseBrowsers) {
    const r = await sidecar.releaseBrowsers();
    const td = await teardownLocalBrowsers();
    if (r.timedOut) {
      return { ok: false, detail: "Release timed out. Try again or restart the app." };
    }
    if (!r.success) {
      return { ok: false, detail: r.error || r.stderr || "Release did not complete cleanly." };
    }
    if (td?.playwright_disconnect_ok === false) {
      return { ok: true, detail: WORKER_STILL_BUSY_HINT };
    }
    return { ok: true };
  }
  const td = await teardownLocalBrowsers();
  if (td?.playwright_disconnect_ok === false) {
    return { ok: true, detail: WORKER_STILL_BUSY_HINT };
  }
  return { ok: true };
}
