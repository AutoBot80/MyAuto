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
  created_at: string;
  updated_at: string;
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
