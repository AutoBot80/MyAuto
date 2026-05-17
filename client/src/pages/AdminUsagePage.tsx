import { useCallback, useEffect, useState } from "react";
import {
  getAdminFailureLogs,
  getAdminUsageDealerMatrix,
  type AdminProcessFailureLogListResponse,
  type AdminProcessFailureLogRow,
  type AdminUsageDealerMatrixResponse,
  type AdminUsageDealerMatrixRow,
} from "../api/admin";
import { AdminDataFolderPage } from "./AdminDataFolderPage";
import "./AdminUsagePage.css";

function dayHeaderLabel(isoDay: string): string {
  const p = isoDay.split("-");
  if (p.length === 3) return `${p[2]}/${p[1]}`;
  return isoDay;
}

function UsageDealerMatrixTable({
  days,
  rows,
  ariaLabel,
}: {
  days: string[];
  rows: AdminUsageDealerMatrixRow[];
  ariaLabel: string;
}) {
  return (
    <div className="app-table-wrap admin-usage-matrix-wrap">
      <table className="app-table admin-usage-matrix-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            <th scope="col" className="admin-usage-matrix-dealer-col">
              Dealer
            </th>
            {days.map((d) => (
              <th key={d} scope="col" className="admin-usage-matrix-day" title={`${d} (IST)`}>
                {dayHeaderLabel(d)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={Math.max(1, days.length + 1)} className="app-table-empty">
                No rows in this period.
              </td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr key={`${r.dealer_id}-${r.dealer_name}`}>
                <th scope="row" className="admin-usage-matrix-dealer">
                  {r.dealer_name}
                </th>
                {r.counts.map((c, i) => (
                  <td key={i} className="admin-usage-matrix-num">
                    {c}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function FailureLogsTable({ rows, ariaLabel }: { rows: AdminProcessFailureLogRow[]; ariaLabel: string }) {
  return (
    <div className="app-table-wrap admin-failure-logs-wrap">
      <table className="app-table admin-failure-logs-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            <th scope="col">When (IST)</th>
            <th scope="col">Dealer</th>
            <th scope="col">Process</th>
            <th scope="col">Mobile</th>
            <th scope="col">Challan</th>
            <th scope="col">RTO queue</th>
            <th scope="col">Error</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={7} className="app-table-empty">
                No failure rows yet.
              </td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr key={r.id}>
                <td className="admin-failure-logs-nowrap">{r.occurred_at_ist}</td>
                <td>
                  <span className="admin-failure-logs-dealer">{r.dealer_name}</span>
                  <span className="admin-failure-logs-dealer-id"> ({r.dealer_id})</span>
                </td>
                <td>{r.process_label}</td>
                <td className="admin-failure-logs-nowrap">{r.customer_mobile ?? "—"}</td>
                <td className="admin-failure-logs-challan">
                  {[r.challan_book_num, r.challan_date].filter(Boolean).join(" · ") || "—"}
                  {r.challan_batch_id ? (
                    <span className="admin-failure-logs-batch" title={r.challan_batch_id}>
                      {" "}
                      (batch)
                    </span>
                  ) : null}
                </td>
                <td className="admin-failure-logs-num">{r.rto_queue_id ?? "—"}</td>
                <td className="admin-failure-logs-error">{r.error_text}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export interface AdminUsagePageProps {
  dealerId: number;
}

export function AdminUsagePage({ dealerId }: AdminUsagePageProps) {
  const [sub, setSub] = useState<"sales" | "challans" | "failures">("sales");
  const [matrix, setMatrix] = useState<AdminUsageDealerMatrixResponse | null>(null);
  const [matrixErr, setMatrixErr] = useState<string | null>(null);
  const [failures, setFailures] = useState<AdminProcessFailureLogListResponse | null>(null);
  const [failuresErr, setFailuresErr] = useState<string | null>(null);

  const loadMatrix = useCallback(() => {
    setMatrixErr(null);
    getAdminUsageDealerMatrix()
      .then(setMatrix)
      .catch((e) => {
        setMatrix(null);
        setMatrixErr(e instanceof Error ? e.message : "Could not load usage matrix.");
      });
  }, []);

  useEffect(() => {
    loadMatrix();
  }, [loadMatrix]);

  const loadFailures = useCallback(() => {
    setFailuresErr(null);
    getAdminFailureLogs(200)
      .then(setFailures)
      .catch((e) => {
        setFailures(null);
        setFailuresErr(e instanceof Error ? e.message : "Could not load failure logs.");
      });
  }, []);

  useEffect(() => {
    if (sub === "failures") loadFailures();
  }, [sub, loadFailures]);

  return (
    <div
      className={`admin-usage-page${sub === "failures" ? " admin-usage-page--failures" : ""}`}
    >
      <div className="admin-usage-subtabs" role="tablist" aria-label="Usage sections">
        <button type="button" role="tab" className={sub === "sales" ? "active" : ""} onClick={() => setSub("sales")}>
          Sales
        </button>
        <button
          type="button"
          role="tab"
          className={sub === "challans" ? "active" : ""}
          onClick={() => setSub("challans")}
        >
          Challans
        </button>
        <button
          type="button"
          role="tab"
          className={sub === "failures" ? "active" : ""}
          onClick={() => setSub("failures")}
        >
          Failure Logs
        </button>
      </div>

      {sub === "failures" ? (
        <section className="admin-usage-failures-panel" aria-label="Failure logs">
          <p className="admin-failure-logs-hint">
            Newest first ({failures?.timezone_label ?? "Asia/Kolkata (IST)"}). Includes Print / Queue RTO,
            Create Invoice, Insurance, challan, and related automation failures.
          </p>
          {failuresErr ? <p className="view-vehicles-error">{failuresErr}</p> : null}
          {failures ? (
            <FailureLogsTable rows={failures.rows} ariaLabel="Process failure log, newest first" />
          ) : !failuresErr ? (
            <p>Loading…</p>
          ) : null}
        </section>
      ) : sub === "sales" ? (
        <>
          <section aria-labelledby="usage-sales-table-title">
            <h2 id="usage-sales-table-title" className="admin-usage-section-title">
              Sales
            </h2>
            {matrixErr ? <p className="view-vehicles-error">{matrixErr}</p> : null}
            {matrix ? (
              <div className="admin-usage-table-wrap">
                <UsageDealerMatrixTable
                  days={matrix.days}
                  rows={matrix.sales}
                  ariaLabel="Sales master counts by dealer and IST day"
                />
              </div>
            ) : !matrixErr ? (
              <p>Loading…</p>
            ) : null}
          </section>

          <section className="admin-usage-folder-embed" aria-label="Sales folder browsers">
            <h3>Browse folders (session dealer)</h3>
            <div className="admin-usage-sales-folder-grid">
              <div className="admin-usage-sales-folder-cell">
                <h4>Upload scans</h4>
                <AdminDataFolderPage dealerId={dealerId} kind="upload-scans" dealerPicker="hidden" />
              </div>
              <div className="admin-usage-sales-folder-cell">
                <h4>OCR output</h4>
                <AdminDataFolderPage dealerId={dealerId} kind="run-logs" dealerPicker="hidden" />
              </div>
            </div>
          </section>
        </>
      ) : (
        <>
          <section aria-labelledby="usage-challan-table-title">
            <h2 id="usage-challan-table-title" className="admin-usage-section-title">
              Challans
            </h2>
            {matrixErr ? <p className="view-vehicles-error">{matrixErr}</p> : null}
            {matrix ? (
              <div className="admin-usage-table-wrap">
                <UsageDealerMatrixTable
                  days={matrix.days}
                  rows={matrix.challans}
                  ariaLabel="Challan master counts by from-dealer and IST day"
                />
              </div>
            ) : !matrixErr ? (
              <p>Loading…</p>
            ) : null}
          </section>

          <section className="admin-usage-folder-embed" aria-label="Challan folder contents">
            <h3>Browse challan folder</h3>
            <AdminDataFolderPage dealerId={dealerId} kind="challans" />
          </section>
        </>
      )}
    </div>
  );
}
