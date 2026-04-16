import { apiFetch, ApiHttpError } from "./client";
import type {
  AiReaderQueueItem,
  ProcessStatusResponse,
  ExtractedDetailsResponse,
} from "../types";

export async function getAiReaderQueue(
  limit = 200
): Promise<AiReaderQueueItem[]> {
  return apiFetch<AiReaderQueueItem[]>(`/ai-reader-queue?limit=${limit}`);
}

export async function getProcessStatus(): Promise<ProcessStatusResponse> {
  return apiFetch<ProcessStatusResponse>("/ai-reader-queue/process-status");
}

export async function startProcessAll(): Promise<{
  started: boolean;
  message: string;
}> {
  return apiFetch<{ started: boolean; message: string }>(
    "/ai-reader-queue/process-all",
    { method: "POST" }
  );
}

export async function emptyAiReaderQueue(): Promise<{ ok: boolean; deleted: number }> {
  return apiFetch<{ ok: boolean; deleted: number }>(
    "/ai-reader-queue/empty",
    { method: "POST" }
  );
}

export async function reprocessQueueItem(
  itemId: number
): Promise<{ ok: boolean; id: number; message: string }> {
  return apiFetch<{ ok: boolean; id: number; message: string }>(
    `/ai-reader-queue/${itemId}/reprocess`,
    { method: "POST" }
  );
}

/** Get structured extracted details (vehicle, customer) for a subfolder. Returns null if not found (e.g. not yet processed). */
export async function getExtractedDetails(
  subfolder: string,
  dealerId?: number
): Promise<ExtractedDetailsResponse | null> {
  const params = new URLSearchParams();
  params.set("subfolder", subfolder);
  if (dealerId != null) params.set("dealer_id", String(dealerId));
  try {
    return await apiFetch<ExtractedDetailsResponse>(
      `/ai-reader-queue/extracted-details?${params.toString()}`
    );
  } catch (e) {
    if (e instanceof ApiHttpError && e.status === 404) return null;
    throw e;
  }
}

/** Debug: get what Textract extracted from Insurance.jpg (raw + parsed) and file status. */
export async function getInsuranceExtraction(subfolder: string): Promise<{
  subfolder: string;
  insurance_jpg_exists: boolean;
  ocr_files: string[];
  insurance_from_details: Record<string, string> | null;
  insurance_ocr_json: Record<string, string> | null;
  raw_ocr_txt: string | null;
  insurance_txt_preview?: string;
}> {
  const params = new URLSearchParams();
  params.set("subfolder", subfolder);
  return apiFetch(`/ai-reader-queue/insurance-extraction?${params.toString()}`);
}
