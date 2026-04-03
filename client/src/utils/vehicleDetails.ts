/** Normalize vehicle details from API or storage so the UI can display them. */

import type { ExtractedVehicleDetails } from "../types";
import { sanitizeFormFieldValue } from "./formFieldSanitize";

const FIELDS = [
  "frame_no",
  "engine_no",
  "model_colour",
  "key_no",
  "battery_no",
] as const;

/** Alternate keys the backend or storage might use (e.g. from Textract labels). */
const ALIASES: Record<(typeof FIELDS)[number], string[]> = {
  frame_no: ["frame_no", "frame no", "frame no.", "Frame no.", "frameNo", "chassis", "chassis no", "frame number"],
  engine_no: ["engine_no", "engine no", "engine no.", "Engine no.", "engineNo", "engine", "engine number"],
  model_colour: ["model_colour", "model & colour", "model and colour", "model", "colour", "color", "modelColour"],
  key_no: ["key_no", "key no", "key no.", "Key no.", "keyNo", "key", "key number"],
  battery_no: ["battery_no", "battery no", "battery no.", "Battery no.", "batteryNo", "battery", "battery number"],
};

function getString(val: unknown): string {
  if (val == null) return "";
  if (typeof val === "string") return val.trim();
  if (typeof val === "number" && !Number.isNaN(val)) return String(val);
  return "";
}

/** Normalize a key for matching: lower case, collapse spaces/punctuation. */
function normKey(k: string): string {
  return k
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[.\s&\/]/g, "")
    .trim();
}

/**
 * Normalize a raw vehicle object from the API or storage into ExtractedVehicleDetails.
 * 1) Tries known aliases per field.
 * 2) Then scans all keys in the object and matches by normalized key (so any backend key shape works).
 */
export function normalizeVehicleDetails(raw: unknown): ExtractedVehicleDetails | null {
  if (raw == null) return null;
  if (typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const out: ExtractedVehicleDetails = {};

  for (const field of FIELDS) {
    for (const key of ALIASES[field]) {
      const val = o[key];
      const s = getString(val);
      if (s) {
        out[field] = s;
        break;
      }
    }
  }

  // Fallback: match any key in the object by normalized name (handles backend key variants)
  const aliasNormToField = new Map<string, (typeof FIELDS)[number]>();
  for (const field of FIELDS) {
    for (const a of ALIASES[field]) {
      aliasNormToField.set(normKey(a), field);
    }
  }
  for (const key of Object.keys(o)) {
    const n = normKey(key);
    const field = aliasNormToField.get(n);
    if (field && !out[field]) {
      const s = getString(o[key]);
      if (s) out[field] = s;
    }
  }

  for (const key of Object.keys(out) as (keyof ExtractedVehicleDetails)[]) {
    const val = out[key];
    if (typeof val === "string" && val.length > 0) {
      out[key] = sanitizeFormFieldValue(val);
    }
  }

  return Object.keys(out).length > 0 ? out : null;
}

/** Return true if we have at least one vehicle field to show. */
export function hasVehicleData(v: ExtractedVehicleDetails | null): boolean {
  if (!v) return false;
  return FIELDS.some((f) => getString(v[f]).length > 0);
}
