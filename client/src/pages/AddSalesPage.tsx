import { useState, useEffect, useRef } from "react";
import type { AddSalesStep, ExtractedVehicleDetails } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { getExtractedDetails } from "../api/aiReaderQueue";
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
  const [addSalesStep, setAddSalesStep] = useState<AddSalesStep>("upload-scans");
  const [formResetKey, setFormResetKey] = useState(0);

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
    });
  }, [aadharLast4, mobile, savedTo, uploadedFiles, uploadStatus, extractedVehicle]);

  // When we have savedTo but no vehicle data (e.g. came back from another page), fetch once to fill Extracted Information
  const hasVehicle = hasVehicleData(extractedVehicle);
  useEffect(() => {
    if (!savedTo || hasVehicle) return;
    let cancelled = false;
    getExtractedDetails(savedTo).then((details) => {
      if (cancelled) return;
      const normalized = details?.vehicle ? normalizeVehicleDetails(details.vehicle) : null;
      if (normalized) setExtractedVehicle(normalized);
    });
    return () => {
      cancelled = true;
    };
  }, [savedTo, hasVehicle]);

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
        const normalized = details?.vehicle ? normalizeVehicleDetails(details.vehicle) : null;
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
    setFormResetKey((k) => k + 1);
  };

  // Normalize for display so we always show known fields (frame_no, engine_no, etc.) regardless of key naming
  const v = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
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
                    <div className="add-sales-v2-dl-row"><dt>Name</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Address</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>City</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>State</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>PIN</dt><dd>—</dd></div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <h3 className="add-sales-v2-subsection-title">Vehicle Details</h3>
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
          </div>
        </main>
      </div>
  );
}
