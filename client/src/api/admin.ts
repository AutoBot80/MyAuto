import { getAccessToken } from "../auth/token";
import { apiFetch } from "./client";

export interface AdminDataFoldersResponse {
  dealer_id: number;
  /** `local` = on-disk (dev: under My Auto.AI by default); `s3` = production bucket */
  storage_backend: "local" | "s3";
  upload_scans_path: string;
  ocr_output_path: string;
  challans_path: string;
}

export function getAdminDataFolders(dealerId: number) {
  const q = new URLSearchParams({ dealer_id: String(dealerId) });
  return apiFetch<AdminDataFoldersResponse>(`/admin/data-folders?${q.toString()}`);
}

export type AdminFolderRootApi = "upload_scans" | "ocr_output" | "challans";

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

export type AdminFolderFileOpenResult = {
  blobUrl: string;
  revoke: () => void;
  /** Presigned URL after API 307 — use in iframe/img; downloads open a new tab. */
  external: boolean;
};

/**
 * Load admin folder file with JWT. On S3 the API returns JSON ``{ url }`` (not HTTP redirect),
 * because cross-origin ``fetch(..., redirect: "manual")`` hides redirect targets (HTTP 0).
 */
export async function fetchAdminFolderFileBlobUrl(
  dealerId: number,
  root: AdminFolderRootApi,
  relativePath: string,
): Promise<AdminFolderFileOpenResult> {
  const base = import.meta.env.VITE_API_URL ?? "";
  const q = new URLSearchParams({
    dealer_id: String(dealerId),
    root,
    path: relativePath,
  });
  const headers = new Headers();
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const url = `${base}/admin/folder-file?${q.toString()}`;
  const res = await fetch(url, { headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Could not open file (HTTP ${res.status})`);
  }
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    const j = (await res.json()) as { url?: unknown };
    if (typeof j.url !== "string" || !j.url) {
      throw new Error("Server did not return a file URL.");
    }
    return { blobUrl: j.url, revoke: () => {}, external: true };
  }
  const blob = await res.blob();
  const blobUrl = URL.createObjectURL(blob);
  return {
    blobUrl,
    revoke: () => URL.revokeObjectURL(blobUrl),
    external: false,
  };
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

export interface AdminUsageDealerMatrixRow {
  dealer_id: number;
  dealer_name: string;
  /** Seven counts aligned with ``days`` (oldest → newest, IST). */
  counts: number[];
}

export interface AdminUsageDealerMatrixResponse {
  timezone_label: string;
  /** Seven ``YYYY-MM-DD`` IST dates, oldest first. */
  days: string[];
  sales: AdminUsageDealerMatrixRow[];
  challans: AdminUsageDealerMatrixRow[];
}

export function getAdminUsageDealerMatrix() {
  return apiFetch<AdminUsageDealerMatrixResponse>("/admin/usage-dealer-matrix");
}

export interface AdminProcessFailureLogRow {
  id: number;
  dealer_id: number;
  dealer_name: string;
  occurred_at_ist: string;
  process_label: string;
  customer_mobile: string | null;
  challan_book_num: string | null;
  challan_date: string | null;
  challan_batch_id: string | null;
  rto_queue_id: number | null;
  error_text: string;
  entity_dedupe_key: string;
}

export interface AdminProcessFailureLogListResponse {
  timezone_label: string;
  rows: AdminProcessFailureLogRow[];
}

export function getAdminFailureLogs(limit = 200) {
  const q = new URLSearchParams({ limit: String(limit) });
  return apiFetch<AdminProcessFailureLogListResponse>(`/admin/failure-logs?${q.toString()}`);
}
