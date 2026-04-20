import { apiFetch, getBaseUrl } from "./client";
import { getAccessToken } from "../auth/token";
import { isElectron } from "../electron";

export interface FillDmsCustomer {
  name?: string | null;
  care_of?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  pin_code?: string | null;
  mobile_number?: string | null;
  mobile?: string | null;
  aadhar_id?: string | null;
}

export interface FillDmsVehicle {
  key_no?: string | null;
  frame_no?: string | null;
  engine_no?: string | null;
}

export interface FillDmsRequest {
  /** Optional when staging_id is set; server resolves from staging (file_location). */
  subfolder?: string | null;
  /** Optional; defaults to server DMS_BASE_URL. */
  dms_base_url?: string | null;
  /** Dealer ID for Form 20 field 10 (dealer name & address from dealer_ref). */
  dealer_id?: number | null;
  /**
   * Draft ``add_sales_staging`` row UUID. When set, DMS fill uses ``payload_json`` only (OCR merge);
   * omit ``customer_id`` / ``vehicle_id``. Optional request ``customer`` / ``vehicle`` fields merge on top.
   */
  staging_id?: string | null;
  /** From Submit Info; used for legacy master join and to persist DMS scrape when no ``staging_id``. */
  customer_id?: number | null;
  vehicle_id?: number | null;
  /** Optional when ``staging_id`` is set (fill values come from staging payload). */
  customer?: FillDmsCustomer;
  vehicle?: FillDmsVehicle;
}

export interface FillDmsResponse {
  success: boolean;
  /** After staging-path Create Invoice: committed master ids (use for insurance / RTO). */
  customer_id?: number | null;
  vehicle_id?: number | null;
  /** Real Siebel mode: navigation only; forms not auto-filled — show instead of "filled successfully". */
  warning?: string | null;
  dms_automation_mode?: string | null;
  vehicle: {
    key_num?: string;
    frame_num?: string;
    engine_num?: string;
    full_chassis?: string;
    full_engine?: string;
    model?: string;
    color?: string;
    cubic_capacity?: string;
    seating_capacity?: string;
    body_type?: string;
    vehicle_type?: string;
    num_cylinders?: string;
    vehicle_price?: string;
    year_of_mfg?: string;
  };
  pdfs_saved: string[];
  application_id?: string | null;
  rto_fees?: number | null;
  error?: string | null;
  /** Completed DMS steps from last Fill DMS run (Add Sales top banner). */
  dms_milestones?: string[];
  /** Real Siebel: ordered narrative lines; UI prefers this over milestones when non-empty. */
  dms_step_messages?: string[];
  /** My Orders grid already had Invoice# — operator can use Create Invoice in the app. */
  ready_for_client_create_invoice?: boolean | null;
  /** When the API uses S3 storage, presigned PDF URLs for Electron to print locally. */
  print_jobs?: ApiPrintJob[];
}

/**
 * Server ``print_jobs``: presigned HTTPS URLs when ``STORAGE_BACKEND=s3``.
 * Electron sidecar runs may use absolute local PDF paths in ``presigned_url`` instead.
 */
export interface ApiPrintJob {
  filename?: string;
  presigned_url: string;
  kind?: string;
}

/** Fire-and-forget: print PDFs in Electron when ``print_jobs`` is present (no-op in browser-only). */
export function dispatchPrintJobsFromApi(jobs: ApiPrintJob[] | undefined | null): void {
  if (!jobs?.length) return;
  if (typeof window === "undefined") return;
  const fn = window.electronAPI?.print?.printPdfsFromUrls;
  if (!fn) return;
  void fn(jobs);
}

/** DMS full flow / Create Invoice — keep in sync with `vite.config.ts` LONG_RUNNING_MS + fetch abort. */
const FILL_FORMS_TIMEOUT_MS = 900_000; // 15 min
/** Pre-open DMS browser after upload; allow enough time for first managed-browser launch. */
const DMS_WARM_BROWSER_TIMEOUT_MS = 300_000; // 5 min
const FILL_HERO_INSURANCE_TIMEOUT_MS = 900_000; // pre + main + post (Playwright)

export interface WarmDmsBrowserRequest {
  dms_base_url?: string | null;
}

export interface WarmDmsBrowserResponse {
  success: boolean;
  error?: string | null;
}

/** Open/attach DMS and wait through login readiness only (no fill). Fire-and-forget from Add Sales after upload. */
export async function warmDmsBrowser(req: WarmDmsBrowserRequest): Promise<WarmDmsBrowserResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DMS_WARM_BROWSER_TIMEOUT_MS);
  try {
    return await apiFetch<WarmDmsBrowserResponse>("/fill-forms/dms/warm-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Electron-aware warm browser: routes through the local sidecar (Playwright on the dealer PC)
 * when running inside Electron, falls back to the cloud API otherwise.
 */
export async function warmDmsBrowserLocal(req: WarmDmsBrowserRequest): Promise<WarmDmsBrowserResponse> {
  if (!isElectron()) return warmDmsBrowser(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "warm_browser",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { dms_base_url: req.dms_base_url ?? undefined },
    });
    if (result.timedOut) return { success: false, error: "Sidecar warm-browser timed out." };
    const data = (result.parsed as { data?: WarmDmsBrowserResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

export interface WarmVahanBrowserResponse {
  success: boolean;
  error?: string | null;
  message?: string | null;
}

/** Open/attach Vahan login (no fill). RTO Queue: first click warms browser; operator logs in; second click runs batch. */
export async function warmVahanBrowser(): Promise<WarmVahanBrowserResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DMS_WARM_BROWSER_TIMEOUT_MS);
  try {
    return await apiFetch<WarmVahanBrowserResponse>("/fill-forms/vahan/warm-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

export interface FillHeroInsuranceRequest {
  insurance_base_url?: string | null;
  /** Optional when staging_id is set (resolved from staging after Create Invoice). */
  customer_id?: number | null;
  vehicle_id?: number | null;
  /** Optional when staging_id is set. */
  subfolder?: string | null;
  dealer_id?: number | null;
  /** When set, server resolves subfolder and can resolve customer_id / vehicle_id from staging payload. */
  staging_id?: string | null;
}

export interface FillHeroInsuranceResponse {
  success: boolean;
  error?: string | null;
  page_url?: string | null;
  login_url?: string | null;
  match_base?: string | null;
  print_jobs?: ApiPrintJob[];
}

/** Run only DMS section (login, enquiry, vehicle search, scrape, PDFs). Independent process. */
export async function fillDmsOnly(req: FillDmsRequest): Promise<FillDmsResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_FORMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-forms/dms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    return res;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Electron-aware Create Invoice: routes through the local sidecar (Playwright on the dealer PC)
 * when running inside Electron, falls back to the cloud API otherwise.
 */
export async function fillDmsLocal(req: FillDmsRequest): Promise<FillDmsResponse> {
  if (!isElectron()) return fillDmsOnly(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_dms",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { ...req },
      timeoutMs: FILL_FORMS_TIMEOUT_MS,
    });
    if (result.timedOut) {
      throw new Error("Create Invoice request timed out. Check the upload folder for PDFs.");
    }
    const data = (result.parsed as { data?: FillDmsResponse })?.data;
    if (data) return data;
    const empty: FillDmsResponse = {
      success: false,
      vehicle: {},
      pdfs_saved: [],
      error: result.error ?? "Sidecar returned no data.",
    };
    return empty;
  } catch (err) {
    if (isFillDmsAbortError(err)) throw err;
    const empty: FillDmsResponse = {
      success: false,
      vehicle: {},
      pdfs_saved: [],
      error: err instanceof Error ? err.message : String(err),
    };
    return empty;
  }
}

export async function fillDms(req: FillDmsRequest): Promise<FillDmsResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_FORMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-forms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    return res;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Hero Insurance: pre_process (same automation as former standalone insurance fill) + main_process + post_process.
 * Single GI entry for Add Sales Generate Insurance.
 */
export async function fillHeroInsurance(req: FillHeroInsuranceRequest): Promise<FillHeroInsuranceResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_HERO_INSURANCE_TIMEOUT_MS);
  try {
    return await apiFetch<FillHeroInsuranceResponse>("/fill-forms/insurance/hero", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

export interface PrintForm20Request {
  subfolder: string;
  customer: FillDmsCustomer;
  vehicle: Record<string, unknown>;
  vehicle_id?: number | null;
  dealer_id?: number | null;
}

export interface PrintForm20Response {
  success: boolean;
  pdfs_saved: string[];
  error?: string | null;
  print_jobs?: ApiPrintJob[];
}

/** Generate Form 20 (all pages) and save to Uploaded scans. Called from Print forms button. */
export async function printForm20(req: PrintForm20Request): Promise<PrintForm20Response> {
  return apiFetch<PrintForm20Response>("/fill-forms/print-form20", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

/** Gate Pass only: Word template → PDF under Uploaded scans, then server schedules print/open. */
export async function printGatePass(req: PrintForm20Request): Promise<PrintForm20Response> {
  return apiFetch<PrintForm20Response>("/fill-forms/print-gate-pass", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

/** Fetch Data from DMS for a subfolder (fallback when Fill Forms response was lost). */
export async function getDataFromDms(subfolder: string, dealerId?: number): Promise<{ vehicle: Record<string, string>; customer: Record<string, string> }> {
  const params = new URLSearchParams();
  params.set("subfolder", subfolder);
  if (dealerId != null) params.set("dealer_id", String(dealerId));
  return apiFetch<{ vehicle: Record<string, string>; customer: Record<string, string> }>(
    `/fill-forms/data-from-dms?${params.toString()}`
  );
}

/** True if the error is from an aborted request (timeout or user abort). */
export function isFillDmsAbortError(err: unknown): boolean {
  if (err instanceof Error) {
    const m = err.message?.toLowerCase() ?? "";
    return m.includes("abort") || m.includes("aborted");
  }
  return false;
}

/**
 * Electron-aware Generate Insurance: routes through the local sidecar when in Electron,
 * falls back to the cloud API otherwise.
 */
export async function fillHeroInsuranceLocal(req: FillHeroInsuranceRequest): Promise<FillHeroInsuranceResponse> {
  if (!isElectron()) return fillHeroInsurance(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_insurance",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { ...req },
      timeoutMs: FILL_HERO_INSURANCE_TIMEOUT_MS,
    });
    if (result.timedOut) return { success: false, error: "Insurance sidecar timed out." };
    const data = (result.parsed as { data?: FillHeroInsuranceResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Electron-aware Warm Vahan Browser: routes through the local sidecar when in Electron,
 * falls back to the cloud API otherwise.
 */
export async function warmVahanBrowserLocal(): Promise<WarmVahanBrowserResponse> {
  if (!isElectron()) return warmVahanBrowser();
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "warm_vahan",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: {},
    });
    if (result.timedOut) return { success: false, error: "Vahan warm-browser timed out." };
    const data = (result.parsed as { data?: WarmVahanBrowserResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}
