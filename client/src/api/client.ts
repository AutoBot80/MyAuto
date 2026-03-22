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
        `Fill DMS / Playwright can take 1–3 minutes — increase dev-server or reverse-proxy timeouts, ` +
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
  return res.json() as Promise<T>;
}
