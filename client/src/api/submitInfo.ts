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
    profession?: string;
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
  };
  dealer_id: number | null;
  file_location?: string | null;
}

export interface SubmitInfoResponse {
  ok: boolean;
  customer_id: number;
  vehicle_id: number;
}

function mapCustomer(
  c: ExtractedCustomerDetails | null,
  mobile: string,
  profession?: string,
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
    profession,
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
  };
}

export async function submitInfo(
  opts: {
    customer: ExtractedCustomerDetails | null;
    vehicle: ExtractedVehicleDetails | null;
    insurance: ExtractedInsuranceDetails | null;
    mobile: string;
    profession?: string;
    fileLocation: string | null;
    dealerId: number | null;
  }
): Promise<SubmitInfoResponse> {
  const payload: SubmitInfoPayload = {
    customer: mapCustomer(opts.customer, opts.mobile, opts.profession, opts.fileLocation),
    vehicle: mapVehicle(opts.vehicle),
    insurance: mapInsurance(opts.insurance),
    dealer_id: opts.dealerId,
    file_location: opts.fileLocation ?? undefined,
  };
  return apiFetch<SubmitInfoResponse>("/submit-info", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

