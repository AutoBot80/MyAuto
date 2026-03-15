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
}

export interface RtoPaymentRow {
  application_id: string;
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

/** URL to dummy Vahan search/payment page for a given application_id */
export function getVahanPayUrl(applicationId: string): string {
  const base = getBaseUrl().replace(/\/$/, "");
  return `${base}/dummy-vaahan/search.html?application_id=${encodeURIComponent(applicationId)}`;
}
