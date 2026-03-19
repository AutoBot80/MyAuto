import { apiFetch } from "./client";

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
