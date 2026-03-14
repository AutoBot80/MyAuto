import { useRef, useState } from "react";

interface UploadScansPanelProps {
  isUploading: boolean;
  onUpload: (files: File[]) => Promise<void>;
  uploadStatus: string;
  uploadedFiles: string[];
  /** Mobile for subfolder mobile_ddmmyy */
  mobile?: string;
  /** 10-digit mobile valid */
  isMobileValid?: boolean;
  /** Upload scans to subfolder mobile_ddmmyy as Aadhar.jpg, Aadhar_back.jpg, Details.jpg; optional Insurance.jpg, Financing.jpg */
  onUploadV2?: (aadharScan: File, aadharBackScan: File, salesDetail: File, insuranceSheet?: File, financingDoc?: File) => Promise<void>;
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
  mobile,
  isMobileValid,
  onUploadV2,
}: UploadScansPanelProps) {
  const aadharInputRef = useRef<HTMLInputElement | null>(null);
  const aadharBackInputRef = useRef<HTMLInputElement | null>(null);
  const salesInputRef = useRef<HTMLInputElement | null>(null);
  const insuranceInputRef = useRef<HTMLInputElement | null>(null);
  const financingInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedAadharFile, setSelectedAadharFile] = useState<File | null>(null);
  const [selectedAadharBackFile, setSelectedAadharBackFile] = useState<File | null>(null);
  const [selectedDetailsFile, setSelectedDetailsFile] = useState<File | null>(null);
  const [selectedInsuranceFile, setSelectedInsuranceFile] = useState<File | null>(null);
  const [selectedFinancingFile, setSelectedFinancingFile] = useState<File | null>(null);
  const [hasInsurance, setHasInsurance] = useState(false);
  const [hasFinancing, setHasFinancing] = useState(false);

  const refs = [aadharInputRef, aadharBackInputRef, salesInputRef] as const;
  const selectedFiles = [selectedAadharFile, selectedAadharBackFile, selectedDetailsFile] as const;
  const setSelectedFiles = [
    setSelectedAadharFile,
    setSelectedAadharBackFile,
    setSelectedDetailsFile,
  ] as const;

  const insuranceRequired = hasInsurance ? selectedInsuranceFile : true;
  const financingRequired = hasFinancing ? selectedFinancingFile : true;
  const canUploadV2 =
    onUploadV2 &&
    isMobileValid &&
    selectedAadharFile &&
    selectedAadharBackFile &&
    selectedDetailsFile &&
    insuranceRequired &&
    financingRequired &&
    !isUploading;

  return (
    <section className="app-panel">
      <div className="app-panel-title">Upload scans</div>
      {SCAN_LABELS.map((label, index) => (
        <div key={label} className="app-panel-row app-panel-scan-row">
          <label className="app-panel-scan-label">{label}</label>
          <input
            ref={refs[index]}
            type="file"
            accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) setSelectedFiles[index](file);
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
      {onUploadV2 && (
        <div className="app-panel-financing-block">
          <div className="app-panel-row app-panel-insurance-check-row">
            <label className="app-panel-insurance-check">
              <input
                type="checkbox"
                checked={hasFinancing}
                onChange={(e) => {
                  setHasFinancing(e.target.checked);
                  if (!e.target.checked) setSelectedFinancingFile(null);
                }}
                aria-label="I have financing"
              />
              <span>I have financing</span>
            </label>
          </div>
          <div className="app-panel-row app-panel-scan-row">
            <label className={`app-panel-scan-label ${!hasFinancing ? "app-panel-scan-label--muted" : ""}`}>
              Financing document
            </label>
            <input
              ref={financingInputRef}
              type="file"
              accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
              style={{ display: "none" }}
              disabled={!hasFinancing}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) setSelectedFinancingFile(file);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="app-button app-panel-scan-button"
              disabled={!hasFinancing || isUploading}
              onClick={() => hasFinancing && financingInputRef.current?.click()}
            >
              {selectedFinancingFile ? selectedFinancingFile.name : "Choose file"}
            </button>
          </div>
        </div>
      )}
      {onUploadV2 && (
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
            <label className={`app-panel-scan-label ${!hasInsurance ? "app-panel-scan-label--muted" : ""}`}>
              Insurance Sheet
            </label>
            <input
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
      )}
      {onUploadV2 && (
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
                  hasInsurance ? selectedInsuranceFile ?? undefined : undefined,
                  hasFinancing ? selectedFinancingFile ?? undefined : undefined
                );
            }}
          >
            {isUploading ? "Uploading..." : "Upload all files"}
          </button>
        </div>
      )}
      {uploadStatus ? (
        <div className="app-panel-status">{uploadStatus}</div>
      ) : null}
    </section>
  );
}
