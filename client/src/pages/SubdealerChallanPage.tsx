import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listDealersByParent, type DealerByParentRow } from "../api/dealers";
import {
  CHALLAN_STAGING_RECENT_DAYS,
  createChallanStaging,
  listRecentChallanStaging,
  parseSubdealerChallanScans,
  patchChallanStagingFailedLine,
  processChallanBatchLocal,
  retryChallanOrderOnlyLocal,
  type ChallanFailedDetailLine,
  type ChallanMasterProcessedRow,
  type SubdealerChallanLine,
} from "../api/subdealerChallan";
import { warmDmsBrowserLocal } from "../api/fillForms";

const ROWS_PER_TABLE = 13;
const TABLE_COUNT = 2;
const PAGE_SIZE = ROWS_PER_TABLE * TABLE_COUNT;

/** Display label for line status (matches product wording; API may use lowercase). */
const STATUS_QUEUED_LABEL = "Queued";

export type ChallanRow = {
  id: string;
  engineNo: string;
  chassisNo: string;
  status: string;
};

function newEmptyRow(): ChallanRow {
  return {
    id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `r-${Date.now()}-${Math.random()}`,
    engineNo: "",
    chassisNo: "",
    status: "",
  };
}

function rowHasVehicleData(r: ChallanRow | undefined): boolean {
  if (!r) return false;
  return Boolean(r.engineNo.trim() || r.chassisNo.trim());
}

function statusCellLabel(r: ChallanRow | undefined): string {
  if (!r) return "";
  const s = (r.status || "").trim();
  if (s) return s;
  return rowHasVehicleData(r) ? STATUS_QUEUED_LABEL : "";
}

/** Same identity as backend dedupe: uppercased trimmed engine + chassis (both may be partial). */
function vehicleIdentityKey(r: ChallanRow): string | null {
  const e = r.engineNo.trim().toUpperCase();
  const c = r.chassisNo.trim().toUpperCase();
  if (!e && !c) return null;
  return `${e}\0${c}`;
}

function uniqueVehicleCount(rows: ChallanRow[]): number {
  const seen = new Set<string>();
  for (const r of rows) {
    const k = vehicleIdentityKey(r);
    if (k) seen.add(k);
  }
  return seen.size;
}

/** Drop later rows that repeat the same (engine, chassis) as an earlier row; first wins. Blank rows kept. */
function dedupeRowsByVehicleIdentity(rows: ChallanRow[]): ChallanRow[] {
  const seen = new Set<string>();
  const out: ChallanRow[] = [];
  for (const r of rows) {
    const k = vehicleIdentityKey(r);
    if (k !== null) {
      if (seen.has(k)) continue;
      seen.add(k);
    }
    out.push(r);
  }
  return out;
}

type ChallanSubTab = "new" | "processed";

function formatDealerDisplay(name: string | null | undefined, dealerId: number): string {
  const n = (name || "").trim();
  return n || `Dealer ${dealerId}`;
}

function formatPreparedOverTotal(r: ChallanMasterProcessedRow): string {
  const p = r.num_vehicles_prepared ?? 0;
  const t = r.num_vehicles ?? 0;
  if (t <= 0) return "—";
  return `${p}/${t}`;
}

/** Detail lines for one master row (failed rows first). Retry uses this with an explicit batch id because row click does not fire when pressing Retry (`stopPropagation`). */
function sortedDetailLinesForBatch(row: ChallanMasterProcessedRow | null): ChallanFailedDetailLine[] {
  if (!row) return [];
  const lines = row.detail_lines !== undefined ? row.detail_lines : row.failed_lines ?? [];
  return [...lines].sort((a, b) => {
    const af = (a.status || "").trim().toLowerCase() === "failed" ? 0 : 1;
    const bf = (b.status || "").trim().toLowerCase() === "failed" ? 0 : 1;
    return af - bf;
  });
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Normalize stored/API challan date to dd/mm/yyyy. */
function formatChallanDateDisplay(s: string | null | undefined): string {
  const t = (s || "").trim();
  if (!t) return "—";
  const iso = t.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) {
    const [, y, m, d] = iso;
    return `${d}/${m}/${y}`;
  }
  const slash = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (slash) {
    const d = parseInt(slash[1], 10);
    const m = parseInt(slash[2], 10);
    const y = slash[3];
    if (d >= 1 && d <= 31 && m >= 1 && m <= 12) {
      return `${pad2(d)}/${pad2(m)}/${y}`;
    }
  }
  const dash = t.match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/);
  if (dash) {
    const d = parseInt(dash[1], 10);
    const m = parseInt(dash[2], 10);
    const y = dash[3];
    if (d >= 1 && d <= 31 && m >= 1 && m <= 12) {
      return `${pad2(d)}/${pad2(m)}/${y}`;
    }
  }
  const dot = t.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
  if (dot) {
    const d = parseInt(dot[1], 10);
    const m = parseInt(dot[2], 10);
    const y = dot[3];
    if (d >= 1 && d <= 31 && m >= 1 && m <= 12) {
      return `${pad2(d)}/${pad2(m)}/${y}`;
    }
  }
  const digitsOnly = t.replace(/\D/g, "");
  if (digitsOnly.length === 8 && digitsOnly !== t) {
    return formatChallanDateDisplay(digitsOnly);
  }
  // Eight digits without separators: ddmmyyyy (e.g. "08042026") or yyyymmdd (e.g. "20260408")
  if (/^\d{8}$/.test(t)) {
    const dd = t.slice(0, 2);
    const mm = t.slice(2, 4);
    const yyyy = t.slice(4, 8);
    const d = parseInt(dd, 10);
    const m = parseInt(mm, 10);
    if (d >= 1 && d <= 31 && m >= 1 && m <= 12) {
      return `${dd}/${mm}/${yyyy}`;
    }
    const yIso = t.slice(0, 4);
    const mmIso = t.slice(4, 6);
    const ddIso = t.slice(6, 8);
    const mi = parseInt(mmIso, 10);
    const di = parseInt(ddIso, 10);
    if (mi >= 1 && mi <= 12 && di >= 1 && di <= 31) {
      return `${ddIso}/${mmIso}/${yIso}`;
    }
  }
  return t;
}

const LATEST_RUN_TZ = "Asia/Kolkata";

/** Batch last DMS run: dd/mm/yyyy hh:mm (IST). */
function formatLatestRunDisplay(iso: string | null | undefined): string {
  const t = (iso || "").trim();
  if (!t) return "—";
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "—";
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: LATEST_RUN_TZ,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return fmt.format(d).replace(",", "").replace(/\s+/g, " ").trim();
}

function showRetryOrderOnly(r: ChallanMasterProcessedRow): boolean {
  const inv = (r.invoice_status || "").trim().toLowerCase();
  const failed = r.failed_line_count ?? 0;
  return inv === "failed" && failed === 0;
}

/** Full DMS retry (prepare + order): failed lines, or vehicles still not Ready/Committed (e.g. all Queued after a retry). */
function showRetryFullBatch(r: ChallanMasterProcessedRow): boolean {
  if (r.invoice_complete) return false;
  if (showRetryOrderOnly(r)) return false;
  const failed = r.failed_line_count ?? 0;
  if (failed > 0) return true;
  const n = r.num_vehicles ?? 0;
  const prep = r.num_vehicles_prepared ?? 0;
  return n > 0 && prep < n;
}

/** Last row must be blank (both engine and chassis empty) so user can add more. */
function ensureTrailingBlankRow(rows: ChallanRow[]): ChallanRow[] {
  if (rows.length === 0) return [newEmptyRow()];
  const last = rows[rows.length - 1];
  if (rowHasVehicleData(last)) {
    return [...rows, newEmptyRow()];
  }
  return rows;
}

/**
 * POS Saathi — Subdealer Challan: to dealer, upload scan (OCR), two 13-row tables per page, delete row, add via blank row.
 */
export type SubdealerChallanPageProps = {
  dealerId: number;
  dmsUrl: string;
  challanFailedCount: number;
  onChallanCountsRefresh: () => void;
};

export function SubdealerChallanPage({
  dealerId,
  dmsUrl,
  challanFailedCount,
  onChallanCountsRefresh,
}: SubdealerChallanPageProps) {
  const [challanSubTab, setChallanSubTab] = useState<ChallanSubTab>("new");
  const [subdealerOptions, setSubdealerOptions] = useState<DealerByParentRow[]>([]);
  const [subdealersLoading, setSubdealersLoading] = useState(false);
  const [subdealersError, setSubdealersError] = useState<string | null>(null);
  /** Selected child ``dealer_id`` (``to_dealer_id``); null = placeholder. */
  const [selectedToDealerId, setSelectedToDealerId] = useState<number | null>(null);
  const [challanNo, setChallanNo] = useState<string | null>(null);
  const [challanDateRaw, setChallanDateRaw] = useState<string | null>(null);
  const [challanDateIso, setChallanDateIso] = useState<string | null>(null);
  const [challanDdmmyyyy, setChallanDdmmyyyy] = useState<string | null>(null);
  const [rows, setRows] = useState<ChallanRow[]>(() => [newEmptyRow()]);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [processingChallan, setProcessingChallan] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [processedRows, setProcessedRows] = useState<ChallanMasterProcessedRow[]>([]);
  const [processedLoading, setProcessedLoading] = useState(false);
  const [processedError, setProcessedError] = useState<string | null>(null);
  /** Draft input; **Search** copies trimmed value to ``processedChallanSearchApplied`` for the API. */
  const [processedChallanSearchDraft, setProcessedChallanSearchDraft] = useState("");
  /** When empty: API lists batches needing attention (failed lines or failed invoice) in the last 15 days. */
  const [processedChallanSearchApplied, setProcessedChallanSearchApplied] = useState("");
  const [retryingProcessBatchId, setRetryingProcessBatchId] = useState<string | null>(null);
  const [retryingOrderBatchId, setRetryingOrderBatchId] = useState<string | null>(null);
  /** Edits to Failed lines (chassis/engine) before **Save all changes**. */
  const [failedLineDrafts, setFailedLineDrafts] = useState<
    Record<number, { raw_chassis: string; raw_engine: string }>
  >({});
  const [savingFailedLineEdits, setSavingFailedLineEdits] = useState(false);
  /** Master row selection drives the lower vehicle lines sub-table. */
  const [selectedProcessedBatchId, setSelectedProcessedBatchId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** Multi-page OCR: ``parse-scan`` is one file per request. */
  const [parseOcrProgress, setParseOcrProgress] = useState<{ current: number; total: number } | null>(null);

  const vehicleCount = useMemo(() => uniqueVehicleCount(rows), [rows]);

  /**
   * Warm / bind DMS browser via the local sidecar (Electron) or cloud API fallback.
   * Always attempts attach — if browser is open, binds to it; otherwise opens a new one.
   * Safe to call multiple times (backend handles idempotent attach-or-open).
   */
  const triggerSubdealerDmsWarm = useCallback(() => {
    const base = (dmsUrl || "").trim();
    if (!base) return;
    void warmDmsBrowserLocal({ dms_base_url: base }).catch(() => {
      // Non-blocking; errors logged server-side.
    });
  }, [dmsUrl]);

  const selectedProcessedRow = useMemo(
    () => processedRows.find((r) => r.challan_batch_id === selectedProcessedBatchId) ?? null,
    [processedRows, selectedProcessedBatchId],
  );

  const selectedBatchVehicleLines = useMemo(
    () => sortedDetailLinesForBatch(selectedProcessedRow),
    [selectedProcessedRow],
  );

  const showSummaryBar =
    Boolean(challanNo || challanDateRaw || challanDateIso) || vehicleCount > 0;

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE) || 1);
  const safePage = Math.min(page, totalPages - 1);
  const pageStart = safePage * PAGE_SIZE;
  const pageSlice = rows.slice(pageStart, pageStart + PAGE_SIZE);

  useEffect(() => {
    const tp = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    setPage((p) => Math.min(p, tp - 1));
  }, [rows.length]);

  useEffect(() => {
    let cancelled = false;
    setSubdealersLoading(true);
    setSubdealersError(null);
    listDealersByParent(dealerId)
      .then((rows) => {
        if (!cancelled) {
          setSubdealerOptions(rows);
          setSelectedToDealerId((prev) => {
            if (prev !== null && rows.some((r) => r.dealer_id === prev)) return prev;
            return null;
          });
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setSubdealersError(err instanceof Error ? err.message : String(err));
          setSubdealerOptions([]);
        }
      })
      .finally(() => {
        if (!cancelled) setSubdealersLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [dealerId]);

  const updateRowField = useCallback((globalIndex: number, field: keyof ChallanRow, value: string) => {
    if (field === "id") return;
    setRows((prev) => {
      const next = [...prev];
      while (next.length <= globalIndex) {
        next.push(newEmptyRow());
      }
      const cur = next[globalIndex];
      next[globalIndex] = { ...cur, [field]: value };
      return ensureTrailingBlankRow(dedupeRowsByVehicleIdentity(next));
    });
  }, []);

  const removeRowAt = useCallback((globalIndex: number) => {
    setRows((prev) => {
      if (globalIndex < 0 || globalIndex >= prev.length) return prev;
      if (prev.length <= 1) {
        return [newEmptyRow()];
      }
      const next = prev.filter((_, i) => i !== globalIndex);
      return ensureTrailingBlankRow(
        dedupeRowsByVehicleIdentity(next.length ? next : [newEmptyRow()]),
      );
    });
  }, []);

  const loadProcessed = useCallback(async () => {
    setProcessedLoading(true);
    setProcessedError(null);
    try {
      const rows = await listRecentChallanStaging(dealerId, CHALLAN_STAGING_RECENT_DAYS, {
        challanBookNum: processedChallanSearchApplied.trim() || null,
      });
      setProcessedRows(rows);
    } catch (err) {
      setProcessedError(err instanceof Error ? err.message : String(err));
    } finally {
      setProcessedLoading(false);
      onChallanCountsRefresh();
    }
  }, [dealerId, processedChallanSearchApplied, onChallanCountsRefresh]);

  useEffect(() => {
    setFailedLineDrafts({});
  }, [selectedProcessedBatchId]);

  const hasUnsavedFailedLineEdits = useMemo(() => {
    for (const fl of selectedBatchVehicleLines) {
      if ((fl.status || "").trim().toLowerCase() !== "failed") continue;
      const d = failedLineDrafts[fl.challan_detail_staging_id];
      if (!d) continue;
      const sc = (fl.raw_chassis || "").trim();
      const se = (fl.raw_engine || "").trim();
      if (d.raw_chassis.trim() !== sc || d.raw_engine.trim() !== se) {
        return true;
      }
    }
    return false;
  }, [selectedBatchVehicleLines, failedLineDrafts]);

  const onFailedLineFieldChange = useCallback(
    (fl: ChallanFailedDetailLine, field: "raw_chassis" | "raw_engine", value: string) => {
      const up = value.toUpperCase();
      setFailedLineDrafts((prev) => {
        const id = fl.challan_detail_staging_id;
        const cur = prev[id] ?? {
          raw_chassis: (fl.raw_chassis || "").trim(),
          raw_engine: (fl.raw_engine || "").trim(),
        };
        return { ...prev, [id]: { ...cur, [field]: up } };
      });
    },
    [],
  );

  /**
   * Save pending failed-line edits for the given batch (or the currently selected batch if omitted).
   * Retry passes `batchIdForSave` because the Retry button uses `stopPropagation` and does not select the row first.
   */
  const onSaveAllFailedLineEdits = useCallback(
    async (batchIdForSave?: string | null): Promise<boolean> => {
      const bid = (batchIdForSave ?? selectedProcessedBatchId)?.trim() || null;
      const row = bid !== null ? processedRows.find((r) => r.challan_batch_id === bid) ?? null : null;
      if (!row) return true;

      const lines = sortedDetailLinesForBatch(row);
      const toSave: { id: number; body: { raw_chassis: string; raw_engine: string } }[] = [];
      for (const fl of lines) {
        if ((fl.status || "").trim().toLowerCase() !== "failed") continue;
        const d = failedLineDrafts[fl.challan_detail_staging_id];
        if (!d) continue;
        const sc = (fl.raw_chassis || "").trim();
        const se = (fl.raw_engine || "").trim();
        if (d.raw_chassis.trim() === sc && d.raw_engine.trim() === se) continue;
        toSave.push({
          id: fl.challan_detail_staging_id,
          body: { raw_chassis: d.raw_chassis.trim(), raw_engine: d.raw_engine.trim() },
        });
      }
      if (toSave.length === 0) return true;
      setSavingFailedLineEdits(true);
      setProcessedError(null);
      try {
        for (const { id, body } of toSave) {
          await patchChallanStagingFailedLine(id, body);
        }
        setFailedLineDrafts({});
        await loadProcessed();
        onChallanCountsRefresh();
        return true;
      } catch (err) {
        setProcessedError(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        setSavingFailedLineEdits(false);
      }
    },
    [
      selectedProcessedBatchId,
      processedRows,
      failedLineDrafts,
      loadProcessed,
      onChallanCountsRefresh,
    ],
  );

  const applyProcessedChallanSearch = useCallback(() => {
    setProcessedChallanSearchApplied(processedChallanSearchDraft.trim());
  }, [processedChallanSearchDraft]);

  useEffect(() => {
    if (challanSubTab === "processed") {
      triggerSubdealerDmsWarm();
      void loadProcessed();
    } else {
      setProcessedRows([]);
      setProcessedError(null);
      setProcessedLoading(false);
      setSelectedProcessedBatchId(null);
      setProcessedChallanSearchDraft("");
      setProcessedChallanSearchApplied("");
    }
  }, [challanSubTab, loadProcessed, triggerSubdealerDmsWarm]);

  useEffect(() => {
    if (challanSubTab !== "processed") return;
    if (selectedProcessedBatchId === null) return;
    const exists = processedRows.some((r) => r.challan_batch_id === selectedProcessedBatchId);
    if (!exists) setSelectedProcessedBatchId(null);
  }, [challanSubTab, processedRows, selectedProcessedBatchId]);

  /** Re-run full batch (re-queues all Failed lines server-side, then prepare_vehicle + order). */
  const onRetryFailedBatch = async (challanBatchId: string) => {
    triggerSubdealerDmsWarm();
    setSelectedProcessedBatchId(challanBatchId);
    const saveOk = await onSaveAllFailedLineEdits(challanBatchId);
    if (!saveOk) return;

    setRetryingProcessBatchId(challanBatchId);
    setProcessedError(null);
    try {
      const pr = await processChallanBatchLocal(challanBatchId, {
        dms_base_url: dmsUrl || null,
        dealer_id: dealerId,
      });
      if (pr.error || !pr.ok) {
        setProcessedError(pr.error || "Batch retry failed.");
      }
      await loadProcessed();
      onChallanCountsRefresh();
    } catch (err) {
      setProcessedError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetryingProcessBatchId(null);
    }
  };

  const onRetryOrderOnly = async (challanBatchId: string) => {
    triggerSubdealerDmsWarm();
    setSelectedProcessedBatchId(challanBatchId);
    setRetryingOrderBatchId(challanBatchId);
    setProcessedError(null);
    try {
      const pr = await retryChallanOrderOnlyLocal(challanBatchId, {
        dms_base_url: dmsUrl || null,
        dealer_id: dealerId,
      });
      if (pr.error || !pr.ok) {
        setProcessedError(pr.error || "Retry order failed.");
      }
      await loadProcessed();
      onChallanCountsRefresh();
    } catch (err) {
      setProcessedError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetryingOrderBatchId(null);
    }
  };

  const onFileSelected = async (e: ChangeEvent<HTMLInputElement>) => {
    const input = e.target;
    // Snapshot files before clearing `value` — `input.files` is a live `FileList` and clearing
    // the value empties it, so we must not read the list after `input.value = ""`.
    const files = input.files && input.files.length > 0 ? Array.from(input.files) : [];
    input.value = "";
    if (files.length === 0) return;
    setLoading(true);
    setParseOcrProgress(null);
    setError(null);
    setWarnings([]);
    try {
      const res = await parseSubdealerChallanScans(
        files,
        files.length > 1 ? (c, t) => setParseOcrProgress({ current: c, total: t }) : undefined
      );
      setChallanNo(res.challan_no);
      setChallanDateRaw(res.challan_date_raw);
      setChallanDateIso(res.challan_date_iso);
      setChallanDdmmyyyy(res.challan_ddmmyyyy ?? null);
      setWarnings(res.warnings || []);
      const mapped: ChallanRow[] = (res.lines || []).map((line: SubdealerChallanLine) => ({
        id:
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : `r-${Date.now()}-${Math.random()}`,
        engineNo: (line.engine_no || "").toUpperCase(),
        chassisNo: (line.chassis_no || "").toUpperCase(),
        status: line.status || "queued",
      }));
      setRows(
        ensureTrailingBlankRow(dedupeRowsByVehicleIdentity(mapped.length ? mapped : [newEmptyRow()])),
      );
      setPage(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setParseOcrProgress(null);
    }
  };

  const onCreateChallans = async () => {
    triggerSubdealerDmsWarm();
    if (selectedToDealerId === null) {
      setError("Select a subdealer (To Dealer).");
      return;
    }
    const toId = selectedToDealerId;
    const dataRows = rows.filter((r) => rowHasVehicleData(r));
    if (dataRows.length === 0) {
      setError("Add at least one engine/chassis line.");
      return;
    }
    setError(null);
    setProcessingChallan(true);
    try {
      const lines = dataRows.map((r) => ({
        raw_engine: r.engineNo.trim(),
        raw_chassis: r.chassisNo.trim(),
      }));
      const st = await createChallanStaging({
        from_dealer_id: dealerId,
        to_dealer_id: toId,
        challan_date: challanDdmmyyyy || challanDateRaw || null,
        challan_book_num: challanNo,
        lines,
      });
      const stagingNotes: string[] = [];
      const ex = st.dropped_existing_same_book_date ?? 0;
      const dup = st.dropped_duplicate_in_request ?? 0;
      if (ex > 0) {
        stagingNotes.push(
          `Skipped ${ex} vehicle line(s) already listed for this challan book number and date (any prior batch, including queued or failed).`,
        );
      }
      if (dup > 0) {
        stagingNotes.push(`Removed ${dup} duplicate engine/chassis row(s) in this list (first occurrence kept).`);
      }
      if (stagingNotes.length > 0) {
        setWarnings((prev) => [...stagingNotes, ...prev]);
      }
      setRows((prev) =>
        prev.map((r) => (rowHasVehicleData(r) ? { ...r, status: "Queued" } : r)),
      );
      const pr = await processChallanBatchLocal(st.challan_batch_id, {
        dms_base_url: dmsUrl || null,
        dealer_id: dealerId,
      });
      if (pr.error || !pr.ok) {
        setError(pr.error || "Challan processing failed.");
        return;
      }
      setRows((prev) =>
        prev.map((r) => (rowHasVehicleData(r) ? { ...r, status: "Committed" } : r)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setProcessingChallan(false);
      onChallanCountsRefresh();
    }
  };

  return (
    <div className="subdealer-challan">
      <nav className="challans-subtabs" role="tablist" aria-label="Subdealer Challans">
        <button
          type="button"
          role="tab"
          id="challans-tab-new"
          aria-controls="challans-panel-new"
          aria-selected={challanSubTab === "new"}
          className={`challans-subtab ${challanSubTab === "new" ? "active" : ""}`}
          onClick={() => setChallanSubTab("new")}
        >
          New Challan
        </button>
        <button
          type="button"
          role="tab"
          id="challans-tab-processed"
          aria-controls="challans-panel-processed"
          aria-selected={challanSubTab === "processed"}
          className={`challans-subtab ${challanSubTab === "processed" ? "active" : ""}`}
          onClick={() => setChallanSubTab("processed")}
        >
          Processed
          {challanFailedCount > 0 ? (
            <span className="app-tab-badge app-tab-badge--danger">
              {" "}
              ({challanFailedCount})
            </span>
          ) : null}
        </button>
      </nav>

      {challanSubTab === "new" ? (
      <div
        id="challans-panel-new"
        role="tabpanel"
        aria-labelledby="challans-tab-new"
        className="challans-new-panel"
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/jpeg,image/jpg,image/png,image/webp,application/pdf,.pdf,.jpg,.jpeg,.png,.webp"
          className="subdealer-challan-file-input"
          aria-hidden
          tabIndex={-1}
          onChange={onFileSelected}
        />

        <div className="subdealer-challan-top-grid">
        <label htmlFor="sdc-dealer" className="subdealer-challan-label subdealer-challan-l-dealer">
          To Dealer (Subdealer):
        </label>
        <div className="subdealer-challan-dealer-cell">
          <select
            id="sdc-dealer"
            className="subdealer-challan-select"
            value={selectedToDealerId === null ? "" : String(selectedToDealerId)}
            onChange={(e) => {
              const v = e.target.value;
              setSelectedToDealerId(v === "" ? null : parseInt(v, 10));
            }}
            disabled={subdealersLoading || Boolean(subdealersError)}
            aria-busy={subdealersLoading}
            aria-label="Subdealer receiving stock"
          >
            <option value="">
              {subdealersLoading ? "Loading subdealers…" : "Select subdealer…"}
            </option>
            {subdealerOptions.map((d) => (
              <option key={d.dealer_id} value={d.dealer_id}>
                {d.dealer_name}
              </option>
            ))}
          </select>
          {subdealersError ? (
            <span className="subdealer-challan-subdealer-err" role="alert">
              {subdealersError}
            </span>
          ) : null}
          {!subdealersLoading && !subdealersError && subdealerOptions.length === 0 ? (
            <span className="subdealer-challan-subdealer-hint">
              No subdealers for this dealer (set <code>parent_id</code> on child rows in{" "}
              <code>dealer_ref</code>).
            </span>
          ) : null}
        </div>
        <div className="subdealer-challan-scan-btns" role="group" aria-label="Scan sources">
          <button
            type="button"
            className="app-button subdealer-challan-inline-btn"
            disabled={loading}
            onClick={() => {
              triggerSubdealerDmsWarm();
              fileInputRef.current?.click();
            }}
            title="PDF or JPEG/PNG, one or more files (e.g. multiple challan pages)"
          >
            {loading
              ? parseOcrProgress && parseOcrProgress.total > 1
                ? `OCR page ${parseOcrProgress.current} of ${parseOcrProgress.total}…`
                : "Processing…"
              : "Upload scan(s)"}
          </button>
          <button
            type="button"
            className="app-button subdealer-challan-inline-btn"
            disabled
            onClick={() => {
              triggerSubdealerDmsWarm();
            }}
          >
            From Scanner
          </button>
        </div>
        <button
          type="button"
          className="app-button app-button--primary subdealer-challan-add-btn"
          disabled={
            loading ||
            processingChallan ||
            subdealersLoading ||
            Boolean(subdealersError) ||
            selectedToDealerId === null
          }
          onClick={() => void onCreateChallans()}
        >
          {processingChallan ? "Creating Challans…" : "Create Challans"}
        </button>
      </div>

      {showSummaryBar && (
        <div className="subdealer-challan-extract-banner" aria-live="polite">
          <span className="subdealer-challan-extract-item">
            <span className="subdealer-challan-extract-label">Date</span>
            {challanDateRaw || challanDateIso || "—"}
          </span>
          <span className="subdealer-challan-extract-item">
            <span className="subdealer-challan-extract-label">Challan no.</span>
            {challanNo && challanNo !== "" ? challanNo : "—"}
          </span>
          <span className="subdealer-challan-extract-item">
            <span className="subdealer-challan-extract-label">Vehicle count</span>
            {vehicleCount}
          </span>
        </div>
      )}

      {error && (
        <div className="subdealer-challan-error" role="alert">
          <p className="subdealer-challan-error-text">{error}</p>
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="subdealer-challan-warnings">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {rows.length > PAGE_SIZE && (
        <div className="subdealer-challan-pagination" role="navigation" aria-label="Challan rows pages">
          <button
            type="button"
            className="app-button subdealer-challan-page-btn"
            disabled={safePage <= 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span className="subdealer-challan-page-info">
            Page {safePage + 1} of {totalPages}
          </span>
          <button
            type="button"
            className="app-button subdealer-challan-page-btn"
            disabled={safePage >= totalPages - 1}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
          >
            Next
          </button>
        </div>
      )}

      <div className="subdealer-challan-tables-scroll" role="region" aria-label="Challan line items">
        <div className="subdealer-challan-tables" role="group" aria-label="Chassis and engine numbers">
          {Array.from({ length: TABLE_COUNT }, (_, tableIdx) => {
            const offset = tableIdx * ROWS_PER_TABLE;
            return (
              <div key={tableIdx} className="subdealer-challan-table-wrap">
                <table className="subdealer-challan-table">
                  <colgroup>
                    <col className="subdealer-challan-col-sno" />
                    <col className="subdealer-challan-col-chassis" />
                    <col className="subdealer-challan-col-engine" />
                    <col className="subdealer-challan-col-status" />
                    <col className="subdealer-challan-col-delete" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col" className="subdealer-challan-th-sno">
                        S.No.
                      </th>
                      <th scope="col">Chassis No.</th>
                      <th scope="col">Engine No.</th>
                      <th scope="col">Status</th>
                      <th scope="col" className="subdealer-challan-th-delete" aria-label="Remove row" />
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from({ length: ROWS_PER_TABLE }, (_, r) => {
                      const slot = offset + r;
                      const globalIdx = pageStart + slot;
                      const row = pageSlice[slot];
                      const sno = globalIdx + 1;
                      const rowExists = globalIdx < rows.length;
                      const rowKey = row?.id ?? `ghost-${safePage}-${globalIdx}`;

                      return (
                        <tr key={rowKey}>
                          <td className="subdealer-challan-sno">{sno}.</td>
                          <td className="subdealer-challan-chassis-cell">
                            <input
                              type="text"
                              className="subdealer-challan-cell-input"
                              value={row?.chassisNo ?? ""}
                              onChange={(e) =>
                                updateRowField(globalIdx, "chassisNo", e.target.value.toUpperCase())
                              }
                              maxLength={32}
                              inputMode="text"
                              autoCapitalize="characters"
                              spellCheck={false}
                              aria-label={`Chassis No. row ${sno}`}
                            />
                          </td>
                          <td className="subdealer-challan-engine-cell">
                            <input
                              type="text"
                              className="subdealer-challan-cell-input"
                              value={row?.engineNo ?? ""}
                              onChange={(e) =>
                                updateRowField(globalIdx, "engineNo", e.target.value.toUpperCase())
                              }
                              maxLength={32}
                              aria-label={`Engine No. row ${sno}`}
                            />
                          </td>
                          <td className="subdealer-challan-status-cell">
                            <span
                              className="subdealer-challan-status-readonly"
                              aria-label={
                                rowHasVehicleData(row)
                                  ? `Status row ${sno}, ${STATUS_QUEUED_LABEL}`
                                  : `Status row ${sno}, empty`
                              }
                            >
                              {statusCellLabel(row)}
                            </span>
                          </td>
                          <td className="subdealer-challan-delete-cell">
                            {rowExists ? (
                              <button
                                type="button"
                                className="subdealer-challan-row-delete"
                                aria-label={`Remove row ${sno}`}
                                onClick={() => removeRowAt(globalIdx)}
                              >
                                ×
                              </button>
                            ) : (
                              <span className="subdealer-challan-delete-placeholder" aria-hidden />
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      </div>
      </div>
      ) : null}

      {challanSubTab === "processed" ? (
      <div
        id="challans-panel-processed"
        role="tabpanel"
        aria-labelledby="challans-tab-processed"
        className="challans-processed-panel"
      >
        {processedError && (
          <div className="subdealer-challan-error" role="alert">
            {processedError}
          </div>
        )}
        <div className="challans-processed-search" role="search">
          <label className="challans-processed-search-label" htmlFor="challans-processed-challan-no">
            Challan No.
          </label>
          <input
            id="challans-processed-challan-no"
            type="search"
            className="challans-processed-search-input"
            placeholder="Search by book number…"
            autoComplete="off"
            value={processedChallanSearchDraft}
            onChange={(e) => setProcessedChallanSearchDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyProcessedChallanSearch();
              }
            }}
          />
          <button
            type="button"
            className="app-button app-button--small challans-processed-search-btn"
            onClick={() => applyProcessedChallanSearch()}
          >
            Search
          </button>
          <button
            type="button"
            className="app-button app-button--small challans-processed-search-btn"
            disabled={processedLoading}
            aria-busy={processedLoading}
            title="Reload processed batches and prepared counts"
            onClick={() => void loadProcessed()}
          >
            {processedLoading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        {processedLoading ? (
          <p className="app-table-empty challans-processed-loading-msg">
            Loading…
          </p>
        ) : processedRows.length === 0 ? (
          <p className="app-table-empty challans-processed-loading-msg">
            {processedChallanSearchApplied.trim()
              ? "No challan found for this Challan No."
              : "No matching batches in the last 15 days (failed vehicles or failed invoice)."}
          </p>
        ) : (
          <div className="challans-processed-split">
            <div className="challans-processed-master">
              <div
                className="challans-processed-table-wrap"
                role="region"
                aria-label="Processed challan batches"
              >
                <table className="app-table">
                  <thead>
                    <tr>
                      <th scope="col">From dealer</th>
                      <th scope="col">To dealer</th>
                      <th scope="col">Challan date</th>
                      <th scope="col">Challan number</th>
                      <th scope="col" title="Vehicles prepared vs total in this batch">
                        Vehicles Prepared
                      </th>
                      <th scope="col">Invoice</th>
                      <th scope="col">Latest run</th>
                      <th scope="col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {processedRows.map((r) => {
                      const bid = r.challan_batch_id;
                      const failedN = r.failed_line_count ?? 0;
                      const orderRetry = showRetryOrderOnly(r);
                      const fullRetry = showRetryFullBatch(r);
                      const sel = selectedProcessedBatchId === bid;
                      return (
                        <tr
                          key={bid}
                          className={
                            "challans-proc-master-row" + (sel ? " challans-proc-master-row--selected" : "")
                          }
                          aria-selected={sel}
                          tabIndex={0}
                          onClick={() => setSelectedProcessedBatchId(bid)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setSelectedProcessedBatchId(bid);
                            }
                          }}
                        >
                          <td>{formatDealerDisplay(r.from_dealer_name, r.from_dealer_id)}</td>
                          <td>{formatDealerDisplay(r.to_dealer_name, r.to_dealer_id)}</td>
                          <td>{formatChallanDateDisplay(r.challan_date)}</td>
                          <td>{(r.challan_book_num || "").trim() || "—"}</td>
                          <td>{formatPreparedOverTotal(r)}</td>
                          <td>
                            {(r.invoice_status || "—").trim()}
                            {r.invoice_complete ? " ✓" : ""}
                          </td>
                          <td>{formatLatestRunDisplay(r.last_run_at)}</td>
                          <td>
                            <div className="challans-proc-actions-cell">
                              {orderRetry ? (
                                <button
                                  type="button"
                                  className="app-button app-button--primary challans-proc-retry-btn"
                                  disabled={
                                    retryingOrderBatchId !== null || retryingProcessBatchId !== null
                                  }
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void onRetryOrderOnly(bid);
                                  }}
                                >
                                  {retryingOrderBatchId === bid ? "Retrying…" : "Retry"}
                                </button>
                              ) : fullRetry ? (
                                <>
                                  {failedN > 0 ? (
                                    <span
                                      className="challans-proc-failed-badge"
                                      title="Details in Failed vehicles below"
                                    >
                                      {failedN} failed
                                    </span>
                                  ) : null}
                                  <button
                                    type="button"
                                    className="app-button app-button--primary challans-proc-retry-btn"
                                    disabled={
                                      retryingProcessBatchId !== null || retryingOrderBatchId !== null
                                    }
                                    title={
                                      failedN > 0
                                        ? "Re-run DMS for all failed vehicles (Find→Vehicles, prepare, then order)"
                                        : "Continue DMS run for vehicles not yet prepared (Queued), then order"
                                    }
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      void onRetryFailedBatch(bid);
                                    }}
                                  >
                                    {retryingProcessBatchId === bid ? "Retrying…" : "Retry"}
                                  </button>
                                </>
                              ) : (
                                "—"
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
            <div
              className="challans-processed-failed-section"
              role="region"
              aria-labelledby="challans-processed-failed-heading"
            >
              <div className="challans-processed-failed-header-row">
                <h3
                  className="challans-processed-failed-heading"
                  id="challans-processed-failed-heading"
                >
                  Vehicles in this batch
                </h3>
                <button
                  type="button"
                  className="app-button app-button--small challans-proc-save-all-btn"
                  disabled={
                    !hasUnsavedFailedLineEdits ||
                    savingFailedLineEdits ||
                    selectedProcessedRow === null ||
                    retryingProcessBatchId !== null ||
                    retryingOrderBatchId !== null
                  }
                  onClick={() => void onSaveAllFailedLineEdits()}
                >
                  {savingFailedLineEdits ? "Saving…" : "Save all changes"}
                </button>
              </div>
              <div className="challans-processed-failed-table-wrap">
                {selectedProcessedRow === null ? (
                  <p className="app-table-empty challans-processed-failed-placeholder">
                    Select a challan batch in the table above.
                  </p>
                ) : selectedBatchVehicleLines.length === 0 ? (
                  <p className="app-table-empty challans-processed-failed-placeholder">
                    No vehicle lines in this batch.
                  </p>
                ) : (
                  <table className="app-table challans-processed-failed-table">
                    <thead>
                      <tr>
                        <th scope="col" title="Line (staging id)">
                          Line
                        </th>
                        <th scope="col" title="Chassis number">
                          Chassis
                        </th>
                        <th scope="col" title="Engine number">
                          Engine
                        </th>
                        <th scope="col">Status</th>
                        <th scope="col">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedBatchVehicleLines.map((fl) => {
                        const st = (fl.status || "").trim().toLowerCase();
                        const isFailed = st === "failed";
                        const id = fl.challan_detail_staging_id;
                        const d = failedLineDrafts[id];
                        const chVal = d ? d.raw_chassis : (fl.raw_chassis || "").trim();
                        const enVal = d ? d.raw_engine : (fl.raw_engine || "").trim();
                        return (
                          <tr
                            key={id}
                            className={isFailed ? "challans-proc-detail-row--failed" : undefined}
                          >
                            <td title={id != null ? String(id) : undefined}>{id}</td>
                            <td
                              className="challans-proc-vehicle-cell"
                              title={!isFailed && chVal ? chVal : undefined}
                            >
                              {isFailed ? (
                                <input
                                  type="text"
                                  className="subdealer-challan-cell-input challans-proc-vehicle-input"
                                  value={chVal}
                                  onChange={(e) =>
                                    onFailedLineFieldChange(fl, "raw_chassis", e.target.value)
                                  }
                                  maxLength={32}
                                  spellCheck={false}
                                  autoCapitalize="characters"
                                  aria-label={`Chassis, line ${id}`}
                                />
                              ) : (
                                (fl.raw_chassis || "").trim() || "—"
                              )}
                            </td>
                            <td
                              className="challans-proc-vehicle-cell"
                              title={!isFailed && enVal ? enVal : undefined}
                            >
                              {isFailed ? (
                                <input
                                  type="text"
                                  className="subdealer-challan-cell-input challans-proc-vehicle-input"
                                  value={enVal}
                                  onChange={(e) =>
                                    onFailedLineFieldChange(fl, "raw_engine", e.target.value)
                                  }
                                  maxLength={32}
                                  spellCheck={false}
                                  aria-label={`Engine, line ${id}`}
                                />
                              ) : (
                                (fl.raw_engine || "").trim() || "—"
                              )}
                            </td>
                            <td>{(fl.status || "").trim() || "—"}</td>
                            <td className={isFailed ? "challans-proc-err" : undefined}>
                              {fl.last_error?.trim() || "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      ) : null}
    </div>
  );
}
