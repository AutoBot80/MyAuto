import { useRef, useState } from "react";
import type { AddSalesStep } from "../types";

interface UploadScansPanelProps {
  addSalesStep: AddSalesStep;
  onStepChange: (step: AddSalesStep) => void;
  isAadharValid: boolean;
  isUploading: boolean;
  onUpload: (files: File[]) => Promise<void>;
  uploadStatus: string;
  uploadedFiles: string[];
  /** When false, only the step content is shown (no tiles). Used for V2 left-bar layout. */
  showTiles?: boolean;
  /** V2: mobile for subfolder mobile_ddmmyy */
  mobile?: string;
  /** V2: 10-digit mobile valid */
  isMobileValid?: boolean;
  /** V2: upload both scans to subfolder mobile_ddmmyy as Aadhar.jpg and Details.jpg */
  onUploadV2?: (aadharScan: File, salesDetail: File) => Promise<void>;
}

const STEPS: { id: AddSalesStep; label: string; num: number }[] = [
  { id: "upload-scans", label: "Upload scans", num: 1 },
  { id: "insurance", label: "Insurance", num: 2 },
  { id: "hero-dms", label: "DMS", num: 3 },
  { id: "rto", label: "RTO", num: 4 },
];

const V2_SCAN_LABELS = [
  "Aadhar (front side)",
  "Sales Detail Sheet",
] as const;

export function UploadScansPanel({
  addSalesStep,
  onStepChange,
  isAadharValid,
  isUploading,
  onUpload,
  uploadStatus,
  uploadedFiles,
  showTiles = true,
  mobile,
  isMobileValid,
  onUploadV2,
}: UploadScansPanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const aadharInputRef = useRef<HTMLInputElement | null>(null);
  const salesInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedAadharFile, setSelectedAadharFile] = useState<File | null>(null);
  const [selectedDetailsFile, setSelectedDetailsFile] = useState<File | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files ? Array.from(e.target.files) : [];
    if (!list.length) return;
    onUpload(list).then(() => {
      e.target.value = "";
    });
  };

  const canUploadV2 =
    onUploadV2 &&
    isMobileValid &&
    selectedAadharFile &&
    selectedDetailsFile &&
    !isUploading;

  const uploadScansContent = !showTiles ? (
    <>
      <div className="app-panel-title">Upload scans</div>
      {V2_SCAN_LABELS.map((label, index) => (
        <div key={label} className="app-panel-row app-panel-scan-row">
          <label className="app-panel-scan-label">{label}</label>
          <input
            ref={index === 0 ? aadharInputRef : salesInputRef}
            type="file"
            accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                if (index === 0) setSelectedAadharFile(file);
                else setSelectedDetailsFile(file);
              }
              e.target.value = "";
            }}
          />
          <button
            type="button"
            className="app-button"
            disabled={isUploading}
            onClick={() =>
              (index === 0 ? aadharInputRef : salesInputRef).current?.click()
            }
          >
            {index === 0 && selectedAadharFile
              ? selectedAadharFile.name
              : index === 1 && selectedDetailsFile
                ? selectedDetailsFile.name
                : "Choose file"}
          </button>
        </div>
      ))}
      {onUploadV2 && (
        <div className="app-panel-row app-panel-actions">
          <button
            type="button"
            className="app-button app-button--primary"
            disabled={!canUploadV2}
            onClick={() => {
              if (selectedAadharFile && selectedDetailsFile)
                onUploadV2(selectedAadharFile, selectedDetailsFile);
            }}
          >
            {isUploading ? "Uploading..." : "Upload both"}
          </button>
        </div>
      )}
      {uploadStatus ? (
        <div className="app-panel-status">{uploadStatus}</div>
      ) : null}
      {uploadedFiles.length > 0 ? (
        <div className="app-panel-uploaded">
          <div className="app-panel-uploaded-title">Uploaded successfully</div>
          <ul className="app-panel-uploaded-list">
            {uploadedFiles.map((f, idx) => (
              <li key={`${f}-${idx}`}>{f}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  ) : (
    <>
      <div className="app-panel-title">Upload scans</div>
      <div className="app-panel-row">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
          style={{ display: "none" }}
          onChange={async (e) => {
            const list = e.target.files ? Array.from(e.target.files) : [];
            if (!list.length) return;
            await onUpload(list);
            e.target.value = "";
          }}
        />
      </div>
      <div className="app-panel-row app-panel-actions">
        <button
          type="button"
          disabled={isUploading || !isAadharValid}
          onClick={() => fileInputRef.current?.click()}
        >
          {isUploading ? "Uploading..." : "Choose files"}
        </button>
      </div>
      {uploadStatus ? (
        <div className="app-panel-status">{uploadStatus}</div>
      ) : null}
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
    </>
  );

  return (
    <>
      {showTiles && (
        <div className="app-tiles">
          {STEPS.map(({ id, label, num }) => (
            <button
              key={id}
              type="button"
              className={`app-tile ${addSalesStep === id ? "active" : ""}`}
              onClick={() => onStepChange(id)}
            >
              <div className="app-tile-step">{num}</div>
              <div className="app-tile-title">{label}</div>
            </button>
          ))}
        </div>
      )}
      {addSalesStep === "upload-scans" ? (
        <section className="app-panel">
          {uploadScansContent}
        </section>
      ) : (
        <div className="app-placeholder">
          <p>Step: {addSalesStep.replace("-", " ")}</p>
        </div>
      )}
    </>
  );
}
