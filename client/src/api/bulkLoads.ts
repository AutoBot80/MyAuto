import { apiFetch } from "./client";

export interface BulkLoadRow {
  id: number;
  subfolder: string;
  mobile: string | null;
  name: string | null;
  folder_path: string | null;
  status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export async function listBulkLoads(status?: "Success" | "Error"): Promise<BulkLoadRow[]> {
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<BulkLoadRow[]>(`/bulk-loads${params}`);
}
