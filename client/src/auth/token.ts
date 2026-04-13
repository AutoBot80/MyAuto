const STORAGE_KEY = "auto_ai_access_token";

/** Prefer localStorage so login survives tab close and dev server reloads; migrate legacy sessionStorage. */
function readStoredToken(): string | null {
  try {
    const fromLocal = localStorage.getItem(STORAGE_KEY);
    if (fromLocal) return fromLocal;
    const legacy = sessionStorage.getItem(STORAGE_KEY);
    if (legacy) {
      localStorage.setItem(STORAGE_KEY, legacy);
      sessionStorage.removeItem(STORAGE_KEY);
      return legacy;
    }
    return null;
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  return readStoredToken();
}

export function setAccessToken(token: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, token);
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  } catch {
    /* ignore */
  }
}

export function clearAccessToken(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
