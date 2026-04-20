/**
 * Default dealer id from Vite env (`VITE_DEALER_ID`). After login, APIs use the JWT dealer where applicable.
 */
export const DEALER_ID = Number((import.meta.env.VITE_DEALER_ID as string | undefined) ?? "100001");
