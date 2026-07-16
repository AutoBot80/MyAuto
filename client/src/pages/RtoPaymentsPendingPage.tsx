import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { formatVahanWarmError, warmVahanBrowserLocal } from "../api/fillForms";
import {
  getRtoBatchStatus,
  getRtoFormsStatus,
  listRtoPayments,
  markRtoDone,
  markRtoFormsReady,
  releaseRtoQueueRow,
  requeueRtoQueueRow,
  retryRtoQueueRow,
  setRtoInQueue,
  startRtoBatchLocal,
  submitOperatorMobileChange,
  submitOperatorOtp,
  uploadRtoQueueFormsApi,
  type RtoBatchStatus,
  type RtoFormsMissingItem,
  type RtoPaymentRow,
} from "../api/rtoPaymentDetails";
import { uploadRtoQueueFormsLocal } from "../api/printRtoSidecar";
import { DEALER_ID } from "../api/dealerId";
import { isElectron } from "../electron";

interface RtoPaymentsPendingPageProps {
  dealerId?: number;
}

type RtoQueueSubTab = "in_process" | "forms_missing" | "completed";

interface FileWithPath extends File {
  path?: string;
}

const IN_PROCESS_STATUSES = new Set(["Queued", "Pending", "In Progress", "Failed", "Needs TRC"]);
const COMPLETED_STATUSES = new Set(["Completed", "Manually Completed"]);
const NEEDS_TRC_STATUS = "Needs TRC";

function filePathFromInput(file: File | null): string | null {
  if (!file) return null;
  const p = (file as FileWithPath).path;
  return typeof p === "string" && p.trim() ? p.trim() : null;
}

function acceptForCategory(key: string): string {
  if (key === "FORM 20" || key.startsWith("AADHAAR") || key === "OWNER UNDERTAKING FORM") {
    return "image/*,.pdf";
  }
  return ".pdf,application/pdf";
}

export function RtoPaymentsPendingPage({ dealerId }: RtoPaymentsPendingPageProps) {
  const [rows, setRows] = useState<RtoPaymentRow[]>([]);
  const [subTab, setSubTab] = useState<RtoQueueSubTab>("in_process");
  const [batchStatus, setBatchStatus] = useState<RtoBatchStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [startingBatch, setStartingBatch] = useState(false);
  const [retryingQueueId, setRetryingQueueId] = useState<number | null>(null);
  const [actionQueueId, setActionQueueId] = useState<number | null>(null);
  const [vahanReadyForBatch, setVahanReadyForBatch] = useState(false);
  const [vahanWarmMessage, setVahanWarmMessage] = useState<string | null>(null);
  const [otpInput, setOtpInput] = useState("");
  const [otpUiMode, setOtpUiMode] = useState<"otp" | "mobile">("otp");
  const [mobileChangeInput, setMobileChangeInput] = useState("");
  const [otpSubmitting, setOtpSubmitting] = useState(false);
  const [otpSubmitError, setOtpSubmitError] = useState<string | null>(null);

  const [uploadRow, setUploadRow] = useState<RtoPaymentRow | null>(null);
  const [uploadMissing, setUploadMissing] = useState<RtoFormsMissingItem[]>([]);
  const [uploadFormsLoading, setUploadFormsLoading] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<Record<string, File | null>>({});
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSubmitting, setUploadSubmitting] = useState(false);

  const triggerVahanWarm = useCallback(() => {
    void warmVahanBrowserLocal().catch(() => {});
  }, []);

  const fetchFromDb = (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    listRtoPayments(dealerId)
      .then((data) =>
        setRows(
          data.map((r) => ({
            ...r,
            in_queue: r.in_queue !== false,
          }))
        )
      )
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
    triggerVahanWarm();
  }, [dealerId, triggerVahanWarm]);

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

  const filteredRows = useMemo(() => {
    if (subTab === "in_process") {
      return rows.filter((r) => IN_PROCESS_STATUSES.has(r.status));
    }
    if (subTab === "forms_missing") {
      return rows.filter((r) => r.status === "Forms Missing");
    }
    return rows.filter((r) => COMPLETED_STATUSES.has(r.status));
  }, [rows, subTab]);

  const formsMissingCount = useMemo(
    () => rows.filter((r) => r.status === "Forms Missing").length,
    [rows]
  );

  const handleStartBatch = async () => {
    setBatchError(null);
    setStartingBatch(true);
    setVahanWarmMessage(null);
    const prevWarmMsg = vahanWarmMessage;
    try {
      const warm = await warmVahanBrowserLocal();
      if (!warm.success) {
        setStartingBatch(false);
        setBatchError(warm.error ?? "Could not open Vahan site");
        return;
      }

      const canStartBatch = Boolean(warm.ready_for_batch) || vahanReadyForBatch;
      if (!canStartBatch) {
        setStartingBatch(false);
        setVahanReadyForBatch(true);
        setVahanWarmMessage(
          warm.message ?? "Vahan Opened. Please login. And then press button again"
        );
        return;
      }

      setVahanReadyForBatch(false);
      const res = await startRtoBatchLocal({ dealer_id: dealerId });
      if (!res.started) {
        setStartingBatch(false);
        setBatchError(res.message || "Batch was not started");
        return;
      }
      setVahanWarmMessage(res.message || "Batch started — processing rows…");
      if (isElectron()) {
        setStartingBatch(false);
      }
      fetchBatchStatus();
      fetchFromDb(false);
    } catch (err) {
      setStartingBatch(false);
      setVahanReadyForBatch(true);
      setVahanWarmMessage(prevWarmMsg);
      setBatchError(formatVahanWarmError(err));
      fetchBatchStatus();
    }
  };

  const handleRelease = async (queueId: number) => {
    setBatchError(null);
    setActionQueueId(queueId);
    try {
      await releaseRtoQueueRow(queueId);
      fetchFromDb(false);
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : "Release failed");
    } finally {
      setActionQueueId(null);
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
    triggerVahanWarm();
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

  const handleInQueueChange = async (row: RtoPaymentRow, checked: boolean) => {
    const prev = row.in_queue !== false;
    setRows((rs) =>
      rs.map((r) => (r.rto_queue_id === row.rto_queue_id ? { ...r, in_queue: checked } : r))
    );
    try {
      await setRtoInQueue(row.rto_queue_id, checked);
    } catch (err) {
      setRows((rs) =>
        rs.map((r) => (r.rto_queue_id === row.rto_queue_id ? { ...r, in_queue: prev } : r))
      );
      setBatchError(err instanceof Error ? err.message : "Could not update In Queue");
    }
  };

  const handleMarkDone = async (queueId: number) => {
    if (!window.confirm("Mark this sale done without running Vahan?")) return;
    setActionQueueId(queueId);
    try {
      await markRtoDone(queueId);
      fetchFromDb(false);
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : "Mark Done failed");
    } finally {
      setActionQueueId(null);
    }
  };

  const handleRequeue = async (queueId: number) => {
    setActionQueueId(queueId);
    try {
      await requeueRtoQueueRow(queueId);
      fetchFromDb(false);
      setSubTab("in_process");
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : "Send to Queue failed");
    } finally {
      setActionQueueId(null);
    }
  };

  const openUploadForms = async (row: RtoPaymentRow) => {
    setUploadError(null);
    setUploadRow(row);
    setUploadFiles({});
    setUploadMissing([]);
    setUploadFormsLoading(true);
    try {
      const st = await getRtoFormsStatus(row.rto_queue_id);
      setUploadMissing(st.missing ?? []);
    } catch (err) {
      setUploadMissing([]);
      setUploadError(err instanceof Error ? err.message : "Could not load missing forms");
    } finally {
      setUploadFormsLoading(false);
    }
  };

  const closeUploadForms = () => {
    setUploadRow(null);
    setUploadMissing([]);
    setUploadFormsLoading(false);
    setUploadFiles({});
    setUploadError(null);
  };

  const handleUploadFormsSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!uploadRow) return;
    const did = dealerId ?? DEALER_ID;
    const subfolder = (uploadRow.subfolder || "").trim();
    if (!subfolder) {
      setUploadError("Sale subfolder is missing on this row.");
      return;
    }
    const apiUploads: { category_key: string; file: File }[] = [];
    const sidecarUploads: { category_key: string; source_path: string }[] = [];
    for (const item of uploadMissing) {
      const file = uploadFiles[item.key];
      if (!file) {
        setUploadError(`Choose a file for: ${item.label}`);
        return;
      }
      if (isElectron()) {
        const path = filePathFromInput(file);
        if (!path) {
          setUploadError(`Choose a file for: ${item.label}`);
          return;
        }
        sidecarUploads.push({ category_key: item.key, source_path: path });
      } else {
        apiUploads.push({ category_key: item.key, file });
      }
    }

    setUploadSubmitting(true);
    setUploadError(null);
    try {
      if (uploadMissing.length === 0) {
        await markRtoFormsReady(uploadRow.rto_queue_id);
        closeUploadForms();
        fetchFromDb(false);
        setSubTab("in_process");
        return;
      }
      const res = isElectron()
        ? await uploadRtoQueueFormsLocal({
            dealer_id: did,
            subfolder,
            rto_queue_id: uploadRow.rto_queue_id,
            mobile: uploadRow.customer_mobile ?? uploadRow.mobile ?? null,
            uploads: sidecarUploads,
          })
        : await uploadRtoQueueFormsApi(uploadRow.rto_queue_id, apiUploads);
      if (res.ready) {
        closeUploadForms();
        fetchFromDb(false);
        setSubTab("in_process");
        return;
      }
      if (res.missing?.length) {
        setUploadError(
          res.error ?? `Still missing: ${res.missing.join(", ")}`
        );
        const st = await getRtoFormsStatus(uploadRow.rto_queue_id);
        setUploadMissing(st.missing ?? []);
      } else {
        setUploadError(res.error ?? "Upload failed");
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploadSubmitting(false);
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

  const inProcessColumns = [
    "Queue ID",
    "RTO App ID",
    "Customer Name",
    "Mobile",
    "Chassis No.",
    "RTO Amount",
    "Status",
    "In Queue",
    "Locked By",
    "Actions",
  ];

  const formsMissingColumns = [
    "Queue ID",
    "Customer Name",
    "Mobile",
    "Missing Forms",
    "Actions",
  ];

  const completedColumns = [
    "Queue ID",
    "RTO App ID",
    "Customer Name",
    "Mobile",
    "Status",
    "Actions",
  ];

  const columns =
    subTab === "in_process"
      ? inProcessColumns
      : subTab === "forms_missing"
        ? formsMissingColumns
        : completedColumns;

  return (
    <div className="rto-payments-page">
      {error && <p className="rto-payments-error">{error}</p>}

      <nav className="challans-subtabs" role="tablist" aria-label="RTO Queue">
        <button
          type="button"
          role="tab"
          aria-selected={subTab === "in_process"}
          className={`challans-subtab ${subTab === "in_process" ? "active" : ""}`}
          onClick={() => setSubTab("in_process")}
        >
          In-process
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={subTab === "forms_missing"}
          className={`challans-subtab ${subTab === "forms_missing" ? "active" : ""}`}
          onClick={() => setSubTab("forms_missing")}
        >
          Forms Missing
          {formsMissingCount > 0 ? (
            <span className="app-tab-badge app-tab-badge--danger"> ({formsMissingCount})</span>
          ) : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={subTab === "completed"}
          className={`challans-subtab ${subTab === "completed" ? "active" : ""}`}
          onClick={() => setSubTab("completed")}
        >
          Completed
        </button>
      </nav>

      {subTab === "in_process" && (
        <>
          <div className="rto-batch-toolbar">
            <div className="rto-batch-toolbar-actions">
              <button
                type="button"
                className="app-button app-button--primary"
                onClick={handleStartBatch}
                disabled={
                  startingBatch ||
                  batchStatus?.state === "running" ||
                  batchStatus?.state === "starting"
                }
                title={
                  vahanReadyForBatch
                    ? "Log in on Vahan, then press again to run the batch"
                    : "Open or attach Vahan; starts batch immediately when already logged in"
                }
              >
                {startingBatch ||
                batchStatus?.state === "running" ||
                batchStatus?.state === "starting"
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
                <>
                  Batch processes up to 7 oldest rows with <strong>In Queue</strong> checked.
                  Uncheck rows to skip them for now.
                  {!isElectron() ? (
                    <span className="rto-batch-dev-hint">
                      {" "}
                      Dev: uses local uploads folder on the API host (no cloud download).
                    </span>
                  ) : null}
                </>
              )}
              </span>
            </div>
          </div>
          {batchStatus && batchStatus.state !== "idle" && (
            <section className="rto-batch-status-card">
              <div className="app-process-status-bar">
                <span className="app-process-count">
                  Processing: {batchStatus.processed_count}/{batchStatus.total_count}, Completed:{" "}
                  {batchStatus.completed_count}, Failed: {batchStatus.failed_count}
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
                <span className="rto-otp-mobile-label">
                  Mobile OTP is sent to (call this number to ask for OTP):
                </span>{" "}
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
                    <strong>Inward Application (Partial Save)</strong> again so a new OTP goes to the
                    new number.
                  </p>
                  <label className="rto-otp-label">
                    New mobile number
                    <input
                      className="rto-otp-input"
                      type="tel"
                      inputMode="numeric"
                      autoComplete="tel"
                      value={mobileChangeInput}
                      onChange={(ev) =>
                        setMobileChangeInput(ev.target.value.replace(/\D/g, "").slice(0, 10))
                      }
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
        </>
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
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>No records.</td>
              </tr>
            ) : subTab === "in_process" ? (
              filteredRows.map((r) => {
                const needsTrc = r.status === NEEDS_TRC_STATUS;
                return (
                <tr
                  key={r.rto_queue_id}
                  className={needsTrc ? "rto-queue-row--needs-trc" : undefined}
                >
                  <td>{r.rto_queue_id}</td>
                  <td>{r.rto_application_id ?? "—"}</td>
                  <td>{r.customer_name ?? "—"}</td>
                  <td>{r.customer_mobile ?? r.mobile ?? "—"}</td>
                  <td>{r.chassis_num ?? "—"}</td>
                  <td>{r.rto_payment_amount != null ? `₹${r.rto_payment_amount}` : "—"}</td>
                  <td>{r.status ?? "Pending"}</td>
                  <td>
                    <label
                      className="rto-in-queue-label"
                      title={
                        needsTrc
                          ? "Needs TRC — out of state"
                          : r.status === "In Progress"
                            ? "Release this row first"
                            : undefined
                      }
                    >
                      <input
                        type="checkbox"
                        checked={needsTrc ? false : r.in_queue !== false}
                        disabled={needsTrc || r.status === "In Progress"}
                        onChange={(ev) => void handleInQueueChange(r, ev.target.checked)}
                      />
                      <span>In Queue</span>
                    </label>
                  </td>
                  <td>{r.locked_by_name ?? "—"}</td>
                  <td className="rto-payments-actions">
                    {r.status === "In Progress" || r.status === "Pending" ? (
                      <button
                        type="button"
                        className="app-button app-button--primary"
                        onClick={() => void handleRelease(r.rto_queue_id)}
                        disabled={actionQueueId === r.rto_queue_id}
                      >
                        {actionQueueId === r.rto_queue_id ? "…" : "Release"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="app-button"
                        onClick={() => void handleMarkDone(r.rto_queue_id)}
                        disabled={needsTrc || actionQueueId === r.rto_queue_id}
                        title={needsTrc ? "Needs TRC — out of state" : undefined}
                      >
                        {actionQueueId === r.rto_queue_id ? "…" : "Mark Done"}
                      </button>
                    )}
                    {String(r.status || "").toLowerCase() === "failed" ? (
                      <button
                        type="button"
                        className="app-button"
                        onClick={() => handleTryAgain(r.rto_queue_id)}
                        disabled={retryingQueueId === r.rto_queue_id}
                      >
                        {retryingQueueId === r.rto_queue_id ? "Retrying..." : "Retry"}
                      </button>
                    ) : null}
                  </td>
                </tr>
                );
              })
            ) : subTab === "forms_missing" ? (
              filteredRows.map((r) => (
                <tr key={r.rto_queue_id}>
                  <td>{r.rto_queue_id}</td>
                  <td>{r.customer_name ?? "—"}</td>
                  <td>{r.customer_mobile ?? r.mobile ?? "—"}</td>
                  <td className="rto-missing-forms-cell">{r.last_error ?? "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="app-button app-button--primary"
                      onClick={() => void openUploadForms(r)}
                    >
                      Upload Forms
                    </button>
                  </td>
                </tr>
              ))
            ) : (
              filteredRows.map((r) => (
                <tr key={r.rto_queue_id}>
                  <td>{r.rto_queue_id}</td>
                  <td>{r.rto_application_id ?? "—"}</td>
                  <td>{r.customer_name ?? "—"}</td>
                  <td>{r.customer_mobile ?? r.mobile ?? "—"}</td>
                  <td>{r.status}</td>
                  <td>
                    <button
                      type="button"
                      className="app-button"
                      onClick={() => void handleRequeue(r.rto_queue_id)}
                      disabled={actionQueueId === r.rto_queue_id}
                    >
                      {actionQueueId === r.rto_queue_id ? "…" : "Send to Queue"}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {uploadRow && (
        <div className="rto-upload-forms-overlay" role="dialog" aria-modal="true" aria-labelledby="rto-upload-forms-title">
          <div className="rto-upload-forms-dialog">
            <h3 id="rto-upload-forms-title">Upload Forms — {uploadRow.customer_name ?? uploadRow.rto_queue_id}</h3>
            <p className="rto-upload-forms-hint">
              {isElectron()
                ? "Files are saved locally, merged Form 20 with cover when applicable, and pushed to the server before returning to In-process."
                : "Dev: files are saved to the local uploads folder on the API host (no cloud sync)."}
            </p>
            <form onSubmit={handleUploadFormsSubmit}>
              {uploadFormsLoading ? (
                <p>Loading missing forms…</p>
              ) : uploadMissing.length === 0 ? (
                <p>All required forms are already on disk. Close this dialog or use Upload & check to re-verify.</p>
              ) : (
                uploadMissing.map((item) => (
                  <label key={item.key} className="rto-upload-forms-field">
                    <span>{item.label}</span>
                    <input
                      type="file"
                      accept={acceptForCategory(item.key)}
                      onChange={(ev) => {
                        const f = ev.target.files?.[0] ?? null;
                        setUploadFiles((prev) => ({ ...prev, [item.key]: f }));
                      }}
                    />
                  </label>
                ))
              )}
              {uploadError && <p className="rto-payments-error">{uploadError}</p>}
              <div className="rto-upload-forms-actions">
                <button
                  type="button"
                  className="app-button"
                  onClick={closeUploadForms}
                  disabled={uploadSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="app-button app-button--primary"
                  disabled={uploadSubmitting || uploadFormsLoading}
                >
                  {uploadSubmitting
                    ? "Uploading…"
                    : uploadMissing.length === 0
                      ? "Re-verify & queue"
                      : "Upload & check"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
