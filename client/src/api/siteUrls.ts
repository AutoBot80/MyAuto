import { apiFetch } from "./client";

export interface SiteUrls {
  dms_base_url: string;
  /** ``dummy`` (static HTML) or ``real`` (Siebel URL navigation); from ``DMS_MODE`` in backend/.env. */
  dms_mode?: string;
  dms_real_siebel?: boolean;
  dms_real_contact_url_configured?: boolean;
  vahan_base_url: string;
  insurance_base_url: string;
}

/** Backend/.env DMS, Vahan, Insurance bases (required server-side; no client-side fallbacks). */
export async function getSiteUrls(): Promise<SiteUrls> {
  return apiFetch<SiteUrls>("/settings/site-urls");
}
