import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { app } from "electron";
import { logError, logInfo } from "./logger";
import { getRepoRootFromMain, getSaathiBaseDir } from "./paths";

function uploadsLeaf(): string {
  return process.platform === "win32" ? "Uploaded scans" : "uploaded-scans";
}

/** Match backend ``safe_sub`` sanitization (see ``fill_forms_router``). */
export function safeSubfolderSegment(raw: string): string {
  const t = raw.trim().replace(/[^\w\-]/g, "_");
  return t || "default";
}

/** Local absolute path: ``Uploaded scans/{dealerId}/{subfolder}/`` under Saathi base. */
export function localSaleFolderAbs(dealerId: number, subfolder: string): string {
  return path.join(getSaathiBaseDir(), uploadsLeaf(), String(dealerId), safeSubfolderSegment(subfolder));
}

function resolveBackendRoot(): string | null {
  const dev = path.join(getRepoRootFromMain(), "backend");
  if (fs.existsSync(path.join(dev, "app", "services", "dealer_sign_overlay.py"))) {
    return dev;
  }
  const cached = path.join(getSaathiBaseDir(), "script_cache", "backend");
  if (fs.existsSync(path.join(cached, "app", "services", "dealer_sign_overlay.py"))) {
    return cached;
  }
  return null;
}

/** ``{dealer_id}_sign.jpg`` / ``.jpeg`` next to ``.env`` (Saathi base) or beside exe when packaged. */
export function resolveDealerSignatureImagePath(dealerId: number): string | null {
  const base = getSaathiBaseDir();
  const exts = [".jpg", ".jpeg", ".JPG", ".JPEG"];
  for (const ext of exts) {
    const p = path.join(base, `${dealerId}_sign${ext}`);
    if (fs.existsSync(p)) return p;
  }
  if (app.isPackaged) {
    const exeDir = path.dirname(app.getPath("exe"));
    for (const ext of exts) {
      const p = path.join(exeDir, `${dealerId}_sign${ext}`);
      if (fs.existsSync(p)) return p;
    }
  }
  // Typo installs: data root ``D:\Saath`` vs ``D:\Saathi`` — try common roots if ``getSaathiBaseDir()`` missed.
  if (process.platform === "win32") {
    for (const root of ["D:\\Saath", "D:\\Saathi"]) {
      if (!fs.existsSync(root)) continue;
      for (const ext of exts) {
        const p = path.join(root, `${dealerId}_sign${ext}`);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  return null;
}

/**
 * Headless PyMuPDF overlay on Form 20 / GST / Sale Certificate PDFs. Always non-fatal for callers.
 */
export function runDealerSignOverlayHeadless(dealerId: number, subfolder: string): { ok: boolean; message?: string } {
  const saleDir = localSaleFolderAbs(dealerId, subfolder);
  if (!fs.existsSync(saleDir)) {
    logInfo(`dealer_sign_overlay: sale folder missing (skip): ${saleDir}`);
    return { ok: true, message: "sale_folder_missing" };
  }

  const sig = resolveDealerSignatureImagePath(dealerId);
  if (!sig) {
    logInfo(
      `dealer_sign_overlay: no signature file for dealer ${dealerId} (signature overlay skipped; ` +
        `Details pencil → Form 20 step still runs if Details/Form 20 exist).`
    );
  }

  const backendRoot = resolveBackendRoot();
  if (!backendRoot) {
    logError("dealer_sign_overlay: backend scripts not found (dev backend/ or script_cache/backend)");
    return { ok: true, message: "no_backend" };
  }

  const py = process.env.SAATHI_PYTHON?.trim() || "python";
  const args = [
    "-m",
    "app.services.dealer_sign_overlay",
    "--sale-dir",
    saleDir,
    "--dealer-id",
    String(dealerId),
    "--signature",
    sig ?? "",
    "--after-sign-pencil-form20",
    "--json",
  ];

  const r = spawnSync(py, args, {
    cwd: getSaathiBaseDir(),
    env: { ...process.env, PYTHONPATH: backendRoot, SAATHI_BASE_DIR: getSaathiBaseDir() },
    windowsHide: true,
    encoding: "utf-8",
    maxBuffer: 10 * 1024 * 1024,
  });

  if (r.error) {
    logError("dealer_sign_overlay spawn", r.error);
    return { ok: true, message: String(r.error) };
  }
  if (r.status !== 0) {
    logInfo(`dealer_sign_overlay: exit ${r.status} stderr=${r.stderr ?? ""}`);
    return { ok: true, message: `exit_${r.status}` };
  }
  logInfo(`dealer_sign_overlay: ${(r.stdout ?? "").trim()}`);
  return { ok: true };
}
