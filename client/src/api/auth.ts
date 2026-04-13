import { apiFetch } from "./client";
import { clearAccessToken, setAccessToken } from "../auth/token";

export type LoginRequest = {
  /** Omit if this login has one dealer in ``login_roles_ref`` (server derives). Required if multiple. */
  dealer_id?: number;
  login_id: string;
  password: string;
};

/** Mirrors ``roles_ref`` flags (OR across assigned roles) for home tiles. */
export type HomeTileFlags = {
  /** Sales Window — ``pos_flag`` */
  tile_pos: boolean;
  /** RTO Desk — ``rto_flag`` */
  tile_rto: boolean;
  /** Service Saathi — ``service_flag`` */
  tile_service: boolean;
  /** Dealer Saathi — ``dealer_flag`` */
  tile_dealer: boolean;
};

export const ALL_HOME_TILES_TRUE: HomeTileFlags = {
  tile_pos: true,
  tile_rto: true,
  tile_service: true,
  tile_dealer: true,
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  dealer_id: number;
  login_id: string;
  roles: string[];
  admin: boolean;
} & HomeTileFlags;

export type MeResponse = {
  login_id: string;
  dealer_id: number;
  name: string | null;
  roles: string[];
  admin: boolean;
} & HomeTileFlags;

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
