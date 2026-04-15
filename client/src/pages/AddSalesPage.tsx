import { useState, useEffect, useRef, useCallback } from "react";
import type { ExtractedVehicleDetails, ExtractedCustomerDetails, ExtractedInsuranceDetails } from "../types";
import { buildDisplayAddress } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { ManualFallbackSplitReview } from "../components/ManualFallbackSplitReview";
import type { ManualFallbackPayload } from "../types";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { submitInfo } from "../api/submitInfo";
import { fillDmsOnly, fillHeroInsurance, printGatePass, isFillDmsAbortError, warmDmsBrowser } from "../api/fillForms";
import { fetchCreateInvoiceEligibility } from "../api/addSales";
import { insertRtoPayment } from "../api/rtoPaymentDetails";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
import { markBulkLoadSuccess } from "../api/bulkLoads";
import { isHeroBajajFinancierForStaging } from "../utils/financierStagingRules";
import { normalizeVehicleDetails, hasVehicleData } from "../utils/vehicleDetails";
import {
  sanitizeExtractedVehicleDetailFields,
  sanitizeFormFieldValue,
  sanitizeNomineeAgeInput,
  sanitizeOptionalFormField,
} from "../utils/formFieldSanitize";
import { StatusMessage } from "../components/StatusMessage";
import { usePageVisible } from "../hooks/usePageVisible";
import type { ConsolidatedFsArchiveContext } from "../utils/scannerArchive";
import { moveConsolidatedToProcessed } from "../utils/scannerArchive";
import {
  composeCareOf,
  formatDobDigitsInput,
  isValidDdMmYyyy,
  normalizeDobToDdMmYyyy,
  parseCareOfFromCombined,
} from "../utils/section2CustomerFormat";

/** Shown under Upload documents while upload or OCR polling runs; counts down toward 00m:00s. */
const ADD_SALES_OCR_COUNTDOWN_START_SEC = 40;

/** True when extracted-details payload has at least one structured OCR block — used before DMS warm-browser. */
function detailsHasOcrPayloadForWarm(details: unknown): boolean {
  if (!details || typeof details !== "object" || Array.isArray(details)) return false;
  const d = details as Record<string, unknown>;
  if (d.customer && typeof d.customer === "object") return true;
  if (d.insurance && typeof d.insurance === "object") return true;
  if (d.vehicle != null && typeof d.vehicle === "object") return true;
  return false;
}

function getInitialForm() {
  const d = loadAddSalesForm();
  return d;
}

function mapApiCustomerToExtracted(cust: Record<string, unknown>): ExtractedCustomerDetails {
  const r = cust;
  const pinVal = sanitizeOptionalFormField(String(r.pin ?? r.pin_code ?? "").trim());
  const dobRaw = sanitizeOptionalFormField(String(r.date_of_birth ?? "").trim());
  const dobNorm = dobRaw ? normalizeDobToDdMmYyyy(dobRaw) : "";
  const careRaw = sanitizeOptionalFormField(String(r.care_of ?? "").trim());
  const careParts = parseCareOfFromCombined(careRaw);
  const careComposed = composeCareOf(careParts.relation, careParts.name) || careRaw;
  const aadharDigits = String(r.aadhar_id ?? "").replace(/\D/g, "");
  const aadharLast4 =
    aadharDigits.length >= 4
      ? aadharDigits.slice(-4)
      : aadharDigits.length > 0
        ? aadharDigits
        : undefined;
  return {
    aadhar_id: sanitizeOptionalFormField(aadharLast4 ?? ""),
    name: sanitizeOptionalFormField(String(r.name ?? "").trim()),
    alt_phone_num: sanitizeOptionalFormField(String(r.alt_phone_num ?? r.alternate_mobile_number ?? "").trim()),
    gender: sanitizeOptionalFormField(String(r.gender ?? "").trim()),
    year_of_birth: sanitizeOptionalFormField(String(r.year_of_birth ?? "").trim()),
    date_of_birth: dobNorm || undefined,
    care_of: careComposed || undefined,
    care_of_relation: careParts.relation,
    care_of_name: careParts.name || undefined,
    house: sanitizeOptionalFormField(String(r.house ?? "").trim()),
    street: sanitizeOptionalFormField(String(r.street ?? "").trim()),
    location: sanitizeOptionalFormField(String(r.location ?? "").trim()),
    city: sanitizeOptionalFormField(String(r.city ?? "").trim()),
    post_office: sanitizeOptionalFormField(String(r.post_office ?? "").trim()),
    district: sanitizeOptionalFormField(String(r.district ?? "").trim()),
    sub_district: sanitizeOptionalFormField(String(r.sub_district ?? "").trim()),
    state: sanitizeOptionalFormField(String(r.state ?? "").trim()),
    pin_code: pinVal,
    address: sanitizeOptionalFormField(String(r.address ?? "").trim()),
  };
}

/** Reject consent/SMS line OCR places under blank "Insurer Name (if needed)" (must match backend ``sanitize_details_sheet_insurer_value``). */
function normalizeInsurerOcrValue(value: unknown): string | undefined {
  const v = String(value ?? "").trim();
  if (!v) return undefined;
  const low = v.toLowerCase();
  if (low.includes("i agree") && (low.includes("sms") || low.includes("periodic") || low.includes("receiving"))) return undefined;
  if (low.includes("periodic sms") || low.includes("registration and service")) return undefined;
  if (low.includes("updates about registration")) return undefined;
  if (low.includes("i agree") && low.includes("registration") && (low.includes("service") || low.includes("status"))) return undefined;
  return v;
}

function normalizeFinancierInput(value: unknown): string | undefined {
  const v = String(value ?? "").trim();
  if (!v) return undefined;
  const low = v.toLowerCase();
  // OCR sometimes returns the placeholder label instead of an actual financer name.
  if (
    low.includes("insurer name (if needed)") ||
    low.includes("insurer name if needed") ||
    low.includes("insurance provider")
  ) {
    return undefined;
  }
  if (
    low === "insurer name (if needed)" ||
    low === "insurer name if needed" ||
    low === "insurer name" ||
    low === "insurance provider" ||
    low === "if needed" ||
    low === "na" ||
    low === "n/a" ||
    low === "null" ||
    low === "none" ||
    low === "-"
  ) {
    return undefined;
  }
  return v;
}

/** Prefer OCR / API value when non-empty so UI matches OCR_To_be_Used.json (stale localStorage or prior merge must not win). */
function preferNonEmptyOcr(
  fromJson: string | undefined,
  current: string | undefined
): string | undefined {
  const j = fromJson != null && String(fromJson).trim() !== "" ? String(fromJson).trim() : undefined;
  if (j !== undefined) return j;
  const c = current != null && String(current).trim() !== "" ? String(current).trim() : undefined;
  return c;
}

function mergeInsuranceFromOcrPayload(
  prev: ExtractedInsuranceDetails | null | undefined,
  r: Record<string, unknown>
): ExtractedInsuranceDetails {
  const current = prev ?? {};
  const fromServer = {
    profession: typeof r.profession === "string" ? r.profession : undefined,
    marital_status: typeof r.marital_status === "string" ? r.marital_status : undefined,
    nominee_gender: typeof r.nominee_gender === "string" ? r.nominee_gender : undefined,
    nominee_name: typeof r.nominee_name === "string" ? r.nominee_name : undefined,
    nominee_age: r.nominee_age != null ? String(r.nominee_age) : undefined,
    nominee_relationship: typeof r.nominee_relationship === "string" ? r.nominee_relationship : undefined,
    insurer: typeof r.insurer === "string" ? r.insurer : undefined,
    policy_num: typeof r.policy_num === "string" ? r.policy_num : undefined,
    policy_from: typeof r.policy_from === "string" ? r.policy_from : undefined,
    policy_to: typeof r.policy_to === "string" ? r.policy_to : undefined,
    premium: typeof r.premium === "string" ? r.premium : r.premium != null ? String(r.premium) : undefined,
  };
  const ocrFinancierRaw = Object.prototype.hasOwnProperty.call(r, "financier")
    ? normalizeFinancierInput(r.financier)
    : preferNonEmptyOcr(undefined, normalizeFinancierInput(current.financier) ?? current.financier);
  const ocrFinancier = sanitizeOptionalFormField(ocrFinancierRaw ?? undefined);
  return {
    ...current,
    profession: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.profession),
      sanitizeOptionalFormField(current.profession)
    ),
    financier: ocrFinancier,
    marital_status: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.marital_status),
      sanitizeOptionalFormField(current.marital_status)
    ),
    nominee_gender: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.nominee_gender),
      sanitizeOptionalFormField(current.nominee_gender)
    ),
    nominee_name: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.nominee_name),
      sanitizeOptionalFormField(current.nominee_name)
    ),
    nominee_age: preferNonEmptyOcr(
      fromServer.nominee_age != null && String(fromServer.nominee_age).trim() !== ""
        ? sanitizeNomineeAgeInput(String(fromServer.nominee_age).trim())
        : undefined,
      current.nominee_age != null ? sanitizeNomineeAgeInput(String(current.nominee_age)) : undefined
    ),
    nominee_relationship: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.nominee_relationship),
      sanitizeOptionalFormField(current.nominee_relationship)
    ),
    insurer: preferNonEmptyOcr(
      sanitizeOptionalFormField(normalizeInsurerOcrValue(fromServer.insurer) ?? undefined),
      sanitizeOptionalFormField(normalizeInsurerOcrValue(current.insurer) ?? undefined)
    ),
    policy_num: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.policy_num),
      sanitizeOptionalFormField(current.policy_num)
    ),
    policy_from: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.policy_from),
      sanitizeOptionalFormField(current.policy_from)
    ),
    policy_to: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.policy_to),
      sanitizeOptionalFormField(current.policy_to)
    ),
    premium: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.premium),
      sanitizeOptionalFormField(current.premium)
    ),
  };
}

interface AddSalesPageProps {
  dealerId: number;
  /** From GET /dealers/:id — Hero MotoCorp is ``1`` (financier staging remap rules). */
  oemId: number | null;
  /** ``dealer_ref.prefer_insurer`` — shown and submitted when extracted ``insurer`` is empty. */
  preferInsurer?: string | null;
  /** DMS base URL from GET /settings/site-urls (server config; Hero defaults in `app/hero_dms_defaults.py`). No client fallbacks. */
  dmsUrl?: string;
  /** True while fetching /settings/site-urls. */
  siteUrlsLoading?: boolean;
  /** Set when site URL config could not be loaded from the API. */
  siteUrlsError?: string | null;
  /** Increment to force the same behavior as pressing "New". */
  autoNewTrigger?: number;
  /** When true, show “I want to upload individual files” (login_id shashank only). */
  showIndividualFileUploadToggle?: boolean;
}

export function AddSalesPage({
  dealerId,
  oemId,
  preferInsurer = null,
  dmsUrl,
  siteUrlsLoading,
  siteUrlsError,
  autoNewTrigger,
  showIndividualFileUploadToggle = false,
}: AddSalesPageProps) {
  const pageVisible = usePageVisible();
  const [mobile, setMobile] = useState(() => getInitialForm().mobile);
  const [savedTo, setSavedTo] = useState<string | null>(() => getInitialForm().savedTo);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>(() => getInitialForm().uploadedFiles);
  const [uploadStatus, setUploadStatus] = useState(() => getInitialForm().uploadStatus);
  const [extractedVehicle, setExtractedVehicle] = useState<ExtractedVehicleDetails | null>(
    () => getInitialForm().extractedVehicle
  );
  const [extractedCustomer, setExtractedCustomer] = useState<ExtractedCustomerDetails | null>(
    () => getInitialForm().extractedCustomer
  );
  const [extractedInsurance, setExtractedInsurance] = useState<ExtractedInsuranceDetails | null>(
    () => getInitialForm().extractedInsurance
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStatus, setSubmitStatus] = useState<string | null>(null);
  const [fillDmsStatus, setFillDmsStatus] = useState<string | null>(null);
  /** Milestones or narrative step lines from the last Fill DMS run (banner at top). */
  const [dmsMilestones, setDmsMilestones] = useState<string[]>([]);
  /** True when banner lines are Siebel `dms_step_messages` (sentence-style) vs checklist milestones. */
  const [dmsBannerIsStepMessages, setDmsBannerIsStepMessages] = useState(false);
  const [isFillDmsLoading, setIsFillDmsLoading] = useState(false);
  const [, setDmsRunEndedWithError] = useState(false);
  const [isFillInsuranceLoading, setIsFillInsuranceLoading] = useState(false);
  const [isPrintFormsLoading, setIsPrintFormsLoading] = useState(false);
  const [printFormsStatus, setPrintFormsStatus] = useState<string | null>(null);
  const [fillInsuranceStatus, setFillInsuranceStatus] = useState<string | null>(null);
  /** Create Invoice (DMS) allowed only after Submit Info and when sales_master has no invoice# for this sale. */
  const [createInvoiceEligibilityLoading, setCreateInvoiceEligibilityLoading] = useState(false);
  const [createInvoiceEnabled, setCreateInvoiceEnabled] = useState(false);
  const [createInvoiceEligibilityReason, setCreateInvoiceEligibilityReason] = useState<string | null>(null);
  const [generateInsuranceEnabled, setGenerateInsuranceEnabled] = useState(false);
  const [generateInsuranceReason, setGenerateInsuranceReason] = useState<string | null>(null);
  /** DMS-scraped vehicle; shown in Fill Forms > DMS. Only populated when user presses Fill Forms. */
  const [dmsScrapedVehicle, setDmsScrapedVehicle] = useState<ExtractedVehicleDetails | null>(null);
  /** True when Form 21 and Form 22 PDFs have been downloaded from DMS. */
  const [dmsPdfsDownloaded, setDmsPdfsDownloaded] = useState(false);
  /** True after user has successfully pressed Submit Info. (Section 3 stays greyed until then.) */
  const [hasSubmittedInfo, setHasSubmittedInfo] = useState(() => getInitialForm().hasSubmittedInfo);
  /** True after user has used Print forms. (Used for beforeunload warning.) */
  const [hasPrintedForms, setHasPrintedForms] = useState(false);
  /** From last successful Submit Info; used when inserting the RTO queue row after Fill Forms. */
  /**
   * Committed `customer_master` / `vehicle_master` ids for this sale.
   * Set from: Create Invoice (`fillDmsOnly`) response; eligibility API (`fetchCreateInvoiceEligibility` /
   * `refreshCreateInvoiceEligibility`) `resolved_*` when chassis/engine/mobile match DB; restored from session storage.
   * Submit Info alone only stores `lastStagingId` — masters are committed after Create Invoice.
   */
  const [lastSubmittedCustomerId, setLastSubmittedCustomerId] = useState<number | null>(() => getInitialForm().lastSubmittedCustomerId);
  const [lastSubmittedVehicleId, setLastSubmittedVehicleId] = useState<number | null>(() => getInitialForm().lastSubmittedVehicleId);
  const [lastStagingId, setLastStagingId] = useState<string | null>(() => getInitialForm().lastStagingId);
  const [createInvoiceCompleted, setCreateInvoiceCompleted] = useState(() => getInitialForm().createInvoiceCompleted);
  const [generateInsuranceCompleted, setGenerateInsuranceCompleted] = useState(
    () => getInitialForm().generateInsuranceCompleted
  );
  /** Extraction error (e.g. QR code not readable) – stops poll and shows message. */
  const [extractionError, setExtractionError] = useState<string | null>(null);
  /** Pre-OCR failed validation; server returned JPEG split session for manual slot assignment. */
  const [manualFallbackPayload, setManualFallbackPayload] = useState<ManualFallbackPayload | null>(null);
  /** After consolidated upload with FS access + manual OCR fallback: move landing → processed when Submit Info succeeds. */
  const [pendingScannerArchiveMove, setPendingScannerArchiveMove] = useState<ConsolidatedFsArchiveContext | null>(null);
  /** Documents placed via manual assign; no Textract/OCR — user fills Section 2 by hand. */
  const [manualFormOnly, setManualFormOnly] = useState(false);
  /** True once Textract has returned insurance data for this upload (details sheet processed). */
  const [insuranceReadByTextract, setInsuranceReadByTextract] = useState(() => {
    const stored = loadAddSalesForm().extractedInsurance;
    return Boolean(
      stored &&
        [
          stored.profession,
          stored.financier,
          stored.marital_status,
          stored.nominee_gender,
          stored.nominee_name,
          stored.nominee_age,
          stored.nominee_relationship,
          stored.insurer,
          stored.policy_from,
          stored.policy_to,
          stored.premium,
        ].some((x) => x != null && String(x).trim() !== "")
    );
  });
  const [formResetKey, setFormResetKey] = useState(0);

  /** DMS warm-browser runs only after OCR text has been applied to the form (upload response or poll), or on restore when persisted extraction exists — not before. */
  const formatWarmBrowserFailure = useCallback((err: unknown): string => {
    const raw = err instanceof Error ? err.message : String(err);
    const unreachable =
      /502|503|504|Service unavailable|ECONNREFUSED|Failed to fetch|Load failed|Cannot connect|network error/i.test(
        raw
      );
    if (unreachable) {
      console.warn("[Add Sales] DMS warm-browser:", raw);
      return "DMS pre-open did not run (API or proxy unreachable — start backend on :8000, use Vite dev with VITE_API_URL unset, then refresh). You can still press Create Invoice when the API is up.";
    }
    return raw.length > 280 ? `${raw.slice(0, 280)}…` : raw;
  }, []);

  const triggerWarmBrowser = useCallback(
    (subfolder: string) => {
      const sf = (subfolder || "").trim();
      const url = (dmsUrl ?? "").trim();
      if (!sf || !url || siteUrlsLoading || siteUrlsError) return;
      if (dmsWarmSubfolderRef.current === sf) return;
      dmsWarmSubfolderRef.current = sf;
      void warmDmsBrowser({ dms_base_url: url }).catch((err) => {
        setFillDmsStatus(`DMS warm-up did not finish: ${formatWarmBrowserFailure(err)}`);
      });
    },
    [dmsUrl, siteUrlsLoading, siteUrlsError, formatWarmBrowserFailure]
  );

  const applyExtractedDetails = useCallback(
    (
      details: { vehicle?: unknown; customer?: unknown; insurance?: unknown },
      opts?: { savedToForWarm?: string }
    ) => {
      const rawVehicle = details?.vehicle ?? details;
      const normalized = normalizeVehicleDetails(rawVehicle);
      if (normalized) setExtractedVehicle(normalized);
      const cust = details?.customer;
      if (cust && typeof cust === "object" && !Array.isArray(cust)) {
        const rec = cust as Record<string, unknown>;
        setExtractedCustomer(mapApiCustomerToExtracted(rec));
        const mobRaw = rec.mobile_number ?? rec.mobile;
        if (mobRaw != null) {
          const digits = String(mobRaw).replace(/\D/g, "").slice(-10);
          if (digits.length === 10) setMobile(digits);
        }
      }
      const ins = details?.insurance;
      if (ins && typeof ins === "object" && !Array.isArray(ins)) {
        setInsuranceReadByTextract(true);
        const r = ins as Record<string, unknown>;
        setExtractedInsurance((prev) => mergeInsuranceFromOcrPayload(prev, r));
      }
      const sf = (opts?.savedToForWarm ?? "").trim();
      if (sf && detailsHasOcrPayloadForWarm(details)) {
        triggerWarmBrowser(sf);
      }
    },
    [triggerWarmBrowser]
  );

  const {
    upload,
    uploadV2,
    uploadConsolidatedV2,
    isUploading,
    isMobileValid,
    clearUploaded,
  } = useUploadScans("", mobile, {
    savedTo,
    setSavedTo,
    uploadedFiles,
    setUploadedFiles,
    uploadStatus,
    setUploadStatus,
    onExtractionComplete: (details, ctx) => {
      applyExtractedDetails(details, { savedToForWarm: ctx.savedTo });
    },
    onUploadSuccess: () => {
      setFillDmsStatus(null);
      setDmsMilestones([]);
      setDmsBannerIsStepMessages(false);
      setDmsRunEndedWithError(false);
      setDmsScrapedVehicle(null);
      setDmsPdfsDownloaded(false);
    },
    onManualFallback: (payload, _warning, scannerArchive) => {
      setManualFallbackPayload(payload);
      if (scannerArchive) setPendingScannerArchiveMove(scannerArchive);
      setExtractionError(null);
      setManualFormOnly(false);
      setExtractedCustomer((prev) => prev ?? {});
      setExtractedVehicle((prev) => prev ?? {});
      setExtractedInsurance((prev) => prev ?? {});
    },
  }, dealerId);

  const pollCountRef = useRef(0);
  /** Subfolder for which DMS warm-browser has already been triggered. */
  const dmsWarmSubfolderRef = useRef<string | null>(null);
  /** Fewer, slower polls to avoid hammering laptop / backend when OCR is slow. */
  const POLL_MAX = 5;
  const POLL_INTERVAL_MS = 10000;

  /** Submit Info succeeded and server returned a draft staging handle (masters commit after Create Invoice). */
  const submitInfoActionsComplete = hasSubmittedInfo && lastStagingId != null && lastStagingId.trim() !== "";
  /** Committed master ids (from Create Invoice response or legacy session). Needed for insurance / RTO queue. */
  const hasCommittedSaleIds = lastSubmittedCustomerId != null && lastSubmittedVehicleId != null;

  // Persist form state so it survives navigation; clear only on "New"
  useEffect(() => {
    saveAddSalesForm({
      mobile,
      savedTo,
      uploadedFiles,
      uploadStatus,
      dmsScrapedVehicle,
      hasSubmittedInfo,
      lastSubmittedCustomerId,
      lastSubmittedVehicleId,
      lastStagingId,
      createInvoiceCompleted,
      generateInsuranceCompleted,
      extractedVehicle,
      extractedCustomer,
      extractedInsurance,
    });
  }, [
    mobile,
    savedTo,
    uploadedFiles,
    uploadStatus,
    dmsScrapedVehicle,
    hasSubmittedInfo,
    lastSubmittedCustomerId,
    lastSubmittedVehicleId,
    lastStagingId,
    createInvoiceCompleted,
    generateInsuranceCompleted,
    extractedVehicle,
    extractedCustomer,
    extractedInsurance,
  ]);

  // DMS and RTO sections populate only when user presses Fill Forms. No auto-fetch from file or DB.

  // Warn on close/refresh if customer processing not complete (forms not filled or print forms not done)
  useEffect(() => {
    const message = "Customer processing is not complete and the information will be lost.";
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      if (submitInfoActionsComplete && (!dmsPdfsDownloaded || !hasPrintedForms)) {
        e.preventDefault();
        e.returnValue = message;
        return message;
      }
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [submitInfoActionsComplete, dmsPdfsDownloaded, hasPrintedForms]);

  const mobileRow = (
    <div className="app-field-row">
      <label className="app-field" htmlFor="add-sales-mobile">
        <div className="app-field-label">Customer Mobile</div>
        <input
          id="add-sales-mobile"
          name="mobile"
          className="app-field-input"
          inputMode="numeric"
          placeholder="9876543210"
          value={mobile}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D/g, "").slice(0, 10);
            setMobile(digits);
          }}
          aria-invalid={mobile.length > 0 && !isMobileValid}
        />
      </label>
    </div>
  );

  const refreshCreateInvoiceEligibility = useCallback(async () => {
    if (!submitInfoActionsComplete) {
      setCreateInvoiceEligibilityLoading(false);
      setCreateInvoiceEnabled(false);
      setCreateInvoiceEligibilityReason(null);
      setGenerateInsuranceEnabled(false);
      setGenerateInsuranceReason(null);
      setCreateInvoiceCompleted(false);
      setGenerateInsuranceCompleted(false);
      return;
    }
    const dmsVeh = normalizeVehicleDetails(dmsScrapedVehicle) ?? dmsScrapedVehicle;
    const ocrVeh = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
    const ch = (dmsVeh?.frame_no ?? ocrVeh?.frame_no ?? "").trim();
    const eng = (dmsVeh?.engine_no ?? ocrVeh?.engine_no ?? "").trim();
    const mob = mobile.trim();
    if (!ch || !eng || !mob) {
      setCreateInvoiceEligibilityLoading(false);
      setCreateInvoiceEnabled(false);
      setCreateInvoiceEligibilityReason(
        "Enter mobile, chassis, and engine in Section 2 before Create Invoice."
      );
      setGenerateInsuranceEnabled(false);
      setGenerateInsuranceReason(null);
      return;
    }
    setCreateInvoiceEligibilityLoading(true);
    try {
      const res = await fetchCreateInvoiceEligibility({
        chassisNum: ch,
        engineNum: eng,
        mobile: mob,
      });
      setCreateInvoiceEnabled(res.create_invoice_enabled);
      setCreateInvoiceEligibilityReason(res.reason);
      setGenerateInsuranceEnabled(res.generate_insurance_enabled);
      setGenerateInsuranceReason(res.generate_insurance_reason);
      if (res.resolved_customer_id != null) {
        setLastSubmittedCustomerId(res.resolved_customer_id);
      }
      if (res.resolved_vehicle_id != null) {
        setLastSubmittedVehicleId(res.resolved_vehicle_id);
      }
    } catch (e) {
      setCreateInvoiceEnabled(false);
      setCreateInvoiceEligibilityReason(
        e instanceof Error ? e.message : "Could not verify invoice status for this sale."
      );
      setGenerateInsuranceEnabled(false);
      setGenerateInsuranceReason(
        e instanceof Error ? e.message : "Could not verify insurance eligibility for this sale."
      );
    } finally {
      setCreateInvoiceEligibilityLoading(false);
    }
  }, [submitInfoActionsComplete, mobile, extractedVehicle, dmsScrapedVehicle]);

  useEffect(() => {
    void refreshCreateInvoiceEligibility();
  }, [refreshCreateInvoiceEligibility]);

  const handleNew = () => {
    clearAddSalesForm();
    setMobile("");
    clearUploaded();
    setExtractedVehicle(null);
    setExtractedCustomer(null);
    setExtractedInsurance(null);
    setExtractionError(null);
    setManualFallbackPayload(null);
    setPendingScannerArchiveMove(null);
    setManualFormOnly(false);
    setInsuranceReadByTextract(false);
    setDmsScrapedVehicle(null);
    setDmsPdfsDownloaded(false);
    setFillDmsStatus(null);
    setDmsMilestones([]);
    setDmsBannerIsStepMessages(false);
    setDmsRunEndedWithError(false);
    setFillInsuranceStatus(null);
    setPrintFormsStatus(null);
    setHasSubmittedInfo(false);
    setHasPrintedForms(false);
    setLastSubmittedCustomerId(null);
    setLastSubmittedVehicleId(null);
    setLastStagingId(null);
    setCreateInvoiceEligibilityLoading(false);
    setCreateInvoiceEnabled(false);
    setCreateInvoiceEligibilityReason(null);
    setGenerateInsuranceEnabled(false);
    setGenerateInsuranceReason(null);
    setCreateInvoiceCompleted(false);
    setGenerateInsuranceCompleted(false);
    setFormResetKey((k) => k + 1);
  };

  useEffect(() => {
    if ((autoNewTrigger ?? 0) > 0) {
      handleNew();
    }
    // Intentionally reacts only to trigger increments from App (POS tile entry).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoNewTrigger]);

  // Normalize for display so we always show known fields (frame_no, engine_no, etc.) regardless of key naming
  const v = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
  const c = extractedCustomer;
  const ins = extractedInsurance;
  const alternateMobileRow = (
    <div className="app-field-row">
      <label className="app-field" htmlFor="add-sales-alt-phone">
        <div className="app-field-label">Alternate No.</div>
        <input
          id="add-sales-alt-phone"
          name="alt_phone_num"
          className="app-field-input"
          inputMode="numeric"
          placeholder="9876543210"
          value={c?.alt_phone_num ?? ""}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D/g, "").slice(0, 10);
            setExtractedCustomer((prev) => ({
              ...(prev ?? {}),
              alt_phone_num: digits,
            }));
          }}
        />
      </label>
    </div>
  );

  /** Required Section 2 text fields (financier optional). Date of birth and mobiles validated separately. */
  const requiredTextFieldChecks: { label: string; value: string | undefined }[] = [
    { label: "Name", value: c?.name },
    { label: "Gender", value: c?.gender },
    { label: "Address", value: c ? buildDisplayAddress(c) : undefined },
    { label: "Key no.", value: v?.key_no },
    { label: "Chassis No.", value: v?.frame_no },
    { label: "Engine no.", value: v?.engine_no },
    { label: "Battery no.", value: v?.battery_no },
    { label: "Customer Profession", value: ins?.profession },
    { label: "Customer Marital Status", value: ins?.marital_status },
    { label: "Nominee Name", value: ins?.nominee_name },
    { label: "Nominee Age", value: ins?.nominee_age },
    { label: "Nominee Relationship", value: ins?.nominee_relationship },
    { label: "Nominee Gender", value: ins?.nominee_gender },
  ];

  const getSection2SubmitIssues = (): string[] => {
    const issues: string[] = [];
    if (!/^\d{10}$/.test(mobile.trim())) issues.push("Customer Mobile (10 digits)");
    if (!/^\d{10}$/.test((c?.alt_phone_num ?? "").trim())) issues.push("Alternate No. (10 digits)");
    if (!isValidDdMmYyyy(c?.date_of_birth)) issues.push("DOB (valid DD/MM/YYYY)");
    if (!(c?.care_of ?? "").trim()) issues.push("C/O");
    if (!/^\d{4}$/.test((c?.aadhar_id ?? "").trim())) issues.push("Aadhar (last 4 digits)");
    requiredTextFieldChecks.forEach(({ label, value }) => {
      if (value == null || String(value).trim() === "" || String(value).trim() === "—") {
        issues.push(label);
      }
    });
    return issues;
  };

  const hasAllRequiredExtractedFields = () => getSection2SubmitIssues().length === 0;

  /** Allowed: letters, digits, space, hyphen, period, slash, comma. No other special characters. */
  const ALLOWED_CHAR_REGEX = /^[a-zA-Z0-9\s\-./,]*$/;
  const isBlank = (val: string | undefined | null): boolean =>
    val == null || String(val).trim() === "" || String(val).trim() === "—";
  const hasDisallowedSpecialChars = (val: string | undefined | null): boolean =>
    val != null && String(val).trim() !== "" && !ALLOWED_CHAR_REGEX.test(String(val).trim());

  const vehicleValidationFields: { label: string; value: string | undefined }[] = [
    { label: "Key no.", value: v?.key_no },
    { label: "Chassis No.", value: v?.frame_no },
    { label: "Engine no.", value: v?.engine_no },
    { label: "Battery no.", value: v?.battery_no },
  ];
  const getVehicleValidationErrors = (): string[] => {
    const errors: string[] = [];
    vehicleValidationFields.forEach(({ label, value }) => {
      if (isBlank(value)) errors.push(`${label} is required`);
      else if (hasDisallowedSpecialChars(value)) errors.push(`${label} must not contain special characters`);
    });
    return errors;
  };

  const insuranceValidationFields: { label: string; value: string | undefined }[] = [
    { label: "Customer Profession", value: ins?.profession },
    { label: "Customer Marital Status", value: ins?.marital_status },
    { label: "Nominee Name", value: ins?.nominee_name },
    { label: "Nominee Age", value: ins?.nominee_age },
    { label: "Nominee Relationship", value: ins?.nominee_relationship },
    { label: "Nominee Gender", value: ins?.nominee_gender },
  ];
  const isValidNomineeAge = (val: string | undefined | null): boolean => {
    if (val == null || String(val).trim() === "") return true;
    const s = String(val).trim();
    if (!/^\d+$/.test(s)) return false;
    const n = parseInt(s, 10);
    return n >= 1 && n <= 150;
  };

  const getInsuranceValidationErrors = (): string[] => {
    const errors: string[] = [];
    insuranceValidationFields.forEach(({ label, value }) => {
      if (isBlank(value)) errors.push(`${label} is required`);
      else if (label === "Nominee Age") {
        if (!isValidNomineeAge(value)) errors.push("Nominee Age must be a number (1–150)");
        else if (hasDisallowedSpecialChars(value)) errors.push(`${label} must not contain special characters`);
      } else if (hasDisallowedSpecialChars(value)) errors.push(`${label} must not contain special characters`);
    });
    return errors;
  };

  const vehicleValidationErrors = savedTo ? getVehicleValidationErrors() : [];
  const insuranceValidationErrors = savedTo ? getInsuranceValidationErrors() : [];
  const hasVehicleOrInsuranceValidationErrors =
    savedTo && (vehicleValidationErrors.length > 0 || insuranceValidationErrors.length > 0);

  const hasMeaningfulCustomer = (cust: typeof c) =>
    cust && (cust.aadhar_id || cust.name || cust.address || buildDisplayAddress(cust) !== "—");
  const hasMeaningfulInsurance = (i: typeof ins) =>
    Boolean(
      i &&
        [
          i.profession,
          i.nominee_name,
          i.nominee_age,
          i.nominee_relationship,
          i.insurer ?? preferInsurer,
          i.policy_from,
          i.policy_to,
          i.premium,
        ].some((x) => x != null && String(x).trim() !== "")
    );

  /** Show per-subsection status while files upload (before savedTo) and while OCR/extraction is still filling that block. */
  const customerProcessing = Boolean(
    !(manualFormOnly && savedTo) &&
      (isUploading || savedTo) &&
      !hasMeaningfulCustomer(c)
  );
  const vehicleProcessing = Boolean(
    !(manualFormOnly && savedTo) && (isUploading || savedTo) && !hasVehicleData(v ?? null)
  );
  const insuranceProcessing = Boolean(
    !(manualFormOnly && savedTo) && (isUploading || savedTo) && !hasMeaningfulInsurance(ins)
  );
  const hasSuppliedInsuranceDoc = uploadedFiles.some((f) =>
    /insurance/i.test(String(f || ""))
  );
  /** Don't show errors until Textract/Tesseract have finished extracting all subsections */
  const extractionComplete = !customerProcessing && !vehicleProcessing && !insuranceProcessing;

  /** When true, polling is not needed; use this as effect deps so we don't restart the interval on every field merge. */
  const extractionSectionsDone =
    Boolean(savedTo) &&
    (manualFormOnly ||
      (hasMeaningfulCustomer(c) && hasVehicleData(v ?? null) && hasMeaningfulInsurance(ins)));

  const ocrWaitActive =
    !extractionError &&
    !manualFormOnly &&
    manualFallbackPayload == null &&
    (isUploading || (Boolean(savedTo) && !extractionSectionsDone));

  const [ocrCountdownSec, setOcrCountdownSec] = useState(ADD_SALES_OCR_COUNTDOWN_START_SEC);

  useEffect(() => {
    if (!ocrWaitActive) {
      setOcrCountdownSec(ADD_SALES_OCR_COUNTDOWN_START_SEC);
      return;
    }
    setOcrCountdownSec(ADD_SALES_OCR_COUNTDOWN_START_SEC);
    const id = setInterval(() => {
      setOcrCountdownSec((s) => Math.max(0, s - 1));
    }, 1000);
    return () => clearInterval(id);
  }, [ocrWaitActive]);

  // Poll for extracted details until customer, vehicle, and insurance blocks match the same "complete" rules as the UI.
  useEffect(() => {
    if (manualFormOnly) {
      pollCountRef.current = 0;
      return;
    }
    if (!savedTo) {
      pollCountRef.current = 0;
      setExtractionError(null);
      return;
    }
    if (extractionSectionsDone) {
      pollCountRef.current = 0;
      return;
    }
    if (!pageVisible) {
      return;
    }
    pollCountRef.current = 0;
    setExtractionError(null);

    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      if (pollCountRef.current >= POLL_MAX) {
        if (intervalId) clearInterval(intervalId);
        return;
      }
      pollCountRef.current += 1;
      try {
        const details = await getExtractedDetails(savedTo, dealerId);
        if (!details) return;
        const dmeta = details as unknown as Record<string, unknown>;
        const extractionErr = dmeta?.extraction_error;
        const nameMismatchErr = dmeta?.name_mismatch_error;
        const err = typeof nameMismatchErr === "string" ? nameMismatchErr : typeof extractionErr === "string" ? extractionErr : null;
        setExtractionError(err);
        if (err && intervalId) clearInterval(intervalId);
        applyExtractedDetails(details, { savedToForWarm: savedTo });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setExtractionError(msg);
        if (intervalId) clearInterval(intervalId);
      }
    };

    poll();
    intervalId = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [savedTo, extractionSectionsDone, dealerId, pageVisible, applyExtractedDetails, manualFormOnly]);

  useEffect(() => {
    if (!savedTo) {
      dmsWarmSubfolderRef.current = null;
    }
  }, [savedTo]);

  useEffect(() => {
    if (!savedTo || !pageVisible) return;
    if (!hasMeaningfulCustomer(c) && !hasVehicleData(v ?? null) && !hasMeaningfulInsurance(ins)) return;
    triggerWarmBrowser(savedTo);
  }, [savedTo, pageVisible, c, v, ins, triggerWarmBrowser]);

  useEffect(() => {
    if (hasSuppliedInsuranceDoc) {
      setFillInsuranceStatus("Insurance document supplied. Insurance section is populated from document extraction.");
    }
  }, [hasSuppliedInsuranceDoc]);

  const handleFillDms = async () => {
    if (!savedTo) {
      setFillDmsStatus("Upload scans first.");
      return;
    }
    if (!submitInfoActionsComplete) {
      setFillDmsStatus("Complete Submit Info (Section 2) before Create Invoice.");
      return;
    }
    if (!dmsUrl) {
      setFillDmsStatus("DMS URL is not available from the server. Check backend configuration and refresh this page.");
      return;
    }
    const c = extractedCustomer;
    const v = extractedVehicle;

    setIsFillDmsLoading(true);
    setFillDmsStatus(null);
    setDmsMilestones([]);
    setDmsBannerIsStepMessages(false);
    setDmsRunEndedWithError(false);
    let dmsRes: Awaited<ReturnType<typeof fillDmsOnly>> | null = null;
    try {
      dmsRes = await fillDmsOnly({
        subfolder: savedTo,
        dms_base_url: dmsUrl,
        dealer_id: dealerId,
        staging_id: lastStagingId ?? undefined,
        ...(lastStagingId
          ? {}
          : {
              customer_id: lastSubmittedCustomerId ?? undefined,
              vehicle_id: lastSubmittedVehicleId ?? undefined,
            }),
        customer: {
          name: c?.name ?? undefined,
          care_of: c?.care_of ?? undefined,
          address: c?.address ?? buildDisplayAddress(c),
          city: c?.city ?? undefined,
          state: c?.state ?? undefined,
          pin_code: c?.pin_code ?? undefined,
          mobile_number: mobile ?? undefined,
        },
        vehicle: {
          key_no: v?.key_no ?? undefined,
          frame_no: v?.frame_no ?? undefined,
          engine_no: v?.engine_no ?? undefined,
        },
      });
      const scraped = dmsRes.vehicle;
      const hasAnyVehicle = !!(scraped && typeof scraped === "object" && (
        (scraped.key_num && String(scraped.key_num).trim()) ||
        (scraped.full_chassis && String(scraped.full_chassis).trim()) ||
        (scraped.full_engine && String(scraped.full_engine).trim()) ||
        (scraped.frame_num && String(scraped.frame_num).trim()) ||
        (scraped.engine_num && String(scraped.engine_num).trim()) ||
        (scraped.model && String(scraped.model).trim()) ||
        (scraped.color && String(scraped.color).trim()) ||
        (scraped.cubic_capacity && String(scraped.cubic_capacity).trim()) ||
        (scraped.seating_capacity && String(scraped.seating_capacity).trim()) ||
        (scraped.body_type && String(scraped.body_type).trim()) ||
        (scraped.vehicle_type && String(scraped.vehicle_type).trim()) ||
        (scraped.num_cylinders && String(scraped.num_cylinders).trim()) ||
        (scraped.vehicle_price && String(scraped.vehicle_price).trim()) ||
        (scraped.year_of_mfg && String(scraped.year_of_mfg).trim())
      ));
      if (hasAnyVehicle && scraped) {
        const frameResolved = scraped.full_chassis ?? scraped.frame_num ?? undefined;
        const engineResolved = scraped.full_engine ?? scraped.engine_num ?? undefined;
        setDmsScrapedVehicle(
          sanitizeExtractedVehicleDetailFields({
            key_no: scraped.key_num ?? undefined,
            frame_no: frameResolved,
            engine_no: engineResolved,
            full_chassis: scraped.full_chassis ?? undefined,
            full_engine: scraped.full_engine ?? undefined,
            model: scraped.model ?? undefined,
            color: scraped.color ?? undefined,
            cubic_capacity: scraped.cubic_capacity ?? undefined,
            seating_capacity: scraped.seating_capacity ?? undefined,
            body_type: scraped.body_type ?? undefined,
            vehicle_type: scraped.vehicle_type ?? undefined,
            num_cylinders: scraped.num_cylinders ?? undefined,
            vehicle_price: scraped.vehicle_price ?? undefined,
            year_of_mfg: scraped.year_of_mfg ?? undefined,
          }) as ExtractedVehicleDetails
        );
      }
      const pdfs = dmsRes.pdfs_saved ?? [];
      const hasForm21 = pdfs.some((f) => /form\s*21|form21/i.test(f));
      const hasForm22 = pdfs.some((f) => /form\s*22|form22/i.test(f));
      const hasInvoiceDetails = pdfs.some((f) => /invoice_details|invoice\s*details/i.test(f));
      if (hasForm21 && hasForm22 && hasInvoiceDetails) setDmsPdfsDownloaded(true);
      if (dmsRes.customer_id != null) setLastSubmittedCustomerId(dmsRes.customer_id);
      if (dmsRes.vehicle_id != null) setLastSubmittedVehicleId(dmsRes.vehicle_id);
      const narrative =
        Array.isArray(dmsRes.dms_step_messages) && dmsRes.dms_step_messages.length > 0
          ? dmsRes.dms_step_messages
          : [];
      const milestones = Array.isArray(dmsRes.dms_milestones) ? dmsRes.dms_milestones : [];
      setDmsBannerIsStepMessages(narrative.length > 0);
      setDmsMilestones(narrative.length > 0 ? narrative : milestones);
      if (!dmsRes.success) {
        setFillDmsStatus(dmsRes.error ?? "Create Invoice (DMS) failed.");
        setDmsRunEndedWithError(true);
      } else if (dmsRes.warning) {
        setFillDmsStatus(dmsRes.warning);
        setDmsRunEndedWithError(true);
      } else {
        setFillDmsStatus("DMS / Create Invoice run completed successfully.");
        setDmsRunEndedWithError(false);
      }
      if (dmsRes.success) {
        setCreateInvoiceCompleted(true);
      }
    } catch (err) {
      if (isFillDmsAbortError(err)) {
        setFillDmsStatus("Create Invoice request timed out. Check the upload folder for PDFs.");
      } else {
        setFillDmsStatus(err instanceof Error ? err.message : "Create Invoice (DMS) failed.");
      }
      setDmsRunEndedWithError(true);
    } finally {
      setIsFillDmsLoading(false);
      void (async () => {
        const scraped = dmsRes?.vehicle;
        const dmsFrame = (scraped?.full_chassis ?? scraped?.frame_num ?? "").trim();
        const dmsEngine = (scraped?.full_engine ?? scraped?.engine_num ?? "").trim();
        const ocrVeh = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
        const ch = dmsFrame || (ocrVeh?.frame_no ?? "").trim();
        const eng = dmsEngine || (ocrVeh?.engine_no ?? "").trim();
        const mob = mobile.trim();
        if (ch && eng && mob) {
          setCreateInvoiceEligibilityLoading(true);
          try {
            const res = await fetchCreateInvoiceEligibility({ chassisNum: ch, engineNum: eng, mobile: mob });
            setCreateInvoiceEnabled(res.create_invoice_enabled);
            setCreateInvoiceEligibilityReason(res.reason);
            setGenerateInsuranceEnabled(res.generate_insurance_enabled);
            setGenerateInsuranceReason(res.generate_insurance_reason);
            if (res.resolved_customer_id != null) setLastSubmittedCustomerId(res.resolved_customer_id);
            if (res.resolved_vehicle_id != null) setLastSubmittedVehicleId(res.resolved_vehicle_id);
          } catch {
            await refreshCreateInvoiceEligibility();
          } finally {
            setCreateInvoiceEligibilityLoading(false);
          }
        } else {
          await refreshCreateInvoiceEligibility();
        }
        if (dmsRes?.success && dmsRes.ready_for_client_create_invoice) {
          setCreateInvoiceEnabled(true);
          setCreateInvoiceEligibilityReason(
            "Siebel My Orders already shows an invoice for this mobile — use Create Invoice to commit masters."
          );
        }
      })();
    }
  };

  const handleFillInsurance = async () => {
    if (!savedTo) {
      setFillInsuranceStatus("Upload scans first.");
      return;
    }
    if (!submitInfoActionsComplete) {
      setFillInsuranceStatus("Complete Submit Info (Section 2) before Generate Insurance.");
      return;
    }
    if (!hasCommittedSaleIds) {
      setFillInsuranceStatus(
        "Run Create Invoice (DMS) successfully first so customer and vehicle IDs exist for insurance."
      );
      return;
    }
    if (siteUrlsError || siteUrlsLoading) {
      setFillInsuranceStatus("Site URLs are not ready. Check backend/.env (INSURANCE_BASE_URL) and refresh.");
      return;
    }
    if (hasSuppliedInsuranceDoc) {
      setFillInsuranceStatus("Insurance document supplied. Generate Insurance is disabled.");
      return;
    }
    setIsFillInsuranceLoading(true);
    setFillInsuranceStatus(null);
    try {
      const insuranceRes = await fillHeroInsurance({
        subfolder: savedTo,
        dealer_id: dealerId,
        customer_id: lastSubmittedCustomerId ?? undefined,
        vehicle_id: lastSubmittedVehicleId ?? undefined,
        staging_id: lastStagingId ?? undefined,
      });
      if (!insuranceRes.success) {
        setFillInsuranceStatus(insuranceRes.error ?? "Generate Insurance (Hero) failed.");
      } else {
        setFillInsuranceStatus("Hero Insurance run completed (pre + main + post). Browser may remain open for operator.");
        setGenerateInsuranceCompleted(true);
      }
    } catch (insuranceErr) {
      if (isFillDmsAbortError(insuranceErr)) {
        setFillInsuranceStatus("Insurance request timed out. Browser remains open for operator.");
      } else {
        setFillInsuranceStatus(insuranceErr instanceof Error ? insuranceErr.message : "Insurance fill failed.");
      }
    } finally {
      setIsFillInsuranceLoading(false);
      void refreshCreateInvoiceEligibility();
    }
  };

  const handlePrintForms = async () => {
    if (!savedTo) {
      setPrintFormsStatus("Upload scans first.");
      return;
    }
    const c = extractedCustomer;
    const v = extractedVehicle;
    const scrapedForGatePass = dmsScrapedVehicle as Record<string, unknown> | null;
    let vehicleDataForGatePass: Record<string, unknown> = {};
    if (scrapedForGatePass && typeof scrapedForGatePass === "object") {
      const s = scrapedForGatePass as Record<string, unknown>;
      vehicleDataForGatePass = {
        key_no: s.key_num ?? s.key_no,
        frame_no: s.full_chassis ?? s.frame_num ?? s.frame_no,
        engine_no: s.full_engine ?? s.engine_num ?? s.engine_no,
        model: s.model,
        color: s.color,
        cubic_capacity: s.cubic_capacity,
        seating_capacity: s.seating_capacity,
        body_type: s.body_type,
        vehicle_type: s.vehicle_type,
        num_cylinders: s.num_cylinders,
        vehicle_price: s.vehicle_price,
        year_of_mfg: s.year_of_mfg,
      };
    } else if (v) {
      vehicleDataForGatePass = {
        key_no: v.key_no,
        frame_no: v.frame_no,
        engine_no: v.engine_no,
        model: v.model ?? v.model_colour,
        color: v.color,
      };
    }

    setIsPrintFormsLoading(true);
    setPrintFormsStatus(null);

    const statusLines: string[] = [];

    if (lastSubmittedCustomerId != null && lastSubmittedVehicleId != null) {
      try {
        await insertRtoPayment({
          customer_id: lastSubmittedCustomerId,
          vehicle_id: lastSubmittedVehicleId,
          dealer_id: dealerId,
          customer_mobile: mobile ?? undefined,
          staging_id: lastStagingId?.trim() || undefined,
          status: "Queued",
        });
        statusLines.push("Added to RTO Queue.");
      } catch (queueErr) {
        setPrintFormsStatus(
          queueErr instanceof Error ? `RTO queue: ${queueErr.message}` : "RTO queue insert failed."
        );
        setIsPrintFormsLoading(false);
        return;
      }
    } else {
      statusLines.push("RTO queue skipped (customer/vehicle IDs missing — run Create Invoice first).");
    }

    try {
      // Form 20 generation (print-form20) — disabled; Gate Pass only below.
      // const form20Res = await printForm20({
      //   subfolder: savedTo,
      //   customer: {
      //     name: c?.name ?? undefined,
      //     care_of: c?.care_of ?? undefined,
      //     address: c?.address ?? buildDisplayAddress(c),
      //     city: c?.city ?? undefined,
      //     state: c?.state ?? undefined,
      //     pin_code: c?.pin_code ?? undefined,
      //     aadhar_id: c?.aadhar_id ?? undefined,
      //   },
      //   vehicle: vehicleDataForGatePass,
      //   vehicle_id: lastSubmittedVehicleId ?? undefined,
      //   dealer_id: dealerId,
      // });
      // if (form20Res.success) {
      //   setHasPrintedForms(true);
      //   statusLines.push(`Form 20 saved: ${(form20Res.pdfs_saved ?? []).join(", ")}`);
      // } else if (form20Res.error) {
      //   statusLines.push(`Form 20: ${form20Res.error}`);
      // }

      const gatePassRes = await printGatePass({
        subfolder: savedTo,
        customer: {
          name: c?.name ?? undefined,
          care_of: c?.care_of ?? undefined,
          address: c?.address ?? buildDisplayAddress(c),
          city: c?.city ?? undefined,
          state: c?.state ?? undefined,
          pin_code: c?.pin_code ?? undefined,
          aadhar_id: c?.aadhar_id ?? undefined,
        },
        vehicle: vehicleDataForGatePass,
        vehicle_id: lastSubmittedVehicleId ?? undefined,
        dealer_id: dealerId,
      });
      if (gatePassRes.success) {
        setHasPrintedForms(true);
        statusLines.push(`Gate Pass saved: ${(gatePassRes.pdfs_saved ?? []).join(", ")}`);
      } else if (gatePassRes.error) {
        statusLines.push(`Gate Pass: ${gatePassRes.error}`);
      }
    } catch (printErr) {
      statusLines.push(
        `Gate Pass: ${printErr instanceof Error ? printErr.message : "Generate & print failed."}`
      );
    } finally {
      setIsPrintFormsLoading(false);
    }

    setPrintFormsStatus(statusLines.join(" "));
  };

  const d = dmsScrapedVehicle;

  const createInvoiceButtonTitle =
    isSubmitting
      ? "Wait for Submit Info to finish."
      : createInvoiceCompleted
        ? "Create Invoice already completed for this sale."
        : !submitInfoActionsComplete
        ? "Complete Submit Info (Section 2) — staging must be saved to the server."
        : dealerId == null || dealerId <= 0
        ? "Dealer is not configured."
        : createInvoiceEligibilityLoading
          ? "Checking whether an invoice is already recorded…"
          : !createInvoiceEnabled
            ? createInvoiceEligibilityReason ?? "Create Invoice is not available for this sale."
            : !dmsUrl || siteUrlsError
              ? "DMS base URL is not available from the server (check backend configuration)"
              : undefined;

  const generateInsuranceButtonTitle =
    isSubmitting
      ? "Wait for Submit Info to finish."
      : generateInsuranceCompleted
        ? "Generate Insurance already completed for this sale."
        : !submitInfoActionsComplete
        ? "Complete Submit Info (Section 2) — staging must be saved to the server."
        : !hasCommittedSaleIds
          ? "Run Create Invoice (DMS) successfully first so master IDs exist for insurance automation."
          : hasSuppliedInsuranceDoc
            ? "Insurance document supplied; values come from document extraction"
            : dealerId == null || dealerId <= 0
              ? "Dealer is not configured."
              : createInvoiceEligibilityLoading
                ? "Checking eligibility…"
                : !generateInsuranceEnabled
                  ? generateInsuranceReason ?? "Generate Insurance is not available for this sale."
                  : siteUrlsError
                    ? "Configure site URLs in backend/.env"
                    : undefined;

  /** Same disabled logic as each primary button — used for Print Forms gate. */
  const newButtonDisabled =
    isFillDmsLoading ||
    isFillInsuranceLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    (submitInfoActionsComplete && !hasPrintedForms);

  const submitInfoPrimaryButtonDisabled =
    isSubmitting ||
    !c ||
    (!manualFormOnly && !insuranceReadByTextract) ||
    !hasAllRequiredExtractedFields() ||
    hasVehicleOrInsuranceValidationErrors ||
    !!extractionError ||
    submitInfoActionsComplete;

  const createInvoicePrimaryButtonDisabled =
    isFillDmsLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    !submitInfoActionsComplete ||
    createInvoiceEligibilityLoading ||
    createInvoiceCompleted ||
    !createInvoiceEnabled ||
    siteUrlsLoading ||
    !!siteUrlsError ||
    !dmsUrl;

  const generateInsurancePrimaryButtonDisabled =
    isFillInsuranceLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    !submitInfoActionsComplete ||
    !hasCommittedSaleIds ||
    createInvoiceEligibilityLoading ||
    generateInsuranceCompleted ||
    !generateInsuranceEnabled ||
    hasSuppliedInsuranceDoc ||
    siteUrlsLoading ||
    !!siteUrlsError;

  /** Print only when the other four actions are inactive; after first print, `hasPrintedForms` allows re-print while New is enabled again. */
  const printFormsButtonEnabled =
    submitInfoActionsComplete &&
    !isSubmitting &&
    !isPrintFormsLoading &&
    !createInvoiceEligibilityLoading &&
    submitInfoPrimaryButtonDisabled &&
    createInvoicePrimaryButtonDisabled &&
    generateInsurancePrimaryButtonDisabled &&
    (newButtonDisabled || hasPrintedForms);

  const printFormsButtonTitle =
    isSubmitting
      ? "Wait for Submit Info to finish."
      : !submitInfoActionsComplete
        ? "Complete Submit Info (Section 2) first."
        : createInvoiceEligibilityLoading
          ? "Wait for eligibility check to finish."
          : !printFormsButtonEnabled
            ? "Available only when New, Submit Info, Create Invoice, and Generate Insurance are all inactive for this sale (invoice recorded; insurance step finished)."
            : undefined;

  const panel = (
    <UploadScansPanel
      key={formResetKey}
      isUploading={isUploading}
      onUpload={upload}
      uploadStatus={uploadStatus}
      uploadedFiles={uploadedFiles}
      savedTo={savedTo}
      mobile={mobile}
      isMobileValid={isMobileValid}
      onUploadV2={uploadV2}
      onUploadConsolidated={uploadConsolidatedV2}
      ocrCountdownSeconds={ocrWaitActive ? ocrCountdownSec : null}
      showIndividualFileUploadToggle={showIndividualFileUploadToggle}
    />
  );

  return (
    <div className="add-sales-v2">
      <main className="add-sales-v2-main">
        {dmsMilestones.length > 0 && (
          <div className="add-sales-v2-dms-milestones-banner" role="status" aria-label="DMS steps completed">
            <span className="add-sales-v2-dms-milestones-title">
              {dmsBannerIsStepMessages ? "DMS progress" : "DMS completed"}
            </span>
            <span className="add-sales-v2-dms-milestones-list">
              {dmsMilestones.map((line, i) => (
                <span key={`dms-banner-${i}`} className="add-sales-v2-dms-milestone-item">
                  <span className="add-sales-v2-dms-milestone-check" aria-hidden>
                    ✓
                  </span>
                  {line}
                </span>
              ))}
            </span>
          </div>
        )}
        <div className="add-sales-v2-three-col">
          <section className="add-sales-v2-box add-sales-v2-box-upload">
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">1. Upload Customer Scans</h2>
                <button
                  type="button"
                  className="app-button app-button--primary"
                  disabled={newButtonDisabled}
                  onClick={handleNew}
                  title={
                    isFillDmsLoading || isFillInsuranceLoading || isPrintFormsLoading || isSubmitting
                      ? "Wait for the current action to finish."
                      : submitInfoActionsComplete && !hasPrintedForms
                        ? "Use Print Forms and Queue RTO first to unlock New for this sale."
                        : "Start a new entry"
                  }
                >
                  New
                </button>
              </div>
              <div className="add-sales-v2-box-body">
                <div className="add-sales-v2-panel-wrap">
                  {panel}
                </div>
                {manualFallbackPayload && (
                  <ManualFallbackSplitReview
                    payload={manualFallbackPayload}
                    dealerId={dealerId}
                    mobile={mobile}
                    isMobileValid={isMobileValid}
                    onApplied={(to, files) => {
                      setSavedTo(to);
                      setUploadedFiles(files);
                      setManualFallbackPayload(null);
                      setManualFormOnly(true);
                      setExtractedCustomer((x) => x ?? {});
                      setExtractedVehicle((x) => x ?? {});
                      setExtractedInsurance((x) => x ?? {});
                      setUploadStatus(`Documents saved to ${to}. Fill Section 2 manually.`);
                    }}
                    onDismiss={() => {
                      setManualFallbackPayload(null);
                      setPendingScannerArchiveMove(null);
                    }}
                  />
                )}
              </div>
          </section>
          <section className="add-sales-v2-box add-sales-v2-box-extracted">
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">2. AI Extracted Information</h2>
                <button
                    type="button"
                    className="app-button add-sales-v2-submit-btn"
                    disabled={submitInfoPrimaryButtonDisabled}
                    onClick={async () => {
                      if (!c) return;
                      if (!manualFormOnly && !insuranceReadByTextract) {
                        setSubmitStatus("Waiting for insurance details from document.");
                        return;
                      }
                      if (!hasAllRequiredExtractedFields()) {
                        setSubmitStatus(`Please fill or fix: ${getSection2SubmitIssues().join(", ")}.`);
                        return;
                      }
                      if (hasVehicleOrInsuranceValidationErrors) {
                        const parts = [...vehicleValidationErrors, ...insuranceValidationErrors];
                        setSubmitStatus(`Fix validation errors: ${parts.join("; ")}.`);
                        return;
                      }
                      setIsSubmitting(true);
                      setSubmitStatus(null);
                      try {
                        const submitRes = await submitInfo({
                          customer: c,
                          vehicle: v ?? null,
                          insurance: ins ?? null,
                          mobile,
                          fileLocation: savedTo,
                          dealerId,
                          oemId,
                          stagingId: lastStagingId,
                          preferInsurer,
                        });
                        let submitStatusMsg = "Saved";
                        const arch = pendingScannerArchiveMove;
                        if (arch) {
                          try {
                            await moveConsolidatedToProcessed(arch.fileHandle, arch.scannerRoot);
                            submitStatusMsg += ". Moved scan to processed folder.";
                            setUploadStatus((prev) => (prev ? `${prev} ` : "") + "Moved scan to processed folder.");
                            setPendingScannerArchiveMove(null);
                          } catch (e) {
                            const detail = e instanceof Error ? e.message : String(e);
                            submitStatusMsg += `. Could not move file to processed: ${detail}`;
                            setUploadStatus((prev) => (prev ? `${prev} ` : "") + `Could not move file to processed: ${detail}`);
                            setPendingScannerArchiveMove(null);
                          }
                        }
                        setSubmitStatus(submitStatusMsg);
                        setHasSubmittedInfo(true);
                        if (submitRes?.staging_id != null && String(submitRes.staging_id).trim())
                          setLastStagingId(String(submitRes.staging_id).trim());
                        const stored = loadAddSalesForm();
                        if (stored.reprocessBulkLoadId != null && savedTo) {
                          try {
                            await markBulkLoadSuccess(stored.reprocessBulkLoadId, savedTo);
                            saveAddSalesForm({ reprocessBulkLoadId: undefined });
                          } catch (e) {
                            setSubmitStatus(`Saved, but failed to update bulk queue: ${e instanceof Error ? e.message : "Unknown error"}`);
                          }
                        }
                      } catch (err) {
                        const msg = err instanceof Error ? err.message : "Submit failed";
                        setSubmitStatus(msg);
                      } finally {
                        setIsSubmitting(false);
                      }
                    }}
                  >
                    {isSubmitting ? "Saving..." : "Submit Info."}
                  </button>
              </div>
              <div className="add-sales-v2-box-body">
                <div className="add-sales-v2-fields-row add-sales-v2-fields-row--section2-identity">
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-mobile">{mobileRow}</div>
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-alt">{alternateMobileRow}</div>
                </div>
                <div
                  className={
                    !savedTo && !manualFallbackPayload ? "add-sales-v2-box--greyed" : ""
                  }
                >
                {extractionComplete && savedTo && !manualFormOnly && !insuranceReadByTextract && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <span className="add-sales-v2-status-text">Waiting for insurance details from document.</span>
                  </div>
                )}
                {extractionComplete && savedTo && (insuranceReadByTextract || manualFormOnly) && getSection2SubmitIssues().length > 0 && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <span className="add-sales-v2-status-text">
                      Please fill or fix: {getSection2SubmitIssues().join(", ")}.
                    </span>
                  </div>
                )}
                {submitStatus && (!savedTo || ((insuranceReadByTextract || manualFormOnly) && getSection2SubmitIssues().length === 0)) && (
                  <div className={`add-sales-v2-status-row ${submitStatus === "Saved" ? "add-sales-v2-status-row--success" : "add-sales-v2-status-row--error"}`}>
                    <StatusMessage message={submitStatus} className="add-sales-v2-status-text" role="status" />
                  </div>
                )}
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Customer Details</h3>
                    {customerProcessing && !extractionError && (
                      <span className="add-sales-v2-processing">{isUploading ? "Uploading…" : "Processing…"}</span>
                    )}
                  </div>
                  {extractionError && (
                    <div className="add-sales-v2-subsection-errors" role="alert">
                      <div className="add-sales-v2-subsection-error-item">{extractionError}</div>
                    </div>
                  )}
                  <dl className="add-sales-v2-dl add-sales-v2-dl--customer">
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--full">
                      <dt>Name</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={c?.name ?? ""}
                          onChange={(e) =>
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              name: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                          autoComplete="name"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--full">
                      <dt>C/O</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--care-of-free"
                          value={c?.care_of ?? ""}
                          onChange={(e) => {
                            const raw = sanitizeFormFieldValue(e.target.value);
                            const parsed = parseCareOfFromCombined(raw);
                            const has = raw.trim() !== "";
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              care_of: raw || undefined,
                              care_of_relation: has ? parsed.relation : undefined,
                              care_of_name: has ? parsed.name || undefined : undefined,
                            }));
                          }}
                          placeholder="C/o Father's Name"
                          autoComplete="off"
                          spellCheck={false}
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--full">
                      <dt>Address</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={c?.address ?? ""}
                          onChange={(e) =>
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              address: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--dob-gender">
                      <dt>Gender</dt>
                      <dd className="add-sales-v2-dd--gender-narrow">
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--gender-narrow"
                          value={c?.gender ?? ""}
                          onChange={(e) =>
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              gender: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                          autoComplete="sex"
                        />
                      </dd>
                      <dt>DOB</dt>
                      <dd className="add-sales-v2-dd--dob-full">
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--dob"
                          type="text"
                          inputMode="numeric"
                          autoComplete="bday"
                          value={c?.date_of_birth ?? ""}
                          onChange={(e) =>
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              date_of_birth: formatDobDigitsInput(e.target.value),
                            }))
                          }
                          placeholder="DD/MM/YYYY"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--full">
                      <dt>Aadhar (last 4 digits)</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--aadhar4"
                          inputMode="numeric"
                          autoComplete="off"
                          maxLength={4}
                          value={c?.aadhar_id ?? ""}
                          onChange={(e) => {
                            const digits = e.target.value.replace(/\D/g, "").slice(0, 4);
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              aadhar_id: digits,
                            }));
                          }}
                          placeholder="0000"
                        />
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Finance Details</h3>
                  </div>
                  <dl className="add-sales-v2-dl add-sales-v2-dl--insurance">
                    <div className="add-sales-v2-dl-row">
                      <dt>Financier</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.financier ?? ""}
                          onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              financier: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                          autoComplete="off"
                        />
                        {isHeroBajajFinancierForStaging(oemId, ins?.financier) && (
                          <p className="add-sales-v2-field-note">
                            This financier will be logged in systems as Hinduja.
                          </p>
                        )}
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Vehicle Details</h3>
                    {vehicleProcessing && !extractionError && (
                      <span className="add-sales-v2-processing">{isUploading ? "Uploading…" : "Processing…"}</span>
                    )}
                  </div>
                  {extractionComplete && vehicleValidationErrors.length > 0 && (
                    <div className="add-sales-v2-subsection-errors" role="alert">
                      {vehicleValidationErrors.map((msg) => (
                        <div key={msg} className="add-sales-v2-subsection-error-item">{msg}</div>
                      ))}
                    </div>
                  )}
                  <dl className="add-sales-v2-dl add-sales-v2-dl--vehicle">
                    <div className="add-sales-v2-dl-row">
                      <dt>Key no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.key_no ?? ""}
                          onChange={(e) =>
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              key_no: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Chassis No.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.frame_no ?? ""}
                          onChange={(e) =>
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              frame_no: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Engine no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.engine_no ?? ""}
                          onChange={(e) =>
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              engine_no: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Battery no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.battery_no ?? ""}
                          onChange={(e) =>
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              battery_no: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Insurance Details</h3>
                    {insuranceProcessing && !extractionError && (
                      <span className="add-sales-v2-processing">{isUploading ? "Uploading…" : "Processing…"}</span>
                    )}
                  </div>
                  {extractionComplete && insuranceValidationErrors.length > 0 && (
                    <div className="add-sales-v2-subsection-errors" role="alert">
                      {insuranceValidationErrors.map((msg) => (
                        <div key={msg} className="add-sales-v2-subsection-error-item">{msg}</div>
                      ))}
                    </div>
                  )}
                  <dl className="add-sales-v2-dl add-sales-v2-dl--insurance">
                    <div className="add-sales-v2-dl-row">
                      <dt>Customer Profession</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.profession ?? ""}
                          onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              profession: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Customer Marital Status</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.marital_status ?? ""}
                          onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              marital_status: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Nominee Name</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.nominee_name ?? ""}
                          onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              nominee_name: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Nominee Age</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          type="text"
                          inputMode="numeric"
                          pattern="[0-9]*"
                          value={ins?.nominee_age ?? ""}
                          onChange={(e) => {
                            const v = sanitizeNomineeAgeInput(e.target.value);
                            setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_age: v }));
                          }}
                          placeholder="e.g. 30"
                          title="Numbers only (1–150)"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Nominee Gender</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.nominee_gender ?? ""}
                          onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              nominee_gender: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Relationship</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.nominee_relationship ?? ""}
                          onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              nominee_relationship: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                          placeholder="—"
                        />
                      </dd>
                    </div>
                  </dl>
                </div>
                </div>
              </div>
          </section>

          <section className={`add-sales-v2-box add-sales-v2-box-fill-forms ${!savedTo || !submitInfoActionsComplete ? "add-sales-v2-box--greyed" : ""}`}>
            <div className="add-sales-v2-box-title-row add-sales-v2-fill-forms-title-row">
              <div className="add-sales-v2-fill-forms-title-block">
                <h2 className="add-sales-v2-box-title">3. Fill Forms (with AI Agents)</h2>
              </div>
            </div>
            <div className="add-sales-v2-box-body">
              {siteUrlsLoading && (
                <div className="add-sales-v2-status-row" role="status">
                  <span className="add-sales-v2-status-text">Loading automation site URLs from server…</span>
                </div>
              )}
              {siteUrlsError && (
                <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                  <span className="add-sales-v2-status-text">{siteUrlsError}</span>
                </div>
              )}
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head">
                  <h3 className="add-sales-v2-subsection-title">A. DMS</h3>
                  {isFillDmsLoading && <span className="add-sales-v2-processing">Processing</span>}
                  <button
                    type="button"
                    className="app-button app-button--primary"
                    disabled={createInvoicePrimaryButtonDisabled}
                    onClick={handleFillDms}
                    title={createInvoiceButtonTitle}
                  >
                    {isFillDmsLoading ? "Processing…" : "Create Invoice"}
                  </button>
                </div>
                {submitInfoActionsComplete &&
                  createInvoiceEligibilityReason &&
                  !createInvoiceEnabled &&
                  !createInvoiceEligibilityLoading &&
                  !createInvoiceCompleted && (
                    <div className="add-sales-v2-status-row" role="status">
                      <span className="add-sales-v2-status-text">{createInvoiceEligibilityReason}</span>
                    </div>
                  )}
                {fillDmsStatus && (
                  <StatusMessage message={fillDmsStatus} className="app-panel-status" role="status" />
                )}
                <div className="add-sales-v2-dms-fields">
                  <div className="add-sales-v2-dms-fields-title">Get fields from DMS</div>
                  <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Chassis no.</dt>
                        <dd>{d?.frame_no ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Engine no.</dt>
                        <dd>{d?.engine_no ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Model</dt>
                        <dd>{d?.model ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Vehicle Price</dt>
                        <dd>{d?.vehicle_price ?? "—"}</dd>
                      </div>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-dms-pdfs">
                  <div className="add-sales-v2-dms-pdfs-title">Get PDFs</div>
                  <ul className="add-sales-v2-dms-pdfs-list">
                    <li className={dmsPdfsDownloaded ? "add-sales-v2-dms-pdf-done" : ""}>
                      {dmsPdfsDownloaded ? (
                        <span className="add-sales-v2-dms-pdf-check" aria-hidden>✓</span>
                      ) : null}
                      Form 21
                    </li>
                    <li className={dmsPdfsDownloaded ? "add-sales-v2-dms-pdf-done" : ""}>
                      {dmsPdfsDownloaded ? (
                        <span className="add-sales-v2-dms-pdf-check" aria-hidden>✓</span>
                      ) : null}
                      Form 22
                    </li>
                    <li className={dmsPdfsDownloaded ? "add-sales-v2-dms-pdf-done" : ""}>
                      {dmsPdfsDownloaded ? (
                        <span className="add-sales-v2-dms-pdf-check" aria-hidden>✓</span>
                      ) : null}
                      Invoice Details
                    </li>
                  </ul>
                  {dmsPdfsDownloaded && (
                    <p className="add-sales-v2-dms-pdfs-msg">Form 21, Form 22 and Invoice Details downloaded.</p>
                  )}
                </div>
              </div>
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head">
                  <h3 className="add-sales-v2-subsection-title">B. Insurance</h3>
                  {isFillInsuranceLoading && <span className="add-sales-v2-processing">Processing</span>}
                  <button
                    type="button"
                    className="app-button app-button--primary"
                    disabled={generateInsurancePrimaryButtonDisabled}
                    onClick={handleFillInsurance}
                    title={generateInsuranceButtonTitle}
                  >
                    {isFillInsuranceLoading ? "Processing…" : "Generate Insurance"}
                  </button>
                </div>
                {submitInfoActionsComplete &&
                  !hasSuppliedInsuranceDoc &&
                  generateInsuranceReason &&
                  !generateInsuranceEnabled &&
                  !createInvoiceEligibilityLoading &&
                  !generateInsuranceCompleted && (
                    <div className="add-sales-v2-status-row" role="status">
                      <span className="add-sales-v2-status-text">{generateInsuranceReason}</span>
                    </div>
                  )}
                <div className="add-sales-v2-dms-fields">
                  <div className="add-sales-v2-dms-fields-title">Insurance details (from uploaded document)</div>
                  <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Insurance Provider</dt>
                        <dd>
                          <input
                            type="text"
                            className="add-sales-v2-dl-input add-sales-v2-dl-input--insurance-provider-wide"
                            value={ins?.insurer ?? preferInsurer ?? ""}
                            onChange={(e) =>
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              insurer: sanitizeFormFieldValue(e.target.value),
                            }))
                          }
                            placeholder="—"
                          />
                        </dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Policy No.</dt>
                        <dd>{ins?.policy_num ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Valid From</dt>
                        <dd>{ins?.policy_from ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Gross Premium</dt>
                        <dd>{ins?.premium ?? "—"}</dd>
                      </div>
                    </div>
                  </dl>
                </div>
                {fillInsuranceStatus && (
                  <div className="add-sales-v2-print-forms-row">
                    <StatusMessage message={fillInsuranceStatus} className="app-panel-status" role="status" />
                  </div>
                )}
                {printFormsStatus && (
                  <div className="add-sales-v2-print-forms-row">
                    <StatusMessage message={printFormsStatus} className="app-panel-status" role="status" />
                  </div>
                )}
              </div>
              <div className="add-sales-v2-rto-actions add-sales-v2-print-forms-actions">
                <button
                  type="button"
                  className="app-button app-button--primary"
                  disabled={!printFormsButtonEnabled}
                  onClick={handlePrintForms}
                  title={printFormsButtonTitle}
                >
                  {isPrintFormsLoading ? "Processing…" : "Print Forms and Queue RTO"}
                </button>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
