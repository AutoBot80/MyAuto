import { apiFetch, getBaseUrl } from "./client";
import { getAccessToken } from "../auth/token";
import { isElectron } from "../electron";
import { getSilentPrintEnabled } from "../settings/printPreferences";

export interface FillDmsCustomer {
  name?: string | null;
  care_of?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  pin_code?: string | null;
  mobile_number?: string | null;
  mobile?: string | null;
  aadhar_id?: string | null;
}

export interface FillDmsVehicle {
  key_no?: string | null;
  frame_no?: string | null;
  engine_no?: string | null;
}

export interface FillDmsRequest {
  /** Optional when staging_id is set; server resolves from staging (file_location). */
  subfolder?: string | null;
  /** Optional; defaults to server DMS_BASE_URL. */
  dms_base_url?: string | null;
  /** Dealer ID for Form 20 field 10 (dealer name & address from dealer_ref). */
  dealer_id?: number | null;
  /**
   * Draft ``add_sales_staging`` row UUID. When set, DMS fill uses ``payload_json`` only (OCR merge);
   * omit ``customer_id`` / ``vehicle_id``. Optional request ``customer`` / ``vehicle`` fields merge on top.
   */
  staging_id?: string | null;
  /** From Submit Info; used for legacy master join and to persist DMS scrape when no ``staging_id``. */
  customer_id?: number | null;
  vehicle_id?: number | null;
  /** Optional when ``staging_id`` is set (fill values come from staging payload). */
  customer?: FillDmsCustomer;
  vehicle?: FillDmsVehicle;
  /**
   * Client API base (``VITE_API_URL`` at build). Sent automatically by ``fillDms`` / ``fillDmsOnly``;
   * logged at the top of ``Playwright_DMS_*.txt`` next to the server-reported request base URL.
   */
  client_api_base_url?: string | null;
}

export interface FillDmsResponse {
  success: boolean;
  /** After staging-path Create Invoice: committed master ids (use for insurance / RTO). */
  customer_id?: number | null;
  vehicle_id?: number | null;
  /** Real Siebel mode: navigation only; forms not auto-filled — show instead of "filled successfully". */
  warning?: string | null;
  dms_automation_mode?: string | null;
  vehicle: {
    key_num?: string;
    frame_num?: string;
    engine_num?: string;
    full_chassis?: string;
    full_engine?: string;
    model?: string;
    color?: string;
    cubic_capacity?: string;
    seating_capacity?: string;
    body_type?: string;
    vehicle_type?: string;
    num_cylinders?: string;
    vehicle_price?: string;
    year_of_mfg?: string;
    order_number?: string | null;
    invoice_number?: string | null;
  };
  pdfs_saved: string[];
  application_id?: string | null;
  rto_fees?: number | null;
  error?: string | null;
  /** When Siebel succeeded but DB commit failed (Electron sidecar). */
  commit_error?: string | null;
  /** Completed DMS steps from last Fill DMS run (Add Sales top banner). */
  dms_milestones?: string[];
  /** Real Siebel: ordered narrative lines; UI prefers this over milestones when non-empty. */
  dms_step_messages?: string[];
  /** My Orders grid already had Invoice# — operator can use Create Invoice in the app. */
  ready_for_client_create_invoice?: boolean | null;
  /** When the API uses S3 storage, presigned PDF URLs for Electron to print locally. */
  print_jobs?: ApiPrintJob[];
}

/**
 * Server ``print_jobs``: presigned HTTPS URLs when ``STORAGE_BACKEND=s3``.
 * Electron sidecar runs may use absolute local PDF paths in ``presigned_url`` instead.
 */
export interface ApiPrintJob {
  filename?: string;
  presigned_url: string;
  kind?: string;
}

export interface PrintQueueRtoFailureContext {
  dealerId: number;
  subfolder: string;
  customer?: FillDmsCustomer | null;
}

function normalizePrintJobResult(
  result: unknown,
  jobCount: number
): { ok: boolean; printed: number; queued?: number; error?: string } {
  if (result && typeof result === "object" && "ok" in result) {
    const r = result as { ok?: boolean; printed?: number; queued?: number; error?: string };
    return {
      ok: Boolean(r.ok),
      printed: Number(r.printed ?? 0),
      queued: typeof r.queued === "number" ? r.queued : undefined,
      error: typeof r.error === "string" ? r.error : undefined,
    };
  }
  return { ok: true, printed: 0, queued: jobCount };
}

/** Print PDFs in Electron (no-op in browser-only). Default: non-blocking background print. */
export async function dispatchPrintJobsFromApi(
  jobs: ApiPrintJob[] | undefined | null,
  options?: { awaitCompletion?: boolean; failureLog?: PrintQueueRtoFailureContext }
): Promise<{ ok: boolean; printed: number; queued?: number; error?: string }> {
  if (!jobs?.length) return { ok: true, printed: 0 };
  if (typeof window === "undefined") return { ok: true, printed: 0 };
  const fn = window.electronAPI?.print?.printPdfsFromUrls;
  if (!fn) return { ok: true, printed: 0 };
  const silent = getSilentPrintEnabled();
  const printOpts = {
    silent,
    /** When Silent print is off: auto-click Print on the dialog and close Sumatra per PDF. */
    dialogAssist: !silent,
    background: options?.awaitCompletion !== true,
  };
  const failureLog = options?.failureLog;
  const logPrintFailure = async (parsed: { ok: boolean; error?: string }) => {
    if (!failureLog || parsed.ok || !parsed.error) return;
    const { recordPrintQueueRtoFailure } = await import("./processFailureLog");
    await recordPrintQueueRtoFailure({
      dealerId: failureLog.dealerId,
      subfolder: failureLog.subfolder,
      customer: failureLog.customer,
      errorText: `Print: ${parsed.error}`,
    });
  };

  if (options?.awaitCompletion) {
    const result = normalizePrintJobResult(await fn(jobs, printOpts), jobs.length);
    await logPrintFailure(result);
    return result;
  }

  void fn(jobs, printOpts)
    .then((result) => normalizePrintJobResult(result, jobs.length))
    .then(async (parsed) => {
      await logPrintFailure(parsed);
      return parsed;
    });

  return { ok: true, printed: 0, queued: jobs.length };
}

/** DMS full flow / Create Invoice — keep in sync with `vite.config.ts` LONG_RUNNING_MS + fetch abort. */
const FILL_FORMS_TIMEOUT_MS = 900_000; // 15 min
/** Pre-open DMS browser after upload; allow enough time for first managed-browser launch. */
const DMS_WARM_BROWSER_TIMEOUT_MS = 300_000; // 5 min
const FILL_HERO_INSURANCE_TIMEOUT_MS = 900_000; // pre + main + post (Playwright)

export interface WarmDmsBrowserRequest {
  dms_base_url?: string | null;
}

export interface WarmDmsBrowserResponse {
  success: boolean;
  error?: string | null;
}

export interface WarmInsuranceBrowserRequest {
  insurance_base_url?: string | null;
}

export interface WarmInsuranceBrowserResponse {
  success: boolean;
  error?: string | null;
}

/** Open/attach DMS and wait through login readiness only (no fill). Fire-and-forget from Add Sales after upload. */
export async function warmDmsBrowser(req: WarmDmsBrowserRequest): Promise<WarmDmsBrowserResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DMS_WARM_BROWSER_TIMEOUT_MS);
  try {
    return await apiFetch<WarmDmsBrowserResponse>("/fill-forms/dms/warm-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Electron-aware warm browser: routes through the local sidecar (Playwright on the dealer PC)
 * when running inside Electron, falls back to the cloud API otherwise.
 */
export async function warmDmsBrowserLocal(req: WarmDmsBrowserRequest): Promise<WarmDmsBrowserResponse> {
  if (!isElectron()) return warmDmsBrowser(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "warm_browser",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { dms_base_url: req.dms_base_url ?? undefined },
    });
    if (result.timedOut) return { success: false, error: "Sidecar warm-browser timed out." };
    const data = (result.parsed as { data?: WarmDmsBrowserResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/** Open/attach Insurance and wait through login readiness only (no fill). */
export async function warmInsuranceBrowser(
  req: WarmInsuranceBrowserRequest
): Promise<WarmInsuranceBrowserResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DMS_WARM_BROWSER_TIMEOUT_MS);
  try {
    return await apiFetch<WarmInsuranceBrowserResponse>("/fill-forms/insurance/warm-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Electron-aware warm insurance: routes through the local sidecar (Playwright on the dealer PC)
 * when running inside Electron, falls back to the cloud API otherwise.
 */
export async function warmInsuranceBrowserLocal(
  req: WarmInsuranceBrowserRequest
): Promise<WarmInsuranceBrowserResponse> {
  if (!isElectron()) return warmInsuranceBrowser(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "warm_insurance",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { insurance_base_url: req.insurance_base_url ?? undefined },
    });
    if (result.timedOut) return { success: false, error: "Sidecar warm-insurance timed out." };
    const data = (result.parsed as { data?: WarmInsuranceBrowserResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

export interface WarmVahanBrowserResponse {
  success: boolean;
  error?: string | null;
  message?: string | null;
  /** When true, Vahan is logged in (Screen 1 visible) — batch can start on the same click. */
  ready_for_batch?: boolean;
}

/** Open/attach Vahan login (no fill). When already logged in, ``ready_for_batch`` allows one-click batch start. */
export async function warmVahanBrowser(): Promise<WarmVahanBrowserResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DMS_WARM_BROWSER_TIMEOUT_MS);
  try {
    return await apiFetch<WarmVahanBrowserResponse>("/fill-forms/vahan/warm-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

export interface FillHeroInsuranceRequest {
  insurance_base_url?: string | null;
  /** Optional when staging_id is set (resolved from staging after Create Invoice). */
  customer_id?: number | null;
  vehicle_id?: number | null;
  /** Optional when staging_id is set. */
  subfolder?: string | null;
  dealer_id?: number | null;
  /** When set, server resolves subfolder and can resolve customer_id / vehicle_id from staging payload. */
  staging_id?: string | null;
}

export interface FillHeroInsuranceResponse {
  success: boolean;
  error?: string | null;
  page_url?: string | null;
  login_url?: string | null;
  match_base?: string | null;
  print_jobs?: ApiPrintJob[];
  hero_insure_reports?: {
    ok?: boolean;
    error?: string | null;
    pdf_path?: string | null;
    grid_scrape?: Record<string, unknown> | null;
  };
}

/** Run only DMS section (login, enquiry, vehicle search, scrape, PDFs). Independent process. */
export async function fillDmsOnly(req: FillDmsRequest): Promise<FillDmsResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_FORMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-forms/dms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...req, client_api_base_url: getBaseUrl() ?? "" }),
      signal: controller.signal,
    });
    return res;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Electron-aware Create Invoice: routes through the local sidecar (Playwright on the dealer PC)
 * when running inside Electron, falls back to the cloud API otherwise.
 */
export async function fillDmsLocal(req: FillDmsRequest): Promise<FillDmsResponse> {
  if (!isElectron()) return fillDmsOnly(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_dms",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { ...req, client_api_base_url: getBaseUrl() ?? "" },
      timeoutMs: FILL_FORMS_TIMEOUT_MS,
    });
    if (result.timedOut) {
      throw new Error("Create Invoice request timed out. Check the upload folder for PDFs.");
    }
    const data = (result.parsed as { data?: FillDmsResponse })?.data;
    if (data) return data;
    const empty: FillDmsResponse = {
      success: false,
      vehicle: {},
      pdfs_saved: [],
      error: result.error ?? "Sidecar returned no data.",
    };
    return empty;
  } catch (err) {
    if (isFillDmsAbortError(err)) throw err;
    const empty: FillDmsResponse = {
      success: false,
      vehicle: {},
      pdfs_saved: [],
      error: err instanceof Error ? err.message : String(err),
    };
    return empty;
  }
}

export async function fillDms(req: FillDmsRequest): Promise<FillDmsResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_FORMS_TIMEOUT_MS);
  try {
    const res = await apiFetch<FillDmsResponse>("/fill-forms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...req, client_api_base_url: getBaseUrl() ?? "" }),
      signal: controller.signal,
    });
    return res;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Hero Insurance: pre_process (same automation as former standalone insurance fill) + main_process + post_process.
 * Single GI entry for Add Sales Generate Insurance.
 */
export async function fillHeroInsurance(req: FillHeroInsuranceRequest): Promise<FillHeroInsuranceResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_HERO_INSURANCE_TIMEOUT_MS);
  try {
    return await apiFetch<FillHeroInsuranceResponse>("/fill-forms/insurance/hero", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

export interface PrintForm20Request {
  subfolder: string;
  customer: FillDmsCustomer;
  vehicle: Record<string, unknown>;
  vehicle_id?: number | null;
  dealer_id?: number | null;
  /** Staging row for API merge when sidecar has no DATABASE_URL (Print / Queue RTO). */
  staging_id?: string | null;
}

export interface PrintForm20Response {
  success: boolean;
  pdfs_saved: string[];
  error?: string | null;
  print_jobs?: ApiPrintJob[];
}

export interface UploadSaleFolderRequest {
  dealer_id: number;
  subfolder: string;
}

export interface UploadSaleFolderResponse {
  success: boolean;
  error?: string | null;
  files_uploaded?: number;
  files_downloaded?: number;
  files_failed?: number;
  subfolder?: string;
  log_file?: string;
}

export type PrintRtoQueueLogLine = { prefix: string; message: string };

/** Per-sale trace filename under ``ocr_output/{dealer}/{subfolder}/`` (see Admin Usage → OCR output). */
export const PRINT_RTO_QUEUE_LOG_FILENAME = "Print_RTO_queue.txt";

/** Max time to mirror a full sale folder (uploads + ocr) to EC2 before Print / Queue RTO. */
const UPLOAD_SALE_FOLDER_TIMEOUT_MS = 600_000;
const UPLOAD_PRINT_RTO_LOG_TIMEOUT_MS = 120_000;

/** Generate Form 20 (all pages) and save to Uploaded scans. Called from Print forms button. */
export async function printForm20(req: PrintForm20Request): Promise<PrintForm20Response> {
  return apiFetch<PrintForm20Response>("/fill-forms/print-form20", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

/**
 * Electron: two-way sale-folder sync via sidecar ``upload_sale_artifacts`` —
 * pull Aadhaar / detail sheet / pencil mark from server, then push all local uploads (+ ocr) to EC2/S3.
 * Browser-only dev: no-op (server must already have files).
 */
export async function uploadSaleFolderToServer(
  req: UploadSaleFolderRequest
): Promise<UploadSaleFolderResponse> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder || req.dealer_id <= 0) {
    return { success: false, error: "dealer_id and subfolder are required." };
  }
  if (!isElectron()) {
    return { success: true, files_uploaded: 0 };
  }
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "upload_sale_artifacts",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { dealer_id: req.dealer_id, subfolder },
      timeoutMs: UPLOAD_SALE_FOLDER_TIMEOUT_MS,
    });
    const data = (result.parsed as { data?: UploadSaleFolderResponse })?.data;
    if (result.timedOut) {
      return { success: false, error: "Upload sale folder timed out." };
    }
    if (!result.success || !data?.success) {
      return {
        success: false,
        error:
          data?.error ||
          (typeof result.error === "string" ? result.error : null) ||
          result.stderr?.slice(0, 500) ||
          "Upload sale folder failed.",
      };
    }
    return {
      success: true,
      files_uploaded: data.files_uploaded,
      files_downloaded: data.files_downloaded,
      files_failed: data.files_failed,
      subfolder: data.subfolder,
      log_file: data.log_file,
    };
  } catch (e) {
    return { success: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/**
 * Append UI/sidecar lines to local ``Print_RTO_queue.txt`` and upload it to EC2 (Admin → OCR output).
 */
export async function finalizePrintRtoQueueLog(req: {
  dealer_id: number;
  subfolder: string;
  lines: PrintRtoQueueLogLine[];
}): Promise<void> {
  const subfolder = (req.subfolder || "").trim();
  if (!subfolder || req.dealer_id <= 0 || !req.lines.length) return;
  if (!isElectron()) return;
  try {
    await window.electronAPI!.sidecar.runJob({
      type: "upload_print_rto_queue_log",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: {
        dealer_id: req.dealer_id,
        subfolder,
        lines: req.lines,
      },
      timeoutMs: UPLOAD_PRINT_RTO_LOG_TIMEOUT_MS,
    });
  } catch {
    /* best-effort */
  }
}

/** Hint for status banners — open under Admin Usage → Sales → OCR output. */
export function printRtoQueueLogHint(subfolder: string): string {
  const sf = (subfolder || "").trim() || "default";
  return `Trace: ocr_output/${sf}/${PRINT_RTO_QUEUE_LOG_FILENAME}`;
}

/**
 * Resolves Sale Certificate + Insurance PDFs in the sale folder, generates Gate Pass, returns
 * ordered ``print_jobs`` (1. Sale Certificate, 2. Insurance, 3. Gate Pass) for Electron.
 */
export async function printGatePass(req: PrintForm20Request): Promise<PrintForm20Response> {
  return apiFetch<PrintForm20Response>("/fill-forms/print-gate-pass", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

/** Fetch Data from DMS for a subfolder (fallback when Fill Forms response was lost). */
export async function getDataFromDms(subfolder: string, dealerId?: number): Promise<{ vehicle: Record<string, string>; customer: Record<string, string> }> {
  const params = new URLSearchParams();
  params.set("subfolder", subfolder);
  if (dealerId != null) params.set("dealer_id", String(dealerId));
  return apiFetch<{ vehicle: Record<string, string>; customer: Record<string, string> }>(
    `/fill-forms/data-from-dms?${params.toString()}`
  );
}

/** True if the error is from an aborted request (timeout or user abort). */
export function isFillDmsAbortError(err: unknown): boolean {
  if (err instanceof Error) {
    const m = err.message?.toLowerCase() ?? "";
    return m.includes("abort") || m.includes("aborted");
  }
  return false;
}

/**
 * Electron-aware Generate Insurance: routes through the local sidecar when in Electron,
 * falls back to the cloud API otherwise.
 */
export async function fillHeroInsuranceLocal(req: FillHeroInsuranceRequest): Promise<FillHeroInsuranceResponse> {
  if (!isElectron()) return fillHeroInsurance(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_insurance",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: { ...req },
      timeoutMs: FILL_HERO_INSURANCE_TIMEOUT_MS,
    });
    if (result.timedOut) return { success: false, error: "Insurance sidecar timed out." };
    const data = (result.parsed as { data?: FillHeroInsuranceResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

export interface FillCpaAllianceInsuranceRequest {
  dealer_id?: number | null;
  /** Optional when staging_id is set (resolved from staging after Submit Info). */
  subfolder?: string | null;
  portal_url: string;
  staging_id?: string | null;
  customer_id?: number | null;
  vehicle_id?: number | null;
}

/** Build CPA Alliance request — server loads fill values from form_cpa_insurance_view + staging. */
export function buildFillCpaAllianceInsuranceRequest(params: {
  dealerId: number;
  portalUrl: string;
  subfolder?: string | null;
  stagingId?: string | null;
  customerId?: number | null;
  vehicleId?: number | null;
}): FillCpaAllianceInsuranceRequest {
  return {
    dealer_id: params.dealerId,
    portal_url: params.portalUrl,
    subfolder: params.subfolder?.trim() || undefined,
    staging_id: params.stagingId?.trim() || undefined,
    customer_id: params.customerId ?? undefined,
    vehicle_id: params.vehicleId ?? undefined,
  };
}

export interface FillCpaAllianceInsuranceResponse {
  success: boolean;
  error?: string | null;
  page_url?: string | null;
  playwright_log?: string | null;
  certificate_number?: string | null;
}

/** CPA Alliance portal on the API host (browser dev / cloud worker). Same automation as the Electron sidecar. */
export async function fillCpaAllianceInsurance(
  req: FillCpaAllianceInsuranceRequest
): Promise<FillCpaAllianceInsuranceResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FILL_HERO_INSURANCE_TIMEOUT_MS);
  try {
    return await apiFetch<FillCpaAllianceInsuranceResponse>("/fill-forms/insurance/cpa-alliance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * CPA Alliance: routes through the local sidecar in Electron; otherwise POSTs to the API
 * (Playwright runs on the machine hosting the backend — same pattern as Generate Insurance / Vahan warm).
 */
export async function fillCpaAllianceInsuranceLocal(
  req: FillCpaAllianceInsuranceRequest
): Promise<FillCpaAllianceInsuranceResponse> {
  if (!isElectron()) return fillCpaAllianceInsurance(req);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_cpa_alliance_insurance",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: {
        dealer_id: req.dealer_id,
        subfolder: req.subfolder ?? undefined,
        portal_url: req.portal_url,
        staging_id: req.staging_id ?? undefined,
        customer_id: req.customer_id ?? undefined,
        vehicle_id: req.vehicle_id ?? undefined,
      },
      timeoutMs: FILL_HERO_INSURANCE_TIMEOUT_MS,
    });
    if (result.timedOut) {
      return { success: false, error: "CPA Insurance sidecar timed out." };
    }
    const root = result.parsed as {
      success?: boolean;
      data?: FillCpaAllianceInsuranceResponse;
      error?: string;
    };
    const data = root?.data;
    if (data && typeof data === "object") {
      return {
        success: Boolean(data.success),
        error: data.error ?? undefined,
        page_url: data.page_url ?? undefined,
        playwright_log: data.playwright_log ?? undefined,
        certificate_number: (data as any).certificate_number ?? undefined,
      };
    }
    const err =
      root?.error ??
      result.error ??
      (root?.success === false ? "CPA Insurance sidecar job failed." : "CPA Insurance sidecar returned no data.");
    return { success: false, error: err };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Electron-aware Warm Vahan Browser: routes through the local sidecar when in Electron,
 * falls back to the cloud API otherwise.
 */
export function formatVahanWarmError(err: unknown): string {
  if (isFillDmsAbortError(err)) {
    return (
      "Vahan warm-browser timed out. Ensure the API is running, log in on Vahan, " +
      "then press Fill Vahan Site again."
    );
  }
  return err instanceof Error ? err.message : String(err);
}

export async function warmVahanBrowserLocal(): Promise<WarmVahanBrowserResponse> {
  if (!isElectron()) return warmVahanBrowser();
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "warm_vahan",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: {},
      timeoutMs: DMS_WARM_BROWSER_TIMEOUT_MS,
    });
    if (result.timedOut) return { success: false, error: "Vahan warm-browser timed out." };
    const data = (result.parsed as { data?: WarmVahanBrowserResponse })?.data;
    if (data) return data;
    return { success: result.success, error: result.error ?? undefined };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}
