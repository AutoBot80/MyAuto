import { useCallback, useEffect, useMemo, useState } from "react";
import type { CpaInsurerPortalRow, CreateInvoiceEligibilityResponse } from "../api/addSales";
import {
  fetchAddSalesInProcess,
  fetchAddSalesStagingPayload,
  fetchCreateInvoiceEligibility,
  fetchDealerCpaContext,
  patchAddSalesStagingPayload,
  type AddSalesInProcessRow,
  type PatchAddSalesStagingPayloadBody,
} from "../api/addSales";
import {
  buildFillCpaAllianceInsuranceRequest,
  dispatchPrintJobsFromApi,
  fillCpaAllianceInsuranceLocal,
  fillDmsLocal,
  fillHeroInsuranceLocal,
} from "../api/fillForms";
import { runPrintQueueRtoFlow } from "../utils/printQueueRtoFlow";
import { buildDisplayAddress } from "../types";
import type { ExtractedCustomerDetails, ExtractedInsuranceDetails, ExtractedVehicleDetails } from "../types";
import { cpaPolicyFromInsuranceRaw } from "../utils/insuranceDisplay";
import { sanitizeFormFieldInputValue, sanitizeFormFieldValue } from "../utils/formFieldSanitize";

function formatTenDigitSegment(raw: unknown): string {
  const d = String(raw ?? "").replace(/\D/g, "").slice(-10);
  return d.length === 10 ? d : "";
}

/** Primary mobile / alternate as ``9999999999/8888888888``; ``—`` when neither valid. */
function formatMobileAlternateSlash(custRec: Record<string, unknown> | null | undefined): string {
  if (!custRec) return "—";
  const m = formatTenDigitSegment(custRec.mobile_number ?? custRec.mobile);
  const a = formatTenDigitSegment(custRec.alt_phone_num ?? custRec.alternate_no ?? custRec.alternate_mobile_number);
  if (m && a) return `${m}/${a}`;
  if (m) return m;
  if (a) return `—/${a}`;
  return "—";
}

function rowHasCommittedIds(r: AddSalesInProcessRow): boolean {
  const c = parseInt(String(r.customer_id_text ?? "").trim(), 10);
  const v = parseInt(String(r.vehicle_id_text ?? "").trim(), 10);
  return !Number.isNaN(c) && !Number.isNaN(v) && c > 0 && v > 0;
}

function formatHeroCpiDisplay(raw: unknown): string {
  const s = String(raw ?? "").trim();
  const v = s.slice(0, 1).toUpperCase();
  if (v === "Y") return "Yes";
  if (v === "N") return "No";
  return "—";
}

function pickCpaPortalRow(
  insurers: CpaInsurerPortalRow[],
  dealerCpa: string | null
): CpaInsurerPortalRow | undefined {
  const d = (dealerCpa ?? "").trim();
  if (d) {
    const byDealer = insurers.find((p) => p.ref_value === d);
    if (byDealer?.login_url?.trim()) return byDealer;
  }
  const first = insurers[0];
  return first?.login_url?.trim() ? first : undefined;
}

function payloadCustomer(rec: Record<string, unknown> | null): ExtractedCustomerDetails | null {
  if (!rec || typeof rec !== "object") return null;
  const c = rec as Record<string, unknown>;
  return {
    name: String(c.name ?? "").trim() || undefined,
    care_of: String(c.care_of ?? "").trim() || undefined,
    address: String(c.address ?? "").trim() || undefined,
    gender: String(c.gender ?? "").trim() || undefined,
    date_of_birth: String(c.date_of_birth ?? "").trim() || undefined,
    aadhar_id: String(c.aadhar_id ?? "").trim() || undefined,
    city: String(c.city ?? "").trim() || undefined,
    state: String(c.state ?? "").trim() || undefined,
    pin_code: String(c.pin ?? c.pin_code ?? "").trim() || undefined,
    financier: String(c.financier ?? "").trim() || undefined,
  };
}

function payloadVehicle(rec: Record<string, unknown> | null): ExtractedVehicleDetails | null {
  if (!rec || typeof rec !== "object") return null;
  const v = rec as Record<string, unknown>;
  return {
    frame_no: String(v.frame_no ?? "").trim() || undefined,
    engine_no: String(v.engine_no ?? "").trim() || undefined,
    key_no: String(v.key_no ?? "").trim() || undefined,
    battery_no: String(v.battery_no ?? "").trim() || undefined,
    order_number: String(v.order_number ?? "").trim() || undefined,
    invoice_number: String(v.invoice_number ?? "").trim() || undefined,
  };
}

/** Editable subset of In-process Sales Details (maps to PATCH whitelist). */
interface InProcessDetailDraft {
  care_of: string;
  address: string;
  frame_no: string;
  engine_no: string;
  key_no: string;
  battery_no: string;
  nominee_name: string;
  nominee_relationship: string;
}

function buildDraftFromPayload(payload: Record<string, unknown> | null): InProcessDetailDraft | null {
  if (!payload) return null;
  const cust = payloadCustomer((payload.customer as Record<string, unknown>) ?? null);
  const veh = payloadVehicle((payload.vehicle as Record<string, unknown>) ?? null);
  const ins = payloadInsurance((payload.insurance as Record<string, unknown>) ?? null);
  const address =
    cust?.address?.trim() ? cust.address : cust ? buildDisplayAddress(cust) : "";
  return {
    care_of: cust?.care_of?.trim() ?? "",
    address: address.trim(),
    frame_no: veh?.frame_no?.trim() ?? "",
    engine_no: veh?.engine_no?.trim() ?? "",
    key_no: veh?.key_no?.trim() ?? "",
    battery_no: veh?.battery_no?.trim() ?? "",
    nominee_name: ins?.nominee_name?.trim() ?? "",
    nominee_relationship: ins?.nominee_relationship?.trim() ?? "",
  };
}

function draftsEqual(a: InProcessDetailDraft, b: InProcessDetailDraft): boolean {
  return (
    a.care_of === b.care_of &&
    a.address === b.address &&
    a.frame_no === b.frame_no &&
    a.engine_no === b.engine_no &&
    a.key_no === b.key_no &&
    a.battery_no === b.battery_no &&
    a.nominee_name === b.nominee_name &&
    a.nominee_relationship === b.nominee_relationship
  );
}

function draftToPatchBody(draft: InProcessDetailDraft): PatchAddSalesStagingPayloadBody {
  return {
    customer: { care_of: draft.care_of, address: draft.address },
    vehicle: {
      frame_no: draft.frame_no,
      engine_no: draft.engine_no,
      key_no: draft.key_no,
      battery_no: draft.battery_no,
    },
    insurance: {
      nominee_name: draft.nominee_name,
      nominee_relationship: draft.nominee_relationship,
    },
  };
}

function payloadInsurance(rec: Record<string, unknown> | null): ExtractedInsuranceDetails | null {
  if (!rec || typeof rec !== "object") return null;
  const i = rec as Record<string, unknown>;
  return {
    insurer: String(i.insurer ?? "").trim() || undefined,
    policy_num: String(i.policy_num ?? "").trim() || undefined,
    nominee_name: String(i.nominee_name ?? "").trim() || undefined,
    nominee_age: i.nominee_age != null ? String(i.nominee_age) : undefined,
    nominee_relationship: String(i.nominee_relationship ?? "").trim() || undefined,
  };
}

export interface AddSalesInProcessPanelProps {
  dealerId: number;
  dmsUrl: string;
  siteUrlsLoading?: boolean;
  siteUrlsError?: string | null;
  preferInsurer?: string | null;
  /** True when the In-process sub-tab is visible (drives default row selection). */
  inProcessTabActive: boolean;
  addSalesMainTabActive: boolean;
  mainLastStagingId: string | null;
  pageActionsBusy: boolean;
  onRowActionStart: (stagingId: string) => void;
  onRowActionEnd: () => void;
  onInProcessCountChange?: (count: number) => void;
}

export function AddSalesInProcessPanel({
  dealerId,
  dmsUrl,
  siteUrlsLoading,
  siteUrlsError,
  preferInsurer = null,
  inProcessTabActive,
  addSalesMainTabActive,
  mainLastStagingId,
  pageActionsBusy,
  onRowActionStart,
  onRowActionEnd,
  onInProcessCountChange,
}: AddSalesInProcessPanelProps) {
  const [rows, setRows] = useState<AddSalesInProcessRow[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailPayload, setDetailPayload] = useState<Record<string, unknown> | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [busyRowId, setBusyRowId] = useState<string | null>(null);
  const [rowMsg, setRowMsg] = useState<{ text: string; success: boolean } | null>(null);
  const [elig, setElig] = useState<CreateInvoiceEligibilityResponse | null>(null);
  const [eligLoading, setEligLoading] = useState(false);

  const [cpaInsurers, setCpaInsurers] = useState<CpaInsurerPortalRow[]>([]);
  const [dealerCpaInsurer, setDealerCpaInsurer] = useState<string | null>(null);
  const [dealerHeroCpi, setDealerHeroCpi] = useState<string | null>(null);
  const [panelRefreshToken, setPanelRefreshToken] = useState(0);
  const [isPanelRefreshing, setIsPanelRefreshing] = useState(false);
  const [detailEditDraft, setDetailEditDraft] = useState<InProcessDetailDraft | null>(null);
  const [detailSaveBusy, setDetailSaveBusy] = useState(false);

  const refreshList = useCallback(async () => {
    if (dealerId <= 0) return;
    setLoadErr(null);
    try {
      const r = await fetchAddSalesInProcess(dealerId, 7);
      setRows(r.rows ?? []);
      onInProcessCountChange?.(r.count ?? (r.rows?.length ?? 0));
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "Failed to load In-Process list.");
    }
  }, [dealerId, onInProcessCountChange]);

  const reloadDealerCpaContext = useCallback(async () => {
    if (dealerId <= 0) return;
    try {
      const r = await fetchDealerCpaContext(dealerId);
      setCpaInsurers(r.cpa_insurers ?? []);
      setDealerCpaInsurer(r.dealer_cpa_insurer?.trim() ? r.dealer_cpa_insurer.trim() : null);
      const hc = r.hero_cpi;
      setDealerHeroCpi(hc != null && String(hc).trim() !== "" ? String(hc).trim() : null);
    } catch {
      setCpaInsurers([]);
      setDealerCpaInsurer(null);
      setDealerHeroCpi(null);
    }
  }, [dealerId]);

  const handlePanelRefresh = useCallback(async () => {
    if (isPanelRefreshing) return;
    setIsPanelRefreshing(true);
    setRowMsg(null);
    try {
      await refreshList();
      await reloadDealerCpaContext();
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "Refresh failed.");
    } finally {
      setPanelRefreshToken((t) => t + 1);
      setIsPanelRefreshing(false);
    }
  }, [isPanelRefreshing, refreshList, reloadDealerCpaContext]);

  useEffect(() => {
    void refreshList();
    const t = setInterval(() => void refreshList(), 60000);
    return () => clearInterval(t);
  }, [refreshList]);

  /** On In-process tab open, select the first row; keep selection if still present after refresh. */
  useEffect(() => {
    if (!inProcessTabActive) return;
    if (rows.length === 0) {
      setSelectedId(null);
      return;
    }
    setSelectedId(rows[0].staging_id);
  }, [inProcessTabActive]);

  useEffect(() => {
    if (!inProcessTabActive || rows.length === 0) return;
    setSelectedId((prev) => {
      if (prev && rows.some((r) => r.staging_id === prev)) return prev;
      return rows[0].staging_id;
    });
  }, [rows, inProcessTabActive]);

  useEffect(() => {
    void reloadDealerCpaContext();
  }, [reloadDealerCpaContext]);

  useEffect(() => {
    if (!selectedId || dealerId <= 0) {
      setDetailPayload(null);
      setDetailErr(null);
      return;
    }
    let c = false;
    setDetailErr(null);
    void (async () => {
      try {
        const r = await fetchAddSalesStagingPayload(selectedId, dealerId);
        if (c) return;
        setDetailPayload(r.payload_json ?? null);
      } catch (e) {
        if (!c) setDetailErr(e instanceof Error ? e.message : "Could not load row details.");
      }
    })();
    return () => {
      c = true;
    };
  }, [selectedId, dealerId, panelRefreshToken]);

  useEffect(() => {
    setDetailEditDraft(buildDraftFromPayload(detailPayload));
  }, [detailPayload, selectedId]);

  const detailDraftBaseline = useMemo(
    () => buildDraftFromPayload(detailPayload),
    [detailPayload]
  );
  const detailDirty = useMemo(() => {
    if (!detailEditDraft || !detailDraftBaseline) return false;
    return !draftsEqual(detailEditDraft, detailDraftBaseline);
  }, [detailEditDraft, detailDraftBaseline]);

  const onSaveDetailChanges = useCallback(async () => {
    if (!selectedId || !detailEditDraft || dealerId <= 0 || !detailDirty) return;
    setDetailSaveBusy(true);
    setDetailErr(null);
    try {
      await patchAddSalesStagingPayload(selectedId, dealerId, draftToPatchBody(detailEditDraft));
      setPanelRefreshToken((t) => t + 1);
      await refreshList();
      setRowMsg({ text: "Changes saved.", success: true });
    } catch (e) {
      setDetailErr(e instanceof Error ? e.message : "Could not save changes.");
    } finally {
      setDetailSaveBusy(false);
    }
  }, [selectedId, detailEditDraft, dealerId, detailDirty, refreshList]);

  const saveDetailDisabled =
    !detailDirty ||
    detailSaveBusy ||
    pageActionsBusy ||
    busyRowId != null ||
    !selectedId ||
    !detailPayload;

  const cust = useMemo(() => payloadCustomer((detailPayload?.customer as Record<string, unknown>) ?? null), [detailPayload]);
  const veh = useMemo(() => payloadVehicle((detailPayload?.vehicle as Record<string, unknown>) ?? null), [detailPayload]);
  const ins = useMemo(() => payloadInsurance((detailPayload?.insurance as Record<string, unknown>) ?? null), [detailPayload]);
  const mobileDigits = useMemo(() => {
    const m = detailPayload?.customer as Record<string, unknown> | undefined;
    const raw = m?.mobile_number;
    return String(raw ?? "").replace(/\D/g, "").slice(-10);
  }, [detailPayload]);

  const portalList = elig?.portal_insurers ?? [];
  const insTrim = String(ins?.insurer ?? "").trim();
  const insuranceProviderDisplay =
    portalList.length > 0
      ? portalList.includes(insTrim)
        ? insTrim || "—"
        : (insTrim || String(preferInsurer ?? "").trim() || "—")
      : insTrim || String(preferInsurer ?? "").trim() || "—";

  useEffect(() => {
    if (!selectedId || !detailPayload) {
      setElig(null);
      return;
    }
    const cid = parseInt(String(detailPayload.customer_id ?? "").trim(), 10);
    const vid = parseInt(String(detailPayload.vehicle_id ?? "").trim(), 10);
    const vrec = detailPayload.vehicle as Record<string, unknown> | undefined;
    const ch = String(vrec?.frame_no ?? "").trim();
    const eng = String(vrec?.engine_no ?? "").trim();
    const mob = mobileDigits.length >= 10 ? mobileDigits : "";
    let cancelled = false;
    setEligLoading(true);
    void (async () => {
      try {
        const res =
          !Number.isNaN(cid) &&
          !Number.isNaN(vid) &&
          cid > 0 &&
          vid > 0
            ? await fetchCreateInvoiceEligibility({
                customerId: cid,
                vehicleId: vid,
                dealerId: dealerId > 0 ? dealerId : undefined,
              })
            : ch && eng && mob.length >= 10
              ? await fetchCreateInvoiceEligibility({
                  chassisNum: ch,
                  engineNum: eng,
                  mobile: mob,
                  dealerId: dealerId > 0 ? dealerId : undefined,
                })
              : null;
        if (!cancelled && res) setElig(res);
        else if (!cancelled) setElig(null);
      } catch {
        if (!cancelled) setElig(null);
      } finally {
        if (!cancelled) setEligLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, detailPayload, dealerId, mobileDigits, panelRefreshToken]);

  const subfolder = useMemo(() => String(detailPayload?.file_location ?? "").trim(), [detailPayload]);

  const wrapRowAction = useCallback(
    async (stagingId: string, fn: () => Promise<void>) => {
      if (busyRowId != null || pageActionsBusy) {
        setRowMsg({
          text: pageActionsBusy
            ? "Another sale action is still running. Wait for it to finish, then try again."
            : "This row is busy. Wait for the current action to finish.",
          success: false,
        });
        return;
      }
      setBusyRowId(stagingId);
      onRowActionStart(stagingId);
      setRowMsg(null);
      try {
        await fn();
      } catch (e) {
        setRowMsg({ text: e instanceof Error ? e.message : String(e), success: false });
      } finally {
        await refreshList();
        setPanelRefreshToken((t) => t + 1);
        setBusyRowId(null);
        onRowActionEnd();
      }
    },
    [pageActionsBusy, onRowActionStart, onRowActionEnd, refreshList, busyRowId]
  );

  const createInvoiceCompleted = Boolean(elig && !elig.create_invoice_enabled && elig.invoice_recorded);
  const generateInsuranceCompleted = Boolean(elig && !elig.generate_insurance_enabled && elig.invoice_recorded);
  const hasSuppliedInsuranceDoc = false;
  const hasCommittedSaleIds =
    detailPayload != null &&
    parseInt(String(detailPayload.customer_id ?? "").trim(), 10) > 0 &&
    parseInt(String(detailPayload.vehicle_id ?? "").trim(), 10) > 0;

  const createInvoicePrimaryDisabled =
    pageActionsBusy ||
    eligLoading ||
    createInvoiceCompleted ||
    !(elig?.create_invoice_enabled ?? false) ||
    !!siteUrlsLoading ||
    !!siteUrlsError ||
    !dmsUrl?.trim();

  const generateInsurancePrimaryDisabled =
    pageActionsBusy ||
    eligLoading ||
    !hasCommittedSaleIds ||
    generateInsuranceCompleted ||
    !(elig?.generate_insurance_enabled ?? false) ||
    hasSuppliedInsuranceDoc ||
    !!siteUrlsLoading ||
    !!siteUrlsError;

  const cpaAlliancePortalEnabled = Boolean(elig?.cpa_alliance_portal_enabled);
  const cpaInsurersFromElig = elig?.cpa_insurers ?? cpaInsurers;
  const dealerCpaFromElig = elig?.dealer_cpa_insurer ?? dealerCpaInsurer;
  const cpaPortal = pickCpaPortalRow(cpaInsurersFromElig ?? [], dealerCpaFromElig ?? null);
  const cpaSelectedPortalUrl = (cpaPortal?.login_url ?? "").trim();

  const cpaPrimaryDisabled =
    pageActionsBusy ||
    eligLoading ||
    !hasCommittedSaleIds ||
    !cpaAlliancePortalEnabled ||
    !(cpaInsurersFromElig?.length) ||
    !cpaSelectedPortalUrl ||
    !(elig?.cpa_alliance_insurance_enabled ?? false) ||
    dealerId <= 0 ||
    !!siteUrlsLoading ||
    !!siteUrlsError;

  const cpaDisabledForRow = (r: AddSalesInProcessRow) =>
    pageActionsBusy ||
    busyRowId !== null ||
    !cpaAlliancePortalEnabled ||
    !(cpaInsurersFromElig?.length) ||
    !cpaSelectedPortalUrl ||
    dealerId <= 0 ||
    !!siteUrlsLoading ||
    !!siteUrlsError ||
    (selectedId === r.staging_id
      ? cpaPrimaryDisabled
      : !rowHasCommittedIds(r));

  /** Same as New tab: CPA is optional; Print unlocks after Create Invoice + Generate Insurance are inactive. */
  const printEnabled =
    !pageActionsBusy &&
    !eligLoading &&
    Boolean(detailPayload) &&
    createInvoicePrimaryDisabled &&
    generateInsurancePrimaryDisabled;

  const custRaw = useMemo(
    () => (detailPayload?.customer as Record<string, unknown> | undefined) ?? undefined,
    [detailPayload]
  );
  const mobileAlternateDisplay = useMemo(() => formatMobileAlternateSlash(custRaw ?? null), [custRaw]);
  const heroCpaSalesDisplay = useMemo(
    () => formatHeroCpiDisplay(elig?.hero_cpi ?? dealerHeroCpi),
    [elig?.hero_cpi, dealerHeroCpi]
  );
  const cpaProviderSalesDisplay = useMemo(() => {
    const portal = pickCpaPortalRow(cpaInsurersFromElig ?? [], dealerCpaFromElig ?? null);
    return (portal?.ref_value ?? dealerCpaFromElig ?? "").trim() || "—";
  }, [cpaInsurersFromElig, dealerCpaFromElig]);
  const cpaPolicySalesDisplay = useMemo(() => {
    const raw = detailPayload?.insurance as Record<string, unknown> | undefined;
    return cpaPolicyFromInsuranceRaw(raw) || "—";
  }, [detailPayload]);

  return (
    <div className="add-sales-in-process">
      {loadErr && (
        <p className="add-sales-in-process-err" role="alert">
          {loadErr}
        </p>
      )}
      {rowMsg && (
        <p
          className={rowMsg.success ? "add-sales-in-process-ok" : "add-sales-in-process-err"}
          role="status"
        >
          {rowMsg.text}
        </p>
      )}
      <div className="add-sales-in-process-split">
        <div className="add-sales-in-process-table-wrap">
          <table className="add-sales-in-process-table">
            <colgroup>
              <col className="add-sales-in-process-col add-sales-in-process-col--status" />
              <col className="add-sales-in-process-col add-sales-in-process-col--customer" />
              <col className="add-sales-in-process-col add-sales-in-process-col--mobile" />
              <col className="add-sales-in-process-col add-sales-in-process-col--chassis" />
              <col className="add-sales-in-process-col add-sales-in-process-col--engine" />
              <col className="add-sales-in-process-col add-sales-in-process-col--order" />
              <col className="add-sales-in-process-col add-sales-in-process-col--actions" />
            </colgroup>
            <thead>
              <tr>
                <th>Status</th>
                <th>Customer</th>
                <th>Mobile</th>
                <th>Chassis</th>
                <th>Engine</th>
                <th>Order #</th>
                <th className="add-sales-in-process-th-actions">
                  <span>Actions</span>
                  <button
                    type="button"
                    className="app-button app-button--small add-sales-in-process-refresh-btn"
                    aria-busy={isPanelRefreshing}
                    title="Reload in-process list and sales details"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handlePanelRefresh();
                    }}
                  >
                    {isPanelRefreshing ? "Refreshing…" : "Refresh"}
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const sel = selectedId === r.staging_id;
                const stLabel =
                  busyRowId === r.staging_id
                    ? "In-process"
                    : pageActionsBusy && mainLastStagingId === r.staging_id && addSalesMainTabActive
                      ? "In-process"
                      : "Queued";
                return (
                  <tr
                    key={r.staging_id}
                    className={sel ? "add-sales-in-process-tr--selected" : undefined}
                    onClick={() => setSelectedId(r.staging_id)}
                  >
                    <td>{stLabel}</td>
                    <td>{r.customer_name ?? "—"}</td>
                    <td>{r.mobile ?? "—"}</td>
                    <td>{r.chassis ?? "—"}</td>
                    <td>{r.engine ?? "—"}</td>
                    <td>{r.order_number ?? "—"}</td>
                    <td>
                      <div className="add-sales-in-process-actions">
                        <button
                          type="button"
                          className="app-button app-button--small"
                          disabled={createInvoicePrimaryDisabled || busyRowId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedId(r.staging_id);
                            void wrapRowAction(r.staging_id, async () => {
                              if (!dmsUrl.trim()) throw new Error("DMS URL missing.");
                              const res = await fillDmsLocal({ staging_id: r.staging_id });
                              if (!res.success) throw new Error(res.error ?? "Create Invoice failed.");
                              dispatchPrintJobsFromApi(res.print_jobs);
                            });
                          }}
                        >
                          Create Invoice
                        </button>
                        <button
                          type="button"
                          className="app-button app-button--small"
                          disabled={generateInsurancePrimaryDisabled || busyRowId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedId(r.staging_id);
                            void wrapRowAction(r.staging_id, async () => {
                              const res = await fillHeroInsuranceLocal({ staging_id: r.staging_id });
                              if (!res.success) throw new Error(res.error ?? "Generate Insurance failed.");
                              dispatchPrintJobsFromApi(res.print_jobs);
                            });
                          }}
                        >
                          Gen. Insurance
                        </button>
                        <button
                          type="button"
                          className="app-button app-button--small"
                          disabled={cpaDisabledForRow(r)}
                          title={
                            cpaDisabledForRow(r)
                              ? !rowHasCommittedIds(r) && selectedId !== r.staging_id
                                ? "Select this row or run Create Invoice first (customer/vehicle IDs required)."
                                : selectedId === r.staging_id &&
                                    elig?.cpa_alliance_insurance_reason &&
                                    !(elig?.cpa_alliance_insurance_enabled ?? false)
                                  ? elig.cpa_alliance_insurance_reason
                                  : "CPA Insurance is not available for this sale."
                              : "Open CPA Alliance portal for this sale."
                          }
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedId(r.staging_id);
                            void wrapRowAction(r.staging_id, async () => {
                              const portal = pickCpaPortalRow(cpaInsurersFromElig ?? [], dealerCpaFromElig ?? null);
                              if (!portal?.login_url) throw new Error("No CPA portal URL.");
                              const res = await fillCpaAllianceInsuranceLocal(
                                buildFillCpaAllianceInsuranceRequest({
                                  dealerId,
                                  portalUrl: portal.login_url,
                                  stagingId: r.staging_id,
                                })
                              );
                              if (!res.success) throw new Error(res.error ?? "CPA Insurance failed.");
                              setRowMsg({
                                text: res.certificate_number
                                  ? `CPA Insurance completed. Certificate: ${res.certificate_number}`
                                  : "CPA Insurance completed.",
                                success: true,
                              });
                            });
                          }}
                        >
                          CPA Insurance
                        </button>
                        <button
                          type="button"
                          className="app-button app-button--small"
                          disabled={!printEnabled || selectedId !== r.staging_id || busyRowId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
                            void wrapRowAction(r.staging_id, async () => {
                              const sf = subfolder || "default";
                              const vrec = detailPayload?.vehicle as Record<string, unknown> | undefined;
                              const vehicleData: Record<string, unknown> = {
                                key_no: vrec?.key_no,
                                frame_no: vrec?.frame_no,
                                engine_no: vrec?.engine_no,
                                model: vrec?.model,
                                colour: vrec?.colour ?? vrec?.color,
                                color: vrec?.color ?? vrec?.colour,
                                oem_name: vrec?.oem_name,
                              };
                              const vid = parseInt(String(detailPayload?.vehicle_id ?? "").trim(), 10);
                              const result = await runPrintQueueRtoFlow({
                                dealerId,
                                stagingId: r.staging_id,
                                subfolder: sf,
                                customer: {
                                  name: cust?.name,
                                  care_of: cust?.care_of,
                                  address: cust?.address ?? buildDisplayAddress(cust),
                                  city: cust?.city,
                                  state: cust?.state,
                                  pin_code: cust?.pin_code,
                                  aadhar_id: cust?.aadhar_id,
                                  mobile: mobileDigits || undefined,
                                },
                                vehicle: vehicleData,
                                vehicleId: Number.isNaN(vid) ? undefined : vid,
                              });
                              if (!result.success) {
                                throw new Error(result.statusLines.join(" ") || result.error || "Print / Queue RTO failed.");
                              }
                              setRowMsg({ text: result.statusLines.join(" "), success: true });
                            });
                          }}
                        >
                          Print/ Queue RTO
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && !loadErr && (
            <p className="add-sales-in-process-empty">No open staging rows in the last 7 days (IST).</p>
          )}
        </div>
        <div className="add-sales-in-process-detail">
          <div className="add-sales-in-process-detail-head">
            <h3 className="add-sales-in-process-detail-title">Sales Details</h3>
            {selectedId && detailPayload && detailEditDraft ? (
              <button
                type="button"
                className="app-button app-button--small add-sales-in-process-save-btn"
                disabled={saveDetailDisabled}
                onClick={() => void onSaveDetailChanges()}
              >
                {detailSaveBusy ? "Saving…" : "Save Changes"}
              </button>
            ) : null}
          </div>
          {!selectedId && <p className="add-sales-in-process-hint">Select a row in the table above.</p>}
          {detailErr && (
            <p className="add-sales-in-process-err" role="alert">
              {detailErr}
            </p>
          )}
          {selectedId && detailPayload && detailEditDraft && (
            <div className="add-sales-in-process-sales-details">
              <div className="add-sales-in-process-sales-col add-sales-in-process-sales-col--customer">
                <dl className="add-sales-v2-dl add-sales-in-process-sales-dl">
                  <div className="add-sales-v2-dl-row">
                    <dt>Mobile</dt>
                    <dd className="add-sales-in-process-dd--readonly">{mobileAlternateDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Customer Name</dt>
                    <dd className="add-sales-in-process-dd--readonly">{cust?.name?.trim() ? cust.name : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Care of</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input add-sales-v2-dl-input--care-of-free"
                        value={detailEditDraft.care_of}
                        onChange={(e) => {
                          const raw = sanitizeFormFieldInputValue(e.target.value);
                          setDetailEditDraft((prev) =>
                            prev ? { ...prev, care_of: raw } : prev
                          );
                        }}
                        placeholder="C/o Father's Name"
                        autoComplete="off"
                        spellCheck={false}
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Address</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.address}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, address: sanitizeFormFieldInputValue(e.target.value) }
                              : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Financier</dt>
                    <dd className="add-sales-in-process-dd--readonly">{cust?.financier?.trim() ? cust.financier : "—"}</dd>
                  </div>
                </dl>
              </div>
              <div className="add-sales-in-process-sales-col add-sales-in-process-sales-col--vehicle">
                <dl className="add-sales-v2-dl add-sales-in-process-sales-dl">
                  <div className="add-sales-v2-dl-row">
                    <dt>Chassis</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.frame_no}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, frame_no: sanitizeFormFieldValue(e.target.value) }
                              : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Engine</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.engine_no}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, engine_no: sanitizeFormFieldValue(e.target.value) }
                              : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Key</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.key_no}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev ? { ...prev, key_no: sanitizeFormFieldValue(e.target.value) } : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Battery</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.battery_no}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, battery_no: sanitizeFormFieldValue(e.target.value) }
                              : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Order#</dt>
                    <dd className="add-sales-in-process-dd--readonly">{veh?.order_number?.trim() ? veh.order_number : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Invoice#</dt>
                    <dd className="add-sales-in-process-dd--readonly">{veh?.invoice_number?.trim() ? veh.invoice_number : "—"}</dd>
                  </div>
                </dl>
              </div>
              <div className="add-sales-in-process-sales-col add-sales-in-process-sales-col--financing">
                <dl className="add-sales-v2-dl add-sales-in-process-sales-dl">
                  <div className="add-sales-v2-dl-row">
                    <dt>Nominee Name</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.nominee_name}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, nominee_name: sanitizeFormFieldInputValue(e.target.value) }
                              : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Relationship</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.nominee_relationship}
                        onChange={(e) =>
                          setDetailEditDraft((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  nominee_relationship: sanitizeFormFieldValue(e.target.value),
                                }
                              : prev
                          )
                        }
                        placeholder="—"
                        disabled={detailSaveBusy}
                      />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Insurance Provider</dt>
                    <dd className="add-sales-in-process-dd--readonly">{insuranceProviderDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Hero CPA</dt>
                    <dd className="add-sales-in-process-dd--readonly">{heroCpaSalesDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Policy#</dt>
                    <dd className="add-sales-in-process-dd--readonly">{ins?.policy_num?.trim() ? ins.policy_num : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>CPA Provider</dt>
                    <dd className="add-sales-in-process-dd--readonly">{cpaProviderSalesDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>CPA Policy#</dt>
                    <dd className="add-sales-in-process-dd--readonly">{cpaPolicySalesDisplay}</dd>
                  </div>
                </dl>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
