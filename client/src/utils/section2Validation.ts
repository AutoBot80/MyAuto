/**
 * Add Sales Section 2 validation — run on Submit Info only (not while typing).
 */

import type {
  ExtractedCustomerDetails,
  ExtractedInsuranceDetails,
  ExtractedVehicleDetails,
} from "../types";
import {
  buildAddressLine1,
  buildSection2FullAddress,
  CARE_OF_RELATION_PREFIX_ERROR,
  careOfHasRecognizedRelationMarker,
  isValidDdMmYyyy,
  validateFreeformAddressLine,
} from "./section2CustomerFormat";

export type Section2FieldError = { field: string; message: string };

const ALLOWED_CHAR_REGEX = /^[a-zA-Z0-9\s\-./,]*$/;

const CANONICAL_GENDERS = ["Male", "Female", "Transgender"] as const;

const GENDER_ALIASES: Record<string, (typeof CANONICAL_GENDERS)[number]> = {
  m: "Male",
  male: "Male",
  f: "Female",
  female: "Female",
  t: "Transgender",
  transgender: "Transgender",
};

export function normalizeGenderForValidation(raw: string | undefined | null): string | null {
  const t = (raw ?? "").trim();
  if (!t) return null;
  const alias = GENDER_ALIASES[t.toLowerCase()];
  if (alias) return alias;
  if ((CANONICAL_GENDERS as readonly string[]).includes(t)) return t;
  return null;
}

function isBlank(val: string | undefined | null): boolean {
  return val == null || String(val).trim() === "" || String(val).trim() === "—";
}

function hasDisallowedSpecialChars(val: string | undefined | null): boolean {
  return val != null && String(val).trim() !== "" && !ALLOWED_CHAR_REGEX.test(String(val).trim());
}

function isValidNomineeAgeVal(val: string | undefined | null): boolean {
  if (val == null || String(val).trim() === "") return true;
  const s = String(val).trim();
  if (!/^\d+$/.test(s)) return false;
  const n = parseInt(s, 10);
  return n >= 1 && n <= 150;
}

/** Row-2 city / state / PIN checks after parse commit. */
export function getAddressLine2ValidationErrors(
  c: ExtractedCustomerDetails | null | undefined,
  rawLine2: string
): Section2FieldError[] {
  const l1 = buildAddressLine1(c);
  const raw = (rawLine2 ?? "").trim();
  if (!l1 && !raw) return [];

  const formatErr = validateFreeformAddressLine(rawLine2, { minCommaSegments: 2 });
  if (formatErr) {
    return [{ field: "address_line2", message: formatErr }];
  }
  return [];
}

export type GetSection2ValidationErrorsOpts = {
  savedTo: string | null;
  mobile: string;
  customer: ExtractedCustomerDetails | null | undefined;
  vehicle: ExtractedVehicleDetails | null | undefined;
  insurance: ExtractedInsuranceDetails | null | undefined;
  addressLine2Input: string;
  masterRefFinanciers: readonly string[];
  includeInsuranceFields: boolean;
};

export function getSection2ValidationErrors(opts: GetSection2ValidationErrorsOpts): Section2FieldError[] {
  if (!opts.savedTo || String(opts.savedTo).trim() === "") {
    return [];
  }

  const c = opts.customer;
  const v = opts.vehicle;
  const ins = opts.insurance;
  const m = new Map<string, string>();
  const setErr = (field: string, message: string) => {
    if (!m.has(field)) m.set(field, message);
  };

  if (!/^\d{10}$/.test(opts.mobile.trim())) {
    setErr("customer_mobile", "Enter exactly 10 digits.");
  }
  if (!/^\d{10}$/.test((c?.alt_phone_num ?? "").trim())) {
    setErr("alternate_no", "Enter exactly 10 digits.");
  }
  if (!isValidDdMmYyyy(c?.date_of_birth)) {
    setErr("dob", "Enter a valid date in DD/MM/YYYY format.");
  }
  const careOfTrimmed = (c?.care_of ?? "").trim();
  if (!careOfTrimmed) {
    setErr("care_of", "C/O is required.");
  } else if (!careOfHasRecognizedRelationMarker(careOfTrimmed)) {
    setErr("care_of", CARE_OF_RELATION_PREFIX_ERROR);
  }
  if (!/^\d{4}$/.test((c?.aadhar_id ?? "").trim())) {
    setErr("aadhar", "Enter the last 4 digits of Aadhaar.");
  }

  const genderNorm = normalizeGenderForValidation(c?.gender);
  if (!genderNorm) {
    if (!(c?.gender ?? "").trim()) {
      setErr("gender", "Gender is required.");
    } else {
      setErr("gender", "Enter Male, Female, or Transgender.");
    }
  }

  if (!buildAddressLine1(c).trim()) {
    setErr("address", "Street / locality is required.");
  }
  for (const e of getAddressLine2ValidationErrors(c, opts.addressLine2Input)) {
    setErr(e.field, e.message);
  }

  const financier = (ins?.financier ?? "").trim();
  if (financier && opts.masterRefFinanciers.length > 0 && !opts.masterRefFinanciers.includes(financier)) {
    setErr("financier", "Select a financier from the list.");
  }

  const customerTextFields: { field: string; label: string; value: string | undefined }[] = [
    { field: "name", label: "Name", value: c?.name },
    { field: "care_of", label: "C/O", value: c?.care_of },
  ];
  for (const { field, label, value } of customerTextFields) {
    if (!isBlank(value) && hasDisallowedSpecialChars(value)) {
      setErr(field, `${label} must not contain special characters.`);
    }
  }
  const line1 = buildAddressLine1(c);
  if (!isBlank(line1) && hasDisallowedSpecialChars(line1)) {
    setErr("address", "Address must not contain special characters.");
  }
  if (!isBlank(opts.addressLine2Input) && hasDisallowedSpecialChars(opts.addressLine2Input)) {
    setErr("address_line2", "City, State, and PIN must not contain special characters.");
  }

  const requiredEmpty: { field: string; label: string; value: string | undefined }[] = [
    { field: "name", label: "Name", value: c?.name },
    { field: "key_no", label: "Key no.", value: v?.key_no },
    { field: "chassis_no", label: "Chassis No.", value: v?.frame_no },
    { field: "engine_no", label: "Engine no.", value: v?.engine_no },
    { field: "battery_no", label: "Battery no.", value: v?.battery_no },
  ];

  if (opts.includeInsuranceFields) {
    requiredEmpty.push(
      { field: "profession", label: "Customer Profession", value: ins?.profession },
      { field: "marital_status", label: "Customer Marital Status", value: ins?.marital_status },
      { field: "nominee_name", label: "Nominee Name", value: ins?.nominee_name },
      { field: "nominee_age", label: "Nominee Age", value: ins?.nominee_age },
      { field: "nominee_relationship", label: "Relationship", value: ins?.nominee_relationship },
      { field: "nominee_gender", label: "Nominee Gender", value: ins?.nominee_gender }
    );
  }

  if (!buildSection2FullAddress(c) && !opts.addressLine2Input.trim() && !buildAddressLine1(c).trim()) {
    setErr("address", "Address is required.");
  }

  for (const { field, label, value } of requiredEmpty) {
    if (value == null || String(value).trim() === "" || String(value).trim() === "—") {
      setErr(field, `${label} is required.`);
    }
  }

  const veh: { field: string; label: string; value: string | undefined }[] = [
    { field: "key_no", label: "Key no.", value: v?.key_no },
    { field: "chassis_no", label: "Chassis No.", value: v?.frame_no },
    { field: "engine_no", label: "Engine no.", value: v?.engine_no },
    { field: "battery_no", label: "Battery no.", value: v?.battery_no },
  ];
  for (const { field, label, value } of veh) {
    if (!isBlank(value) && hasDisallowedSpecialChars(value)) {
      setErr(field, `${label} must not contain special characters.`);
    }
  }

  if (opts.includeInsuranceFields) {
    const insFields: { field: string; label: string; value: string | undefined }[] = [
      { field: "profession", label: "Customer Profession", value: ins?.profession },
      { field: "marital_status", label: "Customer Marital Status", value: ins?.marital_status },
      { field: "nominee_name", label: "Nominee Name", value: ins?.nominee_name },
      { field: "nominee_age", label: "Nominee Age", value: ins?.nominee_age },
      { field: "nominee_relationship", label: "Relationship", value: ins?.nominee_relationship },
      { field: "nominee_gender", label: "Nominee Gender", value: ins?.nominee_gender },
    ];
    for (const { field, label, value } of insFields) {
      if (field === "nominee_age") {
        if (!isBlank(value)) {
          if (!isValidNomineeAgeVal(value)) {
            setErr(field, "Nominee Age must be a number between 1 and 150.");
          } else if (hasDisallowedSpecialChars(value)) {
            setErr(field, `${label} must not contain special characters.`);
          }
        }
      } else if (!isBlank(value) && hasDisallowedSpecialChars(value)) {
        setErr(field, `${label} must not contain special characters.`);
      }
    }
  }

  return Array.from(m.entries()).map(([field, message]) => ({ field, message }));
}

/** Editable In-process Sales Details draft (Save Changes). */
export type InProcessDetailDraftFields = {
  care_of: string;
  address: string;
  frame_no: string;
  engine_no: string;
  key_no: string;
  battery_no: string;
  nominee_name: string;
  nominee_relationship: string;
};

/** Validation for Add Sales In-process Save Changes (parity with Section 2 editable subset). */
export function getInProcessDetailValidationErrors(
  draft: InProcessDetailDraftFields
): Section2FieldError[] {
  const m = new Map<string, string>();
  const setErr = (field: string, message: string) => {
    if (!m.has(field)) m.set(field, message);
  };

  const careOfTrimmed = (draft.care_of ?? "").trim();
  if (!careOfTrimmed) {
    setErr("care_of", "C/O is required.");
  } else if (!careOfHasRecognizedRelationMarker(careOfTrimmed)) {
    setErr("care_of", CARE_OF_RELATION_PREFIX_ERROR);
  }

  const addressTrimmed = (draft.address ?? "").trim();
  if (!addressTrimmed) {
    setErr("address", "Address is required.");
  } else {
    if (hasDisallowedSpecialChars(addressTrimmed)) {
      setErr("address", "Address must not contain special characters.");
    } else {
      const addrErr = validateFreeformAddressLine(addressTrimmed);
      if (addrErr) setErr("address", addrErr);
    }
  }

  const required: { field: string; label: string; value: string }[] = [
    { field: "key_no", label: "Key no.", value: draft.key_no },
    { field: "frame_no", label: "Chassis No.", value: draft.frame_no },
    { field: "engine_no", label: "Engine no.", value: draft.engine_no },
    { field: "battery_no", label: "Battery no.", value: draft.battery_no },
    { field: "nominee_name", label: "Nominee Name", value: draft.nominee_name },
    { field: "nominee_relationship", label: "Relationship", value: draft.nominee_relationship },
  ];
  for (const { field, label, value } of required) {
    if (isBlank(value)) {
      setErr(field, `${label} is required.`);
    } else if (hasDisallowedSpecialChars(value)) {
      setErr(field, `${label} must not contain special characters.`);
    }
  }

  if (!isBlank(draft.care_of) && hasDisallowedSpecialChars(draft.care_of)) {
    setErr("care_of", "C/O must not contain special characters.");
  }

  return Array.from(m.entries()).map(([field, message]) => ({ field, message }));
}
