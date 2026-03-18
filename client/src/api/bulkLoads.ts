import { apiFetch } from "./client";

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
}

export async function listBulkLoads(params?: ListBulkLoadsParams): Promise<BulkLoadRow[]> {
  const search = new URLSearchParams();
  if (params?.status) search.set("status", params.status);
  if (params?.status_in) search.set("status_in", params.status_in);
  if (params?.date_from) search.set("date_from", params.date_from);
  if (params?.date_to) search.set("date_to", params.date_to);
  const qs = search.toString();
  return apiFetch<BulkLoadRow[]>(`/bulk-loads${qs ? "?" + qs : ""}`);
}

/** URL to browse a bulk folder (e.g. Rejected scans/Scan1_15032025) - opens in new tab (legacy) */
export function bulkFolderUrl(resultFolder: string): string {
  return `/bulk-loads/folder/${encodeURIComponent(resultFolder)}`;
}

/** Fetch file list for bulk folder (for in-app display) */
export async function getBulkFolderFiles(folderPath: string): Promise<{
  folder_path: string;
  files: { name: string; size: number }[];
}> {
  return apiFetch(`/bulk-loads/folder/${encodeURIComponent(folderPath)}/list`);
}

/** URL to download a file from a bulk folder */
export function bulkFileUrl(folderPath: string, filename: string): string {
  const path = `${folderPath}/${filename}`;
  return `/bulk-loads/file/${encodeURIComponent(path)}`;
}

/** Fetch file list for documents/uploaded scans subfolder (for Success rows in-app view) */
export async function getDocumentsFolderFiles(subfolder: string): Promise<{
  subfolder: string;
  files: { name: string; size: number }[];
}> {
  return apiFetch(`/documents/${encodeURIComponent(subfolder)}/list`);
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
}): Promise<BulkLoadCounts> {
  const search = new URLSearchParams();
  if (params?.date_from) search.set("date_from", params.date_from);
  if (params?.date_to) search.set("date_to", params.date_to);
  const qs = search.toString();
  return apiFetch<BulkLoadCounts>(`/bulk-loads/counts${qs ? "?" + qs : ""}`);
}

export async function clearBulkLoads(): Promise<void> {
  await apiFetch("/bulk-loads", { method: "DELETE" });
}

export async function prepareReprocess(bulkLoadId: number): Promise<{
  bulk_load_id: number;
  subfolder: string;
  mobile: string | null;
  uploadedFiles: string[];
}> {
  return apiFetch(`/bulk-loads/${bulkLoadId}/prepare-reprocess`, { method: "POST" });
}

/** Mark an Error bulk load as Success after manual completion via Add Customer (Re-Try flow). */
export async function markBulkLoadSuccess(bulkLoadId: number, subfolder: string): Promise<{ ok: boolean }> {
  return apiFetch(
    `/bulk-loads/${bulkLoadId}/mark-success?subfolder=${encodeURIComponent(subfolder)}`,
    { method: "PATCH" }
  );
}

export async function getBulkLoadPendingCount(): Promise<number> {
  const res = await apiFetch<{ pending: number }>("/bulk-loads/pending-count");
  return res.pending ?? 0;
}

export async function setBulkLoadActionTaken(bulkLoadId: number, actionTaken: boolean): Promise<void> {
  await apiFetch(`/bulk-loads/${bulkLoadId}/action-taken?action_taken=${actionTaken}`, { method: "PATCH" });
}
