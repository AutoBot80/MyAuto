import { apiFetch } from "./client";
import { DEALER_ID } from "./dealerId";

/** Matches server default for Processed list and failed badge window. */
export const CHALLAN_STAGING_RECENT_DAYS = 15;

export type SubdealerChallanLine = {
  engine_no: string;
  chassis_no: string;
  status: string;
};

export type ParseSubdealerChallanResponse = {
  challan_no: string | null;
  challan_date_raw: string | null;
  challan_date_iso: string | null;
  challan_ddmmyyyy: string | null;
  lines: SubdealerChallanLine[];
  artifact_dir: string | null;
  raw_ocr_path: string | null;
  ocr_json_path: string | null;
  warnings: string[];
  error: string | null;
};

/**
 * POST /subdealer-challan/parse-scan — multipart image/PDF.
 */
export async function parseSubdealerChallanScan(
  file: File
): Promise<ParseSubdealerChallanResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiFetch<ParseSubdealerChallanResponse>("/subdealer-challan/parse-scan", {
    method: "POST",
    body,
  });
}

export type CreateChallanStagingBody = {
  from_dealer_id: number;
  to_dealer_id: number;
  challan_date?: string | null;
  challan_book_num?: string | null;
  lines: { raw_engine?: string; raw_chassis?: string }[];
};

export type CreateChallanStagingResponse = {
  challan_batch_id: string;
  ok: boolean;
  /** Vehicles dropped because the same engine/chassis already exists on a challan for this book+date (any status). */
  dropped_existing_same_book_date?: number;
  /** Duplicate engine/chassis rows removed within this submission (first kept). */
  dropped_duplicate_in_request?: number;
};

export async function createChallanStaging(
  body: CreateChallanStagingBody
): Promise<CreateChallanStagingResponse> {
  return apiFetch<CreateChallanStagingResponse>("/subdealer-challan/staging", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type ProcessChallanBody = {
  dms_base_url?: string | null;
  dealer_id?: number | null;
};

export type ProcessChallanResponse = {
  ok?: boolean;
  error?: string | null;
  challan_id?: number | null;
  dms_step_messages?: string[];
  vehicle?: Record<string, unknown>;
};

export async function processChallanBatch(
  challanBatchId: string,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  return apiFetch<ProcessChallanResponse>(`/subdealer-challan/process/${encodeURIComponent(challanBatchId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** One failed line under a batch (from GET /staging/recent ``failed_lines``). */
export type ChallanFailedDetailLine = {
  challan_detail_staging_id: number;
  raw_chassis: string | null;
  raw_engine: string | null;
  last_error: string | null;
  status: string | null;
};

/**
 * GET /subdealer-challan/staging/recent — one row per batch (master) for the Processed tab.
 */
export type ChallanMasterProcessedRow = {
  challan_batch_id: string;
  from_dealer_id: number;
  to_dealer_id: number;
  from_dealer_name?: string | null;
  to_dealer_name?: string | null;
  challan_date: string | null;
  challan_book_num: string | null;
  num_vehicles: number;
  num_vehicles_prepared: number;
  invoice_complete: boolean;
  invoice_status: string | null;
  created_at: string | null;
  /** Set when process/retry DMS batch completes (ISO timestamp). */
  last_run_at?: string | null;
  ready_line_count: number;
  failed_line_count: number;
  failed_lines: ChallanFailedDetailLine[];
};

export type ListRecentChallanStagingOptions = {
  /** Maps to ``challan_book_num``; when set, API returns that challan regardless of age (no 15-day window). */
  challanBookNum?: string | null;
};

export async function listRecentChallanStaging(
  dealerId?: number,
  days: number = CHALLAN_STAGING_RECENT_DAYS,
  options?: ListRecentChallanStagingOptions
): Promise<ChallanMasterProcessedRow[]> {
  const search = new URLSearchParams();
  search.set("dealer_id", String(dealerId ?? DEALER_ID));
  search.set("days", String(days));
  const book = (options?.challanBookNum ?? "").trim();
  if (book) search.set("challan_book_num", book);
  return apiFetch<ChallanMasterProcessedRow[]>(`/subdealer-challan/staging/recent?${search.toString()}`);
}

/** GET /subdealer-challan/staging/failed-count — badge: master-table row count (batches needing attention in the window). */
export async function getChallanStagingFailedCount(
  dealerId?: number,
  days: number = CHALLAN_STAGING_RECENT_DAYS
): Promise<number> {
  const search = new URLSearchParams();
  search.set("dealer_id", String(dealerId ?? DEALER_ID));
  search.set("days", String(days));
  const res = await apiFetch<{ failed: number }>(`/subdealer-challan/staging/failed-count?${search.toString()}`);
  return res.failed ?? 0;
}

/** POST /subdealer-challan/staging/{challan_detail_staging_id}/retry — prepare + order for the batch (long-running). */
export async function retryChallanStagingRow(
  challanDetailStagingId: number,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  return apiFetch<ProcessChallanResponse>(
    `/subdealer-challan/staging/${encodeURIComponent(String(challanDetailStagingId))}/retry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}

/** POST /subdealer-challan/batch/{challan_batch_id}/retry-order — order/invoice only (all lines Ready). */
export async function retryChallanOrderOnly(
  challanBatchId: string,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  return apiFetch<ProcessChallanResponse>(
    `/subdealer-challan/batch/${encodeURIComponent(challanBatchId)}/retry-order`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}
