import { useState } from "react";
import type { AddSalesStep } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";

export function AddSalesPage() {
  const [aadharLast4, setAadharLast4] = useState("");
  const [addSalesStep, setAddSalesStep] = useState<AddSalesStep>("upload-scans");
  const {
    upload,
    uploadStatus,
    isUploading,
    uploadedFiles,
    isAadharValid,
    clearUploaded,
  } = useUploadScans(aadharLast4);

  return (
    <>
      <h2>Add Sales</h2>
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
              clearUploaded();
            }}
            aria-invalid={aadharLast4.length > 0 && !isAadharValid}
          />
        </label>
        <div className="app-field-hint">
          {isAadharValid ? "Valid" : "Enter 4 digits"}
        </div>
      </div>
      <UploadScansPanel
        addSalesStep={addSalesStep}
        onStepChange={setAddSalesStep}
        isAadharValid={isAadharValid}
        isUploading={isUploading}
        onUpload={upload}
        uploadStatus={uploadStatus}
        uploadedFiles={uploadedFiles}
      />
    </>
  );
}
