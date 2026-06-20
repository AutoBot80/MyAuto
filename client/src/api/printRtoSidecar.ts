import { getAccessToken } from "../auth/token";
import { getBaseUrl } from "./client";
import { isElectron } from "../electron";
import {
  printGatePass,
  type PrintForm20Request,
  type PrintForm20Response,
  type UploadSaleFolderRequest,
  type UploadSaleFolderResponse,
} from "./fillForms";

const SYNC_TIMEOUT_MS = 600_000;
const GATE_PASS_LOCAL_TIMEOUT_MS = 300_000;
const DEALER_SIGN_OVERLAY_TIMEOUT_MS = 120_000;

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

export async function pullAadharScanJpegsFromServer(
  req: UploadSaleFolderRequest
): Promise<UploadSaleFolderResponse> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder || req.dealer_id <= 0) {
    return { success: false, error: "dealer_id and subfolder are required." };
  }
  const r = await runSidecarJob(
    "pull_aadhar_scan_jpegs",
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
  };
}

/**
 * Electron only: dealer signature overlay on Form 20 / GST / Sale Certificate PDFs
 * in the local sale folder — must run after pull and before gate pass. Non-fatal on failure.
 */
export async function overlayDealerSignaturesLocal(req: {
  dealer_id: number;
  subfolder: string;
}): Promise<void> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder || req.dealer_id <= 0) return;
  try {
    await runSidecarJob(
      "dealer_sign_overlay",
      { dealer_id: req.dealer_id, subfolder },
      DEALER_SIGN_OVERLAY_TIMEOUT_MS
    );
  } catch {
    /* best-effort */
  }
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

export interface PrintViewCustomerSaleFilesRequest {
  dealer_id: number;
  subfolder: string;
  mobile?: string | null;
  customer?: PrintForm20Request["customer"];
}

export interface PrintViewCustomerSaleFilesResponse {
  success: boolean;
  print_jobs?: PrintForm20Response["print_jobs"];
  error?: string | null;
}

export async function printViewCustomerSaleFilesLocal(
  req: PrintViewCustomerSaleFilesRequest
): Promise<PrintViewCustomerSaleFilesResponse> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder) {
    return { success: false, error: "subfolder is required." };
  }
  const r = await runSidecarJob(
    "print_view_customer_sale_files_local",
    {
      dealer_id: req.dealer_id,
      subfolder,
      mobile: req.mobile?.trim() || null,
      customer: req.customer ?? {},
    },
    GATE_PASS_LOCAL_TIMEOUT_MS
  );
  if (!r.success) {
    return { success: false, error: r.error ?? "print_view_customer_sale_files_local failed." };
  }
  const d = r.data ?? {};
  return {
    success: true,
    print_jobs: Array.isArray(d.print_jobs)
      ? (d.print_jobs as PrintViewCustomerSaleFilesResponse["print_jobs"])
      : undefined,
    error: null,
  };
}

export async function printGatePassLocal(req: PrintForm20Request): Promise<PrintForm20Response> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder) {
    return { success: false, pdfs_saved: [], error: "subfolder is required." };
  }
  if (!isElectron()) {
    return printGatePass(req);
  }
  const r = await runSidecarJob(
    "print_gate_pass_local",
    {
      dealer_id: req.dealer_id,
      subfolder,
      customer: req.customer,
      vehicle: req.vehicle ?? {},
      vehicle_id: req.vehicle_id ?? null,
      staging_id: req.staging_id ?? null,
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

export interface UploadRtoQueueFormsRequest {
  dealer_id: number;
  subfolder: string;
  rto_queue_id: number;
  mobile?: string | null;
  uploads: { category_key: string; source_path: string }[];
}

export interface UploadRtoQueueFormsResponse {
  success: boolean;
  ready?: boolean;
  missing?: string[];
  error?: string | null;
  status?: string;
}

export async function uploadRtoQueueFormsLocal(
  req: UploadRtoQueueFormsRequest
): Promise<UploadRtoQueueFormsResponse> {
  if (!isElectron()) {
    return { success: false, error: "Upload Forms requires the Electron app." };
  }
  const r = await runSidecarJob("upload_rto_queue_forms", { ...req }, SYNC_TIMEOUT_MS);
  const d = r.data ?? {};
  if (!r.success) {
    return {
      success: false,
      ready: Boolean(d.ready),
      missing: Array.isArray(d.missing) ? (d.missing as string[]) : undefined,
      error: r.error ?? (typeof d.error === "string" ? d.error : "Upload failed."),
    };
  }
  return {
    success: Boolean(d.success ?? true),
    ready: Boolean(d.ready),
    missing: Array.isArray(d.missing) ? (d.missing as string[]) : undefined,
    error: typeof d.error === "string" ? d.error : null,
    status: typeof d.status === "string" ? d.status : undefined,
  };
}
