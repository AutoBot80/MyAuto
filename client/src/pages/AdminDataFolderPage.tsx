import { useCallback, useEffect, useRef, useState } from "react";
import { fetchAdminFolderFileBlobUrl, listAdminFolderContents, type AdminFolderEntry, type AdminFolderRootApi } from "../api/admin";
import { getAdminDealerNames, type AdminDealerNameRow } from "../api/adminDealers";
import "./AdminDataFolderPage.css";

const DEALER_PICK_STORAGE_KEY = "admin-data-folder-dealer-id";

function readStoredDealerId(): number | null {
  try {
    const raw = sessionStorage.getItem(DEALER_PICK_STORAGE_KEY);
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

function persistDealerId(id: number) {
  try {
    sessionStorage.setItem(DEALER_PICK_STORAGE_KEY, String(id));
  } catch {
    /* ignore quota / private mode */
  }
}

export interface AdminDataFolderPageProps {
  dealerId: number;
  kind: "upload-scans" | "run-logs" | "challans";
  /** When ``hidden``, use ``dealerId`` only (no dealer dropdown). Challans always hides the picker. */
  dealerPicker?: "full" | "hidden";
}

type AdminFolderViewerFile = { url: string; name: string; type: "image" | "pdf"; revoke: () => void };

function joinRel(parent: string, name: string): string {
  if (!parent) return name;
  return `${parent.replace(/\/+$/, "")}/${name}`;
}

function parentRel(rel: string): string {
  if (!rel) return "";
  const parts = rel.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}

function kindToRoot(kind: AdminDataFolderPageProps["kind"]): AdminFolderRootApi {
  if (kind === "upload-scans") return "upload_scans";
  if (kind === "run-logs") return "ocr_output";
  return "challans";
}

function formatModified(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
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

function FolderIcon() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="#1e5a9e" strokeWidth="2" aria-hidden>
      <path d="M6 18h14l4-4h22v26H6V18z" />
      <path d="M6 18v22h36V20H22l-4-4H6" fill="rgba(30,90,158,0.08)" />
    </svg>
  );
}

function fileKindIcon(name: string) {
  const e = name.split(".").pop()?.toLowerCase() ?? "";
  if (e === "pdf") return <PdfIcon />;
  if (["jpg", "jpeg", "png", "gif", "webp"].includes(e)) return <ImageIcon />;
  return <FileIcon />;
}

export function AdminDataFolderPage({ dealerId, kind, dealerPicker = "full" }: AdminDataFolderPageProps) {
  const root = kindToRoot(kind);
  const isChallans = kind === "challans";
  const hideDealerPicker = isChallans || dealerPicker === "hidden";
  const pageTitle = kind === "upload-scans" ? "Upload Scans" : kind === "run-logs" ? "Run Logs" : "Challans";
  const selectId =
    kind === "upload-scans" ? "admin-folder-dealer-uploads" : kind === "run-logs" ? "admin-folder-dealer-ocr" : "admin-folder-challans";

  const [dealerRows, setDealerRows] = useState<AdminDealerNameRow[]>([]);
  const [dealersLoading, setDealersLoading] = useState(true);
  const [dealersError, setDealersError] = useState<string | null>(null);
  const [selectedDealerId, setSelectedDealerId] = useState(() => readStoredDealerId() ?? dealerId);

  const [relPath, setRelPath] = useState("");
  const [items, setItems] = useState<AdminFolderEntry[]>([]);
  const [currentAbs, setCurrentAbs] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewerFile, setViewerFile] = useState<AdminFolderViewerFile | null>(null);
  const [fileOpening, setFileOpening] = useState<string | null>(null);
  const viewerRef = useRef<AdminFolderViewerFile | null>(null);

  useEffect(() => {
    if (hideDealerPicker) {
      setDealersLoading(false);
      setDealersError(null);
      if (isChallans) setDealerRows([]);
      setSelectedDealerId(dealerId);
      return;
    }
    setDealersLoading(true);
    setDealersError(null);
    getAdminDealerNames()
      .then((rows) => {
        setDealerRows(rows);
        setSelectedDealerId((prev) => {
          let next = prev;
          if (rows.length === 0) {
            next = dealerId;
          } else if (!rows.some((r) => r.dealer_id === prev)) {
            next = rows.some((r) => r.dealer_id === dealerId) ? dealerId : rows[0].dealer_id;
          }
          if (next !== prev) {
            persistDealerId(next);
          }
          return next;
        });
      })
      .catch((e) => {
        setDealersError(e instanceof Error ? e.message : "Could not load dealers.");
        setDealerRows([]);
      })
      .finally(() => setDealersLoading(false));
  }, [dealerId, hideDealerPicker, isChallans]);

  function onDealerChange(id: number) {
    setSelectedDealerId(id);
    persistDealerId(id);
    setRelPath("");
  }

  const closeViewer = useCallback(() => {
    setViewerFile((prev) => {
      if (prev?.revoke) prev.revoke();
      return null;
    });
  }, []);

  useEffect(() => {
    viewerRef.current = viewerFile;
  }, [viewerFile]);

  useEffect(() => {
    return () => {
      viewerRef.current?.revoke();
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeViewer();
    };
    if (viewerFile) {
      document.addEventListener("keydown", onKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
    };
  }, [viewerFile, closeViewer]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listAdminFolderContents(selectedDealerId, root, relPath)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setCurrentAbs(res.current_folder_abs);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Could not load folder.");
        setItems([]);
        setCurrentAbs(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedDealerId, root, relPath]);

  const ext = (name: string) => name.split(".").pop()?.toLowerCase() ?? "";
  const isPdf = (n: string) => ext(n) === "pdf";
  const isImage = (n: string) => ["jpg", "jpeg", "png", "gif", "webp"].includes(ext(n));
  const isViewableInBrowser = (n: string) => isPdf(n) || isImage(n);

  async function handleFileOpen(relFile: string, name: string) {
    setFileOpening(name);
    try {
      const { blobUrl, revoke, external } = await fetchAdminFolderFileBlobUrl(selectedDealerId, root, relFile);
      if (isPdf(name) || isImage(name)) {
        setViewerFile((prev) => {
          if (prev?.revoke) prev.revoke();
          return { url: blobUrl, name, type: isPdf(name) ? "pdf" : "image", revoke };
        });
      } else if (external) {
        window.open(blobUrl, "_blank", "noopener,noreferrer");
      } else {
        const a = document.createElement("a");
        a.href = blobUrl;
        a.download = name;
        a.rel = "noopener";
        a.click();
        window.setTimeout(() => revoke(), 2_000);
      }
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not open file.");
    } finally {
      setFileOpening(null);
    }
  }

  async function copyCurrentPath() {
    if (!currentAbs) return;
    try {
      await navigator.clipboard.writeText(currentAbs);
      window.alert("Folder path copied to clipboard.");
    } catch {
      window.alert(currentAbs);
    }
  }

  const titleLine = relPath ? `${pageTitle} / ${relPath.split("/").join(" / ")}` : `${pageTitle} (root)`;

  return (
    <div className="admin-data-folder-page">
      {!hideDealerPicker ? (
        <section className="view-vehicles-search admin-data-folder-page__dealer-bar" aria-label="Choose dealer">
          <div className="view-vehicles-search-field">
            <label htmlFor={selectId}>Dealer</label>
            <select
              id={selectId}
              value={selectedDealerId}
              onChange={(e) => onDealerChange(Number(e.target.value))}
              disabled={dealersLoading || dealerRows.length === 0}
              aria-label="Select dealer for this folder"
            >
              {dealersLoading && dealerRows.length === 0 ? (
                <option value={selectedDealerId}>Loading dealers…</option>
              ) : null}
              {!dealersLoading && dealerRows.length === 0 ? (
                <option value={selectedDealerId}>No dealers in database</option>
              ) : null}
              {dealerRows.map((r) => (
                <option key={r.dealer_id} value={r.dealer_id}>
                  {r.dealer_name}
                </option>
              ))}
            </select>
          </div>
        </section>
      ) : null}
      {!hideDealerPicker && dealersError ? (
        <p className="view-vehicles-error admin-data-folder-page__dealers-err">{dealersError}</p>
      ) : null}

      <div className="bulk-folder-view">
        <div className="bulk-folder-view-header admin-data-folder-page__header">
          {relPath ? (
            <button
              type="button"
              className="app-button app-button--small bulk-folder-view-back"
              onClick={() => setRelPath(parentRel(relPath))}
            >
              ← Up
            </button>
          ) : (
            <span className="app-button app-button--small bulk-folder-view-back" style={{ visibility: "hidden" }} aria-hidden>
              ← Up
            </span>
          )}
          <div className="admin-data-folder-page__heading">
            <h2 className="bulk-folder-view-title admin-data-folder-page__title">{titleLine}</h2>
            {currentAbs ? (
              <div className="admin-data-folder-page__path-row">
                <code className="admin-data-folder-page__path" title={currentAbs}>
                  {currentAbs}
                </code>
                <button type="button" className="app-button app-button--small" onClick={copyCurrentPath}>
                  Copy path
                </button>
              </div>
            ) : null}
          </div>
        </div>
        <div className="bulk-folder-view-body">
          {loading ? (
            <p className="bulk-folder-view-loading">Loading…</p>
          ) : error ? (
            <p className="bulk-folder-view-error">{error}</p>
          ) : (
            <div className="app-table-wrap admin-data-folder-list">
              <table className="app-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Size</th>
                    <th>Modified (newest first)</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((entry) => {
                    if (entry.kind === "dir") {
                      return (
                        <tr key={`d-${entry.name}`}>
                          <td className="admin-data-folder-list__name-cell">
                            <button
                              type="button"
                              className="admin-data-folder-list__name-btn"
                              onClick={() => setRelPath(joinRel(relPath, entry.name))}
                            >
                              <span className="admin-data-folder-list__mini-icon" aria-hidden>
                                <FolderIcon />
                              </span>
                              <span className="admin-data-folder-list__name-text">{entry.name}</span>
                            </button>
                          </td>
                          <td>Folder</td>
                          <td>—</td>
                          <td>{formatModified(entry.modified_at)}</td>
                        </tr>
                      );
                    }
                    const relFile = joinRel(relPath, entry.name);
                    return (
                      <tr key={`f-${entry.name}`}>
                        <td className="admin-data-folder-list__name-cell">
                          <button
                            type="button"
                            className="admin-data-folder-list__name-btn"
                            disabled={fileOpening === entry.name}
                            onClick={() => handleFileOpen(relFile, entry.name)}
                            title={isViewableInBrowser(entry.name) ? "View" : "Download"}
                          >
                            <span className="admin-data-folder-list__mini-icon" aria-hidden>
                              {fileKindIcon(entry.name)}
                            </span>
                            <span className="admin-data-folder-list__name-text">
                              {entry.name}
                              {fileOpening === entry.name ? " …" : ""}
                            </span>
                          </button>
                        </td>
                        <td>File</td>
                        <td>{entry.size != null ? `${(entry.size / 1024).toFixed(1)} KB` : "—"}</td>
                        <td>{formatModified(entry.modified_at)}</td>
                      </tr>
                    );
                  })}
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="app-table-empty">
                        This folder is empty.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      {viewerFile && (
        <div
          className="bulk-folder-viewer-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={`View ${viewerFile.name}`}
          onClick={() => closeViewer()}
        >
          <div className="bulk-folder-viewer-content" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="bulk-folder-viewer-close"
              onClick={() => closeViewer()}
              aria-label="Close"
            >
              ×
            </button>
            <p className="bulk-folder-viewer-title">{viewerFile.name}</p>
            {viewerFile.type === "image" ? (
              <img src={viewerFile.url} alt={viewerFile.name} className="bulk-folder-viewer-img" />
            ) : (
              <iframe src={viewerFile.url} title={viewerFile.name} className="bulk-folder-viewer-iframe" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
