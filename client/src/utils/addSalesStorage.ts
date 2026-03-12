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
}

const DEFAULT: AddSalesStored = {
  aadharLast4: "",
  mobile: "",
  savedTo: null,
  uploadedFiles: [],
  uploadStatus: "",
  extractedVehicle: null,
};

import { normalizeVehicleDetails } from "./vehicleDetails";

function normalizeExtractedVehicle(val: unknown): AddSalesStored["extractedVehicle"] {
  return normalizeVehicleDetails(val);
}

export function loadAddSalesForm(): AddSalesStored {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT };
    const parsed = JSON.parse(raw) as Partial<AddSalesStored> & { extractedVehicle?: unknown };
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
