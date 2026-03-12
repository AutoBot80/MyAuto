import { useEffect, useState, useCallback } from "react";
import { getAiReaderQueue } from "../api/aiReaderQueue";
import type { AiReaderQueueItem } from "../types";

export function useAiReaderQueue(active: boolean, pollIntervalMs = 5000) {
  const [items, setItems] = useState<AiReaderQueueItem[]>([]);
  const [error, setError] = useState<string>("");

  const refetch = useCallback(async () => {
    try {
      setError("");
      const data = await getAiReaderQueue();
      setItems(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load queue.");
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    async function load() {
      try {
        const data = await getAiReaderQueue();
        if (!cancelled) setItems(data);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to load queue.");
      }
    }

    load();
    const t = window.setInterval(load, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [active, pollIntervalMs]);

  return { items, error, refetch };
}
