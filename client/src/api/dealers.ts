import { apiFetch } from "./client";

export interface Dealer {
  dealer_id: number;
  dealer_name: string;
  dealer_of: string | null;
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
