/** Section 2: DOB display as DD/MM/YYYY and C/O split (relation + name) for Add Sales. */

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
