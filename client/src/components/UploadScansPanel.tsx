import { useRef, useState } from "react";
import type { AddSalesStep } from "../types";

interface UploadScansPanelProps {
  addSalesStep: AddSalesStep;
  onStepChange: (step: AddSalesStep) => void;
  isUploading: boolean;
  onUpload: (files: File[]) => Promise<void>;
  uploadStatus: string;
  uploadedFiles: string[];
  /** Mobile for subfolder mobile_ddmmyy */
  mobile?: string;
  /** 10-digit mobile valid */
  isMobileValid?: boolean;
  /** Upload both scans to subfolder mobile_ddmmyy as Aadhar.jpg and Details.jpg */
  onUploadV2?: (aadharScan: File, salesDetail: File) => Promise<void>;
}

const SCAN_LABELS = [
  "Aadhar (front side)",
  "Sales Detail Sheet",
] as const;

export function UploadScansPanel({
  addSalesStep,
  onStepChange,
  isUploading,
  onUpload,
  uploadStatus,
  uploadedFiles,
  mobile,
  isMobileValid,
  onUploadV2,
}: UploadScansPanelProps) {
  const aadharInputRef = useRef<HTMLInputElement | null>(null);
  const salesInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedAadharFile, setSelectedAadharFile] = useState<File | null>(null);
  const [selectedDetailsFile, setSelectedDetailsFile] = useState<File | null>(null);

  const canUploadV2 =
    onUploadV2 &&
    isMobileValid &&
    selectedAadharFile &&
    selectedDetailsFile &&
    !isUploading;

  return (
    <>
      {addSalesStep === "upload-scans" ? (
        <section className="app-panel">
          <div className="app-panel-title">Upload scans</div>
          {SCAN_LABELS.map((label, index) => (
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
        </section>
      ) : (
        <div className="app-placeholder">
          <p>Step: {addSalesStep.replace("-", " ")}</p>
        </div>
      )}
    </>
  );
}
