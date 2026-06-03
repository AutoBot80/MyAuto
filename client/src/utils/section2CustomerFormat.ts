/** Section 2: DOB display as DD/MM/YYYY and C/O split (relation + name) for Add Sales. */

import type { ExtractedCustomerDetails } from "../types";

export const CARE_OF_RELATION_OPTIONS = ["S/o", "D/o", "W/o", "C/o"] as const;
export type CareOfRelation = (typeof CARE_OF_RELATION_OPTIONS)[number];

const CARE_PREFIX_RE = /^(S\/o|D\/o|W\/o|C\/o)\s+/i;

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
