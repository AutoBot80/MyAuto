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
