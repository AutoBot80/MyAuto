import type { ExtractedInsuranceDetails } from "../types";

/** CPA certificate # from staging ``insurance`` (Alliance commit writes ``cpa_policy_num``). */
export function cpaPolicyFromInsuranceRaw(
  rec: Record<string, unknown> | null | undefined
): string {
  if (!rec) return "";
  const v =
    rec.cpa_policy_num ??
    rec.cpa_policy ??
    rec.alliance_policy_num ??
    rec.cpa_policy_number;
  const s = v != null ? String(v).trim() : "";
  return s || "";
}

/** Apply staging ``insurance`` policy fields onto extracted insurance state (New tab refresh). */
export function insuranceFieldsFromStagingInsurance(
  rec: Record<string, unknown> | null | undefined
): Partial<ExtractedInsuranceDetails> {
  if (!rec) return {};
  const out: Partial<ExtractedInsuranceDetails> = {};
  const pn = String(rec.policy_num ?? "").trim();
  if (pn) out.policy_num = pn;
  const pf = String(rec.policy_from ?? "").trim();
  if (pf) out.policy_from = pf;
  const pt = String(rec.policy_to ?? "").trim();
  if (pt) out.policy_to = pt;
  const prem = rec.premium;
  if (prem != null && String(prem).trim() !== "") {
    out.premium = String(prem).trim();
  }
  const cpa = cpaPolicyFromInsuranceRaw(rec);
  if (cpa) out.cpa_policy_num = cpa;
  return out;
}
