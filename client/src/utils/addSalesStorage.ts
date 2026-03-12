/** Persist Add Sales form so it survives navigation; clear only on "New". */

const KEY = "addSalesForm";

export interface AddSalesStored {
  aadharLast4: string;
  mobile: string;
  savedTo: string | null;
  uploadedFiles: string[];
  uploadStatus: string;
  extractedVehicle: {
    frame_no?: string;
    engine_no?: string;
    model_colour?: string;
    key_no?: string;
    battery_no?: string;
  } | null;
  extractedCustomer: {
    name?: string;
    address?: string;
    city?: string;
    state?: string;
    pin?: string;
  } | null;
}

const DEFAULT: AddSalesStored = {
  aadharLast4: "",
  mobile: "",
  savedTo: null,
  uploadedFiles: [],
  uploadStatus: "",
  extractedVehicle: null,
  extractedCustomer: null,
};

import { normalizeVehicleDetails } from "./vehicleDetails";

function normalizeExtractedVehicle(val: unknown): AddSalesStored["extractedVehicle"] {
  return normalizeVehicleDetails(val);
}

export function loadAddSalesForm(): AddSalesStored {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT };
    const parsed = JSON.parse(raw) as Partial<AddSalesStored> & { extractedVehicle?: unknown; extractedCustomer?: unknown };
    const cust = parsed.extractedCustomer;
    const extractedCustomer =
      cust && typeof cust === "object" && !Array.isArray(cust)
        ? {
            name: typeof (cust as Record<string, unknown>).name === "string" ? (cust as Record<string, string>).name : "",
            address: typeof (cust as Record<string, unknown>).address === "string" ? (cust as Record<string, string>).address : "",
            city: typeof (cust as Record<string, unknown>).city === "string" ? (cust as Record<string, string>).city : "",
            state: typeof (cust as Record<string, unknown>).state === "string" ? (cust as Record<string, string>).state : "",
            pin: typeof (cust as Record<string, unknown>).pin === "string" ? (cust as Record<string, string>).pin : "",
          }
        : null;
    return {
      aadharLast4: typeof parsed.aadharLast4 === "string" ? parsed.aadharLast4 : "",
      mobile: typeof parsed.mobile === "string" ? parsed.mobile : "",
      savedTo:
        parsed.savedTo === null || parsed.savedTo === undefined
          ? null
          : typeof parsed.savedTo === "string"
            ? parsed.savedTo
            : "",
      uploadedFiles: Array.isArray(parsed.uploadedFiles) ? parsed.uploadedFiles : [],
      uploadStatus: typeof parsed.uploadStatus === "string" ? parsed.uploadStatus : "",
      extractedVehicle: normalizeExtractedVehicle(parsed.extractedVehicle),
      extractedCustomer: extractedCustomer && (extractedCustomer.name || extractedCustomer.address || extractedCustomer.city || extractedCustomer.state || extractedCustomer.pin) ? extractedCustomer : null,
    };
  } catch {
    return { ...DEFAULT };
  }
}

export function saveAddSalesForm(data: AddSalesStored): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    // ignore
  }
}

export function clearAddSalesForm(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
