/**
 * Add Sales free-text validation allows: letters, digits, space, hyphen, period, slash, comma.
 * OCR often appends parenthetical notes (e.g. nominee name "Priya (Mother)"). We keep only the
 * leading run of allowed characters and trim/collapse spaces so special characters and trailing
 * junk are not submitted.
 */

import type { ExtractedVehicleDetails } from "../types";

const FORM_FIELD_ALLOWED_CHAR = /[a-zA-Z0-9\s\-./,]/;

/**
 * Leading allowed characters only; stop at the first disallowed character. Collapse whitespace
 * runs and trim. Example: `"Priya (Mother)"` → `"Priya"`.
 */
export function sanitizeFormFieldValue(raw: string): string {
  let acc = "";
  for (const ch of raw) {
    if (FORM_FIELD_ALLOWED_CHAR.test(ch)) acc += ch;
    else break;
  }
  return acc.replace(/\s+/g, " ").trim();
}

export function sanitizeOptionalFormField(raw: string | undefined | null): string | undefined {
  if (raw == null) return undefined;
  const s = sanitizeFormFieldValue(String(raw));
  return s === "" ? undefined : s;
}

/** Nominee age: digits only, max length 3. */
export function sanitizeNomineeAgeInput(raw: string): string {
  return String(raw).replace(/\D/g, "").slice(0, 3);
}

/** Apply ``sanitizeFormFieldValue`` to every non-empty string value on a vehicle details object. */
export function sanitizeExtractedVehicleDetailFields(
  v: Partial<ExtractedVehicleDetails> | null | undefined
): Partial<ExtractedVehicleDetails> | undefined {
  if (v == null) return undefined;
  const out: Partial<ExtractedVehicleDetails> = { ...v };
  for (const key of Object.keys(out) as (keyof ExtractedVehicleDetails)[]) {
    const val = out[key];
    if (typeof val === "string" && val.length > 0) {
      (out as Record<string, unknown>)[key as string] = sanitizeFormFieldValue(val);
    }
  }
  return out;
}
