import { useState, useEffect, useRef } from "react";
import type { ExtractedVehicleDetails, ExtractedCustomerDetails, ExtractedInsuranceDetails } from "../types";
import { buildDisplayAddress } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { submitInfo } from "../api/submitInfo";
import { fillDms, isFillDmsAbortError } from "../api/fillDms";
import { insertRtoPayment } from "../api/rtoPaymentDetails";
import { getBaseUrl } from "../api/client";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
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
  const [isFillDmsLoading, setIsFillDmsLoading] = useState(false);
  /** DMS-scraped vehicle; shown in Fill Forms > DMS, does not change Extracted Information. */
  const [dmsScrapedVehicle, setDmsScrapedVehicle] = useState<ExtractedVehicleDetails | null>(null);
  /** True when Form 21 and Form 22 PDFs have been downloaded from DMS. */
  const [dmsPdfsDownloaded, setDmsPdfsDownloaded] = useState(false);
  /** True after user has successfully pressed Submit Info. (Section 3 stays greyed until then.) */
  const [hasSubmittedInfo, setHasSubmittedInfo] = useState(false);
  /** True after user has used Print forms. (Used for beforeunload warning.) */
  const [hasPrintedForms, setHasPrintedForms] = useState(false);
  /** From last successful Submit Info; used when inserting RTO payment row after Fill Forms. */
  const [lastSubmittedCustomerId, setLastSubmittedCustomerId] = useState<number | null>(null);
  const [lastSubmittedVehicleId, setLastSubmittedVehicleId] = useState<number | null>(null);
  /** From Fill Forms (Vahan step); shown under C. RTO. */
  const [rtoApplicationId, setRtoApplicationId] = useState<string | null>(null);
  const [rtoPaymentDue, setRtoPaymentDue] = useState<number | null>(null);
  /** True once Textract has returned insurance data for this upload (details sheet processed). */
  const [insuranceReadByTextract, setInsuranceReadByTextract] = useState(() => {
    const stored = loadAddSalesForm().extractedInsurance;
    return Boolean(
      stored &&
        [stored.profession, stored.nominee_name, stored.nominee_age, stored.nominee_relationship].some(
          (x) => x != null && String(x).trim() !== ""
        )
    );
  });
  const [formResetKey, setFormResetKey] = useState(0);

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
  });

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
      extractedVehicle,
      extractedCustomer,
      extractedInsurance,
    });
  }, [mobile, savedTo, uploadedFiles, uploadStatus, extractedVehicle, extractedCustomer, extractedInsurance]);

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
    getExtractedDetails(savedTo)
      .then((details) => {
        if (cancelled) return;
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
            };
          });
        }
      })
      .catch(() => {
        // Fetch failed (404, network, etc.) – keep existing state
      });
    return () => {
      cancelled = true;
    };
  }, [savedTo, hasVehicle, hasCustomer]);

  // Poll for extracted details when savedTo is set (e.g. right after upload)
  useEffect(() => {
    if (!savedTo) {
      pollCountRef.current = 0;
      return;
    }
    pollCountRef.current = 0;

    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (pollCountRef.current >= POLL_MAX) return;
      pollCountRef.current += 1;
      try {
        const details = await getExtractedDetails(savedTo);
        const rawVehicle = details?.vehicle ?? details;
        const normalized = normalizeVehicleDetails(rawVehicle);
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
            };
          });
        }
        if (normalized) {
          setExtractedVehicle(normalized);
          if (intervalId) clearInterval(intervalId);
          return;
        }
      } catch {
        // keep polling
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
      <label className="app-field">
        <div className="app-field-label">Customer Mobile (10 digits)</div>
        <input
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
    setInsuranceReadByTextract(false);
    setDmsScrapedVehicle(null);
    setDmsPdfsDownloaded(false);
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
  const getInsuranceValidationErrors = (): string[] => {
    const errors: string[] = [];
    insuranceValidationFields.forEach(({ label, value }) => {
      if (isBlank(value)) errors.push(`${label} is required`);
      else if (hasDisallowedSpecialChars(value)) errors.push(`${label} must not contain special characters`);
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
    i && [i.profession, i.nominee_name, i.nominee_age, i.nominee_relationship].some(
      (x) => x != null && String(x).trim() !== ""
    );

  const customerProcessing = Boolean(savedTo && !hasMeaningfulCustomer(c));
  const vehicleProcessing = Boolean(savedTo && !hasVehicleData(v ?? null));
  const insuranceProcessing = Boolean(savedTo && !hasMeaningfulInsurance(ins));

  const handleFillDms = async () => {
    if (!savedTo || !dmsUrl) {
      setFillDmsStatus("Upload scans first.");
      return;
    }
    const c = extractedCustomer;
    const v = extractedVehicle;
    setIsFillDmsLoading(true);
    setFillDmsStatus(null);
    const vahanBase = getBaseUrl().replace(/\/$/, "") + "/dummy-vaahan";
    const rtoDealerId = "RTO" + String(dealerId);
    try {
      const res = await fillDms({
        subfolder: savedTo,
        dms_base_url: dmsUrl,
        vahan_base_url: vahanBase,
        rto_dealer_id: rtoDealerId,
        customer: {
          name: c?.name ?? undefined,
          address: c?.address ?? buildDisplayAddress(c),
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
      const scraped = res.vehicle && (res.vehicle.key_num ?? res.vehicle.frame_num ?? res.vehicle.engine_num ?? res.vehicle.model ?? res.vehicle.color ?? res.vehicle.cubic_capacity ?? res.vehicle.total_amount ?? res.vehicle.year_of_mfg);
      if (scraped) {
        setDmsScrapedVehicle({
          key_no: res.vehicle!.key_num ?? undefined,
          frame_no: res.vehicle!.frame_num ?? undefined,
          engine_no: res.vehicle!.engine_num ?? undefined,
          model: res.vehicle!.model ?? undefined,
          color: res.vehicle!.color ?? undefined,
          cubic_capacity: res.vehicle!.cubic_capacity ?? undefined,
          total_amount: res.vehicle!.total_amount ?? undefined,
          year_of_mfg: res.vehicle!.year_of_mfg ?? undefined,
        });
      }
      if (res.application_id != null) setRtoApplicationId(res.application_id);
      if (res.rto_fees != null) setRtoPaymentDue(res.rto_fees);
      if (res.success) {
        const pdfs = res.pdfs_saved ?? [];
        const hasForm21 = pdfs.some((f) => /form\s*21|form21/i.test(f));
        const hasForm22 = pdfs.some((f) => /form\s*22|form22/i.test(f));
        if (hasForm21 && hasForm22) setDmsPdfsDownloaded(true);
        setFillDmsStatus(null);
        if (res.application_id && res.rto_fees != null && lastSubmittedCustomerId != null) {
          const today = new Date();
          const dd = String(today.getDate()).padStart(2, "0");
          const mm = String(today.getMonth() + 1).padStart(2, "0");
          const yyyy = today.getFullYear();
          const submissionDate = `${dd}-${mm}-${yyyy}`;
          try {
            await insertRtoPayment({
              customer_id: lastSubmittedCustomerId,
              name: c?.name ?? undefined,
              mobile: mobile ?? undefined,
              chassis_num: res.vehicle?.frame_num ?? v?.frame_no ?? undefined,
              application_num: res.application_id,
              submission_date: submissionDate,
              rto_payment_due: res.rto_fees,
              status: "Pending",
            });
          } catch (_) {
            setFillDmsStatus("RTO row saved but adding to Payments Pending list failed.");
          }
        }
      } else {
        setFillDmsStatus(res.error ?? "Fill DMS failed.");
      }
    } catch (err) {
      if (isFillDmsAbortError(err)) {
        setFillDmsStatus(
          "Request timed out. The DMS fill may have completed on the server—check the upload folder for Forms 21 and 22."
        );
      } else {
        setFillDmsStatus(err instanceof Error ? err.message : "Fill DMS failed.");
      }
    } finally {
      setIsFillDmsLoading(false);
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
                      hasVehicleOrInsuranceValidationErrors
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
                {savedTo && !insuranceReadByTextract && (
                  <div className="add-sales-v2-status-row add-sales-v2-status-row--error" role="alert">
                    <span className="add-sales-v2-status-text">Waiting for insurance details from document.</span>
                  </div>
                )}
                {savedTo && insuranceReadByTextract && getMissingRequiredFields().length > 0 && (
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
                    {customerProcessing && <span className="add-sales-v2-processing">Processing</span>}
                  </div>
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
                  {vehicleValidationErrors.length > 0 && (
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
                    {(v?.model ?? v?.color ?? v?.cubic_capacity ?? v?.total_amount ?? v?.year_of_mfg) && (
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
                  {insuranceValidationErrors.length > 0 && (
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
                          value={ins?.nominee_age ?? ""}
                          onChange={(e) => setExtractedInsurance((prev) => ({ ...(prev ?? {}), nominee_age: e.target.value }))}
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

          <section className={`add-sales-v2-box add-sales-v2-box-fill-forms ${!savedTo || !hasSubmittedInfo ? "add-sales-v2-box--greyed" : ""}`}>
            <div className="add-sales-v2-box-title-row add-sales-v2-fill-forms-title-row">
              <h2 className="add-sales-v2-box-title">3. Fill Forms</h2>
              <button
                type="button"
                className="app-button app-button--primary"
                disabled={isFillDmsLoading}
                onClick={handleFillDms}
              >
                {isFillDmsLoading ? "Filling…" : "Fill Forms"}
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
                        <dt>Total amount</dt>
                        <dd>{d?.total_amount ?? "—"}</dd>
                      </div>
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
                  </ul>
                  {dmsPdfsDownloaded && (
                    <p className="add-sales-v2-dms-pdfs-msg">Forms 21 and 22 downloaded.</p>
                  )}
                </div>
              </div>
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head">
                  <h3 className="add-sales-v2-subsection-title">B. Insurance</h3>
                </div>
                <div className="add-sales-v2-dms-fields">
                  <div className="add-sales-v2-dms-fields-title">Get Insurance details</div>
                  <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Policy No.</dt>
                        <dd>—</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>Gross Premium</dt>
                        <dd>—</dd>
                      </div>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-dms-pdfs">
                  <div className="add-sales-v2-dms-pdfs-title">Get PDF</div>
                  <ul className="add-sales-v2-dms-pdfs-list">
                    <li>Insurance Policy</li>
                  </ul>
                </div>
              </div>
              <div className="add-sales-v2-fill-forms-subsection">
                <div className="add-sales-v2-subsection-head">
                  <h3 className="add-sales-v2-subsection-title">C. RTO</h3>
                  {isFillDmsLoading && <span className="add-sales-v2-processing">Processing</span>}
                </div>
                <div className="add-sales-v2-dms-fields">
                  <dl className="add-sales-v2-dl add-sales-v2-dl--dms">
                    <div className="add-sales-v2-dl-row-group">
                      <div className="add-sales-v2-dl-row">
                        <dt>Application ID</dt>
                        <dd>{rtoApplicationId ?? "—"}</dd>
                      </div>
                      <div className="add-sales-v2-dl-row">
                        <dt>RTO Payment Due</dt>
                        <dd>{rtoPaymentDue != null ? `₹${rtoPaymentDue}` : "—"}</dd>
                      </div>
                    </div>
                  </dl>
                </div>
                <div className="add-sales-v2-rto-actions">
                  <button
                    type="button"
                    className="app-button"
                    disabled
                    title="Coming soon"
                  >
                    Print forms
                  </button>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
