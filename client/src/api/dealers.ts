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
  /** Optional canonical MISP insurer label from ``dealer_ref``; used when details insurer is empty. */
  prefer_insurer?: string | null;
}

export async function getDealer(dealerId: number): Promise<Dealer> {
  return apiFetch<Dealer>(`/dealers/${dealerId}`);
}

/** Row for subdealer dropdown: ``dealer_ref.parent_id`` = logged-in dealer. */
export type DealerByParentRow = {
  dealer_id: number;
  dealer_name: string;
};

/** GET /dealers/by-parent/{parentDealerId} — child dealers (``parent_id`` = parent). */
export async function listDealersByParent(parentDealerId: number): Promise<DealerByParentRow[]> {
  return apiFetch<DealerByParentRow[]>(`/dealers/by-parent/${parentDealerId}`);
}
