import { useState, useEffect, useRef } from "react";
import type { ExtractedVehicleDetails, ExtractedCustomerDetails, ExtractedInsuranceDetails } from "../types";
import { buildDisplayAddress } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { submitInfo } from "../api/submitInfo";
import { fillDmsOnly, fillVahanOnly, printForm20, getDataFromDms, isFillDmsAbortError } from "../api/fillDms";
import { insertRtoPayment } from "../api/rtoPaymentDetails";
import { getBaseUrl } from "../api/client";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
import { markBulkLoadSuccess } from "../api/bulkLoads";
import { normalizeVehicleDetails, hasVehicleData } from "../utils/vehicleDetails";

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

interface AddSalesPageProps {
  dealerId: number;
  /** DMS base URL for Fill DMS (Playwright). */
  dmsUrl?: string;
  /** When provided, clicking DMS in the left nav opens the DMS URL in a new browser tab. */
  openDmsInNewTab?: () => void;
  /** When provided, clicking RTO in the left nav opens the Vahan (RTO) URL in a new browser tab. */
  openVahanInNewTab?: () => void;
}

export function AddSalesPage({ dealerId, dmsUrl, openDmsInNewTab, openVahanInNewTab }: AddSalesPageProps) {
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
  const [fillVahanStatus, setFillVahanStatus] = useState<string | null>(null);
  const [isFillDmsLoading, setIsFillDmsLoading] = useState(false);
  const [isFillVahanLoading, setIsFillVahanLoading] = useState(false);
  const [isPrintFormsLoading, setIsPrintFormsLoading] = useState(false);
  const [printFormsStatus, setPrintFormsStatus] = useState<string | null>(null);
  /** DMS-scraped vehicle; shown in Fill Forms > DMS. Only populated when user presses Fill Forms. */
  const [dmsScrapedVehicle, setDmsScrapedVehicle] = useState<ExtractedVehicleDetails | null>(null);
  /** True when Form 21 and Form 22 PDFs have been downloaded from DMS. */
  const [dmsPdfsDownloaded, setDmsPdfsDownloaded] = useState(false);
  /** True after user has successfully pressed Submit Info. (Section 3 stays greyed until then.) */
  const [hasSubmittedInfo, setHasSubmittedInfo] = useState(() => getInitialForm().hasSubmittedInfo);
  /** True after user has used Print forms. (Used for beforeunload warning.) */
  const [hasPrintedForms, setHasPrintedForms] = useState(false);
  /** From last successful Submit Info; used when inserting RTO payment row after Fill Forms. */
  const [lastSubmittedCustomerId, setLastSubmittedCustomerId] = useState<number | null>(() => getInitialForm().lastSubmittedCustomerId);
  const [lastSubmittedVehicleId, setLastSubmittedVehicleId] = useState<number | null>(() => getInitialForm().lastSubmittedVehicleId);
  /** From Fill Forms (Vahan step); shown under C. RTO. Only populated when user presses Fill Forms. */
  const [rtoApplicationId, setRtoApplicationId] = useState<string | null>(null);
  const [rtoPaymentDue, setRtoPaymentDue] = useState<number | null>(null);
  /** Extraction error (e.g. QR code not readable) – stops poll and shows message. */
  const [extractionError, setExtractionError] = useState<string | null>(null);
  /** True once Textract has returned insurance data for this upload (details sheet processed). */
  const [insuranceReadByTextract, setInsuranceReadByTextract] = useState(() => {
    const stored = loadAddSalesForm().extractedInsurance;
    return Boolean(
      stored &&
        [
          stored.profession,
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
      setExtractedInsurance((prev) => {
        const current = prev ?? {};
        const fromServer = {
          profession: typeof r.profession === "string" ? r.profession : undefined,
          nominee_name: typeof r.nominee_name === "string" ? r.nominee_name : undefined,
          nominee_age: r.nominee_age != null ? String(r.nominee_age) : undefined,
          nominee_relationship: typeof r.nominee_relationship === "string" ? r.nominee_relationship : undefined,
          insurer: typeof r.insurer === "string" ? r.insurer : undefined,
          policy_num: typeof r.policy_num === "string" ? r.policy_num : undefined,
          policy_from: typeof r.policy_from === "string" ? r.policy_from : undefined,
          policy_to: typeof r.policy_to === "string" ? r.policy_to : undefined,
          premium: typeof r.premium === "string" ? r.premium : r.premium != null ? String(r.premium) : undefined,
        };
        return {
          ...current,
          profession: current.profession && current.profession.trim() !== "" ? current.profession : fromServer.profession,
          nominee_name: current.nominee_name && current.nominee_name.trim() !== "" ? current.nominee_name : fromServer.nominee_name,
          nominee_age: current.nominee_age && current.nominee_age.trim() !== "" ? current.nominee_age : fromServer.nominee_age,
          nominee_relationship:
            current.nominee_relationship && current.nominee_relationship.trim() !== ""
              ? current.nominee_relationship
              : fromServer.nominee_relationship,
          insurer: fromServer.insurer ?? current.insurer,
          policy_num: fromServer.policy_num ?? current.policy_num,
          policy_from: fromServer.policy_from ?? current.policy_from,
          policy_to: fromServer.policy_to ?? current.policy_to,
          premium: fromServer.premium ?? current.premium,
        };
      });
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
  }, dealerId);

  const pollCountRef = useRef(0);
  const POLL_MAX = 20;
  const POLL_INTERVAL_MS = 2000;

  // Persist form state so it survives navigation; clear only on "New"
  useEffect(() => {
    saveAddSalesForm({
      mobile,
      savedTo,
      uploadedFiles,
      uploadStatus,
      dmsScrapedVehicle,
      rtoApplicationId,
      rtoPaymentDue,
      hasSubmittedInfo,
      lastSubmittedCustomerId,
      lastSubmittedVehicleId,
      extractedVehicle,
      extractedCustomer,
      extractedInsurance,
    });
  }, [mobile, savedTo, uploadedFiles, uploadStatus, dmsScrapedVehicle, rtoApplicationId, rtoPaymentDue, hasSubmittedInfo, lastSubmittedCustomerId, lastSubmittedVehicleId, extractedVehicle, extractedCustomer, extractedInsurance]);

  // DMS and RTO sections populate only when user presses Fill Forms. No auto-fetch from file or DB.

  // Warn on close/refresh if customer processing not complete (forms not filled or print forms not done)
  useEffect(() => {
    const message = "Customer processing is not complete and the information will be lost.";
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      if (hasSubmittedInfo && (!dmsPdfsDownloaded || !hasPrintedForms)) {
        e.preventDefault();
        e.returnValue = message;
        return message;
      }
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasSubmittedInfo, dmsPdfsDownloaded, hasPrintedForms]);

  const hasCustomer = Boolean(
    extractedCustomer &&
    (extractedCustomer.name || extractedCustomer.address || extractedCustomer.aadhar_id ||
     extractedCustomer.city || extractedCustomer.state || extractedCustomer.pin_code ||
     extractedCustomer.care_of || extractedCustomer.house || extractedCustomer.street || extractedCustomer.location)
  );
  // When we have savedTo but missing vehicle or customer data, fetch once to fill Extracted Information
  const hasVehicle = hasVehicleData(extractedVehicle);
  useEffect(() => {
    if (!savedTo || (hasVehicle && hasCustomer)) return;
    let cancelled = false;
      getExtractedDetails(savedTo, dealerId)
      .then((details) => {
        if (cancelled) return;
        const extractionErr = (details as Record<string, unknown>)?.extraction_error;
        const nameMismatchErr = (details as Record<string, unknown>)?.name_mismatch_error;
        const err = typeof nameMismatchErr === "string" ? nameMismatchErr : typeof extractionErr === "string" ? extractionErr : null;
        setExtractionError(err);
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
          setExtractedInsurance((prev) => {
            const current = prev ?? {};
            const fromServer = {
              profession: typeof r.profession === "string" ? r.profession : undefined,
              nominee_name: typeof r.nominee_name === "string" ? r.nominee_name : undefined,
              nominee_age: r.nominee_age != null ? String(r.nominee_age) : undefined,
              nominee_relationship: typeof r.nominee_relationship === "string" ? r.nominee_relationship : undefined,
              insurer: typeof r.insurer === "string" ? r.insurer : undefined,
              policy_num: typeof r.policy_num === "string" ? r.policy_num : undefined,
              policy_from: typeof r.policy_from === "string" ? r.policy_from : undefined,
              policy_to: typeof r.policy_to === "string" ? r.policy_to : undefined,
              premium: typeof r.premium === "string" ? r.premium : r.premium != null ? String(r.premium) : undefined,
            };
            return {
              ...current,
              profession: current.profession && current.profession.trim() !== "" ? current.profession : fromServer.profession,
              nominee_name: current.nominee_name && current.nominee_name.trim() !== "" ? current.nominee_name : fromServer.nominee_name,
              nominee_age: current.nominee_age && current.nominee_age.trim() !== "" ? current.nominee_age : fromServer.nominee_age,
              nominee_relationship:
                current.nominee_relationship && current.nominee_relationship.trim() !== ""
                  ? current.nominee_relationship
                  : fromServer.nominee_relationship,
              insurer: fromServer.insurer ?? current.insurer,
              policy_num: fromServer.policy_num ?? current.policy_num,
              policy_from: fromServer.policy_from ?? current.policy_from,
              policy_to: fromServer.policy_to ?? current.policy_to,
              premium: fromServer.premium ?? current.premium,
            };
          });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setExtractionError(msg);
      });
    return () => {
      cancelled = true;
    };
  }, [savedTo, hasVehicle, hasCustomer]);

  // Poll for extracted details when savedTo is set (e.g. right after upload)
  useEffect(() => {
    if (!savedTo) {
      pollCountRef.current = 0;
      setExtractionError(null);
      return;
    }
    pollCountRef.current = 0;
    setExtractionError(null);

    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (pollCountRef.current >= POLL_MAX) return;
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
        const ins = details?.insurance;
        if (ins && typeof ins === "object" && !Array.isArray(ins)) {
          setInsuranceReadByTextract(true);
          const r = ins as Record<string, unknown>;
          setExtractedInsurance((prev) => {
            const current = prev ?? {};
            const fromServer = {
              profession: typeof r.profession === "string" ? r.profession : undefined,
              nominee_name: typeof r.nominee_name === "string" ? r.nominee_name : undefined,
              nominee_age: r.nominee_age != null ? String(r.nominee_age) : undefined,
              nominee_relationship: typeof r.nominee_relationship === "string" ? r.nominee_relationship : undefined,
              insurer: typeof r.insurer === "string" ? r.insurer : undefined,
              policy_num: typeof r.policy_num === "string" ? r.policy_num : undefined,
              policy_from: typeof r.policy_from === "string" ? r.policy_from : undefined,
              policy_to: typeof r.policy_to === "string" ? r.policy_to : undefined,
              premium: typeof r.premium === "string" ? r.premium : r.premium != null ? String(r.premium) : undefined,
            };
            return {
              ...current,
              profession: current.profession && current.profession.trim() !== "" ? current.profession : fromServer.profession,
              nominee_name: current.nominee_name && current.nominee_name.trim() !== "" ? current.nominee_name : fromServer.nominee_name,
              nominee_age: current.nominee_age && current.nominee_age.trim() !== "" ? current.nominee_age : fromServer.nominee_age,
              nominee_relationship:
                current.nominee_relationship && current.nominee_relationship.trim() !== ""
                  ? current.nominee_relationship
                  : fromServer.nominee_relationship,
              insurer: fromServer.insurer ?? current.insurer,
              policy_num: fromServer.policy_num ?? current.policy_num,
              policy_from: fromServer.policy_from ?? current.policy_from,
              policy_to: fromServer.policy_to ?? current.policy_to,
              premium: fromServer.premium ?? current.premium,
            };
          });
        }
        if (normalized || extractionErr) {
          if (intervalId) clearInterval(intervalId);
          return;
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
  }, [savedTo]);

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
    setFillVahanStatus(null);
    setHasSubmittedInfo(false);
    setHasPrintedForms(false);
    setLastSubmittedCustomerId(null);
    setLastSubmittedVehicleId(null);
    setRtoApplicationId(null);
    setRtoPaymentDue(null);
    setFormResetKey((k) => k + 1);
  };

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

  const customerProcessing = Boolean(savedTo && !hasMeaningfulCustomer(c));
  const vehicleProcessing = Boolean(savedTo && !hasVehicleData(v ?? null));
  const insuranceProcessing = Boolean(savedTo && !hasMeaningfulInsurance(ins));
  /** Don't show errors until Textract/Tesseract have finished extracting all subsections */
  const extractionComplete = !customerProcessing && !vehicleProcessing && !insuranceProcessing;

  const handleFillForms = async () => {
    if (!savedTo || !dmsUrl) {
      setFillDmsStatus("Upload scans first.");
      return;
    }
    const c = extractedCustomer;
    const v = extractedVehicle;
    const vahanBase = getBaseUrl().replace(/\/$/, "") + "/dummy-vaahan";
    const rtoDealerId = "RTO" + String(dealerId);

    // 1) DMS section – independent process
    setIsFillDmsLoading(true);
    setFillDmsStatus(null);
    setFillVahanStatus(null);
    let dmsRes: Awaited<ReturnType<typeof fillDmsOnly>> | null = null;
    let hasAnyVehicle = false;
    try {
      dmsRes = await fillDmsOnly({
        subfolder: savedTo,
        dms_base_url: dmsUrl,
        dealer_id: dealerId,
        customer_id: lastSubmittedCustomerId ?? undefined,
        vehicle_id: lastSubmittedVehicleId ?? undefined,
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
      hasAnyVehicle = !!(scraped && typeof scraped === "object" && (
        (scraped.key_num && String(scraped.key_num).trim()) ||
        (scraped.frame_num && String(scraped.frame_num).trim()) ||
        (scraped.engine_num && String(scraped.engine_num).trim()) ||
        (scraped.model && String(scraped.model).trim()) ||
        (scraped.color && String(scraped.color).trim()) ||
        (scraped.cubic_capacity && String(scraped.cubic_capacity).trim()) ||
        (scraped.seating_capacity && String(scraped.seating_capacity).trim()) ||
        (scraped.body_type && String(scraped.body_type).trim()) ||
        (scraped.vehicle_type && String(scraped.vehicle_type).trim()) ||
        (scraped.num_cylinders && String(scraped.num_cylinders).trim()) ||
        (scraped.horse_power && String(scraped.horse_power).trim()) ||
        (scraped.total_amount && String(scraped.total_amount).trim()) ||
        (scraped.year_of_mfg && String(scraped.year_of_mfg).trim())
      ));
      if (hasAnyVehicle && scraped) {
        setDmsScrapedVehicle({
          key_no: scraped.key_num ?? undefined,
          frame_no: scraped.frame_num ?? undefined,
          engine_no: scraped.engine_num ?? undefined,
          model: scraped.model ?? undefined,
          color: scraped.color ?? undefined,
          cubic_capacity: scraped.cubic_capacity ?? undefined,
          seating_capacity: scraped.seating_capacity ?? undefined,
          body_type: scraped.body_type ?? undefined,
          vehicle_type: scraped.vehicle_type ?? undefined,
          num_cylinders: scraped.num_cylinders ?? undefined,
          horse_power: scraped.horse_power ?? undefined,
          total_amount: scraped.total_amount ?? undefined,
          year_of_mfg: scraped.year_of_mfg ?? undefined,
        });
      }
      const pdfs = dmsRes.pdfs_saved ?? [];
      const hasForm21 = pdfs.some((f) => /form\s*21|form21/i.test(f));
      const hasForm22 = pdfs.some((f) => /form\s*22|form22/i.test(f));
      const hasInvoiceDetails = pdfs.some((f) => /invoice_details|invoice\s*details/i.test(f));
      if (hasForm21 && hasForm22 && hasInvoiceDetails) setDmsPdfsDownloaded(true);
      if (!dmsRes.success) {
        setFillDmsStatus(dmsRes.error ?? "Fill DMS failed.");
      } else {
        setFillDmsStatus(null);
      }
    } catch (err) {
      if (isFillDmsAbortError(err)) {
        setFillDmsStatus("DMS request timed out. Check the upload folder for PDFs.");
      } else {
        setFillDmsStatus(err instanceof Error ? err.message : "Fill DMS failed.");
      }
    } finally {
      setIsFillDmsLoading(false);
    }

    // 2) Vahan (RTO) section – independent process; uses vehicle from DMS response or extracted
    const scrapedForVahan = dmsRes?.vehicle;
    const vehicleForVahan = (hasAnyVehicle && scrapedForVahan) ? {
      frame_no: scrapedForVahan.frame_num ?? scrapedForVahan.frame_no,
      model: scrapedForVahan.model,
      model_colour: scrapedForVahan.model,
      color: scrapedForVahan.color,
      year_of_mfg: scrapedForVahan.year_of_mfg,
      total_amount: scrapedForVahan.total_amount,
    } : v;
    const chassisNo = vehicleForVahan?.frame_no ?? v?.frame_no ?? "";
    const model = vehicleForVahan?.model ?? vehicleForVahan?.model_colour ?? v?.model_colour ?? "";
    const colour = vehicleForVahan?.color ?? "";
    const yearOfMfg = vehicleForVahan?.year_of_mfg ?? "";
    const totalAmount = vehicleForVahan?.total_amount ?? "";
    const totalCost = totalAmount ? parseFloat(String(totalAmount).replace(/,/g, "")) || 72000 : 72000;

    setIsFillVahanLoading(true);
    try {
      const vahanRes = await fillVahanOnly({
        vahan_base_url: vahanBase,
        rto_dealer_id: rtoDealerId,
        customer_name: c?.name ?? undefined,
        chassis_no: chassisNo || undefined,
        vehicle_model: model || undefined,
        vehicle_colour: colour || undefined,
        fuel_type: "Petrol",
        year_of_mfg: yearOfMfg || undefined,
        total_cost: totalCost,
      });
      if (vahanRes.application_id != null) setRtoApplicationId(vahanRes.application_id);
      if (vahanRes.rto_fees != null) setRtoPaymentDue(vahanRes.rto_fees);
      if (!vahanRes.success) {
        setFillVahanStatus(vahanRes.error ?? "Fill RTO failed.");
      } else {
        setFillVahanStatus(null);
        if (vahanRes.application_id && vahanRes.rto_fees != null && lastSubmittedCustomerId != null && lastSubmittedVehicleId != null) {
          const today = new Date();
          const dd = String(today.getDate()).padStart(2, "0");
          const mm = String(today.getMonth() + 1).padStart(2, "0");
          const yyyy = today.getFullYear();
          const registerDate = `${dd}-${mm}-${yyyy}`;
          try {
            await insertRtoPayment({
              application_id: vahanRes.application_id,
              customer_id: lastSubmittedCustomerId,
              vehicle_id: lastSubmittedVehicleId,
              dealer_id: dealerId,
              name: c?.name ?? undefined,
              mobile: mobile ?? undefined,
              chassis_num: chassisNo || undefined,
              register_date: registerDate,
              rto_fees: vahanRes.rto_fees,
              status: "Pending",
              rto_status: "Registered",
              subfolder: savedTo ?? undefined,
            });
          } catch (_) {
            setFillVahanStatus("RTO row saved but adding to Payments Pending list failed.");
          }
        }
      }
    } catch (err) {
      if (isFillDmsAbortError(err)) {
        setFillVahanStatus("RTO request timed out.");
      } else {
        setFillVahanStatus(err instanceof Error ? err.message : "Fill RTO failed.");
      }
    } finally {
      setIsFillVahanLoading(false);
    }

    // 3) Form 20 and Gate Pass – create and store at end of Fill Forms (even if user never clicks Create & print file)
    const scrapedForForm20 = scrapedForVahan ?? dmsRes?.vehicle;
    let vehicleDataForForm20: Record<string, unknown> = {};
    if (scrapedForForm20 && typeof scrapedForForm20 === "object") {
      const s = scrapedForForm20 as Record<string, unknown>;
      vehicleDataForForm20 = {
        key_no: s.key_num ?? s.key_no,
        frame_no: s.frame_num ?? s.frame_no,
        engine_no: s.engine_num ?? s.engine_no,
        model: s.model,
        color: s.color,
        cubic_capacity: s.cubic_capacity,
        seating_capacity: s.seating_capacity,
        body_type: s.body_type,
        vehicle_type: s.vehicle_type,
        num_cylinders: s.num_cylinders,
        horse_power: s.horse_power,
        total_amount: s.total_amount,
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
    }
  };

  const handlePrintForms = async () => {
    if (!savedTo) {
      setPrintFormsStatus("Upload scans first.");
      return;
    }
    const c = extractedCustomer;
    let vehicleData: Record<string, unknown> = {};
    if (dmsScrapedVehicle) {
      const s = dmsScrapedVehicle;
      vehicleData = {
        key_no: s.key_no,
        frame_no: s.frame_no,
        engine_no: s.engine_no,
        model: s.model,
        color: s.color,
        cubic_capacity: s.cubic_capacity,
        seating_capacity: s.seating_capacity,
        body_type: s.body_type,
        vehicle_type: s.vehicle_type,
        num_cylinders: s.num_cylinders,
        horse_power: s.horse_power,
        total_amount: s.total_amount,
        year_of_mfg: s.year_of_mfg,
      };
    } else {
      try {
        const fromDms = await getDataFromDms(savedTo, dealerId);
        if (fromDms?.vehicle && typeof fromDms.vehicle === "object") {
          vehicleData = fromDms.vehicle as Record<string, unknown>;
        }
      } catch {
        /* ignore */
      }
    }
    setIsPrintFormsLoading(true);
    setPrintFormsStatus(null);
    try {
      const res = await printForm20({
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
        vehicle: vehicleData,
        vehicle_id: lastSubmittedVehicleId ?? undefined,
        dealer_id: dealerId,
      });
      if (res.success) {
        setHasPrintedForms(true);
        setPrintFormsStatus(`Form 20 saved: ${(res.pdfs_saved ?? []).join(", ")}`);
      } else {
        setPrintFormsStatus(res.error ?? "Create & print file failed.");
      }
    } catch (err) {
      setPrintFormsStatus(err instanceof Error ? err.message : "Create & print file failed.");
    } finally {
      setIsPrintFormsLoading(false);
    }
  };

  const d = dmsScrapedVehicle;

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
        <div className="add-sales-v2-three-col">
          <section className="add-sales-v2-box add-sales-v2-box-upload">
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">1. Upload Customer Scans</h2>
                <button
                  type="button"
className="app-button app-button--primary"
                    onClick={handleNew}
                    title="Start a new entry"
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
                    disabled={
                      isSubmitting ||
                      !mobile ||
                      !c ||
                      !insuranceReadByTextract ||
                      !hasAllRequiredExtractedFields() ||
                      hasVehicleOrInsuranceValidationErrors ||
                      !!extractionError
                    }
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
                          profession: ins?.profession,
                          fileLocation: savedTo,
                          dealerId,
                        });
                        setSubmitStatus("Saved");
                        setHasSubmittedInfo(true);
                        if (submitRes?.customer_id != null) setLastSubmittedCustomerId(submitRes.customer_id);
                        if (submitRes?.vehicle_id != null) setLastSubmittedVehicleId(submitRes.vehicle_id);
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
                    <span className="add-sales-v2-status-text">{submitStatus}</span>
                  </div>
                )}
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Customer Details</h3>
                    {customerProcessing && !extractionError && <span className="add-sales-v2-processing">Processing</span>}
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
                        <dd>{display(c?.date_of_birth)}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Gender</dt>
                        <dd>{display(c?.gender)}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row">
                      <dt>Address</dt>
                      <dd>{buildDisplayAddress(c)}</dd>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Vehicle Details</h3>
                    {vehicleProcessing && <span className="add-sales-v2-processing">Processing</span>}
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
                    {(v?.model ?? v?.color ?? v?.cubic_capacity ?? v?.seating_capacity ?? v?.body_type ?? v?.vehicle_type ?? v?.num_cylinders ?? v?.horse_power ?? v?.total_amount ?? v?.year_of_mfg) && (
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
                          <dt>Horsepower</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.horse_power ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), horse_power: e.target.value }))}
                              placeholder="—"
                            />
                          </dd>
                        </div>
                        <div className="add-sales-v2-dl-row">
                          <dt>Total Amount</dt>
                          <dd>
                            <input
                              className="add-sales-v2-dl-input"
                              value={v?.total_amount ?? ""}
                              onChange={(e) => setExtractedVehicle((prev) => ({ ...(prev ?? {}), total_amount: e.target.value }))}
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
                    <h3 className="add-sales-v2-subsection-title">Insurance Details</h3>
                    {insuranceProcessing && <span className="add-sales-v2-processing">Processing</span>}
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

          <section className={`add-sales-v2-box add-sales-v2-box-fill-forms ${!savedTo || !hasSubmittedInfo ? "add-sales-v2-box--greyed" : ""}`}>
            <div className="add-sales-v2-box-title-row add-sales-v2-fill-forms-title-row">
              <h2 className="add-sales-v2-box-title">3. Fill Forms</h2>
              <button
                type="button"
                className="app-button app-button--primary"
                disabled={(isFillDmsLoading || isFillVahanLoading) || !hasSubmittedInfo}
                onClick={handleFillForms}
                title={!hasSubmittedInfo ? "Submit Info first (Section 2)" : undefined}
              >
                {isFillDmsLoading ? "DMS…" : isFillVahanLoading ? "RTO…" : "Fill Forms"}
              </button>
            </div>
            <div className="add-sales-v2-box-body">
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head">
                  <h3 className="add-sales-v2-subsection-title">A. DMS</h3>
                  {isFillDmsLoading && <span className="add-sales-v2-processing">Processing</span>}
                </div>
                {fillDmsStatus && (
                  <div className="app-panel-status" role="status">{fillDmsStatus}</div>
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
                        <dt>Frame no.</dt>
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
                        <dt>Cubic Cap.</dt>
                        <dd>{d?.cubic_capacity ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Seating Cap.</dt>
                        <dd>{d?.seating_capacity ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Body type</dt>
                        <dd>{d?.body_type ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Vehicle type</dt>
                        <dd>{d?.vehicle_type ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Num cylinders</dt>
                        <dd>{d?.num_cylinders ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Horsepower</dt>
                        <dd>{d?.horse_power ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Total amount</dt>
                        <dd>{d?.total_amount ?? "—"}</dd>
                      </div>
                    </div>
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Year of Mfg</dt>
                        <dd>{d?.year_of_mfg ?? "—"}</dd>
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
                </div>
                <div className="add-sales-v2-dms-fields">
                  <div className="add-sales-v2-dms-fields-title">Insurance details (from uploaded document)</div>
                  <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Insurance Provider</dt>
                        <dd>{ins?.insurer ?? "—"}</dd>
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
                <div className="add-sales-v2-print-forms-row">
                  <button
                    type="button"
                    className="app-button app-button--primary"
                    disabled={!savedTo || !hasSubmittedInfo || isPrintFormsLoading}
                    onClick={handlePrintForms}
                    title={!hasSubmittedInfo ? "Submit Info first (Section 2)" : undefined}
                  >
                    {isPrintFormsLoading ? "Generating…" : "Create & print file"}
                  </button>
                  {printFormsStatus && (
                    <div className="app-panel-status" role="status">{printFormsStatus}</div>
                  )}
                </div>
              </div>
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head">
                  <h3 className="add-sales-v2-subsection-title">C. RTO</h3>
                  {isFillVahanLoading && <span className="add-sales-v2-processing">Processing</span>}
                </div>
                {fillVahanStatus && (
                  <div className="app-panel-status" role="status">{fillVahanStatus}</div>
                )}
                <div className="add-sales-v2-dms-fields">
                  <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Application ID</dt>
                        <dd>{rtoApplicationId ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>RTO Fees</dt>
                        <dd>{rtoPaymentDue != null ? `₹${rtoPaymentDue}` : "—"}</dd>
                      </div>
                    </div>
                  </dl>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
