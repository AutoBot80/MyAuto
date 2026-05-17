import { apiFetch } from "./client";
import type { FillDmsCustomer } from "./fillForms";

export const PROCESS_LABEL_PRINT_QUEUE_RTO = "Print / Queue RTO";

export function mobileDigitsForPrintRto(
  subfolder: string,
  customer?: FillDmsCustomer | null
): string | null {
  const raw = customer?.mobile_number ?? customer?.mobile ?? "";
  const d = String(raw).replace(/\D/g, "");
  if (d.length >= 10) return d.slice(-10);
  const m = (subfolder || "").trim().match(/^(\d{10})/);
  return m ? m[1] : null;
}

export function printQueueRtoEntityKey(subfolder: string, mobileDigits: string | null): string {
  const safe = (subfolder || "").trim().replace(/[^\w-]/g, "_") || "default";
  const mob = mobileDigits || "nomobile";
  return `pqtrto:${safe}:${mob}`;
}

/** Best-effort: upserts Admin → Failure Logs (``POST /sidecar/failure-log``). */
export async function recordProcessFailureLog(body: {
  dealer_id: number;
  process_label: string;
  entity_dedupe_key: string;
  error_text: string;
  customer_mobile?: string | null;
  rto_queue_id?: number | null;
}): Promise<void> {
  const err = (body.error_text || "").trim();
  if (!err) return;
  try {
    await apiFetch<{ ok: boolean }>("/sidecar/failure-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    /* non-fatal */
  }
}

export async function recordPrintQueueRtoFailure(params: {
  dealerId: number;
  subfolder: string;
  customer?: FillDmsCustomer | null;
  errorText: string;
  rtoQueueId?: number | null;
}): Promise<void> {
  const subfolder = (params.subfolder || "").trim();
  if (!subfolder || params.dealerId <= 0) return;
  const md = mobileDigitsForPrintRto(subfolder, params.customer);
  await recordProcessFailureLog({
    dealer_id: params.dealerId,
    process_label: PROCESS_LABEL_PRINT_QUEUE_RTO,
    entity_dedupe_key: printQueueRtoEntityKey(subfolder, md),
    error_text: params.errorText.trim(),
    customer_mobile: md,
    rto_queue_id: params.rtoQueueId ?? null,
  });
}
