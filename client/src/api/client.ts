/**
 * Base API client. Swap baseUrl per environment or microservice.
 * When unset, uses empty string so Vite dev proxy forwards to backend (avoids CORS).
 */
const baseUrl = import.meta.env.VITE_API_URL ?? "";

export function getBaseUrl(): string {
  return baseUrl;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: { ...options.headers },
  });
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
