import { apiFetch } from "./client";

export interface CpaInsurerPortalRow {
  ref_value: string;
  login_url: string;
}

export interface CreateInvoiceEligibilityResponse {
  create_invoice_enabled: boolean;
  matched_sales_row: boolean;
  invoice_number: string | null;
  reason: string | null;
  invoice_recorded: boolean;
  generate_insurance_enabled: boolean;
  generate_insurance_reason: string | null;
  /** When chassis/engine/mobile match DB rows — use for Generate Insurance / RTO after Create Invoice. */
  resolved_customer_id?: number | null;
  resolved_vehicle_id?: number | null;
  /** Present when ``dealer_id`` was sent on the request and the backend resolved dealer / CPA rows. */
  cpa_insurers?: CpaInsurerPortalRow[] | null;
  hero_cpi?: string | null;
  dealer_cpa_insurer?: string | null;
  cpa_alliance_portal_enabled?: boolean;
  /** False when ``insurance_master`` already has a CPA row for this sale and current calendar year. */
  cpa_alliance_insurance_enabled?: boolean;
  cpa_alliance_insurance_reason?: string | null;
  /** ``master_ref`` insurers with ``comments = 'Y'`` (portal dropdown). */
  portal_insurers?: string[] | null;
  /** ``master_ref`` FINANCER ``ref_value`` rows (Section 2 financier dropdown). */
  financiers?: string[] | null;
  /** ``dealer_ref.cpi_reqd`` when ``dealer_id`` was sent. */
  dealer_cpi_reqd?: string | null;
  /** ``add_sales_staging.cpi_reqd`` when ``staging_id`` was sent. */
  staging_cpi_reqd?: string | null;
  /** Staging value when present, else dealer (sheet wins). */
  effective_cpi_reqd?: string | null;
  prefer_insurer?: string | null;
  dealer_insurance_addon?: number | null;
  staging_insurance_addon?: number | null;
  effective_insurance_addon?: number | null;
  insurance_addons?: { insurance_addon_id: number; display_label: string }[] | null;
}

export type FetchCreateInvoiceEligibilityParams =
  | { customerId: number; vehicleId: number; dealerId?: number | null; stagingId?: string | null }
  | { chassisNum: string; engineNum: string; mobile: string; dealerId?: number | null; stagingId?: string | null };

export async function fetchCreateInvoiceEligibility(
  opts: FetchCreateInvoiceEligibilityParams
): Promise<CreateInvoiceEligibilityResponse> {
  const q = new URLSearchParams();
  if ("customerId" in opts) {
    q.set("customer_id", String(opts.customerId));
    q.set("vehicle_id", String(opts.vehicleId));
  } else {
    q.set("chassis_num", opts.chassisNum.trim());
    q.set("engine_num", opts.engineNum.trim());
    q.set("mobile", opts.mobile.trim());
  }
  if (opts.dealerId != null && opts.dealerId > 0) {
    q.set("dealer_id", String(opts.dealerId));
  }
  const sid = ("stagingId" in opts ? opts.stagingId : undefined)?.trim();
  if (sid) {
    q.set("staging_id", sid);
  }
  return apiFetch<CreateInvoiceEligibilityResponse>(`/add-sales/create-invoice-eligibility?${q.toString()}`);
}

/** Subset of eligibility CPA fields from ``GET /add-sales/dealer-cpa-context`` (no sale keys). */
export type DealerCpaContextResponse = Pick<
  CreateInvoiceEligibilityResponse,
  | "cpa_insurers"
  | "hero_cpi"
  | "dealer_cpa_insurer"
  | "cpa_alliance_portal_enabled"
  | "portal_insurers"
  | "financiers"
  | "dealer_cpi_reqd"
  | "prefer_insurer"
  | "dealer_insurance_addon"
  | "insurance_addons"
>;

export async function fetchDealerCpaContext(dealerId: number): Promise<DealerCpaContextResponse> {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  return apiFetch<DealerCpaContextResponse>(`/add-sales/dealer-cpa-context?${q.toString()}`);
}

/** Row from ``GET /add-sales/in-process`` (staging_id for API only; not shown in grid). */
export interface AddSalesInProcessRow {
  staging_id: string;
  updated_at: string;
  status: string;
  customer_name: string | null;
  mobile: string | null;
  chassis: string | null;
  engine: string | null;
  order_number: string | null;
  sales_id_text?: string | null;
  customer_id_text?: string | null;
  vehicle_id_text?: string | null;
  file_location?: string | null;
  subfolder?: string | null;
  /** Per-sale CPA Required snapshot from ``add_sales_staging.cpi_reqd``. */
  cpi_reqd?: string | null;
  insurance_addon?: number | null;
}

export async function fetchAddSalesInProcess(
  dealerId: number,
  days = 7
): Promise<{ count: number; rows: AddSalesInProcessRow[] }> {
  const q = new URLSearchParams({ dealer_id: String(dealerId), days: String(days) });
  return apiFetch<{ count: number; rows: AddSalesInProcessRow[] }>(`/add-sales/in-process?${q.toString()}`);
}

/** Row from ``GET /add-sales/invoices`` (committed sales_master, last N IST days). */
export interface AddSalesInvoiceRow {
  sales_id: number;
  customer_name: string | null;
  mobile: string | null;
  model: string | null;
  invoice_date: string | null;
  invoice_number: string | null;
  insurance_policy_num: string | null;
  ex_showroom_amount: number | null;
  insurance_premium: number | null;
  cpa_policy_num: string | null;
  cpa_premium: number | null;
  file_location: string | null;
}

export type FetchAddSalesInvoicesParams = {
  days?: number;
  dateFrom?: string | null;
  dateTo?: string | null;
  mobile?: string | null;
  chassis?: string | null;
  engine?: string | null;
};

export async function fetchAddSalesInvoices(
  dealerId: number,
  opts: FetchAddSalesInvoicesParams = {}
): Promise<{ count: number; rows: AddSalesInvoiceRow[] }> {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  const from = (opts.dateFrom ?? "").trim();
  const to = (opts.dateTo ?? "").trim();
  if (from && to) {
    q.set("date_from", from);
    q.set("date_to", to);
  } else {
    q.set("days", String(opts.days ?? 7));
  }
  const m = (opts.mobile ?? "").trim();
  const c = (opts.chassis ?? "").trim();
  const e = (opts.engine ?? "").trim();
  if (m) q.set("mobile", m);
  if (c) q.set("chassis", c);
  if (e) q.set("engine", e);
  return apiFetch<{ count: number; rows: AddSalesInvoiceRow[] }>(`/add-sales/invoices?${q.toString()}`);
}

export async function fetchAddSalesStagingPayload(
  stagingId: string,
  dealerId: number
): Promise<{
  staging_id: string;
  payload_json: Record<string, unknown>;
  cpi_reqd?: string | null;
  insurance_state?: number | null;
  insurance_addon?: number | null;
  effective_insurance_addon?: number | null;
  effective_insurance_addon_label?: string | null;
  addon_config_warnings?: string[] | null;
  insurance_addons?: { insurance_addon_id: number; display_label: string }[];
}> {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  return apiFetch<{
    staging_id: string;
    payload_json: Record<string, unknown>;
    cpi_reqd?: string | null;
    insurance_state?: number | null;
    insurance_addon?: number | null;
    effective_insurance_addon?: number | null;
    effective_insurance_addon_label?: string | null;
    addon_config_warnings?: string[] | null;
    insurance_addons?: { insurance_addon_id: number; display_label: string }[];
  }>(`/add-sales/staging/${encodeURIComponent(stagingId)}/payload?${q.toString()}`);
}

/** Whitelisted operator edits for In-process Sales Details (PATCH merge into staging). */
export interface PatchAddSalesStagingPayloadBody {
  customer?: {
    care_of?: string | null;
    address?: string | null;
  };
  vehicle?: {
    frame_no?: string | null;
    engine_no?: string | null;
    key_no?: string | null;
    battery_no?: string | null;
  };
  insurance?: {
    insurer?: string | null;
    nominee_name?: string | null;
    nominee_relationship?: string | null;
  };
  /** CPA Required → ``add_sales_staging.cpi_reqd``. */
  cpi_reqd?: "Y" | "N";
  /** MISP add-on preset → ``add_sales_staging.insurance_addon``. */
  insurance_addon?: number;
}

export async function patchAddSalesStagingPayload(
  stagingId: string,
  dealerId: number,
  body: PatchAddSalesStagingPayloadBody
): Promise<{ ok: boolean; staging_id: string; updated_at: string | null }> {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  return apiFetch<{ ok: boolean; staging_id: string; updated_at: string | null }>(
    `/add-sales/staging/${encodeURIComponent(stagingId)}/payload?${q.toString()}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}
