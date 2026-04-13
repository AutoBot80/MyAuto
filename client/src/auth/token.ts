const STORAGE_KEY = "auto_ai_access_token";

export function getAccessToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setAccessToken(token: string): void {
  sessionStorage.setItem(STORAGE_KEY, token);
}

export function clearAccessToken(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}
