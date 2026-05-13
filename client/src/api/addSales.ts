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
  /** ``master_ref`` insurers with ``comments = 'Y'`` (portal dropdown). */
  portal_insurers?: string[] | null;
}

export type FetchCreateInvoiceEligibilityParams =
  | { customerId: number; vehicleId: number; dealerId?: number | null }
  | { chassisNum: string; engineNum: string; mobile: string; dealerId?: number | null };

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
  return apiFetch<CreateInvoiceEligibilityResponse>(`/add-sales/create-invoice-eligibility?${q.toString()}`);
}

/** Subset of eligibility CPA fields from ``GET /add-sales/dealer-cpa-context`` (no sale keys). */
export type DealerCpaContextResponse = Pick<
  CreateInvoiceEligibilityResponse,
  "cpa_insurers" | "hero_cpi" | "dealer_cpa_insurer" | "cpa_alliance_portal_enabled" | "portal_insurers"
>;

export async function fetchDealerCpaContext(dealerId: number): Promise<DealerCpaContextResponse> {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  return apiFetch<DealerCpaContextResponse>(`/add-sales/dealer-cpa-context?${q.toString()}`);
}
