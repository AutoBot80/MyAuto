import { useState, useEffect, useRef } from "react";
import type { AddSalesStep, ExtractedVehicleDetails, ExtractedCustomerDetails } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { decodeQrFromImage } from "../api/qrDecode";
import type { QrDecodeResponse } from "../api/qrDecode";
import { QR_FIELD_ORDER, QR_FIELD_LABELS } from "../api/qrDecode";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
import { normalizeVehicleDetails, hasVehicleData } from "../utils/vehicleDetails";

const ADD_SALES_STEPS: { id: AddSalesStep; label: string; num: number }[] = [
  { id: "upload-scans", label: "Upload scans", num: 1 },
  { id: "insurance", label: "Insurance", num: 2 },
  { id: "hero-dms", label: "DMS", num: 3 },
  { id: "rto", label: "RTO", num: 4 },
];

const ADD_SALES_NAV = [
  { ...ADD_SALES_STEPS[0], label: "Customer Info." },
  ...ADD_SALES_STEPS.slice(1),
];

function getInitialForm() {
  const d = loadAddSalesForm();
  return d;
}

export function AddSalesPage() {
  const [aadharLast4, setAadharLast4] = useState(() => getInitialForm().aadharLast4);
  const [mobile, setMobile] = useState(() => getInitialForm().mobile);
  const [savedTo, setSavedTo] = useState<string | null>(() => getInitialForm().savedTo);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>(() => getInitialForm().uploadedFiles);
  const [uploadStatus, setUploadStatus] = useState(() => getInitialForm().uploadStatus);
  const [extractedVehicle, setExtractedVehicle] = useState<ExtractedVehicleDetails | null>(
    () => getInitialForm().extractedVehicle
  );
  const [extractedCustomer, setExtractedCustomer] = useState<ExtractedCustomerDetails | null>(
    () => getInitialForm().extractedCustomer
  );
  const [addSalesStep, setAddSalesStep] = useState<AddSalesStep>("upload-scans");
  const [formResetKey, setFormResetKey] = useState(0);
  const [extractedRefreshLoading, setExtractedRefreshLoading] = useState(false);
  // Temporary: QR decode box
  const [qrDecodeLoading, setQrDecodeLoading] = useState(false);
  const [qrDecodeResult, setQrDecodeResult] = useState<QrDecodeResponse | null>(null);
  const qrFileInputRef = useRef<HTMLInputElement>(null);

  const {
    upload,
    uploadV2,
    isUploading,
    isAadharValid,
    isMobileValid,
    clearUploaded,
  } = useUploadScans(aadharLast4, mobile, {
    savedTo,
    setSavedTo,
    uploadedFiles,
    setUploadedFiles,
    uploadStatus,
    setUploadStatus,
  });

  const pollCountRef = useRef(0);
  const POLL_MAX = 20;
  const POLL_INTERVAL_MS = 2000;

  // Persist form state so it survives navigation; clear only on "New"
  useEffect(() => {
    saveAddSalesForm({
      aadharLast4,
      mobile,
      savedTo,
      uploadedFiles,
      uploadStatus,
      extractedVehicle,
      extractedCustomer,
    });
  }, [aadharLast4, mobile, savedTo, uploadedFiles, uploadStatus, extractedVehicle, extractedCustomer]);

  const hasCustomer = Boolean(
    extractedCustomer &&
    (extractedCustomer.name || extractedCustomer.address || extractedCustomer.city || extractedCustomer.state || extractedCustomer.pin)
  );
  // When we have savedTo but missing vehicle or customer data, fetch once to fill Extracted Information
  const hasVehicle = hasVehicleData(extractedVehicle);
  useEffect(() => {
    if (!savedTo || (hasVehicle && hasCustomer)) return;
    let cancelled = false;
    getExtractedDetails(savedTo)
      .then((details) => {
        if (cancelled) return;
        const rawVehicle = details?.vehicle ?? details;
        const normalized = normalizeVehicleDetails(rawVehicle);
        if (normalized) setExtractedVehicle(normalized);
        const cust = details?.customer;
        if (cust && typeof cust === "object" && !Array.isArray(cust)) {
          setExtractedCustomer({
            name: String((cust as Record<string, unknown>).name ?? "").trim() || undefined,
            address: String((cust as Record<string, unknown>).address ?? "").trim() || undefined,
            city: String((cust as Record<string, unknown>).city ?? "").trim() || undefined,
            state: String((cust as Record<string, unknown>).state ?? "").trim() || undefined,
            pin: String((cust as Record<string, unknown>).pin ?? "").trim() || undefined,
          });
        }
      })
      .catch(() => {
        // Fetch failed (404, network, etc.) – keep existing state
      });
    return () => {
      cancelled = true;
    };
  }, [savedTo, hasVehicle, hasCustomer]);

  // Poll for extracted details when savedTo is set (e.g. right after upload)
  useEffect(() => {
    if (!savedTo) {
      pollCountRef.current = 0;
      return;
    }
    pollCountRef.current = 0;

    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (pollCountRef.current >= POLL_MAX) return;
      pollCountRef.current += 1;
      try {
        const details = await getExtractedDetails(savedTo);
        const rawVehicle = details?.vehicle ?? details;
        const normalized = normalizeVehicleDetails(rawVehicle);
        const cust = details?.customer;
        if (cust && typeof cust === "object" && !Array.isArray(cust)) {
          setExtractedCustomer({
            name: String((cust as Record<string, unknown>).name ?? "").trim() || undefined,
            address: String((cust as Record<string, unknown>).address ?? "").trim() || undefined,
            city: String((cust as Record<string, unknown>).city ?? "").trim() || undefined,
            state: String((cust as Record<string, unknown>).state ?? "").trim() || undefined,
            pin: String((cust as Record<string, unknown>).pin ?? "").trim() || undefined,
          });
        }
        if (normalized) {
          setExtractedVehicle(normalized);
          if (intervalId) clearInterval(intervalId);
          return;
        }
      } catch {
        // keep polling
      }
    };

    poll();
    intervalId = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [savedTo]);

  const aadharBlock = (
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
          }}
          aria-invalid={aadharLast4.length > 0 && !isAadharValid}
        />
      </label>
    </div>
  );

  const mobileBlock = (
    <div className="app-field-row">
      <label className="app-field">
        <div className="app-field-label">Customer Mobile (10 digits)</div>
        <input
          className="app-field-input"
          inputMode="numeric"
          placeholder="9876543210"
          value={mobile}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D/g, "").slice(0, 10);
            setMobile(digits);
          }}
          aria-invalid={mobile.length > 0 && !isMobileValid}
        />
      </label>
    </div>
  );

  const handleNew = () => {
    clearAddSalesForm();
    setAadharLast4("");
    setMobile("");
    clearUploaded();
    setExtractedVehicle(null);
    setExtractedCustomer(null);
    setFormResetKey((k) => k + 1);
  };

  const handleQrDecode = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setQrDecodeLoading(true);
    setQrDecodeResult(null);
    try {
      const res = await decodeQrFromImage(file);
      setQrDecodeResult(res);
    } catch (err) {
      setQrDecodeResult({
        decoded: [],
        error: err instanceof Error ? err.message : "Decode failed",
      });
    } finally {
      setQrDecodeLoading(false);
    }
  };

  const handleRefreshExtracted = async () => {
    if (!savedTo) return;
    setExtractedRefreshLoading(true);
    try {
      const details = await getExtractedDetails(savedTo);
      const rawVehicle = details?.vehicle ?? details;
      const normalized = normalizeVehicleDetails(rawVehicle);
      if (normalized) setExtractedVehicle(normalized);
      const cust = details?.customer;
      if (cust && typeof cust === "object" && !Array.isArray(cust)) {
        setExtractedCustomer({
          name: String((cust as Record<string, unknown>).name ?? "").trim() || undefined,
          address: String((cust as Record<string, unknown>).address ?? "").trim() || undefined,
          city: String((cust as Record<string, unknown>).city ?? "").trim() || undefined,
          state: String((cust as Record<string, unknown>).state ?? "").trim() || undefined,
          pin: String((cust as Record<string, unknown>).pin ?? "").trim() || undefined,
        });
      }
    } finally {
      setExtractedRefreshLoading(false);
    }
  };

  // Normalize for display so we always show known fields (frame_no, engine_no, etc.) regardless of key naming
  const v = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
  const c = extractedCustomer;
  const display = (s: string | undefined) => (s && String(s).trim() ? String(s).trim() : "—");

  const panel = (
    <UploadScansPanel
      key={formResetKey}
      addSalesStep={addSalesStep}
      onStepChange={setAddSalesStep}
      isAadharValid={isAadharValid}
      isUploading={isUploading}
      onUpload={upload}
      uploadStatus={uploadStatus}
      uploadedFiles={uploadedFiles}
      showTiles={false}
      mobile={mobile}
      isMobileValid={isMobileValid}
      onUploadV2={uploadV2}
    />
  );

  return (
    <div className="add-sales-v2">
      <nav className="add-sales-v2-nav" aria-label="Add sales steps">
        {ADD_SALES_NAV.map(({ id, label, num }) => (
            <button
              key={id}
              type="button"
              className={`add-sales-v2-nav-item ${addSalesStep === id ? "active" : ""}`}
              onClick={() => setAddSalesStep(id)}
            >
              <span className="add-sales-v2-nav-num">{num}</span>
              <span className="add-sales-v2-nav-label">{label}</span>
            </button>
          ))}
        </nav>
        <main className="add-sales-v2-main">
          <div className="add-sales-v2-two-col">
            <section className="add-sales-v2-box add-sales-v2-box-upload">
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">Upload Customer Information</h2>
                <button
                  type="button"
                  className="app-button"
                  onClick={handleNew}
                  title="Start a new entry"
                >
                  New
                </button>
              </div>
              <div className="add-sales-v2-box-body">
                <div className="add-sales-v2-fields-row">
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-aadhar">
                    {aadharBlock}
                  </div>
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-mobile">
                    {mobileBlock}
                  </div>
                </div>
                <div className="add-sales-v2-panel-wrap">
                  {panel}
                </div>
              </div>
            </section>
            <section className="add-sales-v2-box add-sales-v2-box-extracted">
              <h2 className="add-sales-v2-box-title">Extracted Information <span className="add-sales-v2-box-title-note">(AI enabled)</span></h2>
              <div className="add-sales-v2-box-body">
                <div className="add-sales-v2-subsection">
                  <h3 className="add-sales-v2-subsection-title">Customer Details</h3>
                  <dl className="add-sales-v2-dl">
                    <div className="add-sales-v2-dl-row"><dt>Name</dt><dd>{display(c?.name)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Address</dt><dd>{display(c?.address)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>City</dt><dd>{display(c?.city)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>State</dt><dd>{display(c?.state)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>PIN</dt><dd>{display(c?.pin)}</dd></div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Vehicle Details</h3>
                    {savedTo && (
                      <button
                        type="button"
                        className="app-button app-button--small"
                        onClick={handleRefreshExtracted}
                        disabled={extractedRefreshLoading}
                        title="Reload customer (Aadhar) and vehicle (Details sheet) from server"
                      >
                        {extractedRefreshLoading ? "…" : "Refresh"}
                      </button>
                    )}
                  </div>
                  <dl className="add-sales-v2-dl">
                    <div className="add-sales-v2-dl-row"><dt>Frame no.</dt><dd>{display(v?.frame_no)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Engine No.</dt><dd>{display(v?.engine_no)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Model & colour</dt><dd>{display(v?.model_colour)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Key no.</dt><dd>{display(v?.key_no)}</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Battery no.</dt><dd>{display(v?.battery_no)}</dd></div>
                  </dl>
                </div>
              </div>
            </section>
            {/* Temporary: QR decode from scan image */}
            <section className="add-sales-v2-box add-sales-v2-box-qr-temp">
              <h2 className="add-sales-v2-box-title">
                QR decode <span className="add-sales-v2-box-title-note">(temporary)</span>
              </h2>
              <div className="add-sales-v2-box-body">
                <input
                  ref={qrFileInputRef}
                  type="file"
                  accept="image/*,.jpg,.jpeg,.png,.webp"
                  aria-label="Choose scan image with QR"
                  className="add-sales-v2-qr-input"
                  onChange={handleQrDecode}
                  disabled={qrDecodeLoading}
                />
                <p className="add-sales-v2-qr-hint">Use an image under 2 MB (crop to the QR if needed).</p>
                <button
                  type="button"
                  className="app-button app-button--small"
                  onClick={() => qrFileInputRef.current?.click()}
                  disabled={qrDecodeLoading}
                >
                  {qrDecodeLoading ? "Decoding…" : "Upload image & decode QR"}
                </button>
                {qrDecodeResult && (
                  <div className="add-sales-v2-qr-output">
                    {qrDecodeResult.error && (
                      <p className="add-sales-v2-qr-error">{qrDecodeResult.error}</p>
                    )}
                    {qrDecodeResult.decoded.length > 0 ? (() => {
                      const first = qrDecodeResult.decoded[0];
                      const fields = first?.fields ?? {};
                      const hasAnyField = QR_FIELD_ORDER.some((k) => fields[k] != null && String(fields[k]).trim() !== "");
                      return (
                        <div className="add-sales-v2-qr-result">
                          {qrDecodeResult.decoded.length > 1 && (
                            <p className="add-sales-v2-qr-multi">{qrDecodeResult.decoded.length} QR code(s) found; showing first.</p>
                          )}
                          {hasAnyField ? (
                            <dl className="add-sales-v2-qr-dl">
                              {QR_FIELD_ORDER.map((key) => {
                                const val = fields[key];
                                if (val == null || String(val).trim() === "") return null;
                                return (
                                  <div key={key} className="add-sales-v2-dl-row">
                                    <dt>{QR_FIELD_LABELS[key]}</dt>
                                    <dd>{String(val).trim()}</dd>
                                  </div>
                                );
                              })}
                            </dl>
                          ) : (
                            <p className="add-sales-v2-qr-no-fields">QR decoded but no standard fields matched. Raw data may be in a different format.</p>
                          )}
                        </div>
                      );
                    })() : !qrDecodeResult.error && (
                      <p className="add-sales-v2-qr-no-output">No QR code found or no data extracted.</p>
                    )}
                  </div>
                )}
              </div>
            </section>
          </div>
        </main>
      </div>
  );
}
