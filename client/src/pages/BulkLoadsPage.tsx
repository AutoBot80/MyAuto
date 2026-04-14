import { useState, useEffect, useCallback } from "react";
import {
  listBulkLoads,
  getBulkLoadCounts,
  prepareReprocess,
  setBulkLoadActionTaken,
  getBulkFolderFiles,
  getDocumentsFolderFiles,
  type BulkLoadRow,
  type BulkLoadCounts,
  type ListBulkLoadsParams,
} from "../api/bulkLoads";
import { DEALER_ID } from "../api/dealerId";
import { saveAddSalesForm } from "../utils/addSalesStorage";

type FolderViewType = "bulk" | "documents";

function FolderView({
  folderPath,
  folderType,
  onBack,
  dealerId,
}: {
  folderPath: string;
  folderType: FolderViewType;
  onBack: () => void;
  dealerId?: number;
}) {
  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [viewerFile, setViewerFile] = useState<{ url: string; name: string; type: "image" | "pdf" } | null>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setViewerFile(null);
    };
    if (viewerFile) {
      document.addEventListener("keydown", onKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
    };
  }, [viewerFile]);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    const fetchFiles =
      folderType === "bulk"
        ? getBulkFolderFiles(folderPath, dealerId).then((res) => res.files || [])
        : getDocumentsFolderFiles(folderPath, dealerId).then((res) => res.files || []);
    fetchFiles
      .then(setFiles)
      .catch((e) => setErr(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [folderPath, folderType, dealerId]);

  const fileUrl = (name: string) => {
    const base = import.meta.env.VITE_API_URL ?? "";
    const did = dealerId ?? DEALER_ID;
    if (folderType === "bulk") {
      const path = `${folderPath}/${name}`;
      return `${base}/bulk-loads/file/${encodeURIComponent(path)}?dealer_id=${did}`;
    }
    return `${base}/documents/${encodeURIComponent(folderPath)}/${encodeURIComponent(name)}?dealer_id=${did}`;
  };

  const ext = (name: string) => name.split(".").pop()?.toLowerCase() ?? "";
  const isPdf = (n: string) => ext(n) === "pdf";
  const isImage = (n: string) => ["jpg", "jpeg", "png", "gif", "webp"].includes(ext(n));
  const isViewableInBrowser = (n: string) => isPdf(n) || isImage(n);

  const handleFileClick = (f: { name: string; size: number }) => {
    const url = fileUrl(f.name);
    if (isPdf(f.name)) {
      setViewerFile({ url, name: f.name, type: "pdf" });
    } else if (isImage(f.name)) {
      setViewerFile({ url, name: f.name, type: "image" });
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  };

  const itemContent = (f: { name: string; size: number }) => (
    <>
      <div className="bulk-folder-view-icon">
        {isPdf(f.name) ? (
          <PdfIcon />
        ) : isImage(f.name) ? (
          <>
            <img
              src={fileUrl(f.name)}
              alt=""
              loading="lazy"
              className="bulk-folder-view-preview"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            <span className="bulk-folder-view-preview-fallback" aria-hidden>
              <ImageIcon />
            </span>
          </>
        ) : (
          <FileIcon />
        )}
      </div>
      <span className="bulk-folder-view-name">{f.name}</span>
      <span className="bulk-folder-view-size">
        {(f.size / 1024).toFixed(1)} KB
      </span>
    </>
  );

  return (
    <div className="bulk-folder-view">
      <div className="bulk-folder-view-header">
        <button
          type="button"
          className="app-button app-button--small bulk-folder-view-back"
          onClick={onBack}
        >
          ← Back to list
        </button>
        <h2 className="bulk-folder-view-title">{folderPath}</h2>
      </div>
      <div className="bulk-folder-view-body">
        {loading ? (
          <p className="bulk-folder-view-loading">Loading…</p>
        ) : err ? (
          <p className="bulk-folder-view-error">{err}</p>
        ) : files.length === 0 ? (
          <p className="bulk-folder-view-empty">No files in this folder.</p>
        ) : (
          <div className="bulk-folder-view-grid">
            {files.map((f) =>
              isViewableInBrowser(f.name) ? (
                <button
                  key={f.name}
                  type="button"
                  className="bulk-folder-view-item bulk-folder-view-item--button"
                  onClick={() => handleFileClick(f)}
                >
                  {itemContent(f)}
                </button>
              ) : (
                <a
                  key={f.name}
                  href={fileUrl(f.name)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="bulk-folder-view-item"
                >
                  {itemContent(f)}
                </a>
              )
            )}
          </div>
        )}
      </div>
      {viewerFile && (
        <div
          className="bulk-folder-viewer-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={`View ${viewerFile.name}`}
          onClick={() => setViewerFile(null)}
        >
          <div
            className="bulk-folder-viewer-content"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="bulk-folder-viewer-close"
              onClick={() => setViewerFile(null)}
              aria-label="Close"
            >
              ×
            </button>
            <p className="bulk-folder-viewer-title">{viewerFile.name}</p>
            {viewerFile.type === "image" ? (
              <img
                src={viewerFile.url}
                alt={viewerFile.name}
                className="bulk-folder-viewer-img"
              />
            ) : (
              <iframe
                src={viewerFile.url}
                title={viewerFile.name}
                className="bulk-folder-viewer-iframe"
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PdfIcon() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="#c41e3a" strokeWidth="2" aria-hidden>
      <path d="M8 4h20l12 12v28H8V4z" />
      <path d="M28 4v12h12" />
      <path d="M16 24h16M16 30h12M16 36h8" />
    </svg>
  );
}
function ImageIcon() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="#1e5a9e" strokeWidth="2" aria-hidden>
      <rect x="4" y="8" width="40" height="32" rx="2" />
      <circle cx="16" cy="20" r="6" />
      <path d="M4 36l12-12 8 8 12-12 8 8" />
    </svg>
  );
}
function FileIcon() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="#555" strokeWidth="2" aria-hidden>
      <path d="M8 4h20l12 12v28H8V4z" />
      <path d="M28 4v12h12" />
    </svg>
  );
}

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
  dealerId?: number;
  onNavigateToAddSales?: () => void;
  onRefreshPendingCount?: () => void;
}

export function BulkLoadsPage({ dealerId, onNavigateToAddSales, onRefreshPendingCount }: BulkLoadsPageProps) {
  const [activeTab, setActiveTab] = useState<"processed" | "rejected">("processed");
  const [dateFrom, setDateFrom] = useState(() => formatDateDdMmYyyy(yesterday()));
  const [dateTo, setDateTo] = useState(() => formatDateDdMmYyyy(new Date()));
  const [showSuccess, setShowSuccess] = useState(true);
  const [showFailure, setShowFailure] = useState(true);
  const [showQueued, setShowQueued] = useState(true);
  const [showProcessing, setShowProcessing] = useState(true);
  const [rows, setRows] = useState<BulkLoadRow[]>([]);
  const [counts, setCounts] = useState<BulkLoadCounts>({
    Success: 0,
    Error: 0,
    Queued: 0,
    Processing: 0,
    Rejected: 0,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reprocessingId, setReprocessingId] = useState<number | null>(null);
  const [actionTakenId, setActionTakenId] = useState<number | null>(null);
  const [folderToView, setFolderToView] = useState<{ path: string; type: FolderViewType } | null>(null);

  const fetchRows = useCallback(() => {
    setError(null);
    const params: ListBulkLoadsParams = {
      date_from: dateFrom,
      date_to: dateTo,
    };
    if (activeTab === "processed") {
      const statuses: string[] = [];
      if (showSuccess) statuses.push("Success");
      if (showFailure) statuses.push("Error");
      if (showQueued) statuses.push("Queued");
      if (showProcessing) statuses.push("Processing");
      params.status_in = statuses.length ? statuses.join(",") : "Success,Error,Queued,Processing";
    } else {
      params.status = "Rejected";
    }
    return Promise.all([
      listBulkLoads({ ...params, dealer_id: dealerId }),
      getBulkLoadCounts({ date_from: dateFrom, date_to: dateTo, dealer_id: dealerId }),
    ])
      .then(([data, cnt]) => {
        setError(null);
        setRows(data);
        setCounts(cnt);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, [activeTab, dateFrom, dateTo, showSuccess, showFailure, showQueued, showProcessing, dealerId]);

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
      const { bulk_load_id, subfolder, mobile, uploadedFiles } = await prepareReprocess(r.id, dealerId);
      await saveAddSalesForm({
        savedTo: subfolder,
        mobile: mobile ?? "",
        uploadedFiles: uploadedFiles ?? [],
        uploadStatus: "Uploaded from Re-Try",
        reprocessBulkLoadId: bulk_load_id,
        extractedVehicle: null,
        extractedCustomer: null,
        extractedInsurance: null,
        hasSubmittedInfo: false,
        lastSubmittedCustomerId: null,
        lastSubmittedVehicleId: null,
      });
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
      await setBulkLoadActionTaken(r.id, checked, dealerId);
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

  if (folderToView) {
    return (
      <FolderView
        folderPath={folderToView.path}
        folderType={folderToView.type}
        onBack={() => setFolderToView(null)}
        dealerId={dealerId}
      />
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
        <label htmlFor="bulk-loads-date-from">
          Date from
          <input
            id="bulk-loads-date-from"
            name="dateFrom"
            type="text"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            placeholder="dd-mm-yyyy"
            className="bulk-loads-date-input"
          />
        </label>
        <label htmlFor="bulk-loads-date-to">
          Date to
          <input
            id="bulk-loads-date-to"
            name="dateTo"
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
          <label className="bulk-loads-checkbox" htmlFor="bulk-loads-show-success">
            <input
              id="bulk-loads-show-success"
              name="showSuccess"
              type="checkbox"
              checked={showSuccess}
              onChange={(e) => setShowSuccess(e.target.checked)}
            />
            Success ({counts.Success})
          </label>
          <label className="bulk-loads-checkbox" htmlFor="bulk-loads-show-failure">
            <input
              id="bulk-loads-show-failure"
              name="showFailure"
              type="checkbox"
              checked={showFailure}
              onChange={(e) => setShowFailure(e.target.checked)}
            />
            Error ({counts.Error})
          </label>
          <label className="bulk-loads-checkbox" htmlFor="bulk-loads-show-queued">
            <input
              id="bulk-loads-show-queued"
              name="showQueued"
              type="checkbox"
              checked={showQueued}
              onChange={(e) => setShowQueued(e.target.checked)}
            />
            Queued ({counts.Queued})
          </label>
          <label className="bulk-loads-checkbox" htmlFor="bulk-loads-show-processing">
            <input
              id="bulk-loads-show-processing"
              name="showProcessing"
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
                <th>Corrected</th>
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
                        <button
                          type="button"
                          className="bulk-loads-folder-link bulk-loads-folder-link-btn"
                          onClick={() => setFolderToView({ path: r.result_folder!, type: "bulk" })}
                          title={`Open ${r.result_folder}`}
                        >
                          {r.result_folder}
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
                    <td>
                      <label className="bulk-loads-checkbox" htmlFor={`bulk-loads-corrected-${r.id}`}>
                        <input
                          id={`bulk-loads-corrected-${r.id}`}
                          name={`corrected-${r.id}`}
                          type="checkbox"
                          checked={r.action_taken ?? false}
                          onChange={(e) => handleActionTakenToggle(r, e.target.checked)}
                          disabled={actionTakenId !== null}
                          title="Mark as corrected"
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
                        r.status === "Success" ? (
                          <button
                            type="button"
                            className="bulk-loads-folder-link bulk-loads-folder-link-btn"
                            onClick={() =>
                              setFolderToView({
                                path: r.subfolder ?? r.result_folder!,
                                type: "documents",
                              })
                            }
                            title={r.result_folder}
                          >
                            {r.subfolder ?? r.result_folder}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="bulk-loads-folder-link bulk-loads-folder-link-btn"
                            onClick={() => setFolderToView({ path: r.result_folder!, type: "bulk" })}
                            title={r.result_folder}
                          >
                            {r.subfolder ?? r.result_folder}
                          </button>
                        )
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
