import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAdminDealerNames, type AdminDealerNameRow } from "../api/adminDealers";
import {
  cancelAdminStagingInvoice,
  getAdminStagingDetail,
  markAdminInsuranceManuallyFilled,
  searchAdminStaging,
  type AdminStagingDetailResponse,
  type AdminStagingSearchRow,
} from "../api/admin";
import { resolvePortalInsurer } from "../utils/addSalesInsurerResolve";

function formatUpdatedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
}

function customerConfirmationLabel(detail: AdminStagingDetailResponse | null): string {
  if (!detail) return "";
  const cust = detail.payload_json.customer;
  if (!cust || typeof cust !== "object") return "";
  const rec = cust as Record<string, unknown>;
  const name = String(rec.name ?? "").trim();
  if (name) return name;
  for (const key of ["mobile_number", "mobile", "phone"]) {
    const mob = String(rec[key] ?? "").trim();
    if (mob) return mob;
  }
  return "";
}

function confirmationMatchesTyped(expected: string, typed: string): boolean {
  const e = expected.trim();
  const t = typed.trim();
  if (!e || !t) return false;
  return e.toLowerCase() === t.toLowerCase();
}

export function AdminStagingToolsPanel() {
  const [dealers, setDealers] = useState<AdminDealerNameRow[]>([]);
  const [dealerId, setDealerId] = useState<number | "">("");
  const [mobileInput, setMobileInput] = useState("");
  const [rows, setRows] = useState<AdminStagingSearchRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminStagingDetailResponse | null>(null);
  const [insurer, setInsurer] = useState("");
  const [policyNum, setPolicyNum] = useState("");
  const [searchBusy, setSearchBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ text: string; success: boolean } | null>(null);
  const [dealersErr, setDealersErr] = useState<string | null>(null);
  const [cancelModalOpen, setCancelModalOpen] = useState(false);
  const [cancelTyped, setCancelTyped] = useState("");
  const cancelConfirmInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await getAdminDealerNames();
        if (cancelled) return;
        setDealers(list);
        if (list.length > 0) {
          setDealerId((prev) => {
            if (typeof prev === "number" && list.some((d) => d.dealer_id === prev)) return prev;
            return list[0].dealer_id;
          });
        }
      } catch (e) {
        if (!cancelled) {
          setDealersErr(e instanceof Error ? e.message : "Failed to load dealers");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const mobileDigits = useMemo(
    () => mobileInput.replace(/\D/g, ""),
    [mobileInput]
  );

  const loadDetail = useCallback(async (stagingId: string, did: number) => {
    setDetailBusy(true);
    setActionMsg(null);
    try {
      const d = await getAdminStagingDetail(did, stagingId);
      setDetail(d);
      const insBlock = d.payload_json.insurance;
      const insName =
        insBlock && typeof insBlock === "object"
          ? String((insBlock as Record<string, unknown>).insurer ?? "").trim()
          : "";
      const resolved = resolvePortalInsurer(insName, null, d.portal_insurers) ?? insName;
      setInsurer(resolved || d.portal_insurers[0] || "");
      const pn =
        insBlock && typeof insBlock === "object"
          ? String((insBlock as Record<string, unknown>).policy_num ?? "").trim()
          : "";
      setPolicyNum(pn);
    } catch (e) {
      setDetail(null);
      setActionMsg({
        text: e instanceof Error ? e.message : "Failed to load staging detail",
        success: false,
      });
    } finally {
      setDetailBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId || typeof dealerId !== "number") {
      setDetail(null);
      return;
    }
    void loadDetail(selectedId, dealerId);
  }, [selectedId, dealerId, loadDetail]);

  const runSearch = useCallback(
    async (opts?: { keepSelection?: boolean }) => {
      if (typeof dealerId !== "number") return;
      if (mobileDigits.length < 10) {
        setSearchErr("Enter at least 10 digits for mobile");
        return;
      }
      setSearchBusy(true);
      setSearchErr(null);
      if (!opts?.keepSelection) {
        setActionMsg(null);
        setSelectedId(null);
        setDetail(null);
      }
      try {
        const res = await searchAdminStaging(dealerId, mobileDigits);
        setRows(res.rows);
        if (!opts?.keepSelection && res.rows.length === 1) {
          setSelectedId(res.rows[0].staging_id);
        }
      } catch (e) {
        setRows([]);
        setSearchErr(e instanceof Error ? e.message : "Search failed");
      } finally {
        setSearchBusy(false);
      }
    },
    [dealerId, mobileDigits]
  );

  async function handleSearch() {
    await runSearch();
  }

  const hasSalesId = useMemo(() => {
    if (!detail) return false;
    const raw = detail.payload_json.sales_id;
    const n = parseInt(String(raw ?? "").trim(), 10);
    return !Number.isNaN(n) && n > 0;
  }, [detail]);

  const cancelConfirmation = customerConfirmationLabel(detail);

  const cancelTypedMatches = useMemo(
    () => confirmationMatchesTyped(cancelConfirmation, cancelTyped),
    [cancelConfirmation, cancelTyped]
  );

  useEffect(() => {
    if (!cancelModalOpen) return;
    const t = window.setTimeout(() => cancelConfirmInputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [cancelModalOpen]);

  function openCancelInvoiceModal() {
    if (!selectedId || typeof dealerId !== "number" || !detail) return;
    if (!cancelConfirmation) {
      setActionMsg({ text: "No customer name or mobile on staging row for confirmation.", success: false });
      return;
    }
    setCancelTyped("");
    setCancelModalOpen(true);
  }

  function closeCancelInvoiceModal(opts?: { aborted?: boolean }) {
    setCancelModalOpen(false);
    setCancelTyped("");
    if (opts?.aborted) {
      setActionMsg({ text: "Cancel Invoice aborted.", success: false });
    }
  }

  async function submitCancelInvoice() {
    if (!selectedId || typeof dealerId !== "number" || !detail || !cancelConfirmation) return;
    if (!cancelTypedMatches) return;
    setCancelModalOpen(false);
    setActionBusy(true);
    setActionMsg(null);
    const typed = cancelTyped.trim();
    setCancelTyped("");
    try {
      const res = await cancelAdminStagingInvoice(dealerId, selectedId, typed);
      setActionMsg({
        text: `Invoice cancelled in Saathi DB. Staging reset: ${res.staging_reset ? "yes" : "no"}.`,
        success: true,
      });
      await runSearch({ keepSelection: true });
      await loadDetail(selectedId, dealerId);
    } catch (e) {
      setActionMsg({
        text: e instanceof Error ? e.message : "Cancel invoice failed",
        success: false,
      });
    } finally {
      setActionBusy(false);
    }
  }

  async function handleInsuranceManuallyFilled() {
    if (!selectedId || typeof dealerId !== "number" || !insurer.trim() || !policyNum.trim()) return;
    const ok = window.confirm(
      `Record portal-only manual issue?\n\nInsurer: ${insurer}\nPolicy #: ${policyNum.trim()}\n\n` +
        `Requires insurance_state=0. Sets insurance_state=2 so operators can run Gen. Insurance from Add Sales In-process (Print Policy / PDF only).`
    );
    if (!ok) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await markAdminInsuranceManuallyFilled(
        dealerId,
        selectedId,
        insurer.trim(),
        policyNum.trim()
      );
      setActionMsg({
        text: "Portal manual issue recorded (insurance_state=2). Operator can run Gen. Insurance from In-process for Print Policy / PDF.",
        success: true,
      });
      await runSearch({ keepSelection: true });
      await loadDetail(selectedId, dealerId);
    } catch (e) {
      setActionMsg({
        text: e instanceof Error ? e.message : "Ins. Manually Filled failed",
        success: false,
      });
    } finally {
      setActionBusy(false);
    }
  }

  const payload = detail?.payload_json;
  const cust =
    payload?.customer && typeof payload.customer === "object"
      ? (payload.customer as Record<string, unknown>)
      : null;
  const veh =
    payload?.vehicle && typeof payload.vehicle === "object"
      ? (payload.vehicle as Record<string, unknown>)
      : null;

  const manualFillDisabled =
    actionBusy ||
    detailBusy ||
    !detail ||
    !hasSalesId ||
    !insurer.trim() ||
    !policyNum.trim() ||
    detail.insurance_state !== 0;

  return (
    <div className="admin-staging-tools">
      <h2 className="admin-staging-tools-title">Staging search</h2>
      {dealersErr ? (
        <p className="add-sales-in-process-err" role="alert">
          {dealersErr}
        </p>
      ) : null}

      <div className="admin-staging-tools-search">
        <label className="admin-staging-tools-field">
          <span>Dealer</span>
          <select
            className="add-sales-v2-dl-input"
            value={dealerId === "" ? "" : String(dealerId)}
            onChange={(e) => setDealerId(e.target.value ? Number(e.target.value) : "")}
            disabled={searchBusy || actionBusy}
          >
            {dealers.map((d) => (
              <option key={d.dealer_id} value={d.dealer_id}>
                {d.dealer_name} ({d.dealer_id})
              </option>
            ))}
          </select>
        </label>
        <label className="admin-staging-tools-field">
          <span>Mobile</span>
          <input
            type="tel"
            className="add-sales-v2-dl-input"
            value={mobileInput}
            onChange={(e) => setMobileInput(e.target.value)}
            placeholder="10-digit mobile"
            disabled={searchBusy || actionBusy}
          />
        </label>
        <button
          type="button"
          className="app-button"
          onClick={() => void handleSearch()}
          disabled={searchBusy || actionBusy || typeof dealerId !== "number" || mobileDigits.length < 10}
        >
          {searchBusy ? "Searching…" : "Search"}
        </button>
      </div>

      {searchErr ? (
        <p className="add-sales-in-process-err" role="alert">
          {searchErr}
        </p>
      ) : null}

      <div className="add-sales-in-process-split admin-staging-tools-split">
        <div className="add-sales-in-process-table-wrap">
          <table className="add-sales-in-process-table">
            <thead>
              <tr>
                <th>Updated</th>
                <th>Status</th>
                <th>Customer</th>
                <th>Mobile</th>
                <th>DMS</th>
                <th>Ins.</th>
                <th>RTO</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={7} className="add-sales-in-process-empty">
                    {searchBusy ? "Searching…" : "No rows — search by dealer and mobile"}
                  </td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr
                    key={r.staging_id}
                    className={
                      selectedId === r.staging_id ? "add-sales-in-process-tr--selected" : undefined
                    }
                    onClick={() => setSelectedId(r.staging_id)}
                  >
                    <td>{formatUpdatedAt(r.updated_at)}</td>
                    <td>{r.status}</td>
                    <td>{r.customer_name ?? "—"}</td>
                    <td>{r.mobile ?? "—"}</td>
                    <td>{r.dms_state}</td>
                    <td>{r.insurance_state}</td>
                    <td>{r.has_rto_queue ? "Yes" : "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="add-sales-in-process-detail admin-staging-tools-detail">
          <h3 className="add-sales-in-process-detail-title">Staging row</h3>
          {!selectedId ? (
            <p className="add-sales-in-process-detail-hint">Select a row from the table above.</p>
          ) : detailBusy ? (
            <p className="add-sales-in-process-detail-hint">Loading…</p>
          ) : detail ? (
            <>
              {actionMsg ? (
                <p
                  className={
                    actionMsg.success ? "add-sales-in-process-ok" : "add-sales-in-process-err"
                  }
                  role="status"
                >
                  {actionMsg.text}
                </p>
              ) : null}

              <dl className="add-sales-v2-dl add-sales-v2-dl--compact">
                <div className="add-sales-v2-dl-row">
                  <dt>Staging ID</dt>
                  <dd className="add-sales-in-process-dd--readonly">{detail.staging_id}</dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Status</dt>
                  <dd className="add-sales-in-process-dd--readonly">{detail.status}</dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Customer</dt>
                  <dd className="add-sales-in-process-dd--readonly">
                    {String(cust?.name ?? "—")}
                  </dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Mobile</dt>
                  <dd className="add-sales-in-process-dd--readonly">
                    {String(cust?.mobile_number ?? cust?.mobile ?? "—")}
                  </dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Chassis / Engine</dt>
                  <dd className="add-sales-in-process-dd--readonly">
                    {String(veh?.frame_no ?? "—")} / {String(veh?.engine_no ?? "—")}
                  </dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Sales ID</dt>
                  <dd className="add-sales-in-process-dd--readonly">
                    {String(payload?.sales_id ?? "—")}
                  </dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>dms_state / insurance_state</dt>
                  <dd className="add-sales-in-process-dd--readonly">
                    {detail.dms_state} / {detail.insurance_state}
                  </dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Insurer</dt>
                  <dd>
                    {detail.portal_insurers.length > 0 ? (
                      <select
                        className="add-sales-v2-dl-input add-sales-v2-dl-input--insurance-provider-wide"
                        value={insurer}
                        onChange={(e) => setInsurer(e.target.value)}
                        disabled={actionBusy}
                      >
                        {detail.portal_insurers.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="add-sales-in-process-dd--readonly">No portal insurers</span>
                    )}
                  </dd>
                </div>
                <div className="add-sales-v2-dl-row">
                  <dt>Policy #</dt>
                  <dd>
                    <input
                      type="text"
                      className="add-sales-v2-dl-input add-sales-v2-dl-input--insurance-provider-wide"
                      value={policyNum}
                      onChange={(e) => setPolicyNum(e.target.value)}
                      placeholder="Issued policy number from portal"
                      disabled={actionBusy || detail.insurance_state !== 0}
                      maxLength={24}
                    />
                  </dd>
                </div>
              </dl>

              <div className="admin-staging-tools-actions">
                <button
                  type="button"
                  className="app-button app-button--small admin-danger-button"
                  onClick={openCancelInvoiceModal}
                  disabled={actionBusy || detailBusy}
                >
                  Cancel Invoice
                </button>
                <button
                  type="button"
                  className="app-button app-button--small"
                  onClick={() => void handleInsuranceManuallyFilled()}
                  disabled={manualFillDisabled}
                  title={
                    !hasSalesId
                      ? "Requires committed sales_id (Create Invoice complete)"
                      : detail.insurance_state !== 0
                        ? "Portal manual issue requires insurance_state=0 (use Gen. Insurance from In-process after automation submit)"
                        : !policyNum.trim()
                          ? "Enter the issued policy number from the portal"
                          : undefined
                  }
                >
                  Portal manual issue
                </button>
              </div>
              <p className="admin-staging-tools-portal-manual-note">
                This button allows admin to mark Insurance done on this app, when dealers have
                manually completed the insurance policy creation.
              </p>
            </>
          ) : null}
        </div>
      </div>

      {cancelModalOpen && cancelConfirmation ? (
        <div
          className="admin-staging-cancel-modal-backdrop"
          role="presentation"
          onClick={() => !actionBusy && closeCancelInvoiceModal({ aborted: true })}
        >
          <div
            className="admin-staging-cancel-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-staging-cancel-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="admin-staging-cancel-modal-title">Cancel Invoice</h3>
            <p className="admin-staging-cancel-modal-warning">
              This will <strong>delete</strong> Saathi database masters for this sale and reset the
              staging row. It does <strong>not</strong> cancel the invoice in Siebel/DMS — operators
              may need to cancel there separately.
            </p>
            <p className="admin-staging-cancel-modal-customer">
              Customer: <strong>{cancelConfirmation}</strong>
            </p>
            <label className="admin-staging-cancel-modal-field">
              Type exactly to confirm
              <input
                ref={cancelConfirmInputRef}
                type="text"
                className="add-sales-v2-dl-input"
                value={cancelTyped}
                onChange={(e) => setCancelTyped(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && cancelTypedMatches && !actionBusy) {
                    e.preventDefault();
                    void submitCancelInvoice();
                  }
                }}
                disabled={actionBusy}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <div className="admin-staging-cancel-modal-actions">
              <button
                type="button"
                className="app-button"
                onClick={() => closeCancelInvoiceModal({ aborted: true })}
                disabled={actionBusy}
              >
                Back
              </button>
              <button
                type="button"
                className="app-button admin-danger-button"
                onClick={() => void submitCancelInvoice()}
                disabled={actionBusy || !cancelTypedMatches}
              >
                {actionBusy ? "Cancelling…" : "Confirm cancel"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
