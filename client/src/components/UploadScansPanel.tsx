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
  /** Upload scans to subfolder mobile_ddmmyy as Aadhar.jpg, Aadhar_back.jpg, Details.jpg */
  onUploadV2?: (aadharScan: File, aadharBackScan: File, salesDetail: File) => Promise<void>;
}

const SCAN_LABELS = [
  "Aadhar (front side)",
  "Aadhar (back side)",
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
  const aadharBackInputRef = useRef<HTMLInputElement | null>(null);
  const salesInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedAadharFile, setSelectedAadharFile] = useState<File | null>(null);
  const [selectedAadharBackFile, setSelectedAadharBackFile] = useState<File | null>(null);
  const [selectedDetailsFile, setSelectedDetailsFile] = useState<File | null>(null);

  const refs = [aadharInputRef, aadharBackInputRef, salesInputRef] as const;
  const selectedFiles = [selectedAadharFile, selectedAadharBackFile, selectedDetailsFile] as const;
  const setSelectedFiles = [
    setSelectedAadharFile,
    setSelectedAadharBackFile,
    setSelectedDetailsFile,
  ] as const;

  const canUploadV2 =
    onUploadV2 &&
    isMobileValid &&
    selectedAadharFile &&
    selectedAadharBackFile &&
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
                className="app-button"
                disabled={isUploading}
                onClick={() => refs[index].current?.click()}
              >
                {selectedFiles[index] ? selectedFiles[index].name : "Choose file"}
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
                  if (selectedAadharFile && selectedAadharBackFile && selectedDetailsFile)
                    onUploadV2(selectedAadharFile, selectedAadharBackFile, selectedDetailsFile);
                }}
              >
                {isUploading ? "Uploading..." : "Upload all files"}
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
