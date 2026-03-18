import { apiFetch } from "./client";
import { DEALER_ID } from "./dealerId";

export interface BulkLoadRow {
  id: number;
  subfolder: string;
  file_name: string | null;
  mobile: string | null;
  name: string | null;
  folder_path: string | null;
  result_folder: string | null;
  status: string;
  error_message: string | null;
  action_taken?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ListBulkLoadsParams {
  status?: "Success" | "Error" | "Rejected" | "Processing";
  status_in?: string; // e.g. "Success,Error,Processing"
  date_from?: string; // dd-mm-yyyy
  date_to?: string; // dd-mm-yyyy
  dealer_id?: number;
}

export async function listBulkLoads(params?: ListBulkLoadsParams): Promise<BulkLoadRow[]> {
  const search = new URLSearchParams();
  search.set("dealer_id", String(params?.dealer_id ?? DEALER_ID));
  if (params?.status) search.set("status", params.status);
  if (params?.status_in) search.set("status_in", params.status_in);
  if (params?.date_from) search.set("date_from", params.date_from);
  if (params?.date_to) search.set("date_to", params.date_to);
  const qs = search.toString();
  return apiFetch<BulkLoadRow[]>(`/bulk-loads?${qs}`);
}

/** URL to browse a bulk folder (e.g. Rejected scans/Scan1_15032025) - opens in new tab (legacy) */
export function bulkFolderUrl(resultFolder: string, dealerId?: number): string {
  const base = import.meta.env.VITE_API_URL ?? "";
  const did = dealerId ?? DEALER_ID;
  return `${base}/bulk-loads/folder/${encodeURIComponent(resultFolder)}?dealer_id=${did}`;
}

/** Fetch file list for bulk folder (for in-app display) */
export async function getBulkFolderFiles(folderPath: string, dealerId?: number): Promise<{
  folder_path: string;
  files: { name: string; size: number }[];
}> {
  const did = dealerId ?? DEALER_ID;
  return apiFetch(`/bulk-loads/folder/${encodeURIComponent(folderPath)}/list?dealer_id=${did}`);
}

/** URL to download a file from a bulk folder */
export function bulkFileUrl(folderPath: string, filename: string, dealerId?: number): string {
  const base = import.meta.env.VITE_API_URL ?? "";
  const path = `${folderPath}/${filename}`;
  const did = dealerId ?? DEALER_ID;
  return `${base}/bulk-loads/file/${encodeURIComponent(path)}?dealer_id=${did}`;
}

/** Fetch file list for documents/uploaded scans subfolder (for Success rows in-app view) */
export async function getDocumentsFolderFiles(subfolder: string, dealerId?: number): Promise<{
  subfolder: string;
  files: { name: string; size: number }[];
}> {
  const did = dealerId ?? DEALER_ID;
  return apiFetch(`/documents/${encodeURIComponent(subfolder)}/list?dealer_id=${did}`);
}

export interface BulkLoadCounts {
  Success: number;
  Error: number;
  Processing: number;
  Rejected: number;
}

export async function getBulkLoadCounts(params?: {
  date_from?: string;
  date_to?: string;
  dealer_id?: number;
}): Promise<BulkLoadCounts> {
  const search = new URLSearchParams();
  search.set("dealer_id", String(params?.dealer_id ?? DEALER_ID));
  if (params?.date_from) search.set("date_from", params.date_from);
  if (params?.date_to) search.set("date_to", params.date_to);
  const qs = search.toString();
  return apiFetch<BulkLoadCounts>(`/bulk-loads/counts?${qs}`);
}

export async function clearBulkLoads(dealerId?: number): Promise<void> {
  const did = dealerId ?? DEALER_ID;
  await apiFetch(`/bulk-loads?dealer_id=${did}`, { method: "DELETE" });
}

export async function prepareReprocess(bulkLoadId: number, dealerId?: number): Promise<{
  bulk_load_id: number;
  subfolder: string;
  mobile: string | null;
  uploadedFiles: string[];
}> {
  const did = dealerId ?? DEALER_ID;
  return apiFetch(`/bulk-loads/${bulkLoadId}/prepare-reprocess?dealer_id=${did}`, { method: "POST" });
}

/** Mark an Error bulk load as Success after manual completion via Add Customer (Re-Try flow). */
export async function markBulkLoadSuccess(bulkLoadId: number, subfolder: string, dealerId?: number): Promise<{ ok: boolean }> {
  const did = dealerId ?? DEALER_ID;
  return apiFetch(
    `/bulk-loads/${bulkLoadId}/mark-success?subfolder=${encodeURIComponent(subfolder)}&dealer_id=${did}`,
    { method: "PATCH" }
  );
}

export async function getBulkLoadPendingCount(dealerId?: number): Promise<number> {
  const did = dealerId ?? DEALER_ID;
  const res = await apiFetch<{ pending: number }>(`/bulk-loads/pending-count?dealer_id=${did}`);
  return res.pending ?? 0;
}

export async function setBulkLoadActionTaken(bulkLoadId: number, actionTaken: boolean, dealerId?: number): Promise<void> {
  const did = dealerId ?? DEALER_ID;
  await apiFetch(`/bulk-loads/${bulkLoadId}/action-taken?action_taken=${actionTaken}&dealer_id=${did}`, { method: "PATCH" });
}
