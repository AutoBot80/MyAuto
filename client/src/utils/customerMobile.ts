/** Sample / UI placeholder numbers — must not drive upload, folder names, or form fill. */
export const PLACEHOLDER_CUSTOMER_MOBILES = new Set<string>(["9876543210"]);

export function normalizeCustomerMobileDigits(raw: string): string {
  return raw.replace(/\D/g, "");
}

/** True when the last 10 digits are a known placeholder (e.g. printed sample on forms). */
export function isPlaceholderCustomerMobileDigits(digits: string): boolean {
  const ten = digits.length >= 10 ? digits.slice(-10) : digits;
  return ten.length === 10 && PLACEHOLDER_CUSTOMER_MOBILES.has(ten);
}
