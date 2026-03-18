import { apiFetch } from "./client";
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
  subfolder: string
): Promise<ExtractedDetailsResponse | null> {
  const base = await import("./client").then((m) => m.getBaseUrl());
  const res = await fetch(
    `${base}/ai-reader-queue/extracted-details?subfolder=${encodeURIComponent(subfolder)}`
  );
  if (res.status === 404) return null;
  if (!res.ok) {
    const text = await res.text();
    let msg = text || `Failed (${res.status})`;
    try {
      const json = JSON.parse(text) as { detail?: string };
      if (json.detail) msg = json.detail;
    } catch {
      /* use msg as is */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<ExtractedDetailsResponse>;
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
  const base = await import("./client").then((m) => m.getBaseUrl());
  const res = await fetch(
    `${base}/ai-reader-queue/insurance-extraction?subfolder=${encodeURIComponent(subfolder)}`
  );
  if (!res.ok) throw new Error(await res.text().then((t) => t || `Failed (${res.status})`));
  return res.json();
}
