import { DEALER_ID } from "../api/dealerId";
import { isElectron } from "../electron";

/** Canonical names under each sale subfolder — match ``upload_service.save_and_queue_v2``. */
export const UPLOAD_SCAN_MIRROR_V2_NAMES = {
  aadharFront: "Aadhar_front.jpg",
  aadharBack: "Aadhar_back.jpg",
  details: "Details.jpg",
  insurance: "Insurance.jpg",
} as const;

type FileWithPath = File & { path?: string };

function sourcePathFromFile(file: File): string | null {
  const p = (file as FileWithPath).path;
  return typeof p === "string" && p.trim() ? p.trim() : null;
}

/**
 * After a successful scans-v2 API upload, mirror the chosen files into local **Uploaded scans**
 * (Electron only; uses native ``File.path``).
 */
export async function mirrorUploadScansV2FilesToLocalDisk(params: {
  dealerId?: number;
  subfolder: string;
  aadharScan: File;
  aadharBackScan: File;
  salesDetail: File;
  insuranceSheet?: File;
}): Promise<void> {
  if (!isElectron() || !window.electronAPI?.file?.copyUploadScanArtifacts) {
    return;
  }
  const dealerId = params.dealerId ?? DEALER_ID;
  const items: { sourcePath: string; destFileName: string }[] = [];
  const front = sourcePathFromFile(params.aadharScan);
  const back = sourcePathFromFile(params.aadharBackScan);
  const det = sourcePathFromFile(params.salesDetail);
  if (front) {
    items.push({ sourcePath: front, destFileName: UPLOAD_SCAN_MIRROR_V2_NAMES.aadharFront });
  }
  if (back) {
    items.push({ sourcePath: back, destFileName: UPLOAD_SCAN_MIRROR_V2_NAMES.aadharBack });
  }
  if (det) {
    items.push({ sourcePath: det, destFileName: UPLOAD_SCAN_MIRROR_V2_NAMES.details });
  }
  if (params.insuranceSheet) {
    const ins = sourcePathFromFile(params.insuranceSheet);
    if (ins) {
      items.push({ sourcePath: ins, destFileName: UPLOAD_SCAN_MIRROR_V2_NAMES.insurance });
    }
  }
  if (items.length === 0) {
    return;
  }
  try {
    await window.electronAPI.file.copyUploadScanArtifacts({
      dealerId,
      subfolder: params.subfolder,
      items,
    });
  } catch {
    /* non-fatal: cloud upload already succeeded */
  }
}

/**
 * After a successful consolidated scan stream, mirror each chosen file under **Uploaded scans**
 * using each file's **original name** (Electron ``File.path`` + ``File.name``).
 */
export async function mirrorConsolidatedUploadFilesToLocalDisk(params: {
  dealerId?: number;
  subfolder: string;
  files: File[];
}): Promise<void> {
  if (!isElectron() || !window.electronAPI?.file?.copyUploadScanArtifacts) {
    return;
  }
  const dealerId = params.dealerId ?? DEALER_ID;
  const items: { sourcePath: string; destFileName: string }[] = [];
  for (const f of params.files) {
    const src = sourcePathFromFile(f);
    const destFileName = (f.name && f.name.trim()) || "";
    if (!src || !destFileName) continue;
    items.push({ sourcePath: src, destFileName });
  }
  if (items.length === 0) {
    return;
  }
  try {
    await window.electronAPI.file.copyUploadScanArtifacts({
      dealerId,
      subfolder: params.subfolder,
      items,
    });
  } catch {
    /* non-fatal */
  }
}
