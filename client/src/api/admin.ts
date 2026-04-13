import { apiFetch } from "./client";

export interface AdminDataFoldersResponse {
  dealer_id: number;
  upload_scans_path: string;
  ocr_output_path: string;
}

export function getAdminDataFolders(dealerId: number) {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  return apiFetch<AdminDataFoldersResponse>(`/admin/data-folders?${q.toString()}`);
}

export type AdminFolderRootApi = "upload_scans" | "ocr_output";

export interface AdminFolderEntry {
  name: string;
  kind: "file" | "dir";
  size?: number | null;
  modified_at: string;
}

export interface AdminFolderListResponse {
  root: AdminFolderRootApi;
  rel_path: string;
  dealer_id: number;
  current_folder_abs: string;
  items: AdminFolderEntry[];
}

export function listAdminFolderContents(dealerId: number, root: AdminFolderRootApi, relPath: string) {
  const q = new URLSearchParams({
    dealer_id: String(dealerId),
    root,
    rel_path: relPath,
  });
  return apiFetch<AdminFolderListResponse>(`/admin/folder-contents?${q.toString()}`);
}

export function adminFolderFileUrl(dealerId: number, root: AdminFolderRootApi, relativePath: string) {
  const base = import.meta.env.VITE_API_URL ?? "";
  const q = new URLSearchParams({
    dealer_id: String(dealerId),
    root,
    path: relativePath,
  });
  return `${base}/admin/folder-file?${q.toString()}`;
}

export interface ResetAllDataResponse {
  ok: boolean;
  message: string;
  truncated_count: number;
  truncated_tables: string[];
  preserved_tables: string[];
}

export function resetAllData() {
  return apiFetch<ResetAllDataResponse>("/admin/reset-all-data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation: "DELETE ALL DATA" }),
  });
}
