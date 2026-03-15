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
    throw new Error(text || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}
