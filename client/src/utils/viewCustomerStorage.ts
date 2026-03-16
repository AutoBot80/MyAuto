/** Persist View Customer search so it survives tab navigation. */

import type { CustomerSearchResult } from "../api/customerSearch";

const KEY = "viewCustomerPage";

export interface ViewCustomerStored {
  mobile: string;
  plateNum: string;
  result: CustomerSearchResult | null;
  selectedVehicleId: number | null;
}

function load(): ViewCustomerStored {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return { mobile: "", plateNum: "", result: null, selectedVehicleId: null };
    const parsed = JSON.parse(raw) as Partial<ViewCustomerStored>;
    return {
      mobile: typeof parsed.mobile === "string" ? parsed.mobile : "",
      plateNum: typeof parsed.plateNum === "string" ? parsed.plateNum : "",
      result: parsed.result && typeof parsed.result === "object" ? parsed.result : null,
      selectedVehicleId:
        typeof parsed.selectedVehicleId === "number" ? parsed.selectedVehicleId : null,
    };
  } catch {
    return { mobile: "", plateNum: "", result: null, selectedVehicleId: null };
  }
}

function save(data: ViewCustomerStored): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    // ignore
  }
}

export function loadViewCustomer(): ViewCustomerStored {
  return load();
}

export function saveViewCustomer(data: ViewCustomerStored): void {
  save(data);
}
