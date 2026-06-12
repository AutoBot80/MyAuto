/**
 * Portal insurer + financier resolution for Add Sales (OCR merge, display, Submit Info).
 * Mirrors backend ``fuzzy_best_master_ref_value`` / portal-list rules at a practical threshold.
 */

function normalizeForFuzzyMatch(s: string): string {
  return s.toLowerCase().trim().replace(/\s+/g, " ");
}

function stripLeadingThe(norm: string): string {
  return norm.replace(/^the\s+/, "");
}

function compositePairStrength(q: string, c: string): number {
  if (!q || !c) return 0;
  if (q === c) return 1;
  let score = 0;
  const qLen = q.length;
  const cLen = c.length;
  const maxLen = Math.max(qLen, cLen);
  if (maxLen === 0) return 1;
  let matches = 0;
  const minLen = Math.min(qLen, cLen);
  for (let i = 0; i < minLen; i++) {
    if (q[i] === c[i]) matches++;
  }
  score = matches / maxLen;
  const qWords = new Set(q.split(/\s+/).filter((w) => w.length >= 2));
  const cWords = new Set(c.split(/\s+/).filter((w) => w.length >= 2));
  if (qWords.size && cWords.size) {
    let inter = 0;
    for (const w of qWords) {
      if (cWords.has(w)) inter++;
    }
    const union = qWords.size + cWords.size - inter || 1;
    score = Math.max(score, (inter / union) * 0.98);
  }
  if (q.length >= 3 && (q.includes(c) || c.includes(q))) {
    score = Math.max(score, 0.92);
  }
  return score;
}

/** Best ``master_ref`` row at or above ``minScore`` (simplified head-weighted blend). */
export function fuzzyBestMasterRefValue(
  query: string,
  candidates: readonly string[],
  minScore = 0.5
): string | null {
  if (!candidates.length) return null;
  const qn = normalizeForFuzzyMatch(query);
  if (!qn) return null;
  const qs = stripLeadingThe(qn) || qn;
  let best: string | null = null;
  let bestFinal = 0;
  for (const raw of candidates) {
    const c = raw.trim();
    if (!c) continue;
    const cn = normalizeForFuzzyMatch(c);
    const cs = stripLeadingThe(cn) || cn;
    const sFull = compositePairStrength(qs, cs);
    const wq = qs.split(/\s+/);
    const wc = cs.split(/\s+/);
    const headQ = wq.slice(0, 3).join(" ") || qs;
    const headC = wc.slice(0, 3).join(" ") || cs;
    const sHead = headQ && headC ? compositePairStrength(headQ, headC) : 0;
    const final = 0.55 * sHead + 0.45 * sFull;
    if (final > bestFinal) {
      bestFinal = final;
      best = c;
    }
  }
  return bestFinal >= minScore ? best : null;
}

/** True when OCR insurer text likely belongs on the financier field (e.g. Bajaj Finance bleed). */
export function insurerLooksLikeFinancier(
  insurer: string | undefined,
  financiers: readonly string[]
): boolean {
  const s = (insurer ?? "").trim();
  if (!s || !financiers.length) return false;
  return fuzzyBestMasterRefValue(s, financiers, 0.5) !== null;
}

/**
 * Section 3 insurer: portal OCR wins; else dealer ``prefer_insurer`` when in portal list.
 */
export function resolvePortalInsurer(
  ocrValue: string | undefined,
  preferInsurer: string | null | undefined,
  portalInsurers: readonly string[]
): string | undefined {
  const S = (ocrValue ?? "").trim();
  const P = (preferInsurer ?? "").trim();
  if (!portalInsurers.length) {
    return S || P || undefined;
  }
  if (S && portalInsurers.includes(S)) return S;
  if (P && portalInsurers.includes(P)) return P;
  return undefined;
}

/** Map OCR financier to canonical ``master_ref`` FINANCER when fuzzy match succeeds. */
export function resolveCanonicalFinancier(
  ocrValue: string | undefined,
  financiers: readonly string[]
): string | undefined {
  const s = (ocrValue ?? "").trim();
  if (!s) return undefined;
  if (!financiers.length) return s;
  if (financiers.includes(s)) return s;
  return fuzzyBestMasterRefValue(s, financiers, 0.5) ?? s;
}
