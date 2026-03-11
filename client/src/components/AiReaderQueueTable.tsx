import type { AiReaderQueueItem } from "../types";

interface AiReaderQueueTableProps {
  items: AiReaderQueueItem[];
  error: string;
}

export function AiReaderQueueTable({ items, error }: AiReaderQueueTableProps) {
  return (
    <div>
      <h2>AI Reader Queue</h2>
      {error ? <div className="app-panel-status">{error}</div> : null}
      <div className="app-table-wrap">
        <table className="app-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Subfolder</th>
              <th>File</th>
              <th>Status</th>
              <th>Created</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.id}>
                <td>{it.id}</td>
                <td>{it.subfolder}</td>
                <td>{it.filename}</td>
                <td>{it.status}</td>
                <td>{new Date(it.created_at).toLocaleString()}</td>
                <td>{new Date(it.updated_at).toLocaleString()}</td>
              </tr>
            ))}
            {items.length === 0 ? (
              <tr>
                <td colSpan={6} className="app-table-empty">
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
