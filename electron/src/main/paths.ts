import { app } from "electron";
import fs from "fs";
import path from "path";

/** Dev-mode fallback when not running from a packaged build. */
const DEV_SAATHI_BASE = "D:\\Saathi";

export function getSaathiBaseDir(): string {
  const fromEnv = process.env.SAATHI_BASE_DIR?.trim();
  if (fromEnv) return path.resolve(fromEnv);
  if (app.isPackaged) {
    // Packaged NSIS install: exe sits at e.g. D:\Saathi\Dealer Saathi.exe
    return path.dirname(app.getPath("exe"));
  }
  return DEV_SAATHI_BASE;
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

/**
 * Parse a dotenv-style file into a key-value map.
 * Only handles `KEY=value` and `KEY="value"` lines (no variable expansion).
 */
function parseDotenv(filePath: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!fs.existsSync(filePath)) return out;
  const lines = fs.readFileSync(filePath, "utf-8").split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    out[key] = val;
  }
  return out;
}

const HERO_DMS_BASE_URL_DEFAULT = "https://edealerhmcl.heroconnect.co.in";
const VAHAN_BASE_URL_DEFAULT = "https://vahan.parivahan.gov.in/vahan/vahan/ui/login/login.xhtml";

export interface SiteUrlsResult {
  dms_base_url: string;
  dms_mode: string;
  dms_real_siebel: boolean;
  dms_real_contact_url_configured: boolean;
  vahan_base_url: string;
  insurance_base_url: string;
}

export function getSiteUrlsFromEnv(): SiteUrlsResult {
  const envPath = path.join(getSaathiBaseDir(), ".env");
  const env = parseDotenv(envPath);
  const dmsMode = (env["DMS_MODE"] || "real").toLowerCase();
  const realModes = ["real", "siebel", "live", "production", "hero"];
  return {
    dms_base_url: (env["DMS_BASE_URL"] || HERO_DMS_BASE_URL_DEFAULT).replace(/\/+$/, ""),
    dms_mode: dmsMode,
    dms_real_siebel: realModes.includes(dmsMode),
    dms_real_contact_url_configured: !!(env["DMS_REAL_URL_CONTACT"] || "").trim(),
    vahan_base_url: (env["VAHAN_BASE_URL"] || VAHAN_BASE_URL_DEFAULT).replace(/\/+$/, ""),
    insurance_base_url: (env["INSURANCE_BASE_URL"] || "").replace(/\/+$/, ""),
  };
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
