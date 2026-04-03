import { apiFetch } from "./client";
import type { ExtractedCustomerDetails, ExtractedVehicleDetails, ExtractedInsuranceDetails } from "../types";
import { financierForStagingPayload } from "../utils/financierStagingRules";
import {
  sanitizeNomineeAgeInput,
  sanitizeOptionalFormField,
} from "../utils/formFieldSanitize";

export interface SubmitInfoPayload {
  customer: {
    aadhar_id?: string;
    name?: string;
    gender?: string;
    date_of_birth?: string;
    address?: string;
    pin?: string;
    city?: string;
    state?: string;
    mobile_number: string;
    alt_phone_num?: string;
    profession?: string;
    financier?: string;
    marital_status?: string;
    /** Aadhaar QR care-of (father/husband); stored as customer_master.care_of */
    care_of?: string;
    dms_relation_prefix?: string;
    dms_contact_path?: string;
    file_location?: string | null;
  };
  vehicle: {
    frame_no?: string;
    engine_no?: string;
    key_no?: string;
    battery_no?: string;
  };
  insurance: {
    nominee_name?: string;
    nominee_age?: string;
    nominee_relationship?: string;
    nominee_gender?: string;
    insurer?: string;
    policy_num?: string;
    policy_from?: string;
    policy_to?: string;
    premium?: string;
  };
  dealer_id: number | null;
  file_location?: string | null;
  /** Resubmit: update this draft staging row when dealer matches. */
  staging_id?: string | null;
}

export interface SubmitInfoResponse {
  ok: boolean;
  staging_id: string;
}

function mapCustomer(
  c: ExtractedCustomerDetails | null,
  mobile: string,
  insurance?: ExtractedInsuranceDetails | null,
  fileLocation?: string | null,
  oemId?: number | null
): SubmitInfoPayload["customer"] {
  return {
    aadhar_id: sanitizeOptionalFormField(c?.aadhar_id),
    name: sanitizeOptionalFormField(c?.name),
    gender: sanitizeOptionalFormField(c?.gender),
    date_of_birth: sanitizeOptionalFormField(c?.date_of_birth),
    address: sanitizeOptionalFormField(c?.address),
    pin: sanitizeOptionalFormField(c?.pin_code),
    city: sanitizeOptionalFormField(c?.city),
    state: sanitizeOptionalFormField(c?.state),
    mobile_number: String(mobile ?? "").replace(/\D/g, "").slice(0, 10),
    alt_phone_num: sanitizeOptionalFormField(c?.alt_phone_num),
    profession: sanitizeOptionalFormField(insurance?.profession),
    financier: financierForStagingPayload(oemId, sanitizeOptionalFormField(insurance?.financier)),
    marital_status: sanitizeOptionalFormField(insurance?.marital_status),
    care_of: sanitizeOptionalFormField(c?.care_of),
    dms_relation_prefix: sanitizeOptionalFormField(c?.dms_relation_prefix),
    dms_contact_path: sanitizeOptionalFormField(c?.dms_contact_path),
    file_location: fileLocation ?? undefined,
  };
}

function mapVehicle(v: ExtractedVehicleDetails | null): SubmitInfoPayload["vehicle"] {
  return {
    frame_no: sanitizeOptionalFormField(v?.frame_no),
    engine_no: sanitizeOptionalFormField(v?.engine_no),
    key_no: sanitizeOptionalFormField(v?.key_no),
    battery_no: sanitizeOptionalFormField(v?.battery_no),
  };
}

function mapInsurance(ins: ExtractedInsuranceDetails | null): SubmitInfoPayload["insurance"] {
  return {
    nominee_name: sanitizeOptionalFormField(ins?.nominee_name),
    nominee_age:
      ins?.nominee_age != null && String(ins.nominee_age).trim() !== ""
        ? sanitizeNomineeAgeInput(String(ins.nominee_age))
        : undefined,
    nominee_relationship: sanitizeOptionalFormField(ins?.nominee_relationship),
    nominee_gender: sanitizeOptionalFormField(ins?.nominee_gender),
    insurer: sanitizeOptionalFormField(ins?.insurer),
    policy_num: sanitizeOptionalFormField(ins?.policy_num),
    policy_from: sanitizeOptionalFormField(ins?.policy_from),
    policy_to: sanitizeOptionalFormField(ins?.policy_to),
    premium: sanitizeOptionalFormField(ins?.premium),
  };
}

export async function submitInfo(
  opts: {
    customer: ExtractedCustomerDetails | null;
    vehicle: ExtractedVehicleDetails | null;
    insurance: ExtractedInsuranceDetails | null;
    mobile: string;
    fileLocation: string | null;
    dealerId: number | null;
    /** From GET /dealers/:id — used for Hero/Bajaj→Hinduja staging rule. */
    oemId?: number | null;
    stagingId?: string | null;
  }
): Promise<SubmitInfoResponse> {
  const payload: SubmitInfoPayload = {
    customer: mapCustomer(opts.customer, opts.mobile, opts.insurance, opts.fileLocation, opts.oemId),
    vehicle: mapVehicle(opts.vehicle),
    insurance: mapInsurance(opts.insurance),
    dealer_id: opts.dealerId,
    file_location: opts.fileLocation ?? undefined,
    staging_id: opts.stagingId ?? undefined,
  };
  return apiFetch<SubmitInfoResponse>("/submit-info", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

