import fs from "fs";
import path from "path";
import { logError, logInfo } from "./logger";
import { getSaathiBaseDir } from "./paths";

function safeChallanArtifactLeaf(leaf: string): string | null {
  const t = leaf.trim().replace(/\\/g, "/").split("/").pop()?.trim() || "";
  if (!t || t.includes("..")) return null;
  return t;
}

function isWindowsReservedStem(stemUpper: string): boolean {
  if (["CON", "PRN", "AUX", "NUL"].includes(stemUpper)) return true;
  if (/^COM[0-9]$/.test(stemUpper) || /^LPT[0-9]$/.test(stemUpper)) return true;
  return false;
}

function isSafeChallanScanFileName(destFileName: string): boolean {
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

export interface CopyChallanScanArtifactItem {
  sourcePath: string;
  destFileName: string;
}

/**
 * Copy dealer-selected challan scan files into local **Challans/<leaf>/** alongside OCR artifacts.
 */
export async function copyChallanScanArtifacts(args: {
  artifactLeaf: string;
  items: CopyChallanScanArtifactItem[];
}): Promise<{ ok: true; copied: string[]; destDir: string } | { ok: false; message: string }> {
  const safeLeaf = safeChallanArtifactLeaf(args.artifactLeaf);
  if (!safeLeaf) {
    return { ok: false, message: "invalid_artifact_leaf" };
  }
  const items = Array.isArray(args.items) ? args.items : [];
  if (items.length === 0) {
    return { ok: false, message: "no_items" };
  }

  const destDir = path.join(getSaathiBaseDir(), "Challans", safeLeaf);
  let destResolved: string;
  try {
    fs.mkdirSync(destDir, { recursive: true });
    destResolved = fs.realpathSync.native(destDir);
  } catch (e) {
    logError("copyChallanScanArtifacts: mkdir", e);
    return { ok: false, message: "dest_mkdir_failed" };
  }

  const copied: string[] = [];

  for (const raw of items) {
    const destFileName = path.basename(String(raw.destFileName || "").trim());
    if (!isSafeChallanScanFileName(destFileName)) {
      logError(`copyChallanScanArtifacts: reject dest name ${destFileName}`);
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
      logError("copyChallanScanArtifacts: dest escapes folder");
      continue;
    }
    try {
      fs.copyFileSync(srcResolved, destPathResolved);
      copied.push(destFileName);
    } catch (e) {
      logError(`copyChallanScanArtifacts: copy ${destFileName}`, e);
    }
  }

  if (copied.length === 0) {
    return { ok: false, message: "nothing_copied" };
  }
  logInfo(`copyChallanScanArtifacts: copied ${copied.join(", ")} -> ${destResolved}`);
  return { ok: true, copied, destDir: destResolved };
}
