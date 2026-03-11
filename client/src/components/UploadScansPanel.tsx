import { useRef } from "react";
import type { AddSalesStep } from "../types";

interface UploadScansPanelProps {
  addSalesStep: AddSalesStep;
  onStepChange: (step: AddSalesStep) => void;
  isAadharValid: boolean;
  isUploading: boolean;
  onUpload: (files: File[]) => Promise<void>;
  uploadStatus: string;
  uploadedFiles: string[];
}

const STEPS: { id: AddSalesStep; label: string; num: number }[] = [
  { id: "upload-scans", label: "Upload scans", num: 1 },
  { id: "insurance", label: "Insurance", num: 2 },
  { id: "hero-dms", label: "Hero DMS", num: 3 },
  { id: "rto", label: "RTO", num: 4 },
];

export function UploadScansPanel({
  addSalesStep,
  onStepChange,
  isAadharValid,
  isUploading,
  onUpload,
  uploadStatus,
  uploadedFiles,
}: UploadScansPanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  return (
    <>
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
      {addSalesStep === "upload-scans" ? (
        <section className="app-panel">
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
        </section>
      ) : (
        <div className="app-placeholder">
          <p>Step: {addSalesStep.replace("-", " ")}</p>
        </div>
      )}
    </>
  );
}
