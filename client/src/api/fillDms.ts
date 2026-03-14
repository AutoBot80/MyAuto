import { apiFetch } from "./client";

export interface FillDmsCustomer {
  name?: string | null;
  address?: string | null;
  state?: string | null;
  pin_code?: string | null;
  mobile_number?: string | null;
  mobile?: string | null;
}

export interface FillDmsVehicle {
  key_no?: string | null;
  frame_no?: string | null;
  engine_no?: string | null;
}

export interface FillDmsRequest {
  subfolder: string;
  dms_base_url?: string | null;
  customer: FillDmsCustomer;
  vehicle: FillDmsVehicle;
}

export interface FillDmsResponse {
  success: boolean;
  vehicle: {
    key_num?: string;
    frame_num?: string;
    engine_num?: string;
    model?: string;
    color?: string;
    cubic_capacity?: string;
    total_amount?: string;
    year_of_mfg?: string;
  };
  pdfs_saved: string[];
  error?: string | null;
}

const FILL_DMS_TIMEOUT_MS = 300000; // 5 min – Playwright opens browser, login, fill, search, download PDFs

export async function fillDms(req: FillDmsRequest): Promise<FillDmsResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_DMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-dms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    return res;
  } finally {
    clearTimeout(timeoutId);
  }
}

/** True if the error is from an aborted request (timeout or user abort). */
export function isFillDmsAbortError(err: unknown): boolean {
  if (err instanceof Error) {
    const m = err.message?.toLowerCase() ?? "";
    return m.includes("abort") || m.includes("aborted");
  }
  return false;
}
