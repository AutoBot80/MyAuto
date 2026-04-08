import { apiFetch } from "./client";

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
