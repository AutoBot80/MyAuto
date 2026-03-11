import { useState } from "react";
import { uploadScans } from "../api/uploads";

export function useUploadScans(aadharLast4: string) {
  const [uploadStatus, setUploadStatus] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);

  const aadharDigits = aadharLast4.replace(/\D/g, "");
  const isAadharValid = aadharDigits.length === 4;

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
      const data = await uploadScans(aadharDigits, filesToUpload);
      setUploadStatus(`Uploaded ${data.saved_count} file(s) successfully.`);
      if (data.saved_files?.length)
        setUploadedFiles((prev) => [...(data.saved_files ?? []), ...prev]);
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  function clearUploaded() {
    setUploadedFiles([]);
    setUploadStatus("");
  }

  return {
    upload,
    uploadStatus,
    isUploading,
    uploadedFiles,
    isAadharValid,
    clearUploaded,
  };
}
