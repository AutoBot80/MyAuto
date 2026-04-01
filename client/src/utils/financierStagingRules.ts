/** Hero MotoCorp OEM id in ``dealer_ref`` / ``oem_ref``. */
export const HERO_OEM_ID = 1;

/**
 * Hero dealers: financier names starting with "Bajaj" (any case) are stored on staging as **Hinduja**
 * for downstream systems. UI may still show the operator/OCR value.
 */
export function isHeroBajajFinancierForStaging(
  oemId: number | null | undefined,
  financierRaw: string | undefined | null
): boolean {
  if (oemId !== HERO_OEM_ID) return false;
  const t = String(financierRaw ?? "").trim();
  if (!t) return false;
  return /^bajaj/i.test(t);
}

/** Value sent on ``customer.financier`` in Submit Info / staging payload. */
export function financierForStagingPayload(
  oemId: number | null | undefined,
  financierRaw: string | undefined | null
): string | undefined {
  const t = String(financierRaw ?? "").trim();
  if (!t) return undefined;
  if (isHeroBajajFinancierForStaging(oemId, t)) return "Hinduja";
  return t;
}
