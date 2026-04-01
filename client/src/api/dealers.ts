import { apiFetch } from "./client";

export interface Dealer {
  dealer_id: number;
  /** FK to ``oem_ref``; Hero MotoCorp is ``1``. */
  oem_id: number | null;
  dealer_name: string;
  dealer_of: string | null;
  dms_link: string | null;
  address: string | null;
  pin: string | null;
  city: string | null;
  state: string | null;
  parent_id: number | null;
  phone: string | null;
}

export async function getDealer(dealerId: number): Promise<Dealer> {
  return apiFetch<Dealer>(`/dealers/${dealerId}`);
}
