import fs from "fs";
import path from "path";
import { logError, logInfo } from "./logger";
import { getSaathiBaseDir } from "./paths";

function uploadsLeaf(): string {
  return process.platform === "win32" ? "Uploaded scans" : "uploaded-scans";
}

/** Same leaf shape as server ``get_uploaded_scans_sale_subfolder_leaf``: ``{10-digit}_{ddmmyy}``. */
function safeSaleSubfolder(leaf: string): string | null {
  const t = leaf.trim();
  return /^\d{10}_\d{6}$/.test(t) ? t : null;
}

/** Fixed names for scans-v2 mirror — must match ``upload_service.save_and_queue_v2``. */
const ALLOWED_FIXED_DEST = new Set([
  "Aadhar_front.jpg",
  "Aadhar_back.jpg",
  "Details.jpg",
  "Insurance.jpg",
  "Financing.jpg",
]);

function isWindowsReservedStem(stemUpper: string): boolean {
  if (["CON", "PRN", "AUX", "NUL"].includes(stemUpper)) return true;
  if (/^COM[0-9]$/.test(stemUpper) || /^LPT[0-9]$/.test(stemUpper)) return true;
  return false;
}

/** Consolidated / multi-file mirror: keep original basename when safe for the OS. */
function isSafeUserOriginalUploadName(destFileName: string): boolean {
  const name = path.basename(String(destFileName || "").trim());
  if (!name || name !== String(destFileName || "").trim()) return false;
  if (name.length > 240) return false;
  if (/[<>:"|?*\x00-\x1f]/.test(name)) return false;
  if (/[/\\]/.test(name) || name.includes("..")) return false;
  if (name.endsWith(" ") || name.endsWith(".")) return false;
  const ext = path.extname(name).toLowerCase();
  if (![".pdf", ".jpg", ".jpeg", ".png"].includes(ext)) return false;
  const stem = path.basename(name, ext);
  if (!stem) return false;
  if (isWindowsReservedStem(stem.toUpperCase())) return false;
  return true;
}

function isAcceptedMirrorDestFileName(destFileName: string): boolean {
  const base = path.basename(String(destFileName || "").trim());
  if (ALLOWED_FIXED_DEST.has(base)) return true;
  return isSafeUserOriginalUploadName(base);
}

export interface CopyUploadScanArtifactItem {
  sourcePath: string;
  destFileName: string;
}

/**
 * Copy dealer-selected scan files into local **Uploaded scans** so they exist on disk
 * alongside DMS downloads (cloud upload alone does not write here).
 */
export async function copyUploadScanArtifacts(args: {
  dealerId: number;
  subfolder: string;
  items: CopyUploadScanArtifactItem[];
}): Promise<{ ok: true; copied: string[]; destDir: string } | { ok: false; message: string }> {
  const dealerId = Math.floor(Number(args.dealerId));
  if (!Number.isFinite(dealerId) || dealerId <= 0) {
    return { ok: false, message: "invalid_dealer_id" };
  }
  const safeSub = safeSaleSubfolder(args.subfolder);
  if (!safeSub) {
    return { ok: false, message: "invalid_subfolder" };
  }
  const items = Array.isArray(args.items) ? args.items : [];
  if (items.length === 0) {
    return { ok: false, message: "no_items" };
  }

  const destDir = path.join(getSaathiBaseDir(), uploadsLeaf(), String(dealerId), safeSub);
  let destResolved: string;
  try {
    fs.mkdirSync(destDir, { recursive: true });
    destResolved = fs.realpathSync.native(destDir);
  } catch (e) {
    logError("copyUploadScanArtifacts: mkdir", e);
    return { ok: false, message: "dest_mkdir_failed" };
  }

  const copied: string[] = [];

  for (const raw of items) {
    const destFileName = path.basename(String(raw.destFileName || "").trim());
    if (!isAcceptedMirrorDestFileName(destFileName)) {
      logError(`copyUploadScanArtifacts: reject dest name ${destFileName}`);
      continue;
    }
    const srcRaw = String(raw.sourcePath || "").trim();
    if (!srcRaw) continue;
    let srcResolved: string;
    try {
      srcResolved = fs.realpathSync.native(path.resolve(srcRaw));
    } catch {
      continue;
    }
    if (!fs.existsSync(srcResolved) || !fs.statSync(srcResolved).isFile()) {
      continue;
    }
    const destPath = path.join(destResolved, destFileName);
    let destPathResolved: string;
    try {
      destPathResolved = path.resolve(destPath);
    } catch {
      continue;
    }
    const rel = path.relative(destResolved, destPathResolved);
    if (rel.startsWith("..") || path.isAbsolute(rel)) {
      logError("copyUploadScanArtifacts: dest escapes folder");
      continue;
    }
    try {
      fs.copyFileSync(srcResolved, destPathResolved);
      copied.push(destFileName);
    } catch (e) {
      logError(`copyUploadScanArtifacts: copy ${destFileName}`, e);
    }
  }

  if (copied.length === 0) {
    return { ok: false, message: "nothing_copied" };
  }
  logInfo(`copyUploadScanArtifacts: copied ${copied.join(", ")} -> ${destResolved}`);
  return { ok: true, copied, destDir: destResolved };
}
