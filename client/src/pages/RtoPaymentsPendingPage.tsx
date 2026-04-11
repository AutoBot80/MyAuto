import { type FormEvent, useEffect, useState } from "react";
import { warmVahanBrowser } from "../api/fillForms";
import {
  getRtoBatchStatus,
  listRtoPayments,
  retryRtoQueueRow,
  startRtoBatch,
  submitOperatorMobileChange,
  submitOperatorOtp,
  type RtoBatchStatus,
  type RtoPaymentRow,
} from "../api/rtoPaymentDetails";
import { DEALER_ID } from "../api/dealerId";

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
  const [otpInput, setOtpInput] = useState("");
  const [otpUiMode, setOtpUiMode] = useState<"otp" | "mobile">("otp");
  const [mobileChangeInput, setMobileChangeInput] = useState("");
  const [otpSubmitting, setOtpSubmitting] = useState(false);
  const [otpSubmitError, setOtpSubmitError] = useState<string | null>(null);

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
        if (data.state === "running" || data.state === "starting") {
          setVahanWarmMessage(data.message || "Processing…");
        }
        if (data.state === "completed" || data.state === "failed") {
          setStartingBatch(false);
          setVahanReadyForBatch(false);
          setVahanWarmMessage(data.message || (data.state === "completed" ? "Batch completed" : "Batch failed"));
          if (data.last_error) {
            setBatchError(data.last_error);
          }
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
    const ms = batchStatus?.otp_pending ? 1000 : 2000;
    const timer = window.setInterval(() => {
      fetchBatchStatus();
    }, ms);
    return () => window.clearInterval(timer);
  }, [startingBatch, batchStatus?.state, batchStatus?.otp_pending, dealerId]);

  useEffect(() => {
    if (!batchStatus?.otp_pending) {
      setOtpInput("");
      setMobileChangeInput("");
      setOtpSubmitError(null);
      setOtpUiMode("otp");
    }
  }, [batchStatus?.otp_pending]);

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
      const res = await startRtoBatch({ dealer_id: dealerId });
      if (!res.started) {
        setStartingBatch(false);
        setBatchError(res.message || "Batch was not started");
        return;
      }
      setVahanWarmMessage(res.message || "Batch started — processing rows…");
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

  const handleSubmitOtp = async (e: FormEvent) => {
    e.preventDefault();
    const qid = batchStatus?.otp_rto_queue_id;
    if (qid == null) return;
    const did = dealerId ?? DEALER_ID;
    setOtpSubmitting(true);
    setOtpSubmitError(null);
    try {
      await submitOperatorOtp({
        dealer_id: did,
        rto_queue_id: qid,
        otp: otpInput.trim(),
      });
      setOtpInput("");
      fetchBatchStatus();
    } catch (err) {
      setOtpSubmitError(err instanceof Error ? err.message : "Could not submit OTP");
    } finally {
      setOtpSubmitting(false);
    }
  };

  const handleSubmitMobileChange = async (e: FormEvent) => {
    e.preventDefault();
    const qid = batchStatus?.otp_rto_queue_id;
    if (qid == null) return;
    const did = dealerId ?? DEALER_ID;
    const digits = mobileChangeInput.replace(/\D/g, "").slice(-10);
    if (digits.length !== 10 || !/^[6-9]/.test(digits)) {
      setOtpSubmitError("Enter a valid 10-digit Indian mobile (starts with 6–9).");
      return;
    }
    setOtpSubmitting(true);
    setOtpSubmitError(null);
    try {
      await submitOperatorMobileChange({
        dealer_id: did,
        rto_queue_id: qid,
        mobile: digits,
      });
      setMobileChangeInput("");
      fetchBatchStatus();
    } catch (err) {
      setOtpSubmitError(err instanceof Error ? err.message : "Could not apply mobile change");
    } finally {
      setOtpSubmitting(false);
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
          {batchStatus.message && (
            <p className="rto-batch-status-message">{batchStatus.message}</p>
          )}
          {batchStatus.last_error && (
            <p className="rto-payments-error">Last error: {batchStatus.last_error}</p>
          )}
        </section>
      )}
      {batchStatus?.otp_pending && batchStatus.otp_rto_queue_id != null && (
        <section className="rto-otp-prompt-card" aria-live="polite">
          <h3 className="rto-otp-prompt-title">Vahan: OTP for Verify Owner&apos;s Mobile</h3>
          <p className="rto-otp-prompt-text">
            {batchStatus.otp_prompt ??
              "The portal is asking for an OTP. Use Enter OTP, or switch to Use a different mobile."}
          </p>
          <p className="rto-otp-mobile-line">
            <span className="rto-otp-mobile-label">Mobile OTP is sent to (call this number to ask for OTP):</span>{" "}
            <span className="rto-otp-mobile-value">
              {batchStatus.otp_customer_mobile && batchStatus.otp_customer_mobile !== "—"
                ? batchStatus.otp_customer_mobile
                : "— (check Vahan popup or your sale record)"}
            </span>
          </p>
          {batchStatus.otp_allow_change_mobile !== false && (
            <div className="rto-otp-mode-toggle" role="tablist" aria-label="OTP or change mobile">
              <button
                type="button"
                role="tab"
                aria-selected={otpUiMode === "otp"}
                className={`rto-otp-mode-btn${otpUiMode === "otp" ? " rto-otp-mode-btn--active" : ""}`}
                onClick={() => {
                  setOtpUiMode("otp");
                  setOtpSubmitError(null);
                }}
              >
                Enter OTP
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={otpUiMode === "mobile"}
                className={`rto-otp-mode-btn${otpUiMode === "mobile" ? " rto-otp-mode-btn--active" : ""}`}
                onClick={() => {
                  setOtpUiMode("mobile");
                  setOtpSubmitError(null);
                }}
              >
                Use a different mobile
              </button>
            </div>
          )}
          {otpUiMode === "otp" || batchStatus.otp_allow_change_mobile === false ? (
            <form className="rto-otp-form" onSubmit={handleSubmitOtp}>
              <label className="rto-otp-label">
                Enter OTP
                <input
                  className="rto-otp-input"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={otpInput}
                  onChange={(ev) => setOtpInput(ev.target.value.replace(/\D/g, "").slice(0, 8))}
                  placeholder="OTP from SMS"
                  disabled={otpSubmitting}
                />
              </label>
              <button
                type="submit"
                className="app-button app-button--primary"
                disabled={otpSubmitting || otpInput.trim().length < 4}
              >
                {otpSubmitting ? "Sending…" : "Submit OTP to Vahan"}
              </button>
            </form>
          ) : (
            <form className="rto-otp-form rto-otp-form--stack" onSubmit={handleSubmitMobileChange}>
              <p className="rto-otp-hint">
                Automation will cancel the Vahan popup, set this mobile on the form, and press{" "}
                <strong>Inward Application (Partial Save)</strong> again so a new OTP goes to the new number.
                You do not need to click Cancel on Vahan yourself.
              </p>
              <label className="rto-otp-label">
                New mobile number
                <input
                  className="rto-otp-input"
                  type="tel"
                  inputMode="numeric"
                  autoComplete="tel"
                  value={mobileChangeInput}
                  onChange={(ev) => setMobileChangeInput(ev.target.value.replace(/\D/g, "").slice(0, 10))}
                  placeholder="10-digit mobile"
                  disabled={otpSubmitting}
                />
              </label>
              <button
                type="submit"
                className="app-button app-button--primary"
                disabled={otpSubmitting || mobileChangeInput.replace(/\D/g, "").length !== 10}
              >
                {otpSubmitting ? "Applying…" : "Cancel popup, update mobile & reopen OTP"}
              </button>
            </form>
          )}
          {otpSubmitError && <p className="rto-payments-error">{otpSubmitError}</p>}
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
