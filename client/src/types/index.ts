export type Page =
  | "add-sales"
  | "customer-details"
  | "rto-status"
  | "ai-reader-queue";

export type AddSalesStep =
  | "upload-scans"
  | "insurance"
  | "hero-dms"
  | "rto";

export interface AiReaderQueueItem {
  id: number;
  subfolder: string;
  filename: string;
  status: string;
  document_type?: string | null;
  classification_confidence?: number | null;
  created_at: string;
  updated_at: string;
}

export type ProcessStatus = "waiting" | "running" | "sleeping";

export interface ProcessStatusResponse {
  status: ProcessStatus;
  processed_count: number;
  last_error: string | null;
}

export interface UploadScansResponse {
  saved_count: number;
  saved_files?: string[];
  saved_to: string;
  queued_items?: Array<{
    id: number;
    subfolder: string;
    filename: string;
    status: string;
    created_at?: string;
  }>;
  error?: string;
}

/** Structured vehicle details from Details sheet OCR (Textract forms). */
export interface ExtractedVehicleDetails {
  frame_no?: string;
  engine_no?: string;
  model_colour?: string;
  key_no?: string;
  battery_no?: string;
}

/** Customer details: 15 granular fields (e.g. from QR) + optional legacy address. Full Aadhar shown only on frontend; DB stores last 4 only. */
export interface ExtractedCustomerDetails {
  aadhar_id?: string;
  name?: string;
  gender?: string;
  year_of_birth?: string;
  date_of_birth?: string;
  care_of?: string;
  house?: string;
  street?: string;
  location?: string;
  city?: string;
  post_office?: string;
  district?: string;
  sub_district?: string;
  state?: string;
  pin_code?: string;
  /** Legacy/constructed: from Vision or built from care_of + house + street + location */
  address?: string;
}

/** Build display address from granular fields (care of, house, street, location, state, pin). Uses existing address if set. */
export function buildDisplayAddress(c: ExtractedCustomerDetails | null | undefined): string {
  if (!c) return "—";
  if (c.address && String(c.address).trim()) return c.address.trim();
  const parts = [c.care_of, c.house, c.street, c.location, c.state, c.pin_code].filter((s) => s != null && String(s).trim() !== "");
  return parts.length > 0 ? parts.map((s) => String(s).trim()).join(", ") : "—";
}

export interface ExtractedDetailsResponse {
  vehicle: ExtractedVehicleDetails;
  customer: ExtractedCustomerDetails | Record<string, string>;
}
