import { useState } from "react";
import type { AiReaderQueueItem } from "../types";
import { reprocessQueueItem } from "../api/aiReaderQueue";

interface AiReaderQueueTableProps {
  items: AiReaderQueueItem[];
  error: string;
  onReprocess?: () => void;
}

export function AiReaderQueueTable({
  items,
  error,
  onReprocess,
}: AiReaderQueueTableProps) {
  const [reprocessId, setReprocessId] = useState<number | null>(null);

  const handleReprocess = async (id: number) => {
    setReprocessId(id);
    try {
      await reprocessQueueItem(id);
      onReprocess?.();
    } finally {
      setReprocessId(null);
    }
  };

  return (
    <div className="ai-reader-queue-table-root">
      <h2>AI Reader Queue</h2>
      {error ? <div className="app-panel-status">{error}</div> : null}
      <div className="app-table-wrap">
        <table className="app-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Subfolder</th>
              <th>File</th>
              <th>Document Type</th>
              <th>Confidence</th>
              <th>Status</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.id}>
                <td>{it.id}</td>
                <td>{it.subfolder}</td>
                <td>{it.filename}</td>
                <td>{it.document_type ?? "—"}</td>
                <td>
                  {it.classification_confidence != null
                    ? `${(it.classification_confidence * 100).toFixed(0)}%`
                    : "—"}
                </td>
                <td>{it.status}</td>
                <td>{new Date(it.created_at).toLocaleString()}</td>
                <td>
                  <button
                    type="button"
                    className="app-button app-button--small"
                    onClick={() => handleReprocess(it.id)}
                    disabled={reprocessId === it.id}
                    title="Re-queue for processing (classify + OCR)"
                  >
                    {reprocessId === it.id ? "…" : "Re-process"}
                  </button>
                </td>
              </tr>
            ))}
            {items.length === 0 ? (
              <tr>
                <td colSpan={8} className="app-table-empty">
                  No queued documents yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
