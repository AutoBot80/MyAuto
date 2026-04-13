import { apiFetch } from "./client";
import { clearAccessToken, setAccessToken } from "../auth/token";

export type LoginRequest = {
  dealer_id: number;
  login_id: string;
  password: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  dealer_id: number;
  login_id: string;
  roles: string[];
  admin: boolean;
};

export type MeResponse = {
  login_id: string;
  dealer_id: number;
  name: string | null;
  roles: string[];
  admin: boolean;
};

export async function loginApi(body: LoginRequest): Promise<LoginResponse> {
  const r = await apiFetch<LoginResponse>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  setAccessToken(r.access_token);
  return r;
}

export async function getMe(): Promise<MeResponse> {
  return apiFetch<MeResponse>("/auth/me");
}

export function logout(): void {
  clearAccessToken();
}
