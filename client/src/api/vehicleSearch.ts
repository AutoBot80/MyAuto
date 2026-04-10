import { apiFetch } from "./client";
import { DEALER_ID } from "./dealerId";

export interface VehicleSearchMatch {
  vehicle_master: Record<string, string | number | null>;
  /** Rows from `vehicle_inventory_master` where chassis/engine match (any dealer). */
  vehicle_inventory: Array<Record<string, string | number | null>>;
  sales_master: Record<string, string | number | null> | null;
  /** Committed challan lines (`challan_master` / `challan_details`). */
  challans: Array<Record<string, string | number | null>>;
}

export interface VehicleSearchResult {
  found: boolean;
  matches: VehicleSearchMatch[];
  message?: string;
}

export async function searchVehicles(opts: {
  chassis?: string | null;
  engine?: string | null;
  dealer_id?: number | null;
}): Promise<VehicleSearchResult> {
  const c = opts.chassis?.trim();
  const e = opts.engine?.trim();
  if (!c && !e) {
    throw new Error("Provide at least chassis or engine");
  }
  const params = new URLSearchParams();
  if (c) params.set("chassis", c);
  if (e) params.set("engine", e);
  params.set("dealer_id", String(opts.dealer_id ?? DEALER_ID));
  return apiFetch<VehicleSearchResult>(`/vehicle-search/search?${params.toString()}`);
}
