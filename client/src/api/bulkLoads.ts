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
  created_at: string;
  updated_at: string;
}

export async function listBulkLoads(status?: "Success" | "Error" | "Processing"): Promise<BulkLoadRow[]> {
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<BulkLoadRow[]>(`/bulk-loads${params}`);
}

export async function clearBulkLoads(): Promise<void> {
  await apiFetch("/bulk-loads", { method: "DELETE" });
}

export async function prepareReprocess(bulkLoadId: number): Promise<{ subfolder: string; mobile: string | null }> {
  return apiFetch(`/bulk-loads/${bulkLoadId}/prepare-reprocess`, { method: "POST" });
}
