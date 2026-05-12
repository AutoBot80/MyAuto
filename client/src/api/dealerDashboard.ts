import { apiFetch } from "./client";

export type DealerDashboardSummary = {
  timezone_label: string;
  days: string[];
  rto_queued_count: number;
  counter_sales_counts: number[];
  is_principal_dealer: boolean;
  subdealer_sales_counts: number[] | null;
  subdealer_challan_counts: number[] | null;
};

export type SubdealerSalesMatrixRow = {
  dealer_id: number;
  dealer_name: string;
  counts: number[];
};

export type SubdealerSalesMatrixResponse = {
  timezone_label: string;
  days: string[];
  rows: SubdealerSalesMatrixRow[];
};

export type ChallansByIstDayResponse = {
  timezone_label: string;
  ist_date: string;
  rows: Record<string, unknown>[];
};

export async function getDealerDashboardSummary(dealerId: number): Promise<DealerDashboardSummary> {
  return apiFetch<DealerDashboardSummary>(`/dealers/${dealerId}/dashboard/summary`);
}

export async function getDealerDashboardSubdealerSalesMatrix(
  dealerId: number,
): Promise<SubdealerSalesMatrixResponse> {
  return apiFetch<SubdealerSalesMatrixResponse>(`/dealers/${dealerId}/dashboard/subdealer-sales-matrix`);
}

export type ChallansRecentListResponse = {
  timezone_label: string;
  limit: number;
  rows: Record<string, unknown>[];
};

export type ChallansFilteredListResponse = {
  timezone_label: string;
  days: number;
  ist_start: string;
  ist_end: string;
  dealer_to_id: number | null;
  rows: Record<string, unknown>[];
};

export async function getDealerDashboardChallansFiltered(
  dealerId: number,
  opts: { days: 7 | 15 | 30; dealerToId?: number | null },
): Promise<ChallansFilteredListResponse> {
  const q = new URLSearchParams({ days: String(opts.days) });
  if (opts.dealerToId != null && !Number.isNaN(Number(opts.dealerToId))) {
    q.set("dealer_to_id", String(opts.dealerToId));
  }
  return apiFetch<ChallansFilteredListResponse>(
    `/dealers/${dealerId}/dashboard/challans-filtered?${q.toString()}`,
  );
}

export async function getDealerDashboardChallansRecent(
  dealerId: number,
  limit = 5,
): Promise<ChallansRecentListResponse> {
  const q = new URLSearchParams({ limit: String(limit) });
  return apiFetch<ChallansRecentListResponse>(
    `/dealers/${dealerId}/dashboard/challans-recent?${q.toString()}`,
  );
}

export async function getDealerDashboardChallansByIstDay(
  dealerId: number,
  istDateIso: string,
): Promise<ChallansByIstDayResponse> {
  const q = new URLSearchParams({ date: istDateIso });
  return apiFetch<ChallansByIstDayResponse>(
    `/dealers/${dealerId}/dashboard/challans-by-ist-day?${q.toString()}`,
  );
}
