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

export interface ExtractedDetailsResponse {
  vehicle: ExtractedVehicleDetails;
  customer: Record<string, string>;
}
