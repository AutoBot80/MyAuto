import { useState, useEffect, useRef } from "react";
import type { AddSalesStep, ExtractedVehicleDetails, ExtractedCustomerDetails, ExtractedInsuranceDetails } from "../types";
import { buildDisplayAddress } from "../types";
import { useUploadScans } from "../hooks/useUploadScans";
import { UploadScansPanel } from "../components/UploadScansPanel";
import { getExtractedDetails } from "../api/aiReaderQueue";
import { submitInfo } from "../api/submitInfo";
import { fillDms } from "../api/fillDms";
import { loadAddSalesForm, saveAddSalesForm, clearAddSalesForm } from "../utils/addSalesStorage";
import { normalizeVehicleDetails, hasVehicleData } from "../utils/vehicleDetails";

const ADD_SALES_STEPS: { id: AddSalesStep; label: string; num: number }[] = [
  { id: "upload-scans", label: "Upload scans", num: 1 },
  { id: "hero-dms", label: "DMS", num: 2 },
  { id: "insurance", label: "Insurance", num: 3 },
  { id: "rto", label: "RTO", num: 4 },
];

const ADD_SALES_NAV = [
  { ...ADD_SALES_STEPS[0], label: "Customer Info." },
  ...ADD_SALES_STEPS.slice(1),
];

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
  const [addSalesStep, setAddSalesStep] = useState<AddSalesStep>("upload-scans");
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
    setFormResetKey((k) => k + 1);
  };

  // Normalize for display so we always show known fields (frame_no, engine_no, etc.) regardless of key naming
  const v = normalizeVehicleDetails(extractedVehicle) ?? extractedVehicle;
  const c = extractedCustomer;
  const ins = extractedInsurance;
  const display = (s: string | undefined) => (s && String(s).trim() ? String(s).trim() : "—");

  const hasAllRequiredExtractedFields = () => {
    if (!c) return false;
    const requiredCustomer = [c.aadhar_id, c.name, c.gender, c.date_of_birth, buildDisplayAddress(c)];
    const requiredVehicle = [v?.frame_no, v?.engine_no, v?.key_no, v?.battery_no];
    const requiredInsurance = [ins?.profession, ins?.nominee_name, ins?.nominee_age, ins?.nominee_relationship];
    const all = [...requiredCustomer, ...requiredVehicle, ...requiredInsurance];
    return all.every((val) => val != null && String(val).trim() !== "" && String(val).trim() !== "—");
  };

  const hasMeaningfulCustomer = (cust: typeof c) =>
    cust && (cust.aadhar_id || cust.name || cust.address || buildDisplayAddress(cust) !== "—");
  const hasMeaningfulInsurance = (i: typeof ins) =>
    i && [i.profession, i.nominee_name, i.nominee_age, i.nominee_relationship].some(
      (x) => x != null && String(x).trim() !== ""
    );

  const customerProcessing = Boolean(savedTo && !hasMeaningfulCustomer(c));
  const vehicleProcessing = Boolean(savedTo && !hasVehicleData(v ?? null));
  const insuranceProcessing = Boolean(savedTo && !hasMeaningfulInsurance(ins));

  const panel = (
    <UploadScansPanel
      key={formResetKey}
      addSalesStep={addSalesStep}
      onStepChange={setAddSalesStep}
      isUploading={isUploading}
      onUpload={upload}
      uploadStatus={uploadStatus}
      uploadedFiles={uploadedFiles}
      mobile={mobile}
      isMobileValid={isMobileValid}
      onUploadV2={uploadV2}
      onFillDms={async () => {
        if (!savedTo || !dmsUrl) {
          setFillDmsStatus("Upload scans and open DMS first.");
          return;
        }
        const c = extractedCustomer;
        const v = extractedVehicle;
        setIsFillDmsLoading(true);
        setFillDmsStatus(null);
        try {
          const res = await fillDms({
            subfolder: savedTo,
            dms_base_url: dmsUrl,
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
          if (res.success && res.vehicle) {
            setExtractedVehicle((prev) => ({
              ...(prev ?? {}),
              key_no: res.vehicle.key_num ?? prev?.key_no,
              frame_no: res.vehicle.frame_num ?? prev?.frame_no,
              engine_no: res.vehicle.engine_num ?? prev?.engine_no,
              model: res.vehicle.model ?? prev?.model,
              color: res.vehicle.color ?? prev?.color,
              cubic_capacity: res.vehicle.cubic_capacity ?? prev?.cubic_capacity,
              total_amount: res.vehicle.total_amount ?? prev?.total_amount,
              year_of_mfg: res.vehicle.year_of_mfg ?? prev?.year_of_mfg,
            }));
            const pdfMsg = res.pdfs_saved?.length ? ` PDFs saved: ${res.pdfs_saved.join(", ")}.` : "";
            setFillDmsStatus(`DMS filled. Vehicle details updated.${pdfMsg}`);
          } else {
            setFillDmsStatus(res.error ?? "Fill DMS failed.");
          }
        } catch (err) {
          setFillDmsStatus(err instanceof Error ? err.message : "Fill DMS failed.");
        } finally {
          setIsFillDmsLoading(false);
        }
      }}
      fillDmsStatus={fillDmsStatus}
      isFillDmsLoading={isFillDmsLoading}
    />
  );

  return (
    <div className="add-sales-v2">
      <nav className="add-sales-v2-nav" aria-label="Add sales steps">
        {ADD_SALES_NAV.map(({ id, label, num }) => (
            <button
              key={id}
              type="button"
              className={`add-sales-v2-nav-item ${addSalesStep === id ? "active" : ""}`}
              onClick={() => {
                if (id === "rto") {
                  openVahanInNewTab?.();
                  setAddSalesStep(id);
                } else {
                  setAddSalesStep(id);
                }
              }}
            >
              <span className="add-sales-v2-nav-num">{num}</span>
              <span className="add-sales-v2-nav-label">{label}</span>
            </button>
          ))}
        </nav>
        <main className="add-sales-v2-main">
          <div className="add-sales-v2-two-col">
            <section className="add-sales-v2-box add-sales-v2-box-upload">
              {addSalesStep !== "hero-dms" && (
                <div className="add-sales-v2-box-title-row">
                  <h2 className="add-sales-v2-box-title">Upload Customer Information</h2>
                  <button
                    type="button"
                    className="app-button"
                    onClick={handleNew}
                    title="Start a new entry"
                  >
                    New
                  </button>
                </div>
              )}
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
            <section className="add-sales-v2-box add-sales-v2-box-extracted">
              <div className="add-sales-v2-box-title-row">
                <h2 className="add-sales-v2-box-title">Extracted Information <span className="add-sales-v2-box-title-note">(AI enabled)</span></h2>
                {addSalesStep !== "hero-dms" && (
                  <button
                    type="button"
                    className="app-button add-sales-v2-submit-btn"
                    disabled={isSubmitting || !mobile || !c}
                    onClick={async () => {
                      if (!mobile || !c) return;
                      if (!hasAllRequiredExtractedFields()) {
                        setSubmitStatus("Please fill all extracted fields before submitting.");
                        return;
                      }
                      setIsSubmitting(true);
                      setSubmitStatus(null);
                      try {
                        await submitInfo({
                          customer: c,
                          vehicle: v ?? null,
                          insurance: ins ?? null,
                          mobile,
                          profession: ins?.profession,
                          fileLocation: savedTo,
                          dealerId,
                        });
                        setSubmitStatus("Saved");
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
                )}
              </div>
              <div className="add-sales-v2-box-body">
                {submitStatus && (
                  <div className="add-sales-v2-status-row">
                    <span className="add-sales-v2-status-text">{submitStatus}</span>
                  </div>
                )}
                <div className="add-sales-v2-subsection">
                  <div className="add-sales-v2-subsection-head">
                    <h3 className="add-sales-v2-subsection-title">Customer Details</h3>
                    {customerProcessing && <span className="add-sales-v2-processing">Processing</span>}
                  </div>
                  <dl className="add-sales-v2-dl">
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
                          <dt>Cubic capacity</dt>
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
          </div>
        </main>
      </div>
  );
}
