/**
 * Base API client. Swap baseUrl per environment or microservice.
 * When unset, uses empty string so Vite dev proxy forwards to backend (avoids CORS).
 */
const baseUrl = import.meta.env.VITE_API_URL ?? "";

export function getBaseUrl(): string {
  return baseUrl;
}

const BACKEND_HINT =
  "Start the API on port 8000 from the `backend` folder, then refresh: " +
  "`python -m uvicorn app.main:app --reload --port 8000` " +
  "(or run `daily_startup.bat` from the project root).";

/** Maps browser fetch failures (backend down, CORS, proxy reset) to a clear message. */
export function throwMappedFetchError(err: unknown): never {
  const name = err instanceof Error ? err.name : (err as { name?: string })?.name;
  if (name === "AbortError") throw err;
  const raw = err instanceof Error ? err.message : String(err);
  const isUnreachable =
    /ECONNREFUSED|Failed to fetch|NetworkError|Load failed|network error/i.test(raw);
  if (isUnreachable) {
    throw new Error(
      `Cannot connect to the backend. ${BACKEND_HINT} ` +
        "If the API is running, open the app at http://localhost:5173 (not only your LAN IP) unless CORS allows your origin."
    );
  }
  throw err instanceof Error ? err : new Error(raw);
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  // #region agent log
  const __dbg_startedAt = Date.now();
  if (path.includes("/fill-forms") || path.includes("/fill-dms")) {
    fetch("http://127.0.0.1:7384/ingest/843041b7-64c1-4933-bb72-235d36224f70", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "0875fe" },
      body: JSON.stringify({
        sessionId: "0875fe",
        runId: "pre-fix",
        hypothesisId: "G3",
        location: "client.ts:apiFetch",
        message: "fill_dms_fetch_start",
        data: { path, method: String(options.method || "GET") },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  }
  // #endregion
  let res: Response;
  try {
    res = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers: { ...options.headers },
    });
  } catch (err) {
    throwMappedFetchError(err);
  }
  if (!res.ok) {
    // #region agent log
    if (path.includes("/fill-forms") || path.includes("/fill-dms")) {
      fetch("http://127.0.0.1:7384/ingest/843041b7-64c1-4933-bb72-235d36224f70", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "0875fe" },
        body: JSON.stringify({
          sessionId: "0875fe",
          runId: "pre-fix",
          hypothesisId: "G4",
          location: "client.ts:apiFetch",
          message: "fill_dms_fetch_non_ok",
          data: {
            path,
            status: res.status,
            elapsed_ms: Date.now() - __dbg_startedAt,
          },
          timestamp: Date.now(),
        }),
      }).catch(() => {});
    }
    // #endregion
    const text = await res.text();
    let detail: string | undefined;
    try {
      const json = JSON.parse(text) as { detail?: string };
      if (typeof json.detail === "string") detail = json.detail;
    } catch {
      /* not JSON */
    }

    const gatewayOrTimeout = res.status === 502 || res.status === 503 || res.status === 504;
    let msg: string;
    if (detail) {
      msg = detail;
    } else if (gatewayOrTimeout) {
      msg =
        `Service unavailable (${res.status}). The browser or a proxy stopped waiting for the server. ` +
        `Create Invoice (DMS) / Playwright can take several minutes — increase dev-server or reverse-proxy timeouts ` +
        `(Vite: \`vite.config.ts\` \`/fill-forms\` proxyTimeout; client: \`fillForms.ts\`), ` +
        `confirm the Python API on port 8000 is running, then try again.`;
    } else {
      const trimmed = (text || "").trim();
      msg =
        trimmed.length > 0 && trimmed.length < 400 && !trimmed.startsWith("<")
          ? trimmed
          : `Request failed (${res.status})`;
    }
    throw new Error(msg);
  }
  const contentType = (res.headers.get("content-type") || "").toLowerCase();
  const bodyText = await res.text();
  const looksLikeHtml = /^\s*<!doctype html|^\s*<html/i.test(bodyText);
  const likelyJson = contentType.includes("application/json") || (!looksLikeHtml && bodyText.trim().startsWith("{"));
  if (!likelyJson) {
    throw new Error(
      `Expected JSON response for ${path}, but received ${contentType || "unknown content type"} ` +
        "(often dev-server/proxy fallback HTML). Verify VITE_API_URL/proxy routes to backend on :8000."
    );
  }
  try {
    return JSON.parse(bodyText) as T;
  } catch {
    throw new Error(`Expected valid JSON response for ${path}, but received non-JSON content.`);
  }
}
