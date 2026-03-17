import { useState, useEffect } from "react";
import { listBulkLoads, clearBulkLoads, prepareReprocess, type BulkLoadRow } from "../api/bulkLoads";
import { saveAddSalesForm } from "../utils/addSalesStorage";

interface BulkLoadsPageProps {
  onNavigateToAddSales?: () => void;
}

export function BulkLoadsPage({ onNavigateToAddSales }: BulkLoadsPageProps) {
  const [rows, setRows] = useState<BulkLoadRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSuccess, setShowSuccess] = useState(true);
  const [showError, setShowError] = useState(true);
  const [showProcessing, setShowProcessing] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [reprocessingId, setReprocessingId] = useState<number | null>(null);

  const fetchRows = () => {
    setError(null);
    const onlySuccess = showSuccess && !showError && !showProcessing;
    const onlyError = showError && !showSuccess && !showProcessing;
    const onlyProcessing = showProcessing && !showSuccess && !showError;
    const statusParam = onlySuccess ? "Success" : onlyError ? "Error" : onlyProcessing ? "Processing" : undefined;
    return listBulkLoads(statusParam)
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  };

  useEffect(() => {
    setLoading(true);
    fetchRows().finally(() => setLoading(false));
  }, [showSuccess, showError, showProcessing]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") fetchRows();
    };
    document.addEventListener("visibilitychange", onVisible);
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") fetchRows();
    }, 10000);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(interval);
    };
  }, [showSuccess, showError, showProcessing]);

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
        <label className="bulk-loads-checkbox">
          <input
            type="checkbox"
            checked={showProcessing}
            onChange={(e) => setShowProcessing(e.target.checked)}
          />
          Processing
        </label>
      </div>
      {error && <p className="bulk-loads-error">{error}</p>}
      <div className="bulk-loads-table-wrap">
        <table className="bulk-loads-table">
          <thead>
            <tr>
              <th>Mobile</th>
              <th>Name</th>
              <th>File</th>
              <th>Folder</th>
              <th>Status</th>
              <th>Error</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={8}>No records.</td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className={`bulk-loads-row bulk-loads-row--${r.status.toLowerCase()}`}>
                  <td>{r.mobile ?? "—"}</td>
                  <td>{r.name ?? "—"}</td>
                  <td>{r.file_name ?? "—"}</td>
                  <td>
                    {r.subfolder ? (
                      <a
                        href={`/documents/${encodeURIComponent(r.subfolder)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="bulk-loads-folder-link"
                        title={`Open Uploaded scans / ${r.subfolder}`}
                      >
                        {r.subfolder}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <span className={`bulk-loads-status bulk-loads-status--${r.status.toLowerCase()}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="bulk-loads-error-cell" title={r.error_message ?? undefined}>
                    {r.error_message ?? "—"}
                  </td>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
                  <td>
                    {r.status === "Error" && (
                      <button
                        type="button"
                        className="app-button app-button--small bulk-loads-reprocess-btn"
                        onClick={() => handleReprocess(r)}
                        disabled={reprocessingId !== null}
                        title="Open Add Customer with mobile and scanned files"
                      >
                        {reprocessingId === r.id ? "Preparing…" : "Re-process"}
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
