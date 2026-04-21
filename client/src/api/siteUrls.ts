import { apiFetch } from "./client";
import { isElectron } from "../electron";

export interface SiteUrls {
  dms_base_url: string;
  /** From ``DMS_MODE`` in backend/.env (default **real** Siebel; **dummy** is no longer supported). */
  dms_mode?: string;
  dms_real_siebel?: boolean;
  dms_real_contact_url_configured?: boolean;
  /** Informational only; Vahan automation not yet wired. */
  vahan_base_url?: string;
  insurance_base_url: string;
}

/**
 * DMS / Vahan / Insurance bases from the API (`GET /settings/site-urls`, same as server `.env`).
 * Prefer the API; in Electron, fall back to main-process `D:\\Saathi\\.env` via IPC if fetch fails
 * (offline, misconfigured `VITE_API_URL`, CORS).
 */
export async function getSiteUrls(): Promise<SiteUrls> {
  try {
    return await apiFetch<SiteUrls>("/settings/site-urls");
  } catch (err) {
    if (isElectron() && window.electronAPI?.config) {
      return window.electronAPI.config.getSiteUrls();
    }
    throw err;
  }
}
