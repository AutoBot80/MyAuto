import { apiFetch, getBaseUrl } from "./client";
import { getAccessToken } from "../auth/token";
import { isElectron } from "../electron";
import { DEALER_ID } from "./dealerId";

export interface RtoPaymentInsertPayload {
  sales_id?: number | null;
  customer_id?: number | null;
  vehicle_id?: number | null;
  dealer_id?: number | null;
  insurance_id?: number | null;
  customer_mobile?: string | null;
  rto_application_date?: string | null;
  rto_payment_amount?: number | null;
  status?: string;
  /** Add Sales draft handle (`add_sales_staging.staging_id`). */
  staging_id?: string | null;
}

export interface RtoPaymentRow {
  rto_queue_id: number;
  sales_id: number;
  staging_id?: string | null;
  insurance_id?: number | null;
  customer_mobile?: string | null;
  rto_application_id?: string | null;
  rto_application_date?: string | null;
  rto_payment_id?: string | null;
  rto_payment_amount?: number | null;
  status: string;
  customer_id?: number;
  vehicle_id?: number;
  dealer_id?: number | null;
  customer_name?: string | null;
  mobile?: string | null;
  chassis_num?: string | null;
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
  rto_queue_id: number | null;
  customer_name: string | null;
  status: string;
  rto_application_id?: string | null;
  rto_payment_amount?: number | null;
  error?: string | null;
}

export interface RtoBatchStatus {
  dealer_id: number;
  session_id: string | null;
  state: "idle" | "starting" | "running" | "completed" | "failed";
  message: string;
  started_at: string | null;
  completed_at: string | null;
  current_rto_queue_id: number | null;
  current_customer_name: string | null;
  total_count: number;
  processed_count: number;
  completed_count: number;
  failed_count: number;
  last_error: string | null;
  rows: RtoBatchRowResult[];
  /** True while Vahan is waiting for OTP after Inward Application (Partial Save). */
  otp_pending?: boolean;
  otp_rto_queue_id?: number | null;
  otp_customer_mobile?: string | null;
  otp_prompt?: string | null;
  /** When true, UI may offer "use different mobile" (automation cancels dialog, updates form, Partial Save again). */
  otp_allow_change_mobile?: boolean;
}

export async function insertRtoPayment(payload: RtoPaymentInsertPayload): Promise<{ rto_queue_id: number; ok: boolean }> {
  return apiFetch<{ rto_queue_id: number; ok: boolean }>("/rto-queue", {
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
  limit?: number;
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

export async function submitOperatorOtp(payload: {
  dealer_id?: number;
  rto_queue_id: number;
  otp: string;
}): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>("/rto-queue/submit-operator-otp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function submitOperatorMobileChange(payload: {
  dealer_id?: number;
  rto_queue_id: number;
  mobile: string;
}): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>("/rto-queue/submit-operator-mobile-change", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Get RTO queue row for a sale. */
export async function getRtoPaymentBySale(customerId: number, vehicleId: number): Promise<RtoPaymentRow | null> {
  const res = await apiFetch<RtoPaymentRow | null>(
    `/rto-queue/by-sale?customer_id=${customerId}&vehicle_id=${vehicleId}`
  );
  return res;
}

export async function retryRtoQueueRow(rtoQueueId: number): Promise<{ ok: boolean; rto_queue_id: number; status: string }> {
  return apiFetch<{ ok: boolean; rto_queue_id: number; status: string }>(
    `/rto-queue/${encodeURIComponent(rtoQueueId)}/retry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }
  );
}

const RTO_BATCH_TIMEOUT_MS = 900_000; // 15 min

/**
 * Electron-aware RTO batch: routes through the local sidecar when in Electron,
 * falls back to the cloud API otherwise.
 */
export async function startRtoBatchLocal(payload?: {
  dealer_id?: number;
  limit?: number;
}): Promise<{ started: boolean; session_id: string; message: string }> {
  if (!isElectron()) return startRtoBatch(payload);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_vahan_batch",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { ...(payload ?? {}) },
      timeoutMs: RTO_BATCH_TIMEOUT_MS,
    });
    if (result.timedOut) return { started: false, session_id: "", message: "Vahan batch timed out." };
    const data = result.parsed as { data?: { success: boolean; total: number; completed: number; failed: number } } | null;
    const d = data?.data;
    if (d) {
      return {
        started: true,
        session_id: "",
        message: `Batch done: ${d.completed} completed, ${d.failed} failed of ${d.total}`,
      };
    }
    return { started: result.success, session_id: "", message: result.error ?? "Sidecar returned no data." };
  } catch (err) {
    return { started: false, session_id: "", message: err instanceof Error ? err.message : String(err) };
  }
}
