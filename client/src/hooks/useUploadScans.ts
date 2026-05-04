import { useState } from "react";
import { uploadScans, uploadScansV2, uploadScansV2ConsolidatedStream } from "../api/uploads";
import {
  mirrorConsolidatedUploadFilesToLocalDisk,
  mirrorUploadScansV2FilesToLocalDisk,
} from "../utils/electronUploadScanMirror";
import { validateAadharScanFile } from "../utils/aadharScanFileValidation";
import type { ConsolidatedFsArchiveContext } from "../utils/scannerArchive";
import type { ExtractedDetailsResponse, ManualFallbackPayload } from "../types";

/** Second argument when upload response includes OCR — run after applying details so DMS warm-up can follow. */
export interface ExtractionCompleteContext {
  savedTo: string;
}

export interface UseUploadScansControlled {
  savedTo: string | null;
  setSavedTo: (v: string | null) => void;
  uploadedFiles: string[];
  setUploadedFiles: React.Dispatch<React.SetStateAction<string[]>>;
  uploadStatus: string;
  setUploadStatus: (v: string) => void;
  /** Called when upload completes with extraction.details so form can populate immediately (before onUploadSuccess). */
  onExtractionComplete?: (details: ExtractedDetailsResponse, ctx: ExtractionCompleteContext) => void;
  /** After a successful upload (e.g. clear stale Fill DMS banner; receives saved_to for V2). Runs after onExtractionComplete when both apply. */
  onUploadSuccess?: (savedTo?: string) => void;
  /** Pre-OCR could not classify pages; server created a manual split session (JPEGs under 200KB). */
  onManualFallback?: (
    payload: ManualFallbackPayload,
    warning?: string,
    scannerArchive?: ConsolidatedFsArchiveContext | null
  ) => void;
  /** Consolidated PDF came from scanner `landing`; parent defers FS move until Print Forms and Queue RTO succeeds. */
  onConsolidatedScannerArchiveDeferred?: (archive: ConsolidatedFsArchiveContext) => void;
}

export function useUploadScans(
  aadharLast4: string,
  mobile: string = "",
  controlled?: UseUploadScansControlled,
  dealerId?: number
) {
  const [internalUploadStatus, setInternalUploadStatus] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [internalUploadedFiles, setInternalUploadedFiles] = useState<string[]>([]);
  const [internalSavedTo, setInternalSavedTo] = useState<string | null>(null);

  const setUploadStatus = controlled?.setUploadStatus ?? setInternalUploadStatus;
  const setUploadedFiles = controlled?.setUploadedFiles ?? setInternalUploadedFiles;
  const setSavedTo = controlled?.setSavedTo ?? setInternalSavedTo;
  const uploadStatus = controlled?.uploadStatus ?? internalUploadStatus;
  const uploadedFiles = controlled?.uploadedFiles ?? internalUploadedFiles;
  const savedTo = controlled?.savedTo ?? internalSavedTo;

  const aadharDigits = aadharLast4.replace(/\D/g, "");
  const isAadharValid = aadharDigits.length === 4;
  const mobileDigits = mobile.replace(/\D/g, "");
  const isMobileValid = mobileDigits.length === 10;

  async function upload(filesToUpload: File[]) {
    if (filesToUpload.length === 0) {
      setUploadStatus("Please choose files first.");
      return;
    }
    if (!isAadharValid) {
      setUploadStatus("Enter last 4 digits of Customer Aadhar first.");
      return;
    }
    setIsUploading(true);
    setUploadStatus("Uploading...");
    try {
      const data = await uploadScans(aadharDigits, filesToUpload, dealerId);
      setUploadStatus(`Uploaded ${data.saved_count} file(s) successfully.`);
      controlled?.onUploadSuccess?.();
      if (data.saved_files?.length)
        setUploadedFiles((prev) => [...(data.saved_files ?? []), ...prev]);
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  async function uploadV2(aadharScan: File, aadharBackScan: File, salesDetail: File, insuranceSheet?: File) {
    if (!isMobileValid) {
      setUploadStatus("Enter 10-digit Customer Mobile first.");
      return;
    }
    const errFront = validateAadharScanFile(aadharScan);
    if (errFront) {
      setUploadStatus(errFront);
      return;
    }
    const errBack = validateAadharScanFile(aadharBackScan);
    if (errBack) {
      setUploadStatus(errBack);
      return;
    }
    setIsUploading(true);
    setUploadStatus("Uploading...");
    try {
      const data = await uploadScansV2(mobileDigits, aadharScan, aadharBackScan, salesDetail, insuranceSheet, dealerId);
      const savedToFolder = data.saved_to;
      if (!savedToFolder) {
        setUploadStatus("Upload failed: server did not return saved_to.");
        return;
      }
      setSavedTo(savedToFolder);
      let msg = `Uploaded ${data.saved_count} file(s) to ${savedToFolder}.`;
      if (data.extraction?.error) msg += ` Warning: ${data.extraction.error}`;
      const soft = data.extraction?.warnings;
      if (Array.isArray(soft) && soft.length > 0) msg += ` ${soft.join(" ")}`;
      setUploadStatus(msg);
      if (data.saved_files?.length)
        setUploadedFiles((prev) => [...(data.saved_files ?? []), ...prev]);
      const details = data.extraction?.details;
      if (details && controlled?.onExtractionComplete) {
        controlled.onExtractionComplete(details, { savedTo: savedToFolder });
      }
      void mirrorUploadScansV2FilesToLocalDisk({
        dealerId,
        subfolder: savedToFolder,
        aadharScan,
        aadharBackScan,
        salesDetail,
        insuranceSheet,
      });
      controlled?.onUploadSuccess?.(savedToFolder);
      // Extraction already ran on the server inside upload (save_and_queue_v2). No AI reader queue / process-all.
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  async function uploadConsolidatedV2(
    consolidatedFiles: File[],
    fsArchive?: ConsolidatedFsArchiveContext | null
  ) {
    setIsUploading(true);
    setUploadStatus("Uploading…");
    try {
      const data = await uploadScansV2ConsolidatedStream(consolidatedFiles, dealerId, mobileDigits, (p) => {
        if (p.savedTo) {
          setSavedTo(p.savedTo);
        }
        const hint =
          p.fragment === "aadhar"
            ? "Aadhaar read — updating form…"
            : p.fragment === "details"
              ? "Details sheet read — updating form…"
              : "Extracting…";
        setUploadStatus(hint);
        if (p.details && controlled?.onExtractionComplete) {
          controlled.onExtractionComplete(p.details, { savedTo: p.savedTo });
        }
      });
      if (data.manual_fallback) {
        const partialDetails = data.extraction?.details;
        if (partialDetails && controlled?.onExtractionComplete) {
          controlled.onExtractionComplete(partialDetails, { savedTo: data.saved_to ?? "" });
        }
        if (controlled?.onManualFallback) {
          controlled.onManualFallback(data.manual_fallback, data.warning, fsArchive ?? null);
        }
        setUploadStatus(
          partialDetails
            ? "Details sheet applied. Confirm Aadhaar front/back for each page below, then Apply document layout."
            : data.warning ??
                "Could not auto-read all pages. Assign each page below, enter Customer Mobile, then Apply."
        );
        return;
      }
      if ((data as any).error) {
        setUploadStatus((data as any).error);
        return;
      }
      if (data.saved_to) {
        setSavedTo(data.saved_to);
      }
      if (data.saved_to && consolidatedFiles.length > 0) {
        void mirrorConsolidatedUploadFilesToLocalDisk({
          dealerId,
          subfolder: data.saved_to,
          files: consolidatedFiles,
        });
      }
      let msg = data.saved_to
        ? `Uploaded consolidated scan to ${data.saved_to}.`
        : "Consolidated scan processed, but no destination folder was returned.";
      if (data.extraction?.error) msg += ` Warning: ${data.extraction.error}`;
      const soft = data.extraction?.warnings;
      if (Array.isArray(soft) && soft.length > 0) msg += ` ${soft.join(" ")}`;
      if (fsArchive) {
        controlled?.onConsolidatedScannerArchiveDeferred?.(fsArchive);
      }
      setUploadStatus(msg);
      if (data.saved_files?.length)
        setUploadedFiles((prev) => [...(data.saved_files ?? []), ...prev]);
      const details = data.extraction?.details;
      if (details && controlled?.onExtractionComplete) {
        controlled.onExtractionComplete(details, { savedTo: data.saved_to ?? "" });
      }
      if (data.saved_to) {
        controlled?.onUploadSuccess?.(data.saved_to);
      }
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  function clearUploaded() {
    setUploadedFiles([]);
    setUploadStatus("");
    setSavedTo(null);
  }

  return {
    upload,
    uploadV2,
    uploadConsolidatedV2,
    uploadStatus,
    isUploading,
    uploadedFiles,
    savedTo,
    isAadharValid,
    isMobileValid,
    clearUploaded,
  };
}
