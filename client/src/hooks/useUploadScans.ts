import { useState } from "react";
import { uploadScans, uploadScansV2 } from "../api/uploads";
import type { ExtractedDetailsResponse, UploadExtractionSectionTimings } from "../types";

function formatUploadExtractionTimings(extraction: { section_timings_ms?: UploadExtractionSectionTimings } | undefined): string {
  const t = extraction?.section_timings_ms;
  if (!t) return "";
  const parts: string[] = [];
  if (t.total_ms != null) parts.push(`total ${t.total_ms} ms`);
  if (t.aadhar_textract_front_ms != null) parts.push(`Aadhaar Textract front ${t.aadhar_textract_front_ms} ms`);
  if (t.aadhar_textract_back_ms != null) parts.push(`Aadhaar Textract back ${t.aadhar_textract_back_ms} ms`);
  if (t.detail_sheet_textract_ms != null) parts.push(`Detail sheet Textract ${t.detail_sheet_textract_ms} ms`);
  if (t.aws_textract_prefetch_ms != null) {
    parts.push(`phase1 Textract prefetch ${t.aws_textract_prefetch_ms} ms`);
  }
  if (t.parallel_aadhar_details_compile_ms != null) {
    parts.push(`Aadhaar + Details compile ${t.parallel_aadhar_details_compile_ms} ms`);
  }
  if (t.merge_write_json_ms != null) parts.push(`merge ${t.merge_write_json_ms} ms`);
  if (t.insurance_ms != null) parts.push(`insurance ${t.insurance_ms} ms`);
  if (t.extras_raw_ms != null) parts.push(`extra pages ${t.extras_raw_ms} ms`);
  if (t.raw_ocr_finalize_ms != null) parts.push(`Raw_OCR ${t.raw_ocr_finalize_ms} ms`);
  return parts.length ? ` ${parts.join(" · ")}.` : "";
}

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
      const timingSuffix = formatUploadExtractionTimings(data.extraction);
      setUploadStatus(`Uploaded ${data.saved_count} file(s) to ${data.saved_to}.${timingSuffix}`);
      if (data.saved_files?.length)
        setUploadedFiles((prev) => [...(data.saved_files ?? []), ...prev]);
      const details = data.extraction?.details;
      if (details && controlled?.onExtractionComplete) {
        controlled.onExtractionComplete(details);
      }
      // Extraction already ran on the server inside upload (save_and_queue_v2). No AI reader queue / process-all.
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
