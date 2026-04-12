export type Page =
  | "add-sales"
  | "subdealer-challan"
  | "bulk-loads"
  | "customer-details"
  | "view-vehicles"
  | "rto-status"
  | "service-reminders"
  | "dealer-dashboard"
  | "admin-tools"
  | "admin-dealers"
  | "contact-us";

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

/** Wall-clock breakdown from `OcrService.process_uploaded_subfolder` (scans-v2). */
export interface UploadExtractionSectionTimings {
  /** Aadhaar front: DetectDocumentText — prefetch job time + any sync retry in the Aadhaar pipeline. */
  aadhar_textract_front_ms?: number;
  /** Aadhaar back: DetectDocumentText — prefetch job time + any sync retry in the Aadhaar pipeline. */
  aadhar_textract_back_ms?: number;
  /** Sales detail sheet: AnalyzeDocument FORMS — prefetch job time + sync call if prefetch disabled. */
  detail_sheet_textract_ms?: number;
  /** When `OCR_UPLOAD_PARALLEL_TEXTRACT` is true: wall time for AWS Textract prefetch jobs. */
  aws_textract_prefetch_ms?: number;
  parallel_aadhar_details_compile_ms?: number;
  merge_write_json_ms?: number;
  insurance_ms?: number;
  extras_raw_ms?: number;
  raw_ocr_finalize_ms?: number;
  total_ms?: number;
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
  /** Present for scans-v2 when extraction runs in the same request. */
  extraction?: {
    processed?: string[];
    errors?: string[];
    error?: string;
    details?: ExtractedDetailsResponse;
    section_timings_ms?: UploadExtractionSectionTimings;
  };
}

/** Structured vehicle details from Details sheet OCR (Textract forms). */
export interface ExtractedVehicleDetails {
  frame_no?: string;
  engine_no?: string;
  full_chassis?: string;
  full_engine?: string;
  model_colour?: string;
  key_no?: string;
  battery_no?: string;
  /** From DMS fill */
  model?: string;
  color?: string;
  cubic_capacity?: string;
  seating_capacity?: string;
  body_type?: string;
  vehicle_type?: string;
  num_cylinders?: string;
  vehicle_price?: string;
  year_of_mfg?: string;
}

/** Customer details: 15 granular fields (e.g. from QR) + optional legacy address. Full Aadhar shown only on frontend; DB stores last 4 only. */
export interface ExtractedCustomerDetails {
  aadhar_id?: string;
  name?: string;
  alt_phone_num?: string;
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
  dms_relation_prefix?: string;
  dms_contact_path?: string;
}

/** Build display address from granular fields (care of, house, street, location, state, pin). Uses existing address if set. */
export function buildDisplayAddress(c: ExtractedCustomerDetails | null | undefined): string {
  if (!c) return "—";
  if (c.address && String(c.address).trim()) return c.address.trim();
  const parts = [c.care_of, c.house, c.street, c.location, c.state, c.pin_code].filter((s) => s != null && String(s).trim() !== "");
  return parts.length > 0 ? parts.map((s) => String(s).trim()).join(", ") : "—";
}

export interface ExtractedInsuranceDetails {
  profession?: string;
  financier?: string;
  marital_status?: string;
  nominee_gender?: string;
  nominee_name?: string;
  nominee_age?: string;
  nominee_relationship?: string;
  /** From Insurance.jpg: insurer name (e.g. National Insurance) */
  insurer?: string;
  /** Policy number */
  policy_num?: string;
  /** Valid From / policy_from (dd-mm-yyyy or dd/mm/yyyy) */
  policy_from?: string;
  /** Valid To / policy_to */
  policy_to?: string;
  /** Gross Premium */
  premium?: string;
}

export interface ExtractedDetailsResponse {
  vehicle: ExtractedVehicleDetails;
  customer: ExtractedCustomerDetails | Record<string, string>;
  insurance?: ExtractedInsuranceDetails | Record<string, string>;
}
