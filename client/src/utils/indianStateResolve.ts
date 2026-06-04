/**
 * Indian state / UT names — keep in sync with ``_INDIA_REGIONS`` in
 * ``backend/app/services/customer_address_infer.py``.
 */

const INDIA_REGIONS = [
  "Dadra and Nagar Haveli and Daman and Diu",
  "Andaman and Nicobar Islands",
  "Jammu and Kashmir",
  "Andhra Pradesh",
  "Arunachal Pradesh",
  "Himachal Pradesh",
  "Madhya Pradesh",
  "Tamil Nadu",
  "Uttar Pradesh",
  "West Bengal",
  "Uttarakhand",
  "Chhattisgarh",
  "Maharashtra",
  "Meghalaya",
  "Nagaland",
  "Puducherry",
  "Lakshadweep",
  "Telangana",
  "Karnataka",
  "Rajasthan",
  "Gujarat",
  "Haryana",
  "Sikkim",
  "Tripura",
  "Manipur",
  "Mizoram",
  "Assam",
  "Bihar",
  "Delhi",
  "Goa",
  "Kerala",
  "Odisha",
  "Punjab",
  "Ladakh",
] as const;

const REGION_BY_LOWER = new Map(INDIA_REGIONS.map((r) => [r.toLowerCase(), r]));

const TWO_LETTER: Record<string, string> = {
  AP: "Andhra Pradesh",
  AR: "Arunachal Pradesh",
  AS: "Assam",
  BR: "Bihar",
  CG: "Chhattisgarh",
  GA: "Goa",
  GJ: "Gujarat",
  HR: "Haryana",
  HP: "Himachal Pradesh",
  JK: "Jammu and Kashmir",
  KA: "Karnataka",
  KL: "Kerala",
  LD: "Lakshadweep",
  MP: "Madhya Pradesh",
  MH: "Maharashtra",
  MN: "Manipur",
  ML: "Meghalaya",
  MZ: "Mizoram",
  NL: "Nagaland",
  OD: "Odisha",
  OR: "Odisha",
  PB: "Punjab",
  RJ: "Rajasthan",
  SK: "Sikkim",
  TN: "Tamil Nadu",
  TS: "Telangana",
  TR: "Tripura",
  UP: "Uttar Pradesh",
  UK: "Uttarakhand",
  WB: "West Bengal",
  AN: "Andaman and Nicobar Islands",
  DL: "Delhi",
  PY: "Puducherry",
  LA: "Ladakh",
  DD: "Dadra and Nagar Haveli and Daman and Diu",
};

const OCR_SYNONYMS: Record<string, string> = {
  rajashan: "Rajasthan",
  orissa: "Odisha",
};

const FUZZY_MIN_RATIO = 0.86;

function squish(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function sequenceRatio(a: string, b: string): number {
  if (a === b) return 1;
  if (!a.length || !b.length) return 0;
  const longer = a.length >= b.length ? a : b;
  const shorter = a.length >= b.length ? b : a;
  if (longer.length === 0) return 1;
  const editDistance = (() => {
    const rows = shorter.length + 1;
    const cols = longer.length + 1;
    const dist: number[][] = Array.from({ length: rows }, () => Array(cols).fill(0));
    for (let i = 0; i < rows; i++) dist[i][0] = i;
    for (let j = 0; j < cols; j++) dist[0][j] = j;
    for (let i = 1; i < rows; i++) {
      for (let j = 1; j < cols; j++) {
        const cost = shorter[i - 1] === longer[j - 1] ? 0 : 1;
        dist[i][j] = Math.min(dist[i - 1][j] + 1, dist[i][j - 1] + 1, dist[i - 1][j - 1] + cost);
      }
    }
    return dist[rows - 1][cols - 1];
  })();
  return (longer.length + shorter.length - editDistance) / (longer.length + shorter.length);
}

/** Map OCR / shorthand / two-letter codes to canonical state (mirrors backend ``resolve_indian_state_name``). */
export function resolveIndianStateName(
  ocrToken: string | undefined | null,
  options?: { allowLaLadakh?: boolean }
): string | null {
  const allowLa = options?.allowLaLadakh ?? true;
  let s = squish(ocrToken ?? "");
  s = s.replace(/(?:\s*[-–—])+\s*$/u, "").trim();
  s = s.replace(/[.,;:]+$/u, "").trim();
  if (!s) return null;

  const syn = OCR_SYNONYMS[s.toLowerCase()];
  if (syn) return syn;

  const canon = REGION_BY_LOWER.get(s.toLowerCase());
  if (canon) return canon;

  const lettersOnly = s.replace(/[^A-Za-z]/g, "");
  if (lettersOnly.length === 2) {
    const code = lettersOnly.toUpperCase();
    if (code === "LA" && !allowLa) {
      // skip two-letter LA
    } else {
      const hit = TWO_LETTER[code];
      if (hit) return hit;
    }
  }

  if (/^raj\.?$/i.test(s)) return "Rajasthan";

  let best: string | null = null;
  let bestScore = 0;
  const low = s.toLowerCase();
  for (const region of INDIA_REGIONS) {
    const score = sequenceRatio(low, region.toLowerCase());
    if (score > bestScore) {
      bestScore = score;
      best = region;
    }
  }
  if (best && bestScore >= FUZZY_MIN_RATIO) return best;
  return null;
}
