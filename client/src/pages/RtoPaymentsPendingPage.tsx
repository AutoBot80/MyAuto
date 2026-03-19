import { useEffect, useMemo, useState } from "react";
import {
  getRtoBatchStatus,
  listRtoPayments,
  retryRtoQueueRow,
  startRtoBatch,
  type RtoBatchStatus,
  type RtoPaymentRow,
} from "../api/rtoPaymentDetails";

interface RtoPaymentsPendingPageProps {
  dealerId?: number;
}

export function RtoPaymentsPendingPage({ dealerId }: RtoPaymentsPendingPageProps) {
  const [rows, setRows] = useState<RtoPaymentRow[]>([]);
  const [batchStatus, setBatchStatus] = useState<RtoBatchStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [startingBatch, setStartingBatch] = useState(false);
  const [retryingQueueId, setRetryingQueueId] = useState<string | null>(null);

  const progressPercent = useMemo(() => {
    if (!batchStatus || batchStatus.total_count <= 0) return 0;
    return Math.min(100, Math.round((batchStatus.processed_count / batchStatus.total_count) * 100));
  }, [batchStatus]);

  const fetchFromDb = (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    listRtoPayments(dealerId)
      .then((data) => setRows(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"))
      .finally(() => setLoading(false));
  };

  const fetchBatchStatus = () => {
    getRtoBatchStatus(dealerId)
      .then((data) => {
        setBatchStatus(data);
        if (data.state === "completed" || data.state === "failed") {
          setStartingBatch(false);
          fetchFromDb(false);
        }
      })
      .catch((err) => {
        setBatchError(err instanceof Error ? err.message : "Failed to load batch status");
      });
  };

  useEffect(() => {
    fetchFromDb(true);
    fetchBatchStatus();
    return undefined;
  }, [dealerId]);

  useEffect(() => {
    const isActive =
      startingBatch ||
      batchStatus?.state === "starting" ||
      batchStatus?.state === "running";
    if (!isActive) return undefined;
    const timer = window.setInterval(() => {
      fetchBatchStatus();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [startingBatch, batchStatus?.state, dealerId]);

  const handleStartBatch = async () => {
    setBatchError(null);
    setStartingBatch(true);
    try {
      await startRtoBatch({ dealer_id: dealerId });
      fetchBatchStatus();
      fetchFromDb(false);
    } catch (err) {
      setStartingBatch(false);
      setBatchError(err instanceof Error ? err.message : "Failed to start batch");
      fetchBatchStatus();
    }
  };

  const handleTryAgain = async (queueId: string) => {
    setBatchError(null);
    setRetryingQueueId(queueId);
    try {
      await retryRtoQueueRow(queueId);
      fetchFromDb(false);
      fetchBatchStatus();
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : "Failed to retry row");
    } finally {
      setRetryingQueueId(null);
    }
  };

  if (loading && rows.length === 0) {
    return (
      <div className="app-placeholder rto-payments-page">
        <p>Loading…</p>
      </div>
    );
  }

  if (error && rows.length === 0) {
    return (
      <div className="app-placeholder rto-payments-page">
        <p className="rto-payments-error">{error}</p>
      </div>
    );
  }

  const columns = [
    "Queue ID",
    "Vahan App ID",
    "Customer ID",
    "Customer Name",
    "Mobile",
    "Vehicle ID",
    "Chassis No.",
    "Register Date",
    "RTO Fees",
    "Status",
    "Actions",
  ];

  const batchStateClass =
    batchStatus?.state === "running" || batchStatus?.state === "starting"
      ? "app-process-status app-process-status--running"
      : batchStatus?.state === "completed"
        ? "app-process-status app-process-status--sleeping"
        : "app-process-status app-process-status--waiting";
  const isInstructionalVahanError = (msg?: string | null) => {
    const text = String(msg || "").toLowerCase();
    return (
      text.includes("opened. please login") ||
      text.includes("cannot switch to a different thread")
    );
  };

  return (
    <div className="rto-payments-page">
      {error && <p className="rto-payments-error">{error}</p>}
      <div className="rto-batch-toolbar">
        <div className="rto-batch-toolbar-actions">
        <button
          type="button"
          className="app-button app-button--primary"
          onClick={handleStartBatch}
          disabled={startingBatch || batchStatus?.state === "running" || batchStatus?.state === "starting"}
        >
          {startingBatch || batchStatus?.state === "running" || batchStatus?.state === "starting"
            ? "Processing oldest 7..."
            : "Fill Vahan Site"}
        </button>
        <span className="rto-batch-toolbar-note">If Vahan opens, login and press Fill Vahan Site again.</span>
        </div>
      </div>
      {batchStatus && batchStatus.state !== "idle" && (
        <section className="rto-batch-status-card">
          <div className="app-process-status-bar">
            <span className="app-process-status-label">Batch status</span>
            <span className={batchStateClass}>{batchStatus.state}</span>
            <span className="app-process-count">
              {batchStatus.processed_count} / {batchStatus.total_count} processed
            </span>
            <span className="app-process-count">{batchStatus.cart_count} added to RTO Cart</span>
            <span className="app-process-count">{batchStatus.failed_count} failed</span>
          </div>
          <div className="rto-batch-progress-track" aria-hidden="true">
            <div className="rto-batch-progress-fill" style={{ width: `${progressPercent}%` }} />
          </div>
          <div className="rto-batch-meta">
            <span>{batchStatus.message}</span>
            {batchStatus.current_queue_id && <span>Current queue: {batchStatus.current_queue_id}</span>}
            {batchStatus.current_customer_name && <span>Customer: {batchStatus.current_customer_name}</span>}
            {batchStatus.current_vahan_application_id && <span>Vahan App ID: {batchStatus.current_vahan_application_id}</span>}
          </div>
          {batchStatus.last_error && !isInstructionalVahanError(batchStatus.last_error) && (
            <p className="app-process-last-error">Last error: {batchStatus.last_error}</p>
          )}
        </section>
      )}
      {batchError && !isInstructionalVahanError(batchError) && <p className="rto-payments-error">{batchError}</p>}
      <div className="rto-payments-table-wrap">
        <table className="rto-payments-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>No records.</td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.queue_id}>
                  <td>{r.queue_id}</td>
                  <td>{r.vahan_application_id ?? "—"}</td>
                  <td>{r.customer_id}</td>
                  <td>{r.name ?? "—"}</td>
                  <td>{r.mobile ?? "—"}</td>
                  <td>{r.vehicle_id}</td>
                  <td>{r.chassis_num ?? "—"}</td>
                  <td>{r.register_date}</td>
                  <td>{r.rto_fees != null ? `₹${r.rto_fees}` : "—"}</td>
                  <td>{r.status ?? "Pending"}</td>
                  <td>
                    {String(r.status || "").toLowerCase() === "failed" ? (
                      <button
                        type="button"
                        className="app-button"
                        onClick={() => handleTryAgain(r.queue_id)}
                        disabled={retryingQueueId === r.queue_id}
                      >
                        {retryingQueueId === r.queue_id ? "Retrying..." : "Retry"}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
