import { useState, useEffect, useCallback } from "react";
import { getAiReaderQueue } from "../api/aiReaderQueue";
import type { AiReaderQueueItem } from "../types";

export function useAiReaderQueue(enabled: boolean) {
  const [items, setItems] = useState<AiReaderQueueItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!enabled) return;
    try {
      setError(null);
      const data = await getAiReaderQueue();
      setItems(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load AI reader queue");
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    void refetch();
  }, [enabled, refetch]);

  return { items, error, refetch };
}
