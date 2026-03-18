import { getBaseUrl } from "./client";
import type { UploadScansResponse } from "../types";
import { DEALER_ID } from "./dealerId";

export async function uploadScans(
  aadharLast4: string,
  files: File[],
  dealerId?: number
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("aadhar_last4", aadharLast4);
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  for (const f of files) form.append("files", f);
  const res = await fetch(`${getBaseUrl()}/uploads/scans`, {
    method: "POST",
    body: form,
  });
  const data = (await res.json()) as UploadScansResponse & { error?: string };
  if (data.error) throw new Error(data.error);
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return data;
}

/** Subfolder = mobile_ddmmyy; files saved as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg */
export async function uploadScansV2(
  mobile: string,
  aadharScan: File,
  aadharBackScan: File,
  salesDetail: File,
  insuranceSheet?: File,
  financingDoc?: File,
  dealerId?: number
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("mobile", mobile.trim());
  form.append("dealer_id", String(dealerId ?? DEALER_ID));
  form.append("aadhar_scan", aadharScan);
  form.append("aadhar_back", aadharBackScan);
  form.append("sales_detail", salesDetail);
  if (insuranceSheet) form.append("insurance_sheet", insuranceSheet);
  if (financingDoc) form.append("financing_doc", financingDoc);
  const res = await fetch(`${getBaseUrl()}/uploads/scans-v2`, {
    method: "POST",
    body: form,
  });
  const data = (await res.json()) as UploadScansResponse & { error?: string };
  if (data.error) throw new Error(data.error);
  if (!res.ok) throw new Error(`Upload failed (${res.status})`);
  return data;
}
