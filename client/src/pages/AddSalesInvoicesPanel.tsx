import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { fetchAddSalesInvoices, type AddSalesInvoiceRow } from "../api/addSales";
import { openDocumentFileInNewTab } from "../api/customerSearch";

const INVOICES_RECENT_DAYS = 15;

export interface AddSalesInvoicesPanelProps {
  dealerId: number;
  invoicesTabActive: boolean;
}

function cell(value: string | null | undefined): string {
  const s = (value ?? "").trim();
  return s || "—";
}

function formatCost(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-IN");
}

function FolderIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export function AddSalesInvoicesPanel({ dealerId, invoicesTabActive }: AddSalesInvoicesPanelProps) {
  const [mobile, setMobile] = useState("");
  const [chassis, setChassis] = useState("");
  const [engine, setEngine] = useState("");
  const [rows, setRows] = useState<AddSalesInvoiceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [docSubfolder, setDocSubfolder] = useState<string | null>(null);
  const [docFiles, setDocFiles] = useState<{ name: string; size: number }[]>([]);
  const [docLoading, setDocLoading] = useState(false);
  const [docOpenErr, setDocOpenErr] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    if (dealerId <= 0) return;
    setLoadErr(null);
    setLoading(true);
    try {
      const r = await fetchAddSalesInvoices(dealerId, {
        days: INVOICES_RECENT_DAYS,
        mobile: mobile.trim() || null,
        chassis: chassis.trim() || null,
        engine: engine.trim() || null,
      });
      setRows(r.rows ?? []);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "Failed to load invoices.");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [dealerId, mobile, chassis, engine]);

  useEffect(() => {
    if (!invoicesTabActive || dealerId <= 0) return;
    let cancelled = false;
    setLoadErr(null);
    setLoading(true);
    void fetchAddSalesInvoices(dealerId, { days: INVOICES_RECENT_DAYS })
      .then((r) => {
        if (!cancelled) setRows(r.rows ?? []);
      })
      .catch((e) => {
        if (!cancelled) {
          setLoadErr(e instanceof Error ? e.message : "Failed to load invoices.");
          setRows([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [invoicesTabActive, dealerId]);

  const handleSearch = () => {
    void loadList();
  };

  const openDocuments = async (subfolder: string, label: string) => {
    if (!subfolder?.trim()) return;
    setDocSubfolder(subfolder.trim());
    setDocumentsOpen(true);
    setDocOpenErr(null);
    setDocLoading(true);
    setDocFiles([]);
    try {
      const res = await apiFetch<{ files: { name: string; size: number }[] }>(
        `/documents/${encodeURIComponent(subfolder.trim())}/list?dealer_id=${dealerId}`
      );
      setDocFiles(res.files || []);
    } catch {
      setDocFiles([]);
      setDocOpenErr(`Could not load documents for ${label}.`);
    } finally {
      setDocLoading(false);
    }
  };

  return (
    <div className="add-sales-invoices">
      <section className="add-sales-invoices-search" aria-label="Invoice search">
        <div className="view-customer-search-row">
          <label htmlFor="asi-mobile">Customer Mobile</label>
          <input
            id="asi-mobile"
            type="text"
            autoComplete="off"
            placeholder="e.g. 9876543210"
            value={mobile}
            onChange={(e) => setMobile(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div className="view-vehicles-search-field">
          <label htmlFor="asi-chassis">Chassis / VIN</label>
          <input
            id="asi-chassis"
            type="text"
            autoComplete="off"
            placeholder="e.g. MB* or last 5 digits"
            value={chassis}
            onChange={(e) => setChassis(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div className="view-vehicles-search-field">
          <label htmlFor="asi-engine">Engine</label>
          <input
            id="asi-engine"
            type="text"
            autoComplete="off"
            placeholder="e.g. *12345 or partial"
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <button
          type="button"
          className="app-button app-button--primary view-vehicles-search-btn"
          onClick={handleSearch}
          disabled={loading}
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </section>

      <div className="add-sales-invoices-toolbar" role="toolbar" aria-label="Invoice list actions">
        <span className="add-sales-invoices-hint">
          Showing sales from the last {INVOICES_RECENT_DAYS} days (newest first).
        </span>
        <button
          type="button"
          className="app-button app-button--small"
          disabled={loading}
          aria-busy={loading}
          title="Reload invoice list"
          onClick={() => void loadList()}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {loadErr ? (
        <p className="view-vehicles-error" role="alert">
          {loadErr}
        </p>
      ) : null}

      {loading && rows.length === 0 ? (
        <p className="app-table-empty">Loading…</p>
      ) : !loading && rows.length === 0 ? (
        <p className="app-table-empty">No sales found in this period for the current filters.</p>
      ) : (
        <div className="add-sales-invoices-table-wrap">
          <table className="app-table add-sales-invoices-table">
            <thead>
              <tr>
                <th scope="col">Customer Name</th>
                <th scope="col">Mobile</th>
                <th scope="col">Model</th>
                <th scope="col">Invoice Date</th>
                <th scope="col">Invoice Number</th>
                <th scope="col">Insurance Policy No.</th>
                <th scope="col">CPA Policy No.</th>
                <th scope="col">Ex-Showroom</th>
                <th scope="col">Insurance Premium</th>
                <th scope="col">CPA Premium</th>
                <th scope="col">Scans</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const loc = (r.file_location ?? "").trim();
                const folderLabel = cell(r.customer_name) !== "—" ? cell(r.customer_name) : `sale ${r.sales_id}`;
                return (
                  <tr key={r.sales_id}>
                    <td>{cell(r.customer_name)}</td>
                    <td>{cell(r.mobile)}</td>
                    <td>{cell(r.model)}</td>
                    <td>{cell(r.invoice_date)}</td>
                    <td className="view-vehicles-mono">{cell(r.invoice_number)}</td>
                    <td className="view-vehicles-mono">{cell(r.insurance_policy_num)}</td>
                    <td className="view-vehicles-mono">{cell(r.cpa_policy_num)}</td>
                    <td className="view-vehicles-mono">{formatCost(r.ex_showroom_amount)}</td>
                    <td className="view-vehicles-mono">{formatCost(r.insurance_premium)}</td>
                    <td className="view-vehicles-mono">{formatCost(r.cpa_premium)}</td>
                    <td className="add-sales-invoices-scans-cell">
                      <button
                        type="button"
                        className="view-customer-folder-btn add-sales-invoices-folder-btn"
                        disabled={!loc}
                        title={loc ? "View uploaded scans" : "No scan folder for this sale"}
                        aria-label={`Open uploaded scans for ${folderLabel}`}
                        onClick={() => loc && void openDocuments(loc, folderLabel)}
                      >
                        <FolderIcon />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {documentsOpen ? (
        <div
          className="view-customer-modal-backdrop"
          onClick={() => {
            setDocOpenErr(null);
            setDocumentsOpen(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setDocOpenErr(null);
              setDocumentsOpen(false);
            }
          }}
          role="button"
          tabIndex={0}
          aria-label="Close"
        >
          <div className="view-customer-modal" onClick={(e) => e.stopPropagation()}>
            <div className="view-customer-modal-header">
              <h3>Stored Documents</h3>
              <button
                type="button"
                className="view-customer-modal-close"
                onClick={() => {
                  setDocOpenErr(null);
                  setDocumentsOpen(false);
                }}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="view-customer-modal-body">
              {docLoading ? (
                <p>Loading…</p>
              ) : docFiles.length === 0 ? (
                <p>No documents found.</p>
              ) : (
                <>
                  <ul className="view-customer-doc-list">
                    {docFiles.map((f) => (
                      <li key={f.name}>
                        <button
                          type="button"
                          className="doc-open-link"
                          disabled={!docSubfolder}
                          onClick={() => {
                            if (!docSubfolder) return;
                            setDocOpenErr(null);
                            void openDocumentFileInNewTab(docSubfolder, f.name, dealerId).catch((e) => {
                              setDocOpenErr(e instanceof Error ? e.message : "Could not open document");
                            });
                          }}
                        >
                          {f.name}
                        </button>
                        <span className="vc-doc-size">({(f.size / 1024).toFixed(1)} KB)</span>
                      </li>
                    ))}
                  </ul>
                  {docOpenErr ? (
                    <p className="view-customer-doc-open-error" role="alert">
                      {docOpenErr}
                    </p>
                  ) : null}
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
