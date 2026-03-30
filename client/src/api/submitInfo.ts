import { apiFetch } from "./client";
import type { ExtractedCustomerDetails, ExtractedVehicleDetails, ExtractedInsuranceDetails } from "../types";

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
  fileLocation?: string | null
): SubmitInfoPayload["customer"] {
  return {
    aadhar_id: c?.aadhar_id,
    name: c?.name,
    gender: c?.gender,
    date_of_birth: c?.date_of_birth,
    address: c?.address,
    pin: c?.pin_code,
    city: c?.city,
    state: c?.state,
    mobile_number: mobile,
    alt_phone_num: c?.alt_phone_num,
    profession: insurance?.profession,
    financier: insurance?.financier,
    marital_status: insurance?.marital_status,
    care_of: c?.care_of,
    dms_relation_prefix: c?.dms_relation_prefix,
    dms_contact_path: c?.dms_contact_path,
    file_location: fileLocation ?? undefined,
  };
}

function mapVehicle(v: ExtractedVehicleDetails | null): SubmitInfoPayload["vehicle"] {
  return {
    frame_no: v?.frame_no,
    engine_no: v?.engine_no,
    key_no: v?.key_no,
    battery_no: v?.battery_no,
  };
}

function mapInsurance(ins: ExtractedInsuranceDetails | null): SubmitInfoPayload["insurance"] {
  return {
    nominee_name: ins?.nominee_name,
    nominee_age: ins?.nominee_age,
    nominee_relationship: ins?.nominee_relationship,
    nominee_gender: ins?.nominee_gender,
    insurer: ins?.insurer,
    policy_num: ins?.policy_num,
    policy_from: ins?.policy_from,
    policy_to: ins?.policy_to,
    premium: ins?.premium,
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
    stagingId?: string | null;
  }
): Promise<SubmitInfoResponse> {
  const payload: SubmitInfoPayload = {
    customer: mapCustomer(opts.customer, opts.mobile, opts.insurance, opts.fileLocation),
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

