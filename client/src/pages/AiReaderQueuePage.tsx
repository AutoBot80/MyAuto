import { useState, useEffect, useCallback } from "react";
import { useAiReaderQueue } from "../hooks/useAiReaderQueue";
import { AiReaderQueueTable } from "../components/AiReaderQueueTable";
import { getProcessStatus, startProcessAll, emptyAiReaderQueue } from "../api/aiReaderQueue";
import type { ProcessStatus } from "../types";

const POLL_INTERVAL_MS = 2000;

export function AiReaderQueuePage() {
  const { items, error, refetch } = useAiReaderQueue(true);
  const [processStatus, setProcessStatus] = useState<ProcessStatus>("waiting");
  const [processedCount, setProcessedCount] = useState(0);
  const [lastError, setLastError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [emptyError, setEmptyError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await getProcessStatus();
      setProcessStatus(res.status as ProcessStatus);
      setProcessedCount(res.processed_count);
      setLastError(res.last_error);
    } catch {
      setProcessStatus("waiting");
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    if (processStatus !== "running") return;
    const t = setInterval(() => {
      fetchStatus();
      refetch();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [processStatus, fetchStatus, refetch]);

  const handleStartProcess = async () => {
    setStartError(null);
    try {
      const res = await startProcessAll();
      if (res.started) {
        setProcessStatus("running");
        fetchStatus();
        refetch();
      } else {
        setStartError(res.message);
      }
    } catch (e) {
      setStartError(e instanceof Error ? e.message : "Failed to start process");
    }
  };

  const handleEmptyQueue = async () => {
    if (!confirm("Empty the entire AI reader queue?")) return;
    setEmptyError(null);
    try {
      const res = await emptyAiReaderQueue();
      refetch();
      if (res.deleted > 0) setStartError(null);
    } catch (e) {
      setEmptyError(e instanceof Error ? e.message : "Failed to empty queue");
    }
  };

  const statusLabel =
    processStatus === "running"
      ? "Running"
      : processStatus === "sleeping"
        ? "Sleeping"
        : "Waiting";

  return (
    <div className="ai-reader-queue-page">
      <div className="app-process-status-bar">
        <span className="app-process-status-label">Process status:</span>
        <span
          className={`app-process-status app-process-status--${processStatus}`}
          title={
            processStatus === "running"
              ? "Reading documents from queue"
              : processStatus === "sleeping"
                ? "Finished; all queued documents read"
                : "Idle; ready to start"
          }
        >
          {statusLabel}
        </span>
        {processStatus === "running" && (
          <span className="app-process-count">
            Processed: {processedCount}
          </span>
        )}
        {lastError && (
          <span className="app-process-last-error" title={lastError}>
            Last error: {lastError}
          </span>
        )}
        <button
          type="button"
          className="app-button app-button--primary"
          onClick={handleStartProcess}
          disabled={processStatus === "running"}
        >
          Process all
        </button>
        <button
          type="button"
          className="app-button"
          onClick={handleEmptyQueue}
          disabled={processStatus === "running"}
          title="Remove all items from the queue"
        >
          Empty queue
        </button>
      </div>
      {startError && (
        <div className="app-panel-status app-panel-status--error">
          {startError}
        </div>
      )}
      {emptyError && (
        <div className="app-panel-status app-panel-status--error">
          {emptyError}
        </div>
      )}
      <div className="ai-reader-queue-table-section">
        <AiReaderQueueTable
          items={items}
          error={error}
          onReprocess={refetch}
        />
      </div>
    </div>
  );
}
