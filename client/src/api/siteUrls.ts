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
 * Backend/.env DMS, Vahan, Insurance bases.
 * In Electron (file:// origin) the HTTP endpoint is unreachable, so we
 * read the .env directly via the main-process IPC bridge.
 */
export async function getSiteUrls(): Promise<SiteUrls> {
  if (isElectron()) {
    return window.electronAPI!.config.getSiteUrls();
  }
  return apiFetch<SiteUrls>("/settings/site-urls");
}
