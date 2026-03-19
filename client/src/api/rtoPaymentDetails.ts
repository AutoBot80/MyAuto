import { apiFetch } from "./client";
import { getBaseUrl } from "./client";
import { DEALER_ID } from "./dealerId";

export interface RtoPaymentInsertPayload {
  application_id?: string | null;
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
  queue_id: string;
  application_id: string;
  vahan_application_id?: string | null;
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
  leased_until?: string | null;
  attempt_count?: number;
  last_error?: string | null;
  started_at?: string | null;
  uploaded_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  processing_session_id?: string | null;
  worker_id?: string | null;
}

export interface RtoBatchRowResult {
  queue_id: string | null;
  customer_name: string | null;
  status: string;
  vahan_application_id?: string | null;
  rto_fees?: number | null;
  error?: string | null;
}

export interface RtoBatchStatus {
  dealer_id: number;
  session_id: string | null;
  state: "idle" | "starting" | "running" | "completed" | "failed";
  message: string;
  started_at: string | null;
  completed_at: string | null;
  current_queue_id: string | null;
  current_customer_name: string | null;
  current_vahan_application_id: string | null;
  total_count: number;
  processed_count: number;
  cart_count: number;
  failed_count: number;
  last_error: string | null;
  rows: RtoBatchRowResult[];
}

export async function insertRtoPayment(payload: RtoPaymentInsertPayload): Promise<{ queue_id: string; application_id: string; ok: boolean }> {
  return apiFetch<{ queue_id: string; application_id: string; ok: boolean }>("/rto-queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listRtoPayments(dealerId?: number): Promise<RtoPaymentRow[]> {
  const did = dealerId ?? DEALER_ID;
  return apiFetch<RtoPaymentRow[]>(`/rto-queue?dealer_id=${did}`);
}

export async function startRtoBatch(payload?: {
  dealer_id?: number;
  operator_id?: string | null;
  limit?: number;
  vahan_base_url?: string | null;
}): Promise<{ started: boolean; session_id: string; message: string }> {
  return apiFetch<{ started: boolean; session_id: string; message: string }>("/rto-queue/process-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

export async function getRtoBatchStatus(dealerId?: number): Promise<RtoBatchStatus> {
  const did = dealerId ?? DEALER_ID;
  return apiFetch<RtoBatchStatus>(`/rto-queue/process-batch/status?dealer_id=${did}`);
}

/** Get RTO queue row for a sale. */
export async function getRtoPaymentBySale(customerId: number, vehicleId: number): Promise<RtoPaymentRow | null> {
  const res = await apiFetch<RtoPaymentRow | null>(
    `/rto-queue/by-sale?customer_id=${customerId}&vehicle_id=${vehicleId}`
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
    `/rto-queue/${encodeURIComponent(applicationId)}/pay`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vahan_base_url: vahanBase }),
    }
  );
}
