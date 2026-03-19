import { useState, useEffect } from "react";
import {
  searchCustomer,
  getFormVahanView,
  getDocumentFileUrl,
  type CustomerSearchResult,
  type FormVahanViewResult,
} from "../api/customerSearch";
import { apiFetch } from "../api/client";
import { DEALER_ID } from "../api/dealerId";
import { loadViewCustomer, saveViewCustomer } from "../utils/viewCustomerStorage";

interface ViewCustomerPageProps {
  /** Optional: pre-fill search from URL or context */
  initialMobile?: string;
  dealerId?: number;
}

function getInitialState(initialMobile: string) {
  const stored = loadViewCustomer();
  return {
    mobile: initialMobile || stored.mobile,
    plateNum: stored.plateNum,
    result: stored.result,
    selectedVehicleId: stored.selectedVehicleId,
  };
}

export function ViewCustomerPage({ initialMobile = "", dealerId = DEALER_ID }: ViewCustomerPageProps) {
  const [mobile, setMobile] = useState(() => getInitialState(initialMobile).mobile);
  const [plateNum, setPlateNum] = useState(() => getInitialState(initialMobile).plateNum);
  const [result, setResult] = useState<CustomerSearchResult | null>(
    () => getInitialState(initialMobile).result
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(
    () => getInitialState(initialMobile).selectedVehicleId
  );
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [docFiles, setDocFiles] = useState<{ name: string; size: number }[]>([]);
  const [docLoading, setDocLoading] = useState(false);
  const [formVahan, setFormVahan] = useState<FormVahanViewResult | null>(null);
  const [formVahanLoading, setFormVahanLoading] = useState(false);

  useEffect(() => {
    saveViewCustomer({ mobile, plateNum, result, selectedVehicleId });
  }, [mobile, plateNum, result, selectedVehicleId]);

  const handleSearch = async () => {
    const m = mobile.trim();
    const p = plateNum.trim();
    if (!m && !p) {
      setError("Enter customer mobile or vehicle plate number");
      return;
    }
    setError(null);
    setLoading(true);
    setResult(null);
    try {
      const res = await searchCustomer({ mobile: m || null, plate_num: p || null, dealer_id: dealerId });
      setResult(res);
      if (res.found && res.vehicles.length === 1) {
        setSelectedVehicleId(res.vehicles[0].vehicle_id);
      } else {
        setSelectedVehicleId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  };

  const openDocuments = async (subfolder: string) => {
    if (!subfolder?.trim()) return;
    setDocumentsOpen(true);
    setDocLoading(true);
    setDocFiles([]);
    try {
      const res = await apiFetch<{ files: { name: string; size: number }[] }>(
        `/documents/${encodeURIComponent(subfolder)}/list?dealer_id=${dealerId}`
      );
      setDocFiles(res.files || []);
    } catch {
      setDocFiles([]);
    } finally {
      setDocLoading(false);
    }
  };

  const cust = result?.found ? result.customer : null;
  const vehicles = result?.vehicles ?? [];
  const insMap = result?.insurance_by_vehicle ?? {};
  const selectedVehicle = selectedVehicleId
    ? vehicles.find((v) => v.vehicle_id === selectedVehicleId)
    : vehicles[0];
  const selectedFileLocation = selectedVehicle?.file_location ?? null;
  const selectedIns = selectedVehicle
    ? insMap[selectedVehicle.vehicle_id]
    : null;
  const selectedCustomerId = cust?.customer_id ?? null;
  const selectedVehicleKey = selectedVehicle?.vehicle_id ?? null;

  useEffect(() => {
    if (!selectedCustomerId || !selectedVehicleKey) {
      setFormVahan(null);
      setFormVahanLoading(false);
      return;
    }
    let cancelled = false;
    setFormVahanLoading(true);
    getFormVahanView(selectedCustomerId, selectedVehicleKey)
      .then((res) => {
        if (!cancelled) setFormVahan(res);
      })
      .catch(() => {
        if (!cancelled) setFormVahan({ found: false, columns: [], row: null });
      })
      .finally(() => {
        if (!cancelled) setFormVahanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedCustomerId, selectedVehicleKey]);

  return (
    <div className="view-customer-page">
      {/* Search section */}
      <section className="view-customer-search">
        <div className="view-customer-search-row">
          <label htmlFor="vc-mobile">Customer Mobile</label>
          <input
            id="vc-mobile"
            type="text"
            placeholder="e.g. 9876543210"
            value={mobile}
            onChange={(e) => setMobile(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div className="view-customer-search-row">
          <label htmlFor="vc-plate">Vehicle Plate No.</label>
          <input
            id="vc-plate"
            type="text"
            placeholder="e.g. RJ29AB1234"
            value={plateNum}
            onChange={(e) => setPlateNum(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <button
          type="button"
          className="app-button app-button--primary view-customer-search-btn"
          onClick={handleSearch}
          disabled={loading}
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </section>

      {error && <p className="view-customer-error">{error}</p>}
      {result && !result.found && result.message && (
        <p className="view-customer-message">{result.message}</p>
      )}

      {/* Customer details */}
      {cust && (
        <section className="view-customer-details">
          <div className="view-customer-details-inner">
            <div className="view-customer-details-grid">
              <div className="view-customer-detail-item">
                <span className="vc-label">Name</span>
                <span className="vc-value">{cust.name ?? "—"}</span>
              </div>
              <div className="view-customer-detail-item">
                <span className="vc-label">Mobile</span>
                <span className="vc-value">
                  {cust.mobile ?? cust.mobile_number ?? "—"}
                </span>
              </div>
              <div className="view-customer-detail-item">
                <span className="vc-label">DOB</span>
                <span className="vc-value">{cust.date_of_birth ?? "—"}</span>
              </div>
              <div className="view-customer-detail-item">
                <span className="vc-label">Address</span>
                <span className="vc-value">
                  {[cust.address, cust.city, cust.state, cust.pin]
                    .filter(Boolean)
                    .join(", ") || "—"}
                </span>
              </div>
            </div>
            <button
              type="button"
              className="view-customer-folder-btn"
              onClick={() => selectedFileLocation && openDocuments(selectedFileLocation)}
              title="View documents for selected vehicle"
              disabled={!selectedFileLocation}
              aria-label="Open documents folder"
            >
              <FolderIcon />
            </button>
          </div>
        </section>
      )}

      {/* Vehicles table */}
      {vehicles.length > 0 && (
        <section className="view-customer-vehicles">
          <div className="view-customer-table-wrap">
            <table className="view-customer-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Colour</th>
                  <th>Plate No.</th>
                  <th>Date of Purchase</th>
                </tr>
              </thead>
              <tbody>
                {vehicles.map((v) => (
                  <tr
                    key={v.vehicle_id}
                    className={
                      selectedVehicleId === v.vehicle_id ? "selected" : ""
                    }
                    onClick={() => setSelectedVehicleId(v.vehicle_id)}
                  >
                    <td>{v.model ?? "—"}</td>
                    <td>{v.colour ?? "—"}</td>
                    <td>{v.plate_num ?? "—"}</td>
                    <td>{v.date_of_purchase ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Insurance & Service tiles (when vehicle selected or only one) */}
      {selectedVehicle && (
        <section className="view-customer-tiles">
          <div className="view-customer-tile">
            <h4 className="view-customer-tile-title">Insurance</h4>
            {selectedIns ? (
              <dl className="view-customer-tile-dl">
                <dt>Provider</dt>
                <dd>{selectedIns.insurer ?? "—"}</dd>
                <dt>Policy ID</dt>
                <dd>{selectedIns.policy_num ?? "—"}</dd>
                <dt>From</dt>
                <dd>{selectedIns.policy_from ?? "—"}</dd>
                <dt>To</dt>
                <dd>{selectedIns.policy_to ?? "—"}</dd>
              </dl>
            ) : (
              <p className="view-customer-tile-empty">No insurance record</p>
            )}
          </div>
          <div className="view-customer-tile">
            <h4 className="view-customer-tile-title">Service</h4>
            <p className="view-customer-tile-empty">—</p>
          </div>
        </section>
      )}

      {selectedVehicle && (
        <section className="view-customer-vahan-view">
          <div className="view-customer-vahan-wrap">
            {formVahanLoading ? (
              <p className="view-customer-tile-empty">Loading Vahan values…</p>
            ) : !formVahan?.found || !formVahan.row || formVahan.columns.length === 0 ? (
              <p className="view-customer-tile-empty">No Vahan view row available for this vehicle.</p>
            ) : (
              <table className="view-customer-vahan-table">
                <thead>
                  <tr>
                    {formVahan.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {formVahan.columns.map((column) => {
                      const value = formVahan.row?.[column];
                      return <td key={column}>{value == null || value === "" ? "—" : String(value)}</td>;
                    })}
                  </tr>
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}

      {/* Documents modal */}
      {documentsOpen && (
        <div
          className="view-customer-modal-backdrop"
          onClick={() => setDocumentsOpen(false)}
          onKeyDown={(e) => e.key === "Escape" && setDocumentsOpen(false)}
          role="button"
          tabIndex={0}
          aria-label="Close"
        >
          <div
            className="view-customer-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="view-customer-modal-header">
              <h3>Stored Documents</h3>
              <button
                type="button"
                className="view-customer-modal-close"
                onClick={() => setDocumentsOpen(false)}
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
                <ul className="view-customer-doc-list">
                  {docFiles.map((f) => (
                    <li key={f.name}>
                      <a
                        href={selectedFileLocation ? getDocumentFileUrl(selectedFileLocation, f.name, dealerId) : "#"}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {f.name}
                      </a>
                      <span className="vc-doc-size">
                        ({(f.size / 1024).toFixed(1)} KB)
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FolderIcon() {
  return (
    <svg
      width="24"
      height="24"
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
