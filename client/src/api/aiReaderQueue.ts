import { apiFetch } from "./client";
import type {
  AiReaderQueueItem,
  ProcessStatusResponse,
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
