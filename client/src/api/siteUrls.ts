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
  /** True when backend ``ENVIRONMENT`` is prod/production (case-insensitive). */
  environment_is_production?: boolean;
}

/**
 * DMS / Vahan / Insurance bases from the API (`GET /settings/site-urls`, same as server `.env`).
 * Pass *dealerId* so DMS uses ``dealer_ref.dms_siebel_portal`` (HMCL vs ASC).
 * Prefer the API; in Electron, fall back to main-process `D:\\Saathi\\.env` via IPC if fetch fails
 * (offline, misconfigured `VITE_API_URL`, CORS).
 */
export async function getSiteUrls(dealerId?: number): Promise<SiteUrls> {
  const q =
    dealerId != null && Number.isFinite(dealerId) && dealerId > 0
      ? `?dealer_id=${encodeURIComponent(String(dealerId))}`
      : "";
  try {
    return await apiFetch<SiteUrls>(`/settings/site-urls${q}`);
  } catch (err) {
    if (isElectron() && window.electronAPI?.config) {
      return window.electronAPI.config.getSiteUrls();
    }
    throw err;
  }
}
