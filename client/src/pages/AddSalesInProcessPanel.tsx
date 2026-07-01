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
import { pullAadharScansForInsurance } from "../utils/ensureAadharScansBeforeInsurance";
import { runPrintQueueRtoFlow } from "../utils/printQueueRtoFlow";
import { buildDisplayAddress } from "../types";
import {
  buildSection2FullAddress,
  inProcessAddressFromStaging,
  normalizeOperatorFreeformAddress,
} from "../utils/section2CustomerFormat";
import {
  getInProcessDetailValidationErrors,
  type Section2FieldError,
} from "../utils/section2Validation";
import type { ExtractedCustomerDetails, ExtractedInsuranceDetails, ExtractedVehicleDetails } from "../types";
import { cpaPolicyFromInsuranceRaw } from "../utils/insuranceDisplay";
import { resolvePortalInsurer } from "../utils/addSalesInsurerResolve";
import {
  insuranceAddonDisplayLabel,
  mergeSelectedInsuranceAddonOption,
  normalizeInsuranceAddonRows,
} from "../utils/insuranceAddonUi";
import { sanitizeFormFieldInputValue, sanitizeFormFieldValue } from "../utils/formFieldSanitize";

function normalizeCpaRequiredFlag(raw: unknown): "Y" | "N" {
  const s = String(raw ?? "").trim().toUpperCase();
  if (s === "Y" || s === "YES") return "Y";
  return "N";
}

function formatCpaRequiredDisplay(raw: unknown): string {
  return normalizeCpaRequiredFlag(raw) === "Y" ? "Yes" : "No";
}

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

function normalizeHeroCpiFlag(raw: unknown): "Y" | "N" | null {
  const v = String(raw ?? "").trim().slice(0, 1).toUpperCase();
  if (v === "Y") return "Y";
  if (v === "N") return "N";
  return null;
}

function formatHeroCpiDisplay(raw: unknown): string {
  const flag = normalizeHeroCpiFlag(raw);
  if (flag === "Y") return "Yes";
  if (flag === "N") return "No";
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
  insurer: string;
  cpi_reqd: "Y" | "N";
}

function buildDraftFromPayload(
  payload: Record<string, unknown> | null,
  cpiReqd: "Y" | "N" | null
): InProcessDetailDraft | null {
  if (!payload) return null;
  const cust = payloadCustomer((payload.customer as Record<string, unknown>) ?? null);
  const veh = payloadVehicle((payload.vehicle as Record<string, unknown>) ?? null);
  const ins = payloadInsurance((payload.insurance as Record<string, unknown>) ?? null);
  const address =
    inProcessAddressFromStaging(cust) ||
    (cust ? buildSection2FullAddress(cust) : "") ||
    "";
  return {
    care_of: cust?.care_of?.trim() ?? "",
    address: address.trim(),
    frame_no: veh?.frame_no?.trim() ?? "",
    engine_no: veh?.engine_no?.trim() ?? "",
    key_no: veh?.key_no?.trim() ?? "",
    battery_no: veh?.battery_no?.trim() ?? "",
    nominee_name: ins?.nominee_name?.trim() ?? "",
    nominee_relationship: ins?.nominee_relationship?.trim() ?? "",
    insurer: ins?.insurer?.trim() ?? "",
    cpi_reqd: cpiReqd ?? "N",
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
    a.nominee_relationship === b.nominee_relationship &&
    a.insurer === b.insurer &&
    a.cpi_reqd === b.cpi_reqd
  );
}

function InProcessFieldError({
  field,
  errors,
}: {
  field: string;
  errors: readonly Section2FieldError[];
}) {
  const msg = errors.find((e) => e.field === field)?.message;
  if (!msg) return null;
  return (
    <p className="add-sales-v2-field-error" role="alert">
      {msg}
    </p>
  );
}

function draftToPatchBody(
  draft: InProcessDetailDraft,
  baseline: InProcessDetailDraft,
  opts: { includeInsurer: boolean }
): PatchAddSalesStagingPayloadBody {
  const body: PatchAddSalesStagingPayloadBody = {
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
  if (opts.includeInsurer && draft.insurer !== baseline.insurer) {
    body.insurance = {
      ...body.insurance,
      insurer: draft.insurer.trim() || null,
    };
  }
  if (draft.cpi_reqd !== baseline.cpi_reqd) {
    body.cpi_reqd = draft.cpi_reqd;
  }
  return body;
}

function insuranceAddonPatchIfChanged(
  edit: number | "",
  baseline: number | ""
): number | undefined {
  if (edit === baseline) return undefined;
  if (edit === "" || !Number.isFinite(edit) || edit <= 0) return undefined;
  return edit;
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
  const [stagingCpiReqd, setStagingCpiReqd] = useState<"Y" | "N" | null>(null);
  const [stagingInsuranceState, setStagingInsuranceState] = useState<number | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [busyRowId, setBusyRowId] = useState<string | null>(null);
  const [rowMsg, setRowMsg] = useState<{ text: string; success: boolean } | null>(null);
  const [elig, setElig] = useState<CreateInvoiceEligibilityResponse | null>(null);
  const [eligLoading, setEligLoading] = useState(false);

  const [cpaInsurers, setCpaInsurers] = useState<CpaInsurerPortalRow[]>([]);
  const [portalInsurers, setPortalInsurers] = useState<string[]>([]);
  const [insuranceAddons, setInsuranceAddons] = useState<
    { insurance_addon_id: number; display_label: string }[]
  >([]);
  const [insuranceAddonEdit, setInsuranceAddonEdit] = useState<number | "">("");
  const [insuranceAddonBaseline, setInsuranceAddonBaseline] = useState<number | "">("");
  const [dealerCpaInsurer, setDealerCpaInsurer] = useState<string | null>(null);
  const [dealerHeroCpi, setDealerHeroCpi] = useState<string | null>(null);
  const [panelRefreshToken, setPanelRefreshToken] = useState(0);
  const [isPanelRefreshing, setIsPanelRefreshing] = useState(false);
  const [detailEditDraft, setDetailEditDraft] = useState<InProcessDetailDraft | null>(null);
  const [detailSaveBusy, setDetailSaveBusy] = useState(false);
  const [detailValidationErrors, setDetailValidationErrors] = useState<Section2FieldError[]>([]);
  const [detailSaveAttempted, setDetailSaveAttempted] = useState(false);

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
      const pi = r.portal_insurers;
      setPortalInsurers(Array.isArray(pi) ? pi.map((x) => String(x).trim()).filter(Boolean) : []);
      setDealerCpaInsurer(r.dealer_cpa_insurer?.trim() ? r.dealer_cpa_insurer.trim() : null);
      const hc = r.hero_cpi;
      setDealerHeroCpi(hc != null && String(hc).trim() !== "" ? String(hc).trim() : null);
      const addons = r.insurance_addons;
      setInsuranceAddons(normalizeInsuranceAddonRows(addons));
    } catch {
      setCpaInsurers([]);
      setPortalInsurers([]);
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
      setStagingCpiReqd(null);
      setStagingInsuranceState(null);
      setInsuranceAddonEdit("");
      setInsuranceAddonBaseline("");
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
        setStagingCpiReqd(
          r.cpi_reqd != null ? normalizeCpaRequiredFlag(r.cpi_reqd) : null
        );
        setStagingInsuranceState(r.insurance_state ?? null);
        const effAddon = r.effective_insurance_addon ?? r.insurance_addon;
        const addonNum =
          effAddon != null && Number(effAddon) > 0 ? Number(effAddon) : "";
        setInsuranceAddonEdit(addonNum);
        setInsuranceAddonBaseline(addonNum);
        const effLabel =
          typeof r.effective_insurance_addon_label === "string"
            ? r.effective_insurance_addon_label.trim()
            : "";
        const labelLookup =
          effLabel && addonNum !== ""
            ? [{ insurance_addon_id: Number(addonNum), display_label: effLabel }]
            : [];
        setInsuranceAddons(
          mergeSelectedInsuranceAddonOption(
            normalizeInsuranceAddonRows(r.insurance_addons),
            addonNum,
            labelLookup
          )
        );
      } catch (e) {
        if (!c) setDetailErr(e instanceof Error ? e.message : "Could not load row details.");
      }
    })();
    return () => {
      c = true;
    };
  }, [selectedId, dealerId, panelRefreshToken]);

  useEffect(() => {
    setDetailEditDraft(buildDraftFromPayload(detailPayload, stagingCpiReqd));
    setDetailValidationErrors([]);
    setDetailSaveAttempted(false);
  }, [detailPayload, selectedId, stagingCpiReqd]);

  const insurerEditable = stagingInsuranceState == null || stagingInsuranceState === 0;

  const clearDetailValidation = useCallback(() => {
    setDetailValidationErrors([]);
    setDetailSaveAttempted(false);
  }, []);

  const detailFieldInvalid = useCallback(
    (field: string) => detailSaveAttempted && detailValidationErrors.some((e) => e.field === field),
    [detailSaveAttempted, detailValidationErrors]
  );

  const detailErrorsToShow = detailSaveAttempted ? detailValidationErrors : [];

  const detailDraftBaseline = useMemo(
    () => buildDraftFromPayload(detailPayload, stagingCpiReqd),
    [detailPayload, stagingCpiReqd]
  );
  const detailDirty = useMemo(() => {
    if (!detailEditDraft || !detailDraftBaseline) return false;
    if (insuranceAddonEdit !== insuranceAddonBaseline) return true;
    return !draftsEqual(detailEditDraft, detailDraftBaseline);
  }, [detailEditDraft, detailDraftBaseline, insuranceAddonEdit, insuranceAddonBaseline]);

  const onSaveDetailChanges = useCallback(async () => {
    if (!selectedId || !detailEditDraft || dealerId <= 0 || !detailDirty) return;
    const validationErrors = getInProcessDetailValidationErrors(detailEditDraft);
    if (validationErrors.length > 0) {
      setDetailValidationErrors(validationErrors);
      setDetailSaveAttempted(true);
      const labels: Record<string, string> = {
        care_of: "Care of",
        address: "Address",
        frame_no: "Chassis",
        engine_no: "Engine",
        key_no: "Key",
        battery_no: "Battery",
        nominee_name: "Nominee Name",
        nominee_relationship: "Relationship",
      };
      setDetailErr(
        validationErrors
          .map((e) => `${labels[e.field] ?? e.field}: ${e.message}`)
          .join(" · ")
      );
      return;
    }
    const normedAddress = normalizeOperatorFreeformAddress(detailEditDraft.address);
    if (!normedAddress) {
      setDetailValidationErrors([{ field: "address", message: "Address could not be normalized." }]);
      setDetailSaveAttempted(true);
      setDetailErr("Address: Address could not be normalized.");
      return;
    }
    const draftForSave: InProcessDetailDraft = {
      ...detailEditDraft,
      address: normedAddress.address,
    };
    setDetailEditDraft(draftForSave);
    setDetailValidationErrors([]);
    setDetailSaveAttempted(false);
    setDetailSaveBusy(true);
    setDetailErr(null);
    try {
      await patchAddSalesStagingPayload(
        selectedId,
        dealerId,
        {
          ...draftToPatchBody(draftForSave, detailDraftBaseline!, { includeInsurer: insurerEditable }),
          ...(() => {
            const addon = insuranceAddonPatchIfChanged(insuranceAddonEdit, insuranceAddonBaseline);
            return addon != null ? { insurance_addon: addon } : {};
          })(),
        }
      );
      setPanelRefreshToken((t) => t + 1);
      await refreshList();
      setRowMsg({ text: "Changes saved.", success: true });
    } catch (e) {
      setDetailErr(e instanceof Error ? e.message : "Could not save changes.");
    } finally {
      setDetailSaveBusy(false);
    }
  }, [selectedId, detailEditDraft, dealerId, detailDirty, refreshList, detailDraftBaseline, insurerEditable, insuranceAddonEdit, insuranceAddonBaseline]);

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

  const portalList = portalInsurers.length > 0 ? portalInsurers : (elig?.portal_insurers ?? []);
  const insTrim = String(ins?.insurer ?? "").trim();
  const insuranceProviderDisplay =
    portalList.length > 0
      ? portalList.includes(insTrim)
        ? insTrim || "—"
        : (insTrim || String(preferInsurer ?? "").trim() || "—")
      : insTrim || String(preferInsurer ?? "").trim() || "—";
  const insuranceProviderSelectValue =
    resolvePortalInsurer(detailEditDraft?.insurer, preferInsurer, portalList) ?? "";

  const preferInsurerTrimmed = String(preferInsurer ?? "").trim();

  const insuranceAddonSelectRows = useMemo(
    () => mergeSelectedInsuranceAddonOption(insuranceAddons, insuranceAddonEdit, insuranceAddons),
    [insuranceAddons, insuranceAddonEdit]
  );

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
                stagingId: selectedId,
              })
            : ch && eng && mob.length >= 10
              ? await fetchCreateInvoiceEligibility({
                  chassisNum: ch,
                  engineNum: eng,
                  mobile: mob,
                  dealerId: dealerId > 0 ? dealerId : undefined,
                  stagingId: selectedId,
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
  const giResumeActive = stagingInsuranceState === 2;
  const generateInsuranceCompleted = Boolean(
    stagingInsuranceState === 3 ||
      (elig?.invoice_recorded && !(elig?.generate_insurance_enabled ?? false) && !giResumeActive)
  );
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
    normalizeCpaRequiredFlag(r.cpi_reqd) !== "Y" ||
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
  const heroCpiFlag = useMemo(
    () => normalizeHeroCpiFlag(elig?.hero_cpi ?? dealerHeroCpi),
    [elig?.hero_cpi, dealerHeroCpi]
  );
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
  const cpaRequiredIsYes = useMemo(() => {
    const raw =
      detailEditDraft?.cpi_reqd ??
      stagingCpiReqd ??
      elig?.staging_cpi_reqd ??
      elig?.effective_cpi_reqd ??
      "N";
    return normalizeCpaRequiredFlag(raw) === "Y";
  }, [detailEditDraft?.cpi_reqd, stagingCpiReqd, elig?.staging_cpi_reqd, elig?.effective_cpi_reqd]);

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
                              const sf = (r.file_location ?? r.subfolder ?? subfolder).trim();
                              const res = await fillDmsLocal({
                                staging_id: r.staging_id,
                                dealer_id: dealerId,
                                subfolder: sf || undefined,
                              });
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
                              const sf = (r.file_location ?? r.subfolder ?? subfolder).trim();
                              await pullAadharScansForInsurance(dealerId, sf);
                              const res = await fillHeroInsuranceLocal({
                                staging_id: r.staging_id,
                                dealer_id: dealerId,
                                subfolder: sf || undefined,
                              });
                              if (!res.success) throw new Error(res.error ?? "Generate Insurance failed.");
                              if (res.hero_insure_reports?.ok === false) {
                                throw new Error(
                                  res.hero_insure_reports.error ??
                                    res.error ??
                                    "Print Policy / PDF download failed."
                                );
                              }
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
                                  address:
                                    (cust ? buildSection2FullAddress(cust) : "") ||
                                    cust?.address ||
                                    buildDisplayAddress(cust),
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
          {detailSaveAttempted && detailValidationErrors.length > 0 && (
            <ul className="add-sales-v2-validation-list" role="alert">
              {detailValidationErrors.map((e) => (
                <li key={e.field}>
                  <strong>{e.field}</strong>: {e.message}
                </li>
              ))}
            </ul>
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
                          clearDetailValidation();
                          const raw = sanitizeFormFieldInputValue(e.target.value);
                          setDetailEditDraft((prev) =>
                            prev ? { ...prev, care_of: raw } : prev
                          );
                        }}
                        placeholder="S/o Father's Name"
                        autoComplete="off"
                        spellCheck={false}
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("care_of")}
                      />
                      <InProcessFieldError field="care_of" errors={detailErrorsToShow} />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Address</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.address}
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, address: sanitizeFormFieldInputValue(e.target.value) }
                              : prev
                          );
                        }}
                        placeholder="locality, City, State, 123456 or State - 123456"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("address")}
                      />
                      <InProcessFieldError field="address" errors={detailErrorsToShow} />
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
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, frame_no: sanitizeFormFieldValue(e.target.value) }
                              : prev
                          );
                        }}
                        placeholder="—"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("frame_no")}
                      />
                      <InProcessFieldError field="frame_no" errors={detailErrorsToShow} />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Engine</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.engine_no}
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, engine_no: sanitizeFormFieldValue(e.target.value) }
                              : prev
                          );
                        }}
                        placeholder="—"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("engine_no")}
                      />
                      <InProcessFieldError field="engine_no" errors={detailErrorsToShow} />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Key</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.key_no}
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev ? { ...prev, key_no: sanitizeFormFieldValue(e.target.value) } : prev
                          );
                        }}
                        placeholder="—"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("key_no")}
                      />
                      <InProcessFieldError field="key_no" errors={detailErrorsToShow} />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Battery</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.battery_no}
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, battery_no: sanitizeFormFieldValue(e.target.value) }
                              : prev
                          );
                        }}
                        placeholder="—"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("battery_no")}
                      />
                      <InProcessFieldError field="battery_no" errors={detailErrorsToShow} />
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
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev
                              ? { ...prev, nominee_name: sanitizeFormFieldInputValue(e.target.value) }
                              : prev
                          );
                        }}
                        placeholder="—"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("nominee_name")}
                      />
                      <InProcessFieldError field="nominee_name" errors={detailErrorsToShow} />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Relationship</dt>
                    <dd>
                      <input
                        className="add-sales-v2-dl-input"
                        value={detailEditDraft.nominee_relationship}
                        onChange={(e) => {
                          clearDetailValidation();
                          setDetailEditDraft((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  nominee_relationship: sanitizeFormFieldValue(e.target.value),
                                }
                              : prev
                          );
                        }}
                        placeholder="—"
                        disabled={detailSaveBusy}
                        aria-invalid={detailFieldInvalid("nominee_relationship")}
                      />
                      <InProcessFieldError field="nominee_relationship" errors={detailErrorsToShow} />
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Insurance Provider</dt>
                    <dd
                      className={
                        insurerEditable && portalList.length > 0
                          ? "add-sales-v2-dd--insurance-editable"
                          : "add-sales-in-process-dd--readonly"
                      }
                    >
                      {insurerEditable && portalList.length > 0 && detailEditDraft ? (
                        <select
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--insurance-provider-wide"
                          aria-label="Insurance provider"
                          value={insuranceProviderSelectValue}
                          onChange={(e) => {
                            clearDetailValidation();
                            setDetailEditDraft((prev) =>
                              prev ? { ...prev, insurer: e.target.value } : prev
                            );
                          }}
                          disabled={detailSaveBusy}
                        >
                          {portalList.map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        insuranceProviderDisplay
                      )}
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Insurance Add-ons</dt>
                    <dd
                      className={
                        insurerEditable && preferInsurerTrimmed
                          ? "add-sales-v2-dd--insurance-editable"
                          : "add-sales-in-process-dd--readonly"
                      }
                    >
                      {insurerEditable && preferInsurerTrimmed ? (
                        <select
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--insurance-provider-wide"
                          aria-label="Insurance add-ons"
                          value={
                            insuranceAddonSelectRows.some(
                              (o) => o.insurance_addon_id === insuranceAddonEdit
                            )
                              ? String(insuranceAddonEdit)
                              : ""
                          }
                          onChange={(e) => {
                            clearDetailValidation();
                            const v = e.target.value;
                            setInsuranceAddonEdit(v === "" ? "" : Number(v));
                          }}
                          disabled={detailSaveBusy}
                        >
                          <option value="">— None —</option>
                          {insuranceAddonSelectRows.map((row) => (
                            <option key={row.insurance_addon_id} value={row.insurance_addon_id}>
                              {row.display_label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        insuranceAddonDisplayLabel(insuranceAddonEdit, insuranceAddonSelectRows)
                      )}
                    </dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>Policy#</dt>
                    <dd className="add-sales-in-process-dd--readonly">{ins?.policy_num?.trim() ? ins.policy_num : "—"}</dd>
                  </div>
                  <div className="add-sales-v2-dl-row">
                    <dt>CPA Required</dt>
                    <dd>
                      {detailEditDraft ? (
                        <select
                          className="add-sales-v2-input"
                          value={detailEditDraft.cpi_reqd}
                          onChange={(e) => {
                            clearDetailValidation();
                            setDetailEditDraft((prev) =>
                              prev
                                ? { ...prev, cpi_reqd: normalizeCpaRequiredFlag(e.target.value) }
                                : prev
                            );
                          }}
                          aria-label="CPA Required"
                          disabled={detailSaveBusy}
                        >
                          <option value="Y">Yes</option>
                          <option value="N">No</option>
                        </select>
                      ) : (
                        formatCpaRequiredDisplay(
                          stagingCpiReqd ?? elig?.staging_cpi_reqd ?? elig?.effective_cpi_reqd
                        )
                      )}
                    </dd>
                  </div>
                  {cpaRequiredIsYes && (
                    <>
                      {heroCpiFlag === "Y" && (
                        <div className="add-sales-v2-dl-row">
                          <dt>Hero CPA</dt>
                          <dd className="add-sales-in-process-dd--readonly">{heroCpaSalesDisplay}</dd>
                        </div>
                      )}
                      {heroCpiFlag === "N" && (
                        <div className="add-sales-v2-dl-row">
                          <dt>CPA Provider</dt>
                          <dd className="add-sales-in-process-dd--readonly">{cpaProviderSalesDisplay}</dd>
                        </div>
                      )}
                      <div className="add-sales-v2-dl-row">
                        <dt>CPA Policy#</dt>
                        <dd className="add-sales-in-process-dd--readonly">{cpaPolicySalesDisplay}</dd>
                      </div>
                    </>
                  )}
                </dl>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
