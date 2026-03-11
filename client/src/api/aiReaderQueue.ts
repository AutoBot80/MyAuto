import { apiFetch } from "./client";
import type { AiReaderQueueItem } from "../types";

export async function getAiReaderQueue(
  limit = 200
): Promise<AiReaderQueueItem[]> {
  return apiFetch<AiReaderQueueItem[]>(`/ai-reader-queue?limit=${limit}`);
}
