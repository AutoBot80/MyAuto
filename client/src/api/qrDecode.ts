import { getBaseUrl } from "./client";

/** Mapped UIDAI fields (only non-empty keys present). */
export interface QrDecodeFields {
  aadhar_id?: string;
  name?: string;
  gender?: string;
  year_of_birth?: string;
  date_of_birth?: string;
  care_of?: string;
  house?: string;
  street?: string;
  location?: string;
  city?: string;
  post_office?: string;
  district?: string;
  sub_district?: string;
  state?: string;
  pin_code?: string;
}

export interface QrDecodedEntry {
  raw: string;
  expanded?: string;
  parsed: Record<string, string | number | boolean>;
  /** Mapped fields for display (Aadhar ID, Name, etc.). */
  fields: QrDecodeFields;
  /** Base64-encoded photo from QR if present. */
  photo_base64: string | null;
}

export interface QrDecodeResponse {
  decoded: QrDecodedEntry[];
  error: string | null;
}

/** Display labels and display order for mapped fields. */
export const QR_FIELD_ORDER: (keyof QrDecodeFields)[] = [
  "aadhar_id",
  "name",
  "gender",
  "year_of_birth",
  "date_of_birth",
  "care_of",
  "house",
  "street",
  "location",
  "city",
  "post_office",
  "district",
  "sub_district",
  "state",
  "pin_code",
];

export const QR_FIELD_LABELS: Record<keyof QrDecodeFields, string> = {
  aadhar_id: "Aadhar ID",
  name: "Name",
  gender: "Gender",
  year_of_birth: "Year of birth",
  date_of_birth: "Date of birth",
  care_of: "Care of",
  house: "House",
  street: "Street",
  location: "Location",
  city: "City",
  post_office: "Post Office",
  district: "District",
  sub_district: "Sub District",
  state: "State",
  pin_code: "Pin Code",
};

const QR_DECODE_TIMEOUT_MS = 60_000;

export async function decodeQrFromImage(file: File): Promise<QrDecodeResponse> {
  const form = new FormData();
  form.append("file", file);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), QR_DECODE_TIMEOUT_MS);
  try {
    const res = await fetch(`${getBaseUrl()}/qr-decode`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `QR decode failed (${res.status})`);
    }
    return res.json() as Promise<QrDecodeResponse>;
  } catch (err) {
    if (err instanceof Error) {
      if (err.name === "AbortError") {
        throw new Error("Request timed out. Try a smaller image (e.g. under 2 MB).");
      }
      throw err;
    }
    throw new Error("QR decode request failed");
  } finally {
    clearTimeout(timeoutId);
  }
}
