import { useEffect, useMemo, useState } from "react";
import { warmVahanBrowser } from "../api/fillForms";
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
  const [retryingQueueId, setRetryingQueueId] = useState<number | null>(null);
  /** After warm-browser succeeds, next click starts the batch (same UX as Create Invoice / login gate). */
  const [vahanReadyForBatch, setVahanReadyForBatch] = useState(false);
  const [vahanWarmMessage, setVahanWarmMessage] = useState<string | null>(null);

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
          setVahanReadyForBatch(false);
          setVahanWarmMessage(null);
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
    setVahanReadyForBatch(false);
    setVahanWarmMessage(null);
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

    if (!vahanReadyForBatch) {
      setStartingBatch(true);
      setVahanWarmMessage(null);
      try {
        const warm = await warmVahanBrowser();
        if (!warm.success) {
          setBatchError(warm.error ?? "Could not open Vahan site");
          return;
        }
        setVahanReadyForBatch(true);
        setVahanWarmMessage(
          warm.message ??
            "Vahan Opened. Please login. And then press button again"
        );
      } catch (err) {
        setBatchError(err instanceof Error ? err.message : "Failed to open Vahan site");
      } finally {
        setStartingBatch(false);
      }
      return;
    }

    setStartingBatch(true);
    const prevWarmMsg = vahanWarmMessage;
    setVahanWarmMessage(null);
    setVahanReadyForBatch(false);
    try {
      await startRtoBatch({ dealer_id: dealerId });
      fetchBatchStatus();
      fetchFromDb(false);
    } catch (err) {
      setStartingBatch(false);
      setVahanReadyForBatch(true);
      setVahanWarmMessage(prevWarmMsg);
      setBatchError(err instanceof Error ? err.message : "Failed to start batch");
      fetchBatchStatus();
    }
  };

  const handleTryAgain = async (queueId: number) => {
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
    "RTO App ID",
    "Customer Name",
    "Mobile",
    "Chassis No.",
    "RTO Amount",
    "Status",
    "Actions",
  ];

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
          title={
            vahanReadyForBatch
              ? "Start processing queued RTO rows on the logged-in Vahan tab"
              : "Open Vahan in the browser; log in; then press again to run the batch"
          }
        >
          {startingBatch || batchStatus?.state === "running" || batchStatus?.state === "starting"
            ? "Processing oldest 7..."
            : vahanReadyForBatch
              ? "Continue Vahan batch"
              : "Fill Vahan Site"}
        </button>
        <span className="rto-batch-toolbar-note">
          {vahanWarmMessage ? (
            <span className="rto-batch-vahan-warm-msg" role="status">
              {vahanWarmMessage}
            </span>
          ) : (
            <>Batch processes up to 7 queued rows. First click opens Vahan—log in, then press again to run.</>
          )}
        </span>
        </div>
      </div>
      {batchStatus && batchStatus.state !== "idle" && (
        <section className="rto-batch-status-card">
          <div className="app-process-status-bar">
            <span className="app-process-count">
              Processing: {batchStatus.processed_count}/{batchStatus.total_count}, Completed: {batchStatus.completed_count}, Failed: {batchStatus.failed_count}
            </span>
          </div>
        </section>
      )}
      {batchError && <p className="rto-payments-error">{batchError}</p>}
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
                <tr key={r.rto_queue_id}>
                  <td>{r.rto_queue_id}</td>
                  <td>{r.rto_application_id ?? "—"}</td>
                  <td>{r.customer_name ?? "—"}</td>
                  <td>{r.mobile ?? r.customer_mobile ?? "—"}</td>
                  <td>{r.chassis_num ?? "—"}</td>
                  <td>{r.rto_payment_amount != null ? `₹${r.rto_payment_amount}` : "—"}</td>
                  <td>{r.status ?? "Pending"}</td>
                  <td>
                    {String(r.status || "").toLowerCase() === "failed" ? (
                      <button
                        type="button"
                        className="app-button"
                        onClick={() => handleTryAgain(r.rto_queue_id)}
                        disabled={retryingQueueId === r.rto_queue_id}
                      >
                        {retryingQueueId === r.rto_queue_id ? "Retrying..." : "Retry"}
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
