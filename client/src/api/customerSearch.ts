import { getAccessToken } from "../auth/token";
import { apiFetch, getBaseUrl } from "./client";
import { DEALER_ID } from "./dealerId";

export interface InsuranceByVehicleEntry {
  insurer: string | null;
  policy_num: string | null;
  policy_from: string | null;
  policy_to: string | null;
  nominee_gender: string | null;
}

export interface CustomerSearchResult {
  found: boolean;
  customer: {
    customer_id: number;
    name: string;
    mobile: string | null;
    mobile_number: number | null;
    alt_phone_num: string | null;
    care_of: string | null;
    address: string | null;
    pin: string | null;
    city: string | null;
    state: string | null;
    date_of_birth: string | null;
    profession: string | null;
    financier: string | null;
    marital_status: string | null;
    gender: string | null;
  } | null;
  vehicles: Array<{
    vehicle_id: number;
    model: string | null;
    colour: string | null;
    plate_num: string | null;
    chassis: string | null;
    date_of_purchase: string | null;
    invoice_number: string | null;
    file_location: string | null;
  }>;
  insurance_by_vehicle: Record<number, InsuranceByVehicleEntry>;
  cpa_by_vehicle: Record<number, InsuranceByVehicleEntry>;
  message?: string;
}

export interface FormVahanViewResult {
  found: boolean;
  columns: string[];
  row: Record<string, string | number | null> | null;
}

export const VAHAN_DISPLAY_COLUMNS = [
  { key: "dealer_name", label: "Dealer Name" },
  { key: "rto", label: "RTO" },
  { key: "billing_date", label: "Billing Date" },
  { key: "model", label: "Vehicle Model" },
  { key: "chassis", label: "Chassis" },
  { key: "engine", label: "Engine" },
] as const;

export async function searchCustomer(opts: {
  mobile?: string | null;
  plate_num?: string | null;
  dealer_id?: number | null;
}): Promise<CustomerSearchResult> {
  const params = new URLSearchParams();
  if (opts.mobile?.trim()) params.set("mobile", opts.mobile.trim());
  if (opts.plate_num?.trim()) params.set("plate_num", opts.plate_num.trim());
  params.set("dealer_id", String(opts.dealer_id ?? DEALER_ID));
  const qs = params.toString();
  if (!qs) throw new Error("Provide at least mobile or plate_num");
  return apiFetch<CustomerSearchResult>(
    `/customer-search/search?${qs}`
  );
}

export async function getFormVahanView(customerId: number, vehicleId: number): Promise<FormVahanViewResult> {
  const params = new URLSearchParams({
    customer_id: String(customerId),
    vehicle_id: String(vehicleId),
  });
  return apiFetch<FormVahanViewResult>(`/customer-search/form-vahan?${params.toString()}`);
}

/** Get documents list URL for a subfolder */
export function getDocumentsListUrl(subfolder: string, dealerId?: number): string {
  const base = getBaseUrl().replace(/\/$/, "");
  return `${base}/documents/${encodeURIComponent(subfolder)}/list?dealer_id=${dealerId ?? DEALER_ID}`;
}

/** Get document file URL for opening in new tab */
export function getDocumentFileUrl(subfolder: string, filename: string, dealerId?: number): string {
  const base = getBaseUrl().replace(/\/$/, "");
  return `${base}/documents/${encodeURIComponent(subfolder)}/${encodeURIComponent(filename)}?dealer_id=${dealerId ?? DEALER_ID}`;
}

/**
 * Open a stored scan in a new tab. A raw anchor to getDocumentFileUrl() gets "Not authenticated"
 * because top-level navigation does not send the Bearer token; this fetches with Authorization
 * then opens a blob URL (same approach as openCreateInvoicePdfs.ts).
 */
export async function openDocumentFileInNewTab(
  subfolder: string,
  filename: string,
  dealerId?: number
): Promise<void> {
  const base = getBaseUrl().replace(/\/$/, "");
  const params = new URLSearchParams({ dealer_id: String(dealerId ?? DEALER_ID) });
  const url = `${base}/documents/${encodeURIComponent(subfolder)}/${encodeURIComponent(filename)}?${params}`;
  const headers = new Headers();
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(url, { headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const ct = res.headers.get("content-type") ?? "";
      if (ct.includes("application/json")) {
        const j = (await res.json()) as { detail?: unknown };
        if (typeof j.detail === "string") detail = j.detail;
        else if (Array.isArray(j.detail)) detail = JSON.stringify(j.detail);
      } else {
        const t = (await res.text()).trim();
        if (t) detail = t.length > 200 ? `${t.slice(0, 200)}…` : t;
      }
    } catch {
      /* keep detail */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  const w = window.open(objUrl, "_blank", "noopener,noreferrer");
  if (!w) {
    URL.revokeObjectURL(objUrl);
    throw new Error("Popup blocked — allow popups for this site to view documents.");
  }
  window.setTimeout(() => URL.revokeObjectURL(objUrl), 120_000);
}
