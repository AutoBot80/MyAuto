import { useRef, useState } from "react";
import type { AddSalesStep } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { extractWithTextract } from "../api/textract";

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

export function AddSalesPage() {
  const [aadharLast4, setAadharLast4] = useState("");
  const [mobile, setMobile] = useState("");
  const [addSalesStep, setAddSalesStep] = useState<AddSalesStep>("upload-scans");
  const [formResetKey, setFormResetKey] = useState(0);
  const [textractResult, setTextractResult] = useState<{ full_text: string; error: string | null } | null>(null);
  const [textractLoading, setTextractLoading] = useState(false);
  const textractInputRef = useRef<HTMLInputElement>(null);
  const {
    upload,
    uploadV2,
    uploadStatus,
    isUploading,
    uploadedFiles,
    isAadharValid,
    isMobileValid,
    clearUploaded,
  } = useUploadScans(aadharLast4, mobile);

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

  const handleTextractTest = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setTextractLoading(true);
    setTextractResult(null);
    try {
      const data = await extractWithTextract(file);
      setTextractResult({ full_text: data.full_text ?? "", error: data.error ?? null });
    } catch (err) {
      setTextractResult({ full_text: "", error: err instanceof Error ? err.message : "Textract failed" });
    } finally {
      setTextractLoading(false);
    }
  };

  const handleNew = () => {
    setAadharLast4("");
    setMobile("");
    clearUploaded();
    setFormResetKey((k) => k + 1);
  };

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
                    <div className="add-sales-v2-dl-row"><dt>Key No.</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Engine#</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Chassis#</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Battery#</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Model</dt><dd>—</dd></div>
                    <div className="add-sales-v2-dl-row"><dt>Colour</dt><dd>—</dd></div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <h3 className="add-sales-v2-subsection-title">Test AWS Textract (Details sheet)</h3>
                  <input
                    ref={textractInputRef}
                    type="file"
                    accept=".jpg,.jpeg,.png,image/jpeg,image/png"
                    style={{ display: "none" }}
                    onChange={handleTextractTest}
                  />
                  <button
                    type="button"
                    className="app-button app-button--small"
                    onClick={() => textractInputRef.current?.click()}
                    disabled={textractLoading}
                  >
                    {textractLoading ? "Running…" : "Choose file & run Textract"}
                  </button>
                  {textractResult && (
                    <div className="add-sales-v2-textract-output">
                      {textractResult.error ? (
                        <p className="add-sales-v2-textract-error">{textractResult.error}</p>
                      ) : (
                        <pre className="add-sales-v2-textract-pre">{textractResult.full_text || "(no text detected)"}</pre>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </section>
          </div>
        </main>
      </div>
  );
}
