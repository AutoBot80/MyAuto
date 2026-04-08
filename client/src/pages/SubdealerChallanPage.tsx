import { Fragment, type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiHttpError } from "../api/client";
import { listDealersByParent, type DealerByParentRow } from "../api/dealers";
import {
  CHALLAN_STAGING_RECENT_DAYS,
  createChallanStaging,
  listRecentChallanStaging,
  parseSubdealerChallanScan,
  processChallanBatch,
  retryChallanOrderOnly,
  retryChallanStagingRow,
  type ChallanMasterProcessedRow,
  type SubdealerChallanLine,
} from "../api/subdealerChallan";

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

function formatStagingCreatedAt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

function shortBatchId(batchId: string): string {
  const s = batchId.replace(/-/g, "");
  if (s.length <= 8) return batchId;
  return `…${s.slice(-8)}`;
}

function formatPreparedOverTotal(r: ChallanMasterProcessedRow): string {
  const p = r.num_vehicles_prepared ?? 0;
  const t = r.num_vehicles ?? 0;
  if (t <= 0) return "—";
  return `${p}/${t}`;
}

function showRetryOrderOnly(r: ChallanMasterProcessedRow): boolean {
  const inv = (r.invoice_status || "").trim().toLowerCase();
  const failed = r.failed_line_count ?? 0;
  return inv === "failed" && failed === 0;
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
  /** Set when POST /staging returns 409 (duplicate book+date); show Processed tab hint. */
  const [duplicateChallanGuide, setDuplicateChallanGuide] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [processedRows, setProcessedRows] = useState<ChallanMasterProcessedRow[]>([]);
  const [processedLoading, setProcessedLoading] = useState(false);
  const [processedError, setProcessedError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const [retryingOrderBatchId, setRetryingOrderBatchId] = useState<string | null>(null);
  const [expandedBatchId, setExpandedBatchId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const vehicleCount = useMemo(() => uniqueVehicleCount(rows), [rows]);

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
      const rows = await listRecentChallanStaging(dealerId, CHALLAN_STAGING_RECENT_DAYS);
      setProcessedRows(rows);
    } catch (err) {
      setProcessedError(err instanceof Error ? err.message : String(err));
    } finally {
      setProcessedLoading(false);
    }
  }, [dealerId]);

  useEffect(() => {
    if (challanSubTab === "processed") {
      void loadProcessed();
    } else {
      setProcessedRows([]);
      setProcessedError(null);
      setProcessedLoading(false);
    }
  }, [challanSubTab, loadProcessed]);

  const onRetryStagingRow = async (challanDetailStagingId: number) => {
    setRetryingId(challanDetailStagingId);
    setProcessedError(null);
    try {
      const pr = await retryChallanStagingRow(challanDetailStagingId, {
        dms_base_url: dmsUrl || null,
        dealer_id: dealerId,
      });
      if (pr.error || pr.ok === false) {
        setProcessedError(pr.error || "Retry failed.");
      }
      await loadProcessed();
      onChallanCountsRefresh();
    } catch (err) {
      setProcessedError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetryingId(null);
    }
  };

  const onRetryOrderOnly = async (challanBatchId: string) => {
    setRetryingOrderBatchId(challanBatchId);
    setProcessedError(null);
    try {
      const pr = await retryChallanOrderOnly(challanBatchId, {
        dms_base_url: dmsUrl || null,
        dealer_id: dealerId,
      });
      if (pr.error || pr.ok === false) {
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
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setLoading(true);
    setError(null);
    setDuplicateChallanGuide(false);
    setWarnings([]);
    try {
      const res = await parseSubdealerChallanScan(file);
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
    }
  };

  const onCreateChallans = async () => {
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
    setDuplicateChallanGuide(false);
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
      setRows((prev) =>
        prev.map((r) => (rowHasVehicleData(r) ? { ...r, status: "Queued" } : r)),
      );
      const pr = await processChallanBatch(st.challan_batch_id, {
        dms_base_url: dmsUrl || null,
        dealer_id: dealerId,
      });
      if (pr.error || pr.ok === false) {
        setError(pr.error || "Challan processing failed.");
        return;
      }
      setRows((prev) =>
        prev.map((r) => (rowHasVehicleData(r) ? { ...r, status: "Committed" } : r)),
      );
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 409) {
        setDuplicateChallanGuide(true);
        setError(err.message);
      } else {
        setDuplicateChallanGuide(false);
        setError(err instanceof Error ? err.message : String(err));
      }
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
          {challanFailedCount > 0 ? ` (${challanFailedCount})` : ""}
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
          accept="image/jpeg,image/png,image/webp,application/pdf"
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
            onClick={() => fileInputRef.current?.click()}
          >
            {loading ? "Processing…" : "Upload Scan"}
          </button>
          <button type="button" className="app-button subdealer-challan-inline-btn" disabled>
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
          {duplicateChallanGuide ? (
            <div className="subdealer-challan-error-actions">
              <button
                type="button"
                className="app-button app-button--primary"
                onClick={() => {
                  setError(null);
                  setDuplicateChallanGuide(false);
                  setChallanSubTab("processed");
                }}
              >
                Open Processed tab
              </button>
            </div>
          ) : null}
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
        <div className="subdealer-challan-tables" role="group" aria-label="Engine and chassis numbers">
          {Array.from({ length: TABLE_COUNT }, (_, tableIdx) => {
            const offset = tableIdx * ROWS_PER_TABLE;
            return (
              <div key={tableIdx} className="subdealer-challan-table-wrap">
                <table className="subdealer-challan-table">
                  <colgroup>
                    <col className="subdealer-challan-col-sno" />
                    <col className="subdealer-challan-col-engine" />
                    <col className="subdealer-challan-col-chassis" />
                    <col className="subdealer-challan-col-status" />
                    <col className="subdealer-challan-col-delete" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col" className="subdealer-challan-th-sno">
                        S.No.
                      </th>
                      <th scope="col">Engine No.</th>
                      <th scope="col">Chassis No.</th>
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
        <div className="challans-processed-table-wrap">
          {processedLoading ? (
            <p className="app-table-empty" style={{ padding: "1rem" }}>
              Loading…
            </p>
          ) : processedRows.length === 0 ? (
            <p className="app-table-empty" style={{ padding: "1rem" }}>
              No challan batches in this period.
            </p>
          ) : (
            <table className="app-table">
              <thead>
                <tr>
                  <th scope="col">Batch</th>
                  <th scope="col">Created</th>
                  <th scope="col">To dealer</th>
                  <th scope="col">Book / date</th>
                  <th scope="col" title="Prepared vs total vehicles in this batch">
                    Prep / total
                  </th>
                  <th scope="col">Invoice</th>
                  <th scope="col" title="Detail lines still in Failed (prepare or inventory)">
                    Failed lines
                  </th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {processedRows.map((r) => {
                  const bid = r.challan_batch_id;
                  const expanded = expandedBatchId === bid;
                  const failedN = r.failed_line_count ?? 0;
                  const canExpand = failedN > 0;
                  const orderRetry = showRetryOrderOnly(r);
                  return (
                    <Fragment key={bid}>
                      <tr>
                        <td title={bid}>{shortBatchId(bid)}</td>
                        <td>{formatStagingCreatedAt(r.created_at)}</td>
                        <td>{r.to_dealer_id}</td>
                        <td>
                          {(r.challan_book_num || "—") + " / " + (r.challan_date || "—")}
                        </td>
                        <td>{formatPreparedOverTotal(r)}</td>
                        <td>
                          {(r.invoice_status || "—").trim()}
                          {r.invoice_complete ? " ✓" : ""}
                        </td>
                        <td>
                          {failedN > 0 ? (
                            <button
                              type="button"
                              className="app-button app-button--small"
                              aria-expanded={expanded}
                              onClick={() => setExpandedBatchId(expanded ? null : bid)}
                            >
                              {failedN} {expanded ? "▾" : "▸"}
                            </button>
                          ) : (
                            "0"
                          )}
                        </td>
                        <td>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
                            {orderRetry ? (
                              <button
                                type="button"
                                className="app-button app-button--primary challans-proc-retry-btn"
                                disabled={retryingOrderBatchId !== null || retryingId !== null}
                                onClick={() => void onRetryOrderOnly(bid)}
                              >
                                {retryingOrderBatchId === bid ? "Retrying order…" : "Retry order"}
                              </button>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                      {canExpand && expanded
                        ? r.failed_lines.map((fl) => (
                            <tr key={`${bid}-${fl.challan_detail_staging_id}`} className="challans-proc-failed-row">
                              <td colSpan={2}>Line {fl.challan_detail_staging_id}</td>
                              <td colSpan={2}>
                                {(fl.raw_engine || "—") + " / " + (fl.raw_chassis || "—")}
                              </td>
                              <td colSpan={1}>{(fl.status || "").trim() || "Failed"}</td>
                              <td colSpan={2} className="challans-proc-err">
                                {fl.last_error || "—"}
                              </td>
                              <td colSpan={1}>
                                <button
                                  type="button"
                                  className="app-button app-button--primary challans-proc-retry-btn"
                                  disabled={retryingId !== null || retryingOrderBatchId !== null}
                                  onClick={() => void onRetryStagingRow(fl.challan_detail_staging_id)}
                                >
                                  {retryingId === fl.challan_detail_staging_id ? "Retrying…" : "Retry line"}
                                </button>
                              </td>
                            </tr>
                          ))
                        : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
      ) : null}
    </div>
  );
}
