import { useRef, useState } from "react";
import type { AddSalesStep } from "../types";

/** Vehicle details scraped from DMS (or already in extracted info) to show on DMS page */
export interface DmsVehicleDisplay {
  key_no?: string;
  frame_no?: string;
  engine_no?: string;
  model?: string;
  color?: string;
  cubic_capacity?: string;
  total_amount?: string;
  year_of_mfg?: string;
}

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
  /** When on DMS step, called when user clicks Fill DMS (e.g. trigger automation). */
  onFillDms?: () => void;
  fillDmsStatus?: string | null;
  isFillDmsLoading?: boolean;
  /** Vehicle details to show on DMS step (from Fill DMS or existing extracted info). */
  dmsVehicleDetails?: DmsVehicleDisplay | null;
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
  onFillDms,
  fillDmsStatus,
  isFillDmsLoading,
  dmsVehicleDetails,
}: UploadScansPanelProps) {
  const d = dmsVehicleDetails;
  const hasDmsVehicle = d && (d.key_no ?? d.frame_no ?? d.engine_no ?? d.model ?? d.color ?? d.cubic_capacity ?? d.total_amount ?? d.year_of_mfg);
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
      ) : addSalesStep === "rto" ? (
        <section className="app-panel app-panel-dms-step">
          <div className="app-panel-title">RTO (Vahan)</div>
          <p className="app-panel-dms-text">Vahan has been opened in a new browser tab. Use the button below to fill RTO details from extracted information.</p>
          <div className="app-panel-row app-panel-actions">
            <button
              type="button"
              className="app-button app-button--primary"
              onClick={() => {}}
            >
              Fill RTO Details
            </button>
          </div>
        </section>
      ) : addSalesStep === "hero-dms" ? (
        <section className="app-panel app-panel-dms-step">
          <div className="app-panel-title">DMS</div>
          <p className="app-panel-dms-text">Press the button below to open the DMS in a browser and fill login, customer details, and vehicle search from extracted information.</p>
          <div className="app-panel-row app-panel-actions">
            <button
              type="button"
              className="app-button app-button--primary"
              disabled={isFillDmsLoading}
              onClick={() => onFillDms?.()}
            >
              {isFillDmsLoading ? "Filling DMS…" : "Fill DMS"}
            </button>
          </div>
          {fillDmsStatus && (
            <div className="app-panel-status" role="status">{fillDmsStatus}</div>
          )}
          {hasDmsVehicle && (
            <div className="app-panel-dms-vehicle">
              <div className="app-panel-dms-vehicle-title">Details from DMS</div>
              <dl className="app-panel-dms-vehicle-dl">
                {d?.key_no != null && d.key_no !== "" && <><dt>Key no.</dt><dd>{d.key_no}</dd></>}
                {d?.frame_no != null && d.frame_no !== "" && <><dt>Frame no.</dt><dd>{d.frame_no}</dd></>}
                {d?.engine_no != null && d.engine_no !== "" && <><dt>Engine no.</dt><dd>{d.engine_no}</dd></>}
                {d?.model != null && d.model !== "" && <><dt>Model</dt><dd>{d.model}</dd></>}
                {d?.color != null && d.color !== "" && <><dt>Color</dt><dd>{d.color}</dd></>}
                {d?.cubic_capacity != null && d.cubic_capacity !== "" && <><dt>Cubic capacity</dt><dd>{d.cubic_capacity}</dd></>}
                {d?.total_amount != null && d.total_amount !== "" && <><dt>Total amount</dt><dd>{d.total_amount}</dd></>}
                {d?.year_of_mfg != null && d.year_of_mfg !== "" && <><dt>Year of Mfg</dt><dd>{d.year_of_mfg}</dd></>}
              </dl>
            </div>
          )}
        </section>
      ) : (
        <div className="app-placeholder">
          <p>Step: {addSalesStep.replace("-", " ")}</p>
        </div>
      )}
    </>
  );
}
