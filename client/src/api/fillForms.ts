import { apiFetch } from "./client";

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
  subfolder: string;
  dms_base_url?: string | null;
  vahan_base_url?: string | null;
  rto_dealer_id?: string | null;
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
  customer: FillDmsCustomer;
  vehicle: FillDmsVehicle;
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
}

const FILL_FORMS_TIMEOUT_MS = 180000; // 3 min per section
/** Pre-open DMS browser after upload; allow enough time for first managed-browser launch. */
const DMS_WARM_BROWSER_TIMEOUT_MS = 180000;
const FILL_VAHAN_TIMEOUT_MS = 60000; // 1 min for Vahan
const FILL_HERO_INSURANCE_TIMEOUT_MS = 180000; // pre + main + post

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

export interface FillVahanRequest {
  vahan_base_url: string;
  rto_dealer_id?: string | null;
  dealer_id?: number | null;
  customer_id?: number | null;
  vehicle_id?: number | null;
  subfolder?: string | null;
  customer_name?: string | null;
  chassis_no?: string | null;
  vehicle_model?: string | null;
  vehicle_colour?: string | null;
  fuel_type?: string | null;
  year_of_mfg?: string | null;
  vehicle_price?: number | null;
}

export interface FillVahanResponse {
  success: boolean;
  application_id?: string | null;
  rto_fees?: number | null;
  error?: string | null;
}

export interface FillHeroInsuranceRequest {
  insurance_base_url?: string | null;
  customer_id?: number | null;
  vehicle_id?: number | null;
  subfolder?: string | null;
  dealer_id?: number | null;
  staging_id?: string | null;
}

export interface FillHeroInsuranceResponse {
  success: boolean;
  error?: string | null;
  page_url?: string | null;
  login_url?: string | null;
  match_base?: string | null;
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

/** Run only Vahan (RTO) section. Independent process. */
export async function fillVahanOnly(req: FillVahanRequest): Promise<FillVahanResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_VAHAN_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillVahanResponse>("/fill-forms/vahan", {
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
}

/** Generate Form 20 (all pages) and save to Uploaded scans. Called from Print forms button. */
export async function printForm20(req: PrintForm20Request): Promise<PrintForm20Response> {
  return apiFetch<PrintForm20Response>("/fill-forms/print-form20", {
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
