import { app } from "electron";
import fs from "fs";
import path from "path";

/** Default production install root (matches NSIS target). */
export const DEFAULT_SAATHI_BASE = "D:\\Saathi";

export function getSaathiBaseDir(): string {
  const fromEnv = process.env.SAATHI_BASE_DIR?.trim();
  if (fromEnv) return path.resolve(fromEnv);
  return DEFAULT_SAATHI_BASE;
}

export function getLogsDir(): string {
  return path.join(getSaathiBaseDir(), "logs");
}

export function getRepoRootFromMain(): string {
  // dist/main -> electron -> repo
  return path.resolve(__dirname, "..", "..", "..");
}

/**
 * Window/taskbar icon: `.ico` on Windows (recommended); `.png` elsewhere.
 * Packaged builds copy both into `process.resourcesPath` via `electron-builder.yml` `extraResources`.
 */
export function getAppIconPath(): string {
  const name = process.platform === "win32" ? "icon.ico" : "icon.png";
  if (app.isPackaged) {
    return path.join(process.resourcesPath, name);
  }
  return path.join(__dirname, "..", "..", "resources", name);
}

export function getSidecarScriptPath(): string {
  return path.join(getRepoRootFromMain(), "electron", "sidecar", "job_runner.py");
}

export function getSidecarExePath(): string {
  const fromEnv = process.env.SAATHI_SIDECAR_EXE?.trim();
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  if (app.isPackaged) {
    const p = path.join(process.resourcesPath, "sidecar", "job_runner.exe");
    if (fs.existsSync(p)) return p;
    throw new Error(`Sidecar not found at ${p}`);
  }
  return "";
}
