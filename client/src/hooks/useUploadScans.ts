import { useState } from "react";
import { uploadScans, uploadScansV2 } from "../api/uploads";
import { startProcessAll } from "../api/aiReaderQueue";
import type { ExtractedDetailsResponse } from "../types";

export interface UseUploadScansControlled {
  savedTo: string | null;
  setSavedTo: (v: string | null) => void;
  uploadedFiles: string[];
  setUploadedFiles: React.Dispatch<React.SetStateAction<string[]>>;
  uploadStatus: string;
  setUploadStatus: (v: string) => void;
  /** Called when upload completes with extraction.details so form can populate immediately */
  onExtractionComplete?: (details: ExtractedDetailsResponse) => void;
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
    setIsUploading(true);
    setUploadStatus("Uploading...");
    try {
      const data = await uploadScansV2(mobileDigits, aadharScan, aadharBackScan, salesDetail, insuranceSheet, dealerId);
      setSavedTo(data.saved_to);
      setUploadStatus(`Uploaded ${data.saved_count} file(s) to ${data.saved_to}.`);
      if (data.saved_files?.length)
        setUploadedFiles((prev) => [...(data.saved_files ?? []), ...prev]);
      const details = (data as { extraction?: { details?: ExtractedDetailsResponse } }).extraction?.details;
      if (details && controlled?.onExtractionComplete) {
        controlled.onExtractionComplete(details);
      }
      // Trigger Details sheet reader (Textract forms) on new queue items
      const processRes = await startProcessAll();
      void processRes;
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
    uploadStatus,
    isUploading,
    uploadedFiles,
    savedTo,
    isAadharValid,
    isMobileValid,
    clearUploaded,
  };
}
