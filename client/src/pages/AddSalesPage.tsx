import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  type Dispatch,
  type SetStateAction,
} from "react";
import type { ExtractedVehicleDetails, ExtractedCustomerDetails, ExtractedInsuranceDetails } from "../types";
import { buildDisplayAddress } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { ManualFallbackSplitReview } from "../components/ManualFallbackSplitReview";
import type { ManualFallbackPayload } from "../types";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { ApiHttpError } from "../api/client";
import { submitInfo } from "../api/submitInfo";
import {
  buildFillCpaAllianceInsuranceRequest,
  dispatchPrintJobsFromApi,
  fillCpaAllianceInsuranceLocal,
  fillDmsLocal,
  fillHeroInsuranceLocal,
  isFillDmsAbortError,
  warmDmsBrowserLocal,
  warmInsuranceBrowserLocal,
} from "../api/fillForms";
import {
  fetchAddSalesStagingPayload,
  fetchCreateInvoiceEligibility,
  fetchDealerCpaContext,
  patchAddSalesStagingPayload,
  type CreateInvoiceEligibilityResponse,
  type CpaInsurerPortalRow,
} from "../api/addSales";
import { pullAadharScansForInsurance } from "../utils/ensureAadharScansBeforeInsurance";
import { runPrintQueueRtoFlow } from "../utils/printQueueRtoFlow";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
import { markBulkLoadSuccess } from "../api/bulkLoads";
import { isHeroBajajFinancierForStaging } from "../utils/financierStagingRules";
import { normalizeVehicleDetails, hasVehicleData } from "../utils/vehicleDetails";
import {
  sanitizeExtractedVehicleDetailFields,
  sanitizeFormFieldInputValue,
  sanitizeFormFieldValue,
  sanitizeNomineeAgeInput,
  sanitizeOptionalFormField,
} from "../utils/formFieldSanitize";
import { AddSalesInProcessPanel } from "./AddSalesInProcessPanel";
import { AddSalesInvoicesPanel } from "./AddSalesInvoicesPanel";
import { StatusMessage } from "../components/StatusMessage";
import { usePageVisible } from "../hooks/usePageVisible";
import type { ConsolidatedFsArchiveContext } from "../utils/scannerArchive";
import {
  buildAddressLine1,
  buildAddressLine2,
  buildSection2FullAddress,
  composeCareOf,
  formatDobDigitsInput,
  normalizeDobToDdMmYyyy,
  normalizeOperatorFreeformAddress,
  parseAddressLine2,
  parseCareOfFromCombined,
  uppercaseAddressField,
  uppercaseAddressLocality,
} from "../utils/section2CustomerFormat";
import {
  getSection2ValidationErrors,
  type Section2FieldError,
} from "../utils/section2Validation";
import {
  isHorizontallyScrollableFocusTarget,
  syncAddSalesThreeColFocus,
} from "../utils/scrollFocusedIntoHorizontalParent";
import { isPlaceholderCustomerMobileDigits } from "../utils/customerMobile";
import {
  cpaPolicyFromInsuranceRaw,
  insuranceFieldsFromStagingInsurance,
} from "../utils/insuranceDisplay";
import {
  insurerLooksLikeFinancier,
  resolveCanonicalFinancier,
  resolvePortalInsurer,
} from "../utils/addSalesInsurerResolve";
/** Shown under Upload documents while upload or OCR polling runs; counts down toward 00m:00s. */
const ADD_SALES_OCR_COUNTDOWN_START_SEC = 40;

function section2FieldLabel(field: string): string {
  const labels: Record<string, string> = {
    customer_mobile: "Customer Mobile",
    alternate_no: "Alternate No.",
    name: "Name",
    gender: "Gender",
    address: "Address",
    address_line2: "Address (City, State, PIN)",
    financier: "Financier",
    dob: "DOB",
    care_of: "C/O",
    aadhar: "Aadhaar (last 4 digits)",
    key_no: "Key no.",
    chassis_no: "Chassis No.",
    engine_no: "Engine no.",
    battery_no: "Battery no.",
    profession: "Customer Profession",
    marital_status: "Customer Marital Status",
    nominee_name: "Nominee Name",
    nominee_age: "Nominee Age",
    nominee_relationship: "Relationship",
    nominee_gender: "Nominee Gender",
  };
  return labels[field] ?? field;
}

function Section2FieldError({
  field,
  errors,
}: {
  field: string;
  errors: readonly Section2FieldError[];
}) {
  const e = errors.find((x) => x.field === field);
  if (!e) return null;
  return (
    <div className="add-sales-v2-field-error" role="alert">
      {e.message}
    </div>
  );
}

/** Merge DB invoice # from eligibility into DMS display state (Order # comes from DMS scrape only). */
function mergeDmsVehicleWithEligibilityInvoice(
  setDmsScrapedVehicle: Dispatch<SetStateAction<ExtractedVehicleDetails | null>>,
  res: CreateInvoiceEligibilityResponse
) {
  const inv = (res.invoice_number ?? "").trim();
  if (!inv) return;
  setDmsScrapedVehicle((prev) => {
    const merged = { ...(prev ?? {}), invoice_number: inv };
    return (sanitizeExtractedVehicleDetailFields(merged) ?? merged) as ExtractedVehicleDetails;
  });
}

/** Normalize ``dealer_ref.hero_cpi`` from API (single-letter Y/N). */
function normalizeHeroCpiFlag(raw: unknown): "Y" | "N" | null {
  if (raw == null) return null;
  const s = String(raw).trim().toUpperCase().slice(0, 1);
  return s === "Y" || s === "N" ? s : null;
}

/** Normalize CPA Required / ``cpi_reqd`` to Y/N. */
function normalizeCpaRequiredFlag(raw: unknown): "Y" | "N" {
  const s = String(raw ?? "").trim().toUpperCase();
  if (s === "Y" || s === "YES") return "Y";
  return "N";
}

function deriveCpaAllianceUiState(res: CreateInvoiceEligibilityResponse): {
  enabled: boolean;
  insurers: CpaInsurerPortalRow[];
  dealerCpa: string | null;
} {
  const insurers = res.cpa_insurers ?? [];
  const enabled = Boolean(res.cpa_alliance_portal_enabled);
  const dealerCpa = res.dealer_cpa_insurer?.trim() ? res.dealer_cpa_insurer.trim() : null;
  return { enabled, insurers, dealerCpa };
}

/** Resolve which CPA portal row to open: dealer ``cpa_insurer`` match, else first list row with a URL. */
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

function uppercaseLine1Field(raw: unknown): string | undefined {
  const v = sanitizeOptionalFormField(String(raw ?? "").trim());
  return v ? uppercaseAddressField(v) : undefined;
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
  const addressRaw = sanitizeOptionalFormField(String(r.address ?? "").trim());
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
    house: uppercaseLine1Field(r.house),
    street: uppercaseLine1Field(r.street),
    location: uppercaseLine1Field(r.location),
    city: (() => {
      const v = sanitizeOptionalFormField(String(r.city ?? "").trim());
      return v ? uppercaseAddressField(v) : undefined;
    })(),
    post_office: uppercaseLine1Field(r.post_office),
    district: uppercaseLine1Field(r.district),
    sub_district: uppercaseLine1Field(r.sub_district),
    state: (() => {
      const v = sanitizeOptionalFormField(String(r.state ?? "").trim());
      return v ? uppercaseAddressField(v) : undefined;
    })(),
    pin_code: pinVal,
    address: addressRaw ? uppercaseAddressLocality(addressRaw) : undefined,
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

type MergeInsuranceFromOcrOpts = {
  portalInsurers?: readonly string[];
  preferInsurer?: string | null;
  masterRefFinanciers?: readonly string[];
};

function mergeInsuranceFromOcrPayload(
  prev: ExtractedInsuranceDetails | null | undefined,
  r: Record<string, unknown>,
  mergeOpts?: MergeInsuranceFromOcrOpts
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
    cpa_reqd: typeof r.cpa_reqd === "string" ? r.cpa_reqd : undefined,
  };
  const ocrFinancierRaw = Object.prototype.hasOwnProperty.call(r, "financier")
    ? normalizeFinancierInput(r.financier)
    : preferNonEmptyOcr(undefined, normalizeFinancierInput(current.financier) ?? current.financier);
  const ocrFinancier = sanitizeOptionalFormField(
    resolveCanonicalFinancier(ocrFinancierRaw, mergeOpts?.masterRefFinanciers ?? []) ?? undefined
  );
  const portalList = mergeOpts?.portalInsurers ?? [];
  const prefer = mergeOpts?.preferInsurer;
  const insurerFromJson = sanitizeOptionalFormField(
    normalizeInsurerOcrValue(fromServer.insurer) ?? undefined
  );
  const insurerFromJsonSafe =
    insurerFromJson && insurerLooksLikeFinancier(insurerFromJson, mergeOpts?.masterRefFinanciers ?? [])
      ? undefined
      : insurerFromJson;
  const insurerFromCurrent = sanitizeOptionalFormField(
    normalizeInsurerOcrValue(current.insurer) ?? undefined
  );
  const resolvedInsurer = Object.prototype.hasOwnProperty.call(r, "insurer")
    ? resolvePortalInsurer(insurerFromJsonSafe, prefer, portalList) ??
      resolvePortalInsurer(insurerFromCurrent, prefer, portalList)
    : resolvePortalInsurer(insurerFromCurrent, prefer, portalList);
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
    insurer: resolvedInsurer,
    policy_num: preferNonEmptyOcr(
      sanitizeOptionalFormField(fromServer.policy_num),
      sanitizeOptionalFormField(current.policy_num)
    ),
    cpa_policy_num: preferNonEmptyOcr(
      sanitizeOptionalFormField(
        typeof r.cpa_policy_num === "string"
          ? r.cpa_policy_num
          : cpaPolicyFromInsuranceRaw(r) || undefined
      ),
      sanitizeOptionalFormField(current.cpa_policy_num)
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
    cpa_reqd: preferNonEmptyOcr(
      fromServer.cpa_reqd != null ? normalizeCpaRequiredFlag(fromServer.cpa_reqd) : undefined,
      current.cpa_reqd != null ? normalizeCpaRequiredFlag(current.cpa_reqd) : undefined
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
}

export function AddSalesPage({
  dealerId,
  oemId,
  preferInsurer = null,
  dmsUrl,
  siteUrlsLoading,
  siteUrlsError,
  autoNewTrigger,
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
  const [isFillDmsLoading, setIsFillDmsLoading] = useState(false);
  const [, setDmsRunEndedWithError] = useState(false);
  const [isFillInsuranceLoading, setIsFillInsuranceLoading] = useState(false);
  const [isPrintFormsLoading, setIsPrintFormsLoading] = useState(false);
  const [printFormsStatus, setPrintFormsStatus] = useState<string | null>(null);
  const [printFormsStatusSuccess, setPrintFormsStatusSuccess] = useState(false);
  const [fillInsuranceStatus, setFillInsuranceStatus] = useState<string | null>(null);
  /** Create Invoice (DMS) allowed only after Submit Info and when sales_master has no invoice# for this sale. */
  const [createInvoiceEligibilityLoading, setCreateInvoiceEligibilityLoading] = useState(false);
  const [createInvoiceEnabled, setCreateInvoiceEnabled] = useState(false);
  const [createInvoiceEligibilityReason, setCreateInvoiceEligibilityReason] = useState<string | null>(null);
  const [generateInsuranceEnabled, setGenerateInsuranceEnabled] = useState(false);
  const [generateInsuranceReason, setGenerateInsuranceReason] = useState<string | null>(null);
  const [cpaAllianceInsuranceEnabled, setCpaAllianceInsuranceEnabled] = useState(false);
  const [cpaAllianceInsuranceReason, setCpaAllianceInsuranceReason] = useState<string | null>(null);
  const [cpaAlliancePortalEnabled, setCpaAlliancePortalEnabled] = useState(false);
  const [cpaInsurers, setCpaInsurers] = useState<CpaInsurerPortalRow[]>([]);
  /** ``master_ref`` INSURER rows with ``comments = 'Y'`` (Section 3 dropdown). */
  const [portalInsurers, setPortalInsurers] = useState<string[]>([]);
  /** ``master_ref`` FINANCER rows (Section 2 financier dropdown). */
  const [masterRefFinanciers, setMasterRefFinanciers] = useState<string[]>([]);
  const [dealerCpaContextError, setDealerCpaContextError] = useState<string | null>(null);
  const [section2ValidationErrors, setSection2ValidationErrors] = useState<Section2FieldError[]>([]);
  const [section2SubmitAttempted, setSection2SubmitAttempted] = useState(false);
  const [addressLine2Input, setAddressLine2Input] = useState("");
  const addressLine2DirtyRef = useRef(false);
  const threeColRef = useRef<HTMLDivElement>(null);
  const [dealerCpaInsurer, setDealerCpaInsurer] = useState<string | null>(null);
  const [heroCpi, setHeroCpi] = useState<string | null>(null);
  const [dealerCpiReqd, setDealerCpiReqd] = useState<"Y" | "N">("N");
  const [cpaRequired, setCpaRequired] = useState<"Y" | "N">("N");
  const cpaFromSheetRef = useRef(false);
  const [isFillCpaInsuranceLoading, setIsFillCpaInsuranceLoading] = useState(false);
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
  const [addSalesPageTab, setAddSalesPageTab] = useState<"add-sales" | "in-process" | "invoices">("add-sales");
  const [inProcessActionStagingId, setInProcessActionStagingId] = useState<string | null>(null);
  const [inProcessBadgeCount, setInProcessBadgeCount] = useState(0);

  /** User-facing message when warm-browser fails (sidecar or API). */
  const formatWarmBrowserFailure = useCallback((err: unknown, siteLabel: string): string => {
    const raw = err instanceof Error ? err.message : String(err);
    const unreachable =
      /502|503|504|Service unavailable|ECONNREFUSED|Failed to fetch|Load failed|Cannot connect|network error/i.test(
        raw
      );
    if (unreachable) {
      console.warn(`[Add Sales] ${siteLabel} warm-browser:`, raw);
      return `${siteLabel} pre-open did not run (API or proxy unreachable — start backend on :8000, use Vite dev with VITE_API_URL unset, then refresh).`;
    }
    return raw.length > 280 ? `${raw.slice(0, 280)}…` : raw;
  }, []);

  /** Substrings aligned with backend ``_is_browser_disconnected_error`` for same-session warm retry. */
  const messageLooksLikeAutomationBrowserLost = useCallback((msg: string): boolean => {
    return /(connection closed|target closed|browser has been closed|econnreset|websocket error|socket hang up)/i.test(
      msg
    );
  }, []);

  type CpaEligibilitySlice = Pick<
    CreateInvoiceEligibilityResponse,
    | "cpa_insurers"
    | "hero_cpi"
    | "dealer_cpa_insurer"
    | "cpa_alliance_portal_enabled"
    | "portal_insurers"
    | "financiers"
    | "dealer_cpi_reqd"
  >;

  const applyDealerCpaFromApiSlice = useCallback((res: CpaEligibilitySlice) => {
    setHeroCpi(normalizeHeroCpiFlag(res.hero_cpi));
    const cpa = deriveCpaAllianceUiState(res as CreateInvoiceEligibilityResponse);
    setCpaAlliancePortalEnabled(cpa.enabled);
    setCpaInsurers(cpa.insurers);
    setDealerCpaInsurer(cpa.dealerCpa);
    const dealerCpi = normalizeCpaRequiredFlag(res.dealer_cpi_reqd ?? "N");
    setDealerCpiReqd(dealerCpi);
    if (!cpaFromSheetRef.current) {
      setCpaRequired(dealerCpi);
    }
    const pi = res.portal_insurers;
    setPortalInsurers(Array.isArray(pi) ? pi.map((x) => String(x).trim()).filter(Boolean) : []);
    const fin = res.financiers;
    setMasterRefFinanciers(Array.isArray(fin) ? fin.map((x) => String(x).trim()).filter(Boolean) : []);
  }, []);

  const resolvedCpaPortal = useMemo(
    () => pickCpaPortalRow(cpaInsurers, dealerCpaInsurer),
    [cpaInsurers, dealerCpaInsurer]
  );
  const cpaSelectedPortalUrl = (resolvedCpaPortal?.login_url ?? "").trim();

  const reloadDealerCpaContext = useCallback(async () => {
    if (dealerId <= 0) return;
    try {
      const res = await fetchDealerCpaContext(dealerId);
      applyDealerCpaFromApiSlice(res);
      setDealerCpaContextError(null);
    } catch (e) {
      setDealerCpaContextError(
        e instanceof Error ? e.message : "Could not load financier and insurer lists."
      );
    }
  }, [dealerId, applyDealerCpaFromApiSlice]);

  useEffect(() => {
    if (dealerId <= 0) {
      setHeroCpi(null);
      setDealerCpaInsurer(null);
      setCpaInsurers([]);
      setPortalInsurers([]);
      setMasterRefFinanciers([]);
      setCpaAlliancePortalEnabled(false);
      setDealerCpaContextError(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetchDealerCpaContext(dealerId);
        if (cancelled) return;
        applyDealerCpaFromApiSlice(res);
        setDealerCpaContextError(null);
      } catch (e) {
        if (!cancelled) {
          setDealerCpaContextError(
            e instanceof Error ? e.message : "Could not load financier and insurer lists."
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dealerId, applyDealerCpaFromApiSlice]);

  const triggerWarmBrowsers = useCallback(
    (subfolder: string) => {
      const sf = (subfolder || "").trim();
      if (!sf || siteUrlsLoading || siteUrlsError) return;
      if (warmBrowsersSubfolderRef.current === sf) return;
      warmBrowsersSubfolderRef.current = sf;

      const tasks: Promise<unknown>[] = [];
      const dmsBase = (dmsUrl ?? "").trim();
      if (dmsBase) {
        tasks.push(
          warmDmsBrowserLocal({ dms_base_url: dmsBase }).catch((err) => {
            warmBrowsersSubfolderRef.current = null;
            setFillDmsStatus(`DMS warm-up did not finish: ${formatWarmBrowserFailure(err, "DMS")}`);
          })
        );
      }
      tasks.push(
        warmInsuranceBrowserLocal({}).catch((err) => {
          warmBrowsersSubfolderRef.current = null;
          setFillInsuranceStatus(
            `Insurance warm-up did not finish: ${formatWarmBrowserFailure(err, "Insurance")}`
          );
        })
      );
      void Promise.allSettled(tasks);
    },
    [dmsUrl, siteUrlsLoading, siteUrlsError, formatWarmBrowserFailure]
  );

  useEffect(() => {
    if (!cpaFromSheetRef.current) {
      setCpaRequired(dealerCpiReqd);
    }
  }, [dealerCpiReqd]);

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
        addressLine2DirtyRef.current = false;
        const mobRaw = rec.mobile_number ?? rec.mobile;
        if (mobRaw != null) {
          const digits = String(mobRaw).replace(/\D/g, "").slice(-10);
          if (digits.length === 10 && !isPlaceholderCustomerMobileDigits(digits)) setMobile(digits);
        }
      }
      const ins = details?.insurance;
      if (ins && typeof ins === "object" && !Array.isArray(ins)) {
        setInsuranceReadByTextract(true);
        const r = ins as Record<string, unknown>;
        setExtractedInsurance((prev) =>
          mergeInsuranceFromOcrPayload(prev, r, {
            portalInsurers,
            preferInsurer,
            masterRefFinanciers,
          })
        );
        if (Object.prototype.hasOwnProperty.call(r, "cpa_reqd")) {
          cpaFromSheetRef.current = true;
          setCpaRequired(normalizeCpaRequiredFlag(r.cpa_reqd));
        } else {
          cpaFromSheetRef.current = false;
          setCpaRequired(dealerCpiReqd);
        }
      }
      const sf = (opts?.savedToForWarm ?? "").trim();
      if (sf && detailsHasOcrPayloadForWarm(details)) {
        triggerWarmBrowsers(sf);
      }
    },
    [triggerWarmBrowsers, portalInsurers, preferInsurer, masterRefFinanciers, dealerCpiReqd]
  );

  const { uploadConsolidatedV2, isUploading, isMobileValid, clearUploaded } = useUploadScans("", mobile, {
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
      setDmsRunEndedWithError(false);
      setDmsScrapedVehicle(null);
      setDmsPdfsDownloaded(false);
    },
    onManualFallback: (payload, _warning, scannerArchive) => {
      setManualFallbackPayload(payload);
      if (scannerArchive) setPendingScannerArchiveMove(scannerArchive);
      setExtractionError(null);
      setManualFormOnly(false);
    },
    onConsolidatedScannerArchiveDeferred: (archive) => setPendingScannerArchiveMove(archive),
  }, dealerId);

  const pollCountRef = useRef(0);
  /** While true, `refreshCreateInvoiceEligibility()` from useEffect is skipped so a stale fetch cannot race the post–Create Invoice eligibility sync. */
  const suppressPostDmsEligibilitySyncRef = useRef(false);
  /** Subfolder for which DMS warm-browser has already been triggered. */
  const warmBrowsersSubfolderRef = useRef<string | null>(null);
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

  const refreshCreateInvoiceEligibility = useCallback(async (opts?: { force?: boolean }) => {
    if (!opts?.force && suppressPostDmsEligibilitySyncRef.current) {
      return;
    }
    if (!submitInfoActionsComplete) {
      setCreateInvoiceEligibilityLoading(false);
      setCreateInvoiceEnabled(false);
      setCreateInvoiceEligibilityReason(null);
      setGenerateInsuranceEnabled(false);
      setGenerateInsuranceReason(null);
      setCpaAllianceInsuranceEnabled(false);
      setCpaAllianceInsuranceReason(null);
      setCreateInvoiceCompleted(false);
      setGenerateInsuranceCompleted(false);
      return;
    }
    const byIds =
      lastSubmittedCustomerId != null &&
      lastSubmittedVehicleId != null &&
      lastSubmittedCustomerId > 0 &&
      lastSubmittedVehicleId > 0;
    const dmsVeh = normalizeVehicleDetails(dmsScrapedVehicle) ?? dmsScrapedVehicle;
    const ocrVeh = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
    const ch = (dmsVeh?.frame_no ?? ocrVeh?.frame_no ?? "").trim();
    const eng = (dmsVeh?.engine_no ?? ocrVeh?.engine_no ?? "").trim();
    const mob = mobile.trim();
    if (!byIds && (!ch || !eng || !mob)) {
      setCreateInvoiceEligibilityLoading(false);
      setCreateInvoiceEnabled(false);
      setCreateInvoiceEligibilityReason(
        "Enter mobile, chassis, and engine in Section 2 before Create Invoice."
      );
      setGenerateInsuranceEnabled(false);
      setGenerateInsuranceReason(null);
      setCpaAllianceInsuranceEnabled(false);
      setCpaAllianceInsuranceReason(null);
      return;
    }
    try {
      const res = byIds
        ? await fetchCreateInvoiceEligibility({
            customerId: lastSubmittedCustomerId!,
            vehicleId: lastSubmittedVehicleId!,
            dealerId: dealerId > 0 ? dealerId : undefined,
            stagingId: lastStagingId,
          })
        : await fetchCreateInvoiceEligibility({
            chassisNum: ch,
            engineNum: eng,
            mobile: mob,
            dealerId: dealerId > 0 ? dealerId : undefined,
            stagingId: lastStagingId,
          });
      setCreateInvoiceEnabled(res.create_invoice_enabled);
      setCreateInvoiceEligibilityReason(res.reason);
      setGenerateInsuranceEnabled(res.generate_insurance_enabled);
      setGenerateInsuranceReason(res.generate_insurance_reason);
      setCpaAllianceInsuranceEnabled(Boolean(res.cpa_alliance_insurance_enabled));
      setCpaAllianceInsuranceReason(res.cpa_alliance_insurance_reason ?? null);
      applyDealerCpaFromApiSlice(res);
      mergeDmsVehicleWithEligibilityInvoice(setDmsScrapedVehicle, res);
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
      setCpaAllianceInsuranceEnabled(false);
      setCpaAllianceInsuranceReason(
        e instanceof Error ? e.message : "Could not verify CPA insurance eligibility for this sale."
      );
    } finally {
      setCreateInvoiceEligibilityLoading(false);
    }
  }, [
    submitInfoActionsComplete,
    mobile,
    extractedVehicle,
    dmsScrapedVehicle,
    lastSubmittedCustomerId,
    lastSubmittedVehicleId,
    dealerId,
    lastStagingId,
    applyDealerCpaFromApiSlice,
  ]);

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
    setDmsRunEndedWithError(false);
    setFillInsuranceStatus(null);
    setPrintFormsStatus(null);
    setPrintFormsStatusSuccess(false);
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
    setCpaAllianceInsuranceEnabled(false);
    setCpaAllianceInsuranceReason(null);
    setCreateInvoiceCompleted(false);
    setGenerateInsuranceCompleted(false);
    setCpaAlliancePortalEnabled(false);
    setCpaInsurers([]);
    setDealerCpaInsurer(null);
    cpaFromSheetRef.current = false;
    setCpaRequired(dealerCpiReqd);
    setHeroCpi(null);
    setIsFillCpaInsuranceLoading(false);
    void reloadDealerCpaContext();
    setSection2ValidationErrors([]);
    setSection2SubmitAttempted(false);
    setAddressLine2Input("");
    addressLine2DirtyRef.current = false;
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

  const cpaPolicyDisplay = useMemo(() => {
    const direct = (ins?.cpa_policy_num ?? "").trim();
    if (direct) return direct;
    const fromRaw = cpaPolicyFromInsuranceRaw(ins as Record<string, unknown> | undefined);
    return fromRaw || "—";
  }, [ins]);

  const reloadInsuranceFromStaging = useCallback(async () => {
    const sid = lastStagingId;
    if (!sid || dealerId <= 0) return;
    try {
      const r = await fetchAddSalesStagingPayload(sid, dealerId);
      const raw = r.payload_json?.insurance;
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        const patch = insuranceFieldsFromStagingInsurance(raw as Record<string, unknown>);
        if (Object.keys(patch).length > 0) {
          setExtractedInsurance((prev) => ({ ...(prev ?? {}), ...patch }));
        }
      }
    } catch {
      /* display refresh is best-effort */
    }
  }, [lastStagingId, dealerId]);

  /** Section 3 dropdown: value in list, else rule (prefer_insurer) until ``useEffect`` syncs ``extractedInsurance.insurer``. */
  const insuranceProviderSelectValue = useMemo(
    () => resolvePortalInsurer(ins?.insurer, preferInsurer, portalInsurers) ?? "",
    [ins?.insurer, preferInsurer, portalInsurers]
  );

  useEffect(() => {
    if (!portalInsurers.length) return;
    const freshSale = !savedTo || String(savedTo).trim() === "";
    const eff = resolvePortalInsurer(
      freshSale ? undefined : extractedInsurance?.insurer,
      preferInsurer,
      portalInsurers
    );
    if (!eff) return;
    const cur = (extractedInsurance?.insurer ?? "").trim();
    if (freshSale || cur !== eff) {
      setExtractedInsurance((prev) => ({ ...(prev ?? {}), insurer: eff }));
    }
  }, [portalInsurers, preferInsurer, extractedInsurance?.insurer, savedTo]);

  useEffect(() => {
    if (!masterRefFinanciers.length) return;
    const freshSale = !savedTo || String(savedTo).trim() === "";
    const raw = (extractedInsurance?.financier ?? "").trim();
    if (!raw) return;
    const resolved = resolveCanonicalFinancier(raw, masterRefFinanciers);
    if (!resolved || !masterRefFinanciers.includes(resolved)) return;
    const cur = (extractedInsurance?.financier ?? "").trim();
    if (freshSale || cur !== resolved) {
      setExtractedInsurance((prev) => ({ ...(prev ?? {}), financier: resolved }));
    }
  }, [masterRefFinanciers, extractedInsurance?.financier, savedTo]);

  const clearSection2Validation = useCallback(() => {
    setSection2ValidationErrors([]);
    setSection2SubmitAttempted(false);
  }, []);

  const section2FieldInvalid = useCallback(
    (field: string) =>
      section2SubmitAttempted && section2ValidationErrors.some((e) => e.field === field),
    [section2SubmitAttempted, section2ValidationErrors]
  );

  const section2ErrorsToShow = section2SubmitAttempted ? section2ValidationErrors : [];

  useEffect(() => {
    if (!addressLine2DirtyRef.current) {
      setAddressLine2Input(buildAddressLine2(extractedCustomer));
    }
  }, [extractedCustomer, formResetKey]);

  useEffect(() => {
    const root = threeColRef.current;
    if (!root) return;
    const onFocusIn = (e: FocusEvent) => {
      const target = e.target;
      if (!isHorizontallyScrollableFocusTarget(target) || !root.contains(target)) return;
      syncAddSalesThreeColFocus(target, root);
    };
    root.addEventListener("focusin", onFocusIn, true);
    return () => root.removeEventListener("focusin", onFocusIn, true);
  }, []);

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
            clearSection2Validation();
            const digits = e.target.value.replace(/\D/g, "").slice(0, 10);
            setMobile(digits);
          }}
          aria-invalid={
            (mobile.length > 0 && !isMobileValid) || section2FieldInvalid("customer_mobile")
          }
        />
      </label>
    </div>
  );

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
            clearSection2Validation();
            const digits = e.target.value.replace(/\D/g, "").slice(0, 10);
            setExtractedCustomer((prev) => ({
              ...(prev ?? {}),
              alt_phone_num: digits,
            }));
          }}
          aria-invalid={section2FieldInvalid("alternate_no")}
        />
      </label>
    </div>
  );

  const hasMeaningfulCustomer = (cust: typeof c) =>
    cust &&
    (cust.aadhar_id ||
      cust.name ||
      cust.address ||
      buildSection2FullAddress(cust) !== "" ||
      buildDisplayAddress(cust) !== "—");
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
      warmBrowsersSubfolderRef.current = null;
    }
  }, [savedTo]);

  /** Pre-open DMS browser as soon as upload has a subfolder and site URLs are ready (before OCR completes). */
  useEffect(() => {
    if (!savedTo) return;
    triggerWarmBrowsers(savedTo);
  }, [savedTo, triggerWarmBrowsers]);

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

    suppressPostDmsEligibilitySyncRef.current = true;
    setIsFillDmsLoading(true);
    setFillDmsStatus(null);
    setDmsRunEndedWithError(false);
    let dmsRes: Awaited<ReturnType<typeof fillDmsLocal>> | null = null;
    try {
      dmsRes = await fillDmsLocal(
        lastStagingId
          ? {
              staging_id: lastStagingId,
              dealer_id: dealerId,
              subfolder: savedTo ?? undefined,
              customer: {
                name: c?.name ?? undefined,
                care_of: c?.care_of ?? undefined,
                address: buildAddressLine1(c) || c?.address || undefined,
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
            }
          : {
              subfolder: savedTo!,
              dms_base_url: dmsUrl,
              dealer_id: dealerId,
              customer_id: lastSubmittedCustomerId ?? undefined,
              vehicle_id: lastSubmittedVehicleId ?? undefined,
              customer: {
                name: c?.name ?? undefined,
                care_of: c?.care_of ?? undefined,
                address: buildAddressLine1(c) || c?.address || undefined,
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
            }
      );
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
            order_number: String(scraped.order_number ?? "").trim() || undefined,
            invoice_number: String(scraped.invoice_number ?? "").trim() || undefined,
          }) as ExtractedVehicleDetails
        );
      }
      if (dmsRes.customer_id != null) setLastSubmittedCustomerId(dmsRes.customer_id);
      if (dmsRes.vehicle_id != null) setLastSubmittedVehicleId(dmsRes.vehicle_id);
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
        dispatchPrintJobsFromApi(dmsRes.print_jobs);
        // Do not open sale-folder PDFs in new browser tabs after Create Invoice (no tab popups /
        // focus steal). ``pdfs_saved`` already includes Run Report paths from the API or sidecar;
        // operators open files from Uploaded scans or use **Print Forms & Queue RTO**.
        if ((dmsRes.pdfs_saved ?? []).length > 0) {
          setDmsPdfsDownloaded(true);
        }
      }
    } catch (err) {
      if (isFillDmsAbortError(err)) {
        setFillDmsStatus("Create Invoice request timed out. Check the upload folder for PDFs.");
      } else {
        setFillDmsStatus(err instanceof Error ? err.message : "Create Invoice (DMS) failed.");
      }
      setDmsRunEndedWithError(true);
      const msg = err instanceof Error ? err.message : String(err);
      if (messageLooksLikeAutomationBrowserLost(msg) && savedTo) {
        warmBrowsersSubfolderRef.current = null;
        triggerWarmBrowsers(savedTo);
      }
    } finally {
      setIsFillDmsLoading(false);
      void (async () => {
        const applyEligibility = (res: CreateInvoiceEligibilityResponse) => {
          setCreateInvoiceEnabled(res.create_invoice_enabled);
          setCreateInvoiceEligibilityReason(res.reason);
          setGenerateInsuranceEnabled(res.generate_insurance_enabled);
          setGenerateInsuranceReason(res.generate_insurance_reason);
          setCpaAllianceInsuranceEnabled(Boolean(res.cpa_alliance_insurance_enabled));
          setCpaAllianceInsuranceReason(res.cpa_alliance_insurance_reason ?? null);
          applyDealerCpaFromApiSlice(res);
          mergeDmsVehicleWithEligibilityInvoice(setDmsScrapedVehicle, res);
          if (res.resolved_customer_id != null) setLastSubmittedCustomerId(res.resolved_customer_id);
          if (res.resolved_vehicle_id != null) setLastSubmittedVehicleId(res.resolved_vehicle_id);
        };
        try {
          const scraped = dmsRes?.vehicle;
          const dmsFrame = (scraped?.full_chassis ?? scraped?.frame_num ?? "").trim();
          const dmsEngine = (scraped?.full_engine ?? scraped?.engine_num ?? "").trim();
          const ocrVeh = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
          const ch = dmsFrame || (ocrVeh?.frame_no ?? "").trim();
          const eng = dmsEngine || (ocrVeh?.engine_no ?? "").trim();
          const mob = mobile.trim();
          const runEligibilityRetry = async (
            fetchOne: () => Promise<CreateInvoiceEligibilityResponse>
          ) => {
            setCreateInvoiceEligibilityLoading(true);
            try {
              const delaysMs = [0, 400, 800, 1400, 2200];
              let lastErr: unknown = null;
              let synced = false;
              for (let i = 0; i < delaysMs.length; i++) {
                if (delaysMs[i] > 0) {
                  await new Promise((r) => setTimeout(r, delaysMs[i]));
                }
                try {
                  const res = await fetchOne();
                  applyEligibility(res);
                  if (res.invoice_recorded || res.generate_insurance_enabled) {
                    synced = true;
                    break;
                  }
                } catch (e) {
                  lastErr = e;
                }
              }
              if (!synced && lastErr != null) {
                await refreshCreateInvoiceEligibility({ force: true });
              }
            } finally {
              setCreateInvoiceEligibilityLoading(false);
            }
          };

          const cid = dmsRes?.customer_id ?? null;
          const vid = dmsRes?.vehicle_id ?? null;
          if (dmsRes?.success && cid != null && vid != null) {
            await runEligibilityRetry(() =>
              fetchCreateInvoiceEligibility({
                customerId: cid,
                vehicleId: vid,
                dealerId: dealerId > 0 ? dealerId : undefined,
                stagingId: lastStagingId,
              })
            );
          } else if (ch && eng && mob) {
            await runEligibilityRetry(() =>
              fetchCreateInvoiceEligibility({
                chassisNum: ch,
                engineNum: eng,
                mobile: mob,
                dealerId: dealerId > 0 ? dealerId : undefined,
                stagingId: lastStagingId,
              })
            );
          } else {
            await refreshCreateInvoiceEligibility({ force: true });
          }
          if (dmsRes?.success && dmsRes.ready_for_client_create_invoice) {
            setCreateInvoiceEnabled(true);
            setCreateInvoiceEligibilityReason(
              "Siebel My Orders already shows an invoice for this mobile — use Create Invoice to commit masters."
            );
          }
        } finally {
          suppressPostDmsEligibilitySyncRef.current = false;
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
      const selectedInsurer = (extractedInsurance?.insurer ?? "").trim();
      const sid = lastStagingId?.trim();
      if (sid && dealerId > 0 && selectedInsurer) {
        await patchAddSalesStagingPayload(sid, dealerId, {
          insurance: { insurer: selectedInsurer },
        });
      }
      await pullAadharScansForInsurance(dealerId, savedTo);
      const insuranceRes = await fillHeroInsuranceLocal(
        lastStagingId
          ? {
              staging_id: lastStagingId,
              dealer_id: dealerId,
              subfolder: savedTo || undefined,
            }
          : {
              subfolder: savedTo,
              dealer_id: dealerId,
              customer_id: lastSubmittedCustomerId ?? undefined,
              vehicle_id: lastSubmittedVehicleId ?? undefined,
            }
      );
      if (!insuranceRes.success) {
        setFillInsuranceStatus(insuranceRes.error ?? "Generate Insurance (Hero) failed.");
      } else if (insuranceRes.hero_insure_reports?.ok === false) {
        setFillInsuranceStatus(
          insuranceRes.hero_insure_reports.error ??
            insuranceRes.error ??
            "Print Policy / PDF download failed."
        );
      } else {
        const successMsg =
          "Hero Insurance run completed (pre + main + post). Browser may remain open for operator.";
        setFillInsuranceStatus(successMsg);
        setGenerateInsuranceCompleted(true);
        dispatchPrintJobsFromApi(insuranceRes.print_jobs);
        await reloadInsuranceFromStaging();
        if (
          cpaAlliancePortalEnabled &&
          cpaAllianceInsuranceEnabled &&
          savedTo &&
          dealerId > 0 &&
          !hasSuppliedInsuranceDoc
        ) {
          const portal = pickCpaPortalRow(cpaInsurers, dealerCpaInsurer);
          if (portal?.login_url) {
            void fillCpaAllianceInsuranceLocal(
              buildFillCpaAllianceInsuranceRequest({
                dealerId,
                subfolder: savedTo,
                portalUrl: portal.login_url,
                stagingId: lastStagingId ?? undefined,
                customerId: lastSubmittedCustomerId ?? undefined,
                vehicleId: lastSubmittedVehicleId ?? undefined,
              })
            ).catch(() => {});
          }
        }
      }
    } catch (insuranceErr) {
      if (isFillDmsAbortError(insuranceErr)) {
        setFillInsuranceStatus("Insurance request timed out. Browser remains open for operator.");
      } else {
        setFillInsuranceStatus(insuranceErr instanceof Error ? insuranceErr.message : "Insurance fill failed.");
      }
      const msg = insuranceErr instanceof Error ? insuranceErr.message : String(insuranceErr);
      if (messageLooksLikeAutomationBrowserLost(msg) && savedTo) {
        warmBrowsersSubfolderRef.current = null;
        triggerWarmBrowsers(savedTo);
      }
    } finally {
      setIsFillInsuranceLoading(false);
      void refreshCreateInvoiceEligibility();
    }
  };

  const handleCpaAllianceInsurance = async () => {
    if (!savedTo) {
      setFillInsuranceStatus("Upload scans first.");
      return;
    }
    if (!submitInfoActionsComplete) {
      setFillInsuranceStatus("Complete Submit Info (Section 2) before CPA Insurance.");
      return;
    }
    if (!hasCommittedSaleIds) {
      setFillInsuranceStatus("Run Create Invoice first so master IDs exist for CPA.");
      return;
    }
    if (!cpaAlliancePortalEnabled || !cpaInsurers.length) {
      setFillInsuranceStatus(
        "CPA Alliance is not enabled (dealer hero_cpi = Y with CPI add-on, or no CPA URLs in master_ref)."
      );
      return;
    }
    const portal = pickCpaPortalRow(cpaInsurers, dealerCpaInsurer);
    if (!portal?.login_url) {
      setFillInsuranceStatus("No CPA portal URL — set master_ref.comments for the CPA row.");
      return;
    }
    if (heroCpi === "Y") {
      setFillInsuranceStatus("CPA Alliance is disabled while dealer Hero CPI is Y.");
      return;
    }
    if (!cpaAllianceInsuranceEnabled) {
      setFillInsuranceStatus(
        cpaAllianceInsuranceReason ?? "CPA Insurance is not available for this sale."
      );
      return;
    }
    setIsFillCpaInsuranceLoading(true);
    setFillInsuranceStatus(null);
    try {
      const cpaRes = await fillCpaAllianceInsuranceLocal(
        buildFillCpaAllianceInsuranceRequest({
          dealerId,
          subfolder: savedTo,
          portalUrl: portal.login_url,
          stagingId: lastStagingId ?? undefined,
          customerId: lastSubmittedCustomerId ?? undefined,
          vehicleId: lastSubmittedVehicleId ?? undefined,
        })
      );
      if (!cpaRes.success) {
        setFillInsuranceStatus(cpaRes.error ?? "CPA Insurance failed.");
      } else {
        setFillInsuranceStatus(
          cpaRes.certificate_number
            ? `CPA Insurance completed. Certificate: ${cpaRes.certificate_number}`
            : "CPA Insurance completed."
        );
        await reloadInsuranceFromStaging();
      }
    } catch (e) {
      setFillInsuranceStatus(e instanceof Error ? e.message : "CPA Insurance failed.");
    } finally {
      setIsFillCpaInsuranceLoading(false);
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
    setPrintFormsStatusSuccess(false);
    setPrintFormsStatusSuccess(false);

    try {
      const result = await runPrintQueueRtoFlow({
        dealerId,
        stagingId: lastStagingId?.trim() ?? "",
        subfolder: savedTo,
        customer: {
          name: c?.name ?? undefined,
          care_of: c?.care_of ?? undefined,
          address: buildAddressLine1(c) || c?.address || undefined,
          city: c?.city ?? undefined,
          state: c?.state ?? undefined,
          pin_code: c?.pin_code ?? undefined,
          aadhar_id: c?.aadhar_id ?? undefined,
          mobile: mobile ?? undefined,
        },
        vehicle: vehicleDataForGatePass,
        vehicleId: lastSubmittedVehicleId ?? undefined,
        pendingScannerArchiveMove: pendingScannerArchiveMove ?? undefined,
      });
      if (result.gatePassSucceeded) {
        setHasPrintedForms(true);
      }
      if (result.gatePassSucceeded && pendingScannerArchiveMove) {
        setPendingScannerArchiveMove(null);
      }
      setPrintFormsStatus(result.statusLines.join(" "));
      setPrintFormsStatusSuccess(result.success);
    } finally {
      setIsPrintFormsLoading(false);
    }
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

  const pageActionsBusy =
    isFillDmsLoading ||
    isFillInsuranceLoading ||
    isFillCpaInsuranceLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    inProcessActionStagingId != null;

  /** Same disabled logic as each primary button — used for Print Forms gate. */
  const newButtonDisabled =
    pageActionsBusy ||
    (submitInfoActionsComplete && !hasPrintedForms);

  const submitInfoPrimaryButtonDisabled =
    isSubmitting ||
    pageActionsBusy ||
    !c ||
    (!manualFormOnly && !insuranceReadByTextract) ||
    !!extractionError ||
    submitInfoActionsComplete;

  const createInvoicePrimaryButtonDisabled =
    isFillDmsLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    pageActionsBusy ||
    !submitInfoActionsComplete ||
    createInvoiceEligibilityLoading ||
    createInvoiceCompleted ||
    !createInvoiceEnabled ||
    siteUrlsLoading ||
    !!siteUrlsError ||
    !dmsUrl;

  const generateInsurancePrimaryButtonDisabled =
    isFillInsuranceLoading ||
    isFillCpaInsuranceLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    pageActionsBusy ||
    !submitInfoActionsComplete ||
    !hasCommittedSaleIds ||
    createInvoiceEligibilityLoading ||
    generateInsuranceCompleted ||
    !generateInsuranceEnabled ||
    hasSuppliedInsuranceDoc ||
    siteUrlsLoading ||
    !!siteUrlsError;

  const dealerCpaRef = (dealerCpaInsurer ?? "").trim();

  const cpaAlliancePrimaryButtonDisabled =
    isFillInsuranceLoading ||
    isFillCpaInsuranceLoading ||
    isPrintFormsLoading ||
    isSubmitting ||
    pageActionsBusy ||
    !submitInfoActionsComplete ||
    !hasCommittedSaleIds ||
    createInvoiceEligibilityLoading ||
    !cpaAlliancePortalEnabled ||
    !cpaInsurers.length ||
    !cpaSelectedPortalUrl ||
    !cpaAllianceInsuranceEnabled ||
    dealerId <= 0 ||
    siteUrlsLoading ||
    !!siteUrlsError;

  /** Print only when the other four actions are inactive; after first print, `hasPrintedForms` allows re-print while New is enabled again. */
  const printFormsButtonEnabled =
    submitInfoActionsComplete &&
    !pageActionsBusy &&
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
      uploadStatus={uploadStatus}
      uploadedFiles={uploadedFiles}
      savedTo={savedTo}
      onUploadConsolidated={uploadConsolidatedV2}
      ocrCountdownSeconds={ocrWaitActive ? ocrCountdownSec : null}
      dealerId={dealerId}
    />
  );

  return (
    <div
      className={`add-sales-v2${
        addSalesPageTab === "in-process"
          ? " add-sales-v2--in-process-tab"
          : addSalesPageTab === "invoices"
            ? " add-sales-v2--invoices-tab"
            : ""
      }`}
    >
      <nav className="challans-subtabs" role="tablist" aria-label="Add Sales tabs">
        <button
          type="button"
          role="tab"
          aria-selected={addSalesPageTab === "add-sales"}
          className={`challans-subtab ${addSalesPageTab === "add-sales" ? "active" : ""}`}
          onClick={() => setAddSalesPageTab("add-sales")}
        >
          New Sales
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={addSalesPageTab === "in-process"}
          className={`challans-subtab ${addSalesPageTab === "in-process" ? "active" : ""}`}
          onClick={() => setAddSalesPageTab("in-process")}
        >
          In-process
          {inProcessBadgeCount > 0 ? (
            <span className="app-tab-badge app-tab-badge--danger">
              {" "}
              ({inProcessBadgeCount})
            </span>
          ) : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={addSalesPageTab === "invoices"}
          className={`challans-subtab ${addSalesPageTab === "invoices" ? "active" : ""}`}
          onClick={() => setAddSalesPageTab("invoices")}
        >
          Invoices
        </button>
      </nav>
      <div
        className={`add-sales-in-process-tab-panel${
          addSalesPageTab !== "in-process" ? " add-sales-tab-panel--hidden" : ""
        }`}
      >
        <AddSalesInProcessPanel
          dealerId={dealerId}
          dmsUrl={dmsUrl ?? ""}
          siteUrlsLoading={siteUrlsLoading}
          siteUrlsError={siteUrlsError ?? null}
          preferInsurer={preferInsurer ?? null}
          inProcessTabActive={addSalesPageTab === "in-process"}
          addSalesMainTabActive={addSalesPageTab === "add-sales"}
          mainLastStagingId={lastStagingId}
          pageActionsBusy={pageActionsBusy}
          onRowActionStart={(stagingId: string) => setInProcessActionStagingId(stagingId)}
          onRowActionEnd={() => setInProcessActionStagingId(null)}
          onInProcessCountChange={setInProcessBadgeCount}
        />
      </div>
      <div
        className={`add-sales-invoices-tab-panel${
          addSalesPageTab !== "invoices" ? " add-sales-tab-panel--hidden" : ""
        }`}
      >
        <AddSalesInvoicesPanel dealerId={dealerId} invoicesTabActive={addSalesPageTab === "invoices"} />
      </div>
      <main className={`add-sales-v2-main ${addSalesPageTab !== "add-sales" ? "add-sales-tab-panel--hidden" : ""}`}>
        <div className="add-sales-v2-three-col" ref={threeColRef}>
          <section className="add-sales-v2-box add-sales-v2-box-upload">
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">1. Upload Customer Scans</h2>
                <button
                  type="button"
                  className="app-button app-button--primary"
                  disabled={newButtonDisabled}
                  onClick={handleNew}
                  title={
                    isFillDmsLoading || isFillInsuranceLoading || isPrintFormsLoading || isSubmitting || inProcessActionStagingId != null
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
                    onApplied={(to, files, extraction) => {
                      setSavedTo(to);
                      setUploadedFiles(files);
                      setManualFallbackPayload(null);
                      const err = extraction?.error;
                      const details = extraction?.details;
                      if (!err && details) {
                        applyExtractedDetails(details, { savedToForWarm: to });
                        setManualFormOnly(false);
                        setUploadStatus(`Documents saved to ${to}. Review Section 2 and Submit Info when ready.`);
                      } else {
                        setManualFormOnly(true);
                        setUploadStatus(
                          err
                            ? `Documents saved to ${to}. OCR: ${err}. Fill Section 2 manually.`
                            : `Documents saved to ${to}. Fill Section 2 manually.`
                        );
                      }
                    }}
                    onDismiss={() => {
                      setManualFallbackPayload(null);
                      setPendingScannerArchiveMove(null);
                      setManualFormOnly(true);
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
                      const parsedLine2 = parseAddressLine2(addressLine2Input);
                      const customerForValidate: ExtractedCustomerDetails = { ...c, ...parsedLine2 };
                      const validationErrors = getSection2ValidationErrors({
                        savedTo,
                        mobile,
                        customer: customerForValidate,
                        vehicle: v ?? null,
                        insurance: ins ?? null,
                        addressLine2Input,
                        masterRefFinanciers,
                        includeInsuranceFields: manualFormOnly || insuranceReadByTextract,
                      });
                      if (validationErrors.length > 0) {
                        setSection2ValidationErrors(validationErrors);
                        setSection2SubmitAttempted(true);
                        setSubmitStatus(
                          validationErrors
                            .map((e) => `${section2FieldLabel(e.field)}: ${e.message}`)
                            .join(" · ")
                        );
                        return;
                      }
                      const normedLine2 = normalizeOperatorFreeformAddress(addressLine2Input, {
                        minCommaSegments: 2,
                      });
                      if (!normedLine2) {
                        setSection2ValidationErrors([
                          { field: "address_line2", message: "Address could not be normalized." },
                        ]);
                        setSection2SubmitAttempted(true);
                        setSubmitStatus("Address (City, State, PIN): Address could not be normalized.");
                        return;
                      }
                      setAddressLine2Input(normedLine2.address);
                      addressLine2DirtyRef.current = false;
                      const line1Raw = buildAddressLine1(c) || c?.address || "";
                      const line1Upper = line1Raw.trim() ? uppercaseAddressLocality(line1Raw.trim()) : undefined;
                      const customerForSubmit: ExtractedCustomerDetails = {
                        ...c,
                        address: line1Upper,
                        house: undefined,
                        street: undefined,
                        location: undefined,
                        post_office: undefined,
                        district: undefined,
                        sub_district: undefined,
                        city: normedLine2.city,
                        state: normedLine2.state,
                        pin_code: normedLine2.pin_code,
                      };
                      setExtractedCustomer(customerForSubmit);
                      setSection2ValidationErrors([]);
                      setSection2SubmitAttempted(false);
                      setIsSubmitting(true);
                      setSubmitStatus(null);
                      try {
                        const submitRes = await submitInfo({
                          customer: customerForSubmit,
                          vehicle: v ?? null,
                          insurance: ins ?? null,
                          mobile,
                          fileLocation: savedTo,
                          dealerId,
                          oemId,
                          stagingId: lastStagingId,
                          preferInsurer,
                          portalInsurers,
                          cpiReqd: cpaRequired,
                        });
                        setHasSubmittedInfo(true);
                        if (submitRes?.staging_id != null && String(submitRes.staging_id).trim())
                          setLastStagingId(String(submitRes.staging_id).trim());
                        setSubmitStatus("Saved");
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
                        const msg =
                          err instanceof ApiHttpError
                            ? err.message
                            : err instanceof Error
                              ? err.message
                              : "Submit failed";
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
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-mobile">
                    {mobileRow}
                    <Section2FieldError field="customer_mobile" errors={section2ErrorsToShow} />
                  </div>
                  <div className="add-sales-v2-input-wrap add-sales-v2-input-alt">
                    {alternateMobileRow}
                    <Section2FieldError field="alternate_no" errors={section2ErrorsToShow} />
                  </div>
                </div>
                <div
                  className={
                    !savedTo && !manualFallbackPayload && !manualFormOnly ? "add-sales-v2-box--greyed" : ""
                  }
                >
                {extractionComplete && savedTo && !manualFormOnly && !insuranceReadByTextract && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <span className="add-sales-v2-status-text">Waiting for insurance details from document.</span>
                  </div>
                )}
                {extractionComplete && savedTo && section2SubmitAttempted && section2ValidationErrors.length > 0 && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <ul className="add-sales-v2-validation-list">
                      {section2ValidationErrors.map((e) => (
                        <li key={e.field}>
                          <strong>{section2FieldLabel(e.field)}</strong>: {e.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {submitStatus && (!savedTo || !section2SubmitAttempted || section2ValidationErrors.length === 0) && (
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
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              name: sanitizeFormFieldInputValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          autoComplete="name"
                          aria-invalid={section2FieldInvalid("name")}
                        />
                        <Section2FieldError field="name" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--full">
                      <dt>C/O</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--care-of-free"
                          value={c?.care_of ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            const raw = sanitizeFormFieldInputValue(e.target.value);
                            const parsed = parseCareOfFromCombined(raw);
                            const has = raw.trim() !== "";
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              care_of: raw || undefined,
                              care_of_relation: has ? parsed.relation : undefined,
                              care_of_name: has ? parsed.name || undefined : undefined,
                            }));
                          }}
                          placeholder="S/o Father's Name"
                          autoComplete="off"
                          spellCheck={false}
                          aria-invalid={section2FieldInvalid("care_of")}
                        />
                        <Section2FieldError field="care_of" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--full add-sales-v2-dl-customer-line--address">
                      <dt>Address</dt>
                      <dd className="add-sales-v2-dd--address-rows">
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--address-line1"
                          value={buildAddressLine1(c)}
                          onChange={(e) => {
                            clearSection2Validation();
                            const line1 = sanitizeFormFieldInputValue(e.target.value);
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              address: line1 || undefined,
                              house: undefined,
                              street: undefined,
                              location: undefined,
                              post_office: undefined,
                              district: undefined,
                              sub_district: undefined,
                            }));
                          }}
                          placeholder="House, street, locality"
                          aria-invalid={section2FieldInvalid("address")}
                        />
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--address-line2"
                          value={addressLine2Input}
                          onChange={(e) => {
                            clearSection2Validation();
                            addressLine2DirtyRef.current = true;
                            setAddressLine2Input(sanitizeFormFieldInputValue(e.target.value));
                          }}
                          onBlur={() => {
                            const parsed = parseAddressLine2(addressLine2Input);
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              city: parsed.city,
                              state: parsed.state,
                              pin_code: parsed.pin_code,
                            }));
                          }}
                          placeholder="City, State, PIN"
                          aria-invalid={section2FieldInvalid("address_line2")}
                        />
                        <Section2FieldError field="address" errors={section2ErrorsToShow} />
                        <Section2FieldError field="address_line2" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-customer-line add-sales-v2-dl-customer-line--dob-gender">
                      <dt>Gender</dt>
                      <dd className="add-sales-v2-dd--gender-narrow">
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--gender-narrow"
                          value={c?.gender ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              gender: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          autoComplete="sex"
                          aria-invalid={section2FieldInvalid("gender")}
                        />
                        <Section2FieldError field="gender" errors={section2ErrorsToShow} />
                      </dd>
                      <dt>DOB</dt>
                      <dd className="add-sales-v2-dd--dob-full">
                        <input
                          className="add-sales-v2-dl-input add-sales-v2-dl-input--dob"
                          type="text"
                          inputMode="numeric"
                          autoComplete="bday"
                          value={c?.date_of_birth ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              date_of_birth: formatDobDigitsInput(e.target.value),
                            }));
                          }}
                          placeholder="DD/MM/YYYY"
                          aria-invalid={section2FieldInvalid("dob")}
                        />
                        <Section2FieldError field="dob" errors={section2ErrorsToShow} />
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
                            clearSection2Validation();
                            const digits = e.target.value.replace(/\D/g, "").slice(0, 4);
                            setExtractedCustomer((prev) => ({
                              ...(prev ?? {}),
                              aadhar_id: digits,
                            }));
                          }}
                          placeholder="0000"
                          aria-invalid={section2FieldInvalid("aadhar")}
                        />
                        <Section2FieldError field="aadhar" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Finance Details</h3>
                  </div>
                  {dealerCpaContextError ? (
                    <p className="add-sales-v2-field-note" role="alert">
                      {dealerCpaContextError}
                    </p>
                  ) : null}
                  <dl className="add-sales-v2-dl add-sales-v2-dl--insurance">
                    <div className="add-sales-v2-dl-row">
                      <dt>Financier</dt>
                      <dd>
                        {(() => {
                          const financierVal = (ins?.financier ?? "").trim();
                          const financierInList =
                            financierVal === "" || masterRefFinanciers.includes(financierVal);
                          return (
                            <>
                              <select
                                className="add-sales-v2-dl-input"
                                value={financierInList ? financierVal : ""}
                                onChange={(e) => {
                                  clearSection2Validation();
                                  const v = e.target.value.trim();
                                  setExtractedInsurance((prev) => ({
                                    ...(prev ?? {}),
                                    financier: v || undefined,
                                  }));
                                }}
                                aria-invalid={section2FieldInvalid("financier")}
                              >
                                <option value="">—</option>
                                {masterRefFinanciers.map((f) => (
                                  <option key={f} value={f}>
                                    {f}
                                  </option>
                                ))}
                              </select>
                              {!financierInList && financierVal ? (
                                <p className="add-sales-v2-field-note">
                                  Detected «{financierVal}» — select a financier from the list.
                                </p>
                              ) : null}
                              <Section2FieldError field="financier" errors={section2ErrorsToShow} />
                              {isHeroBajajFinancierForStaging(oemId, ins?.financier) && (
                                <p className="add-sales-v2-field-note">
                                  This financier will be logged in systems as Hinduja.
                                </p>
                              )}
                            </>
                          );
                        })()}
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
                  <dl className="add-sales-v2-dl add-sales-v2-dl--vehicle">
                    <div className="add-sales-v2-dl-row">
                      <dt>Key no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.key_no ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              key_no: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("key_no")}
                        />
                        <Section2FieldError field="key_no" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Chassis No.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.frame_no ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              frame_no: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("chassis_no")}
                        />
                        <Section2FieldError field="chassis_no" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Engine no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.engine_no ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              engine_no: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("engine_no")}
                        />
                        <Section2FieldError field="engine_no" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Battery no.</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={v?.battery_no ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedVehicle((prev) => ({
                              ...(prev ?? {}),
                              battery_no: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("battery_no")}
                        />
                        <Section2FieldError field="battery_no" errors={section2ErrorsToShow} />
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
                  <dl className="add-sales-v2-dl add-sales-v2-dl--insurance">
                    <div className="add-sales-v2-dl-row">
                      <dt>Customer Profession</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.profession ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              profession: sanitizeFormFieldInputValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("profession")}
                        />
                        <Section2FieldError field="profession" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Customer Marital Status</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.marital_status ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              marital_status: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("marital_status")}
                        />
                        <Section2FieldError field="marital_status" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Nominee Name</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.nominee_name ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              nominee_name: sanitizeFormFieldInputValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("nominee_name")}
                        />
                        <Section2FieldError field="nominee_name" errors={section2ErrorsToShow} />
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
                            clearSection2Validation();
                            const v = sanitizeNomineeAgeInput(e.target.value);
                            setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_age: v }));
                          }}
                          placeholder="e.g. 30"
                          title="Numbers only (1–150)"
                          aria-invalid={section2FieldInvalid("nominee_age")}
                        />
                        <Section2FieldError field="nominee_age" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Nominee Gender</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.nominee_gender ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              nominee_gender: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("nominee_gender")}
                        />
                        <Section2FieldError field="nominee_gender" errors={section2ErrorsToShow} />
                      </dd>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Relationship</dt>
                      <dd>
                        <input
                          className="add-sales-v2-dl-input"
                          value={ins?.nominee_relationship ?? ""}
                          onChange={(e) => {
                            clearSection2Validation();
                            setExtractedInsurance((prev) => ({
                              ...(prev ?? {}),
                              nominee_relationship: sanitizeFormFieldValue(e.target.value),
                            }));
                          }}
                          placeholder="—"
                          aria-invalid={section2FieldInvalid("nominee_relationship")}
                        />
                        <Section2FieldError field="nominee_relationship" errors={section2ErrorsToShow} />
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
                <div className="add-sales-v2-subsection-head add-sales-v2-subsection-head--fill-forms-row">
                  <div className="add-sales-v2-subsection-head-left">
                    <h3 className="add-sales-v2-subsection-title">A. DMS</h3>
                    {isFillDmsLoading && <span className="add-sales-v2-processing">Processing</span>}
                  </div>
                  <button
                    type="button"
                    className="app-button app-button--primary add-sales-v2-fill-forms-action-btn"
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
                <dl className="add-sales-v2-dl add-sales-v2-dl--dms" style={{ marginTop: "0.75rem" }}>
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>Order #</dt>
                      <dd>{d?.order_number?.trim() ? d.order_number : "—"}</dd>
                    </div>
                  </div>
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>Invoice #</dt>
                      <dd>{d?.invoice_number?.trim() ? d.invoice_number : "—"}</dd>
                    </div>
                  </div>
                </dl>
              </div>
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head add-sales-v2-subsection-head--fill-forms-row">
                  <div className="add-sales-v2-subsection-head-left">
                    <h3 className="add-sales-v2-subsection-title">B. Insurance</h3>
                    {(isFillInsuranceLoading || isFillCpaInsuranceLoading) && (
                      <span className="add-sales-v2-processing">Processing</span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="app-button app-button--primary add-sales-v2-fill-forms-action-btn"
                    disabled={generateInsurancePrimaryButtonDisabled}
                    onClick={handleFillInsurance}
                    title={generateInsuranceButtonTitle}
                  >
                    {isFillInsuranceLoading ? "Processing…" : "Generate Insurance"}
                  </button>
                </div>
                <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>Insurance Provider</dt>
                      <dd className="add-sales-v2-dd--insurance-editable">
                        {portalInsurers.length > 0 ? (
                          <select
                            className="add-sales-v2-dl-input add-sales-v2-dl-input--insurance-provider-wide"
                            aria-label="Insurance provider"
                            value={insuranceProviderSelectValue}
                            onChange={(e) => {
                              const v = e.target.value;
                              setExtractedInsurance((prev) => ({ ...(prev ?? {}), insurer: v }));
                              const patchSid = lastStagingId?.trim();
                              if (patchSid && dealerId > 0 && v.trim()) {
                                void patchAddSalesStagingPayload(patchSid, dealerId, {
                                  insurance: { insurer: v },
                                }).catch(() => {
                                  /* GI will patch again before fill */
                                });
                              }
                            }}
                          >
                            {portalInsurers.map((name) => (
                              <option key={name} value={name}>
                                {name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          (preferInsurer ?? ins?.insurer ?? "").trim() || "—"
                        )}
                      </dd>
                    </div>
                  </div>
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>Hero CPA</dt>
                      <dd>{heroCpi === "Y" ? "Yes" : heroCpi === "N" ? "No" : "—"}</dd>
                    </div>
                  </div>
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>Policy No.</dt>
                      <dd>{ins?.policy_num?.trim() ? ins.policy_num : "—"}</dd>
                    </div>
                  </div>
                </dl>
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
              </div>
              {cpaAlliancePortalEnabled && cpaInsurers.length > 0 && dealerId > 0 && (
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head add-sales-v2-subsection-head--fill-forms-row">
                  <div className="add-sales-v2-subsection-head-left">
                    <h3 className="add-sales-v2-subsection-title">C. CPA</h3>
                    {isFillCpaInsuranceLoading && <span className="add-sales-v2-processing">Processing</span>}
                  </div>
                  <button
                    type="button"
                    className="app-button app-button--primary add-sales-v2-fill-forms-action-btn"
                    disabled={cpaAlliancePrimaryButtonDisabled}
                    onClick={() => void handleCpaAllianceInsurance()}
                    title={
                      !cpaAlliancePortalEnabled || !cpaInsurers.length
                        ? "CPA portal is not enabled or no insurers are configured for this dealer."
                        : !cpaSelectedPortalUrl
                          ? "No CPA row has a valid https URL in master_ref.comments."
                          : !cpaAllianceInsuranceEnabled
                            ? cpaAllianceInsuranceReason ??
                              "CPA Insurance is not available for this sale."
                          : !submitInfoActionsComplete || !hasCommittedSaleIds
                            ? "Complete Submit Info and Create Invoice first."
                            : undefined
                    }
                  >
                    {isFillCpaInsuranceLoading ? "Opening…" : "CPA Insurance"}
                  </button>
                </div>
                <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>CPA Required</dt>
                      <dd>
                        <select
                          className="add-sales-v2-input"
                          value={cpaRequired}
                          onChange={(e) => setCpaRequired(normalizeCpaRequiredFlag(e.target.value))}
                          aria-label="CPA Required"
                        >
                          <option value="Y">Yes</option>
                          <option value="N">No</option>
                        </select>
                      </dd>
                    </div>
                  </div>
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>CPA Provider</dt>
                      <dd>{resolvedCpaPortal?.ref_value || dealerCpaRef || "—"}</dd>
                    </div>
                  </div>
                  <div className="add-sales-v2-dl-row-group">
                    <div className="add-sales-v2-dl-row">
                      <dt>Policy No.</dt>
                      <dd>{cpaPolicyDisplay}</dd>
                    </div>
                  </div>
                </dl>
              </div>
              )}
              {fillInsuranceStatus && (
                <div className="add-sales-v2-print-forms-row">
                  <StatusMessage message={fillInsuranceStatus} className="app-panel-status" role="status" />
                </div>
              )}
              {printFormsStatus && (
                <div className="add-sales-v2-print-forms-row">
                  <StatusMessage
                    message={printFormsStatus}
                    className={`app-panel-status ${printFormsStatusSuccess ? "app-panel-status--success" : "app-panel-status--error"}`}
                    role="status"
                  />
                </div>
              )}
              <div className="add-sales-v2-rto-actions add-sales-v2-print-forms-actions">
                <button
                  type="button"
                  className="app-button app-button--primary add-sales-v2-fill-forms-action-btn"
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
