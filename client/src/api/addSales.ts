import { apiFetch } from "./client";

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
}

export type FetchCreateInvoiceEligibilityParams =
  | { customerId: number; vehicleId: number }
  | { chassisNum: string; engineNum: string; mobile: string };

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
  return apiFetch<CreateInvoiceEligibilityResponse>(`/add-sales/create-invoice-eligibility?${q.toString()}`);
}
