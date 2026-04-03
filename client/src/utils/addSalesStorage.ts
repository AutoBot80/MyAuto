/** Persist Add Sales form so it survives navigation; clear only on "New". */

const KEY = "addSalesForm";

export interface AddSalesStored {
  mobile: string;
  savedTo: string | null;
  uploadedFiles: string[];
  uploadStatus: string;
  /** When set, this bulk load was opened via Re-Try; mark Success on submit. */
  reprocessBulkLoadId?: number;
  dmsScrapedVehicle: { key_no?: string; frame_no?: string; engine_no?: string; full_chassis?: string; full_engine?: string; model?: string; color?: string; cubic_capacity?: string; seating_capacity?: string; body_type?: string; vehicle_type?: string; num_cylinders?: string; vehicle_price?: string; year_of_mfg?: string } | null;
  hasSubmittedInfo: boolean;
  lastSubmittedCustomerId: number | null;
  lastSubmittedVehicleId: number | null;
  /** Draft ``add_sales_staging`` id from last successful Submit Info; passed to Create Invoice. */
  lastStagingId: string | null;
  extractedVehicle: {
    frame_no?: string;
    engine_no?: string;
    model_colour?: string;
    key_no?: string;
    battery_no?: string;
  } | null;
  extractedCustomer: import("../types").ExtractedCustomerDetails | null;
  extractedInsurance: {
    profession?: string;
    financier?: string;
    marital_status?: string;
    nominee_gender?: string;
    nominee_name?: string;
    nominee_age?: string;
    nominee_relationship?: string;
    insurer?: string;
    policy_num?: string;
    policy_from?: string;
    policy_to?: string;
    premium?: string;
  } | null;
}

const DEFAULT: AddSalesStored = {
  mobile: "",
  savedTo: null,
  uploadedFiles: [],
  uploadStatus: "",
  dmsScrapedVehicle: null,
  hasSubmittedInfo: false,
  lastSubmittedCustomerId: null,
  lastSubmittedVehicleId: null,
  lastStagingId: null,
  extractedVehicle: null,
  extractedCustomer: null,
  extractedInsurance: null,
};

import type { ExtractedCustomerDetails } from "../types";
import { normalizeVehicleDetails } from "./vehicleDetails";
import { sanitizeNomineeAgeInput, sanitizeOptionalFormField } from "./formFieldSanitize";

const CUSTOMER_KEYS: (keyof ExtractedCustomerDetails)[] = [
  "aadhar_id", "name", "gender", "year_of_birth", "date_of_birth",
  "care_of", "house", "street", "location", "city", "post_office",
  "district", "sub_district", "state", "pin_code", "address",
  "dms_relation_prefix", "dms_contact_path",
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
    if (v != null && typeof v === "string" && v.trim() !== "") {
      const s = sanitizeOptionalFormField(v.trim());
      if (s) out[k] = s;
    }
  }
  if (!out.pin_code && o.pin != null && String(o.pin).trim() !== "") {
    const p = sanitizeOptionalFormField(String(o.pin).trim());
    if (p) out.pin_code = p;
  }
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
    const dmsV = parsed.dmsScrapedVehicle;
    const dmsScrapedVehicle =
      dmsV != null && typeof dmsV === "object" && !Array.isArray(dmsV)
        ? (dmsV as AddSalesStored["dmsScrapedVehicle"])
        : null;
    return {
      mobile: typeof parsed.mobile === "string" ? parsed.mobile : "",
      savedTo:
        parsed.savedTo === null || parsed.savedTo === undefined
          ? null
          : typeof parsed.savedTo === "string"
            ? parsed.savedTo
            : "",
      uploadedFiles: Array.isArray(parsed.uploadedFiles) ? parsed.uploadedFiles : [],
      uploadStatus: typeof parsed.uploadStatus === "string" ? parsed.uploadStatus : "",
      reprocessBulkLoadId: typeof parsed.reprocessBulkLoadId === "number" ? parsed.reprocessBulkLoadId : undefined,
      dmsScrapedVehicle,
      hasSubmittedInfo: Boolean(parsed.hasSubmittedInfo),
      lastSubmittedCustomerId: typeof parsed.lastSubmittedCustomerId === "number" ? parsed.lastSubmittedCustomerId : null,
      lastSubmittedVehicleId: typeof parsed.lastSubmittedVehicleId === "number" ? parsed.lastSubmittedVehicleId : null,
      lastStagingId: typeof parsed.lastStagingId === "string" && parsed.lastStagingId.trim() ? parsed.lastStagingId.trim() : null,
      extractedVehicle: normalizeExtractedVehicle(parsed.extractedVehicle),
      extractedCustomer: extractedCustomer && hasAnyCustomerValue(extractedCustomer) ? extractedCustomer : null,
      extractedInsurance: (() => {
        if (
          parsed.extractedInsurance == null ||
          typeof parsed.extractedInsurance !== "object" ||
          Array.isArray(parsed.extractedInsurance)
        ) {
          return null;
        }
        const ins = {
          ...(parsed.extractedInsurance as AddSalesStored["extractedInsurance"]),
          profession:
            (parsed.extractedInsurance as any)?.profession ??
            (typeof (parsed as any).profession === "string" ? (parsed as any).profession : undefined),
        } as Record<string, unknown>;
        for (const key of [
          "profession",
          "nominee_name",
          "nominee_relationship",
          "nominee_gender",
          "insurer",
          "policy_num",
          "policy_from",
          "policy_to",
          "premium",
          "marital_status",
          "financier",
        ] as const) {
          const v = ins[key];
          if (v != null && typeof v === "string" && String(v).trim() !== "") {
            const s = sanitizeOptionalFormField(v);
            if (s) ins[key] = s;
            else delete ins[key];
          }
        }
        const na = ins.nominee_age;
        if (na != null && String(na).trim() !== "") {
          const s = sanitizeNomineeAgeInput(String(na));
          if (s) ins.nominee_age = s;
          else delete ins.nominee_age;
        }
        return ins as AddSalesStored["extractedInsurance"];
      })(),
    };
  } catch {
    return { ...DEFAULT };
  }
}

export function saveAddSalesForm(data: Partial<AddSalesStored>): void {
  try {
    const current = loadAddSalesForm();
    const merged = { ...current, ...data };
    sessionStorage.setItem(KEY, JSON.stringify(merged));
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
