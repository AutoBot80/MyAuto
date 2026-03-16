import { apiFetch, getBaseUrl } from "./client";

export interface CustomerSearchResult {
  found: boolean;
  customer: {
    customer_id: number;
    name: string;
    mobile: string | null;
    mobile_number: number | null;
    address: string | null;
    pin: string | null;
    city: string | null;
    state: string | null;
    date_of_birth: string | null;
    profession: string | null;
    file_location: string | null;
    gender: string | null;
  } | null;
  vehicles: Array<{
    vehicle_id: number;
    model: string | null;
    colour: string | null;
    plate_num: string | null;
    chassis: string | null;
    date_of_purchase: string | null;
  }>;
  insurance_by_vehicle: Record<
    number,
    {
      insurer: string | null;
      policy_num: string | null;
      policy_from: string | null;
      policy_to: string | null;
    }
  >;
  message?: string;
}

export async function searchCustomer(opts: {
  mobile?: string | null;
  plate_num?: string | null;
}): Promise<CustomerSearchResult> {
  const params = new URLSearchParams();
  if (opts.mobile?.trim()) params.set("mobile", opts.mobile.trim());
  if (opts.plate_num?.trim()) params.set("plate_num", opts.plate_num.trim());
  const qs = params.toString();
  if (!qs) throw new Error("Provide at least mobile or plate_num");
  return apiFetch<CustomerSearchResult>(
    `/customer-search/search?${qs}`
  );
}

/** Get documents list URL for a subfolder */
export function getDocumentsListUrl(subfolder: string): string {
  const base = getBaseUrl().replace(/\/$/, "");
  return `${base}/documents/${encodeURIComponent(subfolder)}/list`;
}

/** Get document file URL for opening in new tab */
export function getDocumentFileUrl(subfolder: string, filename: string): string {
  const base = getBaseUrl().replace(/\/$/, "");
  return `${base}/documents/${encodeURIComponent(subfolder)}/${encodeURIComponent(filename)}`;
}
