import { apiFetch } from "./client";

export interface RtoPaymentInsertPayload {
  customer_id: number;
  name?: string | null;
  mobile?: string | null;
  chassis_num?: string | null;
  application_num: string;
  submission_date: string; // dd-mm-yyyy
  rto_payment_due: number;
  status?: string;
  pos_mgr_id?: string | null;
  txn_id?: string | null;
  payment_date?: string | null;
}

export interface RtoPaymentRow {
  id: number;
  customer_id: number;
  name: string | null;
  mobile: string | null;
  chassis_num: string | null;
  application_num: string;
  submission_date: string;
  rto_payment_due: number;
  status: string;
  pos_mgr_id: string | null;
  txn_id: string | null;
  payment_date: string | null;
  created_at?: string;
}

export async function insertRtoPayment(payload: RtoPaymentInsertPayload): Promise<{ id: number; ok: boolean }> {
  return apiFetch<{ id: number; ok: boolean }>("/rto-payment-details", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listRtoPayments(): Promise<RtoPaymentRow[]> {
  return apiFetch<RtoPaymentRow[]>("/rto-payment-details");
}
