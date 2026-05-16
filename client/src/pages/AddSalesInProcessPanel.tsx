import { useCallback, useEffect, useMemo, useState } from "react";
import type { CpaInsurerPortalRow, CreateInvoiceEligibilityResponse } from "../api/addSales";
import {
  fetchAddSalesInProcess,
  fetchAddSalesStagingPayload,
  fetchCreateInvoiceEligibility,
  fetchDealerCpaContext,
  type AddSalesInProcessRow,
} from "../api/addSales";
import {
  dispatchPrintJobsFromApi,
  buildFillCpaAllianceInsuranceRequest,
  fillCpaAllianceInsuranceLocal,
  fillDmsLocal,
  fillHeroInsuranceLocal,
  overlayDealerSignaturesLocal,
  printGatePass,
} from "../api/fillForms";
import { insertRtoPayment } from "../api/rtoPaymentDetails";
import { buildDisplayAddress } from "../types";
import type { ExtractedCustomerDetails, ExtractedInsuranceDetails, ExtractedVehicleDetails } from "../types";

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

function cpaPolicyFromInsuranceRaw(rec: Record<string, unknown> | null | undefined): string {
  if (!rec) return "";
  const v = rec.cpa_policy_num ?? rec.cpa_policy ?? rec.alliance_policy_num ?? rec.cpa_policy_number;
  const s = v != null ? String(v).trim() : "";
  return s || "";
}

export interface AddSalesInProcessPanelProps {
  dealerId: number;
  dmsUrl: string;
  siteUrlsLoading?: boolean;
  siteUrlsError?: string | null;
  preferInsurer?: string | null;
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
  const [rowMsg, setRowMsg] = useState<string | null>(null);
  const [elig, setElig] = useState<CreateInvoiceEligibilityResponse | null>(null);
  const [eligLoading, setEligLoading] = useState(false);

  const [cpaInsurers, setCpaInsurers] = useState<CpaInsurerPortalRow[]>([]);
  const [dealerCpaInsurer, setDealerCpaInsurer] = useState<string | null>(null);
  const [dealerHeroCpi, setDealerHeroCpi] = useState<string | null>(null);

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

  useEffect(() => {
    void refreshList();
    const t = setInterval(() => void refreshList(), 60000);
    return () => clearInterval(t);
  }, [refreshList]);

  useEffect(() => {
    if (dealerId <= 0) return;
    let c = false;
    void (async () => {
      try {
        const r = await fetchDealerCpaContext(dealerId);
        if (c) return;
        setCpaInsurers(r.cpa_insurers ?? []);
        setDealerCpaInsurer(r.dealer_cpa_insurer?.trim() ? r.dealer_cpa_insurer.trim() : null);
        const hc = r.hero_cpi;
        setDealerHeroCpi(hc != null && String(hc).trim() !== "" ? String(hc).trim() : null);
      } catch {
        if (!c) {
          setCpaInsurers([]);
          setDealerCpaInsurer(null);
          setDealerHeroCpi(null);
        }
      }
    })();
    return () => {
      c = true;
    };
  }, [dealerId]);

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
  }, [selectedId, dealerId]);

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
  }, [selectedId, detailPayload, dealerId, mobileDigits]);

  const subfolder = useMemo(() => String(detailPayload?.file_location ?? "").trim(), [detailPayload]);

  const wrapRowAction = useCallback(
    async (stagingId: string, fn: () => Promise<void>) => {
      if (busyRowId != null || pageActionsBusy) return;
      setBusyRowId(stagingId);
      onRowActionStart(stagingId);
      setRowMsg(null);
      try {
        await fn();
        await refreshList();
      } catch (e) {
        setRowMsg(e instanceof Error ? e.message : String(e));
      } finally {
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
    dealerId <= 0 ||
    !!siteUrlsLoading ||
    !!siteUrlsError;

  const printEnabled =
    !pageActionsBusy &&
    !eligLoading &&
    Boolean(detailPayload) &&
    createInvoicePrimaryDisabled &&
    generateInsurancePrimaryDisabled &&
    cpaPrimaryDisabled;

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
        <p className="add-sales-in-process-err" role="status">
          {rowMsg}
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
                <th>Actions</th>
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
                          disabled={createInvoicePrimaryDisabled || selectedId !== r.staging_id || busyRowId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
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
                          disabled={generateInsurancePrimaryDisabled || selectedId !== r.staging_id || busyRowId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
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
                          disabled={cpaPrimaryDisabled || selectedId !== r.staging_id || busyRowId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
                            void wrapRowAction(r.staging_id, async () => {
                              const portal = pickCpaPortalRow(cpaInsurersFromElig ?? [], dealerCpaFromElig ?? null);
                              if (!portal?.login_url) throw new Error("No CPA portal URL.");
                              const sf = subfolder || "default";
                              const res = await fillCpaAllianceInsuranceLocal(
                                buildFillCpaAllianceInsuranceRequest({
                                  dealerId,
                                  subfolder: sf,
                                  portalUrl: portal.login_url,
                                  stagingId: r.staging_id,
                                })
                              );
                              if (!res.success) throw new Error(res.error ?? "CPA Insurance failed.");
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
                              const cid = parseInt(String(detailPayload?.customer_id ?? "").trim(), 10);
                              const vid = parseInt(String(detailPayload?.vehicle_id ?? "").trim(), 10);
                              if (Number.isNaN(cid) || Number.isNaN(vid) || cid < 1 || vid < 1) {
                                throw new Error(
                                  "Run Create Invoice first (customer/vehicle IDs required for RTO queue)."
                                );
                              }
                              await insertRtoPayment({
                                customer_id: cid,
                                vehicle_id: vid,
                                dealer_id: dealerId,
                                customer_mobile: mobileDigits || undefined,
                                staging_id: r.staging_id,
                                status: "Queued",
                              });
                              if (dealerId > 0 && subfolder) {
                                try {
                                  await overlayDealerSignaturesLocal({ dealerId, subfolder });
                                } catch {
                                  /* best-effort */
                                }
                              }
                              const vrec = detailPayload?.vehicle as Record<string, unknown> | undefined;
                              const vehicleData: Record<string, unknown> = {
                                key_no: vrec?.key_no,
                                frame_no: vrec?.frame_no,
                                engine_no: vrec?.engine_no,
                              };
                              const gp = await printGatePass({
                                subfolder: subfolder || "default",
                                customer: {
                                  name: cust?.name,
                                  care_of: cust?.care_of,
                                  address: cust?.address ?? buildDisplayAddress(cust),
                                  city: cust?.city,
                                  state: cust?.state,
                                  pin_code: cust?.pin_code,
                                  aadhar_id: cust?.aadhar_id,
                                },
                                vehicle: vehicleData,
                                vehicle_id: vid,
                                dealer_id: dealerId,
                              });
                              if (!gp.success && gp.error) throw new Error(gp.error);
                              dispatchPrintJobsFromApi(gp.print_jobs);
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
          <h3 className="add-sales-in-process-detail-title">Sales Details</h3>
          {!selectedId && <p className="add-sales-in-process-hint">Select a row in the table above.</p>}
          {detailErr && (
            <p className="add-sales-in-process-err" role="alert">
              {detailErr}
            </p>
          )}
          {selectedId && detailPayload && (
            <div className="add-sales-in-process-sales-details">
              <div className="add-sales-in-process-sales-col add-sales-in-process-sales-col--customer">
                <dl className="add-sales-v2-dl add-sales-in-process-sales-dl">
                  <div className="add-sales-v2-dl-row">
                    <dt>Mobile</dt>
                    <dd>{mobileAlternateDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Customer Name</dt>
                    <dd>{cust?.name?.trim() ? cust.name : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Care of</dt>
                    <dd>{cust?.care_of?.trim() ? cust.care_of : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Address</dt>
                    <dd>{cust?.address?.trim() ? cust.address : buildDisplayAddress(cust)}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Financier</dt>
                    <dd>{cust?.financier?.trim() ? cust.financier : "—"}</dd>
                  </div>
                </dl>
              </div>
              <div className="add-sales-in-process-sales-col add-sales-in-process-sales-col--vehicle">
                <dl className="add-sales-v2-dl add-sales-in-process-sales-dl">
                  <div className="add-sales-v2-dl-row">
                    <dt>Chassis</dt>
                    <dd>{veh?.frame_no?.trim() ? veh.frame_no : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Engine</dt>
                    <dd>{veh?.engine_no?.trim() ? veh.engine_no : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Key</dt>
                    <dd>{veh?.key_no?.trim() ? veh.key_no : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Battery</dt>
                    <dd>{veh?.battery_no?.trim() ? veh.battery_no : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Order#</dt>
                    <dd>{veh?.order_number?.trim() ? veh.order_number : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Invoice#</dt>
                    <dd>{veh?.invoice_number?.trim() ? veh.invoice_number : "—"}</dd>
                  </div>
                </dl>
              </div>
              <div className="add-sales-in-process-sales-col add-sales-in-process-sales-col--financing">
                <dl className="add-sales-v2-dl add-sales-in-process-sales-dl">
                  <div className="add-sales-v2-dl-row">
                    <dt>Nominee Name</dt>
                    <dd>{ins?.nominee_name?.trim() ? ins.nominee_name : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Relationship</dt>
                    <dd>{ins?.nominee_relationship?.trim() ? ins.nominee_relationship : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Insurance Provider</dt>
                    <dd>{insuranceProviderDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Hero CPA</dt>
                    <dd>{heroCpaSalesDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Policy#</dt>
                    <dd>{ins?.policy_num?.trim() ? ins.policy_num : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>CPA Provider</dt>
                    <dd>{cpaProviderSalesDisplay}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>CPA Policy#</dt>
                    <dd>{cpaPolicySalesDisplay}</dd>
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
