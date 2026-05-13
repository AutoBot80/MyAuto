import { Fragment, useCallback, useEffect, useState } from "react";
import {
  getDealerDashboardChallansByIstDay,
  getDealerDashboardChallansFiltered,
  getDealerDashboardSubdealerSalesMatrix,
  getDealerDashboardSummary,
  type DealerDashboardSummary,
  type SubdealerSalesMatrixResponse,
} from "../api/dealerDashboard";
import { listDealersByParent, type DealerByParentRow } from "../api/dealers";
import "./AdminUsagePage.css";
import "./DealerDashboardPage.css";

function dayHeaderLabel(isoDay: string): string {
  const p = isoDay.split("-");
  if (p.length === 3) return `${p[2]}/${p[1]}`;
  return isoDay;
}

/** ``YYYY-MM-DD`` → ``dd/mm/yyyy`` for display subtitles. */
function formatIsoDateShort(iso: string): string {
  const p = iso.trim().slice(0, 10).split("-");
  if (p.length !== 3) return iso;
  const [y, m, d] = p;
  return `${d}/${m}/${y}`;
}

/** ``created_at`` from API (ISO string) → ``dd-mm-yyyy hh:mm`` in Asia/Kolkata (IST). */
function formatChallanCreatedAtIst(value: unknown): string {
  if (value == null) return "—";
  const s = String(value).trim();
  if (!s) return "—";
  const ms = Date.parse(s);
  if (Number.isNaN(ms)) return "—";
  const d = new Date(ms);
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(d);
  const get = (type: Intl.DateTimeFormatPartTypes): string => parts.find((p) => p.type === type)?.value ?? "";
  const y = get("year");
  const m = get("month");
  const day = get("day");
  const hour = get("hour");
  const minute = get("minute");
  if (!y || !m || !day) return "—";
  return `${day}-${m}-${y} ${hour || "00"}:${minute || "00"}`;
}

type MergedMetricRow = {
  key: string;
  label: string;
  counts: number[];
  interactive?: "challan";
};

type FilterDays = 7 | 15 | 30;

type ChallanDetailPanel =
  | {
      mode: "filtered";
      days: number;
      ist_start: string;
      ist_end: string;
      dealer_to_id: number | null;
      rows: Record<string, unknown>[];
    }
  | { mode: "matrix-day"; ist_date: string; rows: Record<string, unknown>[] };

function MergedBcdMetricsTable({
  days,
  rows,
  subdealerExpanded,
  subdealerLoading,
  subdealerChildRows,
  onSubdealerLabelClick,
  onChallanCellClick,
}: {
  days: string[];
  rows: MergedMetricRow[];
  subdealerExpanded: boolean;
  subdealerLoading: boolean;
  subdealerChildRows: SubdealerSalesMatrixResponse["rows"] | null;
  onSubdealerLabelClick: () => void;
  onChallanCellClick: (istDayIso: string) => void;
}) {
  const wrapClass = [
    "app-table-wrap",
    "admin-usage-matrix-wrap",
    "dealer-dash-matrix-wrap--compact-cols",
  ].join(" ");

  const colCount = 1 + days.length;

  return (
    <div className={wrapClass}>
      <table
        className="app-table admin-usage-matrix-table"
        aria-label="Counter sales, subdealer sales, and challan counts by IST day"
      >
        <thead>
          <tr>
            <th scope="col" className="admin-usage-matrix-dealer-col">
              Metric
            </th>
            {days.map((d) => (
              <th key={d} scope="col" className="admin-usage-matrix-day" title={`${d} (IST)`}>
                {dayHeaderLabel(d)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const cells = row.counts.map((c, i) => {
              const d = days[i] ?? "";
              if (row.interactive === "challan") {
                return (
                  <td key={i} className="admin-usage-matrix-num">
                    <button type="button" className="dealer-dash-matrix-hit" onClick={() => onChallanCellClick(d)}>
                      {c}
                    </button>
                  </td>
                );
              }
              return (
                <td key={i} className="admin-usage-matrix-num">
                  {c}
                </td>
              );
            });

            const mainTr = (
              <tr key={`${row.key}-main`}>
                <th scope="row" className="admin-usage-matrix-dealer">
                  {row.key === "subdealer-sales" ? (
                    <button
                      type="button"
                      className="dealer-dash-matrix-rowhit"
                      onClick={onSubdealerLabelClick}
                      aria-expanded={subdealerExpanded}
                    >
                      Total Subdealer Sales
                    </button>
                  ) : (
                    row.label
                  )}
                </th>
                {cells}
              </tr>
            );

            return (
              <Fragment key={row.key}>
                {mainTr}
                {row.key === "subdealer-sales" && subdealerExpanded ? (
                  subdealerLoading ? (
                    <tr key={`${row.key}-loading`} className="dealer-dash-matrix-sub-row">
                      <td colSpan={colCount} className="dealer-dash-matrix-sub-msg">
                        Loading…
                      </td>
                    </tr>
                  ) : subdealerChildRows && subdealerChildRows.length > 0 ? (
                    subdealerChildRows.map((r) => (
                      <tr key={`subdealer-${r.dealer_id}`} className="dealer-dash-matrix-sub-row">
                        <th scope="row" className="admin-usage-matrix-dealer dealer-dash-matrix-sub-name">
                          {r.dealer_name || `Dealer ${r.dealer_id}`}
                          <span className="dealer-dash-sub-id"> ({r.dealer_id})</span>
                        </th>
                        {r.counts.map((c, i) => (
                          <td key={i} className="admin-usage-matrix-num">
                            {c}
                          </td>
                        ))}
                      </tr>
                    ))
                  ) : (
                    <tr key={`${row.key}-empty`} className="dealer-dash-matrix-sub-row">
                      <td colSpan={colCount} className="dealer-dash-matrix-sub-msg">
                        No subdealer sales in this period (no subdealers with sales in the last 7 IST days).
                      </td>
                    </tr>
                  )
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ChallanDetailTable({
  rows,
  ariaLabel,
  emptyMessage,
}: {
  rows: Record<string, unknown>[];
  ariaLabel: string;
  emptyMessage?: string;
}) {
  if (rows.length === 0) {
    return <p className="dealer-dash-empty">{emptyMessage ?? "No challan headers for this day."}</p>;
  }
  return (
    <div className="app-table-wrap">
      <table className="app-table dealer-dash-challan-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            <th scope="col">Book No.</th>
            <th scope="col">To dealer</th>
            <th scope="col">Vehicles</th>
            <th scope="col">Order#</th>
            <th scope="col">Invoice#</th>
            <th scope="col">Create date/time</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, idx) => (
            <tr
              key={`${idx}-${String(r.challan_book_num ?? "")}-${String(r.created_at ?? "")}-${String(r.dealer_to ?? "")}`}
            >
              <td className="dealer-dash-nowrap">{r.challan_book_num != null && String(r.challan_book_num).trim() !== "" ? String(r.challan_book_num) : "—"}</td>
              <td>
                {String(r.to_dealer_name ?? "—")} ({String(r.dealer_to ?? "—")})
              </td>
              <td>{String(r.num_vehicles ?? "—")}</td>
              <td>{String(r.order_number ?? "—")}</td>
              <td>{String(r.invoice_number ?? "—")}</td>
              <td className="dealer-dash-nowrap">{formatChallanCreatedAtIst(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type DealerDashboardPageProps = {
  dealerId: number;
};

export function DealerDashboardPage({ dealerId }: DealerDashboardPageProps) {
  const [summary, setSummary] = useState<DealerDashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [subdealerExpanded, setSubdealerExpanded] = useState(false);
  const [subdealerLoading, setSubdealerLoading] = useState(false);
  const [subMatrix, setSubMatrix] = useState<SubdealerSalesMatrixResponse | null>(null);
  const [subMatrixErr, setSubMatrixErr] = useState<string | null>(null);
  const [subdealers, setSubdealers] = useState<DealerByParentRow[]>([]);
  const [filterDays, setFilterDays] = useState<FilterDays>(7);
  const [filterSubdealerTo, setFilterSubdealerTo] = useState<number | null>(null);
  const [matrixDayIst, setMatrixDayIst] = useState<string | null>(null);
  const [challanPanel, setChallanPanel] = useState<ChallanDetailPanel | null>(null);
  const [challanPanelLoading, setChallanPanelLoading] = useState(false);
  const [challanPanelErr, setChallanPanelErr] = useState<string | null>(null);

  const loadSummary = useCallback(() => {
    setError(null);
    getDealerDashboardSummary(dealerId)
      .then(setSummary)
      .catch((e: unknown) => {
        setSummary(null);
        setError(e instanceof Error ? e.message : "Could not load dashboard.");
      });
  }, [dealerId]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    setSubdealerExpanded(false);
    setSubMatrix(null);
    setSubdealerLoading(false);
    setSubMatrixErr(null);
    setSubdealers([]);
    setFilterDays(7);
    setFilterSubdealerTo(null);
    setMatrixDayIst(null);
    setChallanPanel(null);
    setChallanPanelErr(null);
    setChallanPanelLoading(false);
  }, [dealerId]);

  const principal = summary?.is_principal_dealer === true;

  useEffect(() => {
    if (!principal) {
      setSubdealers([]);
      return;
    }
    listDealersByParent(dealerId)
      .then(setSubdealers)
      .catch(() => setSubdealers([]));
  }, [dealerId, principal]);

  useEffect(() => {
    if (!principal) {
      setChallanPanel(null);
      setChallanPanelErr(null);
      setChallanPanelLoading(false);
      return;
    }
    let cancelled = false;
    setChallanPanelLoading(true);
    setChallanPanelErr(null);
    const finish = () => {
      if (!cancelled) setChallanPanelLoading(false);
    };
    if (matrixDayIst) {
      getDealerDashboardChallansByIstDay(dealerId, matrixDayIst)
        .then((res) => {
          if (!cancelled) {
            setChallanPanel({ mode: "matrix-day", ist_date: res.ist_date, rows: res.rows });
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) {
            setChallanPanelErr(e instanceof Error ? e.message : "Could not load challans for that day.");
          }
        })
        .finally(finish);
    } else {
      getDealerDashboardChallansFiltered(dealerId, { days: filterDays, dealerToId: filterSubdealerTo })
        .then((res) => {
          if (!cancelled) {
            setChallanPanel({
              mode: "filtered",
              days: res.days,
              ist_start: res.ist_start,
              ist_end: res.ist_end,
              dealer_to_id: res.dealer_to_id,
              rows: res.rows,
            });
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) {
            setChallanPanel(null);
            setChallanPanelErr(e instanceof Error ? e.message : "Could not load challan details.");
          }
        })
        .finally(finish);
    }
    return () => {
      cancelled = true;
    };
  }, [principal, dealerId, filterDays, filterSubdealerTo, matrixDayIst]);

  const toggleSubdealerBreakdown = useCallback(() => {
    if (!summary?.is_principal_dealer) return;
    if (subdealerExpanded) {
      setSubdealerExpanded(false);
      return;
    }
    setSubdealerExpanded(true);
    setSubMatrixErr(null);
    if (subMatrix) {
      return;
    }
    setSubdealerLoading(true);
    getDealerDashboardSubdealerSalesMatrix(dealerId)
      .then((m) => {
        setSubMatrix(m);
      })
      .catch((e: unknown) => {
        setSubMatrix(null);
        setSubMatrixErr(e instanceof Error ? e.message : "Could not load subdealer sales breakdown.");
        setSubdealerExpanded(false);
      })
      .finally(() => {
        setSubdealerLoading(false);
      });
  }, [dealerId, summary?.is_principal_dealer, subdealerExpanded, subMatrix]);

  const openChallanDayFromMatrix = useCallback((istDayIso: string) => {
    if (!summary?.is_principal_dealer) return;
    setChallanPanelErr(null);
    setMatrixDayIst(istDayIso);
  }, [summary?.is_principal_dealer]);

  const onFilterDaysChange = useCallback((d: FilterDays) => {
    setMatrixDayIst(null);
    setFilterDays(d);
  }, []);

  const onSubdealerSelectChange = useCallback((raw: string) => {
    setMatrixDayIst(null);
    if (raw === "") {
      setFilterSubdealerTo(null);
      return;
    }
    const n = Number(raw);
    setFilterSubdealerTo(Number.isFinite(n) ? n : null);
  }, []);

  if (error) {
    return (
      <div className="dealer-dashboard-page">
        <p className="dealer-dash-error">{error}</p>
        <button type="button" className="dealer-dash-retry" onClick={loadSummary}>
          Retry
        </button>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="dealer-dashboard-page">
        <p>Loading…</p>
      </div>
    );
  }

  const isPrincipal = summary.is_principal_dealer;

  const mergedRows: MergedMetricRow[] = [
    {
      key: "counter",
      label: "Total Counter Sales",
      counts: summary.counter_sales_counts,
    },
  ];
  if (isPrincipal && summary.subdealer_sales_counts) {
    mergedRows.push({
      key: "subdealer-sales",
      label: "Total Subdealer Sales",
      counts: summary.subdealer_sales_counts,
    });
  }
  if (isPrincipal && summary.subdealer_challan_counts) {
    mergedRows.push({
      key: "subdealer-challans",
      label: "Subdealer Challans",
      counts: summary.subdealer_challan_counts,
      interactive: "challan",
    });
  }

  const subdealerFilterName =
    filterSubdealerTo != null ? subdealers.find((s) => s.dealer_id === filterSubdealerTo)?.dealer_name : null;

  const challanSubtitle =
    challanPanel?.mode === "matrix-day"
      ? `Challan headers for ${challanPanel.ist_date} (IST day from matrix)`
      : challanPanel?.mode === "filtered"
        ? `Challans from ${formatIsoDateShort(challanPanel.ist_start)} to ${formatIsoDateShort(challanPanel.ist_end)} (IST) · last ${challanPanel.days} days${
            filterSubdealerTo != null && subdealerFilterName
              ? ` · subdealer: ${subdealerFilterName} (${filterSubdealerTo})`
              : " · All"
          }`
        : "";

  const challanEmptyMessage =
    challanPanel?.mode === "matrix-day"
      ? "No challan headers for this day."
      : "No challan headers in this period for the selected filters.";

  return (
    <div className="dealer-dashboard-page">
      <section className="dealer-dash-section" aria-labelledby="dash-rto-heading">
        <h2 id="dash-rto-heading" className="admin-usage-section-title">
          Current Outstanding RTO queue
        </h2>
        <p className="dealer-dash-rto-count">
          Pending RTO Queue = <strong>{summary.rto_queued_count}</strong>
        </p>
      </section>

      <section
        className="dealer-dash-section dealer-dash-section--merged-metrics"
        aria-label="Past 7 IST days: counter sales, subdealer sales, and subdealer challans"
      >
        <MergedBcdMetricsTable
          days={summary.days}
          rows={mergedRows}
          subdealerExpanded={subdealerExpanded}
          subdealerLoading={subdealerLoading}
          subdealerChildRows={subMatrix?.rows ?? null}
          onSubdealerLabelClick={toggleSubdealerBreakdown}
          onChallanCellClick={openChallanDayFromMatrix}
        />
        {subMatrixErr ? <p className="dealer-dash-error">{subMatrixErr}</p> : null}
      </section>

      {isPrincipal ? (
        <section className="dealer-dash-section" aria-labelledby="dash-challan-detail-heading">
          <h2 id="dash-challan-detail-heading" className="admin-usage-section-title">
            Subdealer Challan details
          </h2>

          <div className="dealer-dash-challan-filters">
            <label className="dealer-dash-challan-filter-label">
              <span className="dealer-dash-challan-filter-caption">Subdealer</span>
              <select
                className="dealer-dash-challan-select"
                value={filterSubdealerTo ?? ""}
                onChange={(e) => onSubdealerSelectChange(e.target.value)}
                aria-label="Filter challans by subdealer; All includes every subdealer"
              >
                <option value="">All</option>
                {subdealers.map((s) => (
                  <option key={s.dealer_id} value={String(s.dealer_id)}>
                    {s.dealer_name?.trim() || "—"}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="dealer-dash-challan-period">
              <legend className="dealer-dash-challan-period-legend">Period (IST)</legend>
              <label className="dealer-dash-radio-label">
                <input
                  type="radio"
                  name="challan-period-ist"
                  checked={filterDays === 7}
                  onChange={() => onFilterDaysChange(7)}
                />{" "}
                Last 7 days
              </label>
              <label className="dealer-dash-radio-label">
                <input
                  type="radio"
                  name="challan-period-ist"
                  checked={filterDays === 15}
                  onChange={() => onFilterDaysChange(15)}
                />{" "}
                Last 15 days
              </label>
              <label className="dealer-dash-radio-label">
                <input
                  type="radio"
                  name="challan-period-ist"
                  checked={filterDays === 30}
                  onChange={() => onFilterDaysChange(30)}
                />{" "}
                Last 30 days
              </label>
            </fieldset>
          </div>

          {matrixDayIst ? (
            <p className="dealer-dash-muted">
              Showing a single IST day from the matrix. Change subdealer or period above to return to the filtered
              list.
            </p>
          ) : null}

          {challanPanelErr ? <p className="dealer-dash-error">{challanPanelErr}</p> : null}
          {challanPanelLoading && !challanPanel ? <p className="dealer-dash-muted">Loading challan details…</p> : null}
          {challanPanel ? (
            <>
              <h3 className="dealer-dash-detail-title">{challanSubtitle}</h3>
              <ChallanDetailTable
                rows={challanPanel.rows}
                ariaLabel={
                  challanPanel.mode === "matrix-day"
                    ? "Challan master rows for selected IST day"
                    : "Challan master rows for filtered period and subdealer"
                }
                emptyMessage={challanEmptyMessage}
              />
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
