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
  plate_num?: string | null;
  dealer_id?: number | null;
}): Promise<VehicleSearchResult> {
  const c = opts.chassis?.trim();
  const e = opts.engine?.trim();
  const p = opts.plate_num?.trim();
  if (!c && !e && !p) {
    throw new Error("Provide at least chassis, engine, or plate number");
  }
  const params = new URLSearchParams();
  if (c) params.set("chassis", c);
  if (e) params.set("engine", e);
  if (p) params.set("plate_num", p);
  params.set("dealer_id", String(opts.dealer_id ?? DEALER_ID));
  return apiFetch<VehicleSearchResult>(`/vehicle-search/search?${params.toString()}`);
}
