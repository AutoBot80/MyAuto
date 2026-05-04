import { apiFetch, getBaseUrl } from "./client";
import { DEALER_ID } from "./dealerId";
import { isElectron } from "../electron";
import { getAccessToken } from "../auth/token";

/** Matches server default for Processed list and failed badge window. */
export const CHALLAN_STAGING_RECENT_DAYS = 15;

/** Timeout for sidecar subdealer challan processing (same order as Fill DMS). */
const SUBDEALER_CHALLAN_TIMEOUT_MS = 900_000;

export type SubdealerChallanLine = {
  engine_no: string;
  chassis_no: string;
  status: string;
};

export type ParseSubdealerChallanResponse = {
  challan_no: string | null;
  challan_date_raw: string | null;
  challan_date_iso: string | null;
  challan_ddmmyyyy: string | null;
  lines: SubdealerChallanLine[];
  artifact_dir: string | null;
  raw_ocr_path: string | null;
  ocr_json_path: string | null;
  /** Present when ``?mirror_bodies=true`` (Electron): folder leaf under ``ocr_output/{dealer_id}/`` on dealer PC. */
  local_artifact_leaf?: string | null;
  raw_ocr_text?: string | null;
  ocr_json_text?: string | null;
  warnings: string[];
  error: string | null;
};

function _pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/**
 * Folder leaf under ``ocr_output/{dealer_id}/`` — matches backend ``challan_artifact_leaf_name`` / ``_challan_folder_name``.
 */
export function computeLocalChallanArtifactLeaf(r: ParseSubdealerChallanResponse): string {
  let ddmmyyyy = (r.challan_ddmmyyyy || "").trim();
  const iso = (r.challan_date_iso || "").trim();
  if (!ddmmyyyy && iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (m) {
      const d = parseInt(m[3], 10);
      const mo = parseInt(m[2], 10);
      const y = parseInt(m[1], 10);
      ddmmyyyy = `${_pad2(d)}${_pad2(mo)}${y}`;
    }
  }
  if (!ddmmyyyy) {
    const raw = (r.challan_date_raw || "").trim();
    const sm = /^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/.exec(raw);
    if (sm) {
      const d = parseInt(sm[1], 10);
      const mo = parseInt(sm[2], 10);
      let y = parseInt(sm[3], 10);
      if (sm[3].length === 2) y = y <= 69 ? 2000 + y : 1900 + y;
      ddmmyyyy = `${_pad2(d)}${_pad2(mo)}${y}`;
    }
  }
  if (!ddmmyyyy) {
    const now = new Date();
    ddmmyyyy = `${_pad2(now.getUTCDate())}${_pad2(now.getUTCMonth() + 1)}${now.getUTCFullYear()}`;
  }
  const bad = '<>:"/\\|?*';
  let cn = (r.challan_no || "")
    .trim()
    .split("")
    .filter((c) => !bad.includes(c))
    .join("")
    .trim()
    .slice(0, 80);
  if (!cn) cn = "unknown";
  if (cn === "unknown") {
    const ts = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 15);
    return `unknown_${ts}`;
  }
  return `${cn}_${ddmmyyyy}`;
}

async function mirrorChallanParseArtifactsToDealerPc(
  merged: ParseSubdealerChallanResponse,
  pages: ParseSubdealerChallanResponse[],
  pageNames: string[]
): Promise<void> {
  if (!isElectron() || !window.electronAPI?.sidecar?.runJob) return;
  const hasBodies = pages.some((p) => (p.raw_ocr_text || "").length > 0 || (p.ocr_json_text || "").length > 0);
  if (!hasBodies) return;
  const leaf = computeLocalChallanArtifactLeaf(merged);
  const rawCombined = pages
    .map((pg, i) => {
      const body = (pg.raw_ocr_text || "").trim();
      if (!body) return "";
      const label = pageNames[i] || `page ${i + 1}`;
      return `=== ${label} ===\n${body}`;
    })
    .filter(Boolean)
    .join("\n\n");
  const jsonText = JSON.stringify(
    {
      challan_no: merged.challan_no,
      challan_date_raw: merged.challan_date_raw,
      challan_date_iso: merged.challan_date_iso,
      challan_ddmmyyyy: merged.challan_ddmmyyyy,
      lines: merged.lines,
    },
    null,
    2
  );
  try {
    await window.electronAPI.sidecar.runJob({
      type: "mirror_challan_parse_artifacts",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: {
        dealer_id: DEALER_ID,
        artifact_leaf: leaf,
        raw_ocr_text: rawCombined,
        ocr_json_text: jsonText,
      },
      timeoutMs: 60_000,
    });
  } catch {
    /* non-fatal: OCR parse already succeeded */
  }
}

/**
 * POST /subdealer-challan/parse-scan — multipart image/PDF (one file per request).
 */
export async function parseSubdealerChallanScan(
  file: File
): Promise<ParseSubdealerChallanResponse> {
  const body = new FormData();
  body.append("file", file);
  const q = isElectron() ? "?mirror_bodies=true" : "";
  return apiFetch<ParseSubdealerChallanResponse>(`/subdealer-challan/parse-scan${q}`, {
    method: "POST",
    body,
  });
}

/** For multi-page challans: pick the numerically largest book number; fallback to string/natural compare. */
export function maxChallanBookNumber(a: string | null, b: string | null): string | null {
  const ta = (a ?? "").trim();
  const tb = (b ?? "").trim();
  if (!ta) return tb || null;
  if (!tb) return ta;
  if (/^\d+$/.test(ta) && /^\d+$/.test(tb)) {
    try {
      return BigInt(ta) >= BigInt(tb) ? ta : tb;
    } catch {
      /* fall through */
    }
  }
  return ta.localeCompare(tb, undefined, { numeric: true, sensitivity: "base" }) >= 0 ? ta : tb;
}

type MergeContext = { label?: string };

function withLabel(msg: string, ctx: MergeContext | undefined, prefixFileName: boolean): string {
  if (!prefixFileName || !ctx?.label) return msg;
  return `${ctx.label}: ${msg}`;
}

/**
 * Merge several `/parse-scan` results (e.g. one per challan page). Vehicle lines are appended
 * in file order; the UI should de-dupe. **Challan book number** uses ``maxChallanBookNumber`` across pages.
 * **Date** uses the first file that has a parseable date; if another file disagrees, a warning is added.
 * Artifact paths come from the last file (for debugging / support).
 */
export function mergeSubdealerChallanParseResults(
  results: ParseSubdealerChallanResponse[],
  fileNames: string[] | null = null
): ParseSubdealerChallanResponse {
  if (results.length === 0) {
    return {
      challan_no: null,
      challan_date_raw: null,
      challan_date_iso: null,
      challan_ddmmyyyy: null,
      lines: [],
      artifact_dir: null,
      raw_ocr_path: null,
      ocr_json_path: null,
      warnings: [],
      error: null,
    };
  }
  if (results.length === 1) {
    return { ...results[0] };
  }

  const first = results[0];
  const multiline = true;
  const allWarnings: string[] = [];
  const lines: SubdealerChallanLine[] = [];
  let challanNo: string | null = null;

  const anchorIndex = results.findIndex(
    (r) => Boolean((r.challan_date_iso || "").trim() || (r.challan_ddmmyyyy || "").trim())
  );
  const a = anchorIndex >= 0 ? results[anchorIndex] : first;
  let dateRaw: string | null = a.challan_date_raw;
  let dateIso: string | null = a.challan_date_iso;
  let ddmm: string | null = a.challan_ddmmyyyy;
  const canonIso = (dateIso || "").trim();
  const canonDd = (ddmm || "").trim();
  const anchorLabel =
    anchorIndex >= 0 ? fileNames?.[anchorIndex] ?? `page ${anchorIndex + 1}` : "the first file";

  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    const name = fileNames?.[i] ?? `page ${i + 1}`;
    const ctx: MergeContext = { label: name };
    challanNo = maxChallanBookNumber(challanNo, r.challan_no);
    for (const w of r.warnings || []) {
      const t = (w || "").trim();
      if (t) allWarnings.push(withLabel(t, ctx, multiline));
    }
    for (const ln of r.lines || []) lines.push(ln);

    const rIso = (r.challan_date_iso || "").trim();
    const rDd = (r.challan_ddmmyyyy || "").trim();
    if (i !== anchorIndex && (rIso || rDd)) {
      const disagree =
        (canonIso && rIso && rIso !== canonIso) || (canonDd && rDd && rDd !== canonDd);
      if (disagree) {
        allWarnings.push(
          withLabel(
            `challan date on this scan (${r.challan_date_raw || r.challan_date_iso || "?"}) ` +
              `differs from ${anchorLabel} — keeping the date from ${anchorLabel} for staging.`,
            ctx,
            true
          )
        );
      }
    }
  }

  const last = results[results.length - 1];
  const seenW = new Set<string>();
  const outW: string[] = [];
  for (const w of allWarnings) {
    const k = w.trim();
    if (!k || seenW.has(k)) continue;
    seenW.add(k);
    outW.push(w);
  }
  return {
    challan_no: challanNo,
    challan_date_raw: dateRaw,
    challan_date_iso: dateIso,
    challan_ddmmyyyy: ddmm,
    lines,
    artifact_dir: last.artifact_dir,
    raw_ocr_path: last.raw_ocr_path,
    ocr_json_path: last.ocr_json_path,
    warnings: outW,
    error: first.error,
  };
}

/**
 * Run OCR for each file (in order) and merge into one result for staging.
 */
export async function parseSubdealerChallanScans(
  files: File[],
  onProgress?: (current: number, total: number) => void
): Promise<ParseSubdealerChallanResponse> {
  if (files.length === 0) {
    return mergeSubdealerChallanParseResults([]);
  }
  if (files.length === 1) {
    onProgress?.(1, 1);
    return parseSubdealerChallanScan(files[0]);
  }
  const results: ParseSubdealerChallanResponse[] = [];
  const names: string[] = [];
  for (let i = 0; i < files.length; i++) {
    onProgress?.(i + 1, files.length);
    const f = files[i];
    names.push(f.name);
    try {
      results.push(await parseSubdealerChallanScan(f));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      throw new Error(`OCR failed on "${f.name}": ${msg}`);
    }
  }
  const merged = mergeSubdealerChallanParseResults(results, names);
  if (isElectron() && !(merged.error || "").trim()) {
    await mirrorChallanParseArtifactsToDealerPc(merged, results, names);
  }
  return merged;
}

export type CreateChallanStagingBody = {
  from_dealer_id: number;
  to_dealer_id: number;
  challan_date?: string | null;
  challan_book_num?: string | null;
  lines: { raw_engine?: string; raw_chassis?: string }[];
};

export type CreateChallanStagingResponse = {
  challan_batch_id: string;
  ok: boolean;
  /** Vehicles dropped because the same engine/chassis already exists on a challan for this book+date (any status). */
  dropped_existing_same_book_date?: number;
  /** Duplicate engine/chassis rows removed within this submission (first kept). */
  dropped_duplicate_in_request?: number;
};

export async function createChallanStaging(
  body: CreateChallanStagingBody
): Promise<CreateChallanStagingResponse> {
  return apiFetch<CreateChallanStagingResponse>("/subdealer-challan/staging", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type ProcessChallanBody = {
  dms_base_url?: string | null;
  dealer_id?: number | null;
  /**
   * Electron sidecar only — matches server ``run_subdealer_challan_batch`` phase:
   * ``full`` (prepare + order, default) or ``order_only`` (Retry Order when all lines Ready).
   */
  subdealer_phase?: "full" | "order_only";
};

export type ProcessChallanResponse = {
  ok?: boolean;
  error?: string | null;
  challan_id?: number | null;
  dms_step_messages?: string[];
  vehicle?: Record<string, unknown>;
};

export async function processChallanBatch(
  challanBatchId: string,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  return apiFetch<ProcessChallanResponse>(`/subdealer-challan/process/${encodeURIComponent(challanBatchId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(SUBDEALER_CHALLAN_TIMEOUT_MS),
  });
}

/**
 * Electron-aware Process Challan: routes through the local sidecar (Playwright on the dealer PC)
 * when running inside Electron, falls back to the cloud API otherwise.
 */
export async function processChallanBatchLocal(
  challanBatchId: string,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  if (!isElectron()) return processChallanBatch(challanBatchId, body);
  try {
    const result = await window.electronAPI!.sidecar.runJob({
      type: "fill_subdealer_challan",
      api_url: getBaseUrl(),
      jwt: getAccessToken() ?? "",
      params: {
        challan_batch_id: challanBatchId,
        dealer_id: body.dealer_id ?? undefined,
        dms_base_url: body.dms_base_url ?? undefined,
        phase: body.subdealer_phase ?? "full",
      },
      timeoutMs: SUBDEALER_CHALLAN_TIMEOUT_MS,
    });
    if (result.timedOut) {
      return { ok: false, error: "Subdealer challan processing timed out." };
    }
    const data = (result.parsed as { data?: ProcessChallanResponse })?.data;
    if (data) return data;
    return {
      ok: result.success,
      error: result.error ?? (result.success ? undefined : "Sidecar returned no data."),
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/** One staging line under a batch (from GET /staging/recent ``detail_lines`` / ``failed_lines``). */
export type ChallanFailedDetailLine = {
  challan_detail_staging_id: number;
  raw_chassis: string | null;
  raw_engine: string | null;
  last_error: string | null;
  status: string | null;
};

/**
 * GET /subdealer-challan/staging/recent — one row per batch (master) for the Processed tab.
 */
export type ChallanMasterProcessedRow = {
  challan_batch_id: string;
  from_dealer_id: number;
  to_dealer_id: number;
  from_dealer_name?: string | null;
  to_dealer_name?: string | null;
  challan_date: string | null;
  challan_book_num: string | null;
  num_vehicles: number;
  num_vehicles_prepared: number;
  invoice_complete: boolean;
  invoice_status: string | null;
  created_at: string | null;
  /** Set when process/retry DMS batch completes (ISO timestamp). */
  last_run_at?: string | null;
  ready_line_count: number;
  failed_line_count: number;
  /** Failed-only subset (legacy). Prefer ``detail_lines`` when present. */
  failed_lines: ChallanFailedDetailLine[];
  /** All vehicle lines: Queued / Failed / Ready / Committed. */
  detail_lines?: ChallanFailedDetailLine[];
};

export type ListRecentChallanStagingOptions = {
  /** Maps to ``challan_book_num``; when set, API returns that challan regardless of age (no 15-day window). */
  challanBookNum?: string | null;
};

export async function listRecentChallanStaging(
  dealerId?: number,
  days: number = CHALLAN_STAGING_RECENT_DAYS,
  options?: ListRecentChallanStagingOptions
): Promise<ChallanMasterProcessedRow[]> {
  const search = new URLSearchParams();
  search.set("dealer_id", String(dealerId ?? DEALER_ID));
  search.set("days", String(days));
  const book = (options?.challanBookNum ?? "").trim();
  if (book) search.set("challan_book_num", book);
  return apiFetch<ChallanMasterProcessedRow[]>(`/subdealer-challan/staging/recent?${search.toString()}`);
}

/** GET /subdealer-challan/staging/failed-count — badge: master-table row count (batches needing attention in the window). */
export async function getChallanStagingFailedCount(
  dealerId?: number,
  days: number = CHALLAN_STAGING_RECENT_DAYS
): Promise<number> {
  const search = new URLSearchParams();
  search.set("dealer_id", String(dealerId ?? DEALER_ID));
  search.set("days", String(days));
  const res = await apiFetch<{ failed: number }>(`/subdealer-challan/staging/failed-count?${search.toString()}`);
  return res.failed ?? 0;
}

/** POST /subdealer-challan/staging/{challan_detail_staging_id}/retry — prepare + order for the batch (long-running). */
export async function retryChallanStagingRow(
  challanDetailStagingId: number,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  return apiFetch<ProcessChallanResponse>(
    `/subdealer-challan/staging/${encodeURIComponent(String(challanDetailStagingId))}/retry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(SUBDEALER_CHALLAN_TIMEOUT_MS),
    }
  );
}

/** POST /subdealer-challan/batch/{challan_batch_id}/retry-order — order/invoice only (all lines Ready). */
export async function retryChallanOrderOnly(
  challanBatchId: string,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  return apiFetch<ProcessChallanResponse>(
    `/subdealer-challan/batch/${encodeURIComponent(challanBatchId)}/retry-order`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(SUBDEALER_CHALLAN_TIMEOUT_MS),
    }
  );
}

/**
 * Electron-aware Retry Order Only: routes through the local sidecar when in Electron.
 * All lines must be Ready; skips prepare_vehicle and runs order phase only.
 */
export async function retryChallanOrderOnlyLocal(
  challanBatchId: string,
  body: ProcessChallanBody = {}
): Promise<ProcessChallanResponse> {
  if (!isElectron()) return retryChallanOrderOnly(challanBatchId, body);
  return processChallanBatchLocal(challanBatchId, {
    ...body,
    subdealer_phase: "order_only",
  });
}

export type PatchChallanStagingFailedLineBody = {
  raw_chassis: string;
  raw_engine: string;
};

/**
 * PATCH /subdealer-challan/staging/detail/{id} — update chassis/engine on a **Failed** line before batch retry.
 */
export async function patchChallanStagingFailedLine(
  challanDetailStagingId: number,
  body: PatchChallanStagingFailedLineBody
): Promise<{ ok?: boolean; error?: string | null }> {
  return apiFetch<{ ok?: boolean; error?: string | null }>(
    `/subdealer-challan/staging/detail/${encodeURIComponent(String(challanDetailStagingId))}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}
