/** Persist Add Sales form so it survives navigation; clear only on "New". */

const KEY = "addSalesForm";

export interface AddSalesStored {
  mobile: string;
  profession: string;
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
  extractedCustomer: import("../types").ExtractedCustomerDetails | null;
  extractedInsurance: { nominee_name?: string; nominee_age?: string; nominee_relationship?: string } | null;
}

const DEFAULT: AddSalesStored = {
  mobile: "",
  profession: "",
  savedTo: null,
  uploadedFiles: [],
  uploadStatus: "",
  extractedVehicle: null,
  extractedCustomer: null,
  extractedInsurance: null,
};

import type { ExtractedCustomerDetails } from "../types";
import { normalizeVehicleDetails } from "./vehicleDetails";

const CUSTOMER_KEYS: (keyof ExtractedCustomerDetails)[] = [
  "aadhar_id", "name", "gender", "year_of_birth", "date_of_birth",
  "care_of", "house", "street", "location", "city", "post_office",
  "district", "sub_district", "state", "pin_code", "address",
];

function normalizeExtractedVehicle(val: unknown): AddSalesStored["extractedVehicle"] {
  return normalizeVehicleDetails(val);
}

function normalizeExtractedCustomer(val: unknown): ExtractedCustomerDetails | null {
  if (val == null || typeof val !== "object" || Array.isArray(val)) return null;
  const o = val as Record<string, unknown>;
  const out: ExtractedCustomerDetails = {};
  for (const k of CUSTOMER_KEYS) {
    const v = o[k];
    if (v != null && typeof v === "string" && v.trim() !== "") out[k] = v.trim();
  }
  if (!out.pin_code && o.pin != null && String(o.pin).trim() !== "") out.pin_code = String(o.pin).trim();
  return Object.keys(out).length > 0 ? out : null;
}

function hasAnyCustomerValue(c: ExtractedCustomerDetails): boolean {
  return CUSTOMER_KEYS.some((k) => c[k] != null && String(c[k]).trim() !== "");
}

export function loadAddSalesForm(): AddSalesStored {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT };
    const parsed = JSON.parse(raw) as Partial<AddSalesStored> & { extractedVehicle?: unknown; extractedCustomer?: unknown };
    const cust = parsed.extractedCustomer;
    const extractedCustomer = normalizeExtractedCustomer(cust);
    return {
      mobile: typeof parsed.mobile === "string" ? parsed.mobile : "",
      profession: typeof parsed.profession === "string" ? parsed.profession : "",
      savedTo:
        parsed.savedTo === null || parsed.savedTo === undefined
          ? null
          : typeof parsed.savedTo === "string"
            ? parsed.savedTo
            : "",
      uploadedFiles: Array.isArray(parsed.uploadedFiles) ? parsed.uploadedFiles : [],
      uploadStatus: typeof parsed.uploadStatus === "string" ? parsed.uploadStatus : "",
      extractedVehicle: normalizeExtractedVehicle(parsed.extractedVehicle),
      extractedCustomer: extractedCustomer && hasAnyCustomerValue(extractedCustomer) ? extractedCustomer : null,
      extractedInsurance:
        parsed.extractedInsurance != null && typeof parsed.extractedInsurance === "object" && !Array.isArray(parsed.extractedInsurance)
          ? (parsed.extractedInsurance as AddSalesStored["extractedInsurance"])
          : null,
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
