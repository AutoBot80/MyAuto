import { useState, useEffect, useCallback } from "react";
import {
  listBulkLoads,
  getBulkLoadCounts,
  prepareReprocess,
  setBulkLoadActionTaken,
  bulkFolderUrl,
  type BulkLoadRow,
  type BulkLoadCounts,
} from "../api/bulkLoads";
import { saveAddSalesForm } from "../utils/addSalesStorage";

function formatDateDdMmYyyy(d: Date): string {
  const day = String(d.getDate()).padStart(2, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const year = d.getFullYear();
  return `${day}-${month}-${year}`;
}

function yesterday(): Date {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d;
}

interface BulkLoadsPageProps {
  onNavigateToAddSales?: () => void;
  onRefreshPendingCount?: () => void;
}

export function BulkLoadsPage({ onNavigateToAddSales, onRefreshPendingCount }: BulkLoadsPageProps) {
  const [activeTab, setActiveTab] = useState<"processed" | "rejected">("processed");
  const [dateFrom, setDateFrom] = useState(() => formatDateDdMmYyyy(yesterday()));
  const [dateTo, setDateTo] = useState(() => formatDateDdMmYyyy(new Date()));
  const [showSuccess, setShowSuccess] = useState(true);
  const [showFailure, setShowFailure] = useState(true);
  const [showProcessing, setShowProcessing] = useState(true);
  const [rows, setRows] = useState<BulkLoadRow[]>([]);
  const [counts, setCounts] = useState<BulkLoadCounts>({
    Success: 0,
    Error: 0,
    Processing: 0,
    Rejected: 0,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reprocessingId, setReprocessingId] = useState<number | null>(null);
  const [actionTakenId, setActionTakenId] = useState<number | null>(null);

  const fetchRows = useCallback(() => {
    setError(null);
    const params = {
      date_from: dateFrom,
      date_to: dateTo,
    };
    if (activeTab === "processed") {
      const statuses: string[] = [];
      if (showSuccess) statuses.push("Success");
      if (showFailure) statuses.push("Error");
      if (showProcessing) statuses.push("Processing");
      params.status_in = statuses.length ? statuses.join(",") : "Success,Error,Processing";
    } else {
      params.status = "Rejected";
    }
    return Promise.all([
      listBulkLoads(params),
      getBulkLoadCounts({ date_from: dateFrom, date_to: dateTo }),
    ])
      .then(([data, cnt]) => {
        setRows(data);
        setCounts(cnt);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, [activeTab, dateFrom, dateTo, showSuccess, showFailure, showProcessing]);

  useEffect(() => {
    setLoading(true);
    fetchRows().finally(() => setLoading(false));
  }, [fetchRows]);

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
  }, [fetchRows]);

  const handleReprocess = async (r: BulkLoadRow) => {
    if (!onNavigateToAddSales) return;
    setReprocessingId(r.id);
    try {
      const { subfolder, mobile } = await prepareReprocess(r.id);
      await saveAddSalesForm({ subfolder, mobile });
      onNavigateToAddSales();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Re-process failed");
    } finally {
      setReprocessingId(null);
    }
  };

  const handleApplyDates = () => {
    setLoading(true);
    fetchRows().finally(() => setLoading(false));
  };

  const handleActionTakenToggle = async (r: BulkLoadRow, checked: boolean) => {
    setActionTakenId(r.id);
    try {
      await setBulkLoadActionTaken(r.id, checked);
      await fetchRows();
      onRefreshPendingCount?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setActionTakenId(null);
    }
  };

  if (loading && rows.length === 0) {
    return (
      <div className="bulk-loads-page">
        <p>Loading…</p>
      </div>
    );
  }

  return (
    <div className="bulk-loads-page">
      <div className="bulk-loads-tabs">
        <button
          type="button"
          className={`bulk-loads-tab ${activeTab === "processed" ? "bulk-loads-tab--active" : ""}`}
          onClick={() => setActiveTab("processed")}
        >
          Processed ({counts.Error})
        </button>
        <button
          type="button"
          className={`bulk-loads-tab ${activeTab === "rejected" ? "bulk-loads-tab--active" : ""}`}
          onClick={() => setActiveTab("rejected")}
        >
          Rejected ({counts.Rejected})
        </button>
      </div>
      <div className="bulk-loads-date-filters">
        <label>
          Date from
          <input
            type="text"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            placeholder="dd-mm-yyyy"
            className="bulk-loads-date-input"
          />
        </label>
        <label>
          Date to
          <input
            type="text"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            placeholder="dd-mm-yyyy"
            className="bulk-loads-date-input"
          />
        </label>
        <button
          type="button"
          className="app-button app-button--small"
          onClick={handleApplyDates}
        >
          Apply
        </button>
      </div>
      {error && <p className="bulk-loads-error">{error}</p>}
      {activeTab === "processed" && (
        <div className="bulk-loads-checkboxes">
          <label className="bulk-loads-checkbox">
            <input
              type="checkbox"
              checked={showSuccess}
              onChange={(e) => setShowSuccess(e.target.checked)}
            />
            Success ({counts.Success})
          </label>
          <label className="bulk-loads-checkbox">
            <input
              type="checkbox"
              checked={showFailure}
              onChange={(e) => setShowFailure(e.target.checked)}
            />
            Error ({counts.Error})
          </label>
          <label className="bulk-loads-checkbox">
            <input
              type="checkbox"
              checked={showProcessing}
              onChange={(e) => setShowProcessing(e.target.checked)}
            />
            Processing ({counts.Processing})
          </label>
        </div>
      )}
      <div className="bulk-loads-table-wrap">
        {activeTab === "rejected" ? (
          <table className="bulk-loads-table">
            <thead>
              <tr>
                <th>File Name</th>
                <th>Reason</th>
                <th>Folder</th>
                <th>Created</th>
                <th>Seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={5}>No rejected records.</td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr key={r.id} className="bulk-loads-row bulk-loads-row--rejected">
                    <td>{r.file_name ?? "—"}</td>
                    <td className="bulk-loads-error-cell" title={r.error_message ?? undefined}>
                      {r.error_message ?? "—"}
                    </td>
                    <td>
                      {r.result_folder ? (
                        <a
                          href={bulkFolderUrl(r.result_folder)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="bulk-loads-folder-link"
                          title={`Open ${r.result_folder}`}
                        >
                          {r.result_folder}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
                    <td>
                      <label className="bulk-loads-checkbox">
                        <input
                          type="checkbox"
                          checked={r.action_taken ?? false}
                          onChange={(e) => handleActionTakenToggle(r, e.target.checked)}
                          disabled={actionTakenId !== null}
                          title="Mark as seen"
                        />
                      </label>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        ) : (
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
                      {r.result_folder ? (
                        <a
                          href={
                            r.status === "Success"
                              ? `/documents/${encodeURIComponent(r.subfolder ?? r.result_folder)}`
                              : bulkFolderUrl(r.result_folder)
                          }
                          target="_blank"
                          rel="noopener noreferrer"
                          className="bulk-loads-folder-link"
                          title={r.result_folder}
                        >
                          {r.subfolder ?? r.result_folder}
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
                          {reprocessingId === r.id ? "Preparing…" : "Re-Try"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
