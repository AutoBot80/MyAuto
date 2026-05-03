import { getAccessToken } from "../auth/token";
import { getBaseUrl } from "./client";

/**
 * POST /system/teardown-local-browsers — best-effort cleanup of the dev FastAPI server's
 * managed Chromium / cached Playwright handles. Used by the SPA on tab close (``pagehide``)
 * and at the start of Subdealer Challan Retry so a previously-killed Edge does not block
 * the next Playwright launch.
 *
 * Errors are swallowed: the next click should not surface a teardown failure to the user
 * (and on production cloud API there is no managed Chromium, so the call is effectively a no-op).
 *
 * ``keepalive: true`` is required for the ``pagehide`` path so the request still flushes
 * after the tab is gone. ``navigator.sendBeacon`` would not let us attach the JWT header.
 */
export async function teardownLocalBrowsers(
  opts: { keepalive?: boolean } = {},
): Promise<void> {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    await fetch(`${getBaseUrl()}/system/teardown-local-browsers`, {
      method: "POST",
      headers,
      body: "{}",
      keepalive: opts.keepalive ?? false,
    });
  } catch {
    /* best-effort: swallow network errors so tab-close / Retry never throws on cleanup */
  }
}
