import { getAccessToken } from "../auth/token";
import { getBaseUrl } from "./client";

/** Parsed body from ``POST /system/teardown-local-browsers`` when the server returns JSON. */
export type TeardownLocalBrowsersResponse = {
  teardown?: boolean;
  managed_debug_port?: number;
  /** False when the API Playwright worker thread could not run disconnect (often still stuck on a prior run). */
  playwright_disconnect_ok?: boolean;
};

/**
 * POST /system/teardown-local-browsers — best-effort cleanup of the dev FastAPI server's
 * managed Chromium / cached Playwright handles. Used by the SPA on tab close (``pagehide``)
 * and at the start of Subdealer Challan Retry so a previously-killed Edge does not block
 * the next Playwright launch.
 *
 * Network errors are swallowed: the next click should not surface a teardown failure to the user
 * (and on production cloud API there is no managed Chromium, so the call is effectively a no-op).
 *
 * ``keepalive: true`` is required for the ``pagehide`` path so the request still flushes
 * after the tab is gone. ``navigator.sendBeacon`` would not let us attach the JWT header.
 */
export async function teardownLocalBrowsers(
  opts: { keepalive?: boolean } = {},
): Promise<TeardownLocalBrowsersResponse | null> {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${getBaseUrl()}/system/teardown-local-browsers`, {
      method: "POST",
      headers,
      body: "{}",
      keepalive: opts.keepalive ?? false,
    });
    if (!res.ok) return null;
    const text = await res.text();
    if (!text.trim()) return null;
    try {
      return JSON.parse(text) as TeardownLocalBrowsersResponse;
    } catch {
      return null;
    }
  } catch {
    /* best-effort: swallow network errors so tab-close / Retry never throws on cleanup */
    return null;
  }
}
