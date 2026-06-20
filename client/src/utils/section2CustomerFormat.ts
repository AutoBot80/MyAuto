/** Section 2: DOB display as DD/MM/YYYY and C/O split (relation + name) for Add Sales. */

import type { ExtractedCustomerDetails } from "../types";
import { resolveIndianStateName } from "./indianStateResolve";

export const CARE_OF_RELATION_OPTIONS = ["S/o", "D/o", "W/o", "C/o"] as const;
export type CareOfRelation = (typeof CARE_OF_RELATION_OPTIONS)[number];

const CARE_PREFIX_RE = /^(S\/o|D\/o|W\/o|C\/o)\s+/i;

/** S/o, D/o, W/o at start (Siebel/DMS); excludes C/o. Aligned with backend ``_relation_type_from_care_of``. */
const CARE_RELATION_MARKER_RE =
  /^\s*(S\s*[\./]?\s*O|W\s*[\./]?\s*O|D\s*[\./]?\s*O)\b\s*(?:[:\-–—]\s*)?(.*)$/i;

export const CARE_OF_RELATION_PREFIX_ERROR =
  "C/O must start with S/o, D/o, or W/o followed by the relation's name (e.g. S/o Ram Singh).";

/** True when ``care_of`` has S/o, D/o, or W/o prefix (any common casing) and a non-empty name after it. */
export function careOfHasRecognizedRelationMarker(raw: string | undefined | null): boolean {
  const s = (raw ?? "").trim();
  if (!s) return false;
  const m = s.match(CARE_RELATION_MARKER_RE);
  if (!m) return false;
  return (m[2] ?? "").trim().length > 0;
}

function canonicalRelation(marker: string): CareOfRelation {
  const m = marker.replace(/\//g, "/").trim();
  const found = CARE_OF_RELATION_OPTIONS.find((opt) => opt.toLowerCase() === m.toLowerCase());
  return found ?? "S/o";
}

/** Parse combined ``care_of`` from OCR/API into dropdown + name (defaults relation ``S/o`` when no marker). */
export function parseCareOfFromCombined(raw: string | undefined | null): { relation: CareOfRelation; name: string } {
  const s = (raw ?? "").trim();
  if (!s) return { relation: "S/o", name: "" };
  const match = s.match(CARE_PREFIX_RE);
  if (match) {
    const relation = canonicalRelation(match[1]);
    const name = s.slice(match[0].length).trim();
    return { relation, name };
  }
  return { relation: "S/o", name: s };
}

/** Stored ``care_of`` for API / DMS: ``S/o Name`` style. */
export function composeCareOf(relation: string | undefined | null, name: string | undefined | null): string {
  const n = (name ?? "").trim();
  if (!n) return "";
  const r = canonicalRelation((relation ?? "S/o").trim() || "S/o");
  return `${r} ${n}`.trim();
}

/** Normalize API/OCR date strings to ``DD/MM/YYYY`` for the Section 2 field. */
export function normalizeDobToDdMmYyyy(raw: string | undefined | null): string {
  const s = (raw ?? "").trim();
  if (!s) return "";
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (iso) {
    const [, y, mo, d] = iso;
    return `${d}/${mo}/${y}`;
  }
  const dmHyphen = /^(\d{2})-(\d{2})-(\d{4})$/.exec(s);
  if (dmHyphen) {
    const [, d, mo, y] = dmHyphen;
    return `${d}/${mo}/${y}`;
  }
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(s)) return s;
  return s;
}

/** Format typed digits as ``DD/MM/YYYY`` (max 8 digits). */
export function formatDobDigitsInput(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
}

export function isValidDdMmYyyy(s: string | undefined | null): boolean {
  const t = (s ?? "").trim();
  if (!/^\d{2}\/\d{2}\/\d{4}$/.test(t)) return false;
  const [dd, mm, yyyy] = t.split("/").map((x) => parseInt(x, 10));
  if (mm < 1 || mm > 12) return false;
  if (dd < 1 || dd > 31) return false;
  const dt = new Date(yyyy, mm - 1, dd);
  return dt.getFullYear() === yyyy && dt.getMonth() === mm - 1 && dt.getDate() === dd;
}

const ADDRESS_LINE1_PARTS = (c: ExtractedCustomerDetails) =>
  [c.house, c.street, c.location, c.post_office, c.district, c.sub_district].filter(
    (s) => s != null && String(s).trim() !== ""
  );

/** Section 2 Address row 1: house / street / locality (not city, state, PIN). */
export function buildAddressLine1(c: ExtractedCustomerDetails | null | undefined): string {
  if (!c) return "";
  const granular = ADDRESS_LINE1_PARTS(c).map((s) => String(s).trim());
  if (granular.length > 0) return granular.join(", ");
  return (c.address ?? "").trim();
}

/** Section 2 Address row 2: city, state, PIN. */
export function buildAddressLine2(c: ExtractedCustomerDetails | null | undefined): string {
  if (!c) return "";
  const parts = [c.city, c.state, c.pin_code].filter((s) => s != null && String(s).trim() !== "");
  return parts.map((s) => String(s).trim()).join(", ");
}

/**
 * In-process Sales Details address from staging ``customer``.
 * When ``address`` is already a full normalized operator line, use it alone (avoids duplicating
 * city/state/PIN after In-process PATCH). Otherwise combine New Sales line 1 + line 2 columns.
 */
export function inProcessAddressFromStaging(c: ExtractedCustomerDetails | null | undefined): string {
  if (!c) return "";
  const fromField = (c.address ?? "").trim();
  const line2 = buildAddressLine2(c);

  if (fromField && validateFreeformAddressLine(fromField) === null) {
    return fromField;
  }
  if (fromField && line2) {
    return `${fromField}, ${line2}`;
  }
  if (fromField) return fromField;
  if (line2) return line2;
  return buildSection2FullAddress(c);
}

/** Full address for Section 2 required-field checks (both rows). */
export function buildSection2FullAddress(c: ExtractedCustomerDetails | null | undefined): string {
  const l1 = buildAddressLine1(c);
  const l2 = buildAddressLine2(c);
  if (!l1 && !l2) return "";
  return [l1, l2].filter(Boolean).join(", ");
}

/** Parse row-2 text into city / state / PIN (comma-separated; last segment may be 6-digit PIN). */
export function parseAddressLine2(raw: string): Pick<ExtractedCustomerDetails, "city" | "state" | "pin_code"> {
  const s = (raw ?? "").trim();
  if (!s) return {};
  const segments = s.split(",").map((x) => x.trim()).filter(Boolean);
  if (segments.length === 0) return {};

  let pin_code: string | undefined;
  let rest = segments;
  const last = segments[segments.length - 1] ?? "";
  const pinDigits = last.replace(/\s/g, "");
  if (/^\d{6}$/.test(pinDigits)) {
    pin_code = pinDigits;
    rest = segments.slice(0, -1);
  }

  let state: string | undefined;
  let city: string | undefined;
  if (rest.length >= 2) {
    state = rest[rest.length - 1];
    city = rest.slice(0, -1).join(", ");
  } else if (rest.length === 1) {
    city = rest[0];
  }

  return {
    city: city || undefined,
    state: state || undefined,
    pin_code,
  };
}

const STATE_PIN_DASH_TAIL_RE = /^(.+?)\s*(?:[-–—]\s*)+(\d{6})\s*$/u;

export type ParsedOperatorAddress = {
  cityRaw: string;
  stateRaw: string;
  pin: string;
};

/**
 * Parse comma-separated address tail: PIN as last segment **or** ``State - 321001`` in the last segment.
 * ``minCommaSegments``: 3 for full In-process line; 2 for New tab address row 2 only.
 */
export function parseOperatorAddressCommaSegments(
  segments: string[],
  minCommaSegments: number
): ParsedOperatorAddress | null {
  if (segments.length < minCommaSegments) return null;
  const last = segments[segments.length - 1] ?? "";
  const pinOnly = last.replace(/\s/g, "");
  if (/^\d{6}$/.test(pinOnly)) {
    if (segments.length < 2) return null;
    const stateRaw = (segments[segments.length - 2] ?? "")
      .replace(/(?:\s*[-–—])+\s*$/u, "")
      .trim();
    const cityRaw = segments.slice(0, -2).join(", ").trim();
    return { cityRaw, stateRaw, pin: pinOnly };
  }
  const dash = last.trim().match(STATE_PIN_DASH_TAIL_RE);
  if (dash) {
    const stateRaw = dash[1].trim();
    const pin = dash[2];
    const cityRaw = segments.slice(0, -1).join(", ").trim();
    return { cityRaw, stateRaw, pin };
  }
  return null;
}

export type ValidateFreeformAddressOpts = {
  /** Default 3 (full line); use 2 for New tab address row 2. */
  minCommaSegments?: number;
};

export type NormalizedOperatorAddress = {
  address: string;
  city: string;
  state: string;
  pin_code: string;
};

function titleCaseWord(word: string): string {
  if (!word) return "";
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}

/** Init-cap each word in a single locality field (house, street, location, etc.). */
export function initCapWords(raw: string): string {
  return raw
    .trim()
    .replace(/\s+/g, " ")
    .split(/\s+/)
    .map(titleCaseWord)
    .filter(Boolean)
    .join(" ");
}

/** Init-cap each comma-separated locality/city clause (``bharatpur`` → ``Bharatpur``). */
export function titleCaseAddressLocality(raw: string): string {
  return raw
    .split(",")
    .map((part) =>
      part
        .trim()
        .split(/\s+/)
        .map(titleCaseWord)
        .filter(Boolean)
        .join(" ")
    )
    .filter(Boolean)
    .join(", ");
}

/** Primary ``customer.city`` from locality string (last clause before state). */
export function primaryCityFromLocalityRaw(cityLocality: string): string {
  const parts = cityLocality
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : cityLocality.trim();
}

function operatorAddressUsedDashBeforePin(lastSegment: string): boolean {
  return STATE_PIN_DASH_TAIL_RE.test(lastSegment.trim());
}

/** Build stored address line with title-cased locality and canonical state spelling. */
export function normalizeOperatorFreeformAddress(
  raw: string | undefined | null,
  opts?: ValidateFreeformAddressOpts
): NormalizedOperatorAddress | null {
  if (validateFreeformAddressLine(raw, opts)) return null;
  const minSeg = opts?.minCommaSegments ?? 3;
  const segments = (raw ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const parsed = parseOperatorAddressCommaSegments(segments, minSeg);
  if (!parsed) return null;
  const canon = resolveIndianStateName(parsed.stateRaw, { allowLaLadakh: true });
  if (!canon) return null;
  const cityLocality = titleCaseAddressLocality(parsed.cityRaw);
  const city = primaryCityFromLocalityRaw(cityLocality);
  const last = segments[segments.length - 1] ?? "";
  const address = operatorAddressUsedDashBeforePin(last)
    ? `${cityLocality}, ${canon} - ${parsed.pin}`
    : `${cityLocality}, ${canon}, ${parsed.pin}`;
  return { address, city, state: canon, pin_code: parsed.pin };
}

/**
 * Single-line address: ``locality…, City, State, 123456`` or ``…, City, State - 123456``.
 * Mirrors ``validate_operator_freeform_address`` in backend.
 */
export function validateFreeformAddressLine(
  raw: string | undefined | null,
  opts?: ValidateFreeformAddressOpts
): string | null {
  const minSeg = opts?.minCommaSegments ?? 3;
  const s = (raw ?? "").trim().replace(/\s+/g, " ");
  if (!s) return "Address is required.";
  if (!s.includes(",")) {
    return "Use comma-separated format: locality, City, State, 123456.";
  }
  const segments = s.split(",").map((x) => x.trim()).filter(Boolean);
  const parsed = parseOperatorAddressCommaSegments(segments, minSeg);
  if (!parsed) {
    if (segments.length < minSeg) {
      return minSeg === 2
        ? "Enter City, State, and 6-digit PIN on the second address line."
        : "Enter locality, City, State, and 6-digit PIN.";
    }
    return "City, State, and PIN could not be detected — use: City, State, 123456.";
  }
  const { cityRaw, stateRaw, pin } = parsed;
  if (!/^\d{6}$/.test(pin)) {
    return "Last segment must be a 6-digit PIN.";
  }
  if (!stateRaw) return "State is required before the PIN.";
  if (cityRaw.length < 2) return "City / locality is required before State and PIN.";
  const canon = resolveIndianStateName(stateRaw, { allowLaLadakh: true });
  if (!canon) {
    return `State «${stateRaw}» is not a recognized Indian state or union territory.`;
  }
  return null;
}
