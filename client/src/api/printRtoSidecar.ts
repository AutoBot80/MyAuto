import { getAccessToken } from "../auth/token";
import { getBaseUrl } from "./client";
import { isElectron } from "../electron";
import type { PrintForm20Request, PrintForm20Response, UploadSaleFolderRequest, UploadSaleFolderResponse } from "./fillForms";

const SYNC_TIMEOUT_MS = 600_000;
const GATE_PASS_LOCAL_TIMEOUT_MS = 300_000;

async function runSidecarJob(
  type: string,
  params: Record<string, unknown>,
  timeoutMs: number
): Promise<{ success: boolean; data?: Record<string, unknown>; error?: string }> {
  if (!isElectron()) {
    return { success: true, data: {} };
  }
  const result = await window.electronAPI!.sidecar.runJob({
    type,
    api_url: getBaseUrl(),
    jwt: getAccessToken() ?? "",
    params,
    timeoutMs,
  });
  const data = (result.parsed as { data?: Record<string, unknown> })?.data;
  if (result.timedOut) {
    return { success: false, error: `${type} timed out.` };
  }
  if (!result.success || data?.success === false) {
    return {
      success: false,
      error:
        (typeof data?.error === "string" ? data.error : null) ||
        (typeof result.error === "string" ? result.error : null) ||
        result.stderr?.slice(0, 500) ||
        `${type} failed.`,
    };
  }
  return { success: true, data };
}

export async function pullSaleScanAssetsFromServer(
  req: UploadSaleFolderRequest
): Promise<UploadSaleFolderResponse> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder || req.dealer_id <= 0) {
    return { success: false, error: "dealer_id and subfolder are required." };
  }
  const r = await runSidecarJob(
    "pull_sale_scan_assets",
    { dealer_id: req.dealer_id, subfolder },
    SYNC_TIMEOUT_MS
  );
  if (!r.success) {
    return { success: false, error: r.error };
  }
  const d = r.data ?? {};
  return {
    success: true,
    files_downloaded: Number(d.files_downloaded ?? 0),
    files_failed: Number(d.files_failed ?? 0),
    subfolder: String(d.subfolder ?? subfolder),
    log_file: typeof d.log_file === "string" ? d.log_file : undefined,
  };
}

export async function pushSaleFolderToServer(
  req: UploadSaleFolderRequest
): Promise<UploadSaleFolderResponse> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder || req.dealer_id <= 0) {
    return { success: false, error: "dealer_id and subfolder are required." };
  }
  const r = await runSidecarJob(
    "push_sale_artifacts",
    { dealer_id: req.dealer_id, subfolder },
    SYNC_TIMEOUT_MS
  );
  if (!r.success) {
    return { success: false, error: r.error };
  }
  const d = r.data ?? {};
  return {
    success: true,
    files_uploaded: Number(d.files_uploaded ?? 0),
    files_failed: Number(d.files_failed ?? 0),
    subfolder: String(d.subfolder ?? subfolder),
  };
}

export async function printGatePassLocal(req: PrintForm20Request): Promise<PrintForm20Response> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder) {
    return { success: false, pdfs_saved: [], error: "subfolder is required." };
  }
  const r = await runSidecarJob(
    "print_gate_pass_local",
    {
      dealer_id: req.dealer_id,
      subfolder,
      customer: req.customer,
      vehicle: req.vehicle ?? {},
      vehicle_id: req.vehicle_id ?? null,
    },
    GATE_PASS_LOCAL_TIMEOUT_MS
  );
  if (!r.success) {
    return { success: false, pdfs_saved: [], error: r.error ?? "print_gate_pass_local failed." };
  }
  const d = r.data ?? {};
  return {
    success: true,
    pdfs_saved: Array.isArray(d.pdfs_saved) ? (d.pdfs_saved as string[]) : ["Gate Pass.pdf"],
    print_jobs: Array.isArray(d.print_jobs)
      ? (d.print_jobs as PrintForm20Response["print_jobs"])
      : undefined,
    error: null,
  };
}
