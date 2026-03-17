import { useState, useEffect } from "react";
import { listBulkLoads, type BulkLoadRow } from "../api/bulkLoads";

export function BulkLoadsPage() {
  const [rows, setRows] = useState<BulkLoadRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSuccess, setShowSuccess] = useState(true);
  const [showError, setShowError] = useState(true);

  const fetchRows = () => {
    setError(null);
    const statusParam =
      showSuccess && showError ? undefined
      : showSuccess ? "Success"
      : showError ? "Error"
      : undefined;
    return listBulkLoads(statusParam)
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  };

  useEffect(() => {
    setLoading(true);
    fetchRows().finally(() => setLoading(false));
  }, [showSuccess, showError]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") fetchRows();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [showSuccess, showError]);

  if (loading && rows.length === 0) {
    return (
      <div className="bulk-loads-page">
        <h2>Bulk Loads</h2>
        <p>Loading…</p>
      </div>
    );
  }

  return (
    <div className="bulk-loads-page">
      <h2 className="bulk-loads-title">Bulk Loads</h2>
      <div className="bulk-loads-filters">
        <label className="bulk-loads-checkbox">
          <input
            type="checkbox"
            checked={showSuccess}
            onChange={(e) => setShowSuccess(e.target.checked)}
          />
          Success
        </label>
        <label className="bulk-loads-checkbox">
          <input
            type="checkbox"
            checked={showError}
            onChange={(e) => setShowError(e.target.checked)}
          />
          Error
        </label>
      </div>
      {error && <p className="bulk-loads-error">{error}</p>}
      <div className="bulk-loads-table-wrap">
        <table className="bulk-loads-table">
          <thead>
            <tr>
              <th>Mobile</th>
              <th>Name</th>
              <th>Folder</th>
              <th>Status</th>
              <th>Error</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6}>No records.</td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className={`bulk-loads-row bulk-loads-row--${r.status.toLowerCase()}`}>
                  <td>{r.mobile ?? "—"}</td>
                  <td>{r.name ?? "—"}</td>
                  <td>
                    {r.folder_path ? (
                      <span className="bulk-loads-folder" title={r.folder_path}>
                        {r.subfolder}
                      </span>
                    ) : (
                      r.subfolder
                    )}
                  </td>
                  <td>
                    <span className={`bulk-loads-status bulk-loads-status--${r.status.toLowerCase()}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="bulk-loads-error-cell">{r.error_message ?? "—"}</td>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
