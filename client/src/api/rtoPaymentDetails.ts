import { apiFetch } from "./client";
import { getBaseUrl } from "./client";

export interface RtoPaymentInsertPayload {
  application_id: string;
  customer_id: number;
  vehicle_id: number;
  dealer_id?: number | null;
  name?: string | null;
  mobile?: string | null;
  chassis_num?: string | null;
  register_date: string; // dd-mm-yyyy
  rto_fees: number;
  status?: string;
  pay_txn_id?: string | null;
  operator_id?: string | null;
  payment_date?: string | null;
  rto_status?: string;
  subfolder?: string | null;
}

export interface RtoPaymentRow {
  application_id: string;
  sales_id?: number;
  customer_id: number;
  vehicle_id: number;
  dealer_id: number | null;
  name: string | null;
  mobile: string | null;
  chassis_num: string | null;
  register_date: string;
  rto_fees: number;
  status: string;
  pay_txn_id: string | null;
  operator_id: string | null;
  payment_date: string | null;
  rto_status: string;
  subfolder?: string | null;
  created_at?: string;
}

export async function insertRtoPayment(payload: RtoPaymentInsertPayload): Promise<{ application_id: string; ok: boolean }> {
  return apiFetch<{ application_id: string; ok: boolean }>("/rto-payment-details", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listRtoPayments(): Promise<RtoPaymentRow[]> {
  return apiFetch<RtoPaymentRow[]>("/rto-payment-details");
}

/** Get RTO payment row for a sale (to restore application_id/rto_fees on Add Sales page). */
export async function getRtoPaymentBySale(customerId: number, vehicleId: number): Promise<RtoPaymentRow | null> {
  const res = await apiFetch<RtoPaymentRow | null>(
    `/rto-payment-details/by-sale?customer_id=${customerId}&vehicle_id=${vehicleId}`
  );
  return res;
}

/** URL to dummy Vahan search/payment page for a given application_id */
export function getVahanPayUrl(applicationId: string): string {
  const base = getBaseUrl().replace(/\/$/, "");
  return `${base}/dummy-vaahan/search.html?application_id=${encodeURIComponent(applicationId)}`;
}

export async function payRtoPayment(applicationId: string): Promise<{ ok: boolean; pay_txn_id?: string; status?: string }> {
  const base = getBaseUrl().replace(/\/$/, "");
  const vahanBase = base ? `${base}/dummy-vaahan` : "/dummy-vaahan";
  return apiFetch<{ ok: boolean; pay_txn_id?: string; status?: string }>(
    `/rto-payment-details/${encodeURIComponent(applicationId)}/pay`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vahan_base_url: vahanBase }),
    }
  );
}
