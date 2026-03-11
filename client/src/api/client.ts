/**
 * Base API client. Swap baseUrl per environment or microservice.
 */
const baseUrl = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

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
