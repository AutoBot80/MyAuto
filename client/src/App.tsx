import { useEffect, useRef, useState } from "react";
import "./App.css";

type Page = "add-sales" | "customer-details" | "rto-status" | "ai-reader-queue";
type AddSalesStep = "upload-scans" | "insurance" | "hero-dms" | "rto";

type AiReaderQueueItem = {
  id: number;
  subfolder: string;
  filename: string;
  status: string;
  created_at: string;
  updated_at: string;
};

function todayFormatted(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function App() {
  const [today] = useState(todayFormatted);
  const [page, setPage] = useState<Page>("add-sales");
  const [addSalesStep, setAddSalesStep] = useState<AddSalesStep>("upload-scans");
  const filesInputRef = useRef<HTMLInputElement | null>(null);
  const [aadharLast4, setAadharLast4] = useState("");
  const [uploadStatus, setUploadStatus] = useState<string>("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);
  const [queueItems, setQueueItems] = useState<AiReaderQueueItem[]>([]);
  const [queueError, setQueueError] = useState<string>("");

  const aadharDigits = aadharLast4.replace(/\D/g, "");
  const isAadharValid = aadharDigits.length === 4;

  useEffect(() => {
    if (page !== "ai-reader-queue") return;

    let cancelled = false;

    async function loadQueue() {
      try {
        setQueueError("");
        const res = await fetch("http://127.0.0.1:8000/ai-reader-queue");
        if (!res.ok) throw new Error(await res.text());
        const data = (await res.json()) as AiReaderQueueItem[];
        if (!cancelled) setQueueItems(data);
      } catch (e) {
        if (!cancelled) {
          setQueueError(e instanceof Error ? e.message : "Failed to load queue.");
        }
      }
    }

    loadQueue();
    const t = window.setInterval(loadQueue, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [page]);

  async function uploadSelectedScans(filesToUpload: File[]) {
    if (filesToUpload.length === 0) {
      setUploadStatus("Please choose files first.");
      return;
    }
    if (!isAadharValid) {
      setUploadStatus("Enter last 4 digits of Customer Aadhar first.");
      return;
    }
    setIsUploading(true);
    setUploadStatus("Uploading...");
    try {
      const form = new FormData();
      form.append("aadhar_last4", aadharDigits);
      for (const f of filesToUpload) form.append("files", f);

      const res = await fetch("http://127.0.0.1:8000/uploads/scans", {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Upload failed (${res.status})`);
      }

      const data = (await res.json()) as {
        saved_count: number;
        saved_files?: string[];
        error?: string;
      };
      if (data.error) throw new Error(data.error);
      setUploadStatus(`Uploaded ${data.saved_count} file(s) successfully.`);
      if (data.saved_files?.length) {
        setUploadedFiles((prev) => [...data.saved_files!, ...prev]);
      }
      if (filesInputRef.current) filesInputRef.current.value = "";
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="app-wrap">
      <div className="app-box">
        <header className="app-topbar">
          <div className="app-topbar-spacer" />
          <h1 className="app-topbar-title">Arya Agencies</h1>
          <div className="app-topbar-date">{today}</div>
        </header>

        <div className="app-body">
          <nav className="app-sidebar">
            <div className="app-nav-label">Main</div>
            <a
              href="#add-sales"
              className={`app-nav-link ${page === "add-sales" ? "active" : ""}`}
              onClick={(e) => {
                e.preventDefault();
                setPage("add-sales");
              }}
            >
              Add Sales
            </a>
            <a
              href="#customer-details"
              className={`app-nav-link ${page === "customer-details" ? "active" : ""}`}
              onClick={(e) => {
                e.preventDefault();
                setPage("customer-details");
              }}
            >
              Customer Details
            </a>
            <a
              href="#rto-status"
              className={`app-nav-link ${page === "rto-status" ? "active" : ""}`}
              onClick={(e) => {
                e.preventDefault();
                setPage("rto-status");
              }}
            >
              RTO Queue
            </a>
            <a
              href="#ai-reader-queue"
              className={`app-nav-link ${page === "ai-reader-queue" ? "active" : ""}`}
              onClick={(e) => {
                e.preventDefault();
                setPage("ai-reader-queue");
              }}
            >
              AI Reader Queue
            </a>
          </nav>

          <main className="app-main">
            {page === "add-sales" && (
              <>
                <h2>Add Sales</h2>
                <div className="app-field-row">
                  <label className="app-field">
                    <div className="app-field-label">Customer Aadhar (last 4 digits)</div>
                    <input
                      className="app-field-input"
                      inputMode="numeric"
                      placeholder="1234"
                      value={aadharLast4}
                      onChange={(e) => {
                        const digits = e.target.value.replace(/\D/g, "").slice(0, 4);
                        setAadharLast4(digits);
                        setUploadStatus("");
                        setUploadedFiles([]);
                      }}
                      aria-invalid={aadharLast4.length > 0 && !isAadharValid}
                    />
                  </label>
                  <div className="app-field-hint">
                    {isAadharValid ? "Valid" : "Enter 4 digits"}
                  </div>
                </div>
                <div className="app-tiles">
                  <button
                    type="button"
                    className={`app-tile ${addSalesStep === "upload-scans" ? "active" : ""}`}
                    onClick={() => setAddSalesStep("upload-scans")}
                  >
                    <div className="app-tile-step">1</div>
                    <div className="app-tile-title">Upload scans</div>
                  </button>
                  <button
                    type="button"
                    className={`app-tile ${addSalesStep === "insurance" ? "active" : ""}`}
                    onClick={() => setAddSalesStep("insurance")}
                  >
                    <div className="app-tile-step">2</div>
                    <div className="app-tile-title">Insurance</div>
                  </button>
                  <button
                    type="button"
                    className={`app-tile ${addSalesStep === "hero-dms" ? "active" : ""}`}
                    onClick={() => setAddSalesStep("hero-dms")}
                  >
                    <div className="app-tile-step">3</div>
                    <div className="app-tile-title">Hero DMS</div>
                  </button>
                  <button
                    type="button"
                    className={`app-tile ${addSalesStep === "rto" ? "active" : ""}`}
                    onClick={() => setAddSalesStep("rto")}
                  >
                    <div className="app-tile-step">4</div>
                    <div className="app-tile-title">RTO</div>
                  </button>
                </div>
                {addSalesStep === "upload-scans" ? (
                  <section className="app-panel">
                    <div className="app-panel-title">Upload scans</div>
                    <div className="app-panel-row">
                      <input
                        ref={filesInputRef}
                        type="file"
                        multiple
                        accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
                        style={{ display: "none" }}
                        onChange={async (e) => {
                          const list = e.target.files ? Array.from(e.target.files) : [];
                          if (!list.length) return;
                          await uploadSelectedScans(list);
                        }}
                      />
                    </div>
                    <div className="app-panel-row app-panel-actions">
                      <button
                        type="button"
                        disabled={isUploading || !isAadharValid}
                        onClick={() => filesInputRef.current?.click()}
                      >
                        {isUploading ? "Uploading..." : "Choose files"}
                      </button>
                    </div>
                    {uploadStatus ? <div className="app-panel-status">{uploadStatus}</div> : null}
                    {uploadedFiles.length ? (
                      <div className="app-panel-uploaded">
                        <div className="app-panel-uploaded-title">Uploaded successfully</div>
                        <ul className="app-panel-uploaded-list">
                          {uploadedFiles.map((f, idx) => (
                            <li key={`${f}-${idx}`}>{f}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </section>
                ) : (
                  <div className="app-placeholder">
                    <p>Step: {addSalesStep.replace("-", " ")}</p>
                  </div>
                )}
              </>
            )}
            {page === "customer-details" && (
              <div className="app-placeholder">
                <h2>Customer Details</h2>
                <p>Customer details page — coming soon.</p>
              </div>
            )}
            {page === "rto-status" && (
              <div className="app-placeholder">
                <h2>RTO Queue</h2>
                <p>RTO status page — coming soon.</p>
              </div>
            )}
            {page === "ai-reader-queue" && (
              <div>
                <h2>AI Reader Queue</h2>
                {queueError ? (
                  <div className="app-panel-status">{queueError}</div>
                ) : null}
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
                      {queueItems.map((it) => (
                        <tr key={it.id}>
                          <td>{it.id}</td>
                          <td>{it.subfolder}</td>
                          <td>{it.filename}</td>
                          <td>{it.status}</td>
                          <td>{new Date(it.created_at).toLocaleString()}</td>
                          <td>{new Date(it.updated_at).toLocaleString()}</td>
                        </tr>
                      ))}
                      {queueItems.length === 0 ? (
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
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
export default App;
