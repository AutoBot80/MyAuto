import type { CustomerSearchResult } from "../api/customerSearch";

const STORAGE_KEY = "saathi_view_customer_v1";

export interface ViewCustomerStored {
  mobile: string;
  plateNum: string;
  result: CustomerSearchResult | null;
  selectedVehicleId: number | null;
}

export function loadViewCustomer(): ViewCustomerStored {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { mobile: "", plateNum: "", result: null, selectedVehicleId: null };
    }
    const parsed = JSON.parse(raw) as Partial<ViewCustomerStored>;
    return {
      mobile: typeof parsed.mobile === "string" ? parsed.mobile : "",
      plateNum: typeof parsed.plateNum === "string" ? parsed.plateNum : "",
      result: parsed.result ?? null,
      selectedVehicleId:
        typeof parsed.selectedVehicleId === "number" ? parsed.selectedVehicleId : null,
    };
  } catch {
    return { mobile: "", plateNum: "", result: null, selectedVehicleId: null };
  }
}

export function saveViewCustomer(state: ViewCustomerStored): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota or private mode — ignore
  }
}
