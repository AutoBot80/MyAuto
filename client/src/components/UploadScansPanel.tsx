import { useRef, useState } from "react";
import { validateAadharScanFile } from "../utils/aadharScanFileValidation";

interface UploadScansPanelProps {
  isUploading: boolean;
  onUpload: (files: File[]) => Promise<void>;
  uploadStatus: string;
  uploadedFiles: string[];
  /** When set with uploadedFiles, shows pre-uploaded state (e.g. from Re-Try) */
  savedTo?: string | null;
  /** Mobile for subfolder mobile_ddmmyy */
  mobile?: string;
  /** 10-digit mobile valid */
  isMobileValid?: boolean;
  /** Upload scans to subfolder mobile_ddmmyy as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg */
  onUploadV2?: (aadharScan: File, aadharBackScan: File, salesDetail: File, insuranceSheet?: File) => Promise<void>;
  /** Single multi-page PDF: pre-OCR classify/split then Textract (mobile from document). */
  onUploadConsolidated?: (consolidatedPdf: File) => Promise<void>;
}

const SCAN_LABELS = [
  "Aadhar (front side)",
  "Aadhar (back side)",
  "Sales Detail Sheet",
] as const;

export function UploadScansPanel({
  isUploading,
  onUpload,
  uploadStatus,
  uploadedFiles,
  savedTo,
  mobile,
  isMobileValid,
  onUploadV2,
  onUploadConsolidated,
}: UploadScansPanelProps) {
  void onUpload;
  void mobile;
  const isPreUploaded = Boolean(savedTo && uploadedFiles.length > 0);
  /** When consolidated API exists: default is consolidated PDF; checking this shows per-document uploads. */
  const [uploadIndividualFiles, setUploadIndividualFiles] = useState(false);
  const [selectedConsolidatedPdf, setSelectedConsolidatedPdf] = useState<File | null>(null);
  const consolidatedInputRef = useRef<HTMLInputElement | null>(null);
  const aadharInputRef = useRef<HTMLInputElement | null>(null);
  const aadharBackInputRef = useRef<HTMLInputElement | null>(null);
  const salesInputRef = useRef<HTMLInputElement | null>(null);
  const insuranceInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedAadharFile, setSelectedAadharFile] = useState<File | null>(null);
  const [selectedAadharBackFile, setSelectedAadharBackFile] = useState<File | null>(null);
  const [selectedDetailsFile, setSelectedDetailsFile] = useState<File | null>(null);
  const [selectedInsuranceFile, setSelectedInsuranceFile] = useState<File | null>(null);
  const [hasInsurance, setHasInsurance] = useState(false);
  const [aadharFileError, setAadharFileError] = useState<string | null>(null);

  const refs = [aadharInputRef, aadharBackInputRef, salesInputRef] as const;
  const selectedFiles = [selectedAadharFile, selectedAadharBackFile, selectedDetailsFile] as const;
  const setSelectedFiles = [
    setSelectedAadharFile,
    setSelectedAadharBackFile,
    setSelectedDetailsFile,
  ] as const;

  const hasConsolidated = Boolean(onUploadConsolidated);
  /** Show Aadhaar / detail / insurance rows: always when no consolidated API; otherwise when checkbox is checked. */
  const individualMode = !hasConsolidated || uploadIndividualFiles;

  const insuranceRequired = hasInsurance ? selectedInsuranceFile : true;
  const canUploadV2 =
    individualMode &&
    onUploadV2 &&
    isMobileValid &&
    selectedAadharFile &&
    selectedAadharBackFile &&
    selectedDetailsFile &&
    insuranceRequired &&
    !isUploading;

  const canUploadConsolidated =
    hasConsolidated &&
    !individualMode &&
    selectedConsolidatedPdf &&
    !isUploading;

  function clearIndividualSelections() {
    setSelectedAadharFile(null);
    setSelectedAadharBackFile(null);
    setSelectedDetailsFile(null);
    setSelectedInsuranceFile(null);
    setHasInsurance(false);
    setAadharFileError(null);
    refs.forEach((r) => {
      if (r.current) r.current.value = "";
    });
    if (insuranceInputRef.current) insuranceInputRef.current.value = "";
  }

  function clearConsolidatedSelection() {
    setSelectedConsolidatedPdf(null);
    if (consolidatedInputRef.current) consolidatedInputRef.current.value = "";
  }

  return (
    <section className="app-panel">
      <div className="app-panel-title">Upload scans</div>
      {isPreUploaded ? (
        <div className="app-panel-row app-panel-pre-uploaded">
          <div className="app-panel-pre-uploaded-files">
            {uploadedFiles.map((f) => (
              <span key={f} className="app-panel-pre-uploaded-file">{f}</span>
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* Default: consolidated PDF — same row layout as Aadhaar / detail scans */}
          {hasConsolidated && !individualMode ? (
            <>
              <p className="app-panel-hint-consolidated" role="note">
                Consolidated PDF should contain Sales Detail Sheet, Aadhaar Front and Aadhaar Back information
              </p>
              <div className="app-panel-row app-panel-scan-row">
                <label className="app-panel-scan-label" htmlFor="upload-scan-consolidated">
                  Consolidated Scan
                </label>
                <input
                  id="upload-scan-consolidated"
                  ref={consolidatedInputRef}
                  type="file"
                  accept=".pdf,application/pdf"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) setSelectedConsolidatedPdf(file);
                    e.target.value = "";
                  }}
                />
                <button
                  type="button"
                  className="app-button app-panel-scan-button"
                  disabled={isUploading}
                  onClick={() => consolidatedInputRef.current?.click()}
                >
                  {selectedConsolidatedPdf ? selectedConsolidatedPdf.name : "Choose file"}
                </button>
              </div>
              {onUploadV2 ? (
                <div className="app-panel-row app-panel-insurance-check-row">
                  <label className="app-panel-insurance-check">
                    <input
                      type="checkbox"
                      checked={uploadIndividualFiles}
                      onChange={(e) => {
                        const on = e.target.checked;
                        setUploadIndividualFiles(on);
                        if (on) clearConsolidatedSelection();
                        else clearIndividualSelections();
                      }}
                      aria-label="I want to upload individual files"
                    />
                    <span>I want to upload individual files</span>
                  </label>
                </div>
              ) : null}
              <div className="app-panel-row app-panel-actions">
                <button
                  type="button"
                  className="app-button app-button--primary"
                  disabled={!canUploadConsolidated}
                  onClick={() => {
                    if (selectedConsolidatedPdf && onUploadConsolidated) void onUploadConsolidated(selectedConsolidatedPdf);
                  }}
                >
                  {isUploading ? "Uploading…" : "Upload documents"}
                </button>
              </div>
            </>
          ) : null}

          {/* Individual file uploads */}
          {individualMode && onUploadV2 ? (
            <>
              {hasConsolidated ? (
                <div className="app-panel-row app-panel-insurance-check-row">
                  <label className="app-panel-insurance-check">
                    <input
                      type="checkbox"
                      checked={uploadIndividualFiles}
                      onChange={(e) => {
                        const on = e.target.checked;
                        setUploadIndividualFiles(on);
                        if (!on) clearIndividualSelections();
                        else clearConsolidatedSelection();
                      }}
                      aria-label="I want to upload individual files"
                    />
                    <span>I want to upload individual files</span>
                  </label>
                </div>
              ) : null}
              <p className="app-panel-hint-aadhar" role="note">
                Aadhaar files should be only jpg, jpeg, png, img files. Max size 512 KB allowed.
              </p>
              {aadharFileError ? (
                <p className="app-panel-file-error" role="alert">
                  {aadharFileError}
                </p>
              ) : null}
              {SCAN_LABELS.map((label, index) => (
                <div key={label} className="app-panel-row app-panel-scan-row">
                  <label className="app-panel-scan-label" htmlFor={`upload-scan-${index}`}>{label}</label>
                  <input
                    id={`upload-scan-${index}`}
                    ref={refs[index]}
                    type="file"
                    accept={
                      index < 2
                        ? ".jpg,.jpeg,.png,.img,image/jpeg,image/png"
                        : ".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
                    }
                    style={{ display: "none" }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (!file) {
                        e.target.value = "";
                        return;
                      }
                      if (index < 2) {
                        const err = validateAadharScanFile(file);
                        if (err) {
                          setAadharFileError(err);
                          e.target.value = "";
                          return;
                        }
                        setAadharFileError(null);
                      }
                      setSelectedFiles[index](file);
                      e.target.value = "";
                    }}
                  />
                  <button
                    type="button"
                    className="app-button app-panel-scan-button"
                    disabled={isUploading}
                    onClick={() => refs[index].current?.click()}
                  >
                    {selectedFiles[index] ? selectedFiles[index].name : "Choose file"}
                  </button>
                </div>
              ))}
              <div className="app-panel-insurance-block">
                <div className="app-panel-row app-panel-insurance-check-row">
                  <label className="app-panel-insurance-check">
                    <input
                      type="checkbox"
                      checked={hasInsurance}
                      onChange={(e) => {
                        setHasInsurance(e.target.checked);
                        if (!e.target.checked) setSelectedInsuranceFile(null);
                      }}
                      aria-label="I have insurance"
                    />
                    <span>I have insurance</span>
                  </label>
                </div>
                <div className="app-panel-row app-panel-scan-row">
                  <label className={`app-panel-scan-label ${!hasInsurance ? "app-panel-scan-label--muted" : ""}`} htmlFor="upload-scan-insurance">
                    Insurance Sheet
                  </label>
                  <input
                    id="upload-scan-insurance"
                    ref={insuranceInputRef}
                    type="file"
                    accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
                    style={{ display: "none" }}
                    disabled={!hasInsurance}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) setSelectedInsuranceFile(file);
                      e.target.value = "";
                    }}
                  />
                  <button
                    type="button"
                    className="app-button app-panel-scan-button"
                    disabled={!hasInsurance || isUploading}
                    onClick={() => hasInsurance && insuranceInputRef.current?.click()}
                  >
                    {selectedInsuranceFile ? selectedInsuranceFile.name : "Choose file"}
                  </button>
                </div>
              </div>
              <div className="app-panel-row app-panel-actions">
                <button
                  type="button"
                  className="app-button app-button--primary"
                  disabled={!canUploadV2}
                  onClick={() => {
                    if (selectedAadharFile && selectedAadharBackFile && selectedDetailsFile)
                      onUploadV2(
                        selectedAadharFile,
                        selectedAadharBackFile,
                        selectedDetailsFile,
                        hasInsurance ? selectedInsuranceFile ?? undefined : undefined
                      );
                  }}
                >
                  {isUploading ? "Uploading…" : "Upload documents"}
                </button>
              </div>
            </>
          ) : null}
        </>
      )}
      {uploadStatus ? (
        <div className="app-panel-status">{uploadStatus}</div>
      ) : null}
    </section>
  );
}
