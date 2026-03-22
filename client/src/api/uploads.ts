import { getBaseUrl, throwMappedFetchError } from "./client";
import type { UploadScansResponse } from "../types";
import { DEALER_ID } from "./dealerId";

const PROXY_TIMEOUT_HINT =
  "If you use the Vite dev server, upload + OCR can take several minutes — the proxy timeout was raised; restart `npm run dev` after pulling. " +
  "Otherwise increase reverse-proxy timeouts for POST /uploads.";

async function postUploadForm(path: string, form: FormData): Promise<UploadScansResponse> {
  let res: Response;
  try {
    res = await fetch(`${getBaseUrl()}${path}`, {
      method: "POST",
      body: form,
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
