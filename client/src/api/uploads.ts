import { getBaseUrl } from "./client";
import type { UploadScansResponse } from "../types";

export async function uploadScans(
  aadharLast4: string,
  files: File[]
): Promise<UploadScansResponse> {
  const form = new FormData();
  form.append("aadhar_last4", aadharLast4);
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
