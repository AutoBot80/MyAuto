import { apiFetch } from "./client";

export interface AdminDealerNameRow {
  dealer_id: number;
  dealer_name: string;
}

export type JsonRecord = Record<string, string | number | boolean | null | undefined>;

/** One row per ``login_roles_ref`` line for the dealer (includes ``role_id`` for upsert). */
export interface DealerLoginAssignmentRow {
  login_id: string;
  login_display_name: string | null;
  login_phone: string | null;
  login_email: string | null;
  login_active_flag: string | null;
  login_roles_ref_id: number;
  role_id: number;
  role_name: string | null;
}

export interface AdminRoleRow {
  role_id: number;
  role_name: string;
}

export interface AdminLoginCatalogRow {
  login_id: string;
  display_name: string | null;
}

export interface SubdealerDiscountRow {
  dealer_id: number;
  subdealer_type: string;
  valid_flag: string;
  model: string;
  discount: number | null;
  create_date: string | null;
}

export function getAdminDealerNames() {
  return apiFetch<AdminDealerNameRow[]>("/admin/dealers");
}

export function getAdminDealerDetail(dealerId: number) {
  return apiFetch<JsonRecord>(`/admin/dealers/${dealerId}`);
}

export function createAdminDealer(body: { dealer_name: string; oem_id?: number | null; parent_id?: number | null }) {
  return apiFetch<JsonRecord & { dealer_id?: number }>("/admin/dealers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getAdminDealerLogins(dealerId: number) {
  return apiFetch<DealerLoginAssignmentRow[]>(`/admin/dealers/${dealerId}/logins`);
}

export function getAdminDealerDiscounts(dealerId: number) {
  return apiFetch<SubdealerDiscountRow[]>(`/admin/dealers/${dealerId}/discounts`);
}

export function createAdminDealerDiscount(
  dealerId: number,
  body: {
    subdealer_type?: string | null;
    model: string;
    discount?: number | null;
    create_date?: string | null;
    valid_flag?: "Y" | "N";
  }
) {
  return apiFetch<SubdealerDiscountRow>(`/admin/dealers/${dealerId}/discounts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function patchAdminDealerInsurerCpi(
  dealerId: number,
  body: { prefer_insurer: string | null; hero_cpi: "Y" | "N" }
) {
  return apiFetch<JsonRecord>(`/admin/dealers/${dealerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getAdminRoles() {
  return apiFetch<AdminRoleRow[]>("/admin/roles");
}

export function getAdminLoginCatalog() {
  return apiFetch<AdminLoginCatalogRow[]>("/admin/login-catalog");
}

export function patchLoginActiveFlag(loginId: string, body: { active_flag: "Y" | "N" }) {
  const enc = encodeURIComponent(loginId);
  return apiFetch<JsonRecord>(`/admin/logins/${enc}/active-flag`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function addDealerLoginRole(dealerId: number, body: { login_id: string; role_id: number }) {
  return apiFetch<JsonRecord & { login_roles_ref_id?: number }>(`/admin/dealers/${dealerId}/login-roles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type LoginAssignmentUpsertRow = {
  login_roles_ref_id: number | null;
  login_id: string;
  role_id: number;
  active_flag: "Y" | "N";
};

export function upsertLoginAssignments(dealerId: number, body: { rows: LoginAssignmentUpsertRow[] }) {
  return apiFetch<DealerLoginAssignmentRow[]>(`/admin/dealers/${dealerId}/login-assignments/upsert`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
