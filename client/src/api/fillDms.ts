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
  /** From Submit Info; used to update vehicle_master with DMS-scraped data. */
  customer_id?: number | null;
  vehicle_id?: number | null;
  customer: FillDmsCustomer;
  vehicle: FillDmsVehicle;
}

export interface FillDmsResponse {
  success: boolean;
  /** Real Siebel mode: navigation only; forms not auto-filled — show instead of "filled successfully". */
  warning?: string | null;
  dms_automation_mode?: string | null;
  vehicle: {
    key_num?: string;
    frame_num?: string;
    engine_num?: string;
    model?: string;
    color?: string;
    cubic_capacity?: string;
    seating_capacity?: string;
    body_type?: string;
    vehicle_type?: string;
    num_cylinders?: string;
    horse_power?: string;
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
}

const FILL_DMS_TIMEOUT_MS = 180000; // 3 min per section
const FILL_VAHAN_TIMEOUT_MS = 60000; // 1 min for Vahan
const FILL_INSURANCE_TIMEOUT_MS = 120000; // 2 min for Insurance

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

export interface FillInsuranceRequest {
  insurance_base_url?: string | null;
  dealer_id?: number | null;
  customer_id?: number | null;
  vehicle_id?: number | null;
  subfolder?: string | null;
}

export interface FillInsuranceResponse {
  success: boolean;
  error?: string | null;
}

/** Run only DMS section (login, enquiry, vehicle search, scrape, PDFs). Independent process. */
export async function fillDmsOnly(req: FillDmsRequest): Promise<FillDmsResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_DMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-dms/dms", {
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
    const res = await apiFetch<FillVahanResponse>("/fill-dms/vahan", {
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
  const timeoutId = setTimeout(() => controller.abort(), FILL_DMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-dms", {
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

/** Run only Insurance flow. Fills data but does not submit policy. */
export async function fillInsuranceOnly(req: FillInsuranceRequest): Promise<FillInsuranceResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_INSURANCE_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillInsuranceResponse>("/fill-dms/insurance", {
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
  return apiFetch<PrintForm20Response>("/fill-dms/print-form20", {
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
    `/fill-dms/data-from-dms?${params.toString()}`
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
