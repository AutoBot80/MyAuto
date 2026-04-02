import { useState, useEffect, useRef, useCallback } from "react";
import type { ExtractedVehicleDetails, ExtractedCustomerDetails, ExtractedInsuranceDetails } from "../types";
import { buildDisplayAddress } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { submitInfo } from "../api/submitInfo";
import { fillDmsOnly, fillHeroInsurance, printForm20, isFillDmsAbortError, warmDmsBrowser } from "../api/fillForms";
import { fetchCreateInvoiceEligibility } from "../api/addSales";
import { insertRtoPayment } from "../api/rtoPaymentDetails";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
import { markBulkLoadSuccess } from "../api/bulkLoads";
import { isHeroBajajFinancierForStaging } from "../utils/financierStagingRules";
import { normalizeVehicleDetails, hasVehicleData } from "../utils/vehicleDetails";
import { StatusMessage } from "../components/StatusMessage";
import { usePageVisible } from "../hooks/usePageVisible";

function getInitialForm() {
  const d = loadAddSalesForm();
  return d;
}

function mapApiCustomerToExtracted(cust: Record<string, unknown>): ExtractedCustomerDetails {
  const r = cust;
  const pinVal = String(r.pin ?? r.pin_code ?? "").trim() || undefined;
  return {
    aadhar_id: String(r.aadhar_id ?? "").trim() || undefined,
    name: String(r.name ?? "").trim() || undefined,
    alt_phone_num: String(r.alt_phone_num ?? r.alternate_mobile_number ?? "").trim() || undefined,
    gender: String(r.gender ?? "").trim() || undefined,
    year_of_birth: String(r.year_of_birth ?? "").trim() || undefined,
    date_of_birth: String(r.date_of_birth ?? "").trim() || undefined,
    care_of: String(r.care_of ?? "").trim() || undefined,
    house: String(r.house ?? "").trim() || undefined,
    street: String(r.street ?? "").trim() || undefined,
    location: String(r.location ?? "").trim() || undefined,
    city: String(r.city ?? "").trim() || undefined,
    post_office: String(r.post_office ?? "").trim() || undefined,
    district: String(r.district ?? "").trim() || undefined,
    sub_district: String(r.sub_district ?? "").trim() || undefined,
    state: String(r.state ?? "").trim() || undefined,
    pin_code: pinVal,
    address: String(r.address ?? "").trim() || undefined,
  };
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
  const ocrFinancier = Object.prototype.hasOwnProperty.call(r, "financier")
    ? normalizeFinancierInput(r.financier)
    : preferNonEmptyOcr(undefined, normalizeFinancierInput(current.financier) ?? current.financier);
  return {
    ...current,
    profession: preferNonEmptyOcr(fromServer.profession, current.profession),
    financier: ocrFinancier,
    marital_status: preferNonEmptyOcr(fromServer.marital_status, current.marital_status),
    nominee_gender: preferNonEmptyOcr(fromServer.nominee_gender, current.nominee_gender),
    nominee_name: preferNonEmptyOcr(fromServer.nominee_name, current.nominee_name),
    nominee_age: preferNonEmptyOcr(
      fromServer.nominee_age != null && String(fromServer.nominee_age).trim() !== ""
        ? String(fromServer.nominee_age).trim()
        : undefined,
      current.nominee_age
    ),
    nominee_relationship: preferNonEmptyOcr(fromServer.nominee_relationship, current.nominee_relationship),
    insurer: preferNonEmptyOcr(fromServer.insurer, current.insurer),
    policy_num: preferNonEmptyOcr(fromServer.policy_num, current.policy_num),
    policy_from: preferNonEmptyOcr(fromServer.policy_from, current.policy_from),
    policy_to: preferNonEmptyOcr(fromServer.policy_to, current.policy_to),
    premium: preferNonEmptyOcr(fromServer.premium, current.premium),
  };
}

function parseAmount(value: unknown): number | null {
  if (value == null) return null;
  const cleaned = String(value).replace(/,/g, "").trim();
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

function computeRtoFees(totalAmount: unknown): number {
      const total = parseAmount(totalAmount) ?? 72000;
  return Math.round(total * 0.01 + 200);
}

interface AddSalesPageProps {
  dealerId: number;
  /** From GET /dealers/:id — Hero MotoCorp is ``1`` (financier staging remap rules). */
  oemId: number | null;
  /** DMS base URL from GET /settings/site-urls (backend/.env DMS_BASE_URL). No client fallbacks. */
  dmsUrl?: string;
  /** True while fetching /settings/site-urls. */
  siteUrlsLoading?: boolean;
  /** Set when site URL config could not be loaded from the API. */
  siteUrlsError?: string | null;
  /** Increment to force the same behavior as pressing "New". */
  autoNewTrigger?: number;
}

export function AddSalesPage({ dealerId, oemId, dmsUrl, siteUrlsLoading, siteUrlsError, autoNewTrigger }: AddSalesPageProps) {
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
  const [dmsRunEndedWithError, setDmsRunEndedWithError] = useState(false);
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
  const [lastSubmittedCustomerId, setLastSubmittedCustomerId] = useState<number | null>(() => getInitialForm().lastSubmittedCustomerId);
  const [lastSubmittedVehicleId, setLastSubmittedVehicleId] = useState<number | null>(() => getInitialForm().lastSubmittedVehicleId);
  const [lastStagingId, setLastStagingId] = useState<string | null>(() => getInitialForm().lastStagingId);
  /** Extraction error (e.g. QR code not readable) – stops poll and shows message. */
  const [extractionError, setExtractionError] = useState<string | null>(null);
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

  const triggerWarmBrowser = useCallback(
    (subfolder: string) => {
      const sf = (subfolder || "").trim();
      const url = (dmsUrl ?? "").trim();
      if (!sf || !url || siteUrlsLoading || siteUrlsError) return;
      if (dmsWarmSubfolderRef.current === sf) return;
      dmsWarmSubfolderRef.current = sf;
      void warmDmsBrowser({ dms_base_url: url }).catch((err) => {
        const msg = err instanceof Error ? err.message : "Could not pre-open DMS browser.";
        setFillDmsStatus(`DMS warm-up did not finish: ${msg}`);
      });
    },
    [dmsUrl, siteUrlsLoading, siteUrlsError]
  );

  const applyExtractedDetails = (details: { vehicle?: unknown; customer?: unknown; insurance?: unknown }) => {
    const rawVehicle = details?.vehicle ?? details;
    const normalized = normalizeVehicleDetails(rawVehicle);
    if (normalized) setExtractedVehicle(normalized);
    const cust = details?.customer;
    if (cust && typeof cust === "object" && !Array.isArray(cust)) {
      setExtractedCustomer(mapApiCustomerToExtracted(cust as Record<string, unknown>));
    }
    const ins = details?.insurance;
    if (ins && typeof ins === "object" && !Array.isArray(ins)) {
      setInsuranceReadByTextract(true);
      const r = ins as Record<string, unknown>;
      setExtractedInsurance((prev) => mergeInsuranceFromOcrPayload(prev, r));
    }
  };

  const {
    upload,
    uploadV2,
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
    onExtractionComplete: applyExtractedDetails,
    onUploadSuccess: (savedSubfolder?: string) => {
      setFillDmsStatus(null);
      setDmsMilestones([]);
      setDmsBannerIsStepMessages(false);
      setDmsRunEndedWithError(false);
      setDmsScrapedVehicle(null);
      setDmsPdfsDownloaded(false);
      if (savedSubfolder) triggerWarmBrowser(savedSubfolder);
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
      extractedVehicle,
      extractedCustomer,
      extractedInsurance,
    });
  }, [mobile, savedTo, uploadedFiles, uploadStatus, dmsScrapedVehicle, hasSubmittedInfo, lastSubmittedCustomerId, lastSubmittedVehicleId, lastStagingId, extractedVehicle, extractedCustomer, extractedInsurance]);

  // After upload path is known, pre-open DMS (reuse/CDP + login wait) so Create Invoice starts closer to ready.
  useEffect(() => {
    if (!savedTo) {
      dmsWarmSubfolderRef.current = null;
      return;
    }
    triggerWarmBrowser(savedTo);
  }, [savedTo, triggerWarmBrowser]);

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
        <div className="app-field-label">Customer Mobile (10 digits)</div>
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
      return;
    }
    const veh = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
    const ch = (veh?.frame_no ?? "").trim();
    const eng = (veh?.engine_no ?? "").trim();
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
  }, [submitInfoActionsComplete, mobile, extractedVehicle]);

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
  const display = (s: string | undefined) => (s && String(s).trim() ? String(s).trim() : "—");

  const requiredFieldChecks: { label: string; value: string | undefined }[] = [
    { label: "Aadhar ID", value: c?.aadhar_id },
    { label: "Name", value: c?.name },
    { label: "Gender", value: c?.gender },
    { label: "Date of birth", value: c?.date_of_birth },
    { label: "Address", value: c ? buildDisplayAddress(c) : undefined },
    { label: "Frame no.", value: v?.frame_no },
    { label: "Engine no.", value: v?.engine_no },
    { label: "Key no.", value: v?.key_no },
    { label: "Battery no.", value: v?.battery_no },
    { label: "Customer Profession", value: ins?.profession },
    { label: "Nominee Name", value: ins?.nominee_name },
    { label: "Nominee Age", value: ins?.nominee_age },
    { label: "Nominee Relationship", value: ins?.nominee_relationship },
  ];

  const getMissingRequiredFields = (): string[] => {
    return requiredFieldChecks
      .filter(({ value }) => value == null || String(value).trim() === "" || String(value).trim() === "—")
      .map(({ label }) => label);
  };

  const hasAllRequiredExtractedFields = () => {
    return getMissingRequiredFields().length === 0;
  };

  /** Allowed: letters, digits, space, hyphen, period, slash, comma. No other special characters. */
  const ALLOWED_CHAR_REGEX = /^[a-zA-Z0-9\s\-./,]*$/;
  const isBlank = (val: string | undefined | null): boolean =>
    val == null || String(val).trim() === "" || String(val).trim() === "—";
  const hasDisallowedSpecialChars = (val: string | undefined | null): boolean =>
    val != null && String(val).trim() !== "" && !ALLOWED_CHAR_REGEX.test(String(val).trim());

  const vehicleValidationFields: { label: string; value: string | undefined }[] = [
    { label: "Frame no.", value: v?.frame_no },
    { label: "Engine no.", value: v?.engine_no },
    { label: "Key no.", value: v?.key_no },
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
    { label: "Nominee Name", value: ins?.nominee_name },
    { label: "Nominee Age", value: ins?.nominee_age },
    { label: "Nominee Relationship", value: ins?.nominee_relationship },
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
          i.insurer,
          i.policy_from,
          i.policy_to,
          i.premium,
        ].some((x) => x != null && String(x).trim() !== "")
    );

  /** Show per-subsection status while files upload (before savedTo) and while OCR/extraction is still filling that block. */
  const customerProcessing = Boolean(
    (isUploading || savedTo) && !hasMeaningfulCustomer(c)
  );
  const vehicleProcessing = Boolean((isUploading || savedTo) && !hasVehicleData(v ?? null));
  const insuranceProcessing = Boolean((isUploading || savedTo) && !hasMeaningfulInsurance(ins));
  const hasSuppliedInsuranceDoc = uploadedFiles.some((f) =>
    /insurance/i.test(String(f || ""))
  );
  /** Don't show errors until Textract/Tesseract have finished extracting all subsections */
  const extractionComplete = !customerProcessing && !vehicleProcessing && !insuranceProcessing;

  /** When true, polling is not needed; use this as effect deps so we don't restart the interval on every field merge. */
  const extractionSectionsDone =
    Boolean(savedTo) &&
    hasMeaningfulCustomer(c) &&
    hasVehicleData(v ?? null) &&
    hasMeaningfulInsurance(ins);

  // Poll for extracted details until customer, vehicle, and insurance blocks match the same "complete" rules as the UI.
  useEffect(() => {
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
        const extractionErr = (details as Record<string, unknown>)?.extraction_error;
        const nameMismatchErr = (details as Record<string, unknown>)?.name_mismatch_error;
        const err = typeof nameMismatchErr === "string" ? nameMismatchErr : typeof extractionErr === "string" ? extractionErr : null;
        setExtractionError(err);
        if (err && intervalId) clearInterval(intervalId);
        const rawVehicle = details?.vehicle ?? details;
        const normalized = normalizeVehicleDetails(rawVehicle);
        if (normalized) setExtractedVehicle(normalized);
        const cust = details?.customer;
        if (cust && typeof cust === "object" && !Array.isArray(cust)) {
          setExtractedCustomer(mapApiCustomerToExtracted(cust as Record<string, unknown>));
        }
        const insPoll = details?.insurance;
        if (insPoll && typeof insPoll === "object" && !Array.isArray(insPoll)) {
          setInsuranceReadByTextract(true);
          const r = insPoll as Record<string, unknown>;
          setExtractedInsurance((prev) => mergeInsuranceFromOcrPayload(prev, r));
        }
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
  }, [savedTo, extractionSectionsDone, dealerId, pageVisible]);

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
      setFillDmsStatus("DMS URL is not available. Set DMS_BASE_URL in backend/.env, restart the server, and refresh this page.");
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
        setDmsScrapedVehicle({
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
        });
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
        await refreshCreateInvoiceEligibility();
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
    const scrapedForForm20 = dmsScrapedVehicle as Record<string, unknown> | null;
    let vehicleDataForForm20: Record<string, unknown> = {};
    if (scrapedForForm20 && typeof scrapedForForm20 === "object") {
      const s = scrapedForForm20 as Record<string, unknown>;
      vehicleDataForForm20 = {
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
      vehicleDataForForm20 = {
        key_no: v.key_no,
        frame_no: v.frame_no,
        engine_no: v.engine_no,
        model: v.model ?? v.model_colour,
        color: v.color,
      };
    }
    setIsPrintFormsLoading(true);
    try {
      const form20Res = await printForm20({
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
        vehicle: vehicleDataForForm20,
        vehicle_id: lastSubmittedVehicleId ?? undefined,
        dealer_id: dealerId,
      });
      if (form20Res.success) {
        setHasPrintedForms(true);
        setPrintFormsStatus(`Form 20 saved: ${(form20Res.pdfs_saved ?? []).join(", ")}`);
      } else if (form20Res.error) {
        setPrintFormsStatus(`Form 20: ${form20Res.error}`);
      }
    } catch (form20Err) {
      setPrintFormsStatus(`Form 20: ${form20Err instanceof Error ? form20Err.message : "Create & print file failed."}`);
    } finally {
      setIsPrintFormsLoading(false);
    }

    if (lastSubmittedCustomerId != null && lastSubmittedVehicleId != null) {
      const today = new Date();
      const dd = String(today.getDate()).padStart(2, "0");
      const mm = String(today.getMonth() + 1).padStart(2, "0");
      const yyyy = today.getFullYear();
      const registerDate = `${dd}-${mm}-${yyyy}`;
      const queueVehicle = dmsScrapedVehicle ?? v;
      const chassisNo = queueVehicle?.frame_no ?? v?.frame_no ?? "";
      const rtoFees = computeRtoFees(queueVehicle?.vehicle_price);
      try {
        await insertRtoPayment({
          customer_id: lastSubmittedCustomerId,
          vehicle_id: lastSubmittedVehicleId,
          dealer_id: dealerId,
          name: c?.name ?? undefined,
          mobile: mobile ?? undefined,
          chassis_num: chassisNo || undefined,
          register_date: registerDate,
          rto_fees: rtoFees,
          status: "Queued",
          rto_status: "Pending",
          subfolder: savedTo ?? undefined,
        });
        setPrintFormsStatus((prev) => (prev ? `${prev} Added to RTO Queue.` : "Added to RTO Queue."));
      } catch (queueErr) {
        setPrintFormsStatus(
          queueErr instanceof Error
            ? `Print done but adding to RTO Queue failed: ${queueErr.message}`
            : "Print done but adding to RTO Queue failed."
        );
      }
    }
  };

  const d = dmsScrapedVehicle;

  const createInvoiceButtonTitle =
    isSubmitting
      ? "Wait for Submit Info to finish."
      : !submitInfoActionsComplete
        ? "Complete Submit Info (Section 2) — staging must be saved to the server."
        : dealerId == null || dealerId <= 0
        ? "Dealer is not configured."
        : createInvoiceEligibilityLoading
          ? "Checking whether an invoice is already recorded…"
          : !createInvoiceEnabled
            ? createInvoiceEligibilityReason ?? "Create Invoice is not available for this sale."
            : !dmsUrl || siteUrlsError
              ? "Configure DMS_BASE_URL in backend/.env"
              : undefined;

  const generateInsuranceButtonTitle =
    isSubmitting
      ? "Wait for Submit Info to finish."
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
    !mobile ||
    !c ||
    !insuranceReadByTextract ||
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
                <div className="add-sales-v2-fields-row">
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-mobile">
                    {mobileRow}
                  </div>
                </div>
                <div className="add-sales-v2-panel-wrap">
                  {panel}
                </div>
              </div>
          </section>
          <section className={`add-sales-v2-box add-sales-v2-box-extracted ${!savedTo ? "add-sales-v2-box--greyed" : ""}`}>
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">2. AI Extracted Information</h2>
                <button
                    type="button"
                    className="app-button add-sales-v2-submit-btn"
                    disabled={submitInfoPrimaryButtonDisabled}
                    onClick={async () => {
                      if (!mobile || !c) return;
                      if (!insuranceReadByTextract) {
                        setSubmitStatus("Waiting for insurance details from document.");
                        return;
                      }
                      if (!hasAllRequiredExtractedFields()) {
                        setSubmitStatus(`Please fill: ${getMissingRequiredFields().join(", ")}.`);
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
                        });
                        setSubmitStatus("Saved");
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
                {extractionComplete && savedTo && !insuranceReadByTextract && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <span className="add-sales-v2-status-text">Waiting for insurance details from document.</span>
                  </div>
                )}
                {extractionComplete && savedTo && insuranceReadByTextract && getMissingRequiredFields().length > 0 && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <span className="add-sales-v2-status-text">
                      Please fill: {getMissingRequiredFields().join(", ")}.
                    </span>
                  </div>
                )}
                {submitStatus && (!savedTo || (insuranceReadByTextract && getMissingRequiredFields().length === 0)) && (
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
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Aadhar ID</dt>
                        <dd>{display(c?.aadhar_id)}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Name</dt>
                        <dd>{display(c?.name)}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Date of birth</dt>
                        <dd className="add-sales-v2-dl-dd--dob">
                          <input
                            className="add-sales-v2-dl-input add-sales-v2-dl-input--dob"
                            type="text"
                            inputMode="numeric"
                            autoComplete="bday"
                            value={c?.date_of_birth ?? ""}
                            onChange={(e) => setExtractedCustomer((prev) => ({ ...(prev ?? {}), date_of_birth: e.target.value }))}
                            placeholder="YYYY-MM-DD"
                          />
                        </dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Gender</dt>
                        <dd>{display(c?.gender)}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Alternate</dt>
                      <dd>{display(c?.alt_phone_num)}</dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Address</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={c?.address ?? ""}
                          onChange={(e) => setExtractedCustomer((prev) => ({ ...(prev ?? {}), address: e.target.value }))}
                          placeholder="—"
                        />
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
                      <dt>Frame no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.frame_no ?? ""}
                          onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), frame_no: e.target.value }))}
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
                          onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), engine_no: e.target.value }))}
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Key no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.key_no ?? ""}
                          onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), key_no: e.target.value }))}
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
                          onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), battery_no: e.target.value }))}
                          placeholder="—"
                        />
                      </dd>
                    </div>
                    {(v?.model ?? v?.color ?? v?.cubic_capacity ?? v?.seating_capacity ?? v?.body_type ?? v?.vehicle_type ?? v?.num_cylinders ?? v?.vehicle_price ?? v?.year_of_mfg) && (
                      <>
                        <div className="add-sales-v2-dl-row">
                          <dt>Model</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.model ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), model: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Color</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.color ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), color: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Cubic Cap.</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.cubic_capacity ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), cubic_capacity: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Seating Cap.</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.seating_capacity ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), seating_capacity: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Body type</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.body_type ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), body_type: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Vehicle type</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.vehicle_type ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), vehicle_type: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Num cylinders</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.num_cylinders ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), num_cylinders: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Vehicle Price</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.vehicle_price ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), vehicle_price: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Year of Mfg</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.year_of_mfg ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), year_of_mfg: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                      </>
                    )}
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
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), financier: e.target.value }))}
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
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), profession: e.target.value }))}
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
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), marital_status: e.target.value }))}
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
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_name: e.target.value }))}
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
                            const v = e.target.value;
                            if (v === "" || /^\d{0,3}$/.test(v)) {
                              setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_age: v }));
                            }
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
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_gender: e.target.value }))}
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
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_relationship: e.target.value }))}
                          placeholder="—"
                        />
                      </dd>
                    </div>
                  </dl>
                </div>
              </div>
          </section>

          <section className={`add-sales-v2-box add-sales-v2-box-fill-forms ${!savedTo || !submitInfoActionsComplete ? "add-sales-v2-box--greyed" : ""}`}>
            <div className="add-sales-v2-box-title-row add-sales-v2-fill-forms-title-row">
              <div className="add-sales-v2-fill-forms-title-block">
                <h2 className="add-sales-v2-box-title">3. Fill Forms &amp; Print File</h2>
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
                  !createInvoiceEligibilityLoading && (
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
                        <dt>Key no.</dt>
                        <dd>{d?.key_no ?? "—"}</dd>
                      </div>
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
                      <div className="add-sales-v2-dl-row">
                        <dt>Model</dt>
                        <dd>{d?.model ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Color</dt>
                        <dd>{d?.color ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Year of Mfg</dt>
                        <dd>{d?.year_of_mfg ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
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
                  !createInvoiceEligibilityLoading && (
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
                            className="add-sales-v2-dl-input"
                            value={ins?.insurer ?? ""}
                            onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), insurer: e.target.value }))}
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
                        <dt>Valid To</dt>
                        <dd>{ins?.policy_to ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
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
              <div className="add-sales-v2-print-forms-row" style={{ marginTop: 10 }}>
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
