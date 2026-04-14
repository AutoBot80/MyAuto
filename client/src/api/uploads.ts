import { getAccessToken } from "../auth/token";
import { getBaseUrl, throwMappedFetchError } from "./client";
import type { ExtractedDetailsResponse, UploadScansResponse } from "../types";
import { DEALER_ID } from "./dealerId";

const PROXY_TIMEOUT_HINT =
  "If you use the Vite dev server, upload + OCR can take several minutes — the proxy timeout was raised; restart `npm run dev` after pulling. " +
  "Otherwise increase reverse-proxy timeouts for POST /uploads.";

async function postUploadForm(path: string, form: FormData): Promise<UploadScansResponse> {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let res: Response;
  try {
    res = await fetch(`${getBaseUrl()}${path}`, {
      method: "POST",
      body: form,
      headers,
    });
  } catch (err) {
    throwMappedFetchError(err);
  }
  const text = await res.text();
  let data: UploadScansResponse & { error?: string };
  try {
    data = JSON.parse(text) as UploadScansResponse & { error?: string };
  } catch {
    const gateway = res.status === 502 || res.status === 503 || res.status === 504;
    throw new Error(
      gateway
        ? `Upload failed (${res.status}). ${PROXY_TIMEOUT_HINT}`
        : `Upload failed (${res.status}). Response was not JSON.`
    );
  }
  if (data.error) throw new Error(data.error);
  if (!res.ok) {
    const gateway = res.status === 502 || res.status === 503 || res.status === 504;
    throw new Error(
      gateway ? `Upload failed (${res.status}). ${PROXY_TIMEOUT_HINT}` : `Upload failed (${res.status})`
    );
  }
  return data;
}

export async function uploadScans(
  aadharLast4: string,
  files: File[],
  dealerId?: number
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("aadhar_last4", aadharLast4);
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  for (const f of files) form.append("files", f);
  return postUploadForm("/uploads/scans", form);
}

/** Subfolder = mobile_ddmmyy; files saved as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg */
export async function uploadScansV2(
  mobile: string,
  aadharScan: File,
  aadharBackScan: File,
  salesDetail: File,
  insuranceSheet?: File,
  dealerId?: number
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("mobile", mobile.trim());
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  form.append("aadhar_scan", aadharScan);
  form.append("aadhar_back", aadharBackScan);
  form.append("sales_detail", salesDetail);
  if (insuranceSheet) form.append("insurance_sheet", insuranceSheet);
  return postUploadForm("/uploads/scans-v2", form);
}

/** Pre-OCR + Textract: one PDF with Aadhaar + sales detail; subfolder mobile from OCR or optional ``customerMobile``. */
export async function uploadScansV2Consolidated(
  consolidatedPdf: File,
  dealerId?: number,
  customerMobile?: string
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  const m = (customerMobile ?? "").replace(/\D/g, "");
  if (m.length === 10) {
    form.append("mobile", m);
  }
  form.append("consolidated_pdf", consolidatedPdf);
  return postUploadForm("/uploads/scans-v2-consolidated", form);
}

export interface ConsolidatedUploadPartial {
  fragment: string;
  details: ExtractedDetailsResponse;
  savedTo: string;
}

/**
 * Consolidated PDF upload with **SSE**: Aadhaar and Details merges can arrive in either order;
 * ``onPartial`` fires as each fragment is persisted so the UI can fill fields before the slowest job finishes.
 */
export async function uploadScansV2ConsolidatedStream(
  consolidatedPdf: File,
  dealerId: number | undefined,
  customerMobile: string | undefined,
  onPartial: (p: ConsolidatedUploadPartial) => void
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  const m = (customerMobile ?? "").replace(/\D/g, "");
  if (m.length === 10) {
    form.append("mobile", m);
  }
  form.append("consolidated_pdf", consolidatedPdf);

  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  headers.set("Accept", "text/event-stream");

  let res: Response;
  try {
    res = await fetch(`${getBaseUrl()}/uploads/scans-v2-consolidated-stream`, {
      method: "POST",
      body: form,
      headers,
    });
  } catch (err) {
    throwMappedFetchError(err);
    throw new Error("unreachable");
  }

  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const j = JSON.parse(text) as { detail?: string };
      if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* plain text */
    }
    const gateway = res.status === 502 || res.status === 503 || res.status === 504;
    throw new Error(
      gateway ? `Upload failed (${res.status}). ${PROXY_TIMEOUT_HINT}` : `Upload failed (${res.status}): ${detail}`
    );
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("No response body from stream");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let final: UploadScansResponse | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, sep).trim();
      buffer = buffer.slice(sep + 2);
      if (!block.startsWith("data:")) continue;
      const jsonStr = block.replace(/^data:\s*/i, "").trim();
      if (!jsonStr) continue;
      const msg = JSON.parse(jsonStr) as {
        event: string;
        fragment?: string;
        details?: ExtractedDetailsResponse;
        saved_to?: string;
        result?: UploadScansResponse;
        message?: string;
      };
      if (msg.event === "error") {
        throw new Error(msg.message || "Stream error");
      }
      if (msg.event === "partial" && msg.details && msg.saved_to) {
        onPartial({
          fragment: msg.fragment ?? "",
          details: msg.details,
          savedTo: msg.saved_to,
        });
      }
      if (msg.event === "complete" && msg.result) {
        final = msg.result;
      }
    }
  }

  if (!final) {
    throw new Error("Stream ended without a complete event");
  }
  return final;
}

/** Assign split pages to document slots after pre-OCR failure (no Textract/OCR run). */
export async function applyConsolidatedManualFallback(
  sessionId: string,
  mobile: string,
  assignments: Record<string, string>,
  dealerId?: number
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("session_id", sessionId);
  form.append("mobile", mobile.replace(/\D/g, "").slice(0, 10));
  form.append("assignments_json", JSON.stringify(assignments));
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  return postUploadForm("/uploads/scans-v2-consolidated/manual-apply", form);
}

/** Fetch a manual-session page JPEG with auth; caller must ``URL.revokeObjectURL`` when done. */
export async function fetchManualSessionPageObjectUrl(
  sessionId: string,
  page1Based: number,
  dealerId?: number
): Promise<string> {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const q = dealerId != null ? `?dealer_id=${dealerId}` : "";
  let res: Response;
  try {
    res = await fetch(
      `${getBaseUrl()}/uploads/manual-session/${encodeURIComponent(sessionId)}/page/${page1Based}${q}`,
      { headers }
    );
  } catch (err) {
    throwMappedFetchError(err);
    throw new Error("unreachable");
  }
  if (!res.ok) {
    throw new Error(`Preview failed (${res.status})`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
