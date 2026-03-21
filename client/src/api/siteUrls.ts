import { apiFetch } from "./client";

export interface SiteUrls {
  dms_base_url: string;
  vahan_base_url: string;
  insurance_base_url: string;
}

/** Backend/.env DMS, Vahan, Insurance bases (required server-side; no client-side fallbacks). */
export async function getSiteUrls(): Promise<SiteUrls> {
  return apiFetch<SiteUrls>("/settings/site-urls");
}
